# -*- coding: utf-8 -*-
"""三规则离线回放验证 (v22.1 存档预测, 零 API)。

v23.2 起直接调用 time_rules.normalize_timestamp_fields 单规则/组合回放,
规则实现即线库实现 (不再有脚本本地副本)。

口径:
  - 输入 eval_dual_v22_1_fix4.jsonl 每 (turn, intent) 的 pred 时间三字段,
    话术/reference_time 取自 dataset_5 原始 session。
  - fix: 应用规则前 pred!=GT, 应用后 ==GT; break: 应用前 ==GT, 应用后 !=GT。
  - correction_case 的豁免字段跳过; cancel_case 跳过。
  - 已知盲区: 计分轮才可见话术, 错误若生于不计分轮 (如 S-000446 T1) 则弃权,
    真实管线每轮都过规则, 线上可能略好于本回放。
"""
import io, sys, json, glob, os, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time_rules as tr

EVAL = "data/evals/eval_dual_v22_1_fix4.jsonl"
TIME_FIELDS = ("start_timestamp", "end_timestamp", "duration")
ALL_RULES = set(tr.RULE_DEFAULTS.keys())

# ---------- 数据加载 ----------
utt_map = {}   # sid -> {turn: utterance}
ref_map = {}   # sid -> reference_time
for f in sorted(glob.glob("data/dataset_5/sessions.*.jsonl")):
    for line in open(f, encoding="utf-8"):
        s = json.loads(line)
        sid = s["session_id"]
        ref_map[sid] = s.get("reference_time")
        utt_map[sid] = {e["turn"]: (e.get("user") or {}).get("utterance", "")
                        for e in s.get("event_sequence", [])}

cases = []  # (sid, tpl, turn, ikey, gt_flat, pred_flat, exempt)
n_skip = 0
for line in open(EVAL, encoding="utf-8"):
    d = json.loads(line)
    sid = d["session_id"]
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
        cases.append((sid, d["template"], td["turn"], td["intent_key"],
                      gt, td.get("pred") or {}, exempt))
print(f"cases: {len(cases)} (skip cancel/special: {n_skip})")


def apply_rules(pred, query, ctx, keep):
    wrapped = {k: {"value": pred[k]} for k in TIME_FIELDS
               if isinstance(pred.get(k), str) and pred[k]}
    out = tr.normalize_timestamp_fields(wrapped, query, ctx,
                                        disabled_rules=ALL_RULES - set(keep))
    new_pred = dict(pred)
    for k in TIME_FIELDS:
        v = out.get(k)
        v = v.get("value") if isinstance(v, dict) else v
        if isinstance(v, str):
            new_pred[k] = v
    return new_pred


CONFIGS = {
    "day_offset_rederive(明天/后天)": {"day_offset_rederive"},
    "halfday_fix(±12h)": {"halfday_fix"},
    "end_from_start_duration": {"end_from_start_duration"},
    "ALL(三规则组合)": set(ALL_RULES),
}

for name, keep in CONFIGS.items():
    fixed, broken = [], []
    for sid, tpl, turn, ikey, gt, pred, exempt in cases:
        query = utt_map.get(sid, {}).get(turn, "")
        ctx = tr.build_context(ref_map.get(sid))
        new_pred = apply_rules(pred, query, ctx, keep)
        for k in TIME_FIELDS:
            if k in exempt:
                continue
            g = gt.get(k) or None
            b = pred.get(k) or None
            a = new_pred.get(k) or None
            if a == b:
                continue
            rec = (sid, tpl, turn, ikey, k, b, a, g)
            if b != g and a == g:
                fixed.append(rec)
            elif b == g and a != g:
                broken.append(rec)
    print(f"\n===== {name}: 修 {len(fixed)} / 破 {len(broken)}")
    for r in fixed:
        print(f"  [FIX] {r[0]} {r[1]} T{r[2]} {r[3]}.{r[4]}: {r[5]} -> {r[6]} (GT {r[7]})")
    for r in broken:
        print(f"  [BRK] {r[0]} {r[1]} T{r[2]} {r[3]}.{r[4]}: {r[5]} -> {r[6]} (GT {r[7]})")

# 基线时间错误盘点 (对照)
base_wrong = collections.Counter()
for sid, tpl, turn, ikey, gt, pred, exempt in cases:
    for k in TIME_FIELDS:
        if k in exempt:
            continue
        if (gt.get(k) or None) != (pred.get(k) or None):
            base_wrong[k] += 1
print(f"\n基线时间字段错误: {dict(base_wrong)} total {sum(base_wrong.values())}")

# ---------- GT 不变性 (规则作用于 GT 应零改动) ----------
gt_changed = []
for sid, tpl, turn, ikey, gt, pred, exempt in cases:
    query = utt_map.get(sid, {}).get(turn, "")
    ctx = tr.build_context(ref_map.get(sid))
    new_gt = apply_rules(gt, query, ctx, ALL_RULES)
    for k in TIME_FIELDS:
        if k in exempt:
            continue
        if (new_gt.get(k) or None) != (gt.get(k) or None):
            gt_changed.append((sid, tpl, turn, ikey, k, gt.get(k), new_gt.get(k)))
print(f"\nGT 不变性 (组合规则作用于 GT): {len(gt_changed)} 处改动 (期望 0)")
for r in gt_changed[:20]:
    print(f"  [GT-CHG] {r[0]} {r[1]} T{r[2]} {r[3]}.{r[4]}: {r[5]} -> {r[6]}")
