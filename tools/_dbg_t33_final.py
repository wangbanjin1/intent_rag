# -*- coding: utf-8 -*-
"""T3-3 12 稳错(v2732 单跑口径)最终归因分类"""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
recs = {json.loads(l)['session_id']: json.loads(l) for l in open('data/evals/eval_dual_v2732_d930_s444.jsonl', encoding='utf-8') if l.strip()}
t33f = [sid for sid in recs if recs[sid].get('template')=='T3-3' and not recs[sid]['session_match']]
from collections import Counter
cats = Counter()
for sid in t33f:
    e = recs[sid]['session_error_type']
    to = recs[sid].get('turn_outputs') or []
    raw2 = (to[1].get('llm_raw') or '') if len(to) >= 2 else ''
    if 'missing=' in e and 'extra=[]' in e:
        # 纯缺失: 看 t2 是否落了身份
        if 'app' in e: pass
        # 检查 t2 raw 是否只补悬空未落身份 (1b 误走)
        if "state.application_name" not in raw2 and "state.service_name" not in raw2:
            cats['1b误走: 选择轮只补悬空未落身份'] += 1
        else:
            cats['纯缺失(其他)'] += 1
    elif 'extra=' in e and 'missing=[]' in e:
        cats['多出(多建/占位残留)'] += 1
    else:
        cats['字段错(时间/画质)'] += 1
print('T3-3 12 失败归因:', dict(cats))
for sid in t33f:
    e = recs[sid]['session_error_type'][:60]
    print(' ', sid, e)
