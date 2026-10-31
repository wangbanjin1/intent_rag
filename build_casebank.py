"""build_casebank.py: 从 data/dataset/*.jsonl 生成检索案例库。

用法:
  python build_casebank.py                     # 用 data/split.json 的 train_sessions
  python build_casebank.py --all              # 用全部 dataset (会含测试集, 仅分析用)
  python build_casebank.py --split data/split.json

输出 data/case_bank_train.jsonl (每行一个完整原始 session), 供 CaseBank.load 检索。
案例库以"原始 session 格式"持久化 (retrieval_bank.CaseBank.load 直接读),
加载时 extract_turn_cases 拆出 turn-level case (含 prev_state 累积 / coreference 解析)。
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logger = logging.getLogger(__name__)


DATASET_DIR = os.environ.get("DST_DATASET", "data/dataset_5")


def list_dataset_files() -> list[str]:
    return sorted(glob.glob(f"{DATASET_DIR}/sessions.*"))


def iter_dataset_sessions(path: str):
    """兼容 .json(JSON数组) 和 .jsonl(每行一个JSON) 两种格式。"""
    if path.endswith(".json"):
        for s in json.load(open(path, "r", encoding="utf-8")):
            yield s
    else:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def load_all_sessions() -> list[dict]:
    out = []
    for f in list_dataset_files():
        out.extend(iter_dataset_sessions(f))
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="用全部 dataset(含测试集, 仅分析用)")
    ap.add_argument("--split", default="data/split.json", help="split.json 路径")
    ap.add_argument("-o", "--out", default="data/case_bank_train.jsonl")
    args = ap.parse_args()

    sessions = load_all_sessions()
    if args.all:
        sel = sessions
    else:
        if not os.path.exists(args.split):
            logger.error("[!] split 文件不存在: %s, 先运行 make_split.py。可用 --all 兜底。", args.split)
            sys.exit(1)
        split = json.load(open(args.split, "r", encoding="utf-8"))
        train_ids = set(split["train_sessions"])
        sel = [s for s in sessions if s["session_id"] in train_ids]
        logger.info("split train_sessions=%d  匹配到 %d 条", len(train_ids), len(sel))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in sel:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    logger.info("写入 %d 条 session -> %s", len(sel), args.out)


if __name__ == "__main__":
    main()
