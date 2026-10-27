# -*- coding: utf-8 -*-
"""1411 纯缺失大规模 — 抽 2 个 T1-1 (单轮!) 看 raw."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session, session_gt, judge_session
from pipeline import MultiIntentPipeline, load_config
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
cfg = load_config('configs/config_d6_qwen38_27b_v273.json')
pipe = MultiIntentPipeline(cfg)
for sid in ['gena-S-000160', 'gena-S-000238']:
    s = ss[sid]
    print('==', sid, s['session_meta'].get('template_id'))
    print('U1:', s['event_sequence'][0]['user']['utterance'][:100])
    pred, per_turn = run_session(pipe, s, retrieve_on=True)
    gt = session_gt(s, mi_on=True)
    sok, setype = judge_session(gt, pred)
    print('pred keys:', list(pred.keys()))
    print('gt keys:', list(gt.keys()))
    print('err:', setype[:60])
    print('raw:', ((per_turn[0] or {}).get('llm_raw') or '')[:400])
    print()
