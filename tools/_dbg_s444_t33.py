# -*- coding: utf-8 -*-
"""T3-3 11 稳错归因: '候选N' 修复后还错什么?"""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
recs = {json.loads(l)['session_id']: json.loads(l) for l in open('data/evals/eval_dual_v2731_d930_s444.jsonl', encoding='utf-8') if l.strip()}
recs2 = {json.loads(l)['session_id']: json.loads(l) for l in open('data/evals/eval_dual_v2731_d930_s444_run2.jsonl', encoding='utf-8') if l.strip()}
t33f = [sid for sid in recs if recs[sid].get('template')=='T3-3' and not recs[sid]['session_match'] and not recs2[sid]['session_match']]
from collections import Counter
et = Counter()
for sid in t33f:
    e = recs[sid]['session_error_type']
    if 'missing=' in e and 'extra=[]' in e: et['纯缺失'] += 1
    elif 'extra=' in e and 'missing=[]' in e: et['多出'] += 1
    else: et['字段错'] += 1
print('T3-3 稳错形态:', dict(et))
# 抽 2 个纯缺失
n = 0
for sid in t33f:
    e = recs[sid]['session_error_type']
    if 'missing=' in e and 'extra=[]' in e:
        s = ss[sid]
        n += 1
        print('==', sid, e[:55])
        for ev in s['event_sequence']:
            print(f"  U{ev['turn']}: {ev['user']['utterance'][:65]}")
            print(f"  A{ev['turn']}: {ev['agent']['utterance'][:65]}")
        raws = recs[sid].get('turn_outputs') or []
        if len(raws) >= 2:
            print('  t2raw:', (raws[1].get('llm_raw') or '')[:300].replace('\n', ' | '))
        if n >= 2: break
