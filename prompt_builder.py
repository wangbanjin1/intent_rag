"""Schema-aware Text-to-Python Prompt Builder — RefPyDST §3.1 + S3-DST PAR。

输出范式: python_par (Text-to-Python, 每个 intent 段前强制 `# summary:`, PAR 落地)。
静态提示词模板、画质映射表与规则文本段拆分于 `prompt_templates.py`。
"""
from __future__ import annotations

import json
import re
from typing import Any

from prompt_templates import (
    _PROFILE_TIME_LINE_COMPACT,
    _PROFILE_TIME_LINE_VERBOSE,
    _QUALITY_SECTION_FULL_D5,
    _SHARED_RULES_HEAD,
    _SHARED_RULES_TAIL,
    _SHARED_RULES_TAIL_COMPACT,
    _SYSTEM_PROMPT_PY_MID,
    _TIME_RULES_COMPACT,
    _TIME_RULES_VERBOSE,
    SYSTEM_PROMPT_PY,
    SYSTEM_PROMPT_PY_PRE,
    SYSTEM_PROMPT_PY_TIME_ANCHOR,
    _compose_system_py,
    _shared_rules,
    format_correction_rule_block,
    format_disambiguation_rule_block,
    format_id_mode_task_block,
    format_key_mode_task_block,
    format_record_rule_block,
)
from python_state import schema_python_text
from retriever import RetrievedCase
from schema import APPLICATION_SERVICE_MATRIX

__all__ = [
    "SYSTEM_PROMPT_PY",
    "SYSTEM_PROMPT_PY_PRE",
    "SYSTEM_PROMPT_PY_TIME_ANCHOR",
    "_PROFILE_TIME_LINE_COMPACT",
    "_PROFILE_TIME_LINE_VERBOSE",
    "_QUALITY_SECTION_FULL_D5",
    "_RECORD_COREF_WORDS",
    "_SERVICE_WORDS_DEMO",
    "_SHARED_RULES_HEAD",
    "_SHARED_RULES_TAIL",
    "_SHARED_RULES_TAIL_COMPACT",
    "_SYSTEM_PROMPT_PY_MID",
    "_TIME_RULES_COMPACT",
    "_TIME_RULES_VERBOSE",
    "_active_app_hint",
    "_build_correction_chain",
    "_build_selection_chain",
    "_case_to_program",
    "_compose_system_py",
    "_dedupe_demos",
    "_format_case",
    "_format_prev",
    "_format_user_context",
    "_record_sourced_slots",
    "_schema_text",
    "_shared_rules",
    "_shared_task_sections",
    "build_prompt",
]


def _schema_text() -> str:
    return schema_python_text()


def _format_prev(prev: dict[str, Any]) -> str:
    """prev_state 用扁平标量 dict 展示(便于 state.xxx 引用)。"""
    from case_format import flatten_params
    return json.dumps(flatten_params(prev), ensure_ascii=False)


def _case_to_program(c: RetrievedCase, id_mode: bool = False) -> str:
    """把 case 的 Δy(嵌套 params) 反编译为 Python 程序(作为 demonstration 的 output)。"""
    from case_format import apply_nested, flatten_params
    flat = flatten_params(c.delta)
    lines = []
    ts_args = []
    for slot, val in flat.items():
        if slot in ("start_timestamp", "end_timestamp", "duration"):
            ts_args.append((slot, val))
            continue
        if isinstance(val, dict) and val.get("ref", "").startswith("state."):
            lines.append(f'state.{slot} = {val["ref"]}')
        else:
            lines.append(f"state.{slot} = {val!r}")
    if ts_args:
        parts = []
        for k, v in ts_args:
            if isinstance(v, dict) and v.get("ref", "").startswith("state."):
                parts.append(f'{k[0]}={v["ref"]}' if k != "duration" else f'duration={v["ref"]}')
            else:
                arg = "start" if k == "start_timestamp" else "end" if k == "end_timestamp" else "duration"
                parts.append(f"{arg}={v!r}")
        lines.append("state.timestamp = find_timestamp(" + ", ".join(parts) + ")")

    final_flat = flatten_params(apply_nested(c.prev_state or {}, c.delta or {}))
    app, svc = final_flat.get("application_name"), final_flat.get("service_name")
    app = app if isinstance(app, str) else None
    svc = svc if isinstance(svc, str) else None
    if id_mode:
        if not app:
            header = "# intent: none"
        elif c.prev_state:
            final_desc = "/".join(x for x in (app, svc) if x)
            header = f"# 身份判定: 更新已有意图 I1({final_desc})\n# intent: I1"
        else:
            header = "# 身份判定: 与已有意图无关, 新建\n# intent: new"
    else:
        header = f"# intent: {app}/{svc}" if app else "# intent: none"
    return header + "\n" + "\n".join(lines) if lines else header


def _record_sourced_slots(delta: dict[str, Any]) -> list[tuple[str, str, str]]:
    """从 delta(嵌套) 提取 record/Memory sourced 字段: [(slot, value, source_type)]。"""
    out: list[tuple[str, str, str]] = []
    if not isinstance(delta, dict):
        return out
    for slot, spec in delta.items():
        if not isinstance(spec, dict):
            continue
        if "value" in spec and isinstance(spec.get("value"), str):
            st = spec.get("source_type", "")
            if st in ("Record", "Memory"):
                out.append((slot, spec["value"], st))
        for k in ("min_value", "max_value"):
            inner = spec.get(k)
            if isinstance(inner, dict) and isinstance(inner.get("value"), str):
                st = inner.get("source_type", "")
                if st in ("Record", "Memory"):
                    out.append((slot, inner["value"], st))
    return out


_RECORD_COREF_WORDS = ("上次", "记录", "历史", "之前", "那条", "那次", "最近办过", "平时", "习惯")
_SERVICE_WORDS_DEMO = ("视频通话", "视频", "开直播", "看直播", "会议", "云游戏")


def _build_selection_chain(c: RetrievedCase) -> str:
    """为 record coref 类 case 生成选择链 summary (demonstration 用)。"""
    slots = _record_sourced_slots(c.delta or {})
    if not slots:
        return ""
    utt = c.utterance or ""
    parts: list[str] = []
    is_memory = any(w in utt for w in ("平时", "习惯"))
    src_label = "profile(平时习惯)" if is_memory else "record(上次记录)"
    conds: list[str] = []
    for w in _SERVICE_WORDS_DEMO:
        if w in utt:
            conds.append(f"service={w}")
            break
    if "号" in utt or ("日" in utt and any(m in utt for m in ("月",))):
        m = re.search(r"(\d{1,2}月\d{1,2}[号日])", utt)
        if m:
            conds.append(f"date={m.group(1)}")
    if "最近一条" in utt or "最近那次" in utt or "最近办过" in utt:
        conds.append("取最近")
    cond_str = ("按" + "+".join(conds) + "过滤") if conds else "无具体过滤条件取最近"
    field_parts = []
    for slot, val, st in slots:
        src = "profile" if st == "Memory" else "record"
        field_parts.append(f"{slot}={val}({src})")
    parts.append(f"用户话语含画像指代({src_label}), 取字段: {', '.join(field_parts)}")
    parts.append(f"选择链: {cond_str} → 取对应字段值")
    return "; ".join(parts) + " (PAR: 显式推理 record 选择, 防全空)"


def _build_correction_chain(c: RetrievedCase) -> str:
    """为改口/否定类 case 生成改口 summary (demonstration 用)。"""
    from case_format import flatten_params
    prev = flatten_params(c.prev_state or {})
    delta = flatten_params(c.delta or {})
    changes: list[str] = []
    for k, v in delta.items():
        vv = v.get("ref") if isinstance(v, dict) else v
        pv = prev.get(k)
        if isinstance(vv, str) and isinstance(pv, str) and pv and pv != vv:
            changes.append(f"{k}: {pv} → {vv}")
    if not changes:
        return ""
    return (
        "用户改口/否定: "
        + ", ".join(changes)
        + " (纠正之前轮旧值, 必须覆盖 prev_state, 不要沿用旧值)"
    )


def _format_case(c: RetrievedCase, idx: int, id_mode: bool = False) -> str:
    tag = "正例" if c.validity == "valid" else "负例"
    program = _case_to_program(c, id_mode=id_mode)
    reason = c.invalid_reason or "none"
    chain = _build_selection_chain(c) or _build_correction_chain(c)
    summary = chain if chain else "(示例, 省略)"
    if id_mode:
        if c.prev_state:
            prev_block = f"# 已有意图:\n#   I1 = {_format_prev(c.prev_state)}"
        else:
            prev_block = "# 已有意图: (无)"
        return (
            f"# === Demonstration {idx} ({tag}, score={c.score:.3f}, invalid_reason={reason}) ===\n"
            f"{prev_block}\n"
            f"# reference_time: {c.reference_time}\n"
            f"# user: {c.utterance}\n"
            f"# summary: {summary}\n"
            f"{program}\n"
        )
    return (
        f"# === Demonstration {idx} ({tag}, score={c.score:.3f}, invalid_reason={reason}) ===\n"
        f"prev_state = {_format_prev(c.prev_state)}\n"
        f"# reference_time: {c.reference_time}\n"
        f"# user: {c.utterance}\n"
        f"# summary: {summary}\n"
        f"{program}\n"
    )


def _dedupe_demos(demos: list[RetrievedCase]) -> list[RetrievedCase]:
    seen: set[frozenset[str]] = set()
    out: list[RetrievedCase] = []
    for c in demos:
        s = frozenset((c.delta or {}).keys())
        if s in seen:
            continue
        seen.add(s)
        out.append(c)
    return out


def _active_app_hint(utterance: str) -> str:
    """B3 活跃槽掩码: 检测 utterance 提到的 app, 只展示这些 app 的合法 service。"""
    if not utterance:
        return "(无)"
    hits = []
    for app in sorted(APPLICATION_SERVICE_MATRIX.keys(), key=len, reverse=True):
        if app in utterance:
            svcs = sorted(APPLICATION_SERVICE_MATRIX[app])
            hits.append(f"#   {app} -> 合法service: {svcs}")
    oos = ["豆瓣", "京东", "知乎", "美团", "网易云音乐"]
    for app in oos:
        if app in utterance:
            hits.append(f"#   {app} -> 域外app(不受理)")
    return "\n".join(hits) if hits else "#   (话语未明确提及矩阵内app, 需从语义判断)"


def _format_user_context(uc: dict[str, Any]) -> str:
    """用户画像 (B2 DCA: 意图驱动对话信息增强)。"""
    if not uc:
        return "(无画像)"
    lines = ["# 用户画像 profile:"]
    prof = uc.get("profile", {})
    for k, v in prof.items():
        lines.append(f"#   {k} = {v}")
    recs = uc.get("recent_records", []) or []
    lines.append(f"# 近期保障记录 (共{len(recs)}条, 最近在前):")
    for i, r in enumerate(recs[:8]):
        lines.append(
            f"#   [{i}] app={r.get('application_name')} svc={r.get('service_name')} "
            f"res={r.get('resolution')} rtt={r.get('rtt')} "
            f"start={r.get('start_timestamp')} end={r.get('end_timestamp')} "
            f"dur={r.get('duration')} completed={r.get('completed_at')}"
        )
    return "\n".join(lines)


def _shared_task_sections(
    utterance: str,
    prev_state: dict[str, Any],
    retrieved: list[RetrievedCase],
    user_context: dict[str, Any] | None,
    id_mode: bool = False,
) -> str:
    pos = [c for c in retrieved if c.validity == "valid"]
    neg = [c for c in retrieved if c.validity == "invalid"]
    demos = _dedupe_demos(pos[:4] + neg[:2]) or retrieved[:4]
    demos_text = "\n".join(_format_case(c, i + 1, id_mode=id_mode) for i, c in enumerate(demos)) or "(无)"
    schema_text = _schema_text()
    uc_text = _format_user_context(user_context or {})
    active_hint = _active_app_hint(utterance)
    return (
        f"# === SCHEMA ===\n{schema_text}\n\n"
        f"# === APPLICATION_SERVICE_MATRIX ===\n"
        + "\n".join(f"# {app}: {sorted(svcs)}" for app, svcs in sorted(APPLICATION_SERVICE_MATRIX.items()))
        + f"\n\n# === ACTIVE APPS (话语提及的应用, B3 活跃槽掩码) ===\n{active_hint}\n"
        f"# 注: 豆瓣/京东/知乎/美团/网易云音乐 是域外app(不在矩阵), 若用户提了这些, 仍按原话赋值, 校验器会判 matrix_out_application\n\n"
        f"# === USER CONTEXT (用户画像与近期记录) ===\n{uc_text}\n\n"
        f"# === DEMONSTRATIONS (含正例与负例) ===\n{demos_text}\n\n"
    )


def build_prompt(
    utterance: str,
    reference_time: str,
    prev_state: dict[str, Any],
    retrieved: list[RetrievedCase],
    user_context: dict[str, Any] | None = None,
    agent_reply: str | None = None,
    prev_statuses: dict[str, str] | None = None,
    disamb_keys: list[str] | None = None,
    disamb_cands: list[dict[str, Any]] | None = None,
    corr_keys: list[str] | None = None,
    record_rule_hint: str | None = None,
    record_ev_hint: str | None = None,
    cand_mode_hint: str | None = None,
    quality_hint: str | None = None,
    cand_landing: bool = False,
    pending_app_hint: str | None = None,
    time_hint: str | None = None,
    time_anchor_on: bool = False,
    idbook: Any | None = None,
    id_states: dict[str, dict[str, Any]] | None = None,
    memory_hint: str | None = None,
) -> tuple[str, str]:
    """Variant A: 返回 (system_prompt, task_prompt)。Text-to-Python + 强制 # summary PAR。"""
    extra = ""
    system = SYSTEM_PROMPT_PY_TIME_ANCHOR if time_anchor_on else SYSTEM_PROMPT_PY
    id_mode = idbook is not None and id_states is not None
    if prev_statuses:
        lines = "\n".join(f"#   {k} = {v}" for k, v in sorted(prev_statuses.items()))
        extra += f"# === INTENT STATUS (当前各意图状态机状态: Collecting/Ready/Confirmed) ===\n{lines}\n\n"
    if agent_reply:
        extra += (
            f"# === AGENT LAST REPLY (上一轮系统回复, 供理解用户当前话语的语境) ===\n"
            f"# agent: {agent_reply}\n\n"
        )
    if disamb_keys:
        extra += format_disambiguation_rule_block(
            disamb_keys=disamb_keys,
            disamb_cands=disamb_cands,
            id_mode=id_mode,
            id_states=id_states,
            cand_landing=cand_landing,
        )
    if corr_keys:
        extra += format_correction_rule_block(
            corr_keys=corr_keys,
            id_mode=id_mode,
            id_states=id_states,
        )
    if quality_hint:
        extra += quality_hint + "\n"
    if cand_mode_hint:
        extra += cand_mode_hint + "\n"
    if record_ev_hint:
        extra += record_ev_hint + "\n"
    if record_rule_hint:
        extra += format_record_rule_block(record_rule_hint)
    if pending_app_hint:
        extra += pending_app_hint + "\n"
    if time_hint:
        extra += time_hint + "\n"
    mem_h = memory_hint or ((user_context or {}).get("memory_hint") if isinstance(user_context, dict) else None)
    if mem_h:
        extra += str(mem_h) + "\n"
    if id_mode and idbook is not None:
        prev_block = idbook.describe_prev(id_states)
        task_prompt = (
            _shared_task_sections(utterance, prev_state, retrieved, user_context, id_mode=True)
            + extra
            + format_id_mode_task_block(prev_block, reference_time, utterance)
        )
        return system, task_prompt
    task_prompt = (
        _shared_task_sections(utterance, prev_state, retrieved, user_context)
        + extra
        + format_key_mode_task_block(_format_prev(prev_state), reference_time, utterance)
    )
    return system, task_prompt
