"""Rule Validator — 嵌套 params 适配。

LLM 输出 {intent, params(嵌套=Δy), validity, invalid_reason}。
流程:
  1. has_unresolved_coref_nested(Δy嵌套, prev嵌套) → ref 不可解析则 unresolved_coreference
  2. apply_nested(prev嵌套, Δy嵌套) → final_state 嵌套(含 coreference 解析)
  3. flatten_params(final_state) → 扁平校验(矩阵/槽位/时间)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from case_format import (
    flatten_params, apply_nested, has_unresolved_coref_nested,
)
from schema import (
    APPLICATION_SERVICE_MATRIX,
    KNOWN_APPLICATIONS,
    REQUIRED_SLOTS,
    RESOLUTION_RANK,
)

DT_FMT = "%Y年%m月%d日%H时%M分"


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime(DT_FMT)


def _complete_timestamp(
    final_nested: dict[str, Any],
    delta_nested: dict[str, Any] | None = None,
) -> None:
    """时间三字段互补全(半规则后处理, 不破坏 text-to-python 范式, 属 validator 职责)。
    知二推一: start+end→duration, start+duration→end, end+duration→start。
    直接在 final_nested["timestamp"] 上补全(若该子对象存在)。
    """
    ts = final_nested.get("timestamp")
    if not isinstance(ts, dict):
        return
    def _get(slot):
        v = ts.get(slot)
        if isinstance(v, dict):
            return v.get("value")
        return v
    def _set(slot, val):
        cur = ts.get(slot)
        if isinstance(cur, dict):
            cur = dict(cur)
            cur["value"] = val
            cur.setdefault("source_type", "Context")
            cur.setdefault("source_ref", "derived")
            ts[slot] = cur
        else:
            ts[slot] = {"value": val, "source_type": "Context", "source_ref": "derived"}

    start = _get("start_timestamp")
    end = _get("end_timestamp")
    dur = _get("duration")
    start_dt = parse_dt(start) if start else None
    end_dt = parse_dt(end) if end else None
    dur_min = parse_duration_minutes(dur) if dur else None

    # Check if delta updated duration without providing a new literal end_timestamp (e.g. T2-5 duration extension)
    delta_ts = (delta_nested or {}).get("timestamp") if isinstance(delta_nested, dict) else None
    if isinstance(delta_ts, dict) and "duration" in delta_ts and start_dt and dur_min:
        d_end = delta_ts.get("end_timestamp")
        d_end_val = d_end.get("value") if isinstance(d_end, dict) else d_end
        end_is_new_literal = (
            d_end_val is not None
            and not (isinstance(d_end_val, dict) and "ref" in d_end_val)
            and not (isinstance(d_end_val, tuple) and len(d_end_val) == 2 and d_end_val[0] == "ref")
        )
        if not end_is_new_literal:
            _set("end_timestamp", _fmt_dt(start_dt + timedelta(minutes=dur_min)))
            return

    # start + end -> duration
    if start_dt and end_dt and not dur:
        actual = int((end_dt - start_dt).total_seconds() // 60)
        if actual >= 0:
            _set("duration", f"{actual}min")
    # start + duration -> end
    elif start_dt and dur_min and not end:
        _set("end_timestamp", _fmt_dt(start_dt + timedelta(minutes=dur_min)))
    # end + duration -> start
    elif end_dt and dur_min and not start:
        _set("start_timestamp", _fmt_dt(end_dt - timedelta(minutes=dur_min)))


def parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, DT_FMT)
    except (ValueError, TypeError):
        return None


def parse_duration_minutes(s: str | None) -> int | None:
    if not s or not isinstance(s, str):
        return None
    try:
        if s.endswith("min"):
            return int(s[:-3])
        if s.endswith("h"):
            return int(s[:-1]) * 60
    except ValueError:
        return None
    return None


def validate(
    llm_output: dict[str, Any],
    reference_time: str,
    prev_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """apply Δy(嵌套) 到 prev_state(嵌套), 校验, 返回带 final_state(嵌套)的结果。"""
    result = dict(llm_output)
    delta_nested = result.get("params", {}) or {}
    prev_nested = prev_state or {}

    # 1) coreference 可解析性
    if has_unresolved_coref_nested(delta_nested, prev_nested):
        result["validity"] = "invalid"
        result["invalid_reason"] = "unresolved_coreference"
        result["final_state"] = apply_nested(prev_nested, delta_nested)
        return result

    # 2) apply (嵌套, 解析 coreference)
    final_nested = apply_nested(prev_nested, delta_nested)
    # 2.5) 时间三字段互补全 (知二推一, 半规则后处理)
    _complete_timestamp(final_nested, delta_nested)
    result["final_state"] = final_nested

    # 3) 校验 (扁平)
    final_flat = flatten_params(final_nested)
    reason = _check(final_flat, reference_time)
    if reason is None:
        result["validity"] = "valid"
        result["invalid_reason"] = "none"
    else:
        result["validity"] = "invalid"
        result["invalid_reason"] = reason
    result["signal_level"] = signal_level(result["invalid_reason"])
    return result


# v25: 信号分层 (report_reflection.md §7.5 P3)。
# invalid 是常态而非错误信号: missing_required_slot 在 Collecting 中间轮/域外意图上
# 是正常未收齐 (soft); 只有确定性矛盾才是硬错误信号 (hard)。
HARD_REASONS = frozenset({
    "time_inconsistent", "time_out_of_range", "slot_value_invalid",
    "matrix_out_application", "matrix_out_service", "unresolved_coreference",
})
SOFT_REASONS = frozenset({"missing_required_slot"})


def signal_level(invalid_reason: str) -> str:
    """invalid_reason -> hard(确定性矛盾) / soft(正常未收齐) / none。"""
    if invalid_reason in ("none", "", None):
        return "none"
    if invalid_reason in HARD_REASONS:
        return "hard"
    return "soft"


def _check(state: dict[str, Any], reference_time: str) -> str | None:
    for slot in REQUIRED_SLOTS:
        v = state.get(slot)
        if v is None or (isinstance(v, str) and not v.strip()):
            return "missing_required_slot"

    app = state.get("application_name")
    svc = state.get("service_name")
    if app and app not in KNOWN_APPLICATIONS:
        return "matrix_out_application"
    if app and svc:
        if app not in APPLICATION_SERVICE_MATRIX or svc not in APPLICATION_SERVICE_MATRIX[app]:
            return "matrix_out_service"

    res = state.get("resolution")
    if res is not None and res not in RESOLUTION_RANK:
        return "slot_value_invalid"

    rtt = state.get("rtt")
    if rtt is not None and not re.fullmatch(r"\d+ms", str(rtt)):
        return "slot_value_invalid"
    bw = state.get("bandwidth")
    if bw is not None and not re.fullmatch(r"\d+(Mbps|Gbps)", str(bw)):
        return "slot_value_invalid"

    start = state.get("start_timestamp")
    end = state.get("end_timestamp")
    dur = state.get("duration")
    ref = parse_dt(reference_time)
    start_dt = parse_dt(start)
    end_dt = parse_dt(end)
    dur_min = parse_duration_minutes(dur)

    if ref is not None and start_dt is not None:
        if start_dt < ref:
            return "time_out_of_range"
        if start_dt > ref + timedelta(days=30):
            return "time_out_of_range"

    if start_dt and end_dt:
        if end_dt <= start_dt:
            return "time_inconsistent"
        if dur_min is not None:
            actual = int((end_dt - start_dt).total_seconds() // 60)
            if abs(actual - dur_min) > 1:
                return "time_inconsistent"
    return None
