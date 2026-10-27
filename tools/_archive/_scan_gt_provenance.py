# -*- coding: utf-8 -*-
"""反向核验: GT 中 source_type=Turn 的 resolution/rtt 字段, 其 source_ref 指向的那轮用户话语里
必须能找到该值(数字)或能映射到该值的画质词(用 train 还原的真实映射表)。找不到 = GT 值在用户
话语中无出处(用户没说过这个值, 也没人纠正)。"""
import glob, json, re, sys, io
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from find_noise import build_quality_map
from case_format import flatten_params

DATASET = "data/dataset_merged"
qmap = build_quality_map(DATASET, "data/split_merged.json")

RES_WORDS = ["超高清", "4K", "4k", "蓝光", "高清", "超清", "标清", "流畅"]

hits = []
n_field = 0
for f in sorted(glob.glob(f"{DATASET}/sessions.T*.jsonl")):
    for line in open(f, encoding="utf-8"):
        s = json.loads(line)
        utt = {i + 1: (e.get("user") or {}).get("utterance", "")
               for i, e in enumerate(s.get("event_sequence") or [])}
        for it in (s.get("intents") or []):
            if it.get("status") != "resolved":
                continue
            flat = flatten_params(it.get("params") or {})
            app, svc = flat.get("application_name") or "", flat.get("service_name") or ""

            def check(field, pat):
                global n_field
                node = (it.get("params") or {}).get(field)
                # 找到叶子 {value, source_type, source_ref}
                leaves = []
                def walk(o):
                    if isinstance(o, dict):
                        if "value" in o and "source_type" in o:
                            leaves.append(o)
                        else:
                            for v in o.values(): walk(v)
                walk(node or {})
                for lf in leaves:
                    if lf.get("source_type") != "Turn":
                        continue
                    ref = str(lf.get("source_ref") or "")
                    m = re.fullmatch(r"U(\d+)", ref)
                    if not m:
                        continue
                    t = utt.get(int(m.group(1)), "")
                    val = str(lf.get("value") or "")
                    num = re.search(r"(\d+)", val)
                    if not num:
                        continue
                    n_field += 1
                    if num.group(1) in t:
                        continue  # 数字明说
                    # 画质词映射(只 resolution 字段)
                    ok = False
                    if field == "resolution":
                        for w in RES_WORDS:
                            if w in t and qmap.get((app, svc, w)) == val:
                                ok = True; break
                    if not ok:
                        hits.append((s["session_id"], s["session_meta"]["template_id"],
                                     it["intent_id"], field, val, ref, t[:120]))

            check("resolution", None)
            check("rtt", None)

print(f"核验 Turn 来源 resolution/rtt 字段 {n_field} 个, 话语无出处 {len(hits)} 个")
for h in hits:
    print(f"{h[0]} [{h[1]}] {h[2]} {h[3]}={h[4]} (ref {h[5]})\n  话语: {h[6]}")
