# -*- coding: utf-8 -*-
"""d930 双子集 v28.1 失败 session 逐例取证: 抽对话/turn_detail/raw, 输出 jsonl 供逐例分析."""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session_eval import load_test_sessions
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
out = []
for tag, path in [('s444', 'data/evals/eval_dual_v281_d930_s444_q38.jsonl'),
                  ('s444b', 'data/evals/eval_dual_v281_d930_s444b_q38.jsonl')]:
    for l in open(path, encoding='utf-8'):
        r = json.loads(l)
        if r['session_match']: continue
        sid = r['session_id']
        s = ss.get(sid) or {}
        evs = [{'turn': e['turn'], 'u': e['user']['utterance'], 'a': e['agent']['utterance']}
               for e in (s.get('event_sequence') or [])]
        tos = [{'turn': t['turn'], 'raw': (t.get('llm_raw') or '')[:900]}
               for t in (r.get('turn_outputs') or [])]
        tds = [{'turn': td['turn'], 'key': td['intent_key'],
                'gt': {k: v for k, v in td['gt'].items() if v is not None},
                'pred': {k: v for k, v in td['pred'].items() if v is not None},
                'wrong': td.get('wrong', [])}
               for td in (r.get('turn_detail') or []) if not td.get('match')]
        out.append({'subset': tag, 'sid': sid, 'tpl': r['template'],
                    'err': r['session_error_type'],
                    'dialogue': evs, 'turn_outputs': tos, 'wrong_detail': tds})
with open('data/evals/_d930_failures_v281.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print('written', len(out), 'failures')
