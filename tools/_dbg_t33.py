# -*- coding: utf-8 -*-
"""d930 T3-3 55.6% 洼地归因: 与 d6 97% 级差 40pp. 抽失败样本分类."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
recs = [json.loads(l) for l in open('data/evals/eval_dual_v2731_d930_q38_run1.jsonl', encoding='utf-8') if l.strip()]
valid = [x for x in recs if x.get('turn_outputs') and any((t.get('llm_raw') or '').strip() for t in x['turn_outputs'])]
t33f = [x for x in valid if x.get('template')=='T3-3' and not x['session_match']]
print('T3-3 有效失败:', len(t33f))
from collections import Counter
# 错误形态
et = Counter()
for x in t33f:
    e = x['session_error_type']
    if 'intent_set_mismatch missing=' in e and 'extra=[]' in e: et['纯缺失(意图没落地)'] += 1
    elif 'extra=' in e and 'missing=[]' in e: et['多出(多建)'] += 1
    else: et['字段错'] += 1
print(dict(et))
# 抽 3 个纯缺失看 U1/A1/U2
n = 0
for x in t33f:
    if 'missing=' in x['session_error_type'] and 'extra=[]' in x['session_error_type']:
        s = ss.get(x['session_id'])
        if not s: continue
        n += 1
        print('==', x['session_id'], x['session_error_type'][:60])
        for e in s['event_sequence'][:3]:
            print(f"  U{e['turn']}: {e['user']['utterance'][:75]}")
            print(f"  A{e['turn']}: {e['agent']['utterance'][:75]}")
        to = x.get('turn_outputs') or []
        if to:
            print('  t1raw:', (to[0].get('llm_raw') or '')[:250].replace('\n', ' | '))
        if n >= 3: break
