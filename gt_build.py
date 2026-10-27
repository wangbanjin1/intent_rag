# -*- coding: utf-8 -*-
"""gt_build.py: turn 级 GT 构建层 (v26.4 自 session_eval.py 拆出)。

职责: 从原始 session 标注构建 turn 级 GT (cancel/correction/gt_derive 三口径 + mi_on 实例化)。
消费者: session_eval.py (评估主流程) 与 error_report.py (报告 GT 累积态)——
拆出后两者均单向 import 本模块, 消除 session_eval↔error_report 双向依赖。

函数与逻辑与拆出前逐字节一致 (v26.4 重构零行为变更)。
"""
from __future__ import annotations

import collections

from case_format import flatten_params, extract_turn_cases, EVAL_SLOTS
from pipeline import intent_key

CANCEL_GT = "__cancelled__"  # turn 级 GT 标记: 该意图此轮应已被取消 (方案 b 口径)


def cancel_turn_map(session: dict) -> dict[str, int]:
    """cancelled intent_id -> 取消发生的轮次。

    依据: event_sequence 里 user.action_types 含 "Cancel_Request" 且该轮 related 此 intent
    (dataset_4 全量核验 88/88 cancelled intent 均有此标记, 且即最后 related 轮)。
    兜底: 无标记时取该 intent 最后 related 轮。
    """
    intents = {i["intent_id"]: i for i in (session.get("intents") or [])}
    canc = {iid for iid, i in intents.items() if i.get("status") == "cancelled"}
    out: dict[str, int] = {}
    if not canc:
        return out
    last_related: dict[str, int] = {}
    for ev in (session.get("event_sequence") or []):
        turn = ev.get("turn", 0)
        rel = set(ev.get("related_intent_ids") or [])
        acts = (ev.get("user") or {}).get("action_types") or []
        for iid in canc & rel:
            last_related[iid] = turn
            if "Cancel_Request" in acts:
                out[iid] = turn
    for iid in canc:
        if iid not in out and iid in last_related:
            out[iid] = last_related[iid]
    return out


CORRECT_MARK = "__correction__"  # turn 级 GT 标记: 该轮为"用户说错"错误值轮 (correction_credit 口径)


def correction_turn_map(session: dict) -> dict[str, tuple[int, set]]:
    """改口错误值轮定位: intent_id -> (错误轮 turn, 被更正字段集合 EVAL_SLOTS 扁平名)。

    依据: event_sequence 里 user.action_types 同时含 "Negate" 与 "Correct_Previous_Input"
    的轮为更正轮 t (d5 全量核验: 仅 T2-3 轮2 x49、T3-2 轮3 x43 命中;
    T3-3 消歧的 Negate 单独出现、T2-5 改参无此组合, 均不误命中)。
    在该 intent 的 slot_updates 中, 轮 t 覆盖(同名字段值不同)的最近一次写入轮即错误值轮,
    被覆盖字段即错误字段。
    """
    from case_format import flatten_params as _fp2
    corr_turns: list[tuple[int, set]] = []  # (更正轮, related intent_ids)
    for ev in (session.get("event_sequence") or []):
        acts = set((ev.get("user") or {}).get("action_types") or [])
        if "Negate" in acts and "Correct_Previous_Input" in acts:
            corr_turns.append((ev.get("turn", 0),
                               set(ev.get("related_intent_ids") or [])))
    if not corr_turns:
        return {}
    out: dict[str, tuple[int, set]] = {}
    updates = {su["intent_id"]: su.get("turn_updates") or []
               for su in (session.get("slot_updates") or [])}
    for t, rel in corr_turns:
        for iid, tups in updates.items():
            if rel and iid not in rel:
                continue
            by_turn = {tu["turn"]: _fp2(tu.get("params") or {}) for tu in tups}
            new = by_turn.get(t)
            if not new:
                continue
            for tp in sorted((x for x in by_turn if x < t), reverse=True):
                old = by_turn[tp]
                changed = {f for f in new
                           if f in old and old[f] != new[f] and f in EVAL_SLOTS}
                if changed:
                    out[iid] = (tp, changed)
                    break
    return out


def _derive_time_flat(gt_flat: dict) -> tuple[dict, list[str]]:
    """turn 级 GT 时间三字段知二推一 (与 validator._complete_timestamp 同规则的扁平版)。

    背景: GT 标注只把字段登记在"字面明说"的轮次, 中间轮常只有 start+end 无 duration
    (d5 全量 29 处/test 11 处, 全 X-2); 而模型侧 validator 每轮强制知二推一,
    GT 最终态也必然三字段齐全 —— GT 中间轮不同步推导会把模型的正确推导判错。
    只补缺失字段, 不覆盖已有值; 三个方向: start+end→duration, start+dur→end, end+dur→start。
    返回 (补齐后的 gt_flat 副本, 推导出的字段名列表)。
    """
    from validator import parse_dt, parse_duration_minutes, _fmt_dt
    from datetime import timedelta
    out = dict(gt_flat)
    st, en, du = out.get("start_timestamp"), out.get("end_timestamp"), out.get("duration")
    st_dt, en_dt = parse_dt(st), parse_dt(en)
    du_min = parse_duration_minutes(du)
    derived = []
    if st_dt and en_dt and not du:
        out["duration"] = f"{int((en_dt - st_dt).total_seconds() // 60)}min"
        derived.append("duration")
    elif st_dt and du_min is not None and not en:
        out["end_timestamp"] = _fmt_dt(st_dt + timedelta(minutes=du_min))
        derived.append("end_timestamp")
    elif en_dt and du_min is not None and not st:
        out["start_timestamp"] = _fmt_dt(en_dt - timedelta(minutes=du_min))
        derived.append("start_timestamp")
    return out, derived


def turn_gt(session: dict, cancel_credit: bool = True, correction_credit: bool = True,
            gt_derive: bool = True, mi_on: bool = False) -> list[dict]:
    """turn 级 GT: 截止某轮，所有当时在状态里的 intent 的累积态 7 字段。
    用 extract_turn_cases (复用 make_split 的逻辑)，每个 case 的 gt_final_flat。
    返回 [{turn, intent_key, gt_flat}]。

    cancel_credit=True (方案 b): cancelled intent 在取消轮的 case GT 记为 CANCEL_GT 标记,
    模型该轮正确移除该意图(pred 无此 key)算命中; 取消轮之前的轮次仍按历史态比对。

    correction_credit=True: "用户说错"错误值轮(T2-3 轮1 / T3-2 轮2)的 case GT 记为
    {CORRECT_MARK: 豁免字段, "__gt__": 真实GT}, 模型该轮拒收错误值(pred 空)算命中,
    pred 非空时豁免被更正字段后比对其余字段 (记字面值也算对, 其余字段错仍判错)。

    gt_derive=True: turn 级 GT 时间三字段知二推一 (_derive_time_flat, 与 validator 同规则),
    对齐模型侧每轮强制推导与 GT 最终态三字段必齐的事实; 只补缺失不覆盖。
    """
    cturn = cancel_turn_map(session) if cancel_credit else {}
    cmap = correction_turn_map(session) if correction_credit else {}
    # v26.3 P3 mi_on: GT 实例编号表——全量 intents[] 中 app/svc base 首现序 (与 session_gt 同规则)
    base_of_iid: dict[str, str] = {}
    if mi_on:
        cnt: collections.Counter = collections.Counter()
        for it in (session.get("intents") or []):
            f = flatten_params(it.get("params") or {})
            app = f.get("application_name")
            if not app:
                continue
            b = intent_key(app, f.get("service_name"))
            cnt[b] += 1
            base_of_iid[it.get("intent_id", "")] = b if cnt[b] == 1 else f"{b}#{cnt[b]}"
    out = []
    for c in extract_turn_cases(session):
        from pipeline import intent_key as _ik
        # 用 case 的 final state (apply prev+delta) 取 app/svc
        from case_format import apply_nested, flatten_params as _fp
        final = apply_nested(c.get("prev_state", {}), c.get("delta", {}))
        fflat = _fp(final)
        app = fflat.get("application_name")
        svc = fflat.get("service_name")
        k = _ik(app, svc)
        if not app:
            continue  # 无 app 的非保障意图，turn 级不算（与 eval.py 一致）
        iid = c.get("case_id", "").rsplit("-", 1)[-1]
        if mi_on and iid in base_of_iid:
            k = base_of_iid[iid]  # 实例 key (与 session_gt 编号一致)
        gt_flat = {s: fflat.get(s) for s in EVAL_SLOTS}
        derived = []
        if gt_derive:
            gt_flat, derived = _derive_time_flat(gt_flat)
        if iid in cturn and c["turn"] == cturn[iid]:
            gt_flat = CANCEL_GT
        elif iid in cmap and c["turn"] == cmap[iid][0]:
            gt_flat = {CORRECT_MARK: sorted(cmap[iid][1]), "__gt__": gt_flat}
        out.append({
            "turn": c["turn"],
            "intent_key": k,
            "intent_id": iid,
            "gt_flat": gt_flat,
            "gt_derived": derived,
        })
    return out

