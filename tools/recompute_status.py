import json
import logging
import os
import re
import sys

for k in ["all_proxy", "ALL_PROXY", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
    os.environ.pop(k, None)
sys.path.insert(0, ".")
from intent_status import (
    build_gt_status_trajectory,
    derive_status,
    has_pending_choice,
    in_matrix,
    is_affirm,
    judge_status,
)
from session_eval import load_test_sessions

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

all_sessions = {s["session_id"]: s for s in load_test_sessions("data/split_d6_full.json")}
total_old = 0
total_new = 0
total = 0
n_old_worse = 0
n_new_worse = 0
for n in [1, 2, 3]:
    rows = {
        json.loads(line)["session_id"]: json.loads(line)
        for line in open(f"data/evals/eval_dual_v272_cand_run{n}.jsonl", encoding="utf-8")
    }
    for sid, r in rows.items():
        s = all_sessions.get(sid)
        if not s:
            continue
        gt_new = build_gt_status_trajectory(s, mi_on=True)
        prev_status = {}
        pred_traj = {}
        for to in r["turn_outputs"]:
            t = to["turn"]
            utt = ""
            for ev in s["event_sequence"]:
                if ev["turn"] == t:
                    utt = ev["user"]["utterance"]
            affirmed = is_affirm(utt or "")
            pchoice = has_pending_choice(utt or "")
            preds = to.get("preds", {})
            cancelled_keys = set()
            raw = to.get("llm_raw", "")
            for m in re.finditer(r"#\s*cancel\s*:\s*(.+)", raw):
                cancelled_keys.add(m.group(1).strip())
            st_out = {}
            for k, flat in preds.items():
                if not in_matrix(flat):
                    continue
                cancelled = any(k == ck or k.startswith(ck) for ck in cancelled_keys)
                st = derive_status(
                    prev_status.get(k), flat, "valid",
                    cancelled=cancelled, affirmed=affirmed,
                    touched=True, pending_choice=pchoice,
                )
                st_out[k] = st
                prev_status[k] = st
            pred_traj[t] = st_out
        nm, nt, nd = judge_status(gt_new, pred_traj)
        om, ot = r.get("status_match", 0), r.get("status_total", 0)
        total_old += om
        total_new += nm
        total += ot
        if nm < om:
            n_old_worse += 1
        if nm > om:
            n_new_worse += 1
logger.info(
    "三跑状态机: 旧GT口径 %d/%d=%.2f%%  新GT口径(重放pred) %d/%d=%.2f%%",
    total_old, total, total_old / total * 100, total_new, total, total_new / total * 100,
)
logger.info("逐session: 新口径变差 %d 个, 变好 %d 个", n_old_worse, n_new_worse)
