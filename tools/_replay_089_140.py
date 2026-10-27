# -*- coding: utf-8 -*-
"""定点回放 S-000089/S-000140 x N 次 (候选并集修复 + 提示互斥 遵从率统计)"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session_eval import (load_test_sessions, run_session, session_gt,
                          judge_session, turn_gt, judge_turn)
from pipeline import MultiIntentPipeline, load_config
from features import Features

IDS = ["S-000089", "S-000140"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 4

def main():
    all_sessions = {s["session_id"]: s for s in load_test_sessions("data/split_d6_full.json")}
    sessions = [all_sessions[i] for i in IDS]
    cfg = load_config("configs/config_d6_qwen_plus_best.json")
    mi_on = Features.from_config(cfg).multi_instance_key
    pipe = MultiIntentPipeline(cfg)
    for run in range(1, N + 1):
        for s in sessions:
            sid = s["session_id"]
            pred, per_turn = run_session(pipe, s, retrieve_on=True)
            gt = session_gt(s, mi_on=mi_on)
            sok, setype = judge_session(gt, pred)
            tgts = turn_gt(s, mi_on=mi_on)
            tm, tt, td = judge_turn(tgts, per_turn)
            raw1 = (per_turn[0] or {}).get("llm_raw") or ""
            cand = "# candidate:" in raw1
            print(f"run{run} {sid}: sess={sok} turn={tm}/{tt} t1_cand={cand} err={setype[:50]}", flush=True)

if __name__ == "__main__":
    main()
