# -*- coding: utf-8 -*-
"""重放 S-000313 看 t2 实际执行链 (id 段处理/合并层)"""
import sys, json, os
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config
ss = load_test_sessions('data/split_d930_full.json')
s = [x for x in ss if x['session_id'] == 'gena-S-000313'][0]
cfg = load_config('configs/config_d6_qwen38_27b_v273.json')
pipe = MultiIntentPipeline(cfg)
import pipeline as pl
orig = pl.MultiIntentPipeline._process_id_segment
def wrapped(self, key, delta, idbook, prev_states, reference_time, results):
    print(f'[SEG] key={key!r} delta={json.dumps(delta, ensure_ascii=False)[:160]}', flush=True)
    r = orig(self, key, delta, idbook, prev_states, reference_time, results)
    print(f'  -> results keys={list(results.keys())}', flush=True)
    return r
pl.MultiIntentPipeline._process_id_segment = wrapped
pred, per_turn = run_session(pipe, s, retrieve_on=True)
for t, p in enumerate(per_turn, 1):
    fs = (p or {}).get('final_state') or {}
    print(f't{t} final keys:', list(fs.keys()))
    for k, v in fs.items():
        ts = (v or {}).get('timestamp') if isinstance(v, dict) else None
        if ts: print('   ', k, json.dumps(ts, ensure_ascii=False)[:150])
