# -*- coding: utf-8 -*-
"""通用规则离线回放: 在任意 eval 存档上测量规则集的修/破 (零 API)。

用法: python tools/replay_rules_on_eval.py <eval_jsonl>
规则集:
  new3   当前 time_rules.py (v23.2 三条重推导规则)
  full   归档 data/_archive/time_rules_v23_full.py (v23 移植全量, 默认门控)
口径与 replay_three_rules.py 相同 (fix/break/correction 豁免/cancel 跳过)。
"""
import io, sys, json, glob, os, collections, importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVAL = sys.argv[1] if len(sys.argv) > 1 else "data/evals/eval_dual_v22_1_fix4.jsonl"
TIME_FIELDS = ("start_timestamp", "end_timestamp", "duration")

import time_rules as tr_new
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "time_rules_full", os.path.join(ROOT, "data", "_archive", "time_rules_v23_full.py"))
tr_full = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr_full)

RULESETS = {
    "new3(v23.2 三条重推导)": (tr_new, set(tr_new.RULE_DEFAULTS)),
    "full(v23 移植全量默认门控)": (tr_full, {r for r, d in tr_full.RULE_DEFAULTS.items() if d}),
    "new3+full": (None, None),  # 先 full 后 new3
}

utt_map, ref_map = {}, {}
for f in sorted(glob.glob("data/dataset_5/sessions.*.jsonl")):
    for line in open(f, encoding="utf-8"):
        s = json.loads(line)
        ref_map[s["session_id"]] = s.get("reference_time")
        utt_map[s["session_id"]] = {e["turn"]: (e.get("user") or {}).get("utterance", "")
                                    for e in s.get("event_sequence", [])}

cases, n_skip = [], 0
for line in open(EVAL, encoding="utf-8"):
    d = json.loads(line)
    for td in d["turn_detail"]:
        gt = td.get("gt")
        exempt = set()
        if td.get("cancel_case"):
            n_skip += 1
            continue
        if isinstance(gt, dict) and "__correction__" in gt:
            exempt = set(gt["__correction__"])
            gt = gt["__gt__"]
        if not isinstance(gt, dict):
            n_skip += 1
            continue
        cases.append((d["session_id"], d["template"], td["turn"], td["intent_key"],
                      gt, td.get("pred") or {}, exempt))
print(f"eval: {EVAL}\ncases: {len(cases)} (skip: {n_skip})")


def apply_mod(mod, pred, query, ctx, keep):
    wrapped = {k: {"value": pred[k]} for k in TIME_FIELDS
               if isinstance(pred.get(k), str) and pred[k]}
    out = mod.normalize_timestamp_fields(wrapped, query, ctx,
                                         disabled_rules=set(mod.RULE_DEFAULTS) - set(keep))
    new_pred = dict(pred)
    for k in TIME_FIELDS:
        v = out.get(k)
        v = v.get("value") if isinstance(v, dict) else v
        if isinstance(v, str):
            new_pred[k] = v
    return new_pred


def run(name, keep_spec):
    fixed, broken = [], []
    for sid, tpl, turn, ikey, gt, pred, exempt in cases:
        query = utt_map.get(sid, {}).get(turn, "")
        ctx = tr_new.build_context(ref_map.get(sid))
        if name == "new3+full":
            np1 = apply_mod(tr_full, pred, query, ctx, RULESETS["full(v23 移植全量默认门控)"][1])
            new_pred = apply_mod(tr_new, np1, query, ctx, RULESETS["new3(v23.2 三条重推导)"][1])
        else:
            mod, keep = keep_spec
            new_pred = apply_mod(mod, pred, query, ctx, keep)
        for k in TIME_FIELDS:
            if k in exempt:
                continue
            g, b = gt.get(k) or None, pred.get(k) or None
            a = new_pred.get(k) or None
            if a == b:
                continue
            rec = (sid, tpl, turn, ikey, k, b, a, g)
            if b != g and a == g:
                fixed.append(rec)
            elif b == g and a != g:
                broken.append(rec)
    print(f"\n===== {name}: 修 {len(fixed)} / 破 {len(broken)}")
    for r in fixed[:30]:
        print(f"  [FIX] {r[0]} {r[1]} T{r[2]} {r[3]}.{r[4]}: {r[5]} -> {r[6]} (GT {r[7]})")
    for r in broken[:30]:
        print(f"  [BRK] {r[0]} {r[1]} T{r[2]} {r[3]}.{r[4]}: {r[5]} -> {r[6]} (GT {r[7]})")
    return len(fixed), len(broken)


summary = {}
for name, spec_ in RULESETS.items():
    summary[name] = run(name, spec_)

base_wrong = collections.Counter()
for sid, tpl, turn, ikey, gt, pred, exempt in cases:
    for k in TIME_FIELDS:
        if k not in exempt and (gt.get(k) or None) != (pred.get(k) or None):
            base_wrong[k] += 1
print(f"\n基线时间字段错误: {dict(base_wrong)} total {sum(base_wrong.values())}")
print("汇总:", json.dumps(summary, ensure_ascii=False))
