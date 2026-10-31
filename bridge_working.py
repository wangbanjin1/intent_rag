"""bridge_working.py — 工作记忆（Working Memory）工单选型、规则渲染与会话后工单抽取。

职责:
  1. 数据驱动地挑选目标工作记忆工单 (_pick_working_record)
  2. 渲染工作记忆三模式规则 (reschedule / disambig / reuse 及其多轮确认规则)
  3. 会话结束后基于系统预测状态抽取工作记忆工单 (extract_working_record_from_session)
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

from memory_system.core.working_rules import (
    classify_working_record_reference,
    has_explicit_time,
    reschedule_shift_minutes,
)
from memory_system.pipeline.extractor import _TEMP_VETO_WORDS

import time_extractor as _tex
from bridge_factual import DT_FMT, _explicit_window

_LOC_RE = re.compile(
    r"(?:在|到)([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9]{1,23}?)"
    r"(?=这边|这儿|那边|那儿|这里|，|,|。|；|！|\s|办|开|用|做|搞|弄|准备|帮我|给|跟|要|有|了|拍|带队|打算|想)"
)
_LOC_PRONOUNS = {"这儿", "这边", "那边", "那儿", "这里", "那里", "线上", "路上", "现场"}
_PARAM_VALUE_RE = re.compile(r"\d+\s*[pP]\b|\d+\s*(?:ms|毫秒)|[24][kK]|调到|压到|改成|改为|换成|降到|提到")


def _is_temp_record(rec: dict[str, Any]) -> bool:
    """判断工单是否为单次临时覆写记录。"""
    return bool(rec.get("is_temporary_override")) or rec.get("validity") == "temporary"


def _rec_sort_key(rec: dict[str, Any]) -> str:
    """工单时间/编号排序键。"""
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
    loc_hits = [
        c for c in pool
        if str(c.get("location") or "").strip() and str(c["location"]).strip() in (utterance or "")
    ]
    pool = loc_hits or pool
    app_hits = [
        c for c in pool
        if c.get("application_name") and str(c["application_name"]) in (utterance or "")
    ]
    pool = app_hits or pool
    if prefer_latest:
        return dict(max(pool, key=_rec_sort_key))
    return dict(pool[0])


def _extract_location(text: str) -> str:
    """仅从用户话语中抽取地点 ("今天在X办…" / "到X了"), 不读取数据集 scenario 元数据。"""
    for m in _LOC_RE.finditer(text or ""):
        cand = m.group(1)
        if cand in _LOC_PRONOUNS or re.search(r"\d|ms|以内|以下|时|分钟|小时|之前|上次|上周", cand):
            continue
        return cand
    return ""


def _is_temporary_override_dialogue(user_texts: list[str]) -> bool:
    """仅凭用户话语判断本次是否为单次临时改参: 同一子句内同时出现临时否决词与具体参数改动。"""
    for txt in user_texts:
        for clause in re.split(r"[，,。；;！!？?\n]", txt or ""):
            if any(w in clause for w in _TEMP_VETO_WORDS) and _PARAM_VALUE_RE.search(clause):
                return True
    return False


def select_target_working_record(
    working_hits: list[dict[str, Any]],
    all_working_records: list[dict[str, Any]],
    best_major: dict[str, Any] | None,
    utterance: str,
) -> dict[str, Any] | None:
    """从检索命中的 working_hits、全量工作记忆或最佳事实记忆中确定候选目标工单。"""
    if working_hits:
        return _pick_working_record(
            [dict(wh.get("content") or {}) for wh in working_hits], utterance, prefer_latest=False
        )
    if all_working_records:
        return _pick_working_record(all_working_records, utterance, prefer_latest=False)
    if best_major is not None:
        rs = best_major["resolved_slots"]
        return {
            "record_id": best_major["memory_key"],
            "application_name": rs["application_name"],
            "service_name": rs["service_name"],
            "resolution": rs["resolution"],
            "rtt": rs["rtt"],
            "start_timestamp": rs["start_timestamp"],
            "end_timestamp": rs["end_timestamp"],
            "duration": rs["duration"],
        }
    return None


def render_working_memory_rules(
    turn_index: int,
    utterance: str,
    reference_time: str,
    prev_states: dict[str, dict[str, Any]],
    exp_applied: bool,
    target_wr: dict[str, Any] | None,
    all_working_records: list[dict[str, Any]],
    session_wr_mode: str | None,
    session_wr_target: dict[str, Any] | None,
    session_turn1_utterance: str,
) -> tuple[str | None, dict[str, Any] | None, list[str]]:
    """根据当前轮次和工单引用类型渲染 Tier 2 工作记忆规则。"""
    hint_lines: list[str] = []
    wr_act = classify_working_record_reference(utterance) if turn_index == 1 else None

    if turn_index == 1 and not exp_applied and target_wr is not None:
        if wr_act == "reschedule":
            ref_dt = _tex.parse_reference_time(reference_time)
            pool = [r for r in all_working_records if not _is_temp_record(r)]
            picked = _pick_working_record(pool, utterance, prefer_latest=False)
            if picked:
                target_wr = picked
            session_wr_mode = "reschedule"
            session_wr_target = target_wr
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
        elif wr_act == "disambig":
            session_wr_mode = "disambig"
            session_wr_target = target_wr
            hint_lines.append(
                "# === WORKING RECORD DISAMBIGUATION RULE (多笔历史工单待消歧规则, 本轮生效, 优先级最高) ===\n"
                "# 用户引用了历史意图保障单规格并给出了今日时间，但历史账本中存在多笔工单，本轮需向用户确认按哪一笔的规格。\n"
                "# 必须遵守:\n"
                "# 1. 本轮只填 application_name, service_name 以及用户给出的今日时间 (start_timestamp, end_timestamp, duration);\n"
                "# 2. 本轮严禁给 state.resolution 和 state.rtt 赋值 (必须留空，即使话语末尾带有『抓紧别掉链子』等口头催促词也不要查黑话词典填 rtt，必须等下一轮用户选定按哪一笔历史工单后再补齐画质与时延)!\n"
            )
        elif wr_act == "reuse":
            session_wr_mode = "reuse"
            session_wr_target = target_wr
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

    elif turn_index > 1 and session_wr_mode and prev_states:
        twr = session_wr_target or target_wr or {}
        if session_wr_mode == "reschedule":
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
        elif session_wr_mode == "disambig":
            if turn_index == 2:
                latest = _pick_working_record(
                    all_working_records, session_turn1_utterance or utterance, prefer_latest=True
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
                    "# 上一轮已补齐全部 7 个槽位，本轮用户确认开通（即使附带『画面要透亮就行』『顺滑』等口头语，也严禁修改 resolution 或 rtt）:\n"
                    "# 必须沿用已有意图段头 (如 # intent: I1), 并用 state.xxx = state.xxx 完整保留 prev_state 中的全部 7 个槽位!\n"
                )
        elif session_wr_mode == "reuse":
            u_st, _ = _explicit_window(utterance, reference_time)
            ref_dt = _tex.parse_reference_time(reference_time)
            rec_st = rec_et = None
            if u_st is None and ref_dt is not None and twr.get("start_timestamp"):
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

    return session_wr_mode, session_wr_target, hint_lines


def extract_working_record_from_session(
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
