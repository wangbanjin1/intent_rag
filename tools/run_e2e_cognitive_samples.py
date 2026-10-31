from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

QWEN_INTENT_DIR = Path(r"D:\coding\qwen_intent_rag")
MEMORY_SYSTEM_DIR = Path(r"D:\coding\memory_system")
COG_DATA_DIR = Path(r"D:\coding\cross_memory_data\data\cognitive_samples")

sys.path.insert(0, str(QWEN_INTENT_DIR))
sys.path.insert(0, str(MEMORY_SYSTEM_DIR / "src"))

from gt_build import turn_gt
from memory_bridge import MemoryBridge
from pipeline import MultiIntentPipeline, load_config
from session_eval import judge_session, judge_turn, run_session, session_gt

logger = logging.getLogger(__name__)


def load_persona_info(persona_dir: Path) -> Dict[str, Any]:
    p_file = persona_dir / "persona.json"
    if p_file.exists():
        return json.loads(p_file.read_text(encoding="utf-8"))
    return {"persona_id": persona_dir.name}


def load_sessions(persona_dir: Path) -> List[Dict[str, Any]]:
    s_json = persona_dir / "sessions.json"
    if s_json.exists():
        return json.loads(s_json.read_text(encoding="utf-8"))
    s_jsonl = persona_dir / "sessions.jsonl"
    out = []
    for line in s_jsonl.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def list_all_personas() -> List[Path]:
    return sorted(
        [d for d in COG_DATA_DIR.iterdir() if d.is_dir() and d.name.startswith("mobile_persona_")],
        key=lambda p: p.name,
    )


def _eval_single_cognitive_session(
    idx: int,
    total_sessions: int,
    uid: str,
    sess: Dict[str, Any],
    pipe: MultiIntentPipeline,
    bridge: MemoryBridge,
    mi_on: bool,
) -> tuple[bool, int, int, Dict[str, Any]]:
    """执行并评估单条认知记忆测试会话。"""
    sid = sess["session_id"]
    ref_time = sess.get("reference_time", "")
    meta = sess.get("session_meta") or {}
    tier = meta.get("memory_tier", "factual_memory")
    role = meta.get("memory_role", "unknown")
    tpl = meta.get("template_id", "")
    scenario = meta.get("scenario") or {}
    env_name = scenario.get("environment", "")
    bp_type = scenario.get("blueprint_type", "")

    t0 = time.time()
    session_out, per_turn_pred = run_session(pipe, sess, retrieve_on=True)
    s_gt = session_gt(sess, mi_on=mi_on)
    s_ok, s_diffs = judge_session(s_gt, session_out)

    t_gts = turn_gt(sess, cancel_credit=True, correction_credit=True, gt_derive=True, mi_on=mi_on)
    t_ok_cnt, t_tot_cnt, t_details = judge_turn(t_gts, per_turn_pred)

    predicted_final = {
        k: v.get("pred_flat")
        for k, v in session_out.items()
        if (v.get("pred_flat") or {}).get("application_name")
    }
    mem_after = bridge.record_session(sess, predicted_final=predicted_final)
    elapsed = round(time.time() - t0, 2)

    status_str = "PASS" if s_ok else "FAIL"
    logger.info(
        "  [%s %02d/%02d] %s (%s | %s | %s | %s:%s) -> Session=%s, Turn=%d/%d (%.2fs)",
        uid, idx, total_sessions, sid, tier, tpl, role, bp_type, env_name, status_str, t_ok_cnt, t_tot_cnt, elapsed,
    )
    if not s_ok:
        logger.info("       Session Diffs: %s", s_diffs)
        logger.info("       GT  : %s", json.dumps(s_gt, ensure_ascii=False))
        logger.info("       Pred: %s", json.dumps(predicted_final, ensure_ascii=False))
    for td in t_details:
        if not td["match"]:
            logger.info(
                "       Turn %s (%s) Wrong=%s | GT=%s | Pred=%s",
                td["turn"],
                td["intent_key"],
                td["wrong"],
                json.dumps(td.get("gt"), ensure_ascii=False),
                json.dumps(td.get("pred"), ensure_ascii=False),
            )

    ev_map = {e.get("turn", 0): e for e in (sess.get("event_sequence") or [])}
    turn_records = [
        {
            "turn": ptp["turn"],
            "user_utterance": ((ev_map.get(ptp["turn"], {}).get("user") or {}).get("utterance", "")),
            "agent_utterance": ((ev_map.get(ptp["turn"], {}).get("agent") or {}).get("utterance", "")),
            "memory_trace": ptp.get("memory_trace"),
            "llm_raw": ptp.get("llm_raw", ""),
            "preds": ptp.get("preds", {}),
            "eval_details": [d for d in t_details if d["turn"] == ptp["turn"]],
            "turn_pass": all(d["match"] for d in t_details if d["turn"] == ptp["turn"]),
        }
        for ptp in per_turn_pred
    ]
    record = {
        "session_id": sid,
        "reference_time": ref_time,
        "memory_tier": tier,
        "template_id": tpl,
        "memory_role": role,
        "blueprint_type": bp_type,
        "environment": env_name,
        "memory_action": scenario.get("memory_action", ""),
        "session_pass": s_ok,
        "session_gt": s_gt,
        "session_pred": predicted_final,
        "session_diffs": s_diffs,
        "turn_ok_count": t_ok_cnt,
        "turn_total_count": t_tot_cnt,
        "turns": turn_records,
        "extracted_events": (mem_after.get("write_result") or {}).get("extracted_events", []),
        "ebbinghaus_decay": (mem_after.get("write_result") or {}).get("ebbinghaus_decay", {}),
        "memory_snapshot": mem_after.get("memory_snapshot", []),
        "elapsed_sec": elapsed,
    }
    return s_ok, t_ok_cnt, t_tot_cnt, record


def _eval_persona(p_dir: Path, work_data_root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    pipe = MultiIntentPipeline(cfg)
    bridge = MemoryBridge(data_root=work_data_root)
    pipe.memory_bridge = bridge  # type: ignore[attr-defined]
    mi_on = pipe.features.multi_instance_key

    persona = load_persona_info(p_dir)
    sessions = load_sessions(p_dir)
    uid = sessions[0]["user_id"] if sessions else p_dir.name
    logger.info("============================================================")
    logger.info("Running E2E Cognitive 3-Tier Eval for %s (%s) | %d sessions", p_dir.name, uid, len(sessions))
    logger.info("============================================================")

    user_session_records: List[Dict[str, Any]] = []
    u_sess_ok = 0
    u_turn_total = 0
    u_turn_ok = 0

    for idx, sess in enumerate(sessions, 1):
        s_ok, t_ok_cnt, t_tot_cnt, rec = _eval_single_cognitive_session(
            idx, len(sessions), uid, sess, pipe, bridge, mi_on
        )
        if s_ok:
            u_sess_ok += 1
        u_turn_total += t_tot_cnt
        u_turn_ok += t_ok_cnt
        user_session_records.append(rec)

    return {
        "persona_dir": p_dir.name,
        "user_id": uid,
        "persona": persona,
        "session_total": len(sessions),
        "session_ok": u_sess_ok,
        "session_jga": round(u_sess_ok / max(1, len(sessions)), 4),
        "turn_total": u_turn_total,
        "turn_ok": u_turn_ok,
        "turn_jga": round(u_turn_ok / max(1, u_turn_total), 4),
        "sessions": user_session_records,
    }


def run_evaluation(
    selected_dirs: List[Path],
    work_data_root: Path,
    cfg_path: Path | None = None,
    max_workers: int = 5,
) -> Dict[str, Any]:
    if work_data_root.exists():
        shutil.rmtree(work_data_root)
    work_data_root.mkdir(parents=True, exist_ok=True)

    old_cwd = os.getcwd()
    os.chdir(str(QWEN_INTENT_DIR))
    try:
        cfg_path = cfg_path or (QWEN_INTENT_DIR / "configs" / "config_memory_qwen38_flash.json")
        cfg = load_config(str(cfg_path))
        bridge_probe = MemoryBridge(data_root=work_data_root)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_eval_persona, p_dir, work_data_root, cfg) for p_dir in selected_dirs]
            all_user_reports = [f.result() for f in futures]

        total_sessions = 0
        total_session_ok = 0
        total_turns = 0
        total_turn_ok = 0
        tier_stats: Dict[str, Dict[str, int]] = {}
        role_stats: Dict[str, Dict[str, int]] = {}
        blueprint_stats: Dict[str, Dict[str, int]] = {}

        for u_rep in all_user_reports:
            for s in u_rep["sessions"]:
                s_ok = s["session_pass"]
                t_ok_cnt = s["turn_ok_count"]
                t_tot_cnt = s["turn_total_count"]
                tier = s["memory_tier"]
                role = s["memory_role"]
                bp_type = s["blueprint_type"]

                total_sessions += 1
                if s_ok:
                    total_session_ok += 1
                total_turns += t_tot_cnt
                total_turn_ok += t_ok_cnt

                for stat_dict, key_name in (
                    (tier_stats, tier),
                    (role_stats, role),
                    (blueprint_stats, f"{tier}:{bp_type}"),
                ):
                    st_entry = stat_dict.setdefault(
                        key_name, {"sessions": 0, "session_ok": 0, "turns": 0, "turn_ok": 0}
                    )
                    st_entry["sessions"] += 1
                    if s_ok:
                        st_entry["session_ok"] += 1
                    st_entry["turns"] += t_tot_cnt
                    st_entry["turn_ok"] += t_ok_cnt

        summary = {
            "intent_model": cfg["llm"]["model"],
            "memory_model": bridge_probe.ms.config.llm["model"],
            "embedding_model": cfg["embedder"]["model"],
            "dataset_dir": str(COG_DATA_DIR),
            "personas": [d.name for d in selected_dirs],
            "total_sessions": total_sessions,
            "total_session_ok": total_session_ok,
            "session_jga": round(total_session_ok / max(1, total_sessions), 4),
            "total_turns": total_turns,
            "total_turn_ok": total_turn_ok,
            "turn_jga": round(total_turn_ok / max(1, total_turns), 4),
            "by_memory_tier": tier_stats,
            "by_memory_role": role_stats,
            "by_blueprint": blueprint_stats,
            "users": all_user_reports,
        }
        return summary
    finally:
        os.chdir(old_cwd)
        if work_data_root.exists():
            shutil.rmtree(work_data_root, ignore_errors=True)


if __name__ == "__main__":
    import argparse

    from tools.generate_cognitive_samples_html import generate_html_report

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", default="", help="逗号分隔的 persona 编号, 如 014,016 (默认全部)")
    ap.add_argument("--config", default="", help="意图模型 config 路径")
    ap.add_argument("--out", default="", help="输出目录 (默认 eval_results/run_02_cognitive_samples)")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--no_html", action="store_true")
    args = ap.parse_args()

    selected = list_all_personas()
    if args.personas:
        wanted = {x.strip().zfill(3) for x in args.personas.split(",") if x.strip()}
        selected = [p for p in selected if p.name[-3:] in wanted]
    logger.info("Evaluating personas: %s", [p.name for p in selected])
    tag = Path(args.out).name if args.out else "default"
    work_root = QWEN_INTENT_DIR / "data" / "evals" / f"cognitive_work_root_{tag}"
    report = run_evaluation(
        selected, work_root,
        cfg_path=Path(args.config) if args.config else None,
        max_workers=args.workers,
    )
    out_dir = Path(args.out) if args.out else QWEN_INTENT_DIR / "eval_results" / "run_02_cognitive_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.no_html:
        generate_html_report(report, out_dir=out_dir)
    logger.info("============================================================")
    logger.info(
        "OVERALL SUMMARY: Session JGA = %d/%d (%.2f%%), Turn JGA = %d/%d (%.2f%%)",
        report["total_session_ok"],
        report["total_sessions"],
        report["session_jga"] * 100,
        report["total_turn_ok"],
        report["total_turns"],
        report["turn_jga"] * 100,
    )
    logger.info("BY MEMORY TIER:")
    for t_name, t_st in sorted(report["by_memory_tier"].items()):
        s_rate = t_st["session_ok"] / max(1, t_st["sessions"]) * 100
        tr_rate = t_st["turn_ok"] / max(1, t_st["turns"]) * 100
        logger.info(
            "  - %s: Session %d/%d (%.1f%%), Turn %d/%d (%.1f%%)",
            t_name, t_st["session_ok"], t_st["sessions"], s_rate, t_st["turn_ok"], t_st["turns"], tr_rate,
        )
