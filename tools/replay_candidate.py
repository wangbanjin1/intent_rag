# -*- coding: utf-8 -*-
"""replay_candidate.py: v27.2 候选模式回放 (13 个 T3-3 + T2-x 对照)。
用法: DST_DATASET=data/dataset_6_clean python tools/replay_candidate.py"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_eval import (load_test_sessions, run_session, session_gt,
                          judge_session, turn_gt, judge_turn)
from pipeline import MultiIntentPipeline, load_config
from features import Features

T33 = ["S-000040", "S-000056", "S-000072", "S-000089", "S-000106", "S-000123",
       "S-000140", "S-000157", "S-000174", "S-000192", "S-000210", "S-000228", "S-000246"]
CONTROLS = ["S-000115", "S-000116", "S-000117", "S-000168",   # T2-3
            "S-000006", "S-000103", "S-000154",               # T2-6
            "S-000031", "S-000183", "S-000097"]               # T1-4


def run_cfg(cfg_path, sessions, mi_on):
    cfg = load_config(cfg_path)
    pipe = MultiIntentPipeline(cfg)
    out = {}
    for s in sessions:
        sid = s["session_id"]
        try:
            pred, per_turn = run_session(pipe, s, retrieve_on=True)
        except Exception as e:
            out[sid] = {"error": str(e)}
            print(f"  [{os.path.basename(cfg_path)}] {sid}: ERROR {e}", flush=True)
            continue
        gt = session_gt(s, mi_on=mi_on)
        sok, setype = judge_session(gt, pred)
        tgts = turn_gt(s, mi_on=mi_on)
        tm, tt, td = judge_turn(tgts, per_turn)
        out[sid] = {"session_match": sok, "session_err": setype,
                    "turn_match": tm, "turn_total": tt}
        print(f"  [{os.path.basename(cfg_path)}] {sid}: sess={sok} turn={tm}/{tt} err={setype[:40]}", flush=True)
    return out


def main():
    all_sessions = {s["session_id"]: s for s in load_test_sessions("data/split_d6_full.json")}
    ids = [i for i in T33 + CONTROLS if i in all_sessions]
    sessions = [all_sessions[i] for i in ids]
    mi_on = Features.from_config(load_config("configs/config_d6_qwen_plus_best.json")).multi_instance_key
    print(f"回放 {len(sessions)} sessions x 2 配置 (T3-3 {len(T33)}, 对照 {len(CONTROLS)})", flush=True)
    base = run_cfg("configs/config_d6_qwen_plus_best.json", sessions, mi_on)
    new = run_cfg("configs/config_d6_qwen_plus_cand.json", sessions, mi_on)
    print("\n==== 翻转对比 (v27.0 -> v27.2) ====")
    rescued, broken = [], []
    for sid in ids:
        b, n = base.get(sid, {}), new.get(sid, {})
        if "error" in b or "error" in n:
            continue
        flip = ""
        if not b["session_match"] and n["session_match"]:
            flip = "修(session)"; rescued.append(sid)
        elif b["session_match"] and not n["session_match"]:
            flip = "破(session)"; broken.append(sid)
        print(f"{sid}: sess {b['session_match']}->{n['session_match']} "
              f"turn {b['turn_match']}/{b['turn_total']}->{n['turn_match']}/{n['turn_total']} "
              f"{b['session_err'][:30]} -> {n['session_err'][:30]} {flip}")
    print(f"\n修: {len(rescued)} {rescued}")
    print(f"破: {len(broken)} {broken}")
    with open("data/evals/_replay_v272_cand.json", "w", encoding="utf-8") as f:
        json.dump({"base": base, "new": new}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
