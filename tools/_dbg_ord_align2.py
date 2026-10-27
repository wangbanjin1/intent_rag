# -*- coding: utf-8 -*-
"""诊断: '云游戏'(仅svc) + '6月25号...'(仅md/hour) 被切成两段, 各自打分:
'云游戏' svc=2 <3 -> return None. 修复 = 纯 svc 段也算可识别属性 + 日期段向前合并."""
import sys, json
sys.path.insert(0, '.')
import pipeline as pl
users = {}
for line in open('data/dataset_930/users_3000.jsonl', encoding='utf-8'):
    if line.strip():
        u = json.loads(line); users[u['user_id']] = u
from session_eval import load_test_sessions
ss = load_test_sessions('data/split_d930_full.json')
s = [x for x in ss if x['session_id']=='genb-S-000960'][0]
raw = users[s['user_id']].get('records') or []
recs = sorted(raw, key=lambda r: r.get('completed_at',''), reverse=True)
flat = [{'application_name': r.get('application_name'), 'service_name': r.get('service_name'),
         'resolution': (r.get('resolution') or {}).get('min_value'), 'rtt': (r.get('rtt') or {}).get('max_value'),
         'completed_at': r.get('completed_at'), 'record_id': r.get('record_id')} for r in recs]
a1 = s['event_sequence'][0]['agent']['utterance']
# 模拟修复后的合并: '云游戏' + '6月25号凌晨2点完成' 拼接
text = pl._DISAMB_QCUT_RE.split(a1)[0]
segs = [x.strip('，。；、 ') for x in pl._DISAMB_SPLIT_RE.split(text)]
merged = []
for seg in segs:
    for pre in pl._DISAMB_PREFIX_RES:
        new = pre.sub('', seg, count=1)
        if new != seg:
            seg = new.strip('，。；、 ')
            break
    if not seg: continue
    a = pl._cand_attrs(seg)
    # 纯日期/时段残段并入上一段 (使其获得 md/hour); 纯svc段先挂起与后续日期段合并
    if set(a) <= {'md', 'hour'} and merged:
        merged[-1] += '，' + seg
        continue
    merged.append(seg)
print('修复后 merged:', merged)
for seg in merged:
    print(repr(seg[:30]), pl._cand_attrs(seg))
