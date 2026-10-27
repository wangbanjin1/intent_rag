# -*- coding: utf-8 -*-
"""离线复算: 对已有 eval 产物应用 correction_credit 口径 (不重跑模型)。

用法: python _recompute_correction_credit.py <eval.jsonl>
产物: <eval去.jsonl>_corrcredit.jsonl + _summary.json
逻辑与 session_eval.judge_turn 的 correction 分支一致:
  错误值轮 case, pred 空(拒收) -> 命中; pred 非空 -> 豁免被更正字段后比对其余字段。
"""
import json, sys, collections, os
import session_eval as se
from case_format import EVAL_SLOTS

DATA = "data/dataset_5"

def load_sessions():
    import glob
    out = {}
    for f in glob.glob(os.path.join(DATA, "sessions.*.jsonl")):
        for line in open(f, encoding="utf-8"):
            d = json.loads(line)
            out[d["session_id"]] = d
    return out

def main(src):
    sessions = load_sessions()
    rows = [json.loads(l) for l in open(src, encoding="utf-8")]
    n_flip = 0
    n_turn_m = 0
    n_turn_t = 0
    by_tpl = collections.defaultdict(lambda: [0, 0, 0, 0])
    flips = []
    for r in rows:
        s = sessions.get(r["session_id"])
        cmap = se.correction_turn_map(s) if s else {}
        err_turns = {t for t, _ in cmap.values()}
        exempt_by_turn = {t: fs for t, fs in cmap.values()}
        tm = 0
        for td in r["turn_detail"]:
            ok = td["match"]
            if (not ok and not td.get("cancel_case")
                    and td["turn"] in err_turns):
                exempt = exempt_by_turn[td["turn"]]
                pred = td.get("pred") or {}
                if not pred:
                    ok, wrong = True, []
                else:
                    gt = td.get("gt") or {}
                    wrong = [x for x in EVAL_SLOTS
                             if x not in exempt
                             and (gt.get(x) or None) != (pred.get(x) or None)]
                    ok = not wrong
                td["correction_case"] = True
                td["correction_exempt"] = sorted(exempt)
                if ok:
                    td["match"], td["wrong"] = True, []
                    td["missed_intent"] = False
                    n_flip += 1
                    flips.append((r["session_id"], r["template"], td["turn"]))
            if td["match"]:
                tm += 1
        r["turn_match"] = tm
        n_turn_m += tm
        n_turn_t += r["turn_total"]
        b = by_tpl[r["template"]]
        b[0] += 1
        b[1] += 1 if r["session_match"] else 0
        b[2] += tm
        b[3] += r["turn_total"]
    out = src.replace(".jsonl", "_corrcredit.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_sess = len(rows)
    n_sess_m = sum(1 for r in rows if r["session_match"])
    summary = {
        "source": src,
        "correction_credit": True,
        "n_sessions": n_sess,
        "session_match": n_sess_m, "session_rate": n_sess_m / n_sess,
        "turn_total": n_turn_t, "turn_match": n_turn_m,
        "turn_rate": n_turn_m / n_turn_t,
        "correction_flips": n_flip,
        "by_tpl": {t: {"sess_m": v[1], "sess_t": v[0],
                        "turn_m": v[2], "turn_t": v[3]}
                   for t, v in sorted(by_tpl.items())},
    }
    json.dump(summary, open(out.replace(".jsonl", "_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"session: {n_sess_m}/{n_sess} = {n_sess_m/n_sess:.4f}")
    print(f"turn:    {n_turn_m}/{n_turn_t} = {n_turn_m/n_turn_t:.4f}")
    print(f"correction 转正: {n_flip}")
    for x in flips:
        print("  转正:", x)
    for t, v in sorted(by_tpl.items()):
        tr = f"{v[2]/v[3]:.3f}" if v[3] else "n/a"
        print(f"  {t}: session {v[1]}/{v[0]}={v[1]/v[0]:.3f}  turn {v[2]}/{v[3]}={tr}")
    print("->", out)

if __name__ == "__main__":
    main(sys.argv[1])
