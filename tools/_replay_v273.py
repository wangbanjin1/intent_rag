# -*- coding: utf-8 -*-
"""v27.3 定点回放: 13 T3-3 + 10 对照, 当前代码 best 配置, 与 v272_cand_run3 基线逐 session 对比翻转。
用法: DST_DATASET=data/dataset_6_clean python tools/_replay_v273.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session_eval import (load_test_sessions, run_session, session_gt,
                          judge_session, turn_gt, judge_turn)
from pipeline import MultiIntentPipeline, load_config
from features import Features

T33 = ["S-000040", "S-000056", "S-000072", "S-000089", "S-000106", "S-000123",
       "S-000140", "S-000157", "S-000174", "S-000192", "S-000210", "S-000228", "S-000246"]
CONTROLS = ["S-000115", "S-000116", "S-000117", "S-000168",
            "S-000006", "S-000103", "S-000154",
            "S-000031", "S-000183", "S-000097"]
BASE = "data/evals/eval_dual_v272_cand_run3.jsonl"

def main():
    all_sessions = {s["session_id"]: s for s in load_test_sessions("data/split_d6_full.json")}
    ids = [i for i in T33 + CONTROLS if i in all_sessions]
    sessions = [all_sessions[i] for i in ids]
    cfg = load_config("configs/config_d6_qwen_plus_best.json")
    mi_on = Features.from_config(cfg).multi_instance_key
    pipe = MultiIntentPipeline(cfg)
    base = {}
    for l in open(BASE, encoding="utf-8"):
        r = json.loads(l)
        base[r["session_id"]] = r
    out = {}
    for s in sessions:
        sid = s["session_id"]
        pred, per_turn = run_session(pipe, s, retrieve_on=True)
        gt = session_gt(s, mi_on=mi_on)
        sok, setype = judge_session(gt, pred)
        tgts = turn_gt(s, mi_on=mi_on)
        tm, tt, td = judge_turn(tgts, per_turn)
        b = base.get(sid, {})
        flip = ""
        if b:
            if bool(b.get("session_match")) != bool(sok):
                flip = "  FLIP " + (" rescued" if sok else " REGRESS")
        print(f"{sid}: sess={sok} turn={tm}/{tt} base_sess={b.get('session_match')} base_turn={b.get('turn_match')}/{b.get('turn_total')}{flip} err={setype[:40]}", flush=True)
        out[sid] = {"session_match": sok, "turn_match": tm, "turn_total": tt,
                    "base_session_match": b.get("session_match")}
    n = sum(1 for v in out.values() if v["session_match"])
    bn = sum(1 for v in out.values() if v["base_session_match"])
    print(f"\nsession: new={n}/{len(out)} base={bn}/{len(out)}", flush=True)

if __name__ == "__main__":
    main()
