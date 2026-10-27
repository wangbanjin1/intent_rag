"""原始 session 格式的 case 抽取与转换。

数据严格保持原始嵌套 params 格式:
  {
    "application_name": {"value": "豆瓣", "source_type": "Turn", "source_ref": "U1"},
    "service_name":     {"value": "视频", ...},
    "resolution":       {"min_value": {"value": "2160p", ...}},
    "rtt":              {"max_value": {"value": "100ms", ...}},
    "timestamp": {
        "start_timestamp": {"value": "...", "source_type": "Context", ...},
        "end_timestamp":   {"value": "...", ...},
        "duration":        {"value": "90min", ...}
    }
  }

- case.delta = turn_updates.params 原样嵌套(本轮新增/更新的槽位, 即 Δy)
- case.prev_state = 之前所有轮累积的嵌套 params
- coreference: value 处可为 {"ref": "state.<slot>"}, apply 时解析
- 校验/检索内部用 flatten_params 临时扁平化, 对外不暴露扁平结构
"""
from __future__ import annotations

import copy
from typing import Any


# --------------------------------------------------------------------
# 嵌套 ↔ 扁平 (内部用)
# --------------------------------------------------------------------
def flatten_params(nested: dict[str, Any]) -> dict[str, Any]:
    """嵌套 params → 扁平 dict(内部校验/apply 用)。
    {"resolution":{"min_value":{"value":"2160p"}}} → {"resolution":"2160p"}
    timestamp 子项拍平到顶层: start_timestamp/end_timestamp/duration
    支持 value = {"ref":"state.<slot>"} 的 coreference。
    """
    out: dict[str, Any] = {}
    if not isinstance(nested, dict):
        return out
    for slot, spec in nested.items():
        if not isinstance(spec, dict):
            out[slot] = spec
            continue
        if slot == "timestamp":
            for sub, sub_spec in spec.items():
                if isinstance(sub_spec, dict) and "value" in sub_spec:
                    out[sub] = sub_spec["value"]
                elif isinstance(sub_spec, dict) and sub_spec.get("ref", "").startswith("state."):
                    out[sub] = sub_spec
            continue
        if "value" in spec:
            out[slot] = spec["value"]
        elif spec.get("ref", "").startswith("state."):
            out[slot] = spec
        else:
            for k in ("min_value", "max_value"):
                if k in spec and isinstance(spec[k], dict):
                    inner = spec[k]
                    if "value" in inner:
                        out[slot] = inner["value"]
                        break
                    if inner.get("ref", "").startswith("state."):
                        out[slot] = inner
                        break
            else:
                out[slot] = spec
    return out


def nested_from_flat(flat: dict[str, Any], source_type: str = "Turn", source_ref: str = "U1") -> dict[str, Any]:
    """扁平 dict → 嵌套 params(带 source_type/source_ref)。"""
    TIME_KEYS = {"start_timestamp", "end_timestamp", "duration"}
    nested: dict[str, Any] = {}
    ts_obj: dict[str, Any] = {}
    for slot, val in (flat or {}).items():
        if slot in TIME_KEYS:
            ts_obj[slot] = _wrap(val, source_type, source_ref)
        elif slot == "resolution":
            nested[slot] = {"min_value": _wrap(val, source_type, source_ref)}
        elif slot in ("rtt", "bandwidth"):
            nested[slot] = {"max_value": _wrap(val, source_type, source_ref)}
        else:
            nested[slot] = _wrap(val, source_type, source_ref)
    if ts_obj:
        nested["timestamp"] = ts_obj
    return nested


def _wrap(val: Any, source_type: str, source_ref: str) -> dict[str, Any]:
    if isinstance(val, dict) and val.get("ref", "").startswith("state."):
        return val
    return {"value": val, "source_type": source_type, "source_ref": source_ref}


# --------------------------------------------------------------------
# 嵌套层 apply (Δy → y_{t-1} → y_t), 保持嵌套结构
# --------------------------------------------------------------------
def apply_nested(prev_nested: dict[str, Any], delta_nested: dict[str, Any]) -> dict[str, Any]:
    """把 Δy(本轮 turn_updates.params, 嵌套) apply 到 prev_state(嵌套)。
    - 同名顶层槽位直接覆盖
    - timestamp 子项按子键合并(start/end/duration)
    - coreference: value={"ref":"state.<slot>"} 从 prev 扁平态解析后写回(解析失败保留 ref)
    """
    y = copy.deepcopy(prev_nested or {})
    if not isinstance(delta_nested, dict):
        return y
    prev_flat = flatten_params(prev_nested)
    for slot, spec in delta_nested.items():
        if slot == "timestamp" and isinstance(spec, dict):
            ts = y.setdefault("timestamp", {})
            for sub, sub_spec in spec.items():
                ts[sub] = _resolve_spec(sub_spec, prev_flat)
        else:
            y[slot] = _resolve_spec(spec, prev_flat)
    return y


def _resolve_spec(spec: Any, prev_flat: dict[str, Any]) -> Any:
    """若 spec 内含 {"ref":"state.<slot>"}, 用 prev_flat 解析; 否则原样返回。"""
    if not isinstance(spec, dict):
        return spec
    # {"ref": "state.xxx"} 顶层 ref
    if spec.get("ref", "").startswith("state."):
        return {"value": _resolve_ref(prev_flat, spec["ref"])}
    # {"value": {"ref": "state.xxx"}}
    if "value" in spec and isinstance(spec["value"], dict) and spec["value"].get("ref", "").startswith("state."):
        resolved = _resolve_ref(prev_flat, spec["value"]["ref"])
        new = copy.deepcopy(spec)
        new["value"] = resolved
        return new
    # {"min_value": {"ref": "state.xxx"}} / {"max_value": ...}
    for k in ("min_value", "max_value"):
        if k in spec and isinstance(spec[k], dict) and spec[k].get("ref", "").startswith("state."):
            resolved = _resolve_ref(prev_flat, spec[k]["ref"])
            new = copy.deepcopy(spec)
            new[k] = {"value": resolved, "source_type": spec[k].get("source_type", "Turn"),
                      "source_ref": spec[k].get("source_ref", "U1")}
            return new
    return spec


def _resolve_ref(state_flat: dict[str, Any], ref: str) -> Any:
    parts = ref.split(".")[1:]
    cur: Any = state_flat
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def has_unresolved_coref_nested(delta_nested: dict[str, Any], prev_nested: dict[str, Any]) -> bool:
    """Δy(嵌套) 中若有 {"ref":"state.<path>"} 但 path 在 prev_state 不存在 → unresolved。"""
    prev_flat = flatten_params(prev_nested)
    for slot, spec in (delta_nested or {}).items():
        refs = _collect_refs(spec)
        for ref in refs:
            if _resolve_ref(prev_flat, ref) is None:
                return True
    return False


def _collect_refs(spec: Any) -> list[str]:
    out: list[str] = []
    if isinstance(spec, dict):
        if spec.get("ref", "").startswith("state."):
            out.append(spec["ref"])
        for k in ("value", "min_value", "max_value"):
            if k in spec and isinstance(spec[k], dict) and spec[k].get("ref", "").startswith("state."):
                out.append(spec[k]["ref"])
    return out


# --------------------------------------------------------------------
# 从原始 session 抽取 case (严格保持嵌套格式)
# --------------------------------------------------------------------
def extract_turn_cases(session: dict[str, Any]) -> list[dict[str, Any]]:
    """从原始 session 抽取每轮 case。

    返回 list[case], 每个 case:
      {case_id, session_id, turn, intent, prev_state(嵌套), utterance,
       delta(嵌套 = turn_updates.params 原样), validity, invalid_reason, reference_time,
       flat_params(intent 最终扁平 params, 供检索)}
    prev_state 按 intent 累积(嵌套 apply)。
    """
    session_id = session.get("session_id", "")
    reference_time = session.get("reference_time", "")
    event_seq = session.get("event_sequence", []) or []
    intents = {i["intent_id"]: i for i in (session.get("intents") or [])}
    slot_updates = {s["intent_id"]: s for s in (session.get("slot_updates") or [])}

    intent_states: dict[str, dict[str, Any]] = {}  # intent_id -> 累积嵌套 params
    cases: list[dict[str, Any]] = []

    for ev in event_seq:
        turn = ev.get("turn", 0)
        user_utt = (ev.get("user") or {}).get("utterance", "")
        related = ev.get("related_intent_ids", []) or []
        for iid in related:
            intent_obj = intents.get(iid, {})
            upd = slot_updates.get(iid, {})
            turn_updates = upd.get("turn_updates", []) or []
            turn_params = {}
            for tu in turn_updates:
                if tu.get("turn") == turn:
                    turn_params = tu.get("params", {}) or {}
                    break
            prev = copy.deepcopy(intent_states.get(iid, {}))
            cases.append({
                "case_id": f"{session_id}-T{turn}-{iid}",
                "session_id": session_id,
                "turn": turn,
                "intent": intent_obj.get("intent", "unknown"),
                "utterance": user_utt,
                "prev_state": prev,            # 嵌套 params
                "delta": copy.deepcopy(turn_params),  # 嵌套 params, 原样 = Δy
                "validity": intent_obj.get("validity", "valid"),
                "invalid_reason": intent_obj.get("invalid_reason"),
                "reference_time": reference_time,
                "flat_params": flatten_params(intent_obj.get("params", {})),
            })
            # 累积(嵌套 apply, coreference 用 prev_flat 解析后写回)
            intent_states[iid] = apply_nested(prev, turn_params)
    return cases


# --------------------------------------------------------------------
# Ground truth: 截止该轮的累积 final_state 的 7 字段扁平值 (JGA 语义)
# --------------------------------------------------------------------
EVAL_SLOTS = [
    "application_name", "service_name", "resolution",
    "rtt", "start_timestamp", "end_timestamp", "duration",
]


def gt_final_flat(case: dict) -> dict[str, Any]:
    """返回 case 截止该轮累积 final_state 的 7 字段扁平值。
    = flatten_params(apply_nested(prev_state, delta)) 取 EVAL_SLOTS。
    对应 RefPyDST y_t = apply(Δy_t, y_{t-1}) 的 JGA 评估口径。
    """
    final = apply_nested(case.get("prev_state", {}), case.get("delta", {}))
    flat = flatten_params(final)
    return {s: flat.get(s) for s in EVAL_SLOTS}


def is_guarantee_intent(intent_name: str, params: dict) -> bool:
    """判定是否保障意图: intent 名含"保障" 且 params 含 application_name。"""
    return ("保障" in (intent_name or "")) and bool(params and params.get("application_name"))
