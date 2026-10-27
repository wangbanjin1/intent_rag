# -*- coding: utf-8 -*-
"""逐规则消融: 每条规则单独开启跑 GT 不变性回归, 输出每条规则的 GT 改动数。"""
import io, sys, json, glob, collections, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time_rules as tr

RULES = list(tr.RULE_DEFAULTS.keys())

# collect all applications once
apps = []  # (sid, tpl, turn, wrapped, query, ctx)
for f in sorted(glob.glob("data/dataset_5/sessions.*.jsonl")):
    tpl = os.path.basename(f).replace("sessions.", "").replace(".jsonl", "")
    for line in open(f, encoding="utf-8"):
        s = json.loads(line)
        ctx = tr.build_context(s.get("reference_time"))
        utts = {e["turn"]: e["user"]["utterance"] for e in s.get("event_sequence", [])}
        for su in s.get("slot_updates") or []:
            for tu in su.get("turn_updates", []):
                ts = (tu.get("params") or {}).get("timestamp")
                if not isinstance(ts, dict) or not ts:
                    continue
                wrapped = {k: v for k, v in ts.items() if isinstance(v, dict) and "value" in v}
                if not wrapped:
                    continue
                apps.append((s["session_id"], tpl, tu.get("turn"), wrapped, utts.get(tu.get("turn"), ""), ctx))
print("applications:", len(apps))


def run(enabled):
    changes = collections.Counter()
    for sid, tpl, t, wrapped, query, ctx in apps:
        out = tr.normalize_timestamp_fields(wrapped, query, ctx, disabled_rules=set(RULES) - set(enabled))
        for k, v in wrapped.items():
            nv = out.get(k)
            nv = nv.get("value") if isinstance(nv, dict) else nv
            if nv != v.get("value") and nv is not None:
                changes[k] += 1
    return changes


total = collections.Counter()
print(f"{'rule':<22}{'start':>7}{'end':>7}{'dur':>7}{'total':>8}")
for rule in RULES:
    c = run([rule])
    n = sum(c.values())
    total[rule] = n
    print(f"{rule:<22}{c['start_timestamp']:>7}{c['end_timestamp']:>7}{c['duration']:>7}{n:>8}")

# cumulative safe set: add rules in ascending harm, verify combined
safe = [r for r in RULES if total[r] == 0]
print("\nzero-harm rules:", safe)
c = run(safe)
print("combined zero-harm set changes:", dict(c), "total:", sum(c.values()))
