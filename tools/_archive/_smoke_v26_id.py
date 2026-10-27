# -*- coding: utf-8 -*-
"""v26.0 intent-id 协议遵从率冒烟: 真实 API 跑 10 个代表性 session。

模板覆盖: T1-1/T1-3(单轮多意图)/T2-1(补槽)/T2-3(改口)/T3-1(确认)/T3-2(说错更正)/
T3-3(消歧)/T2-6(取消)/X-2(多意图编排)。
检查: ①段头遵从率(全部为 id/new/none) ②cancel 段头 ③session/turn 命中 ④与 v25.2 同 session 对照。
"""
import json, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_eval as se
from pipeline import MultiIntentPipeline, load_config

TARGETS = ["S-000040", "S-000845", "S-000935", "S-000902", "S-000325", "S-000416", "S-000183"]

cfg = load_config("configs/config_d5_qwen_plus_qmap.json")
cfg["intent_id_protocol"] = {"enabled": True}
pipe = MultiIntentPipeline(cfg)

sessions = {s["session_id"]: s for s in se.load_test_sessions("data/split_d5.json")}
n_hdr = n_ok = n_cancel = n_cancel_ok = 0
sess_results = []
for sid in TARGETS:
    s = sessions.get(sid)
    if not s:
        print(f"[skip] {sid} 不在 test 集")
        continue
    pred, per_turn = se.run_session(pipe, s, retrieve_on=True)
    # 遵从率统计
    for p in per_turn:
        raw = p.get("llm_raw") or ""
        for h in re.findall(r"#\s*intent:\s*(.+)", raw):
            h = h.strip()
            n_hdr += 1
            if re.match(r"^I\d+$", h) or h in ("new", "none", ""):
                n_ok += 1
        for c in re.findall(r"#\s*cancel:\s*(.+)", raw):
            c = c.strip()
            n_cancel += 1
            if re.match(r"^I\d+$", c):
                n_cancel_ok += 1
    ok, etype = se.judge_session(se.session_gt(s), pred)
    sess_results.append((sid, s["session_meta"]["template_id"], ok, etype))
    print(f"{sid} ({s['session_meta']['template_id']}): match={ok}  {etype[:60]}")
    for k in sorted(pred.keys()):
        filled = {kk: vv for kk, vv in pred[k]["pred_flat"].items() if vv is not None}
        print(f"    {k}: {filled}")

print(f"\n段头遵从: {n_ok}/{n_hdr}  cancel 段头遵从: {n_cancel_ok}/{n_cancel}")
print(f"session 命中: {sum(1 for r in sess_results if r[2])}/{len(sess_results)}")
