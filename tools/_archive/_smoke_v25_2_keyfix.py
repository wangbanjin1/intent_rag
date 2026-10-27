# -*- coding: utf-8 -*-
"""v25.2 key 断裂修复的冒烟: 真实 API 跑 S-000318(改口 key 分裂原型)+ S-000082(X-2 对照)。
只跑这 2 个 session, 打印 pred 最终意图 key 集合, 验证修复。"""
import json, os, sys, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_eval as se
from pipeline import MultiIntentPipeline, load_config

cfg = load_config("configs/config_d5_qwen_plus_qmap.json")
pipe = MultiIntentPipeline(cfg)

targets = {"S-000318", "S-000082", "S-000850", "S-000906", "S-000187"}
sessions = [s for s in se.load_test_sessions("data/split_d5.json") if s["session_id"] in targets]

for s in sessions:
    pred, per_turn = se.run_session(pipe, s, retrieve_on=True)
    for pt in per_turn:
        print(f"  --- turn {pt['turn']} raw ---")
        print("  " + (pt.get('llm_raw') or '').replace(chr(10), chr(10)+"  "))
    keys = sorted(pred.keys())
    print(f"== {s['session_id']} ({s['session_meta']['template_id']}) pred keys: {keys}")
    for k in keys:
        flat = pred[k]["pred_flat"]
        filled = {kk: vv for kk, vv in flat.items() if vv is not None}
        print(f"   {k}: {filled}")
    ok, etype = se.judge_session(se.session_gt(s), pred)
    print(f"   session_match={ok}  ({etype})")
