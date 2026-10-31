# -*- coding: utf-8 -*-
"""qwen3.8-27b 能力冒烟: 8 个靶向 session(覆盖 new 重建/补全/改口/取消/多意图/消歧)。
评估 v26.2b id 协议下该模型档的行为与遵从, 输出 vs qwen-plus 同 session 对照。"""
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_eval as se
from pipeline import MultiIntentPipeline, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

TARGETS = ["S-000040", "S-000183", "S-000263", "S-000416", "S-000318",
           "S-000392", "S-000082", "S-000487"]

cfg = load_config("configs/config_d5_qwen38_27b_idproto.json")
pipe = MultiIntentPipeline(cfg)
sessions = {s["session_id"]: s for s in se.load_test_sessions("data/split_d5.json")}

# qwen-plus v26.2b 已有结果对照
plus = {}
for tag in ("eval_dual_v26_2b", "eval_dual_v26_2b_run2"):
    for line in open(f"data/evals/{tag}.jsonl", encoding="utf-8"):
        r = json.loads(line)
        plus.setdefault(r["session_id"], []).append(r["session_match"])

n_hdr = n_ok = 0
ok_n = 0
for sid in TARGETS:
    s = sessions.get(sid)
    if not s:
        continue
    pred, per_turn = se.run_session(pipe, s, retrieve_on=True)
    ok, etype = se.judge_session(se.session_gt(s), pred)
    ok_n += ok
    for p in per_turn:
        for h in re.findall(r"#\s*intent:\s*(\S+)", p.get("llm_raw") or ""):
            n_hdr += 1
            if re.match(r"^I\d+$", h) or h in ("new", "none", ""):
                n_ok += 1
    logger.info(
        "%s (%s): %s (plus 双跑: %s) %s",
        sid,
        s["session_meta"]["template_id"],
        "PASS" if ok else "FAIL",
        "/".join("✓" if x else "✗" for x in plus.get(sid, [])),
        etype[:55],
    )
    if not ok:
        logger.info("    pred keys: %s", sorted(pred.keys()))
logger.info("命中 %d/%d  段头遵从 %d/%d", ok_n, len(TARGETS), n_ok, n_hdr)
