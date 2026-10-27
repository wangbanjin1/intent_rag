# -*- coding: utf-8 -*-
"""find_noise 适配 d930: split 只有 test_sessions (全测试集无 train).
画质映射表改从 d930 全量 source_ref 还原 (d930 无 train split)."""
import sys, os, json, glob, re
sys.path.insert(0, '.')
sys.path.insert(0, 'tools/_archive')
import importlib.util
spec = importlib.util.spec_from_file_location("find_noise", "tools/_archive/find_noise.py")
fn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fn)

DATASET = 'data/dataset_930'
ids = set(json.load(open('data/split_d930_full.json', encoding='utf-8'))['test_sessions'])

# 画质映射: d930 全量 source_ref 还原 (find_noise 的 train 逻辑适配)
qmap = {}
pat = fn._RES_MAP_PAT
for f in sorted(glob.glob(os.path.join(DATASET, 'sessions.*'))):
    if 'users' in os.path.basename(f): continue
    with open(f, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line: continue
            s = json.loads(line)
            for intent in s.get('intents', []):
                def walk(o):
                    if isinstance(o, dict):
                        ref = str(o.get('source_ref', ''))
                        if ref.startswith('resolution_quality_map'):
                            m = pat.match(ref)
                            if m:
                                qmap[(m.group(1), m.group(2), m.group(3))] = o.get('value')
                        for v in o.values(): walk(v)
                    elif isinstance(o, list):
                        for v in o: walk(v)
                walk(intent)
print(f'画质映射表: {len(qmap)} 条 (从 d930 全量 source_ref 还原)')
users = fn._load_users(DATASET)
print(f'用户画像: {len(users)} 个用户')

noise = {}
n = 0
for f in sorted(glob.glob(os.path.join(DATASET, 'sessions.*'))):
    if 'users' in os.path.basename(f): continue
    with open(f, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line: continue
            s = json.loads(line)
            if s['session_id'] not in ids: continue
            n += 1
            rs = fn.check_session(s, qmap, users)
            if rs: noise[s['session_id']] = rs

out = {'note': '客观规则噪声清单(find_noise.py R1-R5, d930 全量), session_eval 默认剔除',
       'n_scanned': n, 'session_ids': sorted(noise), 'reasons': noise}
json.dump(out, open('data/_noise_exclude_d930_scan.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'扫描 {n} session, 噪声 {len(noise)} 个 -> data/_noise_exclude_d930_scan.json')
for sid, rs in sorted(noise.items()):
    print(f'  {sid}: {rs[0][:110]}')
