# -*- coding: utf-8 -*-
"""1707: t1 执行正常 (芒果TV 全字段). 但最终 pred 缺 芒果TV/视频.
t2 时 I1 的 rtt=30ms 段是更新 I1 → 但 1a 判断: stubs=[I1 app=芒果TV 非空]!
等下 — t1 是列举轮, v27.7 剥离把 app=芒果TV 剥了? 芒果TV 不在候选 [斗鱼,咪咕,优酷] 里.
但打印 listing=[] (t3 后). t2 时的 listing?
真正问题可能在: t1 是列举轮 → record 兜底抑制 OK; 但 v27.7 strip 只在
_listed_this_turn 时跑, t1 app=芒果TV 不在候选 → 不剥. 那 final 里芒果TV 哪去了?
看 t2 判分: intent key 变了? pred_intents keys?"""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
s = ss['genb-S-001707']
cfg = load_config('configs/config_d6_qwen_plus_best.json')
pipe = MultiIntentPipeline(cfg)
pred, per_turn = run_session(pipe, s, retrieve_on=True)
for p in per_turn:
    print('t', p['turn'], 'keys:', list(p['preds'].keys()), json.dumps(p['preds'], ensure_ascii=False)[:150])
