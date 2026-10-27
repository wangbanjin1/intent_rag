# -*- coding: utf-8 -*-
"""关键差异: eval 时 run_multi 的 prev_states 走的是内容键 app/svc (无 #N) — 
line_tolerant 沙箱里 state.end_timestamp 引用 prev['timestamp']['end_timestamp'].
但 313 的 t1 delta 走了 find_timestamp(end=...) 无 start — validator 会重推 start 吗?
t1 final 里 start=None (GT 也是 None, 对). 
重放里 t2 又是好的 — 那 eval 跑的时候什么不同? 重放温度=0 应确定. 
试: 完整复刻 eval 流程跑一次 (同 config), 打中间 results['微信/开直播']['final_state']"""
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
    print('[SEGS]', json.dumps(segs, ensure_ascii=False, default=str)[:400], flush=True)
    print('[PREV]', json.dumps(prev_states, ensure_ascii=False)[:250], flush=True)
    return segs
pl.execute_program_multi = wrapped
pred, per_turn = run_session(pipe, s, retrieve_on=True)
