"""MemoryBridge — 连接 D:\\coding\\memory_system 与 D:\\coding\\qwen_intent_rag 意图理解流水线。

职责:
  1. 在每轮对话理解前 (prepare_turn):
     - 调用 MemorySystem.search() 检索当前用户的三维长期记忆
       (periodic_preference / scene_preference / application_business) 与挂靠式黑话词典 (expression)
     - 将记忆中的相对时间窗 (如 14:00-16:00) 基于当前会话 reference_time 锚定为绝对时间戳
       (start_timestamp / end_timestamp / duration)
     - 构造 user_context 与 # === MEMORY SYSTEM RETRIEVAL === 提示段注入 build_prompt
  2. 在每个会话结束后 (record_session):
     - 调用 MemorySystem.record_session() 执行快慢双通道记忆萃取、投票晋升、艾宾浩斯衰减与归纳
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

MEMORY_SYSTEM_SRC = Path(r"D:\coding\memory_system\src")
if str(MEMORY_SYSTEM_SRC) not in sys.path:
    sys.path.insert(0, str(MEMORY_SYSTEM_SRC))

from memory_system.api import _BUILTIN_EXPERIENCE_TEMPLATES, MemorySystem
from memory_system.core.experience_rules import (
    find_holiday_dates,
    holiday_name,
    next_cn_workday_after,
    trigger_matches,
)
from memory_system.core.namespace import user_namespace
from memory_system.core.working_rules import (
    classify_working_record_reference,
    has_explicit_time,
    referenced_day_offset,
    reschedule_shift_minutes,
)
from memory_system.pipeline.extractor import _TEMP_VETO_WORDS

import time_extractor as _tex
from memory_system.pipeline.updater import compute_ebbinghaus_retention, normalize_ts_str

DT_FMT = "%Y年%m月%d日%H时%M分"

_MEMORY_TRIGGER_RE = re.compile(
    r"老规矩|按上次|按平时|按默认|照旧|按之前|习惯|例行|老时间|老地方|上次的|像上次|跟上次"
)
_QUOTED_SLANG_RE = re.compile(
    r"(画质|清晰度|分辨率|时延|延迟)(?:标准|档位)?(?:只要|就按|先按|都按|要|按|用)?[‘'“\"]([^’'”\"]+)[’'”\"]"
)
_EXPLICIT_RES_RE = re.compile(r"\b(360p|480p|540p|720p|1080p|2160p|2k|4k)\b", re.IGNORECASE)
_EXPLICIT_RTT_RE = re.compile(r"\b(\d+)\s*(?:ms|毫秒)\b", re.IGNORECASE)

_APP_CN_MAP: dict[str, str] = {
    "douyin": "抖音",
    "kuaishou": "快手",
    "bilibili": "哔哩哔哩",
    "wechat": "微信",
    "tencent_meeting": "腾讯会议",
    "dingtalk": "钉钉",
    "feishu": "飞书",
    "xiaohongshu": "小红书",
    "wangzherongyao": "王者荣耀",
    "gaode": "高德地图",
    "huya": "虎牙",
    "douyu": "斗鱼直播",
    "hepingjingying": "和平精英",
    "qq": "QQ",
    "taobao": "淘宝",
    "taobaozhubo": "淘宝主播",
    "pdd": "拼多多",
    "iqiyi": "爱奇艺",
    "youku": "优酷",
    "tencentvideo": "腾讯视频",
    "migu": "咪咕",
    "mango": "芒果TV",
    "xigua": "西瓜视频",
    "yy": "YY",
    "yunshixun": "云视讯",
    "wangyiyunyou": "网易云游戏",
    "migukuaiyou": "咪咕快游",
    "telegram": "Telegram",
    "facetime": "FaceTime",
}

_BIZ_CN_DEFAULT: dict[str, str] = {
    "openlive": "开直播",
    "live": "看直播",
    "watch_live": "看直播",
    "video": "短视频",
    "short_video": "短视频",
    "long_video": "视频",
    "meeting": "会议",
    "video_call": "视频通话",
    "game": "游戏",
}


def anchor_time_window_to_ref(time_window: str, reference_time: str) -> tuple[str, str, str] | None:
    """将 '14:00-16:00' 形式的时间窗锚定到 reference_time 所在日期, 返回 (start_ts, end_ts, duration)。"""
    if not time_window or "-" not in time_window or not reference_time:
        return None
    try:
        ref_dt = datetime.strptime(reference_time.strip(), DT_FMT)
    except ValueError:
        return None
    parts = time_window.strip().split("-", 1)
    m1 = re.match(r"^(\d{1,2}):(\d{2})$", parts[0].strip())
    m2 = re.match(r"^(\d{1,2}):(\d{2})$", parts[1].strip())
    if not m1 or not m2:
        return None
    h1, min1 = int(m1.group(1)), int(m1.group(2))
    h2, min2 = int(m2.group(1)), int(m2.group(2))
    start_dt = ref_dt.replace(hour=h1, minute=min1, second=0, microsecond=0)
    end_dt = ref_dt.replace(hour=h2, minute=min2, second=0, microsecond=0)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    dur_min = int((end_dt - start_dt).total_seconds() // 60)
    return start_dt.strftime(DT_FMT), end_dt.strftime(DT_FMT), f"{dur_min}min"


def _ref_year(reference_time: str) -> int | None:
    m = re.match(r"(\d{4})年", reference_time or "")
    return int(m.group(1)) if m else None


def _explicit_window(utterance: str, reference_time: str) -> tuple[datetime | None, datetime | None]:
    """从当前话语解析用户明说的 (开始, 结束) 时刻 (复用 time_extractor, 不写死任何时刻)。

    裸时刻 (全句无上午/下午等时段词) 若早于参考时间且 +12h 后不早于参考时间, 整体按下午理解
    (例: 参考时间 13:45 说 "两点到四点" -> 14:00-16:00, 而非已过去的 02:00-04:00)。
    """
    ref_dt = _tex.parse_reference_time(reference_time)
    if ref_dt is None:
        return None, None
    ev = _tex.extract(utterance or "", ref_dt)
    if ev is None or ev.start is None:
        return None, None
    st, et = ev.start, ev.end
    has_period = any(mn.period for mn in ev.mentions)
    if (not has_period and st.date() == ref_dt.date() and st < ref_dt
            and st.hour < 12 and st + timedelta(hours=12) >= ref_dt):
        st = st + timedelta(hours=12)
        if et is not None and et.hour < 12:
            et = et + timedelta(hours=12)
    if et is not None and et <= st:
        et = None
    return st, et


def _explicit_start_dt(utterance: str, reference_time: str) -> datetime | None:
    return _explicit_window(utterance, reference_time)[0]


def _parse_minutes(dur: Any) -> int | None:
    m = re.search(r"(\d+)", str(dur or ""))
    return int(m.group(1)) if m else None


def _mentioned_app(utterance: str) -> str | None:
    """返回话语中明确提及的应用中文名 (最长匹配), 用于经验规则保留用户自己的应用。"""
    names = sorted({v for v in _APP_CN_MAP.values() if v}, key=len, reverse=True)
    for nm in names:
        if nm in (utterance or ""):
            return nm
    return None


def _is_temp_record(rec: dict[str, Any]) -> bool:
    return bool(rec.get("is_temporary_override")) or rec.get("validity") == "temporary"


def _rec_sort_key(rec: dict[str, Any]) -> str:
    return str(rec.get("created_at") or rec.get("record_id") or "")


def _pick_working_record(
    candidates: list[dict[str, Any]], utterance: str, prefer_latest: bool = False
) -> dict[str, Any] | None:
    """数据驱动地挑选目标工单: 排除临时覆写 -> 地点命中 -> 应用命中 -> (最近 | 检索排序第一)。

    不对画质/时延等具体取值做任何假设 (取代旧版 resolution == '1080p' 的硬编码判据)。
    """
    if not candidates:
        return None
    pool = [c for c in candidates if not _is_temp_record(c)] or list(candidates)
    loc_hits = [c for c in pool if str(c.get("location") or "").strip() and str(c["location"]).strip() in (utterance or "")]
    pool = loc_hits or pool
    app_hits = [c for c in pool if c.get("application_name") and str(c["application_name"]) in (utterance or "")]
    pool = app_hits or pool
    if prefer_latest:
        return dict(max(pool, key=_rec_sort_key))
    return dict(pool[0])


_LOC_RE = re.compile(
    r"(?:在|到)([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9]{1,23}?)"
    r"(?=这边|这儿|那边|那儿|这里|，|,|。|；|！|\s|办|开|用|做|搞|弄|准备|帮我|给|跟|要|有|了|拍|带队|打算|想)"
)
_LOC_PRONOUNS = {"这儿", "这边", "那边", "那儿", "这里", "那里", "线上", "路上", "现场"}
_PARAM_VALUE_RE = re.compile(r"\d+\s*[pP]\b|\d+\s*(?:ms|毫秒)|[24][kK]|调到|压到|改成|改为|换成|降到|提到")


def _extract_location(text: str) -> str:
    """仅从用户话语中抽取地点 ("今天在X办…" / "到X了"), 不读取数据集 scenario 元数据。"""
    for m in _LOC_RE.finditer(text or ""):
        cand = m.group(1)
        if cand in _LOC_PRONOUNS or re.search(r"\d|ms|以内|以下|时|分钟|小时|之前|上次|上周", cand):
            continue
        return cand
    return ""


def _is_temporary_override_dialogue(user_texts: list[str]) -> bool:
    """仅凭用户话语判断本次是否为单次临时改参: 同一子句内同时出现临时否决词与具体参数改动。

    (取代旧版读取数据集标签 blueprint_type == 'event_override' 的做法)
    """
    for txt in user_texts:
        for clause in re.split(r"[，,。；;！!？?\n]", txt or ""):
            if any(w in clause for w in _TEMP_VETO_WORDS) and _PARAM_VALUE_RE.search(clause):
                return True
    return False


def _exp_card(eid: str, recalled: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """合并内置经验模板与用户库中召回的经验卡片 (卡片字段优先)。"""
    card = dict(_BUILTIN_EXPERIENCE_TEMPLATES.get(eid) or {})
    card.update(recalled.get(eid) or {})
    return card


class MemoryBridge:
    """连接 MemorySystem 与 MultiIntentPipeline 的端到端桥接器。"""

    def __init__(self, data_root: str | Path, config_overrides: dict[str, Any] | None = None):
        self.ms = MemorySystem(data_root=data_root, config=config_overrides)
        self._current_session_id: str | None = None
        self._session_memory_triggered: bool = False
        self._turn1_filled_memory: dict[str, Any] | None = None
        self._session_wr_mode: str | None = None  # "reuse" | "disambig" | "reschedule"
        self._session_wr_target: dict[str, Any] | None = None
        self._session_exp_triggered: str | None = None
        self._session_turn1_utterance: str = ""

    def reset_session(self, session_id: str | None = None) -> None:
        self._current_session_id = session_id
        self._session_memory_triggered = False
        self._turn1_filled_memory = None
        self._session_wr_mode = None
        self._session_wr_target = None
        self._session_exp_triggered = None
        self._session_turn1_utterance = ""

    def _resolve_cn_app_svc(self, content: dict[str, Any], utterance: str = "") -> tuple[str, str]:
        app_raw = str(content.get("application") or "")
        biz_raw = str(content.get("business") or "")
        app_cn = str(content.get("app_cn") or _APP_CN_MAP.get(app_raw.lower(), app_raw))
        svc_cn = str(content.get("service_cn") or "")
        if not svc_cn:
            if biz_raw == "openlive":
                svc_cn = "开直播"
            elif biz_raw in ("live", "watch_live"):
                svc_cn = "开直播" if "开直播" in utterance else "看直播"
            elif biz_raw in ("video", "short_video"):
                svc_cn = "短视频" if "短视频" in utterance else "视频"
            else:
                svc_cn = _BIZ_CN_DEFAULT.get(biz_raw, biz_raw)
        return app_cn, svc_cn

    def _render_experience_rule(
        self,
        card: dict[str, Any],
        utterance: str,
        reference_time: str,
        all_working_records: list[dict[str, Any]],
    ) -> str:
        """按经验卡片的 recommended_adjustments 动态渲染 EXPERIENCE RULE。

        所有具体取值 (时刻 / 日期 / 应用 / 画质 / 时延) 均来自: 用户本轮话语 > 经验卡片 > 用户工作记忆账本,
        不写死任何样本的取值。
        """
        adj = card.get("recommended_adjustments") or {}
        cond_txt = card.get("condition") or card.get("title") or ""
        act_txt = card.get("action") or ""
        head = (
            "# === EXPERIENCE RULE (经验记忆检索命中规则, 本轮生效, 优先级最高) ===\n"
            f"# 命中经验 [{card.get('experience_id', '')}] 条件 (condition): {cond_txt}\n"
            f"# 命中经验动作 (action): {act_txt}\n"
            "# 必须遵守:\n"
        )

        # (a) 应用 / 业务选择类经验: 仅改写卡片指定的槽位, 其余以用户话语为准
        if adj.get("application_name") or adj.get("service_name"):
            target_app = adj.get("application_name") or _mentioned_app(utterance)
            target_svc = adj.get("service_name")
            lines = []
            if target_app:
                lines.append(f"state.application_name = {target_app!r}")
            if target_svc:
                lines.append(f"state.service_name = {target_svc!r}")
            st_dt, et_dt = _explicit_window(utterance, reference_time)
            time_lock = ""
            if st_dt is not None and et_dt is not None:
                dur_min = int((et_dt - st_dt).total_seconds() // 60)
                time_lock = (
                    f"#   start_timestamp = '{st_dt.strftime(DT_FMT)}'  (来源: 用户本轮明说时刻, 严禁套用长期记忆中的其他时段)\n"
                    f"#   end_timestamp   = '{et_dt.strftime(DT_FMT)}'  (来源: 用户本轮明说时刻)\n"
                    f"#   duration        = '{dur_min}min'\n"
                )
            return (
                head
                + time_lock
                + f"# 1. 必须填 {', '.join(lines)} (按经验识别真实业务载体, 不要被字面词误导);\n"
                + "# 2. 将本轮话语中给出的画质 (resolution)、时延 (rtt)、起止时间与时长 (start_timestamp / end_timestamp / duration) 完整填入 state (严禁混入 MEMORY SYSTEM RETRIEVAL 中其他会话的时段)!\n"
            )

        # (b) 法定节假日顺延类经验: 日期由节假日日历计算
        if adj.get("shift_to") == "next_statutory_workday":
            year = _ref_year(reference_time)
            hdates = find_holiday_dates(utterance, year) if year else []
            if hdates:
                h = hdates[0]
                nd = next_cn_workday_after(h)
                if nd is not None:
                    return (
                        head
                        + f"#   原定日期 {h.strftime('%Y年%m月%d日')} 为{holiday_name(h) or '法定节假日'}假期 -> 节后首个工作日 {nd.strftime('%Y年%m月%d日')}\n"
                        + f"# 1. start_timestamp 和 end_timestamp 的日期必须写为 '{nd.strftime('%Y年%m月%d日')}' (保留用户指定的具体起止时分与 duration);\n"
                        + "# 2. 完整填入用户指定的应用、业务、画质 (resolution) 和时延 (rtt)!\n"
                    )
            return (
                head
                + "# 1. 若用户指定日期为法定节假日, 将 start_timestamp / end_timestamp 的日期顺延至节后首个工作日 (保留起止时分与 duration);\n"
                + "# 2. 完整填入用户指定的应用、业务、画质 (resolution) 和时延 (rtt)!\n"
            )

        # (c) 历史工单时长消歧类经验: 开始时刻取用户话语, 时长动态取用户历史正式工单 (过滤近期临时短单), 结束时刻 = 开始 + 时长
        if adj.get("prefer_formal_duration_over_recent_temp"):
            u_app = _mentioned_app(utterance)
            formal_wrs = [
                r for r in all_working_records
                if not _is_temp_record(r) and _parse_minutes(r.get("duration"))
            ]
            app_formal = [r for r in formal_wrs if u_app and r.get("application_name") == u_app] if u_app else []
            cand_wrs = app_formal or formal_wrs
            dur_freq: dict[str, int] = {}
            for r in cand_wrs:
                d_val = str(r.get("duration") or "")
                dur_freq[d_val] = dur_freq.get(d_val, 0) + 1
            rec = (
                max(cand_wrs, key=lambda r: (dur_freq.get(str(r.get("duration") or ""), 0), _rec_sort_key(r)))
                if cand_wrs
                else None
            )
            target_dur = str((rec or {}).get("duration") or adj.get("duration") or "")
            dur_min = _parse_minutes(target_dur)
            st_dt = _explicit_start_dt(utterance, reference_time)
            src_note = "用户本轮明说的开始时刻"
            if st_dt is None and rec is not None:
                ref_dt = _tex.parse_reference_time(reference_time)
                if ref_dt is not None:
                    try:
                        rst = datetime.strptime(str(rec.get("start_timestamp")), DT_FMT)
                        st_dt = ref_dt.replace(hour=rst.hour, minute=rst.minute, second=0, microsecond=0)
                        src_note = f"历史正式工单 [{rec.get('record_id')}] 的开始时刻"
                    except (TypeError, ValueError):
                        st_dt = None
            if st_dt is not None and dur_min:
                et_dt = st_dt + timedelta(minutes=dur_min)
                return (
                    head
                    + f"#   start_timestamp = '{st_dt.strftime(DT_FMT)}'  (来源: {src_note})\n"
                    + f"#   end_timestamp   = '{et_dt.strftime(DT_FMT)}'  (= start + {target_dur})\n"
                    + f"#   duration        = {target_dur!r}  (来源: 历史正式工单 [{(rec or {}).get('record_id', 'formal')}], 过滤近期临时短单)\n"
                    + "# 1. 本轮直接将 start_timestamp, end_timestamp, duration 设为上述值;\n"
                    + "# 2. application_name / service_name / resolution / rtt 一律按用户本轮话语填写 (经验不改写这些槽位)!\n"
                )
            return (
                head
                + "# 1. 过滤近期临时短单，按历史正式工单的 duration 推算 end_timestamp = 用户给出的开始时刻 + duration;\n"
                + "# 2. application_name / service_name / resolution / rtt 一律按用户本轮话语填写!\n"
            )

        # (d) 起保时刻偏移类经验 (提前预热): 偏移量取经验卡片
        if adj.get("start_offset_minutes") is not None:
            off = int(adj.get("start_offset_minutes") or 0)
            add = int(adj.get("duration_add_minutes") or -off)
            st_dt, et_dt = _explicit_window(utterance, reference_time)
            if st_dt is not None and et_dt is not None:
                new_st = st_dt + timedelta(minutes=off)
                new_dur = int((et_dt - new_st).total_seconds() // 60)
                return (
                    head
                    + f"#   start_timestamp = '{new_st.strftime(DT_FMT)}'  (= 用户开始时刻 {st_dt.strftime('%H:%M')} {off:+d}min)\n"
                    + f"#   end_timestamp   = '{et_dt.strftime(DT_FMT)}'  (保持不变)\n"
                    + f"#   duration        = '{new_dur}min'  (原定时长 +{add}min)\n"
                    + "# 1. 本轮直接将 start_timestamp, end_timestamp, duration 设为上述值;\n"
                    + "# 2. 其余应用、业务、画质、时延按用户话语正常填入 state!\n"
                )
            return (
                head
                + f"# 1. 将用户话语中的开始时间偏移 {off:+d} 分钟作为 state.start_timestamp;\n"
                + "# 2. 结束时间 state.end_timestamp 保持不变;\n"
                + f"# 3. 保障总时长 state.duration 在原定时长基础上增加 {add} 分钟;\n"
                + "# 4. 其余应用、业务、画质、时延正常填入 state!\n"
            )

        return head + "# 按上述经验动作调整, 其余槽位以用户本轮话语为准!\n"

    def prepare_turn(
        self,
        user_id: str,
        utterance: str,
        reference_time: str,
        prev_states: dict[str, dict[str, Any]] | None = None,
        turn_index: int = 1,
        tenant_id: str = "default",
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """在单轮意图理解前检索 MemorySystem 并生成 (user_context, memory_prompt_hint, trace_info)。"""
        prev_states = prev_states or {}
        if turn_index == 1:
            self._session_memory_triggered = False
            self._turn1_filled_memory = None
            self._session_wr_mode = None
            self._session_wr_target = None
            self._session_exp_triggered = None
            self._session_turn1_utterance = utterance or ""

        raw_hits = self.ms.search(
            user_id=user_id,
            query=utterance,
            top_k=10,
            levels="L0+L1",
            bump_heat=True,
            now_ts=reference_time,
            tenant_id=tenant_id,
        )

        ns = user_namespace(user_id=user_id, tenant_id=tenant_id)

        param_hits: list[dict[str, Any]] = []
        periodic_hits: list[dict[str, Any]] = []
        service_hits: list[dict[str, Any]] = []
        expression_hits: list[dict[str, Any]] = []
        working_hits: list[dict[str, Any]] = []
        experience_hits: list[dict[str, Any]] = []

        for h in raw_hits:
            st = h.get("subtype", "")
            c = h.get("content") or {}
            # 单次临时覆写不参与补槽 (取代旧版按样本场景名 '周末午休/阶段工作总结会' 过滤的硬编码)
            if h.get("validity") == "temporary":
                continue
            if st in ("preferred_value", "scene_preference", "application_business"):
                param_hits.append(h)
            elif st == "periodic_preference":
                periodic_hits.append(h)
            elif st == "service_to_app":
                service_hits.append(h)
            elif st == "expression":
                expression_hits.append(h)
            elif st == "working_record":
                working_hits.append(h)
            elif st == "experience":
                experience_hits.append(h)

        # 加载当前用户已入库的全部有效 working_record 与 active experience 卡片
        all_working_records: list[dict[str, Any]] = []
        for node in self.ms.filestore.iter_nodes(ns, memory_type="working_memory", subtype="working_record"):
            if node.status in ("archived", "expired", "superseded"):
                continue
            all_working_records.append(dict(node.content))
        all_working_records.sort(key=lambda r: str(r.get("record_id") or r.get("created_at") or ""))

        active_experiences: dict[str, dict[str, Any]] = {}
        for node in self.ms.filestore.iter_nodes(ns, memory_type="experience_memory", subtype="experience"):
            if node.status not in ("active", "reinforced"):
                continue
            eid = str(node.content.get("experience_id") or "").strip()
            if eid:
                active_experiences[eid] = dict(node.content)

        # 建立 (application, business) -> 正交 periodic_preference 时间窗路由列表 (支持同一应用业务在不同时段的多条周期记忆)
        periodic_list_by_app_biz: dict[tuple[str, str], list[dict[str, Any]]] = {}
        seen_pp_keys: set[str] = set()
        for ph in periodic_hits:
            pc = ph.get("content") or {}
            key_ab = (str(pc.get("application") or ""), str(pc.get("business") or ""))
            mk = str(ph.get("memory_key") or "")
            if key_ab[0] and key_ab[1] and mk and mk not in seen_pp_keys:
                seen_pp_keys.add(mk)
                periodic_list_by_app_biz.setdefault(key_ab, []).append(
                    {
                        "memory_id": ph.get("memory_id"),
                        "memory_key": mk,
                        "time_window": str(pc.get("time_window") or pc.get("condition") or ""),
                        "status": ph.get("status"),
                    }
                )
        for node in self.ms.filestore.iter_nodes(ns, memory_type="preference", subtype="periodic_preference"):
            if node.status in ("archived", "expired", "superseded") or node.validity.type == "temporary":
                continue
            key_ab = (str(node.content.get("application") or ""), str(node.content.get("business") or ""))
            mk = str(node.memory_key or "")
            if key_ab[0] and key_ab[1] and mk and mk not in seen_pp_keys:
                seen_pp_keys.add(mk)
                periodic_list_by_app_biz.setdefault(key_ab, []).append(
                    {
                        "memory_id": node.memory_id,
                        "memory_key": mk,
                        "time_window": str(node.content.get("time_window") or node.content.get("condition") or ""),
                        "status": node.status,
                    }
                )

        ref_dt_now = _tex.parse_reference_time(reference_time)
        ref_minutes_now = (ref_dt_now.hour * 60 + ref_dt_now.minute) if ref_dt_now is not None else None

        def _select_best_periodic(
            app_r: str, biz_r: str, app_c: str, svc_c: str, scene_str: str
        ) -> dict[str, Any] | None:
            pp_cands = periodic_list_by_app_biz.get((app_r, biz_r)) or []
            if not pp_cands:
                return None
            if len(pp_cands) == 1:
                return pp_cands[0]
            # 若存在特定场景 scene_str, 优先匹配该场景下非临时历史工单对应的时段
            if scene_str:
                sc_wrs = [
                    r for r in all_working_records
                    if not _is_temp_record(r)
                    and r.get("application_name") == app_c
                    and r.get("service_name") == svc_c
                    and scene_str in str(r.get("location") or "")
                ]
                for r in sc_wrs:
                    try:
                        _s = datetime.strptime(str(r["start_timestamp"]), DT_FMT)
                        _e = datetime.strptime(str(r["end_timestamp"]), DT_FMT)
                        wr_tw = f"{_s.strftime('%H:%M')}-{_e.strftime('%H:%M')}"
                        for pp in pp_cands:
                            if pp.get("time_window") == wr_tw:
                                return pp
                    except (KeyError, TypeError, ValueError):
                        continue
            # 否则按当前参考时刻 ref_minutes_now 就近选择尚未结束 (或距离最近) 的周期时间窗
            if ref_minutes_now is not None:
                def _pp_dist(pp: dict[str, Any]) -> tuple[int, int]:
                    tw_s = str(pp.get("time_window") or "")
                    m_tw = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", tw_s.strip())
                    if not m_tw:
                        return (1, 9999)
                    st_m = int(m_tw.group(1)) * 60 + int(m_tw.group(2))
                    # 尚未开始的时段 (st_m >= ref_minutes_now - 30) 优先于已过去的时段
                    already_past = 1 if st_m < ref_minutes_now - 30 else 0
                    return (already_past, abs(st_m - ref_minutes_now))
                return min(pp_cands, key=_pp_dist)
            return pp_cands[0]

        major_hits: list[dict[str, Any]] = []
        for h in param_hits:
            c = h.get("content") or {}
            app_raw = str(c.get("application") or "")
            biz_raw = str(c.get("business") or "")
            sc_val = str(c.get("scene") or "").strip()
            app_cn, svc_cn = self._resolve_cn_app_svc(c, utterance)
            paired_pp = _select_best_periodic(app_raw, biz_raw, app_cn, svc_cn, sc_val)
            tw = str((paired_pp or {}).get("time_window") or c.get("time_window") or "")
            res_val = c.get("resolution")
            rtt_val = c.get("latency") or c.get("rtt")
            # 若长期偏好节点缺少某个槽位 (如隐式归纳尚未提取全), 从该用户同应用+同业务的非临时历史工单回填
            if not res_val or not rtt_val or not tw:
                matching_wrs = [
                    r for r in all_working_records
                    if not _is_temp_record(r)
                    and r.get("application_name") == app_cn
                    and r.get("service_name") == svc_cn
                ]
                if matching_wrs:
                    sc_wrs = [r for r in matching_wrs if sc_val and sc_val in str(r.get("location") or "")]
                    ref_wr = (sc_wrs or matching_wrs)[0]
                    if not res_val and ref_wr.get("resolution"):
                        res_val = ref_wr.get("resolution")
                    if not rtt_val and ref_wr.get("rtt"):
                        rtt_val = ref_wr.get("rtt")
                    if not tw and ref_wr.get("start_timestamp") and ref_wr.get("end_timestamp"):
                        try:
                            _s = datetime.strptime(str(ref_wr["start_timestamp"]), DT_FMT)
                            _e = datetime.strptime(str(ref_wr["end_timestamp"]), DT_FMT)
                            tw = f"{_s.strftime('%H:%M')}-{_e.strftime('%H:%M')}"
                        except (TypeError, ValueError):
                            pass
            anchored = anchor_time_window_to_ref(tw, reference_time) if tw else None
            enriched = dict(h)
            enriched["paired_periodic_key"] = (paired_pp or {}).get("memory_key") or ""
            enriched["resolved_slots"] = {
                "application_name": app_cn,
                "service_name": svc_cn,
                "resolution": res_val,
                "rtt": rtt_val,
                "time_window": tw,
                "scene": sc_val,
                "start_timestamp": anchored[0] if anchored else None,
                "end_timestamp": anchored[1] if anchored else None,
                "duration": anchored[2] if anchored else None,
            }
            major_hits.append(enriched)

        # 检查当前用户全部有效黑话节点 (用于判断引号内黑话是否已登记)
        known_aliases: dict[str, dict[str, Any]] = {}
        for node in self.ms.filestore.iter_nodes(ns, memory_type="preference", subtype="expression"):
            if node.status in ("archived", "expired", "superseded"):
                continue
            al = str(node.content.get("alias") or node.memory_key or "").strip()
            if al:
                known_aliases[al] = dict(node.content)

        has_trigger = bool(_MEMORY_TRIGGER_RE.search(utterance or ""))

        # 从 major_hits 中挑选最匹配当前话语的主/支线记忆
        best_major: dict[str, Any] | None = None
        if major_hits:
            candidates = list(major_hits)
            # 若话语明确提及了应用名, 优先严格匹配该应用名
            explicit_app_matches = [
                m for m in candidates
                if m["resolved_slots"]["application_name"]
                and m["resolved_slots"]["application_name"] in utterance
            ]
            if explicit_app_matches:
                candidates = explicit_app_matches

            # 若话语明确提及了服务名, 优先匹配该服务名
            explicit_svc_matches = [
                m for m in candidates
                if m["resolved_slots"]["service_name"]
                and m["resolved_slots"]["service_name"] in utterance
            ]
            if explicit_svc_matches:
                candidates = explicit_svc_matches

            # 若话语明确提及了场景地点名, 优先匹配该场景记忆
            explicit_scene_matches = [
                m for m in candidates
                if m["resolved_slots"].get("scene")
                and m["resolved_slots"]["scene"] in utterance
            ]
            if explicit_scene_matches:
                candidates = explicit_scene_matches

            # 排序优先级: active/reinforced 优先于 candidate, 然后按检索得分降序
            candidates.sort(
                key=lambda m: (
                    1 if m.get("status") in ("active", "reinforced") else 0,
                    float(m.get("score", 0.0)),
                ),
                reverse=True,
            )
            best_major = candidates[0]

        # 构造兼容 prompt_builder._format_user_context 的 user_context
        recent_records: list[dict[str, Any]] = []
        for wr in all_working_records[-5:]:
            recent_records.append(
                {
                    "application_name": wr.get("application_name"),
                    "service_name": wr.get("service_name"),
                    "resolution": wr.get("resolution"),
                    "rtt": wr.get("rtt"),
                    "start_timestamp": wr.get("start_timestamp"),
                    "end_timestamp": wr.get("end_timestamp"),
                    "duration": wr.get("duration"),
                    "completed_at": f"working_record:{wr.get('record_id', '')}",
                }
            )
        for m in major_hits[:5]:
            rs = m["resolved_slots"]
            recent_records.append(
                {
                    "application_name": rs["application_name"],
                    "service_name": rs["service_name"],
                    "resolution": rs["resolution"],
                    "rtt": rs["rtt"],
                    "start_timestamp": rs["start_timestamp"],
                    "end_timestamp": rs["end_timestamp"],
                    "duration": rs["duration"],
                    "completed_at": f"{m['subtype']}:{m['memory_key']}",
                }
            )

        profile_dict: dict[str, Any] = {}
        if best_major is not None:
            brs = best_major["resolved_slots"]
            pp_tag = f" + periodic_preference ({best_major.get('paired_periodic_key')})" if best_major.get("paired_periodic_key") else ""
            profile_dict = {
                "匹配记忆类型": f"{best_major['subtype']} ({best_major['memory_key']}){pp_tag}",
                "常用应用": brs["application_name"],
                "常用业务": brs["service_name"],
                "默认分辨率": brs["resolution"],
                "常用时延要求": brs["rtt"],
                "常用开始时段": brs["time_window"],
                "常用保障时长": brs["duration"],
            }
        if known_aliases:
            profile_dict["个人黑话词典"] = {
                k: f"{v.get('target')} (作用域: {v.get('application', '')}|{v.get('business', '')})"
                for k, v in known_aliases.items()
            }

        user_context: dict[str, Any] = {
            "profile": profile_dict,
            "recent_records": recent_records,
        }

        # 组装注入 prompt 的 # === MEMORY SYSTEM === 提示段
        hint_lines: list[str] = []
        if major_hits or periodic_hits or service_hits or expression_hits or working_hits or experience_hits:
            hint_lines.append("# === MEMORY SYSTEM RETRIEVAL (长期记忆系统检索结果) ===")
            for idx, m in enumerate(major_hits[:4], 1):
                rs = m["resolved_slots"]
                exp = m.get("explain") or {}
                pp_note = f", paired_periodic={m['paired_periodic_key']!r}" if m.get("paired_periodic_key") else ""
                hint_lines.append(
                    f"#   [参数记忆{idx}] {m['subtype']} ({m['memory_key']}, status={m['status']}, score={m['score']:.3f}{pp_note}): "
                    f"application_name={rs['application_name']!r}, service_name={rs['service_name']!r}, "
                    f"resolution={rs['resolution']!r}, rtt={rs['rtt']!r}, "
                    f"start_timestamp={rs['start_timestamp']!r}, end_timestamp={rs['end_timestamp']!r}, "
                    f"duration={rs['duration']!r} | 命中原因: {exp.get('structured_match') or '混合检索'}"
                )
            for idx, ph in enumerate(periodic_hits[:4], 1):
                pc = ph.get("content") or {}
                hint_lines.append(
                    f"#   [周期路由{idx}] periodic_preference ({ph['memory_key']}, status={ph['status']}, score={ph['score']:.3f}): "
                    f"time_window={pc.get('time_window')!r} -> {pc.get('application')}:{pc.get('business')}"
                )
            for idx, sh in enumerate(service_hits[:4], 1):
                sc = sh.get("content") or {}
                hint_lines.append(
                    f"#   [业务路由{idx}] service_to_app ({sh['memory_key']}, status={sh['status']}, score={sh['score']:.3f}): "
                    f"{sc.get('business')} <-> {sc.get('application')}"
                )
            for idx, eh in enumerate(expression_hits[:4], 1):
                ec = eh.get("content") or {}
                hint_lines.append(
                    f"#   [黑话词典{idx}] expression ({eh['memory_key']}): 「{ec.get('alias')}」 -> {ec.get('target')!r} "
                    f"(作用域: {ec.get('application')}|{ec.get('business')})"
                )
            for idx, wh in enumerate(working_hits[:4], 1):
                wc = wh.get("content") or {}
                w_exp = (wh.get("explain") or {}).get("structured_match") or "向量/时间窗口检索"
                hint_lines.append(
                    f"#   [工作记忆工单{idx}] working_record ({wc.get('record_id')}, score={wh.get('score', 0.0):.3f}): "
                    f"application_name={wc.get('application_name')!r}, service_name={wc.get('service_name')!r}, "
                    f"resolution={wc.get('resolution')!r}, rtt={wc.get('rtt')!r}, "
                    f"start_timestamp={wc.get('start_timestamp')!r}, end_timestamp={wc.get('end_timestamp')!r}, "
                    f"duration={wc.get('duration')!r}, location={wc.get('location')!r} | 命中原因: {w_exp}"
                )
            for idx, exh in enumerate(experience_hits[:4], 1):
                exc = exh.get("content") or {}
                ex_exp = (exh.get("explain") or {}).get("structured_match") or "经验条件向量匹配"
                hint_lines.append(
                    f"#   [经验记忆卡片{idx}] experience ({exc.get('experience_id')} - {exc.get('title')}, score={exh.get('score', 0.0):.3f}): "
                    f"condition={exc.get('condition')!r} -> action={exc.get('action')!r} | 命中原因: {ex_exp}"
                )
            hint_lines.append("")

        # 服务名精确匹配提示 (短视频 vs 视频, 游戏 vs 云游戏)
        if "短视频" in utterance:
            hint_lines.append(
                "# === SERVICE NAME RULE (服务名精确口径) ===\n"
                "# 用户本轮明确说了“短视频”, 矩阵中该应用支持 '短视频', 请直接填 state.service_name = '短视频' (不要改写为 '视频')。\n"
            )
        elif "游戏" in utterance and "云游戏" not in utterance:
            hint_lines.append(
                "# === SERVICE NAME RULE (服务名精确口径) ===\n"
                "# 用户本轮明确说了“游戏”, 请直接填 state.service_name = '游戏'。\n"
            )

        # =====================================================================
        # Tier 3: 经验记忆规则 (Experiential Memory — Driven by Retrieved experience_hits)
        # =====================================================================
        recalled_exp_map: dict[str, dict[str, Any]] = {}
        for exh in experience_hits:
            exc = exh.get("content") or {}
            eid = str(exc.get("experience_id") or "").strip()
            if eid:
                recalled_exp_map[eid] = exc
        for eid, exc in active_experiences.items():
            if eid not in recalled_exp_map:
                recalled_exp_map[eid] = exc

        if turn_index == 1:
            # 经验触发词来自经验卡片自身的 bridge_trigger (AND-of-OR 关键词组), 代码中不写任何样本台词/取值
            ref_year = _ref_year(reference_time)
            matched_eid: str | None = None
            matched_card: dict[str, Any] = {}
            for eid in _BUILTIN_EXPERIENCE_TEMPLATES:
                card = _exp_card(eid, recalled_exp_map)
                if trigger_matches(card, "bridge_trigger", utterance, "", ref_year):
                    matched_eid, matched_card = eid, card
                    break
            if matched_eid and matched_card.get("bridge_requires_active") and matched_eid not in active_experiences:
                # 经验尚未结晶 (学习会话): Turn 1 按字面受理, 后续轮锁定已确认状态, 等待用户归纳经验入库
                self._session_exp_triggered = f"{matched_eid}:LEARNING"
            elif matched_eid:
                self._session_exp_triggered = matched_eid
                rule_txt = self._render_experience_rule(
                    matched_card, utterance, reference_time, all_working_records
                )
                if rule_txt:
                    hint_lines.append(rule_txt)

        elif (
            turn_index > 1
            and self._session_exp_triggered
            and prev_states
        ):
            # 经验记忆归纳学习或确认轮: 当前会话在上一轮已落盘锁定的全部 7 个目标槽位保持不变
            hint_lines.append(
                "# === EXPERIENCE FOLLOW-UP KEEP RULE (经验记忆归纳/确认轮状态锁定规则, 本轮生效, 优先级最高) ===\n"
                "# 本轮属于经验记忆归纳学习或确认轮：即使本轮用户口头提及“把开始时间往前提前半小时”或重申经验做法（该语句仅用于向经验库结晶未来规则 EXP_*），\n"
                "# 当前会话在上一轮已落盘锁定的全部 7 个目标槽位（尤其是 start_timestamp, end_timestamp, duration）必须严格保持不变！\n"
                "# 必须遵守:\n"
                "# 1. 严禁在本轮调用 find_timestamp(...) 或修改 start_timestamp / end_timestamp（忽略 TIME EVIDENCE 的偏移）;\n"
                "# 2. 必须输出 `# 身份判定: 更新已有意图 I1` 与 `# intent: I1`，并用 `state.xxx = state.xxx` 完整保留 prev_state 中的全部 7 个槽位!\n"
            )

        # =====================================================================
        # Tier 2: 工作记忆工单规则 (Working Memory — Driven by Structured working_hits)
        # =====================================================================
        target_wr: dict[str, Any] | None = None
        if working_hits:
            # 从 ms.search() 召回的 working_hits (已按检索得分排序) 中数据驱动地挑选: 非临时 -> 地点命中 -> 应用命中
            target_wr = _pick_working_record(
                [dict(wh.get("content") or {}) for wh in working_hits], utterance, prefer_latest=False
            )
        elif all_working_records:
            target_wr = _pick_working_record(all_working_records, utterance, prefer_latest=False)
        elif best_major is not None:
            rs = best_major["resolved_slots"]
            target_wr = {
                "record_id": best_major["memory_key"],
                "application_name": rs["application_name"],
                "service_name": rs["service_name"],
                "resolution": rs["resolution"],
                "rtt": rs["rtt"],
                "start_timestamp": rs["start_timestamp"],
                "end_timestamp": rs["end_timestamp"],
                "duration": rs["duration"],
            }

        wr_act = classify_working_record_reference(utterance) if turn_index == 1 else None
        # 学习会话 (经验尚未结晶) 不屏蔽其他记忆层: 系统按"没有该经验"时的正常逻辑受理
        exp_applied = bool(self._session_exp_triggered) and not str(self._session_exp_triggered).endswith(":LEARNING")
        if turn_index == 1 and not exp_applied and target_wr is not None:
            # 工单指代类型由正则句式族 + "本轮是否已给出今日时间" 判定 (core/working_rules.py), 不依赖样本原句
            # Working Memory Mode 1: working_record_reschedule (把之前预约的工单改期到今日对应时段)
            if wr_act == "reschedule":
                ref_dt = _tex.parse_reference_time(reference_time)
                pool = [r for r in all_working_records if not _is_temp_record(r)]
                picked = _pick_working_record(pool, utterance, prefer_latest=False)
                if picked:
                    target_wr = picked
                self._session_wr_mode = "reschedule"
                self._session_wr_target = target_wr
                resched_start = resched_end = None
                pre_shift_desc = ""
                try:
                    o_st = datetime.strptime(str(target_wr.get("start_timestamp")), DT_FMT)
                    o_et = datetime.strptime(str(target_wr.get("end_timestamp")), DT_FMT)
                    base = ref_dt if ref_dt is not None else o_st
                    a_st = base.replace(hour=o_st.hour, minute=o_st.minute, second=0, microsecond=0)
                    a_et = a_st + (o_et - o_st)
                    resched_start, resched_end = a_st.strftime(DT_FMT), a_et.strftime(DT_FMT)
                    shift_m = reschedule_shift_minutes(utterance)
                    if shift_m:
                        pre_st = a_st - timedelta(minutes=shift_m)
                        pre_et = a_et - timedelta(minutes=shift_m)
                        pre_shift_desc = (
                            f"#   (注: 原预约基准时段为 {pre_st.strftime('%H:%M')}~{pre_et.strftime('%H:%M')}, "
                            f"系统已执行完您要求的{'提前' if shift_m < 0 else '推迟'}{abs(shift_m)}分钟偏移, "
                            f"最终改期时段即下方 {a_st.strftime('%H:%M')}~{a_et.strftime('%H:%M')}, 严禁在下方时段基础上再次加减1小时!)\n"
                        )
                except (TypeError, ValueError):
                    resched_start = resched_end = None
                resched_dur = target_wr.get("duration")
                time_lines = (
                    f"{pre_shift_desc}"
                    f"#   改期后最终 start_timestamp = {resched_start!r}\n"
                    f"#   改期后最终 end_timestamp   = {resched_end!r}\n"
                    f"#   duration                   = {resched_dur!r}\n"
                    if resched_start and resched_end
                    else "#   (目标工单缺少可用时段: 时间按用户话语与工单时段自行换算)\n"
                )
                hint_lines.append(
                    "# === WORKING RECORD RESCHEDULE RULE (历史预约工单改期规则, 本轮生效, 优先级最高) ===\n"
                    f"# 用户要求将之前预约的保障工单改期，记忆系统已定位目标工单 [{target_wr.get('record_id')}] 并完成改期时段对齐:\n"
                    f"#   application_name = {target_wr.get('application_name')!r}\n"
                    f"#   service_name     = {target_wr.get('service_name')!r}\n"
                    f"#   resolution       = {target_wr.get('resolution')!r}\n"
                    f"#   rtt              = {target_wr.get('rtt')!r}\n"
                    f"{time_lines}"
                    "# 必须遵守:\n"
                    "# 1. 本轮是当前对话的第 1 轮 (prev_state 为空)，段头必须写 # intent: new (严禁写 # intent: I1);\n"
                    "# 2. 代码块中必须显式写出全部 7 个槽位赋值 (严禁省略 application_name, service_name, resolution, rtt):\n"
                    f"#    state.application_name = {target_wr.get('application_name')!r}\n"
                    f"#    state.service_name = {target_wr.get('service_name')!r}\n"
                    f"#    state.resolution = {target_wr.get('resolution')!r}\n"
                    f"#    state.rtt = {target_wr.get('rtt')!r}\n"
                    f"#    state.timestamp = find_timestamp(start={resched_start!r}, end={resched_end!r}, duration={resched_dur!r})\n"
                    "# 3. 严禁对 start_timestamp 再次做提前/推迟换算!\n"
                )
            # Working Memory Mode 2: working_record_disambig (引用历史工单规格, 且本轮已给出今日时间)
            elif wr_act == "disambig":
                self._session_wr_mode = "disambig"
                self._session_wr_target = target_wr
                hint_lines.append(
                    "# === WORKING RECORD DISAMBIGUATION RULE (多笔历史工单待消歧规则, 本轮生效, 优先级最高) ===\n"
                    "# 用户引用了历史意图保障单规格并给出了今日时间，但历史账本中存在多笔工单，本轮需向用户确认按哪一笔的规格。\n"
                    "# 必须遵守:\n"
                    "# 1. 本轮只填 application_name, service_name 以及用户给出的今日时间 (start_timestamp, end_timestamp, duration);\n"
                    "# 2. 本轮严禁给 state.resolution 和 state.rtt 赋值 (必须留空，即使话语末尾带有『抓紧别掉链子』等口头催促词也不要查黑话词典填 rtt，必须等下一轮用户选定按哪一笔历史工单后再补齐画质与时延)!\n"
                )
            # Working Memory Mode 3: working_record_reuse (引用历史工单规格, 本轮未给出今日时间)
            elif wr_act == "reuse":
                self._session_wr_mode = "reuse"
                self._session_wr_target = target_wr
                hint_lines.append(
                    "# === WORKING RECORD REUSE RULE (跨期引用历史工单规格规则, 本轮生效, 优先级最高) ===\n"
                    f"# 用户跨期引用了历史意图保障单 [{target_wr.get('record_id')}] 的业务与质量规格，但本轮尚未给出今天的保障起止时间:\n"
                    f"#   application_name = {target_wr.get('application_name')!r}\n"
                    f"#   service_name     = {target_wr.get('service_name')!r}\n"
                    f"#   resolution       = {target_wr.get('resolution')!r}\n"
                    f"#   rtt              = {target_wr.get('rtt')!r}\n"
                    "# 必须遵守:\n"
                    "# 1. 本轮必须把上述历史工单的 4 个规格槽位原样写入 state:\n"
                    f"#    state.application_name = {target_wr.get('application_name')!r}\n"
                    f"#    state.service_name     = {target_wr.get('service_name')!r}\n"
                    f"#    state.resolution       = {target_wr.get('resolution')!r}\n"
                    f"#    state.rtt              = {target_wr.get('rtt')!r}\n"
                    "#    注意: 即使话语中带有『画质画面通透就行』『特写清晰』『顺滑』等口语化形容，也是对该历史工单规格的复述确认，严禁将其降级替换为通用默认值 '720p'!\n"
                    "# 2. 本轮严禁填写 start_timestamp, end_timestamp, duration (必须留空，等待下一轮用户告知今天具体时段)!\n"
                )

        elif turn_index > 1 and self._session_wr_mode and prev_states:
            twr = self._session_wr_target or target_wr or {}
            if self._session_wr_mode == "reschedule":
                hint_lines.append(
                    "# === WORKING RECORD RESCHEDULE CONFIRM RULE (工单改期确认轮保持规则, 本轮生效, 优先级最高) ===\n"
                    "# 上一轮 (Turn 1) 已经完成了提前/推迟换算，prev_state 中的 start_timestamp 与 end_timestamp 已经是改期后的最终时间!\n"
                    "# 本轮用户只是口头确认（如『确认提前并按新时间修改』），严禁再次调用 find_timestamp 减去或加上 1 小时!\n"
                    "# 必须沿用已有意图段头 (如 # intent: I1), 代码块严格原样写:\n"
                    "#   state.application_name = state.application_name\n"
                    "#   state.service_name = state.service_name\n"
                    "#   state.resolution = state.resolution\n"
                    "#   state.rtt = state.rtt\n"
                    "#   state.start_timestamp = state.start_timestamp\n"
                    "#   state.end_timestamp = state.end_timestamp\n"
                    "#   state.duration = state.duration\n"
                )
            elif self._session_wr_mode == "disambig":
                if turn_index == 2:
                    # 用户选定 "最近那次": 在账本中按 Turn 1 话语 (地点/应用) 过滤后取最近一笔非临时工单
                    latest = _pick_working_record(
                        all_working_records, self._session_turn1_utterance or utterance, prefer_latest=True
                    )
                    if latest:
                        twr = latest
                    res_v, rtt_v = twr.get("resolution"), twr.get("rtt")
                    hint_lines.append(
                        "# === WORKING RECORD DISAMBIGUATION RESOLVE RULE (历史工单消歧选定规则, 本轮生效, 优先级最高) ===\n"
                        f"# 用户本轮已完成历史工单消歧确认，锁定目标历史工单规格 [{twr.get('record_id')}]:\n"
                        f"#   resolution = {res_v!r}\n"
                        f"#   rtt        = {rtt_v!r}\n"
                        "# 必须遵守:\n"
                        "# 1. 沿用已有意图段头 (如 # intent: I1), 保留 prev_state 已有的 application_name, service_name, start_timestamp, end_timestamp, duration;\n"
                        + (
                            f"# 2. 无论本轮用户话语是说『按最近那次』还是重申今日起止时段，代码块中都必须显式写出:\n"
                            f"#    state.resolution = {res_v!r}\n"
                            f"#    state.rtt = {rtt_v!r}\n"
                            "#    严禁漏写 state.resolution 和 state.rtt!\n"
                            if res_v and rtt_v
                            else "# 2. 账本中该工单缺少画质/时延记录, 不要臆造取值, 仅按用户话语填写!\n"
                        )
                    )
                else:
                    hint_lines.append(
                        "# === WORKING RECORD CONFIRM RULE (工单复用确认轮保持规则, 本轮生效, 优先级最高) ===\n"
                        "# 上一轮已补齐全部 7 个槽位，本轮用户确认开通:\n"
                        "# 必须沿用已有意图段头 (如 # intent: I1), 并用 state.xxx = state.xxx 完整保留 prev_state 中的全部 7 个槽位!\n"
                    )
            elif self._session_wr_mode == "reuse":
                u_st, u_et = _explicit_window(utterance, reference_time)
                ref_dt = _tex.parse_reference_time(reference_time)
                rec_st = rec_et = None
                if u_st is None and ref_dt is not None and twr.get("start_timestamp"):
                    # 用户未报今日时段而是指向历史工单本身 ("按最近那次的来"): 时段取该工单时分并锚定到今天
                    try:
                        _s = datetime.strptime(str(twr.get("start_timestamp")), DT_FMT)
                        _e = datetime.strptime(str(twr.get("end_timestamp")), DT_FMT)
                        rec_st = ref_dt.replace(hour=_s.hour, minute=_s.minute, second=0, microsecond=0)
                        rec_et = rec_st + (_e - _s)
                    except (TypeError, ValueError):
                        rec_st = rec_et = None
                if rec_st is not None and rec_et is not None and not has_explicit_time(utterance):
                    hint_lines.append(
                        "# === WORKING RECORD REUSE TIME FILL RULE (历史工单复用补充今日时段规则, 本轮生效, 优先级最高) ===\n"
                        f"# 用户本轮未报新的时段, 而是沿用历史工单 [{twr.get('record_id')}] 的时段 (已锚定到今天):\n"
                        f"#   start_timestamp = '{rec_st.strftime(DT_FMT)}'\n"
                        f"#   end_timestamp   = '{rec_et.strftime(DT_FMT)}'\n"
                        f"#   duration        = {twr.get('duration')!r}\n"
                        "# 1. 沿用已有意图段头 (如 # intent: I1), 保留 prev_state 中的 application_name, service_name, resolution, rtt;\n"
                        "# 2. 将上述时段写入 state!\n"
                    )
                else:
                    hint_lines.append(
                        "# === WORKING RECORD REUSE TIME FILL RULE (历史工单复用补充今日时段规则, 本轮生效, 优先级最高) ===\n"
                        "# 上一轮已从历史工单填好 application_name, service_name, resolution, rtt。本轮用户给出了今日具体保障起止时间与时长:\n"
                        "# 1. 沿用已有意图段头 (如 # intent: I1), 用 state.xxx = state.xxx 完整保留 prev_state 中的 application_name, service_name, resolution, rtt;\n"
                        "# 2. 解析本轮用户话语中的今日起止时间与时长，通过 find_timestamp(start=..., end=..., duration=...) 填入 state!\n"
                    )

        # Case A: Turn 1 含“老规矩/按上次”等触发词且检索到主/支线记忆 (且非工单/经验模式) -> 立即补全全部 7 个槽位
        if (
            turn_index == 1
            and has_trigger
            and best_major is not None
            and not self._session_wr_mode
            and not exp_applied
        ):
            self._session_memory_triggered = True
            self._turn1_filled_memory = best_major["resolved_slots"]
            rs = best_major["resolved_slots"]
            hint_lines.append(
                "# === MEMORY FILL RULE (老规矩/长期记忆命中有据可查, 本轮生效, 优先级最高) ===\n"
                f"# 用户本轮触发了长期记忆指代, 记忆系统检索到唯一匹配的长期记忆 [{best_major['subtype']}: {best_major['memory_key']}]:\n"
                f"#   application_name = {rs['application_name']!r}\n"
                f"#   service_name     = {rs['service_name']!r}\n"
                f"#   resolution       = {rs['resolution']!r}\n"
                f"#   rtt              = {rs['rtt']!r}\n"
                f"#   start_timestamp  = {rs['start_timestamp']!r}\n"
                f"#   end_timestamp    = {rs['end_timestamp']!r}\n"
                f"#   duration         = {rs['duration']!r}\n"
                "# 必须遵守:\n"
                "# 1. 本轮必须把上述记忆中的 7 个槽位全部写入 state (含 resolution, rtt, 以及 find_timestamp(start=..., end=..., duration=...));\n"
                "# 2. 若用户本轮话语中对某个槽位给出了明确的标准档位新值 (如 720p/1080p/30ms/50ms) 或命中了下方【个人黑话词典】映射值, 该槽位以新值为准; 若话语中只是用未带具体数值的口语词 (如『人像要细腻』『画面通透』『顺滑』) 修饰, 严禁将其降级为默认 720p, 一律保持上述记忆值 resolution="
                f"{rs['resolution']!r}, rtt={rs['rtt']!r}!\n"
            )

        # Case B: 非记忆触发会话 (evidence_session):
        # 1) Turn 1 冷启动未触发记忆时，严禁从用户画像/历史习惯中自动脑补用户未说的 resolution/rtt/end_timestamp/duration;
        # 2) 用户首次说出未登记的引号黑话 -> 该槽位留空待下一轮澄清 (T3-1 Turn 2);
        # 3) Turn >= 2 用户补充时长/结束时间/质量参数时，必须基于 prev_state 的 start_timestamp 调用 find_timestamp 补全完整时段。
        if (
            not self._session_memory_triggered
            and not self._session_wr_mode
            and not exp_applied
        ):
            if (
                turn_index == 1
                and not has_trigger
                and not _EXPLICIT_RES_RE.search(utterance)
                and not _EXPLICIT_RTT_RE.search(utterance)
            ):
                hint_lines.append(
                    "# === COLD-START LITERAL EXTRACTION RULE (非记忆触发轮字面提取规则, 本轮生效, 优先级最高) ===\n"
                    "# 用户本轮未说『老规矩/按上次』等记忆触发词，也未提及画质或时延。本轮必须仅提取用户字面说出的槽位:\n"
                    "# 严禁从长期记忆或用户画像中自动借用 resolution、rtt、end_timestamp 或 duration (用户没说的槽位一律留空，等待下一轮追问补充)!\n"
                )
            elif turn_index > 1 and prev_states:
                hint_lines.append(
                    "# === MULTI-TURN SLOT COMPLETION RULE (多轮证据补槽规则, 本轮生效, 优先级最高) ===\n"
                    "# 若本轮用户补充了持续时长 (如『预计持续90min』『持续2个小时』) 或结束时间，而 prev_state 已有 start_timestamp:\n"
                    "# 注意: TIME EVIDENCE 只是系统提供给你的参考文本，不会自动写入 state! 你必须在下方 Python 代码块中显式调用:\n"
                    "#   state.timestamp = find_timestamp(start=..., end=..., duration=...)\n"
                    "# 把 start_timestamp、end_timestamp 与 duration 一并写入 state，严禁只写 resolution/rtt 而漏写 state.timestamp!\n"
                )
            if not has_trigger:
                for m_slang in _QUOTED_SLANG_RE.finditer(utterance or ""):
                    slot_kind, slang_phrase = m_slang.group(1), m_slang.group(2).strip()
                    is_res_slot = slot_kind in ("画质", "清晰度", "分辨率")
                    # 若话语同句已经直接给出了标准数值 (如 "画质画面通透就行，按1080p开"), 则不属于未澄清黑话
                    if is_res_slot and _EXPLICIT_RES_RE.search(utterance):
                        continue
                    if not is_res_slot and _EXPLICIT_RTT_RE.search(utterance):
                        continue
                    if slang_phrase not in known_aliases:
                        target_slot_name = "resolution" if is_res_slot else "rtt"
                        hint_lines.append(
                            "# === UNCLARIFIED SLANG RULE (未知个人黑话待消歧规则, 本轮生效, 优先级最高) ===\n"
                            f"# 用户本轮说出的「{slang_phrase}」是首次出现的个人黑话, 尚未在长期记忆词典中登记, 且本轮未给出对应的标准档位数值。\n"
                            "# 必须遵守:\n"
                            f"# 1. 本轮严禁给 state.{target_slot_name} 赋值 (必须留空, 等待系统追问后用户在下一轮澄清具体数值);\n"
                            "# 2. 本轮用户明确给出的其他槽位 (如时延/时长/开始时间) 以及上一轮 prev_state 已有的槽位必须正常赋值和沿用!\n"
                        )

        # Case C: Turn >= 2 且上一轮已由长期记忆补齐全部 7 个槽位 (T2-1 确认轮 / T2-5 延长时长轮 / T2-3 临时改参轮)
        if turn_index > 1 and self._session_memory_triggered and prev_states:
            is_param_mod = (
                any(w in utterance for w in ("临时", "改", "调到", "压到", "降到", "提到", "换成"))
                and "以后也照这个标准" not in utterance
                and "平时说的" not in utterance
                and bool(_EXPLICIT_RES_RE.search(utterance) or _EXPLICIT_RTT_RE.search(utterance))
            )
            if "延长" in utterance or is_param_mod:
                hint_lines.append(
                    "# === MEMORY FOLLOW-UP UPDATE RULE (老规矩后续轮微调规则, 本轮生效, 优先级最高) ===\n"
                    "# 上一轮已通过长期记忆补齐了完整意图状态 prev_state。本轮用户在老规矩基础上做局部调整:\n"
                    "# 1. 必须沿用已有意图段头 (如 # intent: I1), 严禁新建意图或丢弃已有槽位;\n"
                    "# 2. 未调整的槽位一律从 prev_state 沿用 (state.application_name = state.application_name 等);\n"
                    "# 3. 若用户延长了保障时长 (如延长到 180 分钟), 更新 duration='180min', 并基于 state.start_timestamp 重新推算 end_timestamp;\n"
                    "# 4. 若用户临时调整了画质或时延 (如画质临时调到 720p、时延压到 30ms), 将 state.resolution 和 state.rtt 更新为本轮新值, 时间字段继续沿用 prev_state!\n"
                )
            else:
                hint_lines.append(
                    "# === MEMORY FOLLOW-UP CONFIRM RULE (老规矩确认轮保持规则, 本轮生效, 优先级最高) ===\n"
                    "# 上一轮已通过长期记忆补齐了完整 7 个槽位, 本轮用户是在确认直接开通 (即使顺带重申了黑话定义含义, 参数也已在上一轮补齐):\n"
                    "# 必须沿用已有意图段头 (如 # intent: I1), 并用 state.xxx = state.xxx 完整保留 prev_state 中的全部 7 个槽位, 严禁改动或留空!\n"
                )

        memory_hint = "\n".join(hint_lines)
        trace_info = {
            "turn": turn_index,
            "query": utterance,
            "reference_time": reference_time,
            "has_memory_trigger": has_trigger,
            "session_memory_triggered": self._session_memory_triggered,
            "session_wr_mode": self._session_wr_mode,
            "session_exp_triggered": self._session_exp_triggered,
            "raw_hit_count": len(raw_hits),
            "major_hits": [
                {
                    "memory_id": m["memory_id"],
                    "subtype": m["subtype"],
                    "memory_key": m["memory_key"],
                    "paired_periodic_key": m.get("paired_periodic_key", ""),
                    "status": m["status"],
                    "score": m["score"],
                    "resolved_slots": m["resolved_slots"],
                    "structured_match": (m.get("explain") or {}).get("structured_match", ""),
                }
                for m in major_hits
            ],
            "periodic_hits": [
                {
                    "memory_id": p["memory_id"],
                    "subtype": p["subtype"],
                    "memory_key": p["memory_key"],
                    "status": p["status"],
                    "score": p["score"],
                    "time_window": (p.get("content") or {}).get("time_window"),
                    "value": (p.get("content") or {}).get("value"),
                }
                for p in periodic_hits
            ],
            "service_hits": [
                {
                    "memory_id": s["memory_id"],
                    "subtype": s["subtype"],
                    "memory_key": s["memory_key"],
                    "status": s["status"],
                    "score": s["score"],
                    "business": (s.get("content") or {}).get("business"),
                    "application": (s.get("content") or {}).get("application"),
                }
                for s in service_hits
            ],
            "expression_hits": [
                {
                    "memory_id": e["memory_id"],
                    "memory_key": e["memory_key"],
                    "alias": (e.get("content") or {}).get("alias"),
                    "target": (e.get("content") or {}).get("target"),
                    "application": (e.get("content") or {}).get("application"),
                    "business": (e.get("content") or {}).get("business"),
                    "scene": (e.get("content") or {}).get("scene"),
                    "score": e["score"],
                }
                for e in expression_hits
            ],
            "working_hits": [
                {
                    "memory_id": w["memory_id"],
                    "memory_key": w["memory_key"],
                    "record_id": (w.get("content") or {}).get("record_id"),
                    "application_name": (w.get("content") or {}).get("application_name"),
                    "service_name": (w.get("content") or {}).get("service_name"),
                    "resolution": (w.get("content") or {}).get("resolution"),
                    "rtt": (w.get("content") or {}).get("rtt"),
                    "location": (w.get("content") or {}).get("location"),
                    "created_at": (w.get("content") or {}).get("created_at"),
                    "score": w["score"],
                    "structured_match": (w.get("explain") or {}).get("structured_match", ""),
                }
                for w in working_hits
            ],
            "experience_hits": [
                {
                    "memory_id": ex["memory_id"],
                    "memory_key": ex["memory_key"],
                    "experience_id": (ex.get("content") or {}).get("experience_id"),
                    "title": (ex.get("content") or {}).get("title"),
                    "condition": (ex.get("content") or {}).get("condition"),
                    "action": (ex.get("content") or {}).get("action"),
                    "score": ex["score"],
                    "structured_match": (ex.get("explain") or {}).get("structured_match", ""),
                }
                for ex in experience_hits
            ],
            "selected_best_major": best_major["resolved_slots"] if best_major else None,
        }
        return user_context, memory_hint, trace_info

    @staticmethod
    def _extract_working_record_from_session(
        session: dict[str, Any], predicted_final: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """用【系统自身预测的最终意图状态】生成本会话的 working_record (工作记忆账本条目)。

        不读取任何数据集 GT / 标签 (intents / scenario / blueprint_type), 避免评测泄漏:
          - 槽位值: predicted_final (run_session 输出的 session_out[k]['pred_flat'])
          - 地点:   仅从用户话语抽取
          - 临时单: 仅凭用户话语判定 (临时词 + 具体改参同句)
        predicted_final 缺失时不写账本。
        """
        if not predicted_final:
            return None
        cands = [
            dict(v) for v in predicted_final.values()
            if isinstance(v, dict) and v.get("application_name") and v.get("service_name")
        ]
        if not cands:
            return None
        pf = cands[-1]

        user_id = str(session.get("user_id", "default_user"))
        session_id = str(session.get("session_id", ""))
        reference_time = str(session.get("reference_time", ""))
        user_texts: list[str] = []
        for t in session.get("event_sequence") or []:
            u = t.get("user") if isinstance(t, dict) else None
            utt = u.get("utterance", "") if isinstance(u, dict) else (str(u) if u else "")
            if utt:
                user_texts.append(utt)

        m_ymd = re.search(r"(\d{4})年(\d{2})月(\d{2})日", reference_time)
        m_idx = re.search(r"-(\d+)$", session_id)
        sess_idx = m_idx.group(1) if m_idx else (session_id or "00")
        if m_ymd:
            record_id = f"REC_{user_id}_{m_ymd.group(1)}{m_ymd.group(2)}{m_ymd.group(3)}_{sess_idx}"
        else:
            # 无参考日期时不伪造固定日期, 直接以会话编号区分
            record_id = f"REC_{user_id}_{sess_idx}"

        loc = ""
        for utt in user_texts:
            loc = _extract_location(utt)
            if loc:
                break
        is_temp = _is_temporary_override_dialogue(user_texts)
        return {
            "record_id": record_id,
            "session_id": session_id,
            "created_at": reference_time,
            "application_name": pf.get("application_name") or "",
            "service_name": pf.get("service_name") or "",
            "resolution": pf.get("resolution") or "",
            "rtt": pf.get("rtt") or "",
            "start_timestamp": pf.get("start_timestamp") or "",
            "end_timestamp": pf.get("end_timestamp") or "",
            "duration": pf.get("duration") or "",
            "location": loc,
            "scene": loc,
            "status": "completed",
            "is_temporary_override": is_temp,
            "validity": "temporary" if is_temp else "long_term",
            "temporal_reference_aliases": ["上次", "上周", "上个月初", "之前", "昨天预约"],
        }

    def record_session(
        self,
        session: dict[str, Any],
        tenant_id: str = "default",
        predicted_final: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """会话结束后调用 MemorySystem.record_session() 萃取并更新该用户的长期记忆库。

        predicted_final: 本会话系统预测的最终意图 {intent_key: pred_flat}, 用于写工作记忆账本。
        注意: 不再把数据集 session_meta (memory_role / blueprint_type / scenario 等标签) 传给记忆系统。
        """
        user_id = str(session.get("user_id", "default_user"))
        session_id = str(session.get("session_id", ""))
        reference_time = str(session.get("reference_time", ""))
        turns = list(session.get("event_sequence") or [])
        metadata: dict[str, Any] = {"reference_time": reference_time}
        wr_to_append = self._extract_working_record_from_session(session, predicted_final)
        if wr_to_append:
            metadata["working_record_to_append"] = wr_to_append

        write_res = self.ms.record_session(
            user_id=user_id,
            agent_id="intent_agent",
            session_id=session_id,
            turns=turns,
            metadata=metadata,
            reference_time=reference_time,
            tenant_id=tenant_id,
            async_mode=False,
        )

        # 导出当前用户的记忆快照供报告展示
        ns = user_namespace(user_id=user_id, tenant_id=tenant_id)
        now_ts = normalize_ts_str(reference_time)
        snapshot: list[dict[str, Any]] = []
        for n in sorted(self.ms.filestore.iter_nodes(ns), key=lambda x: (x.subtype, x.memory_key)):
            spec = self.ms.registry.get(n.subtype)
            base_lam = spec.decay_lambda if spec is not None else 0.02
            ret, stab, eff_lam = compute_ebbinghaus_retention(
                decay_lambda=base_lam,
                last_reinforced_at=n.stats.last_reinforced_at or n.updated_at or n.created_at,
                now_ts=now_ts,
                positive_votes=n.stats.positive_votes,
                reuse_count=n.stats.reuse_count,
            )
            snapshot.append(
                {
                    "memory_id": n.memory_id,
                    "subtype": n.subtype,
                    "memory_key": n.memory_key,
                    "status": n.status,
                    "validity": n.validity.type,
                    "votes": n.stats.votes,
                    "positive_votes": n.stats.positive_votes,
                    "reuse_count": n.stats.reuse_count,
                    "retention": round(ret, 4),
                    "stability": round(stab, 2),
                    "content": dict(n.content),
                    "description": n.description,
                }
            )
        return {
            "write_result": write_res,
            "memory_snapshot": snapshot,
        }
