# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session_eval import load_test_sessions, run_session, session_gt, judge_session
from pipeline import MultiIntentPipeline, load_config
cfg_path = sys.argv[1] if len(sys.argv) > 1 else "configs/config_d6_qwen38_27b_v273.json"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 2
IDS = ["S-000056", "S-000192"]
sessions = {s["session_id"]: s for s in load_test_sessions("data/split_d6_full.json")}
cfg = load_config(cfg_path)
mi_on = cfg.get("multi_instance_key", {}).get("enabled", False)
pipe = MultiIntentPipeline(cfg)
for run in range(1, N + 1):
    for sid in IDS:
        s = sessions[sid]
        pred, _ = run_session(pipe, s, retrieve_on=True)
        gt = session_gt(s, mi_on=mi_on)
        sok, setype = judge_session(gt, pred)
        print(f"run{run} {sid}: {'PASS' if sok else 'FAIL ' + setype[:50]}", flush=True)
