"""_sanity_status.py: 状态机链路自洽性冒烟 (不调 LLM)。

用"完美假模型"(直接吐 GT slot_updates) 驱动 session_eval.run_session,
验证 intent_status 推导 + GT 轨迹重建 + judge_status 全链路。
预期: 状态准确率 ≈ 100%, 差值 = 规则边界案例 (消歧时槽位已齐等)。

用法: python _sanity_status.py [limit]
"""
from __future__ import annotations

import glob
import io
import json
import os
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根

_KEEP_STDOUT = sys.stdout  # 防 GC close 底层 buffer (session_eval import 会重设包装)
from case_format import flatten_params, apply_nested
from intent_status import build_gt_status_trajectory, judge_status
import session_eval  # 注意: 该模块 import 时会重设 sys.stdout 包装


class FakePipe:
    """完美模型: 按 GT slot_updates 构造 finals (含 cancel 信号)。"""

    cfg = {
        "status_tracking": {"enabled": True},
        "confirm_rule": {"enabled": True},
        "agent_context": {"enabled": False},
        "status_prompt": {"enabled": False},
    }
    last_raw = "fake"

    def __init__(self, session: dict):
        events = sorted(session.get("event_sequence") or [], key=lambda e: e.get("turn", 0))
        intents = {it["intent_id"]: it for it in (session.get("intents") or [])}
        # turn -> list[(iid, params)]
        updates: dict[int, list] = {}
        for su in session.get("slot_updates") or []:
            for tu in su.get("turn_updates") or []:
                updates.setdefault(tu.get("turn", 0), []).append((su["intent_id"], tu.get("params") or {}))
        # turn -> 被取消的 iid
        cancel_at: dict[int, list] = {}
        for ev in events:
            acts = (ev.get("user") or {}).get("action_types", []) or []
            if "Cancel_Request" in acts:
                for iid in ev.get("related_intent_ids", []) or []:
                    if intents.get(iid, {}).get("status") == "cancelled":
                        cancel_at.setdefault(ev.get("turn", 0), []).append(iid)
        self._updates = updates
        self._cancel_at = cancel_at
        self._intents = intents
        self._cum: dict[str, dict] = {}
        self._turn_idx = 0
        self._turns = [e.get("turn", 0) for e in events]

    def run_multi(self, utterance, reference_time, prev_states=None, user_context=None,
                  agent_reply=None, prev_statuses=None):
        t = self._turns[self._turn_idx]
        self._turn_idx += 1
        finals: dict[str, dict] = {}
        for iid, params in self._updates.get(t, []):
            self._cum[iid] = apply_nested(self._cum.get(iid, {}), params)
            flat = flatten_params(self._cum[iid])
            app, svc = flat.get("application_name"), flat.get("service_name")
            if not app:
                continue
            key = f"{app}/{svc}" if svc else app
            finals[key] = {"final_state": self._cum[iid], "validity": "valid"}
        for iid in self._cancel_at.get(t, []):
            flat = flatten_params(self._cum.get(iid, {}))
            app, svc = flat.get("application_name"), flat.get("service_name")
            key = f"{app}/{svc}" if svc else (app or iid)
            finals[f"__cancel__:{key}"] = {"__cancel__": True, "cancel_key": key}
        return finals


def main(limit: int = 0):
    files = sorted(glob.glob("data/dataset_merged/sessions.T*.jsonl"))
    sessions = []
    for f in files:
        for line in open(f, encoding="utf-8"):
            sessions.append(json.loads(line))
    if limit:
        sessions = sessions[:limit]
    n_m = n_t = 0
    by_g = collections.Counter(); by_gm = collections.Counter(); conf = collections.Counter()
    worst = []
    for s in sessions:
        pipe = FakePipe(s)
        _, per_turn = session_eval.run_session(pipe, s, retrieve_on=True)
        gt_traj = build_gt_status_trajectory(s)
        pred_traj = {p["turn"]: p.get("status_pred", {}) for p in per_turn}
        m, tot, detail = judge_status(gt_traj, pred_traj)
        n_m += m; n_t += tot
        for d in detail:
            by_g[d["gt_status"]] += 1
            if d["match"]:
                by_gm[d["gt_status"]] += 1
            else:
                conf[f'{d["gt_status"]}->{d["pred_status"]}'] += 1
                if len(worst) < 8:
                    worst.append((s["session_id"], d))
    print(f"sanity (完美模型): 状态准确率 {n_m}/{n_t} = {n_m/max(n_t,1):.4f}")
    for g in ("Collecting", "Ready", "Confirmed", "Cancelled"):
        if by_g.get(g):
            print(f"  {g}: {by_gm.get(g,0)}/{by_g[g]} = {by_gm.get(g,0)/by_g[g]:.3f}")
    print("混淆 (gt->pred):", dict(conf.most_common(10)))
    for sid, d in worst:
        print("  mismatch:", sid, d["turn"], d["intent_key"], d["gt_status"], "->", d["pred_status"])


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
