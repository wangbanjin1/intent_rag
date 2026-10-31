"""session_eval.py: session 级 JGA 评估（用户要求的口径）。

评估单元 = 1 个 session（163 test sessions），不是 turn-level case（383）。
口径:
  - 模型 turn-by-turn 增量跑完整个 session（每轮 run_multi，prev_states 用上一轮真实 final_state 透传，不用 GT）
  - session 级 GT = 该 session intents[] 里 status=resolved 的 intent 的最终 params 7字段，按 intent_key(app,service) 标识
  - 模型输出意图集合 == GT 意图集合（每个意图 7 字段全等）才算这 session 对（严格 JGA）
  - 多轮单意图(T2/T3)的补充/确认轮自然合并（同 intent_key 累积覆盖，最终只比一次）

用法:
  python session_eval.py [--retrieve on|off] [--n_demos 3] [--limit N] [--tag TAG]
"""
from __future__ import annotations

import argparse
import collections
import glob
import io
import json
import logging
import os
import sys
import time
from typing import Any

if not getattr(sys.stdout, "_dst_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stdout._dst_wrapped = True  # type: ignore[attr-defined]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from case_format import EVAL_SLOTS, flatten_params
from features import Features
from gt_build import CANCEL_GT, CORRECT_MARK, turn_gt
from pipeline import MultiIntentPipeline, _resolve_cancel_key, intent_key, load_config
from validator import validate

logger = logging.getLogger(__name__)

DATASET_DIR = os.environ.get("DST_DATASET", "data/dataset_5")


def load_test_sessions(split_path: str = "data/split.json") -> list[dict[str, Any]]:
    """按 split.json 的 test_sessions 从原始 dataset 加载完整 session。"""
    with open(split_path, "r", encoding="utf-8") as sf:
        split = json.load(sf)
    test_ids = set(split["test_sessions"])
    out: list[dict[str, Any]] = []
    for f in sorted(glob.glob(f"{DATASET_DIR}/sessions.*")):
        if "users" in os.path.basename(f):
            continue
        if f.endswith(".json"):
            with open(f, "r", encoding="utf-8") as jf:
                for s in json.load(jf):
                    if s["session_id"] in test_ids:
                        out.append(s)
        else:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        s = json.loads(line)
                        if s["session_id"] in test_ids:
                            out.append(s)
    out.sort(key=lambda s: s["session_id"])
    return out


def session_gt(session: dict[str, Any], mi_on: bool = False) -> dict[str, dict[str, Any]]:
    """session 级 GT: {intent_key(app,svc): {7字段扁平}}，只含 status=resolved 的 intent。"""
    out: dict[str, dict[str, Any]] = {}
    base_count: collections.Counter[str] = collections.Counter()
    for it in session.get("intents") or []:
        params = it.get("params") or {}
        flat = flatten_params(params)
        app = flat.get("application_name")
        svc = flat.get("service_name")
        k = intent_key(app, svc) if app else ""
        if app:
            base_count[k] += 1
            if mi_on and base_count[k] > 1:
                k = f"{k}#{base_count[k]}"
        else:
            continue
        if it.get("status") != "resolved":
            continue
        out[k] = {s: flat.get(s) for s in EVAL_SLOTS}
    return out


def session_turns(session: dict[str, Any]) -> list[dict[str, Any]]:
    """提取 session 的逐轮信息，按 turn 排序。每轮 = {turn, utterance, reference_time, prev_agent_reply}。"""
    ref = session.get("reference_time", "")
    events = sorted((session.get("event_sequence") or []), key=lambda e: e.get("turn", 0))
    turns: list[dict[str, Any]] = []
    prev_reply = None
    for ev in events:
        turns.append({
            "turn": ev.get("turn", 0),
            "utterance": (ev.get("user") or {}).get("utterance", ""),
            "reference_time": ref,
            "prev_agent_reply": prev_reply,
        })
        prev_reply = (ev.get("agent") or {}).get("utterance", "") or prev_reply
    return turns


def _merge_turn_finals(
    finals: dict[str, dict[str, Any]],
    prev_states: dict[str, dict[str, Any]],
    session_out: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """归并单轮模型输出 finals 至 session_out 与下一轮 prev_states。"""
    new_prev: dict[str, dict[str, Any]] = {}
    cancelled_keys: set[str] = set()
    consumed_keys: set[str] = set()
    for k, fin in finals.items():
        if k.startswith("__cancel__:"):
            ck = fin.get("cancel_key", "")
            if ck:
                cancelled_keys.add(ck)
            continue
        ck2 = fin.get("_consumed_key")
        if ck2:
            consumed_keys.add(ck2)
        fs = fin.get("final_state", {}) or {}
        new_prev[k] = fs
        flat = flatten_params(fs)
        session_out[k] = {
            "pred_flat": {s: flat.get(s) for s in EVAL_SLOTS},
            "validity": fin.get("validity"),
        }
    for ck in cancelled_keys | consumed_keys:
        session_out.pop(ck, None)
        new_prev.pop(ck, None)
    for k, v in prev_states.items():
        if k in cancelled_keys or k in consumed_keys:
            continue
        if k not in new_prev:
            new_prev[k] = v
            if k not in session_out:
                session_out[k] = {
                    "pred_flat": {s: flatten_params(v).get(s) for s in EVAL_SLOTS},
                    "validity": "unchanged",
                }
    return new_prev, cancelled_keys


def _derive_turn_status(
    st_on: bool,
    cf_on: bool,
    utt: str,
    finals: dict[str, dict[str, Any]],
    cancelled_keys: set[str],
    session_out: dict[str, dict[str, Any]],
    prev_statuses: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """旁路推导当前轮的意图状态机状态 (Cancelled > Confirmed > Ready > Collecting)。"""
    if not st_on:
        return {}, prev_statuses
    from intent_status import (
        STATUS_CANCELLED,
        derive_status,
        has_pending_choice,
        in_matrix,
        is_affirm,
    )
    from schema import KNOWN_APPLICATIONS

    turn_status: dict[str, str] = {}
    affirmed = bool(cf_on and is_affirm(utt))
    pchoice = has_pending_choice(utt)
    touched_keys = {k for k in finals if not k.startswith("__cancel__:")}
    for ck in cancelled_keys:
        if ck.split("/")[0] in KNOWN_APPLICATIONS:
            turn_status[ck] = STATUS_CANCELLED
    for k, v in session_out.items():
        flat = v.get("pred_flat", {})
        if not in_matrix(flat):
            continue
        turn_status[k] = derive_status(
            prev_statuses.get(k),
            flat,
            v.get("validity"),
            cancelled=False,
            affirmed=affirmed,
            touched=(k in touched_keys),
            pending_choice=pchoice,
        )
    next_statuses = {k: st for k, st in turn_status.items() if st != STATUS_CANCELLED}
    return turn_status, next_statuses


def _record_parsed_candidates(llm_raw: str, cand_book: Any) -> None:
    """将 LLM 输出的 candidate 段解析并记入候选账本。"""
    from candidate_book import Candidate, parse_candidates
    from case_format import flatten_params as _fp2
    from python_state import execute_program as _ep2

    for ck, cseg in parse_candidates(llm_raw):
        cand_flat: dict[str, Any] = {}
        try:
            cand_delta = _ep2(cseg, {}, timeout=1.5)
            cand_flat = _fp2(cand_delta)
        except Exception:
            pass
        cand_book.add(
            Candidate(key=ck, fields=cand_flat, record_ref=str(cand_flat.get("completed_at") or ""))
        )


def _evaluate_single_turn(
    pipe: MultiIntentPipeline,
    session_id: str,
    uid: str,
    ref: str,
    t: dict[str, Any],
    retrieve_on: bool,
    session_disambig: bool,
    id_on: bool,
    idbook: Any,
    cand_book: Any,
    mem_bridge: Any,
    user_context: dict[str, Any],
    prev_states: dict[str, dict[str, Any]],
    prev_statuses: dict[str, str],
    session_out: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, Any]]:
    """在 run_session 内执行单轮推理、候选旁路记账、状态合并与状态机推导。"""
    utt = t["utterance"]
    if id_on:
        pipe._id_states = {idbook.ensure(k): v for k, v in prev_states.items()}

    memory_hint = None
    mem_trace = None
    if mem_bridge is not None:
        user_context, memory_hint, mem_trace = mem_bridge.prepare_turn(
            user_id=uid, utterance=utt, reference_time=ref,
            prev_states=prev_states, turn_index=int(t["turn"]),
        )
    try:
        if retrieve_on:
            finals = pipe.run_multi(
                utt, ref, prev_states if prev_states else None, user_context,
                agent_reply=t.get("prev_agent_reply"),
                prev_statuses=prev_statuses if prev_statuses else None,
                session_disambig=session_disambig, memory_hint=memory_hint,
            )
        else:
            finals = _run_multi_no_retrieve(
                pipe, utt, ref, prev_states, user_context,
                agent_reply=t.get("prev_agent_reply"),
                prev_statuses=prev_statuses if prev_statuses else None,
            )
    except Exception as e:
        logger.error("session %s T%s error: %s", session_id, t["turn"], e)
        return prev_states, prev_statuses, {
            "turn": t["turn"], "preds": {}, "llm_raw": "", "status_pred": {},
        }

    llm_raw = getattr(pipe, "last_raw", "")
    _record_parsed_candidates(llm_raw, cand_book)
    prev_states, cancelled_keys = _merge_turn_finals(finals, prev_states, session_out)
    turn_pred = {
        k: v["pred_flat"] for k, v in session_out.items()
        if v.get("pred_flat", {}).get("application_name")
    }
    turn_status, prev_statuses = _derive_turn_status(
        pipe.features.status_tracking, pipe.features.confirm_rule,
        utt, finals, cancelled_keys, session_out, prev_statuses,
    )
    entry = {
        "turn": t["turn"],
        "preds": turn_pred,
        "llm_raw": llm_raw,
        "task_prompt": getattr(pipe, "last_task_prompt", ""),
        "status_pred": turn_status,
        "memory_trace": mem_trace,
    }
    return prev_states, prev_statuses, entry


def run_session(
    pipe: MultiIntentPipeline, session: dict[str, Any], retrieve_on: bool
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """逐轮 run_multi，prev_states 用上一轮真实 final_state 透传（不用 GT）。"""
    from candidate_book import CandidateBook
    from intent_ids import IntentIdBook

    ref = session.get("reference_time", "")
    prev_states: dict[str, dict[str, Any]] = {}
    session_out: dict[str, dict[str, Any]] = {}
    per_turn_pred: list[dict[str, Any]] = []

    id_on = pipe.features.intent_id_protocol
    idbook = IntentIdBook()
    if id_on:
        pipe._id_book = idbook  # type: ignore[attr-defined]
        pipe._id_states = {}  # type: ignore[attr-defined]

    turns = session_turns(session)
    uid = session.get("user_id", "")
    mem_bridge = getattr(pipe, "memory_bridge", None)
    if mem_bridge is not None:
        mem_bridge.reset_session(session.get("session_id"))
        user_context: dict[str, Any] = {}
    else:
        user_context = _load_user_context(uid)

    session_disambig = session.get("session_meta", {}).get("template_id") == "T3-3" or any(
        "Request_Disambiguation" in json.dumps(ev.get("agent", {}).get("action_types") or [])
        for ev in session.get("event_sequence", [])
    )
    pipe._session_is_t33 = session.get("session_meta", {}).get("template_id") == "T3-3"  # type: ignore[attr-defined]
    pipe._cand_listing = None  # type: ignore[attr-defined]
    cand_book = CandidateBook()
    prev_statuses: dict[str, str] = {}

    for t in turns:
        if not t["utterance"].strip():
            continue
        prev_states, prev_statuses, turn_entry = _evaluate_single_turn(
            pipe=pipe,
            session_id=str(session.get("session_id", "")),
            uid=uid,
            ref=ref,
            t=t,
            retrieve_on=retrieve_on,
            session_disambig=session_disambig,
            id_on=id_on,
            idbook=idbook,
            cand_book=cand_book,
            mem_bridge=mem_bridge,
            user_context=user_context,
            prev_states=prev_states,
            prev_statuses=prev_statuses,
            session_out=session_out,
        )
        per_turn_pred.append(turn_entry)
    return session_out, per_turn_pred


def _run_multi_no_retrieve(
    pipe: MultiIntentPipeline,
    utt: str,
    ref: str,
    prev_states: dict[str, dict[str, Any]],
    user_context: dict[str, Any],
    agent_reply: str | None = None,
    prev_statuses: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """retrieve=off 消融路径: 不检索 demonstrations, 其余与 run_multi 一致。"""
    from pipeline import _build_time_hint
    from prompt_builder import build_prompt
    from python_state import _match_prev_key as _mpk, execute_program_multi

    merged: dict[str, Any] = {}
    for v in prev_states.values():
        merged.update(v or {})
    ac_on = pipe.features.agent_context
    sp_on = pipe.features.status_prompt
    time_hint, _ = _build_time_hint(pipe.features, utt, ref, user_context)
    system, task_prompt = build_prompt(
        utt,
        ref,
        merged,
        [],
        user_context,
        agent_reply=agent_reply if ac_on else None,
        prev_statuses=prev_statuses if sp_on else None,
        time_hint=time_hint,
        time_anchor_on=pipe.features.time_anchor,
    )
    raw = pipe.llm.gen_raw_code(system, task_prompt)
    segs = execute_program_multi(raw, prev_states if prev_states else None)
    finals: dict[str, dict[str, Any]] = {}
    for key, delta in segs:
        if key == "__cancel__":
            ck = delta if isinstance(delta, str) else ""
            ck_full = _resolve_cancel_key(ck, prev_states)
            finals[f"__cancel__:{ck_full}"] = {"__cancel__": True, "cancel_key": ck_full}
            continue
        mkey = _mpk(key, prev_states)
        prev = prev_states[mkey] if mkey else {}
        fin = validate({"intent": "unknown", "params": delta}, ref, prev)
        fflat = flatten_params(fin.get("final_state") or {})
        k = intent_key(fflat.get("application_name"), fflat.get("service_name")) or key
        if mkey and mkey != k:
            fin["_consumed_key"] = mkey
        finals[k] = fin
    return finals


def _match_prev(key: str, prev_states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not key or key == "none":
        return {}
    if key in prev_states:
        return prev_states[key]
    app = key.split("/")[0]
    for k, v in prev_states.items():
        if k == app or k.startswith(app + "/"):
            return v
    return {}


_UCACHE: dict[str, dict[str, Any]] = {}


def _load_user_context(uid: str) -> dict[str, Any]:
    if not uid:
        return {}
    if uid in _UCACHE:
        return _UCACHE[uid]
    cands = sorted(glob.glob(f"{DATASET_DIR}/users*.jsonl"))
    uc: dict[str, Any] = {}
    for c in cands:
        with open(c, "r", encoding="utf-8") as cf:
            for line in cf:
                line = line.strip()
                if not line:
                    continue
                u = json.loads(line)
                if u.get("user_id") == uid:
                    profile = u.get("profile", [])
                    records = u.get("records", [])
                    rec_sorted = sorted(records, key=lambda r: r.get("completed_at", ""), reverse=True)
                    uc = {
                        "profile": {p["key"]: p["value"] for p in profile},
                        "recent_records": [
                            {
                                "application_name": r.get("application_name"),
                                "service_name": r.get("service_name"),
                                "resolution": (r.get("resolution") or {}).get("min_value"),
                                "rtt": (r.get("rtt") or {}).get("max_value"),
                                "start_timestamp": (r.get("timestamp") or {}).get("start_timestamp"),
                                "end_timestamp": (r.get("timestamp") or {}).get("end_timestamp"),
                                "duration": (r.get("timestamp") or {}).get("duration"),
                                "completed_at": r.get("completed_at"),
                            }
                            for r in rec_sorted
                        ],
                    }
                    break
        if uc:
            break
    _UCACHE[uid] = uc
    return uc


def judge_session(gt: dict[str, dict[str, Any]], pred: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    """session 级 JGA: 意图集合严格全等(每意图7字段全等)才算对。"""
    gt_keys = set(gt.keys())
    pred_keys = {
        k for k, v in pred.items() if v.get("pred_flat", {}).get("application_name")
    }
    if gt_keys != pred_keys:
        missing = gt_keys - pred_keys
        extra = pred_keys - gt_keys
        return False, f"intent_set_mismatch missing={sorted(missing)} extra={sorted(extra)}"
    wrong = []
    for k in gt_keys:
        g = gt[k]
        p = pred[k]["pred_flat"]
        for s in EVAL_SLOTS:
            if (g.get(s) or None) != (p.get(s) or None):
                wrong.append(f"{k}.{s}")
                break
    if wrong:
        return False, "field_wrong:" + ",".join(wrong[:5])
    return True, "ok_session"


def judge_turn(
    turn_gts: list[dict[str, Any]], per_turn_pred: list[dict[str, Any]]
) -> tuple[int, int, list[dict[str, Any]]]:
    """turn 级 JGA: 对每个 (turn, intent_key) case，pred 截止该轮该意图累积态 == gt_flat 才算对。"""
    pred_by_turn = {p["turn"]: p["preds"] for p in per_turn_pred}
    raw_by_turn = {p["turn"]: p.get("llm_raw", "") for p in per_turn_pred}
    n_match = 0
    n_total = 0
    detail: list[dict[str, Any]] = []
    for g in turn_gts:
        n_total += 1
        turn = g["turn"]
        k = g["intent_key"]
        preds = pred_by_turn.get(turn, {})
        if g["gt_flat"] == CANCEL_GT:
            ok = k not in preds
            if ok:
                n_match += 1
            detail.append({
                "turn": turn, "intent_key": k, "gt": {"__cancelled__": True},
                "pred": preds.get(k) or {}, "match": ok,
                "wrong": [] if ok else ["__cancel__"],
                "missed_intent": False, "cancel_case": True,
                "llm_raw": raw_by_turn.get(turn, ""),
            })
            continue
        if isinstance(g["gt_flat"], dict) and CORRECT_MARK in g["gt_flat"]:
            exempt = set(g["gt_flat"][CORRECT_MARK])
            real_gt = g["gt_flat"]["__gt__"]
            pred_flat = preds.get(k) or {}
            if not pred_flat:
                ok, wrong = True, []
            else:
                wrong = [
                    s for s in EVAL_SLOTS
                    if s not in exempt and (real_gt.get(s) or None) != (pred_flat.get(s) or None)
                ]
                ok = not wrong
            if ok:
                n_match += 1
            detail.append({
                "turn": turn, "intent_key": k,
                "gt": real_gt, "correction_exempt": sorted(exempt),
                "pred": pred_flat, "match": ok,
                "wrong": wrong, "missed_intent": False,
                "correction_case": True,
                "llm_raw": raw_by_turn.get(turn, ""),
            })
            continue
        pred_flat = preds.get(k, {})
        ok = True
        wrong = []
        for s in EVAL_SLOTS:
            if (g["gt_flat"].get(s) or None) != (pred_flat.get(s) or None):
                ok = False
                wrong.append(s)
                break
        if ok:
            n_match += 1
        detail.append({
            "turn": turn, "intent_key": k, "gt": g["gt_flat"],
            "pred": pred_flat, "match": ok,
            "wrong": wrong, "missed_intent": k not in preds,
            "gt_derived": g.get("gt_derived") or [],
            "llm_raw": raw_by_turn.get(turn, ""),
        })
    return n_match, n_total, detail


def _parse_eval_cli_args() -> argparse.Namespace:
    """解析 session_eval 命令行参数。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieve", default="on", choices=["on", "off"])
    ap.add_argument("--n_demos", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="限制 session 数")
    ap.add_argument("--config", default="configs/config_d5_qwen_plus_qmap.json", help="config 路径")
    ap.add_argument("--split", default="data/split_d5.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--err_report", action="store_true", help="评估结束后自动生成错误样本 HTML 报告")
    ap.add_argument("--include_noise", action="store_true", help="不剔除噪声清单里的噪声 session")
    ap.add_argument("--noise_file", default="data/_noise_exclude_d5_scan.json", help="噪声清单路径")
    ap.add_argument("--no_cancel_credit", action="store_true", help="关闭方案 b 取消计分口径")
    ap.add_argument("--no_correction_credit", action="store_true", help="关闭改口错误值轮豁免口径")
    ap.add_argument("--no_gt_derive", action="store_true", help="关闭 turn 级 GT 时间三字段知二推一口径")
    return ap.parse_args()


def _classify_turn_detail_errors(
    tdetail: list[dict[str, Any]], by_turn_err: collections.Counter[str]
) -> None:
    """将单会话的 turn 级错误详情归入各错误计数器。"""
    for d in tdetail:
        if d["match"]:
            continue
        if d.get("cancel_case"):
            by_turn_err["cancel_wrong"] += 1
        elif d.get("correction_case"):
            by_turn_err["correction_wrong"] += 1
        elif d["missed_intent"]:
            by_turn_err["missed_intent"] += 1
        elif any(x in d["wrong"] for x in ("start_timestamp", "end_timestamp", "duration")):
            by_turn_err["time_wrong"] += 1
        elif any(x in d["wrong"] for x in ("application_name", "service_name")):
            by_turn_err["appsvc_wrong"] += 1
        else:
            by_turn_err["value_wrong"] += 1


def _evaluate_session_status_trajectory(
    s: dict[str, Any],
    per_turn_pred: list[dict[str, Any]],
    mi_on: bool,
    by_gstatus: collections.Counter[str],
    by_gstatus_match: collections.Counter[str],
    by_status_conf: collections.Counter[str],
) -> tuple[int, int, list[dict[str, Any]]]:
    """评估单会话的状态机轨迹并更新全局状态统计计数器。"""
    from intent_status import build_gt_status_trajectory, judge_status

    gt_traj = build_gt_status_trajectory(s, mi_on=mi_on)
    pred_traj = {p["turn"]: p.get("status_pred", {}) for p in per_turn_pred}
    smatch, stotal, sdetail = judge_status(gt_traj, pred_traj)
    for d in sdetail:
        by_gstatus[d["gt_status"]] += 1
        if d["match"]:
            by_gstatus_match[d["gt_status"]] += 1
        else:
            by_status_conf[f"{d['gt_status']}->{d['pred_status']}"] += 1
    return smatch, stotal, sdetail


def _run_batch_evaluation(
    pipe: MultiIntentPipeline,
    sessions: list[dict[str, Any]],
    args: argparse.Namespace,
    feats: Features,
) -> dict[str, Any]:
    """批量运行测试集 sessions 并统计 session / turn / 状态机各项指标。"""
    mi_on = feats.multi_instance_key
    st_on = feats.status_tracking
    results: list[dict[str, Any]] = []
    n_sess_match = n_turn_match = n_turn_total = n_status_match = n_status_total = 0
    by_gstatus: collections.Counter[str] = collections.Counter()
    by_gstatus_match: collections.Counter[str] = collections.Counter()
    by_status_conf: collections.Counter[str] = collections.Counter()
    by_tpl: collections.Counter[str] = collections.Counter()
    by_tpl_sess_match: collections.Counter[str] = collections.Counter()
    by_tpl_turn_match: collections.Counter[str] = collections.Counter()
    by_tpl_turn_total: collections.Counter[str] = collections.Counter()
    by_err: collections.Counter[str] = collections.Counter()
    by_turn_err: collections.Counter[str] = collections.Counter()
    t0 = time.time()

    for si, s in enumerate(sessions):
        sid = s["session_id"]
        tpl = s["session_meta"]["template_id"]
        gt = session_gt(s, mi_on=mi_on)
        pred, per_turn_pred = run_session(pipe, s, retrieve_on=(args.retrieve == "on"))

        sok, setype = judge_session(gt, pred)
        if sok:
            n_sess_match += 1
            by_tpl_sess_match[tpl] += 1
        else:
            by_err[setype.split(":")[0].split("=")[0][:20]] += 1
        by_tpl[tpl] += 1

        tgts = turn_gt(
            s, cancel_credit=not args.no_cancel_credit,
            correction_credit=not args.no_correction_credit,
            gt_derive=not args.no_gt_derive, mi_on=mi_on,
        )
        tmatch, ttotal, tdetail = judge_turn(tgts, per_turn_pred)
        n_turn_match += tmatch
        n_turn_total += ttotal
        by_tpl_turn_match[tpl] += tmatch
        by_tpl_turn_total[tpl] += ttotal
        _classify_turn_detail_errors(tdetail, by_turn_err)

        sdetail: list[dict[str, Any]] = []
        smatch = stotal = 0
        if st_on:
            smatch, stotal, sdetail = _evaluate_session_status_trajectory(
                s, per_turn_pred, mi_on, by_gstatus, by_gstatus_match, by_status_conf
            )
            n_status_match += smatch
            n_status_total += stotal

        results.append({
            "session_id": sid, "template": tpl, "gt_intents": gt,
            "pred_intents": {k: v["pred_flat"] for k, v in pred.items()},
            "session_match": sok, "session_error_type": setype,
            "turn_match": tmatch, "turn_total": ttotal, "turn_detail": tdetail,
            "turn_outputs": [
                {
                    "turn": p["turn"], "llm_raw": p.get("llm_raw", ""),
                    "task_prompt": p.get("task_prompt", ""), "preds": p.get("preds", {}),
                }
                for p in per_turn_pred
            ],
            "status_match": smatch, "status_total": stotal, "status_detail": sdetail,
        })
        if (si + 1) % 5 == 0 or si == len(sessions) - 1:
            logger.info(
                "  [%d/%d] session=%d(%.3f) turn=%d/%d(%.3f) elapsed=%.0fs",
                si + 1, len(sessions), n_sess_match, n_sess_match / (si + 1),
                n_turn_match, n_turn_total, n_turn_match / max(n_turn_total, 1), time.time() - t0,
            )

    return {
        "results": results, "n_sessions": len(sessions), "n_sess_match": n_sess_match,
        "n_turn_match": n_turn_match, "n_turn_total": n_turn_total,
        "n_status_match": n_status_match, "n_status_total": n_status_total,
        "by_gstatus": by_gstatus, "by_gstatus_match": by_gstatus_match,
        "by_status_conf": by_status_conf, "by_tpl": by_tpl,
        "by_tpl_sess_match": by_tpl_sess_match, "by_tpl_turn_match": by_tpl_turn_match,
        "by_tpl_turn_total": by_tpl_turn_total, "by_err": by_err, "by_turn_err": by_turn_err,
    }


def _log_batch_eval_summary(batch_stats: dict[str, Any], st_on: bool) -> tuple[float, float]:
    """打印评测指标汇总表并返回 (sess_rate, turn_rate)。"""
    n_sessions = batch_stats["n_sessions"]
    n_sess_match = batch_stats["n_sess_match"]
    n_turn_match = batch_stats["n_turn_match"]
    n_turn_total = batch_stats["n_turn_total"]
    n_status_match = batch_stats["n_status_match"]
    n_status_total = batch_stats["n_status_total"]
    by_gstatus = batch_stats["by_gstatus"]
    by_gstatus_match = batch_stats["by_gstatus_match"]
    by_tpl = batch_stats["by_tpl"]
    by_tpl_sess_match = batch_stats["by_tpl_sess_match"]
    by_tpl_turn_match = batch_stats["by_tpl_turn_match"]
    by_tpl_turn_total = batch_stats["by_tpl_turn_total"]

    sess_rate = n_sess_match / n_sessions if n_sessions else 0.0
    turn_rate = n_turn_match / n_turn_total if n_turn_total else 0.0
    logger.info("=" * 60)
    logger.info("session 级 JGA: %d/%d = %.4f", n_sess_match, n_sessions, sess_rate)
    logger.info("turn 级 JGA (真实透传): %d/%d = %.4f", n_turn_match, n_turn_total, turn_rate)
    if st_on:
        sr = n_status_match / n_status_total if n_status_total else 0.0
        logger.info("意图状态机准确率: %d/%d = %.4f", n_status_match, n_status_total, sr)
        for gst in ("Collecting", "Ready", "Confirmed", "Cancelled"):
            n_g = by_gstatus.get(gst, 0)
            if n_g:
                m_g = by_gstatus_match.get(gst, 0)
                logger.info("  %s: %d/%d = %.3f", gst, m_g, n_g, m_g / n_g)
    logger.info("=" * 60)
    logger.info("按 template (session / turn):")
    for tpl in sorted(by_tpl):
        sm, st = by_tpl_sess_match[tpl], by_tpl[tpl]
        tm, tt = by_tpl_turn_match[tpl], by_tpl_turn_total[tpl]
        logger.info("  %s: session %d/%d=%.3f  turn %d/%d=%.3f", tpl, sm, st, sm / st, tm, tt, tm / max(tt, 1))
    logger.info("[session] 错误类型分布: %s", dict(batch_stats["by_err"].most_common()))
    logger.info("[turn] 错误类型分布: %s", dict(batch_stats["by_turn_err"].most_common()))
    return sess_rate, turn_rate


def _summarize_session_outcome(
    batch_stats: dict[str, Any],
    args: argparse.Namespace,
    cfg: dict[str, Any],
    feats: Features,
) -> str:
    """输出评测汇总日志并将逐会话结果与摘要写入磁盘。"""
    st_on = feats.status_tracking
    sess_rate, turn_rate = _log_batch_eval_summary(batch_stats, st_on)
    n_status_match = batch_stats["n_status_match"]
    n_status_total = batch_stats["n_status_total"]
    by_gstatus = batch_stats["by_gstatus"]
    by_gstatus_match = batch_stats["by_gstatus_match"]
    by_tpl = batch_stats["by_tpl"]

    out = args.out or f"data/evals/eval_dual_{args.tag or 'run'}.jsonl"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in batch_stats["results"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "n_sessions": batch_stats["n_sessions"],
        "session_match": batch_stats["n_sess_match"],
        "session_rate": sess_rate,
        "turn_total": batch_stats["n_turn_total"],
        "turn_match": batch_stats["n_turn_match"],
        "turn_rate": turn_rate,
        "status_tracking": st_on,
        "status_total": n_status_total,
        "status_match": n_status_match,
        "status_rate": (n_status_match / n_status_total) if n_status_total else None,
        "status_by_gt": {
            g: {"n": by_gstatus.get(g, 0), "match": by_gstatus_match.get(g, 0)}
            for g in ("Collecting", "Ready", "Confirmed", "Cancelled")
            if by_gstatus.get(g)
        },
        "status_confusion": dict(batch_stats["by_status_conf"]),
        "switches": feats.switches(),
        "retrieve": args.retrieve,
        "top_k": cfg["retriever"]["top_k"],
        "cancel_credit": not args.no_cancel_credit,
        "correction_credit": not args.no_correction_credit,
        "gt_derive": not args.no_gt_derive,
        "multi_instance_key": feats.multi_instance_key,
        "by_tpl": {
            tpl: {
                "sess_m": batch_stats["by_tpl_sess_match"][tpl],
                "sess_t": by_tpl[tpl],
                "turn_m": batch_stats["by_tpl_turn_match"][tpl],
                "turn_t": batch_stats["by_tpl_turn_total"][tpl],
            }
            for tpl in sorted(by_tpl)
        },
        "by_session_err": dict(batch_stats["by_err"]),
        "by_turn_err": dict(batch_stats["by_turn_err"]),
    }
    summary_path = out.replace(".jsonl", "_summary.json")
    with open(summary_path, "w", encoding="utf-8") as sf:
        json.dump(summary, sf, ensure_ascii=False, indent=2)
    logger.info("详细 -> %s  摘要 -> %s", out, summary_path)
    return out


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_eval_cli_args()
    cfg = load_config(args.config)
    if args.n_demos:
        cfg["retriever"]["top_k"] = args.n_demos
    feats = Features.from_config(cfg)
    mi_on = feats.multi_instance_key
    logger.info(
        "config: retrieve=%s top_k=%s multi_instance_key=%s",
        args.retrieve,
        cfg["retriever"]["top_k"],
        mi_on,
    )

    sessions = load_test_sessions(args.split)
    if not args.include_noise and os.path.exists(args.noise_file):
        with open(args.noise_file, encoding="utf-8") as nf:
            noise = set(json.load(nf)["session_ids"])
        before = len(sessions)
        sessions = [s for s in sessions if s["session_id"] not in noise]
        logger.info(
            "噪声剔除: %d -> %d sessions (剔除 %d 个, %s)",
            before,
            len(sessions),
            before - len(sessions),
            args.noise_file,
        )
    if args.limit:
        sessions = sessions[: args.limit]
    logger.info("评估 %d 个 session (逐轮真实透传, session+turn 双口径) ...", len(sessions))

    pipe = MultiIntentPipeline(cfg)
    batch_stats = _run_batch_evaluation(pipe, sessions, args, feats)
    out = _summarize_session_outcome(batch_stats, args, cfg, feats)

    if args.err_report:
        import error_report
        error_report.generate(
            out,
            DATASET_DIR,
            os.path.splitext(out)[0] + "_errors.html",
            noise_path=args.noise_file,
        )


if __name__ == "__main__":
    main()
