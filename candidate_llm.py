# -*- coding: utf-8 -*-
"""candidate_llm.py: v27.4 候选模块 LLM 兜底 (方案 B, 用户 2026-10-12 定案)。

设计原则 (与封死路线的边界):
  - 规则优先 (高频快路径零成本), LLM 只兜长尾 (正则解析失败时升级)
  - LLM 只做【信息抽取】(选中序号 / 话术提及顺序), 不做状态写操作;
    落地仍走确定性锚点链 (字段值从 records 查表, 不经 LLM)
  - 失败安全回退 (LLM 输出非法/超时 -> 返回 None, 退回既有行为)
  - 输入只有当前话语 + 候选明细 (由 records 构造), 测试集永不进训练/检索
接口:
  pick_index_llm(llm, utterance, listing) -> int | None  (1-based)
  parse_listing_llm(llm, agent_reply, records) -> list[dict] | None
"""
from __future__ import annotations

import json
import re

_NUM = "一二三四五六七八九"


def _fmt_cand(i: int, r: dict) -> str:
    rtt = (r.get("rtt") or {}).get("max_value") if isinstance(r.get("rtt"), dict) else r.get("rtt")
    res = (r.get("resolution") or {}).get("min_value") if isinstance(r.get("resolution"), dict) else r.get("resolution")
    return (f"候选{i}: {r.get('application_name')}/{r.get('service_name')}, "
            f"{res}, {rtt}, 完成于{r.get('completed_at')}")


def pick_index_llm(llm, utterance: str, listing: list[dict]) -> int | None:
    """LLM 兜底选中序号。返回 1-based 序号, 失败/非法 -> None。"""
    if not listing:
        return None
    lines = "\n".join(_fmt_cand(i + 1, r) for i, r in enumerate(listing))
    system = ("你是一个选择解析器。用户话语是对候选记录的选择回复, 客服此前按序列出了候选。"
              "从用户话语提取选中的候选序号 (1-based)。否定该序号不算选中。"
              "若无法确定, 输出 0。只输出一个整数。")
    task = f"候选列表:\n{lines}\n\n用户: {utterance}\n\n选中序号:"
    try:
        raw = llm.gen_raw_code(system, task)
        m = re.search(r"\d+", raw or "")
        if not m:
            return None
        n = int(m.group(0))
        return n if 1 <= n <= len(listing) else None
    except Exception:
        return None


def judge_selection_turn(llm, utterance: str, listing: list[dict]) -> dict:
    """v28 选择轮判定+选中序号+落地范围一体化 (schema 约束枚举)。

    业界模式 (arXiv:2605.25204 分阶段 + 受限枚举): action/pick/scope 全部枚举,
    输出空间锁死, 替代 v27.6 的自由 prompt judge。
    返回 {"is_selection": bool, "pick": int|None, "scope": list[str]|None};
    任何失败 -> {"is_selection": False, "pick": None, "scope": None} (安全回退)。
    scope 判断依据 = 用户话语动词与限定词 ("时延就按候选2"=只要rtt;
    "按X这条记录来配"=全量; "其他都按这次说的"=明说字段已含, 记录只补悬空)。
    """
    out = {"is_selection": False, "pick": None, "scope": None}
    if not listing:
        return out
    lines = "\n".join(_fmt_cand(i + 1, r) for i, r in enumerate(listing))
    system = (
        "你是客服对话状态分析器。判断用户回复属于哪一类, 只输出一个 JSON 对象:\n"
        "- action: \"select\"(从候选中选了一条, 序数/指代/否定其余后选定/'按最近那条'都算; "
        "字段限定式选择如'时延就按候选2'也算) | \"confirm\"(纯确认已有方案, 如'对就这样') "
        "| \"new_request\"(另提了无关新保障) | \"chitchat\"(闲聊)\n"
        "- pick: 选中候选的序号(仅 action=select 时给 1-based 整数, 否则 null)。"
        "'最近一条/最新那条'=completed_at 完成时间最晚的那条。\n"
        "- scope: 用户要从选中记录取哪些字段(仅 action=select 时给, 否则 null)。"
        "枚举值: \"app\"/\"svc\"/\"res\"/\"rtt\" 的非空数组。判断依据是用户话语的动词与限定词:\n"
        "  · '时延就按候选X'/'按X记录里的时延来' → [\"rtt\"]\n"
        "  · '按候选X这条记录来配/办理' → [\"app\",\"svc\",\"res\",\"rtt\"] 全量\n"
        "  · '其他都按这次说的' → 记录只补话语未明说的字段\n"
        "  · '应用业务清晰度时延都按X' → 全量")
    task = (f"候选列表:\n{lines}\n\n用户: {utterance}\n\n"
            f"JSON (action/pick/scope 三个字段都要给):")
    try:
        raw = llm.gen_raw_code(system, task)
        m = re.search(r"\{[\s\S]*?\}", raw or "")
        if not m:
            return out
        j = json.loads(m.group(0))
        action = str(j.get("action") or "")
        pick = j.get("pick")
        if isinstance(pick, str) and pick.isdigit():
            pick = int(pick)
        scope = j.get("scope")
        # 校验 (非法 -> None 安全回退)
        if action not in ("select", "confirm", "new_request", "chitchat"):
            return out
        if action == "select":
            if not (isinstance(pick, int) and 1 <= pick <= len(listing)):
                return out
            if not (isinstance(scope, list) and scope
                    and all(s in ("app", "svc", "res", "rtt") for s in scope)):
                return out
        else:
            pick, scope = None, None
        return {"is_selection": action == "select", "pick": pick, "scope": scope}
    except Exception:
        return out


def parse_listing_llm(llm, agent_reply: str, records: list[dict]) -> list[dict] | None:
    """LLM 兜底解析客服话术中的候选顺序。返回有序 record 列表, 失败 -> None。

    输入: 话术 + 画像 records (全量)。LLM 输出每个候选的 app/svc 与完成日期,
    本模块据此按 completed_at 升序/降序与话术提及序匹配回 records。"""
    if not agent_reply or not records:
        return None
    # 提供给 LLM 的最小 records 视图 (防超长; app/svc/completed_at/rtt 足够定位)
    slim = [{"record_id": r.get("record_id"), "application_name": r.get("application_name"),
             "service_name": r.get("service_name"), "completed_at": r.get("completed_at"),
             "rtt": (r.get("rtt") or {}).get("max_value") if isinstance(r.get("rtt"), dict) else r.get("rtt")}
            for r in records]
    system = ("你是客服话术解析器。从客服回复中提取其列出的候选记录, 按话术提及顺序输出。"
              "每条给出 application_name/service_name/completed_at(从话术的日期描述推出,"
              "格式 YYYY-MM-DD 或 YYYY年MM月DD日) 供匹配。无法提取候选时输出空数组 []。"
              "只输出 JSON 数组。")
    task = f"records:\n{json.dumps(slim, ensure_ascii=False)}\n\n客服话术:\n{agent_reply}\n\n候选 (按提及顺序, JSON):"
    try:
        raw = llm.gen_raw_code(system, task)
        m = re.search(r"\[[\s\S]*?\]", raw or "")
        if not m:
            return None
        arr = json.loads(m.group(0))
        if not arr or not isinstance(arr, list):
            return None
        out: list[dict] = []
        used: set[str] = set()
        for item in arr:
            app, svc, ca = item.get("application_name"), item.get("service_name"), item.get("completed_at") or ""
            ca_norm = re.sub(r"\D", "", str(ca))  # "2026年07月05日"/"2026-07-05" -> "20260705"
            best = None
            for r in records:
                if r.get("record_id") in used:
                    continue
                ra = re.sub(r"\D", "", str(r.get("completed_at") or ""))[:8]
                if app and r.get("application_name") != app:
                    continue
                if svc and r.get("service_name") != svc:
                    continue
                if ca_norm and not ra.startswith(ca_norm[:8]):
                    continue
                best = r
                break
            if best is None:
                return None
            used.add(best["record_id"])
            out.append(best)
        return out if len(out) >= 2 else None
    except Exception:
        return None


# ------------------------------------------------------------------
# v28.4: resolve_listing — 话术属性匹配还原渲染器候选对 (用户定案实施)
# ------------------------------------------------------------------

_ATTR_W = ("application_name", "service_name", "rtt", "resolution", "completed_at")


def _slim_record(r: dict) -> dict:
    return {"record_id": r.get("record_id"),
            "application_name": r.get("application_name"),
            "service_name": r.get("service_name"),
            "resolution": (r.get("resolution") or {}).get("min_value")
                if isinstance(r.get("resolution"), dict) else r.get("resolution"),
            "rtt": (r.get("rtt") or {}).get("max_value")
                if isinstance(r.get("rtt"), dict) else r.get("rtt"),
            "completed_at": r.get("completed_at")}


def resolve_listing_rule(agent_reply: str, records: list[dict]) -> list[dict] | None:
    """规则快路径: 从话术逐候选拆属性 (app/svc/rtt/res/完成日期), 与 records 精确匹配。
    全部候选唯一命中才返回; 任一候选匹配 0 或 ≥2 条 → None (升级 LLM)。
    1959 型根治点: '候选1=咪咕快游+100ms+6月12号' 的属性组合唯一锚定 R-001,
    不会像'约束+取最近'那样被同 app 更近的 R-002(80ms) 挤掉。"""
    if not agent_reply or not records:
        return None
    # 复用 _parse_disamb_candidates 的分段的思路过重; 直接让 LLM 前先试规则:
    # 按候选分隔符粗切 (；/；/; 和 '候选N'/'第N条' 引导), 逐段抽属性。
    text = re.split(r"请问|您选|您看|要选", agent_reply)[0]
    segs = [s for s in re.split(r"[；;]|，(?=候选|第)|。(?=候选|第)", text) if s.strip()]
    # 只保留候选引导段 (候选N/第N条开头), 前导剥离 ("我查到两条记录：" 等);
    # 无引导的评述段 ("您这次指定6月23号完成…") 跳过不算候选 (1959 型第三段)
    cand_segs = []
    for s in segs:
        m = re.search(r"(候选|第)\s*[一二三1234]\s*[条个]?[是为]?", s)
        if m:
            seg = s[m.start():]
            # v28.4: 剥段尾回指附加语 ("候选2是腾讯视频…，您说的看直播和7月2日分别对应…"
            # 的"您说的…"是话语回指, '看直播' 会被 svc 扫描误命中成段2属性, 540 型)。
            seg = re.split(r"，您|。您|您说的", seg)[0].strip("，。；、 ")
            cand_segs.append(seg)
    if len(cand_segs) < 2:
        return None
    segs = cand_segs
    slim = [_slim_record(r) for r in records]
    out: list[dict] = []
    used: set[str] = set()
    for seg in segs:
        # 抽属性: app 名/svc 词/rtt 值/res 值/日期
        from schema import KNOWN_APPLICATIONS
        attrs: dict = {}
        for a in sorted(KNOWN_APPLICATIONS, key=len, reverse=True):
            if a in seg:
                attrs["application_name"] = a
                break
        for sv in ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频"):
            if re.search(rf"{re.escape(sv)}", seg):
                # 剥已命中 app 名防子串误命中 (v28.2 同源教训)
                u2 = seg.replace(attrs.get("application_name", ""), "")
                if sv in u2:
                    attrs["service_name"] = sv
                    break
        m = re.search(r"(\d+)\s*(?:ms|毫秒)", seg)
        if m:
            attrs["rtt"] = f"{m.group(1)}ms"
        m = re.search(r"(\d{3,4})\s*[pP]", seg)
        if m:
            attrs["resolution"] = f"{m.group(1)}p"
        m = re.search(r"(\d{1,2})月(\d{1,2})[号日]", seg)
        if m:
            attrs["date"] = f"{int(m.group(1)):02d}月{int(m.group(2)):02d}"
        if not any(k in attrs for k in ("application_name", "rtt", "resolution", "date")):
            return None
        hits = []
        for r in slim:
            if r["record_id"] in used:
                continue
            ok = True
            for k, v in attrs.items():
                if k == "date":
                    if v not in (r.get("completed_at") or ""):
                        ok = False
                elif str(r.get(k) or "").lower() != str(v).lower():
                    ok = False
            if ok:
                hits.append(r)
        if len(hits) != 1:
            return None  # 0 或多命中 → 升级 LLM
        used.add(hits[0]["record_id"])
        out.append(hits[0])
    return out if len(out) >= 2 else None


def resolve_listing_llm(llm, agent_reply: str, records: list[dict]) -> list[dict] | None:
    """LLM 兜底: 话术属性匹配失败 (属性歧义/描述形态超规则覆盖) 时升级。
    输出: 话术列出的候选 record_id 有序列表 (按提及顺序)。失败 -> None。"""
    if not agent_reply or not records:
        return None
    slim = [_slim_record(r) for r in records]
    system = ("你是客服话术与记录的匹配器。客服回复里列出了若干候选记录(每条带属性:"
              "应用/服务/清晰度/时延/完成日期)。把话术里的每个候选与 records 表精确匹配,"
              "按话术提及顺序输出匹配到的 record_id 数组。属性要对上(时延数值/清晰度/"
              "完成日期都要一致, 不要按'最近'臆测)。匹配不上就少输出。"
              "只输出 JSON 数组, 如 [\"R-U1959-001\", \"R-U1959-003\"]。")
    task = f"records:\n{json.dumps(slim, ensure_ascii=False)}\n\n客服话术:\n{agent_reply}\n\nrecord_id 数组:"
    try:
        raw = llm.gen_raw_code(system, task)
        m = re.search(r"\[[\s\S]*?\]", raw or "")
        if not m:
            return None
        ids = json.loads(m.group(0))
        if not isinstance(ids, list) or len(ids) < 2:
            return None
        by_id = {r.get("record_id"): r for r in records}
        out = []
        for rid in ids:
            r = by_id.get(rid)
            if r is None:
                return None
            out.append(r)
        return out
    except Exception:
        return None
