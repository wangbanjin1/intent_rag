# -*- coding: utf-8 -*-
"""崩点 = 加载序第 3517 个 session. 重放它, 打含 start= 字面省略号的 raw."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = load_test_sessions('data/split_d930_full.json')
target = ss[3516]
print('crash session:', target['session_id'], target['session_meta'].get('template_id'))
from session_eval import run_session
from pipeline import MultiIntentPipeline, load_config
import pipeline as pl
cfg = load_config('configs/config_d6_qwen38_27b_v273.json')
pipe = MultiIntentPipeline(cfg)
pred, per_turn = run_session(pipe, target, retrieve_on=True)
# 找含 '...' 字面参数的 raw
for t, p in enumerate(per_turn, 1):
    raw = (p or {}).get('llm_raw') or ''
    if '...' in raw:
        print(f'--- t{t} raw with ... ---')
        print(raw[:500])
        break
else:
    print('no literal ... found in replay (API 非确定性, 当时才有)')
