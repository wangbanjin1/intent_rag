# -*- coding: utf-8 -*-
"""真相: 764/1789 的错误不在 t2, 在【t3 确认轮】——t2 落地正确,
t3 模型又"切换"到了别的候选 (FaceTime/钉钉)! t3 时 _cand_listing 仍非空?
待选择状态没在选择轮后清除, t3 又触发选择轮 judge, 模型再选一次选了别的!"""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
s = ss['gena-S-000764']
for e in s['event_sequence']:
    print(f"U{e['turn']}: {e['user']['utterance']}")
    print(f"A{e['turn']}: {e['agent']['utterance'][:80]}")
e = s['session_meta']['evidence']
print('GT sel:', e.get('selected_record_id'))
