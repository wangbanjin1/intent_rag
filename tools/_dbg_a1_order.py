# -*- coding: utf-8 -*-
"""关键: 话术序 (=用户口径) 是否总能从 A1 解析? 960 话术序=[咪咕快游, 虎牙]=升序.
选择轮锚点顺序来源优先级: 话术解析 disamb_cands -> _cand_listing (selector 降序).
960 话术解析 None (话术无 app 名: '第一条是云游戏, 6月25号...'), 回退存档=降序=反!
修复方向: 当 selector matched 与 GT 结构同源时, 候选明细注入时【同时给 completed_at】,
让模型可按话术中的日期对齐; 或更直接 — selector 候选组装时检测 U1 里日期词顺序?
最稳: 选择轮锚点里对每个候选附 completed_at 日期, 并指示"用户说的第N条 = 客服话术中
第N个被提及的记录; 若无法确定顺序, 用话术中提到的日期匹配本表"。这是信息补充而非顺序假设."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions
ss = load_test_sessions('data/split_d930_full.json')
users = {}
for line in open('data/dataset_930/users_3000.jsonl', encoding='utf-8'):
    if line.strip():
        u = json.loads(line); users[u['user_id']] = u
# 话术解析覆盖率普查 (d930 T3-3): A1 能被 _parse_disamb_candidates 解析的比例
from pipeline import _parse_disamb_candidates
n_ok = n_none = 0
for s in ss:
    if s['session_meta'].get('template_id') != 'T3-3': continue
    raw = users.get(s['user_id'], {}).get('records') or []
    recs = sorted(raw, key=lambda r: r.get('completed_at',''), reverse=True)
    flat = [{'application_name': r.get('application_name'), 'service_name': r.get('service_name'),
             'resolution': (r.get('resolution') or {}).get('min_value'), 'rtt': (r.get('rtt') or {}).get('max_value'),
             'completed_at': r.get('completed_at'), 'record_id': r.get('record_id')} for r in recs]
    a1 = s['event_sequence'][0]['agent']['utterance']
    out = _parse_disamb_candidates(a1, flat)
    if out is None: n_none += 1
    else: n_ok += 1
print(f'd930 T3-3 A1 话术解析: 成功 {n_ok} / 失败 {n_none} / 258')
