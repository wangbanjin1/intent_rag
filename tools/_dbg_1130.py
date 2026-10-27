# -*- coding: utf-8 -*-
"""1130: t2 delta 落了 Telegram, 最终 pred intents keys=['Telegram/视频通话'] 但 app=None.
turn_detail 里 pred app=None — 是 key 在但字段空? 逐轮 pred_flat 看."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session, session_gt
from pipeline import MultiIntentPipeline, load_config
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
s = ss['gena-S-001130']
cfg = load_config('configs/config_d6_qwen_plus_best.json')
pipe = MultiIntentPipeline(cfg)
pred, per_turn = run_session(pipe, s, retrieve_on=True)
for p in per_turn:
    print('t', p['turn'], json.dumps(p['preds'], ensure_ascii=False)[:300])
for k, v in pred.items():
    print('final key:', k, json.dumps(v.get('pred_flat'), ensure_ascii=False)[:200])
