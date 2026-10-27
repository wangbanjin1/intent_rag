# -*- coding: utf-8 -*-
"""replay_time_cases.py: v27 时间机制离线回放 (目标 case + 对照组, 双配置对比)。

用法 (前台跑, 先 unset 代理防 DashScope 劫持):
  DST_DATASET=data/dataset_6_clean python tools/replay_time_cases.py

对照口径: config_d6_qwen_plus_best.json (v26.3-p3 基线) vs config_d6_qwen_plus_time.json (v27)。
逐 session 报告 session/turn match 翻转 (修/破), 目标 case 要求 1修0破 才进全量。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_eval import (load_test_sessions, run_session, judge_session,
                          turn_gt, judge_turn)
from pipeline import MultiIntentPipeline, load_config
from features import Features

# 机制性时间错误目标 (跨 5 跑稳定, plan_time_anchor_20260920.md §2)
TARGETS = [
    "S-000063", "S-000066", "S-000072", "S-000119", "S-000140", "S-000154",
    "S-000165", "S-000185", "S-000227", "S-000237", "S-000249",
]
# 对照组: run5 判对且时间规则触发强度高 (防破窗; 含矛盾/候选触发的 T2-4/X-2 + 日期表高频)
CONTROLS = [
    "S-000005", "S-000029", "S-000070", "S-000218", "S-000009",
    "S-000014", "S-000045", "S-000083", "S-000184", "S-000186",
    "S-000113", "S-000117",
]


def run_cfg(cfg_path: str, sessions: list[dict], mi_on: bool) -> dict:
    cfg = load_config(cfg_path)
    pipe = MultiIntentPipeline(cfg)
    out = {}
    for s in sessions:
        sid = s["session_id"]
        try:
            pred, per_turn = run_session(pipe, s, retrieve_on=True)
        except Exception as e:  # API 故障不静默
            out[sid] = {"error": str(e)}
            continue
        from session_eval import session_gt
        gt = session_gt(s, mi_on=mi_on)
        sok, setype = judge_session(gt, pred)
        tgts = turn_gt(s, mi_on=mi_on)
        tm, tt, tdetail = judge_turn(tgts, per_turn)
        time_wrong = sum(1 for d in tdetail if not d["match"] and not d.get("cancel_case")
                         and not d.get("correction_case")
                         and any(x in d["wrong"] for x in ("start_timestamp", "end_timestamp", "duration")))
        out[sid] = {"session_match": sok, "session_err": setype,
                    "turn_match": tm, "turn_total": tt, "time_wrong": time_wrong}
        print(f"  [{os.path.basename(cfg_path)}] {sid}: sess={sok} turn={tm}/{tt} time_wrong={time_wrong}",
              flush=True)
    return out


def main() -> None:
    all_sessions = {s["session_id"]: s
                    for s in load_test_sessions("data/split_d6_full.json")}
    ids = [i for i in TARGETS + CONTROLS if i in all_sessions]
    missing = [i for i in TARGETS + CONTROLS if i not in all_sessions]
    if missing:
        print(f"[!] 不在 split_d6_full: {missing}")
    sessions = [all_sessions[i] for i in ids]
    mi_on = Features.from_config(load_config("configs/config_d6_qwen_plus_best.json")).multi_instance_key

    print(f"回放 {len(sessions)} sessions x 2 配置 (目标 {len(TARGETS)}, 对照 {len(CONTROLS)})")
    base = run_cfg("configs/config_d6_qwen_plus_best.json", sessions, mi_on)
    new = run_cfg("configs/config_d6_qwen_plus_time.json", sessions, mi_on)

    print("\n==== 翻转对比 (base -> v27) ====")
    rescued, broken = [], []
    for sid in ids:
        b, n = base.get(sid, {}), new.get(sid, {})
        if "error" in b or "error" in n:
            print(f"{sid}: ERROR base={b.get('error')} new={n.get('error')}")
            continue
        flip = ""
        if not b["session_match"] and n["session_match"]:
            flip = "修(session)"; rescued.append(sid)
        elif b["session_match"] and not n["session_match"]:
            flip = "破(session)"; broken.append(sid)
        dtw = n["time_wrong"] - b["time_wrong"]
        mark = "TARGET" if sid in TARGETS else "ctrl  "
        print(f"{mark} {sid}: sess {b['session_match']}->{n['session_match']} "
              f"turn {b['turn_match']}/{b['turn_total']}->{n['turn_match']}/{n['turn_total']} "
              f"time_wrong {b['time_wrong']}->{n['time_wrong']} {flip}")
    print(f"\n修: {len(rescued)} {rescued}")
    print(f"破: {len(broken)} {broken}")
    os.makedirs("data/evals", exist_ok=True)
    with open("data/evals/_replay_v27_time.json", "w", encoding="utf-8") as f:
        json.dump({"base": base, "new": new}, f, ensure_ascii=False, indent=1)
    print("明细: data/evals/_replay_v27_time.json")


if __name__ == "__main__":
    main()
