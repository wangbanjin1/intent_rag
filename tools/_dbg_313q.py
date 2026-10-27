# -*- coding: utf-8 -*-
"""无 id->content 替换. 那 d6 上 coreference 段怎么活的?
答案候选: d6 模型极少写 state.X 引用 (v26.2c prompt: 只写变化字段勿写 state.x=state.x),
而 state.end_timestamp 出现在 find_timestamp(end=state.end_timestamp) — d930 模型爱用这种自引用.
验证: grep d6 产物 llm_raw 里 'state.end_timestamp'/'state.start_timestamp' 出现率 vs d930."""
import json, re
for tag in ['v2731_plus_run1', 'v273_d930_q38_run1']:
    n = tot = 0
    for l in open(f'data/evals/eval_dual_{tag}.jsonl', encoding='utf-8'):
        if not l.strip(): continue
        r = json.loads(l)
        for t in (r.get('turn_outputs') or []):
            raw = t.get('llm_raw') or ''
            if raw.strip():
                tot += 1
                if re.search(r'state\.(start_timestamp|end_timestamp|duration)', raw):
                    n += 1
    print(f'{tag}: 自引用轮 {n}/{tot} = {n/tot*100:.1f}%')
