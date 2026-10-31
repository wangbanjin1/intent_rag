"""intent_status.py: 意图状态机 — Collecting / Ready / Confirmed / Cancelled。

设计 (详见 report_flow.md):
  - Collecting: 已识别, 但存在阻塞性缺失/模糊/错误 (必填槽未齐 或 校验未过)
  - Ready:      参数完整并通过规则校验
  - Confirmed:  用户明确确认 (Affirm 轮, 规则识别)
  - Cancelled:  用户取消 (复用 pipeline 的 __cancel__ 信号)

纯后处理旁路: 不改变 prev_state 透传 / validator / JGA 判定, 只做状态推导与对比评估。
"""
from __future__ import annotations

import collections
import re
from typing import Any

from schema import KNOWN_APPLICATIONS

STATUS_COLLECTING = "Collecting"
STATUS_READY = "Ready"
STATUS_CONFIRMED = "Confirmed"
STATUS_CANCELLED = "Cancelled"
ALL_STATUSES = (STATUS_COLLECTING, STATUS_READY, STATUS_CONFIRMED, STATUS_CANCELLED)

_REQUIRED = ("application_name", "service_name", "start_timestamp", "resolution", "rtt")

_AFFIRM_START = (
    "对", "是的", "好的", "可以", "确认", "没问题", "没错",
    "嗯", "就这样", "行吧", "行,", "行，", "OK", "ok",
)
_AFFIRM_NEG = (
    "不是", "不对", "搞错", "说错", "弄错", "取消", "不用", "别管",
    "改", "换成", "算了", "等等", "等一下", "先别",
    "谢谢", "没其他", "先这样", "没别的", "辛苦", "拜拜", "再见", "感谢",
    "吗", "？", "?",
)


def is_affirm(utterance: str) -> bool:
    """规则识别用户确认话语 (零 LLM 成本)。"""
    t = (utterance or "").strip()
    if not t or len(t) > 50:
        return False
    if any(w in t for w in _AFFIRM_NEG):
        return False
    return any(t.startswith(w) for w in _AFFIRM_START)


_CHOICE_PAT = re.compile(
    r"还是[^。！？]{0,15}(呢|啊|没想好|不确定|没定|没拿定|没决定)"
    r"|没想好|没拿定主意|还没定|没确定|还没决定|还在犹豫|犹豫|还在纠结|纠结"
    r"|或者[^。！？]{0,12}(呢|还没|没想|没定)"
    r"|是[^。！？]{1,14}还是[^。！？]{1,14}[呢啊?？]"
)


def has_pending_choice(utterance: str) -> bool:
    return bool(_CHOICE_PAT.search(utterance or ""))


def _slots_complete(flat: dict[str, Any]) -> bool:
    return all((flat.get(s) or None) not in (None, "", "None") for s in _REQUIRED)


def in_matrix(flat: dict[str, Any]) -> bool:
    """app 在受理矩阵内才进入状态机评估 (域外与 JGA 口径一致: 不评)。"""
    app = flat.get("application_name")
    return isinstance(app, str) and app in KNOWN_APPLICATIONS


def derive_status(
    prev_status: str | None,
    flat: dict[str, Any],
    validity: str | None,
    cancelled: bool = False,
    affirmed: bool = False,
    touched: bool = False,
    pending_choice: bool = False,
) -> str:
    """推导某 intent 本轮末的状态。优先级: Cancelled > Confirmed > Ready > Collecting。"""
    if cancelled:
        return STATUS_CANCELLED
    complete = _slots_complete(flat)
    ok = validity in (None, "valid", "unchanged")
    if affirmed and complete:
        return STATUS_CONFIRMED
    if prev_status == STATUS_CONFIRMED:
        if not touched:
            return STATUS_CONFIRMED
        return STATUS_READY if (complete and ok and not pending_choice) else STATUS_COLLECTING
    return STATUS_READY if (complete and ok and not pending_choice) else STATUS_COLLECTING


def _build_iid_key_map(session: dict[str, Any], mi_on: bool) -> dict[str, str]:
    """构造多实例模式下 intent_id -> 实例 key 编号映射表。"""
    if not mi_on:
        return {}
    from case_format import flatten_params
    key_of_iid: dict[str, str] = {}
    cnt: collections.Counter[str] = collections.Counter()
    for it in session.get("intents") or []:
        f = flatten_params(it.get("params") or {})
        app = f.get("application_name")
        if not app:
            continue
        b = f"{app}/{f.get('service_name')}" if f.get("service_name") else app
        cnt[b] += 1
        key_of_iid[it.get("intent_id", "")] = b if cnt[b] == 1 else f"{b}#{cnt[b]}"
    return key_of_iid


def _collect_turn_action_maps(
    events: list[dict[str, Any]], intents: dict[str, dict[str, Any]]
) -> tuple[dict[str, int], dict[str, int], dict[int, set[str]]]:
    """从对话事件序列中收集每个意图的取消轮、确认轮与系统追问/消歧阻塞轮。"""
    cancel_turn: dict[str, int] = {}
    affirm_turn: dict[str, int] = {}
    blocking_turn: dict[int, set[str]] = {}
    for ev in events:
        u_acts = (ev.get("user") or {}).get("action_types", []) or []
        a_acts = (ev.get("agent") or {}).get("action_types", []) or []
        rel = ev.get("related_intent_ids", []) or []
        t = ev.get("turn", 0)
        if "Cancel_Request" in u_acts:
            for iid in rel:
                if intents.get(iid, {}).get("status") == "cancelled" and iid not in cancel_turn:
                    cancel_turn[iid] = t
        if "Affirm" in u_acts:
            for iid in rel:
                affirm_turn.setdefault(iid, t)
        if "Request_Slot" in a_acts or "Request_Disambiguation" in a_acts:
            blocking_turn[t] = set(rel) if rel else set(intents)
    return cancel_turn, affirm_turn, blocking_turn


def build_gt_status_trajectory(session: dict[str, Any], mi_on: bool = False) -> dict[int, dict[str, str]]:
    """返回 {turn: {intent_key: status}} — 每轮末每个活跃 intent 的 GT 状态。"""
    from case_format import apply_nested, flatten_params

    intents = {it["intent_id"]: it for it in (session.get("intents") or [])}
    events = sorted(session.get("event_sequence") or [], key=lambda e: e.get("turn", 0))
    key_of_iid = _build_iid_key_map(session, mi_on)
    cancel_turn, affirm_turn, blocking_turn = _collect_turn_action_maps(events, intents)

    cum: dict[str, dict[str, Any]] = {}
    updates_by_turn: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for su in session.get("slot_updates") or []:
        iid = su.get("intent_id")
        for tu in su.get("turn_updates") or []:
            updates_by_turn.setdefault(tu.get("turn", 0), []).append((iid, tu.get("params") or {}))

    traj: dict[int, dict[str, str]] = {}
    prev_status: dict[str, str] = {}
    seen: set[str] = set()
    for ev in events:
        t = ev.get("turn", 0)
        for iid, params in updates_by_turn.get(t, []):
            cum[iid] = apply_nested(cum.get(iid, {}), params)
            seen.add(iid)
        out: dict[str, str] = {}
        blocked = blocking_turn.get(t, set())
        if t in blocking_turn:
            _slot_only = {
                iid for iid in blocked if not _slots_complete(flatten_params(cum.get(iid, {})))
            }
            if _slot_only:
                blocked = _slot_only
        for iid in sorted(seen):
            flat = flatten_params(cum.get(iid, {}))
            if not in_matrix(flat):
                continue
            app, svc = flat.get("application_name"), flat.get("service_name")
            if not app:
                continue
            key = f"{app}/{svc}" if svc else app
            if mi_on:
                key = key_of_iid.get(iid, key)
            st = derive_status(
                prev_status.get(key),
                flat,
                "valid",
                cancelled=(iid in cancel_turn and t >= cancel_turn[iid]),
                affirmed=(iid in affirm_turn and t >= affirm_turn[iid]),
                touched=any(u == iid for u, _ in updates_by_turn.get(t, [])),
            )
            if st == STATUS_READY and iid in blocked:
                st = STATUS_COLLECTING
            out[key] = st
            prev_status[key] = st
        seen = {iid for iid in seen if not (iid in cancel_turn and t >= cancel_turn[iid])}
        traj[t] = out
    return traj


def judge_status(
    gt_traj: dict[int, dict[str, str]],
    pred_traj: dict[int, dict[str, str]],
) -> tuple[int, int, list[dict[str, Any]]]:
    """逐 (turn, intent_key) 比对 pred/GT 状态。返回 (n_match, n_total, detail)。"""
    n_match = n_total = 0
    detail: list[dict[str, Any]] = []
    for t in sorted(gt_traj):
        pdict = pred_traj.get(t, {})
        for key, gst in sorted(gt_traj[t].items()):
            n_total += 1
            pst = pdict.get(key)
            ok = pst == gst
            if ok:
                n_match += 1
            detail.append({
                "turn": t, "intent_key": key, "gt_status": gst,
                "pred_status": pst, "match": ok, "missed": pst is None,
            })
    return n_match, n_total, detail
