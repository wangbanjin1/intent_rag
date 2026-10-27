# -*- coding: utf-8 -*-
"""离线复算: 对已有 eval 产物应用 gt_derive 口径 (turn 级 GT 时间三字段知二推一, 不重跑模型)。

用法: python tools/recompute_gt_derive.py <eval.jsonl>
产物: <eval去.jsonl>_gtderive.jsonl + _summary.json
逻辑与 session_eval.turn_gt(gt_derive=True) + judge_turn 一致:
  GT 某时间字段缺失且另两个齐全时推导补齐 (start+end→duration / start+dur→end / end+dur→start),
  只补缺失不覆盖; cancel_case 不动; correction_case 在 real_gt 上推导后仍豁免被更正字段。
双向翻转都统计 (理论上前向口径只补 GT 缺失字段, 对->错只可能发生在 pred 该字段也空的 case)。
"""
import json, sys, collections, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import session_eval as se
from case_format import EVAL_SLOTS


def main(src):
    rows = [json.loads(l) for l in open(src, encoding="utf-8")]
    n_up = 0      # 错 -> 对
    n_down = 0    # 对 -> 错
    n_turn_m = 0
    n_turn_t = 0
    by_tpl = collections.defaultdict(lambda: [0, 0, 0, 0])
    flips_up, flips_down = [], []
    for r in rows:
        tm = 0
        for td in r["turn_detail"]:
            if td.get("cancel_case"):
                if td["match"]:
                    tm += 1
                continue
            gt = dict(td.get("gt") or {})
            gt2, derived = se._derive_time_flat(gt)
            pred = td.get("pred") or {}
            exempt = set(td.get("correction_exempt") or [])
            if td.get("correction_case") and not pred:
                ok = True
                wrong = []
            else:
                wrong = [s for s in EVAL_SLOTS
                         if s not in exempt
                         and (gt2.get(s) or None) != (pred.get(s) or None)]
                ok = not wrong
            if ok and not td["match"]:
                n_up += 1
                flips_up.append((r["session_id"], r["template"], td["turn"],
                                 td["intent_key"], derived))
            elif td["match"] and not ok:
                n_down += 1
                flips_down.append((r["session_id"], r["template"], td["turn"],
                                   td["intent_key"], derived, wrong))
            td["gt"] = gt2
            td["gt_derived"] = derived
            td["match"] = ok
            td["wrong"] = wrong
            if ok:
                td["missed_intent"] = False
                tm += 1
        r["turn_match"] = tm
        n_turn_m += tm
        n_turn_t += r["turn_total"]
        b = by_tpl[r["template"]]
        b[0] += 1
        b[1] += 1 if r["session_match"] else 0
        b[2] += tm
        b[3] += r["turn_total"]
    out = src.replace(".jsonl", "_gtderive.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_sess = len(rows)
    n_sess_m = sum(1 for r in rows if r["session_match"])
    summary = {
        "source": src,
        "gt_derive": True,
        "n_sessions": n_sess,
        "session_match": n_sess_m, "session_rate": n_sess_m / n_sess,
        "turn_total": n_turn_t, "turn_match": n_turn_m,
        "turn_rate": n_turn_m / n_turn_t,
        "flips_up": n_up, "flips_down": n_down,
        "by_tpl": {t: {"sess_m": v[1], "sess_t": v[0],
                        "turn_m": v[2], "turn_t": v[3]}
                   for t, v in sorted(by_tpl.items())},
    }
    json.dump(summary, open(out.replace(".jsonl", "_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"session: {n_sess_m}/{n_sess} = {n_sess_m/n_sess:.4f}")
    print(f"turn:    {n_turn_m}/{n_turn_t} = {n_turn_m/n_turn_t:.4f}")
    print(f"转正(+): {n_up}   翻错(-): {n_down}")
    for x in flips_up:
        print("  +:", x)
    for x in flips_down:
        print("  -:", x)
    for t, v in sorted(by_tpl.items()):
        tr = f"{v[2]/v[3]:.3f}" if v[3] else "n/a"
        print(f"  {t}: session {v[1]}/{v[0]}={v[1]/v[0]:.3f}  turn {v[2]}/{v[3]}={tr}")
    print("->", out)


if __name__ == "__main__":
    main(sys.argv[1])
