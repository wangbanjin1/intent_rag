# -*- coding: utf-8 -*-
"""plus 在 T3-3 仍 50%: 13 个失败归因. LLM 兜底是否被调用?"""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
recs = {json.loads(l)['session_id']: json.loads(l) for l in open('data/evals/eval_dual_v274_d930_s444_plus.jsonl', encoding='utf-8') if l.strip()}
t33f = [sid for sid in recs if recs[sid].get('template')=='T3-3' and not recs[sid]['session_match']]
from collections import Counter
et = Counter()
for sid in t33f:
    e = recs[sid]['session_error_type']
    if 'missing=' in e and 'extra=[]' in e: et['纯缺失'] += 1
    elif 'missing=[]' in e: et['多出'] += 1
    else: et['字段错'] += 1
print('plus T3-3 13 失败:', dict(et))
# extra≠missing 型: 选错记录
sel_wrong = [sid for sid in t33f if 'missing=' in recs[sid]['session_error_type'] and 'extra=[]' not in recs[sid]['session_error_type'] and 'extra=[' in recs[sid]['session_error_type']]
print('选错记录型:', sel_wrong)
# 这 5 个的 t2 raw 里有没有选中明示
for sid in sel_wrong[:3]:
    to = recs[sid].get('turn_outputs') or []
    if len(to) >= 2:
        raw = to[1].get('llm_raw') or ''
        print('==', sid)
        print('  U2:', ss[sid]['event_sequence'][1]['user']['utterance'][:55])
        print('  t2 落地行:', [l for l in raw.split('\n') if 'application_name' in l or 'service_name' in l][:2])
