# -*- coding: utf-8 -*-
"""package.py: 入库打包脚本 (v26.4 正式化, 替代历史手工 zip)。

打包内容: 根目录代码 + docs/ + configs/(含 _archive) + tools/(含 _archive)
        + data/{dataset_5, dataset_6_clean, split/casebank/noise/sample} + README + requirements。
排除: keys.txt 等敏感文件、embed_cache 缓存、data/evals 产物、data/_archive、__pycache__、*.zip/rar。

打包后自检 (断言, v26.2c 曾漏 .jsonl 致 dataset 缺失的教训):
  dataset_5 与 dataset_6_clean 各 17 文件、case_bank_train_d5.jsonl、split_d6_full.json 必须在包内。

用法:
  python tools/package.py                    # 产物 dist/qwen_intent_rag_v<版本>.zip
  python tools/package.py --out my.zip       # 自定义输出路径
"""
from __future__ import annotations

import argparse
import glob
import io
import logging
import os
import re
import sys
import zipfile

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- 打包白名单 ----
ROOT_FILES = sorted(glob.glob(os.path.join(ROOT, "*.py"))) + [
    os.path.join(ROOT, "README.md"),
    os.path.join(ROOT, "requirements.txt"),
]
DIRS = ["docs", "configs", "tools", "prompts"]
DATA_FILES = [
    "data/split_d5.json",
    "data/split_d6_full.json",
    "data/case_bank_train_d5.jsonl",
    "data/sample_session.json",
] + sorted(glob.glob(os.path.join(ROOT, "data", "_noise_exclude*.json"))) \
  + sorted(glob.glob(os.path.join(ROOT, "data", "split_d930*.json")))
DATA_DIRS = ["data/dataset_5", "data/dataset_6_clean", "data/dataset_930",
             "data/formats", "data/semi_structured_dataset"]


def _excluded(path: str) -> bool:
    p = path.replace("\\", "/")
    return (
        "keys.txt" in p or p.endswith(".key") or p.endswith(".env")
        or "__pycache__" in p or p.endswith(".pyc")
        or "data/evals/" in p or "data/_archive/" in p
        or "embed_cache" in p
        or p.endswith((".zip", ".rar", ".tar.gz"))
    )


def collect() -> list[str]:
    """返回待打包的绝对路径清单 (确定性排序)。"""
    files: list[str] = []
    for f in ROOT_FILES + DATA_FILES:
        if os.path.isfile(f) and not _excluded(f):
            files.append(f)
        elif not os.path.isfile(f):
            logger.warning("[warn] 缺失: %s", os.path.relpath(f, ROOT))
    for d in DIRS + DATA_DIRS:
        base = os.path.join(ROOT, d)
        for root, dirs, names in os.walk(base):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for n in sorted(names):
                p = os.path.join(root, n)
                if not _excluded(p):
                    files.append(p)
    return sorted(set(files))


def package(out_path: str) -> str:
    files = collect()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.relpath(f, ROOT))
    verify(out_path)
    return out_path


def verify(zip_path: str) -> None:
    """打包后自检断言 (入库最低完整性)。"""
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        d5 = [n for n in names if n.startswith("data/dataset_5/") and n.endswith(".jsonl")]
        d6 = [n for n in names if n.startswith("data/dataset_6_clean/") and n.endswith(".jsonl")]
        d930 = [n for n in names if n.startswith("data/dataset_930/") and n.endswith(".jsonl")]
        must = [
            "data/case_bank_train_d5.jsonl",
            "data/split_d5.json",
            "data/split_d6_full.json",
            "data/split_d930_full.json",
            "data/_noise_exclude_d930_scan.json",
            "data/formats/registry.json",
            "data/formats/semi_261022.json",
            "data/semi_structured_dataset/261022.jsonl",
            "features.py",
            "gt_build.py",
            "pipeline.py",
            "session_eval.py",
            "candidate_llm.py",
            "candidate_book.py",
            "time_extractor.py",
            "format_registry.py",
            "configs/config_d6_qwen_plus_best.json",
            "configs/config_d6_qwen38_27b_v273.json",
            "README.md",
        ]
        missing = [m for m in must if m not in names]
        assert not missing, f"打包缺失: {missing}"
        assert len(d5) >= 16, f"dataset_5 文件不足: {len(d5)}"
        assert len(d6) >= 16, f"dataset_6_clean 文件不足: {len(d6)}"
        assert len(d930) >= 16, f"dataset_930 文件不足: {len(d930)}"
        # 敏感信息不外带
        leak = [n for n in names if "keys" in n.lower() or "embed_cache" in n]
        assert not leak, f"敏感/缓存文件泄漏: {leak}"
        logger.info(
            "[verify] dataset_5=%d 文件, dataset_6_clean=%d 文件, dataset_930=%d 文件, must-have=%d 全在, 无敏感泄漏",
            len(d5), len(d6), len(d930), len(must),
        )
    logger.info("[verify] OK -> %s", zip_path)


def verify_local_vs_zip(zip_path: str) -> bool:
    """版本一致性自检: 本地工作目录(打包白名单) vs dist 包逐文件字节对比。

    检查三个方向:
      A. 包内有而本地已改/已删的文件 (改代码后忘重打包)
      B. 本地白名单内而包缺失的文件 (打包清单过期)
      C. 双方都在但字节不一致的文件 (内容漂移)
    返回 True=完全一致。任何不一致打印明细并返回 False。
    """
    if not os.path.isfile(zip_path):
        logger.error("[verify-local] 包不存在: %s", zip_path)
        return False
    with zipfile.ZipFile(zip_path) as zf:
        zip_files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    local_files = {os.path.relpath(f, ROOT).replace("\\", "/"): f for f in collect()}

    only_zip = sorted(set(zip_files) - set(local_files))
    only_local = sorted(set(local_files) - set(zip_files))
    changed = []
    for n in sorted(set(zip_files) & set(local_files)):
        with open(local_files[n], "rb") as fh:
            if fh.read() != zip_files[n]:
                changed.append(n)

    ok = not (only_zip or only_local or changed)
    logger.info("[verify-local] 本地 %d 文件 vs 包 %d 文件", len(local_files), len(zip_files))
    if only_zip:
        logger.warning("  包内有而本地白名单缺失(%d): %s...", len(only_zip), only_zip[:5])
    if only_local:
        logger.warning("  本地有而包缺失(%d): %s...", len(only_local), only_local[:5])
    if changed:
        logger.warning("  内容不一致(%d): %s%s", len(changed), changed[:8], "..." if len(changed) > 8 else "")
    logger.info(
        "[verify-local] %s",
        "[OK] 本地与包完全一致" if ok else "[DRIFT] 存在漂移 (改代码后请重跑 python tools/package.py)",
    )
    return ok


def latest_zip() -> str:
    """dist/ 下最新修改的 zip。"""
    d = os.path.join(ROOT, "dist")
    zips = glob.glob(os.path.join(d, "*.zip"))
    return max(zips, key=os.path.getmtime) if zips else ""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--verify", action="store_true",
                    help="不打包, 校验本地工作目录与 dist 最新包的一致性")
    args = ap.parse_args()
    if args.verify:
        zp = args.out or latest_zip()
        if not zp:
            logger.error("[verify-local] dist/ 下无 zip, 先运行 python tools/package.py")
            sys.exit(2)
        sys.exit(0 if verify_local_vs_zip(zp) else 1)
    # 从 CHANGELOG 抓最新版本号命名（## 与 ### 级都算，v28 链为 ### 条目；
    # 标题带"封死"的路线条目跳过——已删除路线不得用于包命名）
    ver = "v_latest"
    cl = os.path.join(ROOT, "docs", "CHANGELOG.md")
    if os.path.isfile(cl):
        for line in io.open(cl, encoding="utf-8"):
            m = re.match(r"#+ (v[\w.\-]+)", line)
            if m and "封死" not in line:
                ver = m.group(1)
    out = args.out or os.path.join(ROOT, "dist", f"qwen_intent_rag_{ver}.zip")
    files = collect()
    logger.info("collect: %d files", len(files))
    package(out)


if __name__ == "__main__":
    main()
