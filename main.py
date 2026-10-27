"""main.py: 端到端 demo (RefPyDST 全流程, 原始 session 格式)。

用法:
  export DASHSCOPE_API_KEY=sk-xxxxx
  python main.py

从 data/sample_session.json 读真实 session, 跑三轮场景:
  1. 原始 query (S-000474) — 应 valid
  2. 负例 (豆瓣游戏) — 应 invalid: matrix_out_service
  3. 多轮 coreference — 第二轮用 ref 引用第一轮状态
"""
from __future__ import annotations

import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json

from case_format import extract_turn_cases, flatten_params, nested_from_flat
from pipeline import IntentPipeline, load_config


def show(result, title):
    print("=" * 70)
    print(title)
    print("=" * 70)
    print(f"utterance: {result.utterance}")
    print(f"reference_time: {result.reference_time}")
    print(f"prev_state: {json.dumps(result.prev_state, ensure_ascii=False)}")
    print("-" * 70)
    print("MMR 检索 E_k:")
    for r in result.retrieved:
        print(f"  {r['case_id']:>16}  intent={r['intent']:>14}  validity={r['validity']:>7}  score={r['score']}")
    print("-" * 70)
    print(f"-> best Δy: {json.dumps(result.best.get('params',{}), ensure_ascii=False)[:200]}")
    print("-" * 70)
    print("Rule Validator (执行程序得Δy → apply到prev → 校验):")
    print(json.dumps(result.final, ensure_ascii=False, indent=2))
    print("=" * 70)


def main():
    cfg = load_config("configs/config_d5_qwen_plus_qmap.json")
    pipe = IntentPipeline(cfg)

    # 从真实 session 取 reference_time 和首轮 utterance
    session = json.loads(open("data/sample_session.json", "r", encoding="utf-8").read())
    ref = session["reference_time"]
    first_turn = session["event_sequence"][0]
    utterance = first_turn["user"]["utterance"]

    # 场景1: 原始 query (豆瓣视频 — 豆瓣不在受理矩阵, 应 invalid: matrix_out_application)
    r1 = pipe.run(utterance, ref, prev_state={})
    show(r1, "场景1: 原始 session query (S-000474, 豆瓣视频应 invalid)")

    # 场景2: 负例 — 豆瓣游戏
    r2 = pipe.run("我要豆瓣游戏保障,2160P,100ms,明天12点到13点。", ref, prev_state={})
    show(r2, "场景2: 负例 (应 invalid: matrix_out_application)")

    # 场景3: 域内合法 app 全链路 (腾讯视频)
    r3 = pipe.run("我要腾讯视频的视频保障,2160P,100ms,明天12点到13点。", ref, prev_state={})
    show(r3, "场景3: 域内 query (应 valid)")

    print("\n回归断言:")
    print(f"  场景1 reason   = {r1.final.get('invalid_reason')}  "
          f"({'OK' if r1.final.get('invalid_reason') == 'matrix_out_application' else 'X'})")
    print(f"  场景2 reason   = {r2.final.get('invalid_reason')}  "
          f"({'OK' if r2.final.get('invalid_reason') == 'matrix_out_application' else 'X'})")
    if r3.final.get("validity") == "valid":
        fs = r3.final.get("final_state", {})
        rtt = (fs.get("rtt") or {}).get("max_value", {}).get("value")
        start = (fs.get("timestamp") or {}).get("start_timestamp", {}).get("value")
        print(f"  场景3 validity=valid  start={start}  rtt={rtt}  (OK)")
    else:
        print(f"  场景3 = {r3.final.get('invalid_reason')}  (X)")


if __name__ == "__main__":
    main()
