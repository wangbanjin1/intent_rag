# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session_eval import load_test_sessions, run_session, session_gt, judge_session, turn_gt, judge_turn
from pipeline import MultiIntentPipeline, load_config
from features import Features
s = [x for x in load_test_sessions("data/split_d6_full.json") if x["session_id"] == "S-000056"][0]
cfg = load_config("configs/config_d6_qwen_plus_best.json")
mi_on = Features.from_config(cfg).multi_instance_key
pipe = MultiIntentPipeline(cfg)
for run in range(1, 4):
    pred, per_turn = run_session(pipe, s, retrieve_on=True)
    gt = session_gt(s, mi_on=mi_on)
    sok, setype = judge_session(gt, pred)
    tm, tt, _ = judge_turn(turn_gt(s, mi_on=mi_on), per_turn)
    print(f"run{run}: sess={sok} turn={tm}/{tt} err={setype[:60]}", flush=True)
