# -*- coding: utf-8 -*-
"""1707: t1 明说芒果TV, 但 t1 是列举轮 (_cand_listing 非空) → v27.7 剥离逻辑
把 app=芒果TV 判为候选之一?? 候选是斗鱼直播/咪咕/优酷 — 芒果TV 不在其中!
那 t1 的芒果TV 怎么丢的? 重放看剥离前后."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config
import pipeline as pl
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
s = ss['genb-S-001707']
cfg = load_config('configs/config_d6_qwen_plus_best.json')
pipe = MultiIntentPipeline(cfg)
orig_exec = pl.execute_program_multi
def wrapped(raw, prevs):
    segs = orig_exec(raw, prevs)
    print('[EXEC]', json.dumps(segs, ensure_ascii=False, default=str)[:300], flush=True)
    return segs
pl.execute_program_multi = wrapped
pred, per_turn = run_session(pipe, s, retrieve_on=True)
print('listing:', [(r.get('application_name')) for r in (getattr(pipe, "_cand_listing", None) or [])])
