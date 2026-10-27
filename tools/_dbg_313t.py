# -*- coding: utf-8 -*-
"""端到端验证: 313 在 alias 修复下, 即使模型输出 state.end_timestamp 引用也能落地."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config
import pipeline as pl
ss = load_test_sessions('data/split_d930_full.json')
s = [x for x in ss if x['session_id'] == 'gena-S-000313'][0]
cfg = load_config('configs/config_d6_qwen38_27b_v273.json')
pipe = MultiIntentPipeline(cfg)
# 强制注入 eval 时失败的那段 raw (含 state.end_timestamp 引用)
fake = "# intent: I1\nstate.timestamp = find_timestamp(start='2026年07月25日19时00分', end=state.end_timestamp)"
orig_gen = pipe.llm.gen_raw_code
state = {'turn': 0}
def gen(system, task):
    state['turn'] += 1
    if state['turn'] == 2:
        return fake
    return orig_gen(system, task)
pipe.llm.gen_raw_code = gen
pred, per_turn = run_session(pipe, s, retrieve_on=True)
for k, v in pred.items():
    pf = v.get('pred_flat', {})
    print(k, 'start:', pf.get('start_timestamp'), 'end:', pf.get('end_timestamp'), 'dur:', pf.get('duration'))
