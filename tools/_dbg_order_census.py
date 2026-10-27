# -*- coding: utf-8 -*-
"""d930 T3-3 候选序普查: GT candidate_record_ids 相对 records 降序是升序还是降序?"""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = load_test_sessions('data/split_d930_full.json')
users = {}
for line in open('data/dataset_930/users_3000.jsonl', encoding='utf-8'):
    if line.strip():
        u = json.loads(line); users[u['user_id']] = u
asc = desc = mixed = unknown = 0
for s in ss:
    if s['session_meta'].get('template_id') != 'T3-3': continue
    e = (s['session_meta'].get('evidence') or {})
    cands = e.get('candidate_record_ids') or []
    if len(cands) < 2: unknown += 1; continue
    raw = users.get(s['user_id'], {}).get('records') or []
    by_id = {r['record_id']: r.get('completed_at','') for r in raw}
    dates = [by_id.get(c) for c in cands if c in by_id]
    if len(dates) < 2: unknown += 1; continue
    if dates == sorted(dates): asc += 1          # GT 序 = 升序 (旧在前)
    elif dates == sorted(dates, reverse=True): desc += 1
    else: mixed += 1
print(f'd930 T3-3 GT候选序: 升序 {asc} / 降序 {desc} / 混合 {mixed} / 未知 {unknown}')
# 对照 d6
