"""error_report.py: 每次评估后的错误样本归类 + HTML 可视化报告。

输入: session_eval.py 产出的 eval_dual_*.jsonl（逐 session，含 turn_detail gt/pred/llm_raw）
     + 原始数据集（取对话内容、GT 字段来源 source_type）
输出: 单文件 HTML 报告（错误归类总结 + 每类描述 + 全部错误样本明细 + LLM 原始输出）

拆分模块:
  - report_metrics.py:   数据加载、GT 来源映射、一级/二级错误归类与指标聚合
  - report_templates.py: HTML/CSS/JS 模板与组件渲染

用法:
  python error_report.py --eval data/eval_dual_v11_demo_chain.jsonl
  python error_report.py --eval data/eval_dual_v11_demo_chain.jsonl --dataset data/dataset_merged --out reports/v11.html
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from report_metrics import (
    APPSVC_FIELDS,
    CAT_META,
    CAT_ORDER,
    EVAL_SLOTS,
    FIELD_LABEL,
    NESTED_TO_FLAT,
    REC_MATCH_FIELDS,
    TAG_META,
    TAG_ORDER,
    TIME_FIELDS,
    TURN_CATS,
    _SEL_RE,
    _cum_gt_by_turn,
    _iter_leaf_params,
    _norm_val,
    _pred_by_turn,
    _rec_brief,
    _rec_flat,
    _turn_utterance,
    _walk_gt_leaves,
    aggregate_eval_metrics,
    build_source_map,
    categorize,
    diff_fields,
    dup_key_sessions,
    latest_source,
    load_eval,
    load_noise,
    load_sessions,
    load_user_profiles,
    load_users,
    near_keys,
    scenario_tag,
)
from report_templates import (
    _CSS,
    _JS,
    _MI,
    _state_pane_html,
    analysis_text,
    conversation_html,
    diff_table_html,
    llm_raw_html,
    raw_data_html,
    record_analysis,
    render_report_page,
    slot_updates_table_html,
    state_evolution_html,
)

__all__ = [
    "APPSVC_FIELDS",
    "CAT_META",
    "CAT_ORDER",
    "EVAL_SLOTS",
    "FIELD_LABEL",
    "NESTED_TO_FLAT",
    "REC_MATCH_FIELDS",
    "TAG_META",
    "TAG_ORDER",
    "TIME_FIELDS",
    "TURN_CATS",
    "_CSS",
    "_JS",
    "_MI",
    "_SEL_RE",
    "_cum_gt_by_turn",
    "_iter_leaf_params",
    "_norm_val",
    "_pred_by_turn",
    "_rec_brief",
    "_rec_flat",
    "_state_pane_html",
    "_turn_utterance",
    "_walk_gt_leaves",
    "aggregate_eval_metrics",
    "analysis_text",
    "build_source_map",
    "categorize",
    "conversation_html",
    "diff_fields",
    "diff_table_html",
    "dup_key_sessions",
    "generate",
    "latest_source",
    "llm_raw_html",
    "load_eval",
    "load_noise",
    "load_sessions",
    "load_user_profiles",
    "load_users",
    "main",
    "near_keys",
    "raw_data_html",
    "record_analysis",
    "render_report_page",
    "scenario_tag",
    "slot_updates_table_html",
    "state_evolution_html",
]


def generate(
    eval_path: str,
    dataset_dir: str,
    out_path: str,
    noise_path: str = "data/_noise_exclude.json",
) -> dict[str, Any]:
    """执行错误归类并生成单文件 HTML 错误分析报告。"""
    all_results = load_eval(eval_path)
    sessions = load_sessions(dataset_dir)
    users = load_users(dataset_dir)
    profiles = load_user_profiles(dataset_dir)
    noise_map = load_noise(noise_path)

    noise_ids = set(noise_map)
    results = [r for r in all_results if r["session_id"] not in noise_ids]
    noise_in_eval = [r for r in all_results if r["session_id"] in noise_ids]

    summary_path = eval_path.replace(".jsonl", "_summary.json")
    _mi = False
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as sf:
            _mi = bool(json.load(sf).get("multi_instance_key"))
    _MI[0] = _mi

    src_maps: dict[str, dict[str, Any]] = {}
    for r in results:
        s = sessions.get(r["session_id"])
        src_maps[r["session_id"]] = build_source_map(s, mi_on=_mi) if s else {}

    metrics = aggregate_eval_metrics(results, sessions, src_maps)
    page = render_report_page(
        eval_path=eval_path,
        dataset_dir=dataset_dir,
        metrics=metrics,
        sessions=sessions,
        users=users,
        profiles=profiles,
        src_maps=src_maps,
        noise_map=noise_map,
        noise_in_eval=noise_in_eval,
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)

    cases = metrics["cases"]
    tag_counter = metrics["tag_counter"]
    n_sess = metrics["n_sess"]
    n_sess_match = metrics["n_sess_match"]
    n_turn = metrics["n_turn"]
    n_turn_match = metrics["n_turn_match"]
    sess_set_mismatch = metrics["sess_set_mismatch"]
    sess_field_wrong = metrics["sess_field_wrong"]
    rate_s = n_sess_match / max(n_sess, 1)
    rate_t = n_turn_match / max(n_turn, 1)

    print(
        f"\n[error_report] session {n_sess_match}/{n_sess}={rate_s:.2%}  "
        f"turn {n_turn_match}/{n_turn}={rate_t:.2%}"
    )
    print(" [turn 级]")
    for c in CAT_ORDER:
        if cases.get(c):
            print(f"  {CAT_META[c]['title']}: {len(cases[c])}")
    print(" [二级标签·原因维度]")
    for t in TAG_ORDER:
        if tag_counter.get(t):
            print(f"  {t} {TAG_META[t]['name']}: {tag_counter[t]}")
    print(
        f" [session 级] 意图集合不一致: {len(sess_set_mismatch)}  "
        f"字段错误: {len(sess_field_wrong)}"
    )
    print(f"[error_report] HTML -> {out_path}")
    return {
        "cases": {k: len(v) for k, v in cases.items()},
        "tags": dict(tag_counter),
        "out": out_path,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", required=True, help="session_eval 产出的 eval_dual_*.jsonl")
    ap.add_argument("--dataset", default=os.environ.get("DST_DATASET", "data/dataset_merged"))
    ap.add_argument("--out", default="", help="输出 html 路径（默认与 eval 同名 .html）")
    ap.add_argument(
        "--noise",
        default="data/_noise_exclude.json",
        help="噪声清单路径（find_noise.py 产物，默认 data/_noise_exclude.json）",
    )
    args = ap.parse_args()
    out = args.out or os.path.splitext(args.eval)[0] + "_errors.html"
    generate(args.eval, args.dataset, out, noise_path=args.noise)


if __name__ == "__main__":
    main()
