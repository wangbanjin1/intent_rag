import logging
import os
import sys

for k in ["all_proxy", "ALL_PROXY", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
    os.environ.pop(k, None)
for line in open("keys.txt", encoding="utf-8"):
    if line.startswith("BAILIAN_API_KEY="):
        os.environ["BAILIAN_API_KEY"] = line.strip().split("=", 1)[1]
os.environ["DST_DATASET"] = "data/dataset_6_clean"
sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from features import Features
from pipeline import MultiIntentPipeline, load_config
from session_eval import judge_session, load_test_sessions, run_session, session_gt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

all_sessions = {s["session_id"]: s for s in load_test_sessions("data/split_d6_full.json")}
targets = ["S-000095", "S-000114", "S-000141", "S-000211", "S-000164"]
pipe = MultiIntentPipeline(load_config("configs/config_d6_qwen_plus_cand.json"))
mi_on = Features.from_config(load_config("configs/config_d6_qwen_plus_best.json")).multi_instance_key
fixed = 0
for sid in targets:
    s = all_sessions[sid]
    pred, pt = run_session(pipe, s, retrieve_on=True)
    gt = session_gt(s, mi_on=mi_on)
    sok, err = judge_session(gt, pred)
    logger.info("%s -> %s", sid, "PASS" if sok else f"FAIL {err}")
    if sok:
        fixed += 1
logger.info("%d/5 修复", fixed)
