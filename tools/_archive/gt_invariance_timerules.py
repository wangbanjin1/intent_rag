# -*- coding: utf-8 -*-
"""GT 不变性回归: 对 dataset_5 每个 turn 的 slot_updates 时间字段应用 time_rules,
统计规则会把 GT 值改动的比例 (贴近真实管线: 每轮只喂当轮 utterance)。
"""
import io, sys, json, glob, collections, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time_rules as tr

files = sorted(glob.glob("data/dataset_5/sessions.*.jsonl"))
field_changes = collections.Counter()
tpl_changes = collections.Counter()
examples = []
total_field_apps = 0
sessions_touched = set()

for f in files:
    tpl = os.path.basename(f).replace("sessions.", "").replace(".jsonl", "")
    for line in open(f, encoding="utf-8"):
        s = json.loads(line)
        ref = s.get("reference_time")
        ctx = tr.build_context(ref)
        utts = {e["turn"]: e["user"]["utterance"] for e in s.get("event_sequence", [])}
        for su in s.get("slot_updates") or []:
            for tu in su.get("turn_updates", []):
                t = tu.get("turn")
                params = tu.get("params", {}) or {}
                ts = params.get("timestamp")
                if not isinstance(ts, dict) or not ts:
                    continue
                query = utts.get(t, "")
                wrapped = {k: v for k, v in ts.items() if isinstance(v, dict) and "value" in v}
                if not wrapped:
                    continue
                for k, v in wrapped.items():
                    if isinstance(v.get("value"), str):
                        total_field_apps += 1
                out = tr.normalize_timestamp_fields(wrapped, query, ctx)
                for k, v in wrapped.items():
                    nv = out.get(k)
                    nv = nv.get("value") if isinstance(nv, dict) else nv
                    ov = v.get("value")
                    if nv != ov and nv is not None:
                        field_changes[k] += 1
                        tpl_changes[tpl] += 1
                        sessions_touched.add(s["session_id"])
                        if len(examples) < 60:
                            examples.append((s["session_id"], tpl, t, k, ov, nv, query[:50]))

print("total time-field applications (GT values fed through rules):", total_field_apps)
print("GT-CHANGING counts by field:", dict(field_changes))
print("by template:", dict(tpl_changes))
print("sessions touched:", len(sessions_touched))
print()
for e in examples:
    print(e)
