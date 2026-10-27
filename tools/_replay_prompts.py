# -*- coding: utf-8 -*-
"""重放 v2771 444 子集, 抓每轮 task_prompt 生成补充 HTML (候选列表/prompt 审计用).
只抓 T3-3+T2-2 (候选链路靶区) 失败 session, 快速出报告."""
import sys, json, os, html
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config

recs = {json.loads(l)['session_id']: json.loads(l)
        for l in open('data/evals/eval_dual_v2771_d930_s444_plus.jsonl', encoding='utf-8') if l.strip()}
fails = [sid for sid, x in recs.items() if not x['session_match']
         and x.get('template') in ('T3-3', 'T2-2', 'T2-3')]
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_s446.json')}
cfg = load_config('configs/config_d6_qwen_plus_best.json')
pipe = MultiIntentPipeline(cfg)
out = []
for n, sid in enumerate(sorted(fails), 1):
    s = ss.get(sid)
    if not s: continue
    try:
        pred, per_turn = run_session(pipe, s, retrieve_on=True)
    except Exception as e:
        print(f'[{n}/{len(fails)}] {sid} ERROR {e}', flush=True); continue
    blocks = []
    for p in per_turn:
        tp = getattr(pipe, 'last_task_prompt', '') or ''
        ci = tp.find('CANDIDATE MODE')
        show = tp[max(0, ci-60):ci+2400] if ci >= 0 else tp[-1500:]
        blocks.append(f'<div class="blk"><h4>第{p["turn"]}轮 prompt{"（含 CANDIDATE MODE）" if ci>=0 else ""}</h4>'
                      f'<pre>{html.escape(show)}</pre>'
                      f'<h5>模型输出</h5><pre>{html.escape((p.get("llm_raw") or "")[:800])}</pre></div>')
    out.append(f'<details class="sample"><summary>{sid} ({recs[sid]["template"]}) {recs[sid]["session_error_type"][:50]}</summary>'
               + ''.join(blocks) + '</details>')
    print(f'[{n}/{len(fails)}] {sid} done', flush=True)
html_doc = ('<!DOCTYPE html><meta charset="utf-8"><title>v2771 候选链路 prompt 审计</title>'
            '<style>body{font-family:monospace;background:#111;color:#ddd}pre{white-space:pre-wrap;'
            'background:#1a1a1a;padding:8px;border-radius:4px;font-size:12px}details{margin:8px;'
            'border:1px solid #333;border-radius:4px;padding:6px}summary{cursor:pointer;color:#7fbfff}'
            'h4,h5{color:#9fd49f;margin:6px 0 2px}</style>'
            + f'<h1>v2771 失败 session prompt 审计（T3-3/T2-2/T2-3, n={len(out)}）</h1>' + ''.join(out))
open('data/evals/v2771_prompt_audit.html', 'w', encoding='utf-8').write(html_doc)
print('written data/evals/v2771_prompt_audit.html', len(out), 'sessions')
