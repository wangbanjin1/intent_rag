"""make_split.py: 按 template 分层 80/20 划分 train/test, 输出 split.json + test_cases.jsonl。

- 分层抽样: 每个 template 内随机 80% train / 20% test (种子固定 42, 可复现)
- 测试集 session 不进 case_bank (防数据泄露)
- 对每个 test session 调 extract_turn_cases 拆 turn-level case
- 每个 case 记录评估所需信息 (方案 B: 每个 intent 一个 case, 独立 prev_state 累积)
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import logging
import os
import random
import sys

from case_format import extract_turn_cases, gt_final_flat, is_guarantee_intent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logger = logging.getLogger(__name__)

DATASET_DIR = os.environ.get("DST_DATASET", "data/dataset_5")


def load_all_sessions() -> list[dict]:
    out = []
    for f in sorted(glob.glob(f"{DATASET_DIR}/sessions.*")):
        if "users" in os.path.basename(f):
            continue
        if f.endswith(".json"):
            out.extend(json.load(open(f, "r", encoding="utf-8")))
        else:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        out.append(json.loads(line))
    return out


def load_users(path: str = None) -> dict:
    if path is None:
        # 自动找 dataset 目录下的 users*.jsonl
        cands = sorted(glob.glob(f"{DATASET_DIR}/users*.jsonl"))
        path = cands[0] if cands else ""
    users = {}
    if path and os.path.exists(path):
        for line in open(path, "r", encoding="utf-8"):
            line = line.strip()
            if line:
                u = json.loads(line)
                users[u["user_id"]] = u
    return users


def stratified_split(sessions: list[dict], test_ratio: float, seed: int):
    by_tpl: dict[str, list[dict]] = collections.defaultdict(list)
    for s in sessions:
        by_tpl[s["session_meta"]["template_id"]].append(s)
    train, test = [], []
    rng = random.Random(seed)
    for tpl, lst in sorted(by_tpl.items()):
        lst = sorted(lst, key=lambda s: s["session_id"])
        rng.shuffle(lst)
        n_test = max(1, round(len(lst) * test_ratio)) if len(lst) >= 2 else 0
        # 小模板(n<5)保证至少 1 测试 1 训练
        if len(lst) >= 2 and n_test == 0:
            n_test = 1
        test.extend(lst[:n_test])
        train.extend(lst[n_test:])
    return train, test


def build_test_cases(test_sessions: list[dict], users: dict) -> list[dict]:
    cases = []
    for s in test_sessions:
        tpl = s["session_meta"]["template_id"]
        uid = s.get("user_id", "")
        u = users.get(uid, {})
        profile = u.get("profile", [])
        records = u.get("records", [])
        # 画像上下文 (供 LLM coreference 引用): profile + records 倒序(最近在前)
        rec_sorted = sorted(records, key=lambda r: r.get("completed_at", ""), reverse=True)
        user_context = {
            "profile": {p["key"]: p["value"] for p in profile},
            "recent_records": [
                {
                    "application_name": r.get("application_name"),
                    "service_name": r.get("service_name"),
                    "resolution": (r.get("resolution") or {}).get("min_value"),
                    "rtt": (r.get("rtt") or {}).get("max_value"),
                    "start_timestamp": (r.get("timestamp") or {}).get("start_timestamp"),
                    "end_timestamp": (r.get("timestamp") or {}).get("end_timestamp"),
                    "duration": (r.get("timestamp") or {}).get("duration"),
                    "completed_at": r.get("completed_at"),
                }
                for r in rec_sorted
            ],
        }
        intents = {i["intent_id"]: i for i in (s.get("intents") or [])}
        for c in extract_turn_cases(s):
            iid = c["case_id"].rsplit("-", 1)[-1]  # ...-I1
            it = intents.get(iid, {})
            params = it.get("params") or {}
            is_g = is_guarantee_intent(c.get("intent", ""), params)
            # 标记 GT 哪些字段来自 Record/Memory (用于宽松评估)
            record_slots = set()
            for slot in ["application_name", "service_name", "resolution", "rtt"]:
                spec = params.get(slot)
                if isinstance(spec, dict):
                    st = spec.get("source_type", "")
                    inner = spec.get("min_value", spec.get("max_value", {}))
                    if isinstance(inner, dict):
                        st = inner.get("source_type", st)
                    if st in ("Record", "Memory"):
                        record_slots.add(slot)
            cases.append({
                "case_id": c["case_id"],
                "session_id": c["session_id"],
                "user_id": uid,
                "template": tpl,
                "turn": c["turn"],
                "intent_id": iid,
                "intent": c.get("intent", ""),
                "utterance": c["utterance"],
                "reference_time": c["reference_time"],
                "prev_state": c["prev_state"],          # 嵌套, 作为 pipeline 输入
                "user_context": user_context,           # 画像: profile + 近期 records
                "gt_final_flat": gt_final_flat(c),      # 7 字段真值
                "record_sourced_slots": sorted(record_slots),
                "validity": c.get("validity", "valid"),
                "invalid_reason": c.get("invalid_reason"),
                "is_guarantee": is_g,
            })
    return cases


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--test_ratio", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--split_out", default="data/split.json")
    ap.add_argument("--cases_out", default="data/test_cases.jsonl")
    args = ap.parse_args()

    sessions = load_all_sessions()
    train, test = stratified_split(sessions, args.test_ratio, args.seed)
    logger.info("总 sessions=%d train=%d test=%d", len(sessions), len(train), len(test))

    os.makedirs(os.path.dirname(args.split_out) or ".", exist_ok=True)
    json.dump(
        {"seed": args.seed, "test_ratio": args.test_ratio,
         "train_sessions": [s["session_id"] for s in train],
         "test_sessions": [s["session_id"] for s in test]},
        open(args.split_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2,
    )
    logger.info("写入 %s", args.split_out)

    cases = build_test_cases(test, load_users())
    with open(args.cases_out, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # 统计
    by_tpl = collections.Counter(c["template"] for c in cases)
    g = sum(1 for c in cases if c["is_guarantee"])
    logger.info("test cases=%d 保障=%d 非保障=%d", len(cases), g, len(cases) - g)
    logger.info("按 template: %s", dict(sorted(by_tpl.items())))


if __name__ == "__main__":
    main()
