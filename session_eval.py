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
import os
import sys
import time

if not getattr(sys.stdout, "_dst_wrapped", False):  # 幂等: 重复 import (如 error_report 内引用) 不再重包, 防旧 wrapper 被 GC 关流
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stdout._dst_wrapped = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from case_format import flatten_params, EVAL_SLOTS, extract_turn_cases
from features import Features
from gt_build import (CANCEL_GT, CORRECT_MARK, cancel_turn_map, correction_turn_map,
                      turn_gt)
from pipeline import MultiIntentPipeline, intent_key, load_config, _resolve_cancel_key
from validator import validate

DATASET_DIR = os.environ.get("DST_DATASET", "data/dataset_5")


def load_test_sessions(split_path: str = "data/split.json") -> list[dict]:
    """按 split.json 的 test_sessions 从原始 dataset 加载完整 session。"""
    split = json.load(open(split_path, "r", encoding="utf-8"))
    test_ids = set(split["test_sessions"])
    out = []
    for f in sorted(glob.glob(f"{DATASET_DIR}/sessions.*")):
        if "users" in os.path.basename(f):
            continue
        if f.endswith(".json"):
            for s in json.load(open(f, "r", encoding="utf-8")):
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


def session_gt(session: dict, mi_on: bool = False) -> dict[str, dict]:
    """session 级 GT: {intent_key(app,svc): {7字段扁平}}，只含 status=resolved 的 intent。

    域外/取消/拒绝(status != resolved)的 intent 不算进 GT。
    若 intent 的 application_name 缺失（域外非保障），跳过。

    mi_on (v26.3 P3 multi_instance_key): 同 app/svc 多意图实例化——第 N 条(N>1)的 key
    加 "#N" 后缀。编号基于【全量 intents[]】(含 cancelled/rejected)中该 base 的出现序,
    与 pred 侧 idbook.alloc 序对齐(S-000225: I1 cancelled 不产条目但占 #1, I2=#2)。
    """
    out: dict[str, dict] = {}
    base_count: collections.Counter = collections.Counter()
    for it in (session.get("intents") or []):
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
            continue  # 无 app 的非保障意图，不算 GT
        if it.get("status") != "resolved":
            continue
        out[k] = {s: flat.get(s) for s in EVAL_SLOTS}
    return out


def session_turns(session: dict) -> list[dict]:
    """提取 session 的逐轮信息，按 turn 排序。每轮 = {turn, utterance, reference_time, prev_agent_reply}。

    一个 session 的 event_sequence 里每条是一个 turn 的 user utterance。
    多意图在同一 turn，run_multi 一次调用处理。
    prev_agent_reply = 上一轮 agent 的话术 (对话上下文的输入侧信息, 非状态 GT;
    config agent_context.enabled 时才进 prompt)。
    """
    ref = session.get("reference_time", "")
    events = sorted((session.get("event_sequence") or []), key=lambda e: e.get("turn", 0))
    turns = []
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


def run_session(pipe: MultiIntentPipeline, session: dict, retrieve_on: bool):
    """逐轮 run_multi，prev_states 用上一轮真实 final_state 透传（不用 GT）。

    返回 (session_out, per_turn_pred):
      session_out: {intent_key: {pred_flat, validity}} —— session 级最终意图集合
      per_turn_pred: [{turn, preds: {intent_key: pred_flat}}] —— 每轮截止的累积态（turn 级比对用）
    """
    ref = session.get("reference_time", "")
    prev_states: dict[str, dict] = {}  # intent_key -> 嵌套 final_state（上一轮累积，真实透传）
    session_out: dict[str, dict] = {}
    per_turn_pred: list[dict] = []
    # v26: intent-id 协议 (config intent_id_protocol.enabled)。
    # idbook 维护 id<->内容key 映射; id 模式下传给 pipeline 的 prev_states 以 id 为键,
    # pipeline 返回的 results 也以内容 key 为键(带 _iid/_consumed_key 元数据), 透传逻辑不变。
    id_on = pipe.features.intent_id_protocol
    from intent_ids import IntentIdBook
    idbook = IntentIdBook()
    if id_on:
        pipe._id_book = idbook
        pipe._id_states = {}

    turns = session_turns(session)
    uid = session.get("user_id", "")
    mem_bridge = getattr(pipe, "memory_bridge", None)
    if mem_bridge is not None:
        mem_bridge.reset_session(session.get("session_id"))
        user_context = {}
    else:
        user_context = _load_user_context(uid)

    # v27.1: 消歧类会话信号 (T3-3 模板或事件序列含 Request_Disambiguation) → record_selector 悬空口径生效域
    session_disambig = session.get("session_meta", {}).get("template_id") == "T3-3" or any(
        "Request_Disambiguation" in json.dumps(ev.get("agent", {}).get("action_types") or [])
        for ev in session.get("event_sequence", []))
    # v27.2: 候选模式信号域收窄到 T3-3 模板 (Request_Disambiguation 会误扩到 T2-2/T2-3, 回放 S-000116 实证)
    pipe._session_is_t33 = session.get("session_meta", {}).get("template_id") == "T3-3"
    pipe._cand_listing = None  # 候选列举存档按 session 重置 (防跨 session 残留)
    # v27.2: 候选账本 (candidate 模式旁路, 不入 JGA; 供 select 落地与报告可视化)
    from candidate_book import CandidateBook, parse_candidates
    cand_book = CandidateBook()

    # 意图状态机 (独立消融开关, 纯旁路不改 JGA 主流程):
    #   status_tracking.enabled — 推导 pred 状态轨迹
    #   confirm_rule.enabled    — Affirm 规则识别 (命中才产生 Confirmed)
    st_on = pipe.features.status_tracking
    cf_on = pipe.features.confirm_rule
    # v26.3 P3: 多实例 key (联动规则在 Features 唯一实现)
    mi_on = pipe.features.multi_instance_key
    prev_statuses: dict[str, str] = {}

    for t in turns:
        utt = t["utterance"]
        if not utt.strip():
            continue
        if id_on:
            # id 模式: pipeline 的 prev_states 以 id 为键; 内容键 -> id
            pipe._id_states = {}
            for k, v in prev_states.items():
                iid = idbook.ensure(k)
                pipe._id_states[iid] = v
        memory_hint = None
        mem_trace = None
        if mem_bridge is not None:
            user_context, memory_hint, mem_trace = mem_bridge.prepare_turn(
                user_id=uid,
                utterance=utt,
                reference_time=ref,
                prev_states=prev_states,
                turn_index=int(t["turn"]),
            )
        try:
            if retrieve_on:
                finals = pipe.run_multi(utt, ref, prev_states if prev_states else None, user_context,
                                        agent_reply=t.get("prev_agent_reply"),
                                        prev_statuses=prev_statuses if prev_statuses else None,
                                        session_disambig=session_disambig,
                                        memory_hint=memory_hint)
            else:
                finals = _run_multi_no_retrieve(pipe, utt, ref, prev_states, user_context,
                                                agent_reply=t.get("prev_agent_reply"),
                                                prev_statuses=prev_statuses if prev_statuses else None)
        except Exception as e:
            print(f"  [!] session {session['session_id']} T{t['turn']} error: {e}")
            per_turn_pred.append({"turn": t["turn"], "preds": {}, "llm_raw": "", "status_pred": {}})
            continue
        llm_raw = getattr(pipe, "last_raw", "")
        # v27.2: 记账本轮 candidate 段 (旁路)
        for ck, cseg in parse_candidates(llm_raw):
            from case_format import flatten_params as _fp2
            cand_flat = {}
            try:
                from python_state import execute_program as _ep2
                cand_delta = _ep2(cseg, {}, timeout=1.5)
                cand_flat = _fp2(cand_delta)
            except Exception:
                pass
            from candidate_book import Candidate
            cand_book.add(Candidate(key=ck, fields=cand_flat,
                                    record_ref=str(cand_flat.get('completed_at') or '')))
        # 透传: 下一轮的 prev_states = 本轮各 intent 的 final_state（真实输出）
        new_prev: dict[str, dict] = {}
        cancelled_keys: set = set()
        consumed_keys: set = set()  # v25.2: 被归并/改名的 prev key, 不再沿用
        for k, fin in finals.items():
            # 取消信号: 从 session_out 和 prev_states 移除被取消的意图
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
        # 执行取消: 移除被取消的意图; 执行归并: 移除被改名/被吃掉的旧 key (v25.2)
        for ck in cancelled_keys | consumed_keys:
            session_out.pop(ck, None)
            new_prev.pop(ck, None)
        # 保留上轮未被本轮触及且未被取消/归并的 intent（状态沿用）
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
        prev_states = new_prev
        # 记录本轮截止的累积态（所有当前在状态里的意图）
        turn_pred = {k: v["pred_flat"] for k, v in session_out.items()
                     if v.get("pred_flat", {}).get("application_name")}

        # ---- 意图状态机推导 (旁路): Cancelled > Confirmed > Ready > Collecting ----
        turn_status: dict[str, str] = {}
        # v26.3 P3 mi_on: _iid -> 本轮 pred key 映射 (与 GT 状态机实例 key 对齐)
        iid_to_key: dict[str, str] = {}
        if mi_on:
            for k, fin in finals.items():
                if k.startswith("__cancel__:"):
                    continue
                _iid = fin.get("_iid") if isinstance(fin, dict) else None
                if _iid:
                    iid_to_key[_iid] = k
        if st_on:
            from intent_status import (derive_status, is_affirm, in_matrix,
                                       has_pending_choice, STATUS_CANCELLED)
            from schema import KNOWN_APPLICATIONS
            affirmed = bool(cf_on and is_affirm(utt))
            pchoice = has_pending_choice(utt)
            touched_keys = {k for k in finals if not k.startswith("__cancel__:")}
            for ck in cancelled_keys:
                if ck.split("/")[0] in KNOWN_APPLICATIONS:
                    turn_status[ck] = STATUS_CANCELLED
            for k, v in session_out.items():
                flat = v.get("pred_flat", {})
                if not in_matrix(flat):
                    continue  # 域外意图不进状态机 (与 JGA/GT 口径一致)
                turn_status[k] = derive_status(
                    prev_statuses.get(k), flat, v.get("validity"),
                    cancelled=False, affirmed=affirmed,
                    touched=(k in touched_keys),
                    pending_choice=pchoice,
                )
            prev_statuses = {k: st for k, st in turn_status.items()
                             if st != STATUS_CANCELLED}
        per_turn_pred.append({"turn": t["turn"], "preds": turn_pred, "llm_raw": llm_raw,
                              "task_prompt": getattr(pipe, "last_task_prompt", ""),
                              "status_pred": turn_status,
                              "memory_trace": mem_trace})
    return session_out, per_turn_pred


def _run_multi_no_retrieve(pipe, utt, ref, prev_states, user_context,
                           agent_reply=None, prev_statuses=None):
    """retrieve=off 消融路径: 不检索 demonstrations, 其余与 run_multi 一致。"""
    from prompt_builder import build_prompt
    from python_state import execute_program_multi
    merged = {}
    for v in prev_states.values():
        merged.update(v or {})
    ac_on = pipe.features.agent_context
    sp_on = pipe.features.status_prompt
    # v27: 与 run_multi 一致的时间注入
    from pipeline import _build_time_hint
    time_hint = _build_time_hint(pipe.features, utt, ref, user_context)
    system, task_prompt = build_prompt(utt, ref, merged, [], user_context,
                                       agent_reply=agent_reply if ac_on else None,
                                       prev_statuses=prev_statuses if sp_on else None,
                                       time_hint=time_hint,
                                       time_anchor_on=pipe.features.time_anchor)
    raw = pipe.llm.gen_raw_code(system, task_prompt)
    segs = execute_program_multi(raw, prev_states if prev_states else None)
    finals = {}
    for key, delta in segs:
        if key == "__cancel__":
            ck = delta if isinstance(delta, str) else ""
            ck_full = _resolve_cancel_key(ck, prev_states)
            finals[f"__cancel__:{ck_full}"] = {"__cancel__": True, "cancel_key": ck_full}
            continue
        from python_state import _match_prev_key as _mpk
        mkey = _mpk(key, prev_states)
        prev = prev_states[mkey] if mkey else {}
        fin = validate({"intent": "unknown", "params": delta}, ref, prev)
        # v25.2: key 从合并后 final 推导 (与 run_multi 一致)
        fflat = flatten_params(fin.get("final_state") or {})
        k = intent_key(fflat.get("application_name"), fflat.get("service_name")) or key
        if mkey and mkey != k:
            fin["_consumed_key"] = mkey
        finals[k] = fin
    return finals


def _match_prev(key, prev_states):
    if not key or key == "none":
        return {}
    if key in prev_states:
        return prev_states[key]
    app = key.split("/")[0]
    for k, v in prev_states.items():
        if k == app or k.startswith(app + "/"):
            return v
    return {}


_UCACHE: dict = {}


def _load_user_context(uid: str) -> dict:
    if not uid:
        return {}
    if uid in _UCACHE:
        return _UCACHE[uid]
    cands = sorted(glob.glob(f"{DATASET_DIR}/users*.jsonl"))
    uc = {}
    for c in cands:
        for line in open(c, "r", encoding="utf-8"):
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


def judge_session(gt: dict[str, dict], pred: dict[str, dict]) -> tuple[bool, str]:
    """session 级 JGA: 意图集合严格全等(每意图7字段全等)才算对。"""
    gt_keys = set(gt.keys())
    pred_keys = {k for k, v in pred.items()
                 if v.get("pred_flat", {}).get("application_name")}  # 空意图不算 pred
    if gt_keys != pred_keys:
        missing = gt_keys - pred_keys
        extra = pred_keys - gt_keys
        return False, f"intent_set_mismatch missing={sorted(missing)} extra={sorted(extra)}"
    # 每个意图 7 字段全等
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




def judge_turn(turn_gts: list[dict], per_turn_pred: list[dict]) -> tuple[int, int, list[dict]]:
    """turn 级 JGA: 对每个 (turn, intent_key) case，pred 截止该轮该意图累积态 == gt_flat 才算对。
    误差累积透传（prev_states 用真实输出）。

    返回 (n_match, n_total, detail)。
    """
    # 按 turn 索引 per_turn_pred
    pred_by_turn = {p["turn"]: p["preds"] for p in per_turn_pred}
    raw_by_turn = {p["turn"]: p.get("llm_raw", "") for p in per_turn_pred}
    n_match = 0
    n_total = 0
    detail = []
    for g in turn_gts:
        n_total += 1
        turn = g["turn"]
        k = g["intent_key"]
        preds = pred_by_turn.get(turn, {})
        # 方案 b 取消计分 case: GT 要求此轮该意图已被移除
        if g["gt_flat"] == CANCEL_GT:
            ok = k not in preds
            if ok:
                n_match += 1
            detail.append({"turn": turn, "intent_key": k, "gt": {"__cancelled__": True},
                           "pred": preds.get(k) or {}, "match": ok,
                           "wrong": [] if ok else ["__cancel__"],
                           "missed_intent": False, "cancel_case": True,
                           "llm_raw": raw_by_turn.get(turn, "")})
            continue
        # correction_credit 改口错误值轮 case: 拒收(pred 空)或记字面值均算对, 其余字段照常比对
        if isinstance(g["gt_flat"], dict) and CORRECT_MARK in g["gt_flat"]:
            exempt = set(g["gt_flat"][CORRECT_MARK])
            real_gt = g["gt_flat"]["__gt__"]
            pred_flat = preds.get(k) or {}
            if not pred_flat:
                ok, wrong = True, []
            else:
                wrong = [s for s in EVAL_SLOTS
                         if s not in exempt
                         and (real_gt.get(s) or None) != (pred_flat.get(s) or None)]
                ok = not wrong
            if ok:
                n_match += 1
            detail.append({"turn": turn, "intent_key": k,
                           "gt": real_gt, "correction_exempt": sorted(exempt),
                           "pred": pred_flat, "match": ok,
                           "wrong": wrong, "missed_intent": False,
                           "correction_case": True,
                           "llm_raw": raw_by_turn.get(turn, "")})
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
        detail.append({"turn": turn, "intent_key": k, "gt": g["gt_flat"],
                       "pred": pred_flat, "match": ok,
                       "wrong": wrong, "missed_intent": k not in preds,
                       "gt_derived": g.get("gt_derived") or [],
                       "llm_raw": raw_by_turn.get(turn, "")})
    return n_match, n_total, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieve", default="on", choices=["on", "off"])
    ap.add_argument("--n_demos", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="限制 session 数")
    ap.add_argument("--config", default="configs/config_d5_qwen_plus_qmap.json", help="config 路径")
    ap.add_argument("--split", default="data/split_d5.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--err_report", action="store_true",
                    help="评估结束后自动生成错误样本 HTML 报告 (error_report.py)")
    ap.add_argument("--include_noise", action="store_true",
                    help="不剔除噪声清单里的噪声 session (默认剔除)")
    ap.add_argument("--noise_file", default="data/_noise_exclude_d5_scan.json",
                    help="噪声清单路径 (find_noise.py 产物)")
    ap.add_argument("--no_cancel_credit", action="store_true",
                    help="关闭方案 b 取消计分口径 (turn 级 GT 恢复取消轮历史态, 旧口径)")
    ap.add_argument("--no_correction_credit", action="store_true",
                    help="关闭改口错误值轮豁免口径 (correction_credit, 默认开)")
    ap.add_argument("--no_gt_derive", action="store_true",
                    help="关闭 turn 级 GT 时间三字段知二推一口径 (gt_derive, 默认开)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.n_demos:
        cfg["retriever"]["top_k"] = args.n_demos
    # v26.4: 机制开关统一走 Features (联动规则在其内部唯一实现)
    feats = Features.from_config(cfg)
    mi_on = feats.multi_instance_key
    print(f"config: retrieve={args.retrieve} top_k={cfg['retriever']['top_k']} "
          f"multi_instance_key={mi_on}")

    sessions = load_test_sessions(args.split)
    # 噪声剔除 (find_noise.py 产物, 数据集更新前默认剔除; --include_noise 关闭)
    if not args.include_noise and os.path.exists(args.noise_file):
        noise = set(json.load(open(args.noise_file, encoding="utf-8"))["session_ids"])
        before = len(sessions)
        sessions = [s for s in sessions if s["session_id"] not in noise]
        print(f"噪声剔除: {before} -> {len(sessions)} sessions (剔除 {before - len(sessions)} 个, "
              f"{args.noise_file})")
    if args.limit:
        sessions = sessions[:args.limit]
    print(f"评估 {len(sessions)} 个 session (逐轮真实透传, session+turn 双口径) ...")

    pipe = MultiIntentPipeline(cfg)
    results = []
    n_sess_match = 0
    n_turn_match = 0
    n_turn_total = 0
    # 意图状态机评估 (status_tracking.enabled 时启用)
    st_on = feats.status_tracking
    n_status_match = 0
    n_status_total = 0
    by_gstatus = collections.Counter()        # GT 状态分桶计数
    by_gstatus_match = collections.Counter()  # 各桶命中数
    by_status_conf = collections.Counter()    # 混淆: "gt->pred"
    by_tpl = collections.Counter()
    by_tpl_sess_match = collections.Counter()
    by_tpl_turn_match = collections.Counter()
    by_tpl_turn_total = collections.Counter()
    by_err = collections.Counter()
    by_turn_err = collections.Counter()
    t0 = time.time()

    for si, s in enumerate(sessions):
        sid = s["session_id"]
        tpl = s["session_meta"]["template_id"]
        gt = session_gt(s, mi_on=mi_on)
        pred, per_turn_pred = run_session(pipe, s, retrieve_on=(args.retrieve == "on"))

        # session 级
        sok, setype = judge_session(gt, pred)
        if sok:
            n_sess_match += 1
            by_tpl_sess_match[tpl] += 1
        else:
            by_err[setype.split(":")[0].split("=")[0][:20]] += 1
        by_tpl[tpl] += 1

        # turn 级 (同一套真实透传输出)
        tgts = turn_gt(s, cancel_credit=not args.no_cancel_credit,
                       correction_credit=not args.no_correction_credit,
                       gt_derive=not args.no_gt_derive, mi_on=mi_on)
        tmatch, ttotal, tdetail = judge_turn(tgts, per_turn_pred)
        n_turn_match += tmatch
        n_turn_total += ttotal
        by_tpl_turn_match[tpl] += tmatch
        by_tpl_turn_total[tpl] += ttotal
        for d in tdetail:
            if not d["match"]:
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

        # 意图状态机口径 (同一套透传输出, 旁路评估)
        sdetail: list[dict] = []
        smatch = stotal = 0
        if st_on:
            from intent_status import build_gt_status_trajectory, judge_status
            gt_traj = build_gt_status_trajectory(s, mi_on=mi_on)
            pred_traj = {p["turn"]: p.get("status_pred", {}) for p in per_turn_pred}
            smatch, stotal, sdetail = judge_status(gt_traj, pred_traj)
            n_status_match += smatch
            n_status_total += stotal
            for d in sdetail:
                by_gstatus[d["gt_status"]] += 1
                if d["match"]:
                    by_gstatus_match[d["gt_status"]] += 1
                else:
                    by_status_conf[f"{d['gt_status']}->{d['pred_status']}"] += 1

        results.append({
            "session_id": sid, "template": tpl,
            "gt_intents": gt, "pred_intents": {k: v["pred_flat"] for k, v in pred.items()},
            "session_match": sok, "session_error_type": setype,
            "turn_match": tmatch, "turn_total": ttotal,
            "turn_detail": tdetail,
            # 全量逐轮输出（含无 GT case 的轮, 如 T3-3 的查询轮）, 供 error_report 展示模型每轮实时输出
            "turn_outputs": [{"turn": p["turn"], "llm_raw": p.get("llm_raw", ""),
                              "task_prompt": p.get("task_prompt", ""),
                              "preds": p.get("preds", {})} for p in per_turn_pred],
            "status_match": smatch, "status_total": stotal,
            "status_detail": sdetail,
        })
        if (si + 1) % 5 == 0 or si == len(sessions) - 1:
            dt = time.time() - t0
            print(f"  [{si+1}/{len(sessions)}] session={n_sess_match}({n_sess_match/(si+1):.3f}) "
                  f"turn={n_turn_match}/{n_turn_total}({n_turn_match/max(n_turn_total,1):.3f}) elapsed={dt:.0f}s")

    sess_rate = n_sess_match / len(sessions) if sessions else 0
    turn_rate = n_turn_match / n_turn_total if n_turn_total else 0
    print("\n" + "=" * 60)
    print(f"session 级 JGA: {n_sess_match}/{len(sessions)} = {sess_rate:.4f}")
    print(f"turn 级 JGA (真实透传): {n_turn_match}/{n_turn_total} = {turn_rate:.4f}")
    if st_on:
        sr = n_status_match / n_status_total if n_status_total else 0
        print(f"意图状态机准确率: {n_status_match}/{n_status_total} = {sr:.4f}")
        for gst in ("Collecting", "Ready", "Confirmed", "Cancelled"):
            n_g = by_gstatus.get(gst, 0)
            if n_g:
                print(f"  {gst}: {by_gstatus_match.get(gst,0)}/{n_g} = {by_gstatus_match.get(gst,0)/n_g:.3f}")
    print("=" * 60)
    print("\n按 template (session / turn):")
    for tpl in sorted(by_tpl):
        sm, st = by_tpl_sess_match[tpl], by_tpl[tpl]
        tm, tt = by_tpl_turn_match[tpl], by_tpl_turn_total[tpl]
        print(f"  {tpl}: session {sm}/{st}={sm/st:.3f}  turn {tm}/{tt}={tm/max(tt,1):.3f}")
    print("\n[session] 错误类型分布:")
    for e, n in by_err.most_common():
        print(f"  {e}: {n}")
    print("\n[turn] 错误类型分布:")
    for e, n in by_turn_err.most_common():
        print(f"  {e}: {n}")

    out = args.out or f"data/evals/eval_dual_{args.tag or 'run'}.jsonl"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {
        "n_sessions": len(sessions), "session_match": n_sess_match, "session_rate": sess_rate,
        "turn_total": n_turn_total, "turn_match": n_turn_match, "turn_rate": turn_rate,
        "status_tracking": st_on,
        "status_total": n_status_total, "status_match": n_status_match,
        "status_rate": (n_status_match / n_status_total) if n_status_total else None,
        "status_by_gt": {g: {"n": by_gstatus.get(g, 0), "match": by_gstatus_match.get(g, 0)}
                          for g in ("Collecting", "Ready", "Confirmed", "Cancelled")
                          if by_gstatus.get(g)},
        "status_confusion": dict(by_status_conf),
        "switches": feats.switches(),
        "retrieve": args.retrieve,
        "top_k": cfg["retriever"]["top_k"],
        "cancel_credit": not args.no_cancel_credit,
        "correction_credit": not args.no_correction_credit,
        "gt_derive": not args.no_gt_derive,
        "multi_instance_key": mi_on,
        "by_tpl": {tpl: {"sess_m": by_tpl_sess_match[tpl], "sess_t": by_tpl[tpl],
                          "turn_m": by_tpl_turn_match[tpl], "turn_t": by_tpl_turn_total[tpl]}
                   for tpl in sorted(by_tpl)},
        "by_session_err": dict(by_err), "by_turn_err": dict(by_turn_err),
    }
    json.dump(summary, open(out.replace(".jsonl", "_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n详细 -> {out}  摘要 -> {out.replace('.jsonl','_summary.json')}")

    if args.err_report:
        import error_report
        error_report.generate(out, DATASET_DIR,
                              os.path.splitext(out)[0] + "_errors.html",
                              noise_path=args.noise_file)


if __name__ == "__main__":
    main()
