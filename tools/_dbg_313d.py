# -*- coding: utf-8 -*-
"""313: reference_time 会不会是问题? run_session 用 session.reference_time.
真实执行时 validate 正常(上面). 那 turn_detail 的 pred 为何 None?
回看 _dbg_313b 输出: '[SEG] I1 delta' 后 results keys 有微信/开直播, 但 final keys=[]?
—— final keys=[] 是我 debug 脚本取错键 (final_state 在 fin 里, 我取了 v['timestamp']).
重放直接打 session_out."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session, session_gt
from pipeline import MultiIntentPipeline, load_config
ss = load_test_sessions('data/split_d930_full.json')
s = [x for x in ss if x['session_id'] == 'gena-S-000313'][0]
cfg = load_config('configs/config_d6_qwen38_27b_v273.json')
pipe = MultiIntentPipeline(cfg)
pred, per_turn = run_session(pipe, s, retrieve_on=True)
for k, v in pred.items():
    print(k, json.dumps(v.get('pred_flat'), ensure_ascii=False)[:220])
for p in per_turn:
    print('t', p['turn'], json.dumps(p['preds'], ensure_ascii=False)[:200])
