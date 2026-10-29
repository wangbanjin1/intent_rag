"""Pipeline 编排 — RefPyDST + S3-DST PAR 全流程。

输出范式: python_par (Text-to-Python + 强制 # summary PAR)。
(xml_par 变体消融不优已移除; PMI_β 重排无收益已移除; record_selector 后处理选值
两次实验净负已移除; time_rules 后处理净负已移除。)

  utterance + prev_state
      │
      ▼ Embedding(prev_state || utterance)
      ▼ DiverseRetriever (MMR)  →  E_k
      ▼ build_prompt → (system, task_prompt)
      ▼ LLM.gen_raw_code → 原始输出
      ▼ execute_program_multi (沙箱执行得 Δy)
      ▼ PAR 校验回路 (缺 summary 触发重采样1次) + record 兜底
      ▼ Rule Validator (apply Δy → y_t → 校验, 含时间知二推一)
      ▼ final JSON {intent, delta, validity, invalid_reason, final_state}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

from embedder import Embedder
from features import Features
from llm_client import LLMClient
from prompt_builder import build_prompt
from python_state import execute_program_multi, parse_summaries, _match_prev_key
from retriever import DiverseRetriever, RetrievedCase
from retrieval_bank import CaseBank
from schema import KNOWN_APPLICATIONS
from validator import validate


@dataclass
class PipelineResult:
    utterance: str
    reference_time: str
    prev_state: dict[str, Any]
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    best: dict[str, Any] = field(default_factory=dict)
    final: dict[str, Any] = field(default_factory=dict)


_CANCEL_WORDS = ("不用了", "取消", "撤了", "不弄了", "不用保障", "不用办",
                 "算了", "只留", "只保留", "照旧", "那个不用", "去掉")

# v12: 用户改口/否定词 (Correct_Previous_Input / Negate / Modify_Request)
_CORR_WORDS = ("不是", "不对", "错了", "改成", "改为", "换成", "应该是",
               "我说的是", "搞错", "弄错", "重新")


def _is_correction(utterance: str) -> bool:
    return any(w in (utterance or "") for w in _CORR_WORDS)


# v21: 消歧轮检测 (修补② T3-3)。窄触发: 三条件同时满足才注入消歧规则。
# 1) 上轮 agent 话术是"列出多条记录候选并问选哪条"的消歧问句
_DISAMB_AGENT_RE = re.compile(r"记录")  # 先含"记录"
_DISAMB_AGENT_Q_RE = re.compile(r"哪一?条|哪条|按哪|哪一个|哪次|还是.{0,30}(呢|？|\?)")
# 2) 当前 utterance 是选择回答 (序数/日期指代/承按)
_DISAMB_USER_RE = re.compile(
    r"第[一二三四五六七八九十\d]+\s*[条个]|候选\s*[一二三四五六七八九十\d]+|就按|就选|就要|按.{0,12}(那条|该条|这条)"
    r"|\d{1,2}月\d{1,2}[号日].{0,8}(那条|这条|那条记录)|\d{1,2}[号日].{0,6}那条|最新的?一?条|最近的?一?条"
)


def _is_disambiguation_turn(utterance: str, agent_reply: str | None,
                            prev_states: dict) -> bool:
    """消歧回答轮: agent 上轮列出记录候选问选哪条 + 用户本轮做选择 + 有待沿用意图。"""
    if not prev_states or not agent_reply:
        return False
    ar = agent_reply or ""
    if not (_DISAMB_AGENT_RE.search(ar) and _DISAMB_AGENT_Q_RE.search(ar)):
        return False
    return bool(_DISAMB_USER_RE.search(utterance or ""))


# v24.3: 消歧候选顺序解析。规则文字"按话术顺序"仍要求模型自行对齐两套顺序
# (prompt records 按 completed_at 降序 vs agent 话术顺序, S-000303 实证模型会颠倒),
# 改为 harness 侧确定性解析: 从 agent 话术按出现顺序抽取候选, 属性匹配到画像 records,
# 生成"第N条 = 记录字段明细"对照注入 prompt。解析不可靠时返回 None 安全回退纯文字规则。
_DISAMB_QCUT_RE = re.compile(r"请问|您看|您想|要选|要选择|要按")  # 截掉问句, 防"按A这条还是B这条"复读
_DISAMB_SPLIT_RE = re.compile(
    r"分别是|，和|、|；|，还有|还有|；另一条|，另一条|；而|，而|和(?!平)"
    r"|，(?=\d{1,2}月\d{1,2}[号日])")  # 逗号后紧跟日期 = 新候选 ("看直播，6月16日的钉钉")
_DISAMB_PREFIX_RES = [  # 段内前导剥离: 枚举引导语/数量声明, 应用一次
    re.compile(r"^.*?条记录是"),       # "最近一条记录是咪咕快游…"
    re.compile(r"^.*?条是"),           # "一条是QQ…" / "…有两条视频通话记录，一条是…"
    re.compile(r"^.*?记录有"),         # "…最近办过的记录有QQ…"
    re.compile(r"^.*?分别是"),
    re.compile(r"^.*?[两三四五六七八九\d]条[^，；]*?记录[，,]?"),  # "有两条咪咕视频540p的记录，"
]
_CN_NUM = "一二三四五六七八九"
_DAYPART_HOUR = {"凌晨": 0, "早上": 0, "上午": 0, "中午": 12, "下午": 12, "晚上": 12}


def _llm_pick(llm, utterance: str, listing: list) -> int | None:
    """v27.4: 规则解析失败时的 LLM 兜底序号抽取 (方案 B 用户定案)。
    只做信息抽取 (选中序号), 不做状态写; 字段值从 records 查表。"""
    try:
        import candidate_llm as _cl
        return _cl.pick_index_llm(llm, utterance, listing)
    except Exception:
        return None


def _cl_judge_selection(llm, utterance: str, listing: list) -> dict:
    """v27.6: 选择轮判定+选中解析一体化 LLM 调用 (用户定案: 不用话术正则)。"""
    try:
        import candidate_llm as _cl
        return _cl.judge_selection_turn(llm, utterance, listing)
    except Exception:
        return {"is_selection": False, "pick": None}


def _resolve_listing(llm, agent_reply: str, records: list) -> list | None:
    """v28.4: 候选列表属性匹配 (规则快路径→LLM 兜底)。"""
    try:
        import candidate_llm as _cl
        _r = _cl.resolve_listing_rule(agent_reply, records)
        if _r:
            return _r
        return _cl.resolve_listing_llm(llm, agent_reply, records)
    except Exception:
        return None


def _llm_parse_listing(llm, agent_reply: str, records: list) -> list | None:
    """v27.4: 话术解析失败时的 LLM 兜底顺序解析。"""
    try:
        import candidate_llm as _cl
        return _cl.parse_listing_llm(llm, agent_reply, records)
    except Exception:
        return None

# 选择轮选中项解析 (2026-09-28 v27.3): 序数语义 = 候选明细顺序, 不再交模型数。
# v27.3-d930: "候选N" 指称 (d930 话术主形态 "候选1是…候选2是…您选候选2吧") 并入序数族。
_ORD_SEL_RE = re.compile(r"(?:就按|就选|就要|就取|就|选|按|用|取)\s*(?:第([一二三四五六七八九十\d]+)\s*[条个]|候选\s*([一二三四五六七八九十\d]+))")
_ORD_NEG_RE = re.compile(r"(?:不选|不要|排除|去掉|取消)\s*(?:第([一二三四五六七八九十\d]+)\s*[条个]|候选\s*([一二三四五六七八九十\d]+))")
_ORD_ANY_RE = re.compile(r"第([一二三四五六七八九十\d]+)\s*[条个]|候选\s*([一二三四五六七八九十\d]+)")
_ORD_LATEST_RE = re.compile(r"最近的?一?条|最新的?一?条")


def _cn2int(s: str) -> int:
    return int(s) if s.isdigit() else _CN_NUM.index(s) + 1


def _parse_pick_ord(utterance: str) -> int | None:
    """选择轮序数解析 -> 1-based 候选序号 或 None。
    动词紧邻优先 ("选第二条"/"选候选2"); 前置否定 ("不选第一条"/"候选1不要") 排除;
    全部序数去否定后唯一兜底。"第二条不要, 就第一条吧"(否定后置) 由动词紧邻命中 "就第一条"。
    "候选1不要了" 型: 否定词后置于候选词 — 兜底时把"候选N+不要/不选/取消"也计入 neg。
    """
    utt = utterance or ""
    neg = {_cn2int(m.group(1) or m.group(2)) for m in _ORD_NEG_RE.finditer(utt)}
    neg |= {int(_cn2int(m.group(1) or m.group(2))) for m in
            re.finditer(r"候选\s*([一二三四五六七八九十\d]+)\s*(?:不要|不选|不要了|排除)", utt)}
    neg |= {_cn2int(m.group(1)) for m in
            re.finditer(r"第([一二三四五六七八九十\d]+)\s*[条个]\s*(?:不要|不选|排除)", utt)}
    # 枚举否定串: "候选1和候选3不要" — 否定词前列举的全部候选都排除
    for m in re.finditer(r"((?:候选\s*[一二三四五六七八九十\d]+\s*和\s*)+候选\s*[一二三四五六七八九十\d]+\s*)不要", utt):
        neg |= {int(_cn2int(x)) for x in re.findall(r"候选\s*([一二三四五六七八九十\d]+)", m.group(1))}
    m = _ORD_SEL_RE.search(utt)
    if m:
        n = _cn2int(m.group(1) or m.group(2))
        if n not in neg:
            return n
    ords = [_cn2int(x.group(1) or x.group(2)) for x in _ORD_ANY_RE.finditer(utt)]
    rest = [n for n in ords if n not in neg]
    if len(rest) == 1:
        return rest[0]
    return None


def _cand_attrs(seg: str) -> dict:
    """从候选描述片段抽取可匹配属性: app/service/rtt/resolution/日期/时点(小时)。"""
    from schema import KNOWN_APPLICATIONS
    a: dict = {}
    for name in sorted(KNOWN_APPLICATIONS, key=len, reverse=True):
        if name in seg:
            a["app"] = name
            break
    for svc in ("视频通话", "开直播", "看直播", "云游戏", "视频", "会议"):
        if svc in seg:
            a["svc"] = svc
            break
    m = re.search(r"(\d+)\s*(?:ms|毫秒)", seg)
    if m:
        a["rtt"] = int(m.group(1))
    m = re.search(r"(\d{3,4})p", seg, re.I)
    if m:
        a["res"] = m.group(1).lower() + "p"
    m = re.search(r"(\d{1,2})月(\d{1,2})[号日]", seg)
    if m:
        a["md"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"(凌晨|早上|上午|中午|下午|晚上)(\d{1,2})点", seg)
    if m:
        a["hour"] = (_DAYPART_HOUR[m.group(1)] + int(m.group(2))) % 24
    return a


def _rec_match_score(attrs: dict, rec: dict) -> int:
    """候选片段 vs 画像 record(recent_records 扁平格式) 的属性匹配分。"""
    s = 0
    if attrs.get("app") and attrs["app"] == rec.get("application_name"):
        s += 3
    if attrs.get("svc") and attrs["svc"] == rec.get("service_name"):
        s += 2
    if attrs.get("rtt") is not None:
        m = re.search(r"(\d+)", str(rec.get("rtt") or ""))
        if m and int(m.group(1)) == attrs["rtt"]:
            s += 3
    if attrs.get("res") and attrs["res"] == str(rec.get("resolution") or "").lower():
        s += 2
    ca = str(rec.get("completed_at") or "")
    m = re.search(r"(\d{1,2})月(\d{1,2})日(\d{1,2})时", ca)
    if m:
        if attrs.get("md") and attrs["md"] == (int(m.group(1)), int(m.group(2))):
            s += 2
        if attrs.get("hour") is not None and attrs["hour"] == int(m.group(3)):
            s += 2
    return s


def _parse_disamb_candidates(agent_reply: str, records: list[dict]) -> list[dict] | None:
    """把 agent 话术里的记录候选按话术顺序映射到画像 records。

    返回有序 record 列表; 候选<2 / 片段匹配不到 / 声明条数不符 -> None (回退纯文字规则)。
    """
    if not agent_reply or not records:
        return None
    text = _DISAMB_QCUT_RE.split(agent_reply or "")[0]
    segs = [s.strip("，。；、 ") for s in _DISAMB_SPLIT_RE.split(text)]
    # 段内前导剥离 + 纯属性残片(rtt/清晰度, 无 app 无日期) 且不与前段冲突时向前合并
    # ("一条是QQ，清晰度360p、时延150毫秒" 被顿号劈开 -> 并回; 但 "晚上9点…50ms" 与
    #  前段 rtt 冲突 -> 是下一个候选, 不并)
    merged: list[str] = []
    for seg in segs:
        # 2026-09-28 S-000140: 段首纯属性残片属于上一候选——
        # "爱奇艺看直播、720p、50ms，抖音那条是开直播" 顿号/逗号切分后 "50ms" 被带进下一段首,
        # 是上一候选 (爱奇艺) 的 rtt; 切回, 否则下候选失 rtt 且本段匹配错记录 (虎牙 4 分同分抢占)。
        _mhead = re.match(r"^(\d{1,4}\s*(?:ms|毫秒)|\d{3,4}\s*[pP])[，,、]\s*(.+)$", seg)
        if _mhead and merged:
            merged[-1] += "，" + _mhead.group(1)
            seg = _mhead.group(2).strip("，。；、 ")
            if not seg:
                continue
        for pre in _DISAMB_PREFIX_RES:
            new = pre.sub("", seg, count=1)
            if new != seg:
                # S-000140: "抖音那条是开直播" 型——"^.*?条是" 剥离跨界吞掉本候选 app。
                # 剥前有 app 而剥后无 -> 回补 (引导语 app 在上一段, 此情形即剥前 app=本候选主体)。
                _ba, _aa = _cand_attrs(seg), _cand_attrs(new)
                if _ba.get("app") and not _aa.get("app"):
                    new = _ba["app"] + " " + new
                seg = new.strip("，。；、 ")
                break
        if not seg:
            continue
        a = _cand_attrs(seg)
        # 单 app 的问句/评述残段不是候选 ("您选虎牙还是钉钉这条?" -> {app:虎牙})
        if set(a) == {"app"} and re.search(r"还是|哪条|哪一|对不上|吗|？|\?", seg):
            continue
        frag = set(a) <= {"rtt", "res"}  # 纯属性残片
        if frag and merged:
            pa = _cand_attrs(merged[-1])
            if not (set(a) & set(pa)):  # 属性不冲突才并回上一候选
                merged[-1] += "，" + seg
                continue
        # v27.3.3-d930: "第一条是云游戏，6月25号凌晨2点完成" 型——svc 与日期被逗号切开,
        # 纯日期/时段残段并回上一段 (使其获得 md/hour; 否则 '仅svc' 段打分 <3 -> 整体 None)
        if set(a) <= {"md", "hour"} and merged:
            merged[-1] += "，" + seg
            continue
        merged.append(seg)
    declared = re.search(r"([两三四五六七八九\d])条", agent_reply)
    decl_n = None
    if declared:
        g = declared.group(1)
        decl_n = int(g) if g.isdigit() else (2 if g == "两" else _CN_NUM.index(g) + 1)
    cands: list[dict] = []
    taken: set[int] = set()
    for seg in merged:
        attrs = _cand_attrs(seg)
        if not any(k in attrs for k in ("app", "rtt", "res", "md")):
            continue  # 无识别属性的片段 (前导/连接语) 跳过
        best_i, best_s = None, 0
        for i, rec in enumerate(records):
            if i in taken:
                continue
            sc = _rec_match_score(attrs, rec)
            if sc > best_s:
                best_i, best_s = i, sc
        if best_i is None or best_s < 3:
            return None  # 有属性却匹配不到 -> 解析不可靠, 回退
        taken.add(best_i)
        cands.append(records[best_i])
    if len(cands) < 2:
        return None
    if decl_n is not None and decl_n != len(cands):
        return None  # 话术声明条数与解析条数不符 -> 回退
    return cands


# v24: 无约束"按上次记录"严格规则 (数据集已修复: "上次"无约束 = completed_at 最新一条,
# 与 profile 习惯值/语义过滤无关)。触发条件:
# 1) utterance 引用记录 ("记录/历史", 或 "上次/之前" 且非"跟上次一样"型 prev 沿用)
# 2) 记录指代短语内无筛选条件: app/service 须出现在"上次X那条/按X的记录"式紧邻结构
#    才算筛选 (句首"保障微信的视频通话…时延按上次记录"里的 app 是新意图目标, 不是筛选)
# 3) 画像有 records
_RECORD_REF_RE = re.compile(r"记录|历史")
_RECORD_REF_LOOSE_RE = re.compile(r"上次|之前")
_PREV_COREF_RE = re.compile(r"跟上次一样|和上次一样|像上次|照旧|跟之前一样|和之前一样")


def _record_constraint_hit(utterance: str) -> bool:
    """记录指代短语内是否有筛选条件 (app/service/日期/序数紧邻记录词)。"""
    utt = utterance or ""
    # 序数: "第N条/个" 恒为记录筛选
    if re.search(r"第[一二三四五六七八九十\d]+\s*[条个]", utt):
        return True
    # 日期紧邻: "7月8号(的)?(那条|那次|记录)" / "7月4号完成的那次会议记录"
    if re.search(r"\d{1,2}月\d{1,2}[号日]\s*的?\s*(那条|那次|那场|记录)", utt):
        return True
    if re.search(r"\d{1,2}月\d{1,2}[号日][^，。；]{0,8}?(那条|那次|那个|记录)", utt):
        return True
    # app/service 紧邻记录词: "上次哔哩哔哩那条" / "按云游戏的记录" / "哔哩哔哩那条记录"
    names = sorted(KNOWN_APPLICATIONS | {"视频通话", "视频", "开直播", "看直播", "会议", "云游戏"},
                   key=len, reverse=True)
    for name in names:
        if re.search(rf"(上次|之前|历史)\s*的?\s*{re.escape(name)}", utt):
            return True
        if re.search(rf"{re.escape(name)}\s*的?\s*(那条|那次|那场)", utt):
            return True
    return False


def _needs_latest_record_rule(utterance: str, user_context: dict | None) -> bool:
    utt = utterance or ""
    recs = (user_context or {}).get("recent_records") or []
    if not recs:
        return False
    if _PREV_COREF_RE.search(utt):
        return False
    # T3-3 预选轮 ("参考最近办过的记录"/"按选中的那条"/"帮我查一下…记录"): 记录字段 GT 悬空待消歧, 不取 records[0]
    if re.search(r"选中|办过|参考|查.{0,18}(记录|历史)", utt):
        return False
    if not (_RECORD_REF_RE.search(utt) or _RECORD_REF_LOOSE_RE.search(utt)):
        return False
    return not _record_constraint_hit(utt)


# v26.3 P1: 应用名未明说/待补规则 (config pending_app_rule.enabled)。
# 动机 (d6 candmap 8 session 实证): turn1 用户只说服务词不说 app 时, 模型被
# "application_name 必须取 schema Literal"约束逼着从 records/矩阵猜 app
# (S-000149 猜哔哩哔哩 / S-000155 猜YY / S-000244 fallback 优酷), turn2 用户补真 app 后
# 猜测头变幻影意图。修法 = prompt 放开"允许留空"(时间等字段照记) + 身份判定补齐规则;
# harness 侧归并链 (v26.1 无 app 桩归并) 已就绪, 只差生成侧许可。
_OOS_APPS = ("豆瓣", "京东", "知乎", "美团", "网易云音乐")
_SERVICE_WORDS = ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频")


def _utterance_mentions_app(utterance: str) -> bool:
    """话语是否明说任何 app (矩阵内 + 域外)。"""
    utt = utterance or ""
    return any(a in utt for a in list(KNOWN_APPLICATIONS) + list(_OOS_APPS))


def _pending_stubs(id_states: dict[str, dict] | None) -> list[tuple[str, str]]:
    """已有意图中"应用名待补"的桩: [(iid, 扁平描述)] (id 协议下 prev 以 id 为键)。"""
    from case_format import flatten_params
    out = []
    for iid, v in sorted((id_states or {}).items(),
                         key=lambda x: int(x[0][1:]) if x[0].startswith("I") else 999):
        f = flatten_params(v or {})
        if not f.get("application_name"):
            desc = "/".join(x for x in ("?", f.get("service_name")) if x and x != "?") or "(空)"
            out.append((iid, f"应用待补/{desc}"))
    return out


# ====================================================================
# v27: 时间锚点 + 明说时间证据 + 时间口径规则 (docs/plan_time_anchor_20260920.md)
# 生成侧证据注入: time_extractor 确定性预解析, 模型引用结论, 不做后处理覆盖。
# ====================================================================
import time_extractor as _tex

_PROFILE_REF_RE = re.compile(r"平时|习惯")
_RECORD_REF_ANY_RE = re.compile(r"上次|最近.{0,4}条|记录|平时|习惯")


def _profile_slot_conflict(user_context: dict | None) -> tuple[str, str, int] | None:
    """画像"常用开始时段"与"常用保障时长"数值冲突 -> (时段, 时长, 时段跨度分钟)。"""
    prof = (user_context or {}).get("profile") or {}
    slot, dur = prof.get("常用开始时段"), prof.get("常用保障时长")
    if not slot or not dur:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", str(slot).strip())
    dm = re.fullmatch(r"(\d+)min", str(dur).strip())
    if not m or not dm:
        return None
    span = (int(m.group(3)) * 60 + int(m.group(4))) - (int(m.group(1)) * 60 + int(m.group(2)))
    if span <= 0:
        span += 24 * 60  # 跨夜时段
    if span == int(dm.group(1)):
        return None
    return str(slot), str(dur), span


def _build_time_hint(features, utterance: str, reference_time: str,
                     user_context: dict | None) -> tuple[str | None, Any]:
    """组装时间相关 prompt 注入段 (TIME ANCHOR / TIME EVIDENCE / TIME RULE)。

    各段独立开关独立消融; 全部关闭或 reference_time 不可解析时返回 (None, None)。
    返回值: (注入文本, time_extractor 的 ev) —— ev 供候选列举轮时间桩兜底复用
    (v27.3, 与模型所见同一预解析值)。
    """
    if not (features.time_anchor or features.time_extractor
            or features.time_rule_contradiction or features.time_rule_profile_slot
            or features.time_rule_explicit_first or features.time_rule_candidate):
        return None, None
    ref_dt = _tex.parse_reference_time(reference_time)
    if ref_dt is None:
        return None, None

    need_ev = (features.time_extractor or features.time_rule_contradiction
               or features.time_rule_explicit_first or features.time_rule_candidate)
    ev = _tex.extract(utterance, ref_dt) if need_ev else None

    parts: list[str] = []
    # ① TIME ANCHOR: 相对日期词触发的预计算日期表 (查表代替算术)
    if features.time_anchor:
        table = _tex.build_date_table(ref_dt, utterance)
        if table:
            rows = "、".join(f"{w}={d}" for w, d in table)
            parts.append(
                "# === TIME ANCHOR (本轮日期锚点, 系统已预计算) ===\n"
                f"# {rows}\n"
                "# 相对日期一律按上表查取, 禁止自行加减天数推算。\n")
    # ② TIME EVIDENCE: 明说时间预解析候选 (只给证据, 绑定/落槽由模型决定)
    if features.time_extractor and ev is not None:
        lines: list[str] = []
        for mn in ev.mentions:
            abs_str = _tex._fmt(mn.dt) if mn.dt else f"时刻{mn.clock}(日期按语境定)"
            role = f" [{mn.role}]" if mn.role in ("start", "end") else ""
            lines.append(f"#   \"{mn.raw}\" -> {abs_str}{role}")
        for raw, dmin in ev.durations:
            lines.append(f"#   \"{raw}\" -> duration={dmin}min")
        if ev.start is not None and ev.end is not None and not ev.has_contradiction:
            dur_txt = f", duration={ev.duration_min}min" if ev.duration_min is not None else ""
            lines.append(f"# 组装(一致): start={_tex._fmt(ev.start)}, end={_tex._fmt(ev.end)}{dur_txt}")
        if lines:
            parts.append(
                "# === TIME EVIDENCE (本轮话语明说时间的系统预解析, 直接引用勿重算) ===\n"
                + "\n".join(lines) + "\n")
    # ③a 矛盾保字面
    if features.time_rule_contradiction and ev is not None and ev.has_contradiction:
        parts.append(
            "# === TIME RULE (检测到明说时间互相矛盾, 本轮生效, 优先级最高) ===\n"
            f"# 系统解析: start={_tex._fmt(ev.start)}, end={_tex._fmt(ev.end)}"
            + (f", duration={ev.duration_min}min" if ev.duration_min is not None else "")
            + ", 存在矛盾。\n"
            "# 按口径照字面录入用户明说值, 禁止自行调和/翻转/倒推覆盖, 等待用户后续更正。\n")
    # ③b 画像时段优先 (时段与时长冲突时; 仅当用户提到"时段"才触发——
    # 只说"时长按平时"时 GT 口径就是取时长, 误触发曾致 S-000080/125 回退, v27 fix_run1 实证)
    if features.time_rule_profile_slot and _PROFILE_REF_RE.search(utterance) \
            and "时段" in utterance:
        conflict = _profile_slot_conflict(user_context)
        if conflict:
            slot, dur, span = conflict
            parts.append(
                "# === TIME RULE (画像时段/时长不一致, 本轮生效, 优先级最高) ===\n"
                f"# 画像\"常用开始时段\"={slot}(跨度{span}min) 与 \"常用保障时长\"={dur} 不一致。\n"
                "# 按口径一律以\"常用开始时段\"为准: start/end 取时段起止, "
                f"duration={span}min, 忽略\"常用保障时长\"。\n")
    # ③c 明说时间优先于画像/记录
    if features.time_rule_explicit_first and ev is not None \
            and any(mn.dt for mn in ev.mentions) and _RECORD_REF_ANY_RE.search(utterance):
        parts.append(
            "# === TIME RULE (本轮明说时间优先, 本轮生效, 优先级最高) ===\n"
            "# 用户本轮明说了时间(见 TIME EVIDENCE)。时间槽(start/end/duration)一律以\n"
            "# 本轮明说为准, 禁止取 records 的 completed_at 或画像时段覆盖/替代明说时间。\n")
    # ③d 候选时段不落槽
    if features.time_rule_candidate and ev is not None and ev.has_candidate:
        # v28.3: 未定词槽位限定修正 (time_extractor) 后, has_candidate 只表示话语含
        # 未定词/候选信号, 组装是否被抑制取决于"未定的对象是否包含时间"。组装成功
        # (start/end 有值) 时, 明说时间已确立——旧文案"start/end 一律留空"会把这些
        # 已确立时间也压掉 (533/1732 型: 应用未定≠时间未定, T2 类 8 例残余根因)。
        if ev.start is not None or ev.end is not None:
            parts.append(
                "# === TIME RULE (部分槽位候选未定, 本轮生效, 优先级最高) ===\n"
                "# 用户话语含未定词, 但时间本体已明说且无歧义 (见 TIME EVIDENCE 组装结论)。\n"
                "# 未定的只是其他槽位 (如应用/时延)——start/end 按 TIME EVIDENCE 组装结论\n"
                "# 【照常落地】, 不要因其他槽位未定而把时间一并留空。\n")
        else:
            parts.append(
                "# === TIME RULE (候选时段未定, 本轮生效, 优先级最高) ===\n"
                "# 用户给出多个候选时段且尚未最终确定(\"或者/还没定/不确定\")。\n"
                "# start/end 一律留空, 禁止自行挑选某个候选落槽; 只登记无歧义字段(如明说 duration)。\n")

    return ("\n".join(parts) if parts else None), ev


def _is_correction_case(case) -> bool:
    """纠正类 demo: utterance 含改口词 且 delta 覆盖 prev_state 已有字段为新值。"""
    if not _is_correction(case.utterance):
        return False
    from case_format import flatten_params
    prev = flatten_params(case.prev_state or {})
    delta = flatten_params(case.delta or {})
    for k, v in delta.items():
        vv = v.get("ref") if isinstance(v, dict) else v
        pv = prev.get(k)
        if isinstance(vv, str) and isinstance(pv, str) and pv and pv != vv:
            return True
    return False


def _retrieve_correction_demos(retriever: "DiverseRetriever", utterance: str,
                               prev_state: dict, n: int,
                               exclude: set) -> list:
    """从全库选纠正类 demo (v12), 按 embedding 相似度排序, 跳过已检索到的。"""
    bank = retriever.bank
    if len(bank) == 0:
        return []
    if not hasattr(bank, "_corr_flags"):
        bank._corr_flags = [_is_correction_case(c) for c in bank.cases]
    qvec = bank.query_vec(prev_state or {}, utterance)
    sims = bank.embedding_scores(qvec)
    out = []
    for idx in np.argsort(-sims):
        if len(out) >= n:
            break
        if not bank._corr_flags[idx]:
            continue
        c = bank.cases[idx]
        if c.case_id in exclude:
            continue
        out.append(RetrievedCase(
            case_id=c.case_id, utterance=c.utterance, intent=c.intent,
            prev_state=c.prev_state, delta=c.delta, validity=c.validity,
            invalid_reason=c.invalid_reason, reference_time=c.reference_time,
            score=float(sims[idx]), emb_score=float(sims[idx])))
    return out


def _identity_ok(code: str, id_on: bool) -> bool:
    """v26.2 身份判定 PAR 校验: id 协议下每个 # intent 段头前必须有 '# 身份判定:' 行。"""
    if not id_on:
        return True
    segs = re.split(r"#\s*intent:", code)[1:]
    if not segs:
        return True
    for seg in segs:
        if "身份判定" not in seg.split("\n")[0] and "身份判定" not in (code.split(segs[0])[0] if seg is segs[0] else ""):
            # 段头前的最近文本(上一段尾或正文)需含身份判定行
            pass
    # 简化判定: 身份判定行数量 >= intent 段数量
    n_id = len(re.findall(r"#\s*身份判定", code))
    n_hdr = len(re.findall(r"#\s*intent:", code))
    return n_id >= n_hdr


def _need_par(utterance: str, merged_prev: dict[str, Any]) -> bool:
    """是否触发 PAR 校验:
    1) utterance 含"上次/平时/记录"且 prev_state 空 (首轮 coreference)
    2) prev_state 非空 + utterance 含取消词 (取消意图场景, 需校验输出 # cancel)
    """
    if merged_prev:
        return any(w in utterance for w in _CANCEL_WORDS)
    return any(w in utterance for w in ("上次", "平时", "记录", "历史"))


def _summaries_ok(code: str) -> bool:
    """Variant A PAR 校验: 每个 intent 段前都有 summary, 且含 record 选择。"""
    summaries = parse_summaries(code)
    if not summaries:
        return True  # 无 intent 段(非保障请求), 不触发
    for key, txt in summaries.items():
        if not txt:
            return False
        # coreference 场景需 summary 提及 records/profile 选择
    return True


def _cancel_check_ok(utterance: str, code: str) -> bool:
    """取消场景校验: utterance 含取消词时, 输出必须含 # cancel 段。
    返回 True 表示通过(有 cancel 或 无取消词不触发)。
    """
    if not any(w in utterance for w in _CANCEL_WORDS):
        return True
    from python_state import parse_cancels
    return len(parse_cancels(code)) > 0


class IntentPipeline:
    """单意图 pipeline (main.py demo 用)。评估用 MultiIntentPipeline。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.features = Features.from_config(cfg)   # v26.4: 机制开关单一事实源
        self.embedder = Embedder(cfg["embedder"])
        self.bank = CaseBank.load(cfg["retriever"]["case_bank_path"], self.embedder)
        rcfg = cfg["retriever"]
        self.retriever = DiverseRetriever(
            self.bank,
            top_k=rcfg["top_k"],
            pool_n=rcfg["pool_n"],
            alpha=rcfg["alpha"],
        )
        self.llm = LLMClient(cfg["llm"])

    def run(
        self,
        utterance: str,
        reference_time: str,
        prev_state: dict[str, Any] | None = None,
    ) -> PipelineResult:
        prev_state = prev_state or {}
        retrieved = self.retriever.retrieve(utterance, prev_state)

        system, task_prompt = build_prompt(
            utterance, reference_time, prev_state, retrieved, None
        )
        raw = self.llm.gen_raw_code(system, task_prompt)
        segs = execute_program_multi(raw, prev_state or None)

        best_delta = segs[0][1] if segs else {}
        llm_output = {"intent": "unknown", "params": best_delta}
        final = validate(llm_output, reference_time, prev_state)

        return PipelineResult(
            utterance=utterance,
            reference_time=reference_time,
            prev_state=prev_state,
            retrieved=[
                {"case_id": r.case_id, "intent": r.intent,
                 "validity": r.validity, "score": round(r.score, 4)}
                for r in retrieved
            ],
            best=llm_output,
            final=final,
        )


def load_config(path: str = "configs/config_d6_qwen_plus_best.json") -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def intent_key(app: str | None, svc: str | None) -> str:
    """统一 intent_key 格式: app/service。"""
    a = (app or "").strip()
    s = (svc or "").strip()
    if not a and not s:
        return ""
    if not s:
        return a
    return f"{a}/{s}"


# v26.3 P3 multi_instance_key: 同 app/svc 多意图实例化 (config multi_instance_key.enabled)
# 实例 key = "app/svc#N" (N≥2); 单实例保持原 key, 兼容存量数据。模型不可见——
# id 协议下模型只写 I1/new, 实例后缀是 harness 内部内容 key 形态。
def _base_key(k: str) -> str:
    """剥实例后缀: '淘宝/看直播#2' -> '淘宝/看直播'。"""
    return k.split("#", 1)[0] if "#" in k else k


def _instanced_key(k: str, results: dict, enabled: bool,
                   prior_keys: dict | list | None = None) -> str:
    """同 base key 已存在(本轮 results 或历史 prior_keys)时分配下一个实例后缀;
    enabled=false 或无冲突返回原 key。

    v26.3 P3 修复: prior_keys = 透传中的既有实例 key 集合(prev_states/session_out),
    跨轮场景(上轮建了 #1, 本轮再 new 同 base)必须延续编号, 否则新实例写回 base key
    与历史实例在透传层互相覆盖(S-000035 turn2)/被 cancel 误杀(S-000225 turn2)。
    """
    if not enabled:
        return k
    base = _base_key(k)
    pool = set(results)
    if prior_keys:
        pool |= set(prior_keys)
    existing = {(_base_key(x), x) for x in pool if not x.startswith("__cancel__")}
    same = [x for b, x in existing if b == base]
    if not same:
        return k
    n = 2
    while f"{base}#{n}" in pool:
        n += 1
    return f"{base}#{n}"


class MultiIntentPipeline(IntentPipeline):
    """方案 A: 一次 LLM 调用输出多意图 (B2 IDIC-DST 多意图并发范式)。

    run_multi(utterance, reference_time, prev_states, user_context) -> {intent_key: final}
    含 PAR 校验回路 + record coreference 兜底。
    """

    def run_multi(
        self,
        utterance: str,
        reference_time: str,
        prev_states: dict[str, dict[str, Any]] | None = None,
        user_context: dict[str, Any] | None = None,
        agent_reply: str | None = None,
        prev_statuses: dict[str, str] | None = None,
        session_disambig: bool = False,
        memory_hint: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        prev_states = prev_states or {}
        merged_prev: dict[str, Any] = {}
        for v in prev_states.values():
            merged_prev.update(v or {})

        retrieved = self.retriever.retrieve(utterance, merged_prev)

        # v12: 改口/否定轮注入纠正类 demo (config correction_demo.enabled, 回退置 false)
        if self.features.correction_demo and _is_correction(utterance):
            extra = _retrieve_correction_demos(
                self.retriever, utterance, merged_prev,
                n=int((self.cfg.get("correction_demo") or {}).get("n", 2)),
                exclude={c.case_id for c in retrieved})
            retrieved = extra + retrieved

        # 可选上下文开关 (独立消融, 默认 false 即现状):
        #   agent_context.enabled — 上一轮 agent 回复进 prompt
        #   status_prompt.enabled — 各意图状态快照进 prompt
        ac_on = self.features.agent_context
        sp_on = self.features.status_prompt

        # v21: 消歧轮规则注入 (修补② T3-3, config disambiguation_rule.enabled)
        # 触发时把"候选不得新建意图/沿用意怈头/按指代取字段"规则 + 现有 intent_key 写进 prompt。
        disamb_keys: list[str] | None = None
        disamb_cands: list[dict] | None = None
        if self.features.disambiguation_rule and \
                _is_disambiguation_turn(utterance, agent_reply, prev_states):
            disamb_keys = sorted(prev_states.keys())
            # v24.3: 候选顺序对照 (config disambiguation_rule.candidate_map):
            # harness 解析 agent 话术候选顺序 -> "第N条=记录字段明细" 直接注入,
            # 消除模型自行对齐两套顺序的负担; 解析失败回退纯文字规则。
            if self.features.candidate_map:
                # v28.4: 话术属性匹配 (规则→LLM 兜底) 取代旧正则解析作为话术序首选来源;
                # 解析不到回退 _parse_disamb_candidates (纯正则, 覆盖 LLM 也失败的形态)。
                disamb_cands = _resolve_listing(
                    self.llm, agent_reply, (user_context or {}).get("recent_records") or [])
                if disamb_cands is None:
                    disamb_cands = _parse_disamb_candidates(
                        agent_reply, (user_context or {}).get("recent_records"))

        # v22: 改口轮规则注入 (修补④, config correction_rule.enabled)
        # 改口词 + prev_state 非空 -> prompt 注入"沿用旧意图头/只改被更正字段/其余沿用"规则。
        corr_keys: list[str] | None = None
        if self.features.correction_rule and \
                prev_states and _is_correction(utterance):
            corr_keys = sorted(prev_states.keys())

        # v24: 无约束"按上次记录"严格规则 (config record_rule.enabled)
        # 数据集已修复: "上次"无约束 = completed_at 最新一条(records[0]), 与 profile 无关。
        # 与消歧轮互斥 (T3-3 的选择走 disambiguation_rule 的 agent 话术序)。
        # v27.1: record_selector 开时作为 record_rule 的上游统一入口——先跑判定树,
        # defer(unique/latest/defer), record_rule 仅在 selector 无产出时兜底 (触发域互补, 防双注入)。
        record_rule_hint: str | None = None
        record_ev_hint: str | None = None
        if self.features.record_selector:
            import record_selector as _rse
            tpl_t3_3 = bool(re.search(r"查.{0,18}(?:记录|历史)|选中|办过|参考|最终选中", utterance)) \
                or (agent_reply is not None and _rse.AGENT_DISAMB_RE.search(agent_reply or ""))
            # 消歧类会话信号: 消歧规则已触发(上轮 agent 列候选) 或 本轮为预选查询式
            ev = _rse.record_evidence(
                utterance, user_context, agent_reply,
                disambiguation_active=bool(disamb_keys is not None) or getattr(self, "_session_is_t33", False))
            if ev is not None:
                record_ev_hint = _rse.render_hint(ev)
            if record_ev_hint is None and self.features.record_rule and disamb_keys is None \
                    and _needs_latest_record_rule(utterance, user_context):
                r0 = (user_context or {}).get("recent_records", [{}])[0]
                record_rule_hint = (
                    f"application_name={r0.get('application_name')}, service_name={r0.get('service_name')}, "
                    f"resolution={r0.get('resolution')}, rtt={r0.get('rtt')}, "
                    f"completed_at={r0.get('completed_at')}"
                )
        elif self.features.record_rule and disamb_keys is None \
                and _needs_latest_record_rule(utterance, user_context):
            r0 = (user_context or {}).get("recent_records", [{}])[0]
            record_rule_hint = (
                f"application_name={r0.get('application_name')}, service_name={r0.get('service_name')}, "
                f"resolution={r0.get('resolution')}, rtt={r0.get('rtt')}, "
                f"completed_at={r0.get('completed_at')}"
            )

        # v27.2.1: QUALITY HINT (画质条目高亮) — 话语提及 app/画质词时查标准表注入精确值。
        # 背景: 反常识条目 (如 高清@淘宝/看直播=1080p) 模型倾向落默认 720p, 表遵从波动。
        quality_hint: str | None = None
        if getattr(self.features, "quality_hint", False):
            from schema import QUALITY_MAP as _QMAP, KNOWN_APPLICATIONS as _KA
            _qw_set = set(m.group(1) for m in re.finditer("(超清|高清|标清|蓝光|流畅)", utterance))
            _apps_in_utt = [a for a in _KA if a in utterance]
            _hits = []
            for _qw in _qw_set:
                _cands = {}
                for (a, sv, w), val in _QMAP.items():
                    if w == _qw and a in _apps_in_utt:
                        _cands.setdefault(a, []).append((sv, val))
                for a, sv_vals in _cands.items():
                    if len(sv_vals) == 1:
                        _hits.append((a, sv_vals[0][0], _qw, sv_vals[0][1]))
                    else:
                        _best = None
                        for sv, val in sv_vals:
                            _pos_svc = utterance.find(sv)
                            _pos_w = utterance.find(_qw)
                            if _pos_svc >= 0:
                                _d = abs(_pos_svc - _pos_w)
                                if _best is None or _d < _best[0]:
                                    _best = (_d, sv, val)
                        if _best:
                            _hits.append((a, _best[1], _qw, _best[2]))
                # v27.2.1 补: 话语无 app (app 来自记录/画像) 时, 用 record_rule 定位的 records[0]
                # 或消歧记录的 app/svc 查表 — S-000114 型 ("应用按上次记录, 画质用高清")
                if not _cands and (record_rule_hint or record_ev_hint):
                    _src = record_rule_hint or record_ev_hint
                    import re as _re2
                    _m = _re2.search(r"application_name=([^,]+), service_name=([^,]+)", _src)
                    if _m:
                        _ra, _rsv = _m.group(1).strip(), _m.group(2).strip()
                        _val = _QMAP.get((_ra, _rsv, _qw))
                        if _val:
                            _hits.append((_ra, _rsv, _qw, _val))
            if _hits:
                _qlines = ["#   {a}/{sv} 的\"{w}\" = {v}".format(a=a, sv=sv, w=w, v=v) for a, sv, w, v in _hits]
                quality_hint = (
                    "# === QUALITY HINT (画质词精确映射, 系统查表, 优先级最高) ===\n"
                    + "\n".join(_qlines) + "\n"
                    "# 上表为标准映射的精确值, 直接采用; 禁止使用默认值覆盖。\n")

        # v27.2: 候选模式 (config candidate_mode.enabled, 消歧会话信号域内)
        # 触发: session_disambig + record_selector 判定树 defer(多候选/预选查询式)
        # -> t1 注入 CANDIDATE MODE 列举指令(候选明细来自判定树); t2 选择语 -> 落地/移除指令
        cand_mode_hint: str | None = None
        _listed_this_turn = False  # 列举轮标记 (供时间桩兜底)
        if getattr(self.features, "candidate_mode", False) and getattr(self, "_session_is_t33", False):
            import candidate_book as _cb
            from record_selector import record_evidence as _rev
            # v27.6 (2026-10-13 用户定案): 选择轮判定不再靠话术正则 (select_phrase),
            # 改用 harness 状态机 + LLM 判断——列举轮后进入"待选择"状态 (_cand_listing
            # 非空), 下一轮由 LLM 判断是否在选择及选中第几条。正则只作快速路径注释。
            _pending = getattr(self, "_cand_listing", None)
            if _pending:
                _judge = _cl_judge_selection(self.llm, utterance, _pending)
                if not _judge["is_selection"]:
                    self._cand_listing = None  # 用户没在选择, 退出候选模式
                    _pending = None
            if _pending:
                # v27.6: LLM 判定为选择轮, pick 由 judge 一次给出
                # v27.2 双跑教训: a/b 分支交模型自判是二义的 (run2 三例 T3-3 各走错一支)。
                # harness 确定性判定: 桩意图 application_name 为空 (t1 悬空桩) -> 1a 落地候选身份;
                # 已有明说 app -> 1b 只补悬空字段。分支结论直接写入提示。
                def _fv(_v, _k):
                    # v27.3.1 修复: resolution/rtt 是 {"min_value"/"max_value": {"value": ...}}
                    # 嵌套格式, 旧版只解 {"value": ...} -> 已落值误判悬空, 注入记录值
                    # 覆盖用户明说 (056/192 resolution 机制性致错, qwen38 双跑全错实证)
                    _x = (_v or {}).get(_k)
                    if isinstance(_x, dict):
                        if "value" in _x:
                            return _x.get("value")
                        for _sub in ("min_value", "max_value"):
                            _y = _x.get(_sub)
                            if isinstance(_y, dict) and "value" in _y:
                                return _y.get("value")
                        return None
                    return _x

                def _time_keep(_iid):
                    # 2026-09-28 S-000089: 落地后 start/end/duration 被选中记录的 completed
                    # 时段覆盖 (GT=用户 t1 明说时间)。把已落的明说时间显式写入锚点。
                    _sv = _idst.get(_iid) or {}
                    _ts = _sv.get("timestamp") or {}  # 时间三字段归 timestamp 子对象
                    _tp = []
                    for _tk, _tn in (("start_timestamp", "start"), ("end_timestamp", "end"),
                                     ("duration", "duration")):
                        _tv = _fv(_ts, _tk)
                        if _tv:
                            _tp.append(f"{_tn}={_tv}")
                    return ("#    该意图时间字段已是用户明说值 (" + ", ".join(_tp)
                            + ")——落地候选后【原样保留, 禁止用记录的时间覆盖, 也禁止从画像时段编造】。\n"
                            if _tp else "")

                _idst = getattr(self, "_id_states", None) or {}
                _stubs = [k for k, v in _idst.items() if not _fv(v, "application_name")]
                # 2026-09-28 S-000089: 选择轮补候选明细锚点——d6 新话术("您选虎牙还是钉钉这条?")
                # 不含"记录"字样 -> disambiguation_rule 不触发 -> 模型拿序数去数 records 列表
                # ("第二条"误作 records[1])。候选明细用列举轮存档的 selector matched (确定性),
                # 序数按此顺序对齐, 不依赖模型 t1 是否遵从列出 candidate 段。
                # S-000157 回退教训: 序数语义 = agent 话术顺序, 与 records 降序可能相反——
                # 优先用 candidate_map 的话术顺序解析 (disamb_cands), 解析不到才回退存档顺序。
                # v27.4 方案B: 话术解析失败时升级为 LLM 兜底 (规则正则只覆盖已知形态,
                # d930 变体持续穿透); LLM 只做抽取不做状态写, 落地仍走确定性锚点链。
                _listing = disamb_cands or getattr(self, "_cand_listing", None) or []
                if disamb_cands is None and agent_reply and self.features.candidate_map:
                    # v28.4: 候选列表来源重构——resolve_listing(话术属性匹配→LLM 兜底)
                    # 取代 parse_listing(仅日期粗匹配)。"候选1=咪咕快游+100ms+6月12号"
                    # 的属性组合唯一锚定 record (1959 型: '约束+取最近' 会混入同 app
                    # 更近的 80ms 那条), 渲染器候选对按属性挑选, 属性匹配可精确还原。
                    _recs = (user_context or {}).get("recent_records") or []
                    _rl = _resolve_listing(self.llm, agent_reply, _recs)
                    if _rl:
                        _listing = _rl
                    else:
                        _pl = _llm_parse_listing(self.llm, agent_reply, _recs)
                        if _pl:
                            _listing = _pl
                _anchor = ""
                if _listing:
                    _alines = []
                    for _i, _rr in enumerate(_listing, 1):
                        _rtt = (_rr.get("rtt") or {}).get("max_value") if isinstance(_rr.get("rtt"), dict) else _rr.get("rtt")
                        _res = (_rr.get("resolution") or {}).get("min_value") if isinstance(_rr.get("resolution"), dict) else _rr.get("resolution")
                        _alines.append("#   第%d条: application_name=%s, service_name=%s, resolution=%s, rtt=%s, completed_at=%s"
                                       % (_i, _rr.get("application_name"), _rr.get("service_name"), _res, _rtt, _rr.get("completed_at")))
                    _anchor = ("# 候选明细 (与客服上轮列出顺序一致, 用户说的\"第N条\"按此对齐, 禁止数 records 列表):\n"
                               + "\n".join(_alines) + "\n")
                # 2026-09-28 v27.3: 选中项 harness 确定性解析 (序数/“最近一条”),
                # 直接告知模型选中记录, 消除模型自行对齐 + 幻觉编造时段的空间。
                # v27.6: 选中序号优先用 judge 的结果 (LLM 语义判断), 规则解析仅兜底
                # (judge pick=None 时); "最近一条" 语义规则最后兜底。
                _pick_i = _judge.get("pick")
                if _pick_i is None:
                    _pick_i = _parse_pick_ord(utterance)
                if _pick_i is None and _ORD_LATEST_RE.search(utterance) and _listing:
                    _pick_i = max(range(len(_listing)),
                                  key=lambda _i: str(_listing[_i].get("completed_at") or "")) + 1
                _pick_rec = (_listing[_pick_i - 1]
                             if (_pick_i is not None and 1 <= _pick_i <= len(_listing)) else None)
                _pick_txt = ""
                if _pick_rec is not None:
                    _prtt = (_pick_rec.get("rtt") or {}).get("max_value") if isinstance(_pick_rec.get("rtt"), dict) else _pick_rec.get("rtt")
                    _pres = (_pick_rec.get("resolution") or {}).get("min_value") if isinstance(_pick_rec.get("resolution"), dict) else _pick_rec.get("resolution")
                    _pick_txt = ("# 系统解析: 用户本轮选中 = 第%d条 (application_name=%s, service_name=%s, resolution=%s, rtt=%s)。\n"
                                 % (_pick_i, _pick_rec.get("application_name"), _pick_rec.get("service_name"), _pres, _prtt))
                # 占位意图: t1 抢跑落地的候选 (app/svc 在候选列表且非选中项) —— S-000089 型
                # t1 违令落虎牙 -> 2b 严禁改写身份与用户选择死锁 -> 模型 cancel+new+编造时段。
                # v27.6.1: 选择落地后清除待选择状态——_cand_listing 残留会让 t3 确认轮
                # 再次触发 judge, 模型重选别的候选 (gena-S-000764/001789 型: t2 落地正确,
                # t3 确认轮被误判为选择轮 -> 改写成另一条候选)。
                self._cand_listing = None
                _placeholders: list[str] = []
                if _pick_rec is not None:
                    _pk = (_pick_rec.get("application_name"), _pick_rec.get("service_name"))
                    for _k, _v in _idst.items():
                        _kv = (_fv(_v, "application_name"), _fv(_v, "service_name"))
                        if _kv[0] and _kv in {(r.get("application_name"), r.get("service_name")) for r in _listing} \
                                and _kv != _pk:
                            _placeholders.append(_k)
                if _stubs:
                    # v28: scope 驱动落地范围 (LLM 从话语语义判断, 取代"桩空=全量"硬规则)。
                    # "时延就按候选X"(524 型) → 只补 rtt; "按X这条记录来配"(经典 T3-3) → 全量。
                    _scope = _judge.get("scope") or ["app", "svc", "res", "rtt"]
                    _sc_map = {"app": "application_name", "svc": "service_name",
                               "res": "resolution", "rtt": "rtt"}
                    _sc_flds = [_sc_map[s] for s in _scope if s in _sc_map]
                    if set(_scope) == {"app", "svc", "res", "rtt"}:
                        _branch = ("# 系统核验: 当前桩意图 " + "、".join(_stubs)
                                   + " 的 application_name 为空(用户从未明说 app)。\n"
                                   + "# 本轮选中候选后, 必须把选中记录的 application_name/service_name/resolution/rtt\n"
                                   + "# 【全量落地】到该桩意图(段头写其 id); 时间等用户明说字段保持不变。\n"
                                   + _time_keep(_stubs[0] if len(_stubs) == 1 else None))
                    else:
                        _fld_txt = "/".join(_sc_flds)
                        _assigns = "；".join(
                            (f"state.{_f} = 选中记录的{_f}" + (
                                " (rtt.max_value)" if _f == "rtt" else
                                " (resolution.min_value)" if _f == "resolution" else ""))
                            for _f in _sc_flds)
                        # v28.1: 压制 RECORD RULE 悬空条款——1840 型实证, scope 判定为引用
                        # 语义后模型仍被 RECORD RULE 的"悬空待消歧"压住, 选中记录的 scope
                        # 字段不落地。语义链: 用户已确认选中记录 → 指代已消解 → 悬空条款失效。
                        _branch = ("# 系统核验: 用户本轮仅要求按选中记录取部分字段 (语义判定 scope="
                                   + json.dumps(_scope, ensure_ascii=False) + ")。\n"
                                   + "# 本轮【只允许】把 " + _fld_txt + " 写入已有意图 (段头写其 id): " + _assigns + ";\n"
                                   + "# 【严禁】落地未列出的字段, 尤其严禁把选中记录的 application_name/service_name\n"
                                   + "#  当作新保障意图办理——选中记录只是字段来源, 不是新办业务。\n"
                                   + "# 【记录指代已消解】用户明确选定了候选, RECORD RULE 的\"悬空待消歧\"条款\n"
                                   + "#  自本轮起失效——scope 列出的字段【必须照常落地】, 不得再以\"悬空\"为由拒写;\n"
                                   + "#  也【严禁新建意图】承接选中记录——字段写入已有意图, 不另办业务。\n"
                                   + _time_keep(_stubs[0] if len(_stubs) == 1 else None))
                elif _placeholders:
                    _branch = ("# 系统核验: 意图 " + "、".join(_placeholders)
                               + " 的 app/svc 是候选列表中【未被选中】的候选 (t1 抢跑占位, 非用户明说)。\n"
                               + "# 按用户本轮选择, 把该意图身份【改写为选中候选】并全量落地 app/svc/resolution/rtt;\n"
                               + "# 时间等用户明说字段保持不变; 未被选中的候选不再保留, 也不另建意图。\n"
                               + _time_keep(_placeholders[0] if len(_placeholders) == 1 else None))
                else:
                    # 2026-09-28 S-000056 (B 型明说, 只悬 rtt): _pick_txt 给了选中记录全字段
                    # 明细后, 模型把记录身份 (飞书/会议) 与意图身份 (YY/开直播) 比对, 误判
                    # "身份不匹配整条不可用" 连悬空 rtt 也拒补。1b 分支直给悬空字段补齐指令。
                    _miss_txt = ""
                    if _pick_rec is not None:
                        _mlines = []
                        for _k, _v in _idst.items():
                            if not _fv(_v, "application_name"):
                                continue  # 桩由 1a 处理
                            for _fk in ("rtt", "resolution"):
                                if not _fv(_v, _fk):
                                    _pr = _pick_rec.get(_fk)
                                    _pv = ((_pr or {}).get("max_value") if _fk == "rtt" else (_pr or {}).get("min_value")) \
                                        if isinstance(_pr, dict) else _pr
                                    if _pv:
                                        _mlines.append(f"#    意图 {_k} 的 {_fk} 悬空 → 直接从选中记录补: state.{_fk} = \"{_pv}\";\n")
                        if _mlines:
                            _miss_txt = ("# 逐意图悬空字段补齐指令 (选中记录只是字段来源; 其 app/svc 与意图不一致是正常的,\n"
                                         "# 禁止因身份不一致而拒绝补字段):\n" + "".join(_mlines))
                    _branch = ("# 系统核验: 当前各意图的 application_name 均为用户此前明说。\n"
                               + "# 本轮选中候选后, 【严禁改写任何意图的身份】, 只把选中记录中此前悬空的字段\n"
                               + "# (如 rtt)写入对应意图; 用户明说的其他字段严禁被记录值覆盖。\n"
                               + _miss_txt)
                cand_mode_hint = (
                    "# === CANDIDATE MODE (选择轮, 本轮生效, 优先级最高) ===\n"
                    "# 用户在选定此前列出的候选记录。必须遵守:\n"
                    + _anchor
                    + _pick_txt
                    + _branch +
                    "# 2. 未被选中的候选: 不再保留, 也不为其输出任何段;\n"
                    "# 3. 记录的 completed_at 是记录完成时间, 不是保障时间, 禁止当作 start/end。\n")
            else:
                import record_selector as _rsmod
                _ev = _rev(utterance, user_context, agent_reply, disambiguation_active=True)
                if _ev is not None and _ev.mode == "defer" and len(_ev.matched) >= 2:
                    self._cand_listing = _ev.matched  # 存档供选择轮序数锚定 (跨轮, session 内)
                    _listed_this_turn = True
                    _lines = []
                    for _i, _rr in enumerate(_ev.matched, 1):
                        _rtt = (_rr.get("rtt") or {}).get("max_value") if isinstance(_rr.get("rtt"), dict) else _rr.get("rtt")
                        _res = (_rr.get("resolution") or {}).get("min_value") if isinstance(_rr.get("resolution"), dict) else _rr.get("resolution")
                        _lines.append("#   第%d条: application_name=%s, service_name=%s, resolution=%s, rtt=%s, completed_at=%s"
                                      % (_i, _rr.get("application_name"), _rr.get("service_name"), _res, _rtt, _rr.get("completed_at")))
                    # 2026-09-28 S-000140: "按最近一条记录,最好是抖音那条"——偏好表述诱导模型
                    # 把偏好当最终选择直接落地 (4 跑 2 次违令)。偏好词出现时显式声明偏好≠选择。
                    _prefer = ("# 5. 用户话语里的偏好 (\"最好是X那条\") 不是最终选择, 仍须全部列出等用户确认;\n"
                               "#    禁止只落地偏好那条。\n"
                               if _rsmod.PREFERENTIAL_RE.search(utterance) else "")
                    # v27.5 方案B (2026-10-13 用户定案): 候选列举从模型输出收回为 harness 直驱。
                    # d930 实证: 客服话术由数据集写死不由模型实时生成, 模型的 candidate 段
                    # 驱动不了下游, 反而引入顺序污染源 (模型按 records 降序列举 ≠ 话术序,
                    # gena-S-001130/1840 型)。模型只需看到候选列表 (理解有歧义在消歧),
                    # 不需复述; 落地走选择轮确定性锚点链。
                    cand_mode_hint = (
                        "# === CANDIDATE MODE (候选列举轮, 本轮生效, 优先级最高) ===\n"
                        "# 用户要求按记录办理但系统核验候选不唯一。候选列表如下 (按客服话术顺序):\n"
                        + "\n".join(_lines) + "\n"
                        "# 必须遵守:\n"
                        "# 1. 本轮【不要输出 # candidate: 段】; 记录身份字段 (application_name/service_name/\n"
                        "#    resolution/rtt) 一律悬空, 不落地到任何保障意图;\n"
                        "# 2. 用户本轮明说的字段 (如时间/画质) 正常落地到意图段 (意图段只含明说字段);\n"
                        "# 3. # summary 写明: 约束命中 N 条候选, 等待用户选择。\n"
                        + _prefer)

        # v26: intent-id 协议 (config intent_id_protocol.enabled, 默认关)
        # 身份与内容解耦: prev_states 以 id 为键(I1/I2...), 模型段头写 id/new, cancel 写 id。
        # id_states/idbook 由 session_eval.run_session 注入(self._id_*), 旧协议路径为 None。
        id_on = self.features.intent_id_protocol
        idbook = getattr(self, "_id_book", None) if id_on else None
        id_states = getattr(self, "_id_states", None) if id_on else None
        # v26.3 P3: 多实例 key (联动规则在 Features 唯一实现)
        self._mi_on = self.features.multi_instance_key

        # v26.3 P1: 应用名待补规则 (config pending_app_rule.enabled)
        # 触发: 本轮话语未明说任何 app。注入"app 未明说不得猜/已有意图补齐"规则。
        # v3 文案: 修补 S-000220 型副作用——补参数轮(画质/时延/时间, 话语无 app)模型
        # 误判"新建"而非更新已有意图, 导致意图分裂。规则改为"默认更新": 话语无 app 时
        # 若存在已有意图且非明确的新请求信号, 应写该意图 id(唯一时), 参数按槽位空缺对齐;
        # 只有确定用户另提新保障才 new。S-000184 型(更新段丢 app)已由 coreference 条款覆盖。
        pending_app_hint: str | None = None
        if self.features.pending_app_rule and \
                not _utterance_mentions_app(utterance):
            stubs = _pending_stubs(id_states)
            stub_txt = "、".join(f"{iid}({desc})" for iid, desc in stubs) if stubs else "无"
            id_list = "、".join(sorted((id_states or {}).keys())) if (id_states or {}) else "无"
            pending_app_hint = (
                "# === PENDING-APP RULE (应用名未明说, 本轮生效, 优先级最高) ===\n"
                "# 用户本轮话语没有提到任何具体应用名。必须遵守:\n"
                "# 1. 禁止猜测 application_name: 不得从画像 records、受理矩阵或 schema 列表里挑一个填上;\n"
                "#    已知的服务/时间/画质/时延等字段照常提取赋值。\n"
                f"# 2. 当前已有意图: {id_list}; 其中待补应用名的桩: {stub_txt}。\n"
                "#    【默认更新, 谨慎新建】本轮无 app 时, 若话语内容(补充的参数/时段/画质/时延,\n"
                "#    或确认/改口)对应某个已有意图的未收齐槽位或延续其语境, 必须段头写该意图的 id\n"
                "#    (已有意图唯一时就是它; 多个时按参数对齐), 只赋本轮新给的字段;\n"
                "#    application_name 必须用 coreference 沿用(state.application_name = state.application_name),\n"
                "#    绝对不能让已有意图的 application_name/service_name 因本轮不赋值而丢失或被搁置。\n"
                "#    仅当用户明确另提一个全新保障(新服务词+新时段且与所有已有意图都对不上)才写 # intent: new;\n"
                "#    拿不准是更新还是新建时, 一律按更新处理。\n"
                "# 3. # summary 写明: 应用名用户未明说(更新已有意图/新建留空)。\n\n"
            )

        # v27: 时间锚点/明说时间证据/时间口径规则 (config time_anchor/time_extractor/time_rule_*)
        time_hint, _time_ev = _build_time_hint(self.features, utterance, reference_time, user_context)

        system, task_prompt = build_prompt(
            utterance, reference_time, merged_prev, retrieved, user_context,
            agent_reply=agent_reply if ac_on else None,
            prev_statuses=prev_statuses if sp_on else None,
            disamb_keys=disamb_keys,
            disamb_cands=disamb_cands,
            corr_keys=corr_keys,
            record_rule_hint=record_rule_hint,
            record_ev_hint=record_ev_hint,
            cand_mode_hint=cand_mode_hint,
            quality_hint=quality_hint,
            pending_app_hint=pending_app_hint,
            time_hint=time_hint,
            time_anchor_on=self.features.time_anchor,
            cand_landing=bool(cand_mode_hint) and _cb.select_phrase(utterance),
            idbook=idbook,
            id_states=id_states,
            memory_hint=memory_hint,
        )
        raw = self.llm.gen_raw_code(system, task_prompt)
        # PAR 校验回路: 缺 summary / 取消场景缺 # cancel -> 重采样1次
        # v26 id 协议追加: 段头出现非 id/non-new 引用(不遵从协议)也重采样 1 次
        def _id_headers_ok(code: str) -> bool:
            if not id_on or not idbook:
                return True
            from intent_ids import ID_RE
            for hk in re.findall(r"#\s*intent:\s*(.+)", code):
                h = hk.strip()
                if h == "none" or ID_RE.match(h) or h.lower() in ("new", ""):
                    continue
                return False
            for ck in re.findall(r"#\s*cancel:\s*(.+)", code):
                c = ck.strip()
                if not ID_RE.match(c):
                    return False
            return True

        def _suspicious_new(code: str) -> bool:
            """v26.2 full-match new 门: new 段的 app+svc 与某个 prev 意图完全同名,
            且话语无新增词 -> 可疑(多半该写已有 id), 触发重采样。
            full-match 信号安全: d5 全库同 app+svc 双意图仅 2 例。"""
            if not id_on or not idbook or not prev_states:
                return False
            add_words = ("再", "加", "还要", "另外", "办个", "再来", "多个", "也")
            if any(w in utterance for w in add_words):
                return False
            from case_format import flatten_params as _fpn
            import python_state as _ps
            for key, seg in _ps._split_intent_segments(code):
                if key.strip().lower() != "new":
                    continue
                try:
                    delta = _ps.execute_program(seg, {}, timeout=1.5)
                except Exception:
                    continue
                f = _fpn(delta)
                app, svc = f.get("application_name"), f.get("service_name")
                if not app or not svc:
                    continue
                for pk in prev_states:
                    pf = _fpn(prev_states[pk])
                    if pf.get("application_name") == app and pf.get("service_name") == svc:
                        # v26.3 P3: mi_on 下同 app+svc 的 new 段是合法第二实例, 不触发;
                        # 仅当该段与 prev 意图 7 字段全同(纯复读)才可疑
                        if self._mi_on:
                            # mi_on: 该 new 段是合法第二实例, 除非与 prev 参数完全相同(纯复读)。
                            # 只有存在非空差异值才放行; 全空/全等 -> 保守判可疑(该写 id 却写 new)
                            fields = ("resolution", "rtt", "start_timestamp",
                                      "end_timestamp", "duration")
                            differing = [s for s in fields
                                         if (f.get(s) or None) and
                                         (f.get(s) or None) != (pf.get(s) or None)]
                            if differing:
                                break
                        return True
            return False

        if (_need_par(utterance, merged_prev) and (not _summaries_ok(raw) or not _cancel_check_ok(utterance, raw))) \
                or not _id_headers_ok(raw) or not _identity_ok(raw, id_on) or _suspicious_new(raw):
            raw2 = self.llm.gen_raw_code(system, task_prompt)
            if _summaries_ok(raw2) and _cancel_check_ok(utterance, raw2) and _id_headers_ok(raw2) \
                    and _identity_ok(raw2, id_on) and not _suspicious_new(raw2):
                raw = raw2
        # v27.3-d930: id 协议下模型段头写 I1, 但 prev_states 是内容键 —— _match_prev
        # 对 'I1' 永远失配 -> prev={} -> 段内 state.xxx 自引用 (find_timestamp(end=
        # state.end_timestamp), d930 q38 ~2% 轮) 解析失败 -> line_tolerant 跳行 -> delta
        # 整段丢失 (S-000313 型 T2-2 时间更新轮静默清零). 沙箱 prev 挂 id 别名视图
        # (仅执行用, 段处理层仍按 id 协议), 让 state 引用拿到正确 prev。
        exec_prevs = prev_states
        if id_on and idbook is not None and prev_states:
            exec_prevs = dict(prev_states)
            for _iid, _ck in list(idbook._by_id.items()):
                if _ck in prev_states and _iid not in exec_prevs:
                    exec_prevs[_iid] = prev_states[_ck]
        segs = execute_program_multi(raw, exec_prevs)

        # v27.7 路线乙 (2026-10-13 用户定案): 列举轮抢跑在 harness 层不可表达。
        # 模型违令在列举轮落地候选身份 (app/svc/res/rtt) 时, 执行后剥离——
        # 剥离条件: 该段 app 恰为本轮候选列表之一 **且不是用户本轮明说的新保障目标**
        # (v27.7.1 修正: genb-S-001707 型——宽指代候选列表会包含话语明说的目标 app
        # (芒果TV∈"最近办过的记录"并集), 无差别剥离会误杀真明说意图。明说判定 =
        # app 出现在话语中且不是记录筛选词 (与 pending_app_rule 同口径)。
        if _listed_this_turn and getattr(self, "_cand_listing", None):
            import record_selector as _rs7
            from schema import KNOWN_APPLICATIONS as _KAP7
            _f7 = _rs7._extract_filters(
                utterance, tuple(_KAP7),
                ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频")).get("application_name")
            _cand_apps = {r.get("application_name") for r in self._cand_listing}
            _stripped: list[tuple[str, dict]] = []
            for _k, _d in segs:
                if _k == "__cancel__":
                    _stripped.append((_k, _d))
                    continue
                _da = _d.get("application_name")
                _da_v = _da.get("value") if isinstance(_da, dict) else _da
                if _da_v in _cand_apps and not (
                        _da_v and _da_v in utterance and _da_v != _f7):
                    _d = {kk: vv for kk, vv in _d.items()
                          if kk not in ("application_name", "service_name",
                                        "resolution", "rtt")}
                _stripped.append((_k, _d))
            segs = _stripped

        self.last_raw = raw  # 供 session_eval 记录 LLM 原始输出
        # v27.7.2: 保存本轮 prompt (供 error_report 展示候选列表/注入内容)
        self.last_task_prompt = getattr(self.llm, "last_task_prompt", "")
        self.last_system_prompt = getattr(self.llm, "last_system_prompt", "")

        # record coreference 兜底: LLM 全空且 utterance 含"按上次/平时/记录"且 prev 空
        # v27.3: 候选列举轮抑制——候选模式语义是"悬空等选择", record 兜底会把 records[0]
        # 直接抢跑落地 (S-000140 run2 实证: t1 模型只出 candidate 段, 兜底把爱奇艺全字段
        # 落了地, 恰好是 GT 目标才没炸; 若用户最终选另一条即成占位错案)。列举轮改走
        # 下方时间桩兜底。
        all_empty = all(not d for _, d in segs) or not segs
        if all_empty and user_context and not merged_prev and not _listed_this_turn:
            rec_words = ("上次", "平时", "记录", "历史")
            if any(w in utterance for w in rec_words):
                recs = user_context.get("recent_records") or []
                if recs:
                    delta = _delta_from_record(recs[0])
                    segs = [("", delta)]

        # v27.3 候选列举轮时间桩兜底 (S-000140 run2 型):
        # 模型只输出 # candidate 段(旁路剥离), 漏掉承载明说时间的意图段 -> 零意图段,
        # 明说时间无家可归 (判分 start/end/duration=None)。用 TIME EVIDENCE 预解析值
        # 补建时间桩——与模型所见同一值, 非猜测 (性质同 prev_state 兜底/知二推一:
        # harness 代执行模型已认可的机械落地, 时间提取规则化的闭环)。
        # 四重收窄: 列举轮 + ev 明说时间完整 + 零意图段 + 话语无新保障目标 app。
        # 注意"明说目标"≠筛选提及: "最好是抖音那条"的抖音是记录筛选约束 (紧邻指代词,
        # _extract_filters 口径), 不算新目标 (A 型); "用YY开直播"的 YY 才是 (B 型,
        # 空桩会被选择轮 1a 全量落地劫持身份, 排除)。
        if _listed_this_turn and all_empty and _time_ev is not None \
                and _time_ev.start is not None and _time_ev.end is not None \
                and not _time_ev.has_contradiction and not _time_ev.has_candidate:
            import record_selector as _rs2
            from schema import KNOWN_APPLICATIONS as _KAPPS
            _f_app = _rs2._extract_filters(
                utterance, tuple(_KAPPS),
                ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频")).get("application_name")
            _new_app = any(name in utterance and name != _f_app for name in _KAPPS)
            if not _new_app:
                _ts: dict[str, Any] = {
                    "start_timestamp": {"value": _tex._fmt(_time_ev.start), "source_type": "Turn", "source_ref": "derived"},
                    "end_timestamp": {"value": _tex._fmt(_time_ev.end), "source_type": "Turn", "source_ref": "derived"},
                }
                if _time_ev.duration_min is not None:
                    _ts["duration"] = {"value": f"{_time_ev.duration_min}min",
                                       "source_type": "Turn", "source_ref": "derived"}
                segs = [("new", {"timestamp": _ts})]

        results: dict[str, dict[str, Any]] = {}
        # v26 id 协议: 段头 id -> prev 映射; new 段分配新 id
        id_on = self.features.intent_id_protocol
        idbook = getattr(self, "_id_book", None) if id_on else None
        for key, delta in segs:
            if key == "__cancel__":
                self._handle_cancel_segment(delta, idbook, prev_states, results,
                                            utterance=utterance)
                continue
            if idbook is not None:  # v26.3: 修 __len__ truthiness 陷阱(空账本 falsy 致首轮误走旧协议)
                self._process_id_segment(key, delta, idbook, prev_states,
                                         reference_time, results)
            else:
                self._process_key_segment(key, delta, prev_states, reference_time, results)

        return results

    # ------------------------------------------------------------------
    # 段处理 (run_multi 的核心分派, v26.2c 重构提取)
    # ------------------------------------------------------------------

    def _handle_cancel_segment(self, delta, idbook, prev_states, results,
                               utterance: str = "") -> None:
        """# cancel 段: 归一化被取消意图的 key 并写入取消信号。
        v26.1: cancel 优先写名字(app/svc, 名字自校验); 写 id 时查表回退名字归一化。
        v26.3 P3 (mi_on): 同 base 多实例时, 名字/序数 cancel 走时段/序数消歧链。"""
        from intent_ids import parse_id_header
        ck = delta if isinstance(delta, str) else ""
        if idbook:
            iid, _ = parse_id_header(ck)
            if iid and idbook.key_of(iid):
                ck_full = idbook.key_of(iid)        # id 直达 (实例唯一)
            else:
                ck_full = _resolve_cancel_key(ck, prev_states)
                # P3: 名字归一化结果命中多实例之一时, 用话语时段/序数消歧
                if self._mi_on and utterance:
                    cands = idbook.keys_by_base(_base_key(ck_full) or ck_full)
                    if len(cands) > 1:
                        hit = _disambiguate_cancel(utterance, cands, prev_states)
                        if hit:
                            ck_full = hit
        else:
            ck_full = _resolve_cancel_key(ck, prev_states)
        results[f"__cancel__:{ck_full}"] = {"__cancel__": True, "cancel_key": ck_full}

    def _process_id_segment(self, key, delta, idbook, prev_states,
                            reference_time, results) -> None:
        """v26 id 协议段处理: 段头是 I1/I2...(更新)或 new(新建)。

        - new 段归并校验: 产出意图与"无 app 的已有 id"互补(补全型共指)时,
          harness 改判为该 id 的更新(prev 填空槽), 残骸 key 经 _consumed_key 退役;
        - 段去重: 同轮产出同 key 时先到先得(防冗余 new 段覆盖完整 I1 段);
        - 更新段 key 从合并后 final_state 重推导, 改名走 idbook.rename(id 不变)。
        """
        from case_format import flatten_params
        from intent_ids import parse_id_header

        iid, is_new = parse_id_header(key)
        if is_new and (not key or key == "none" or key in prev_states) and len(prev_states) == 1:
            sole_ck = next(iter(prev_states))
            sole_iid = idbook.iid_of(sole_ck)
            if sole_iid:
                iid, is_new = sole_iid, False
        if is_new or (iid and idbook.key_of(iid) is None):
            # --- 新建(引用不存在的 id 也按新建兜底) ---
            final = validate({"intent": "unknown", "params": delta}, reference_time, {})
            fflat = flatten_params(final.get("final_state") or {})
            k = intent_key(fflat.get("application_name"), fflat.get("service_name"))
            if not k:
                results[key or "__nokey__"] = final   # 域外/非保障段, 不占 id
                return
            merge_cands = [pk for pk, pv in prev_states.items()
                           if not flatten_params(pv).get("application_name")
                           and flatten_params(pv).get("service_name") == fflat.get("service_name")]
            if not merge_cands:
                # 弱信号: prev 全体中无 app 的悬置意图唯一(S-000263 纯参数悬置轮)
                noapp = [pk for pk, pv in prev_states.items()
                         if not flatten_params(pv).get("application_name")]
                if len(noapp) == 1 and len(prev_states) == 1:
                    merge_cands = noapp
            if len(merge_cands) == 1:
                # 归并: 用该 id 的 prev 重新 validate(delta 优先, prev 填空槽)
                # v26.3: 归并目标若在 idbook 有既有 id(待补桩), id 沿用不断裂;
                # 引用不存在 id 的兜底新建路径 mk 不在 idbook, 才 alloc。
                mk = merge_cands[0]
                final = validate({"intent": "unknown", "params": delta},
                                 reference_time, prev_states[mk])
                fflat = flatten_params(final.get("final_state") or {})
                k = intent_key(fflat.get("application_name"), fflat.get("service_name")) or mk
                if k != mk:
                    idbook.rename(mk, k)          # id 不变, 内容 key 更新(与更新段同路径)
                else:
                    idbook.ensure(k, idbook.iid_of(k) or idbook.alloc())
                final["_iid"] = idbook.iid_of(k)
                final["_consumed_key"] = mk       # 残骸退役
                # 归并路径与更新段同策略: 沿用 rename 后 idbook 登记的 key(目标被占时
                # rename 内部已实例化), 避免把"自己的目标 key"当占用再编 #N
                k_final = idbook.key_of(idbook.iid_of(k)) or k
                results[k_final] = final
                return
            new_iid = idbook.alloc()
            k2 = _instanced_key(k, results, self._mi_on,
                                prior_keys=list(prev_states) + list(idbook._by_key))
            idbook.ensure(k2, new_iid)
            final["_iid"] = new_iid
            if k2 != k and k2 in results:
                return    # 段去重(窄化): 仅同实例 key 重复时丢弃; mi_on 下同 base 冲突已实例化
            if k2 == k and k in results:
                return    # 段去重: mi_off 原语义——同轮已有 I1 段产出同 key, 丢弃冗余 new 段
            results[k2] = final
        else:
            # --- 更新已有意图 ---
            cur_key = idbook.key_of(iid)
            final = validate({"intent": "unknown", "params": delta},
                             reference_time, prev_states.get(cur_key, {}))
            fflat = flatten_params(final.get("final_state") or {})
            k = intent_key(fflat.get("application_name"), fflat.get("service_name")) or cur_key
            if not k:
                return    # 非法头(如 "I1：")且无字段: 丢弃, 防 results[None]
            # v26.3 P3: k 从合并 final 推导会丢实例后缀('#2'->base)。base 相同 = 未改名,
            # 必须保持 cur_key 实例形态, 否则伪改名触发 rename 链 -> _consumed_key=自己
            # -> 透传层把自己 pop 掉(S-000225 turn2 空 delta 沿用段实害)。
            if _base_key(k) == _base_key(cur_key):
                k = cur_key
            if k != cur_key:
                idbook.rename(cur_key, k)         # id 不变, 内容 key 更新
                final["_consumed_key"] = cur_key
            final["_iid"] = iid
            # 更新段: 直接沿用 rename 后 idbook 登记的 key(改名目标被占时 rename 内部
            # 已自动实例化成 #N); 未改名则 cur_key 即原实例 key。更新段永不重新编号。
            k_final = idbook.key_of(iid) or cur_key
            results[k_final] = final

    def _process_key_segment(self, key, delta, prev_states,
                             reference_time, results) -> None:
        """旧协议(v25.2)段处理: 段头是内容 key(app/svc), harness 内容匹配归并。"""
        from case_format import flatten_params
        # v12.1 无头段兜底: prev 只有一个意图时归并, 避免 coreference 对空 prev 段崩
        if (not key or key == "none") and len(prev_states) == 1:
            key = next(iter(prev_states))
        mkey = _match_prev_key(key, prev_states)
        prev = prev_states[mkey] if mkey else {}
        final = validate({"intent": "unknown", "params": delta}, reference_time, prev)
        # key 从合并后 final_state 推导(v25.2): 改口单字段不再裂 key
        fflat = flatten_params(final.get("final_state") or {})
        k = intent_key(fflat.get("application_name"), fflat.get("service_name")) or key
        if mkey and mkey != k:
            final["_consumed_key"] = mkey         # 归并/改名: 旧 key 退役
        results[k] = final




def _resolve_cancel_key(ck: str, prev_states: dict[str, dict[str, Any]]) -> str:
    """把 cancel 信号归一化到 prev_states 里真实存在的 intent_key。
    LLM 可能只写 app (如"FaceTime") 或 app/service (如"FaceTime/视频通话")。
    """
    if not ck:
        return ""
    if ck in prev_states:
        return ck
    app = ck.split("/")[0]
    for k in prev_states:
        if k == app or k.startswith(app + "/"):
            return k
    return ck  # 找不到也返回原值（可能 prev_states 里真没有）


# ---- v26.3 P3: 同 base 多实例 cancel 消歧 (时段匹配 > 序数) ----
_DAYPART_HOUR_C = {"凌晨": 0, "早上": 0, "上午": 0, "中午": 12, "下午": 12, "晚上": 12}


def _parse_time_hint(utterance: str) -> set[tuple[int, int]]:
    """从 cancel 话语提取提及的时刻 (时,分) 集合, 供与实例 start/end 匹配。
    "7点到8点半" -> {(7,0),(8,30)}; "7点一刻到8点45分" -> {(7,15),(8,45)}。"""
    out: set[tuple[int, int]] = set()
    for m in re.finditer(
            r"(凌晨|早上|上午|中午|下午|晚上)?(\d{1,2})[点:：时](半|一刻|(?:\d{1,2})分?)?",
            utterance or ""):
        h = int(m.group(2))
        if m.group(1):
            h = (_DAYPART_HOUR_C[m.group(1)] + h) % 24
        mm = 0
        tail = m.group(3)
        if tail == "半":
            mm = 30
        elif tail == "一刻":
            mm = 15
        elif tail:
            mm = int(re.sub(r"\D", "", tail) or 0)
        out.add((h, mm))
    return out


def _parse_ordinal(utterance: str) -> int | None:
    """话语中的序数 ("第一个/第1个/第一份") -> 1-based 序号; 无则 None。"""
    m = re.search(r"第([一二三四五六七八九\d]+)[个份条]", utterance or "")
    if not m:
        return None
    g = m.group(1)
    if g.isdigit():
        return int(g)
    return "一二三四五六七八九".index(g) + 1


def _disambiguate_cancel(utterance: str, inst_keys: list[str],
                         prev_states: dict[str, dict[str, Any]]) -> str | None:
    """同 base 多实例 cancel 消歧: 时段文本匹配 -> 序数 (按 start 排序)。
    唯一命中返回实例 key; 无法消歧返回 None (调用方保持原归一化)。"""
    from case_format import flatten_params
    from validator import parse_dt
    hints = _parse_time_hint(utterance)
    if hints:
        hits = []
        for k in inst_keys:
            f = flatten_params(prev_states.get(k) or {})
            times = set()
            for s in ("start_timestamp", "end_timestamp"):
                dt = parse_dt(f.get(s) or "")
                if dt:
                    times.add((dt.hour, dt.minute))
            if times and (times & hints) == times:  # 实例起止都被话语覆盖才算命中
                hits.append(k)
        if len(hits) == 1:
            return hits[0]
    ordinal = _parse_ordinal(utterance)
    if ordinal and len(inst_keys) >= ordinal:
        ranked = sorted(inst_keys, key=lambda k: (
            flatten_params(prev_states.get(k) or {}).get("start_timestamp") or ""))
        return ranked[ordinal - 1]
    return None


def _delta_from_record(rec: dict) -> dict[str, Any]:
    """把画像 record (扁平) 转成嵌套 delta (Record 来源字段)。"""
    src = {"source_type": "Record", "source_ref": "derived"}
    delta: dict[str, Any] = {}
    if rec.get("application_name"):
        delta["application_name"] = {"value": rec["application_name"], **src}
    if rec.get("service_name"):
        delta["service_name"] = {"value": rec["service_name"], **src}
    if rec.get("resolution"):
        delta["resolution"] = {"min_value": {"value": rec["resolution"], **src}}
    if rec.get("rtt"):
        delta["rtt"] = {"max_value": {"value": rec["rtt"], **src}}
    return delta


