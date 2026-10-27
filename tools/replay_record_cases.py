# -*- coding: utf-8 -*-
"""replay_record_cases.py: v27.1 record_selector 离线回放 (目标 + 对照, 双配置对比)。

用法 (前台跑, 先 unset 代理):
  DST_DATASET=data/dataset_6_clean python tools/replay_record_cases.py

目标 = C 类抢跑 (S-000056/123/192) + A 类画质 (S-000114/169/026);
对照 = record 高频模板 (T1-4/T2-3/T2-4/T2-6/X-2) 且 fix3 判对, 盯 record 机制桶增量 (v27 纪律)。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_eval import (load_test_sessions, run_session, session_gt,
                          judge_session, turn_gt, judge_turn)
from pipeline import MultiIntentPipeline, load_config
from features import Features

TARGETS = ["S-000056", "S-000123", "S-000192", "S-000114", "S-000169", "S-000026"]
CONTROLS = ["S-000031", "S-000097", "S-000114", "S-000183",   # T1-4 record_rule 高频 (114 兼目标)
            "S-000064", "S-000115", "S-000220",               # T2-1
            "S-000066", "S-000117", "S-000168",               # T2-3
            "S-000005", "S-000011", "S-000152",               # T2-4
            "S-000006", "S-000103", "S-000154",               # T2-6
            "S-000075", "S-000092", "S-000232"]               # X-2


def run_cfg(cfg_path: str, sessions: list[dict], mi_on: bool) -> dict:
    cfg = load_config(cfg_path)
    pipe = MultiIntentPipeline(cfg)
    out = {}
    for s in sessions:
        sid = s["session_id"]
        try:
            pred, per_turn = run_session(pipe, s, retrieve_on=True)
        except Exception as e:
            out[sid] = {"error": str(e)}
            continue
        gt = session_gt(s, mi_on=mi_on)
        sok, setype = judge_session(gt, pred)
        tgts = turn_gt(s, mi_on=mi_on)
        tm, tt, tdetail = judge_turn(tgts, per_turn)
        rec_wrong = sum(1 for d in tdetail if not d["match"]
                        and not d.get("cancel_case") and not d.get("correction_case")
                        and any(x in d["wrong"] for x in
                                ("application_name", "service_name", "resolution", "rtt")))
        out[sid] = {"session_match": sok, "session_err": setype,
                    "turn_match": tm, "turn_total": tt, "rec_wrong": rec_wrong}
        print(f"  [{os.path.basename(cfg_path)}] {sid}: sess={sok} turn={tm}/{tt} rec_wrong={rec_wrong}",
              flush=True)
    return out


def main() -> None:
    all_sessions = {s["session_id"]: s
                    for s in load_test_sessions("data/split_d6_full.json")}
    ids = [i for i in TARGETS + CONTROLS if i in all_sessions]
    sessions = [all_sessions[i] for i in ids]
    mi_on = Features.from_config(load_config("configs/config_d6_qwen_plus_best.json")).multi_instance_key

    print(f"回放 {len(sessions)} sessions x 2 配置 (目标 {len(TARGETS)}, 对照 {len(CONTROLS)})")
    base = run_cfg("configs/config_d6_qwen_plus_best.json", sessions, mi_on)
    new = run_cfg("configs/config_d6_qwen_plus_rec.json", sessions, mi_on)

    print("\n==== 翻转对比 (v27.0 -> v27.1) ====")
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
        mark = "TARGET" if sid in TARGETS else "ctrl  "
        print(f"{mark} {sid}: sess {b['session_match']}->{n['session_match']} "
              f"turn {b['turn_match']}/{b['turn_total']}->{n['turn_match']}/{n['turn_total']} "
              f"rec_wrong {b['rec_wrong']}->{n['rec_wrong']} {flip}")
    print(f"\n修: {len(rescued)} {rescued}")
    print(f"破: {len(broken)} {broken}")
    with open("data/evals/_replay_v271_record.json", "w", encoding="utf-8") as f:
        json.dump({"base": base, "new": new}, f, ensure_ascii=False, indent=1)
    print("明细: data/evals/_replay_v271_record.json")


if __name__ == "__main__":
    main()
