"""bridge_experience.py — 经验记忆（Experiential Memory）卡片合并、触发匹配与规则渲染。

职责:
  1. 合并内置经验模板与用户已结晶的经验卡片 (_exp_card)
  2. 根据经验卡片的 recommended_adjustments 动态渲染 EXPERIENCE RULE (render_experience_rule)
  3. 评估单轮经验记忆触发状态与多轮确认锁定规则 (evaluate_experience_tier)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

MEMORY_SYSTEM_SRC = Path(r"D:\coding\memory_system\src")
if str(MEMORY_SYSTEM_SRC) not in sys.path:
    sys.path.insert(0, str(MEMORY_SYSTEM_SRC))

from memory_system.api import _BUILTIN_EXPERIENCE_TEMPLATES
from memory_system.core.experience_rules import (
    find_holiday_dates,
    holiday_name,
    next_cn_workday_after,
    trigger_matches,
)

import time_extractor as _tex
from bridge_factual import (
    DT_FMT,
    _explicit_start_dt,
    _explicit_window,
    _mentioned_app,
    _parse_minutes,
    _ref_year,
)
from bridge_working import _is_temp_record, _rec_sort_key


def _exp_card(eid: str, recalled: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """合并内置经验模板与用户库中召回的经验卡片 (卡片字段优先)。"""
    card = dict(_BUILTIN_EXPERIENCE_TEMPLATES.get(eid) or {})
    card.update(recalled.get(eid) or {})
    return card


def render_experience_rule(
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


def evaluate_experience_tier(
    turn_index: int,
    utterance: str,
    reference_time: str,
    prev_states: dict[str, dict[str, Any]],
    experience_hits: list[dict[str, Any]],
    active_experiences: dict[str, dict[str, Any]],
    all_working_records: list[dict[str, Any]],
    session_exp_triggered: str | None,
) -> tuple[str | None, list[str]]:
    """评估 Tier 3 经验记忆规则触发与后续轮状态锁定规则。"""
    hint_lines: list[str] = []
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
        ref_year = _ref_year(reference_time)
        matched_eid: str | None = None
        matched_card: dict[str, Any] = {}
        for eid in _BUILTIN_EXPERIENCE_TEMPLATES:
            card = _exp_card(eid, recalled_exp_map)
            if trigger_matches(card, "bridge_trigger", utterance, "", ref_year):
                matched_eid, matched_card = eid, card
                break
        if matched_eid and matched_card.get("bridge_requires_active") and matched_eid not in active_experiences:
            session_exp_triggered = f"{matched_eid}:LEARNING"
        elif matched_eid:
            session_exp_triggered = matched_eid
            rule_txt = render_experience_rule(
                matched_card, utterance, reference_time, all_working_records
            )
            if rule_txt:
                hint_lines.append(rule_txt)
    elif turn_index > 1 and session_exp_triggered and prev_states:
        hint_lines.append(
            "# === EXPERIENCE FOLLOW-UP KEEP RULE (经验记忆归纳/确认轮状态锁定规则, 本轮生效, 优先级最高) ===\n"
            "# 本轮属于经验记忆归纳学习或确认轮：即使本轮用户口头提及“把开始时间往前提前半小时”、“平时对词我们都是打微信视频电话”或重申经验做法（该语句仅用于向经验库结晶未来规则 EXP_*），\n"
            "# 当前会话在上一轮已落盘锁定的全部 7 个目标槽位（application_name, service_name, resolution, rtt, start_timestamp, end_timestamp, duration）必须严格保持不变！\n"
            "# 必须遵守:\n"
            "# 1. 严禁在本轮调用 find_timestamp(...) 或修改 start_timestamp / end_timestamp（忽略 TIME EVIDENCE 的偏移）;\n"
            "# 2. 严禁修改 state.resolution 或 state.rtt（严禁从【用户习惯画像】或长期记忆中拉取其他参数如 100ms 覆盖 prev_state 已有的 resolution 和 rtt）;\n"
            "# 3. 必须输出 `# 身份判定: 更新已有意图 I1` 与 `# intent: I1`，并用 `state.xxx = state.xxx` 完整保留 prev_state 中的全部 7 个槽位!\n"
        )

    return session_exp_triggered, hint_lines
