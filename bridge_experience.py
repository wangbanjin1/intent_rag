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
from prompts.memory_hint_templates import (
    EXPERIENCE_DEFAULT_TAIL,
    EXPERIENCE_FOLLOWUP_KEEP_RULE,
    format_experience_app_service_rule,
    format_experience_formal_duration_rule,
    format_experience_holiday_shift_rule,
    format_experience_rule_header,
    format_experience_start_offset_rule,
)


def _exp_card(eid: str, recalled: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """合并内置经验模板与用户库中召回的经验卡片 (卡片字段优先)。"""
    card = dict(_BUILTIN_EXPERIENCE_TEMPLATES.get(eid) or {})
    card.update(recalled.get(eid) or {})
    return card


def _format_fastpath_experience_rule(
    head: str,
    adj: dict[str, Any],
    utterance: str,
    reference_time: str,
) -> str | None:
    """渲染无需检索多笔工作记忆账本的直接槽位修正类经验规则（载体替换 / 节假日顺延 / 提前预热偏移）。"""
    if adj.get("application_name") or adj.get("service_name"):
        target_app = adj.get("application_name") or _mentioned_app(utterance)
        target_svc = adj.get("service_name")
        lines = []
        if target_app:
            lines.append(f"state.application_name = {target_app!r}")
        if target_svc:
            lines.append(f"state.service_name = {target_svc!r}")
        st_dt, et_dt = _explicit_window(utterance, reference_time)
        if st_dt is not None and et_dt is not None:
            dur_min = int((et_dt - st_dt).total_seconds() // 60)
            return format_experience_app_service_rule(
                head, lines, st_dt.strftime(DT_FMT), et_dt.strftime(DT_FMT), dur_min
            )
        return format_experience_app_service_rule(head, lines)

    if adj.get("shift_to") == "next_statutory_workday":
        year = _ref_year(reference_time)
        hdates = find_holiday_dates(utterance, year) if year else []
        if hdates:
            h = hdates[0]
            nd = next_cn_workday_after(h)
            if nd is not None:
                return format_experience_holiday_shift_rule(
                    head,
                    h.strftime("%Y年%m月%d日"),
                    holiday_name(h) or "法定节假日",
                    nd.strftime("%Y年%m月%d日"),
                )
        return format_experience_holiday_shift_rule(head)

    if adj.get("start_offset_minutes") is not None:
        off = int(adj.get("start_offset_minutes") or 0)
        add = int(adj.get("duration_add_minutes") or -off)
        st_dt, et_dt = _explicit_window(utterance, reference_time)
        if st_dt is not None and et_dt is not None:
            new_st = st_dt + timedelta(minutes=off)
            new_dur = int((et_dt - new_st).total_seconds() // 60)
            return format_experience_start_offset_rule(
                head,
                off,
                add,
                new_st.strftime(DT_FMT),
                st_dt.strftime("%H:%M"),
                et_dt.strftime(DT_FMT),
                new_dur,
            )
        return format_experience_start_offset_rule(head, off, add)

    return None


def _format_slowpath_clarification_rule(
    head: str,
    adj: dict[str, Any],
    utterance: str,
    reference_time: str,
    all_working_records: list[dict[str, Any]],
) -> str | None:
    """渲染需要结合历史工单账本过滤临时短单并消歧正式工单时长的经验规则。"""
    if not adj.get("prefer_formal_duration_over_recent_temp"):
        return None
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
        rec_id = str((rec or {}).get("record_id", "formal"))
        return format_experience_formal_duration_rule(
            head, st_dt.strftime(DT_FMT), et_dt.strftime(DT_FMT), target_dur, src_note, rec_id
        )
    return format_experience_formal_duration_rule(head)


def render_experience_rule(
    card: dict[str, Any],
    utterance: str,
    reference_time: str,
    all_working_records: list[dict[str, Any]],
) -> str:
    """按经验卡片的 recommended_adjustments 动态渲染 EXPERIENCE RULE。"""
    adj = card.get("recommended_adjustments") or {}
    cond_txt = str(card.get("condition") or card.get("title") or "")
    act_txt = str(card.get("action") or "")
    head = format_experience_rule_header(str(card.get("experience_id", "")), cond_txt, act_txt)

    fast_rule = _format_fastpath_experience_rule(head, adj, utterance, reference_time)
    if fast_rule is not None:
        return fast_rule

    slow_rule = _format_slowpath_clarification_rule(
        head, adj, utterance, reference_time, all_working_records
    )
    if slow_rule is not None:
        return slow_rule

    return head + EXPERIENCE_DEFAULT_TAIL


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
        hint_lines.append(EXPERIENCE_FOLLOWUP_KEEP_RULE)

    return session_exp_triggered, hint_lines
