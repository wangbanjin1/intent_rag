# -*- coding: utf-8 -*-
"""t2 段 delta={} 空! eval 时沙箱产出空 delta (start=..., end=state.end_timestamp 全被 line_tolerant 跳过?)
重放时又正常 — 差异在 prev 结构? 打完整 prev 看 timestamp 是否在."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config
import pipeline as pl
ss = load_test_sessions('data/split_d930_full.json')
s = [x for x in ss if x['session_id'] == 'gena-S-000313'][0]
cfg = load_config('configs/config_d6_qwen38_27b_v273.json')
pipe = MultiIntentPipeline(cfg)
orig_exec = pl.execute_program_multi
def wrapped(raw, prev_states):
    segs = orig_exec(raw, prev_states)
    if any(not d for _, d in segs) and prev_states:
        k = next(iter(prev_states))
        print('[EMPTY-SEG] raw:', raw[:300], flush=True)
        print('[PREV-FULL]', json.dumps(prev_states[k], ensure_ascii=False)[:500], flush=True)
    return segs
pl.execute_program_multi = wrapped
pred, per_turn = run_session(pipe, s, retrieve_on=True)
