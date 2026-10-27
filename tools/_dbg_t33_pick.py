# -*- coding: utf-8 -*-
"""extra≠missing 型 (7个): 用户选了候选N, 模型落的是另一条 — pick 解析对了但
候选明细顺序(话术序)与落地错位? 抽 2 个看 t2 raw 选中与 GT 选中."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
recs = {json.loads(l)['session_id']: json.loads(l) for l in open('data/evals/eval_dual_v2732_d930_s444.jsonl', encoding='utf-8') if l.strip()}
from pipeline import _parse_pick_ord
for sid in ['genb-S-000960', 'genb-S-001045']:
    s = ss[sid]
    e = s['session_meta'].get('evidence') or {}
    u2 = s['event_sequence'][1]['user']['utterance']
    print('==', sid)
    print('  U2:', u2[:60], '| pick:', _parse_pick_ord(u2))
    print('  A1:', s['event_sequence'][0]['agent']['utterance'][:80])
    print('  GT sel:', e.get('selected_record_id'), 'idx:', e.get('selected_candidate_index'))
    print('  GT summaries:', [(c['record_id'], c['application_name']) for c in e.get('candidate_summaries', [])])
    raw2 = (recs[sid]['turn_outputs'][1].get('llm_raw') or '')
    # 提取模型落地了什么
    import re
    m = re.findall(r'state\.(?:application_name|service_name) = ["\'](.+?)["\']', raw2)
    print('  t2 落地:', m)
    print('  t2 summary 首200:', raw2[:200].replace('\n', ' | '))
    print()
