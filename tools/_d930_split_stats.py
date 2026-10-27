# -*- coding: utf-8 -*-
"""分析: arrearage 从 3060/4455 (genb-S-000916 附近) 开始, 此后 1393 session 全 API 失败空分.
有效口径 = 前 3060 session. 重算前段指标."""
import json
recs = [json.loads(l) for l in open('data/evals/eval_dual_v2731_d930_q38_run1.jsonl', encoding='utf-8') if l.strip()]
# API 失败的 session: llm_raw 全空 & pred 全空
valid = [x for x in recs if (x.get('turn_outputs') and any((t.get('llm_raw') or '').strip() for t in x['turn_outputs']))]
dead  = [x for x in recs if x not in valid]
n_v = sum(1 for x in valid if x['session_match'])
t_v = sum(x['turn_match'] for x in valid); tt_v = sum(x['turn_total'] for x in valid)
print(f'有效 session: {len(valid)}  session 级: {n_v}/{len(valid)} = {n_v/len(valid)*100:.2f}%  turn 级: {t_v}/{tt_v} = {t_v/tt_v*100:.2f}%')
print(f'API 失败(空分): {len(dead)}')
# 有效段的模板分布
from collections import Counter
tot = Counter(x['template'] for x in valid)
ok = Counter(x['template'] for x in valid if x['session_match'])
print('有效段分模板:')
for tpl in sorted(tot):
    print(f'  {tpl}: {ok[tpl]}/{tot[tpl]} = {ok[tpl]/tot[tpl]*100:.1f}%')
