# -*- coding: utf-8 -*-
"""365/476/508 共同形态: t1 落桩 OK, 看失败在哪一轮. 打 t2/t3 raw."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
recs = {json.loads(l)['session_id']: json.loads(l) for l in open('data/evals/eval_dual_v2731_d930_q38_run1.jsonl', encoding='utf-8') if l.strip()}
x = recs['gena-S-000365']
for t in x.get('turn_outputs', []):
    raw = (t.get('llm_raw') or '')
    print(f"--- t{t['turn']} raw ({len(raw)} chars) ---")
    print(raw[:600])
    print()
