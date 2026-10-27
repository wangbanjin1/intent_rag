"""intent_status.py: 意图状态机 — Collecting / Ready / Confirmed / Cancelled。

设计 (详见 report_flow.md):
  - Collecting: 已识别, 但存在阻塞性缺失/模糊/错误 (必填槽未齐 或 校验未过)
  - Ready:      参数完整并通过规则校验
  - Confirmed:  用户明确确认 (Affirm 轮, 规则识别)
  - Cancelled:  用户取消 (复用 pipeline 的 __cancel__ 信号)

纯后处理旁路: 不改变 prev_state 透传 / validator / JGA 判定, 只做状态推导与对比评估。
系统视角: 状态是"系统当时能观察到的状态" (如纠正轮之前系统认为是 Ready),
与 GT 轨迹重建规则保持同一视角, 保证 pred/GT 可比。

独立消融开关 (config, 默认全 false = 现状基线):
  status_tracking.enabled — 推导 pred 状态轨迹 (session_eval 内)
  confirm_rule.enabled    — Affirm 规则识别 (命中才产生 Confirmed)
  status_prompt.enabled   — INTENT STATUS 快照进 prompt (prompt_builder)
  agent_context.enabled   — 上一轮 agent 回复进 prompt (prompt_builder)
"""
from __future__ import annotations

import re
from typing import Any

from schema import KNOWN_APPLICATIONS

STATUS_COLLECTING = "Collecting"
STATUS_READY = "Ready"
STATUS_CONFIRMED = "Confirmed"
STATUS_CANCELLED = "Cancelled"
ALL_STATUSES = (STATUS_COLLECTING, STATUS_READY, STATUS_CONFIRMED, STATUS_CANCELLED)

# "参数完整"操作定义 (数据实证, 见 report_flow.md):
#   dataset_merged 全量交叉表 — agent Acknowledge 的 912 例全部满足
#   app/svc/start/resolution/rtt 五槽齐; agent Request_Slot 的 89 例正是
#   core 三槽齐但缺 resolution/rtt (requested_params 标注主要为
#   resolution.min_value / rtt.max_value)。故五槽齐 = Ready 的充要近似 (≈99%)。
_REQUIRED = ("application_name", "service_name", "start_timestamp", "resolution", "rtt")


# --------------------------------------------------------------------
# 1) Affirm 规则识别 (Confirmed 信号)
#    数据支撑: dataset_merged 117 条 Affirm 话语高度模板化
#    ("对的,没问题。"/"好的,确认。"/"对,就按这个来。" ...), 全部出现在末轮。
# --------------------------------------------------------------------
_AFFIRM_START = (
    "对", "是的", "好的", "可以", "确认", "没问题", "没错",
    "嗯", "就这样", "行吧", "行,", "行，", "OK", "ok",
)
# 否定/改口/取消词: 命中即不是 Affirm ("没错"含"错"但不在此列;
# "别"过宽会误伤"没别的问题", 用"别管"; "改"拦截"对了,...帮我改一下"(T2-5 改口);
# "谢谢/没其他/先这样"是 End_Dialogue 标注的结尾寒暄, 数据集不标 Affirm)
_AFFIRM_NEG = (
    "不是", "不对", "搞错", "说错", "弄错", "取消", "不用", "别管",
    "改", "换成", "算了", "等等", "等一下", "先别",
    "谢谢", "没其他", "先这样", "没别的", "辛苦", "拜拜", "再见", "感谢",
    "吗", "？", "?",  # 疑问句不是确认
)


def is_affirm(utterance: str) -> bool:
    """规则识别用户确认话语 (零 LLM 成本)。

    起点词命中 且 无否定/疑问词, 且为短句 (确认话语都很短, 最长样本 <40 字)。
    """
    t = (utterance or "").strip()
    if not t or len(t) > 50:
        return False
    if any(w in t for w in _AFFIRM_NEG):
        return False
    return any(t.startswith(w) for w in _AFFIRM_START)


# --------------------------------------------------------------------
# 1b) 选择疑问检测 (Collecting 的"模糊"信号, pred 侧 utterance 规则)
#    T2-2 clarify 场景: 用户给选择疑问 ("是A还是B呢/我还没想好/还在犹豫"),
#    槽位可能已有值但语义未定 → Collecting。数据实证: 召回 48/78 (62%), FP 0。
#    已知边界: record 消歧类 ("按那条记录来") 话语无犹豫词, 规则覆盖不了,
#    需画像候选数>1 才可判, 本次不做 (见 report_flow.md §已知边界)。
# --------------------------------------------------------------------
_CHOICE_PAT = re.compile(
    r"还是[^。！？]{0,15}(呢|啊|没想好|不确定|没定|没拿定|没决定)"
    r"|没想好|没拿定主意|还没定|没确定|还没决定|还在犹豫|犹豫|还在纠结|纠结"
    r"|或者[^。！？]{0,12}(呢|还没|没想|没定)"
    r"|是[^。！？]{1,14}还是[^。！？]{1,14}[呢啊?？]"
)


def has_pending_choice(utterance: str) -> bool:
    return bool(_CHOICE_PAT.search(utterance or ""))


# --------------------------------------------------------------------
# 2) 状态推导 (系统视角, pred/GT 共用同一套规则保证可比)
# --------------------------------------------------------------------
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
    """推导某 intent 本轮末的状态。优先级: Cancelled > Confirmed > Ready > Collecting。

    prev_status:    上一轮状态 (锁存 Confirmed 用)
    flat:           本轮末累积槽位 (扁平)
    validity:       validator 结果 ("valid"/"invalid"; 透传未触及的意图用 "unchanged")
    cancelled:      本轮该 intent 被取消
    affirmed:       本轮用户话语被识别为 Affirm
    touched:        本轮该 intent 有 delta (参数被修改)
    pending_choice: 本轮话语含选择疑问/犹豫词 (槽位有值但语义未定 → Collecting)
    """
    if cancelled:
        return STATUS_CANCELLED
    complete = _slots_complete(flat)
    ok = validity in (None, "valid", "unchanged")
    if affirmed and complete:
        return STATUS_CONFIRMED
    if prev_status == STATUS_CONFIRMED:
        # 锁存: 确认后未被触及保持 Confirmed; 确认后又改参数 -> 退回 Ready/Collecting
        if not touched:
            return STATUS_CONFIRMED
        return STATUS_READY if (complete and ok and not pending_choice) else STATUS_COLLECTING
    return STATUS_READY if (complete and ok and not pending_choice) else STATUS_COLLECTING


# --------------------------------------------------------------------
# 3) GT 状态轨迹离线重建 (从 event_sequence + intents + slot_updates)
#    与 derive_status 同规则、同系统视角; 域外/rejected intent 不进入轨迹。
# --------------------------------------------------------------------
def build_gt_status_trajectory(session: dict[str, Any], mi_on: bool = False) -> dict[int, dict[str, str]]:
    """返回 {turn: {intent_key: status}} — 每轮末每个活跃 intent 的 GT 状态。

    mi_on (v26.3 P3): 同 app/svc 多意图实例化 key 加 #N (全量 intents[] 首现序,
    与 session_gt 同规则); pred 侧 run_session 的 turn_status 用同一张编号表对齐。

    重建依据:
      - 累积槽位: slot_updates[intent].turn_updates 逐轮 apply
      - Collecting 双通道 (系统视角, 无未来信息泄漏):
          a) 当轮 agent action 含 Request_Slot / Request_Disambiguation
             (生成器标注的"系统认为存在阻塞性缺失/模糊", requested_params 实证)
          b) 五槽未齐 (与 pred 侧槽位规则同定义)
      - Cancelled: intents[].status=="cancelled", cancel 轮 = 含 Cancel_Request
        且 related_intent_ids 含该 intent 的轮 (86 例, 已验证对齐)
      - Confirmed: 含 Affirm 的轮, related_intent_ids 即确认对象
        (117 例全部单意图, 无确认对象歧义)
      - Ready: 五槽齐且 agent 未 Request_Slot/Disambig
    """
    from case_format import flatten_params, apply_nested

    intents = {it["intent_id"]: it for it in (session.get("intents") or [])}
    events = sorted(session.get("event_sequence") or [], key=lambda e: e.get("turn", 0))

    # mi_on: iid -> 实例 key 编号表 (全量 intents[] 中 app/svc base 首现序)
    key_of_iid: dict[str, str] = {}
    if mi_on:
        import collections as _cols
        cnt: _cols.Counter = _cols.Counter()
        for it in (session.get("intents") or []):
            f = flatten_params(it.get("params") or {})
            app = f.get("application_name")
            if not app:
                continue
            b = f"{app}/{f.get('service_name')}" if f.get("service_name") else app
            cnt[b] += 1
            key_of_iid[it.get("intent_id", "")] = b if cnt[b] == 1 else f"{b}#{cnt[b]}"

    # 每轮的 cancel / affirm 目标 intent_id 集合, 及 agent 的阻塞性反馈轮
    cancel_turn: dict[str, int] = {}
    affirm_turn: dict[str, int] = {}
    blocking_turn: dict[str, set[int]] = {}  # turn -> agent Request_Slot/Disambig 波及的 iid
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
            # 单意图 session rel 即该 intent; 多意图 session 用 related 过滤
            blocking_turn[t] = set(rel) if rel else set(intents)
            # v27.2 口径修正标记: Request_Slot 只波及槽位不齐的意图 (过滤在使用处做,
            # 因为需要当轮累积槽位 cum, 此处尚未构建)

    # 累积槽位: intent_id -> 累积 nested
    cum: dict[str, dict[str, Any]] = {}
    updates_by_turn: dict[int, list[tuple[str, dict]]] = {}
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
        # v27.2 口径修正 (用户拍板): Request_Slot 只波及【槽位不齐】的意图——客服不会对
        # 字段已齐的保障追问; S-000012 型 related 整轮列出但仅个别意图被问。
        # 消歧 (Request_Disambiguation) 不做此过滤: 有歧义待选与槽位齐否无关。
        if t in blocking_turn:
            _slot_only = {iid for iid in blocked
                          if not _slots_complete(flatten_params(cum.get(iid, {})))}
            if _slot_only:
                blocked = _slot_only
        for iid in sorted(seen):
            flat = flatten_params(cum.get(iid, {}))
            if not in_matrix(flat):
                continue  # 域外/rejected 不进状态机
            app, svc = flat.get("application_name"), flat.get("service_name")
            if not app:
                continue
            key = f"{app}/{svc}" if svc else app
            if mi_on:
                key = key_of_iid.get(iid, key)  # 实例 key (编号表兜底原 key)
            st = derive_status(
                prev_status.get(key), flat, "valid",
                cancelled=(iid in cancel_turn and t >= cancel_turn[iid]),
                affirmed=(iid in affirm_turn and t >= affirm_turn[iid]),
                touched=any(u == iid for u, _ in updates_by_turn.get(t, [])),
            )
            # agent 当轮阻塞性反馈覆盖槽位规则 (消歧场景槽位可能已齐但仍 Collecting)
            if st == STATUS_READY and iid in blocked:
                st = STATUS_COLLECTING
            out[key] = st
            prev_status[key] = st
        # 已 Cancelled 的 intent 后续轮不再跟踪
        seen = {iid for iid in seen
                if not (iid in cancel_turn and t >= cancel_turn[iid])}
        traj[t] = out
    return traj


# --------------------------------------------------------------------
# 4) 状态对比评估
# --------------------------------------------------------------------
def judge_status(
    gt_traj: dict[int, dict[str, str]],
    pred_traj: dict[int, dict[str, str]],
) -> tuple[int, int, list[dict[str, Any]]]:
    """逐 (turn, intent_key) 比对 pred/GT 状态。

    pred key 缺失 (意图识别错/张冠李戴) 记 missed=True, 不算 match。
    返回 (n_match, n_total, detail)。
    """
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
