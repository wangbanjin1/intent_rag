# -*- coding: utf-8 -*-
"""确认是字面 '...' 还是别的. 打 t3 完整 raw 代码体."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config
ss = load_test_sessions('data/split_d930_full.json')
target = [x for x in ss if x['session_id']=='genb-S-001419'][0]
cfg = load_config('configs/config_d6_qwen38_27b_v273.json')
pipe = MultiIntentPipeline(cfg)
pred, per_turn = run_session(pipe, target, retrieve_on=True)
raw = (per_turn[2] or {}).get('llm_raw') or ''
# 只打代码行 (state./find_)
for line in raw.split('\n'):
    if line.strip().startswith(('state.', '# intent', '# candidate', '# cancel')) or 'find_' in line:
        print(repr(line[:130]))
