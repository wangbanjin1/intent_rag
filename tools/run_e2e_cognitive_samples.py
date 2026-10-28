from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

QWEN_INTENT_DIR = Path(r"D:\coding\qwen_intent_rag")
MEMORY_SYSTEM_DIR = Path(r"D:\coding\memory_system")
COG_DATA_DIR = Path(r"D:\coding\cross_memory_data\data\cognitive_samples")

sys.path.insert(0, str(QWEN_INTENT_DIR))
sys.path.insert(0, str(MEMORY_SYSTEM_DIR / "src"))

from concurrent.futures import ThreadPoolExecutor
from gt_build import turn_gt
from memory_bridge import MemoryBridge
from pipeline import MultiIntentPipeline, load_config
from session_eval import judge_session, judge_turn, run_session, session_gt


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


def _eval_persona(p_dir: Path, work_data_root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    pipe = MultiIntentPipeline(cfg)
    bridge = MemoryBridge(data_root=work_data_root)
    pipe.memory_bridge = bridge  # type: ignore[attr-defined]
    mi_on = pipe.features.multi_instance_key

    persona = load_persona_info(p_dir)
    sessions = load_sessions(p_dir)
    uid = sessions[0]["user_id"] if sessions else p_dir.name
    print(f"\n============================================================", flush=True)
    print(f"Running E2E Cognitive 3-Tier Eval for {p_dir.name} ({uid}) | {len(sessions)} sessions", flush=True)
    print(f"============================================================", flush=True)

    user_session_records: List[Dict[str, Any]] = []
    u_sess_ok = 0
    u_turn_total = 0
    u_turn_ok = 0

    for idx, sess in enumerate(sessions, 1):
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

        # Session 结束后写入 MemorySystem (含三层记忆事件处理与工单入库)
        mem_after = bridge.record_session(sess)
        elapsed = round(time.time() - t0, 2)

        if s_ok:
            u_sess_ok += 1
        u_turn_total += t_tot_cnt
        u_turn_ok += t_ok_cnt

        status_str = "PASS" if s_ok else "FAIL"
        print(
            f"  [{uid} {idx:02d}/{len(sessions):02d}] {sid} ({tier} | {tpl} | {role} | {bp_type}:{env_name}) "
            f"-> Session={status_str}, Turn={t_ok_cnt}/{t_tot_cnt} ({elapsed}s)",
            flush=True,
        )
        if not s_ok:
            print(f"       Session Diffs: {s_diffs}", flush=True)
            print(f"       GT  : {json.dumps(s_gt, ensure_ascii=False)}", flush=True)
            print(
                f"       Pred: {json.dumps({k: v.get('pred_flat') for k, v in session_out.items()}, ensure_ascii=False)}",
                flush=True,
            )
        for td in t_details:
            if not td["match"]:
                print(
                    f"       Turn {td['turn']} ({td['intent_key']}) Wrong={td['wrong']} | "
                    f"GT={json.dumps(td.get('gt'), ensure_ascii=False)} | "
                    f"Pred={json.dumps(td.get('pred'), ensure_ascii=False)}",
                    flush=True,
                )

        turn_records = []
        ev_map = {e.get("turn", 0): e for e in (sess.get("event_sequence") or [])}
        for ptp in per_turn_pred:
            t_no = ptp["turn"]
            ev_obj = ev_map.get(t_no, {})
            u_utt = (ev_obj.get("user") or {}).get("utterance", "")
            a_utt = (ev_obj.get("agent") or {}).get("utterance", "")
            t_det_for_turn = [d for d in t_details if d["turn"] == t_no]
            turn_records.append(
                {
                    "turn": t_no,
                    "user_utterance": u_utt,
                    "agent_utterance": a_utt,
                    "memory_trace": ptp.get("memory_trace"),
                    "llm_raw": ptp.get("llm_raw", ""),
                    "preds": ptp.get("preds", {}),
                    "eval_details": t_det_for_turn,
                    "turn_pass": all(d["match"] for d in t_det_for_turn) if t_det_for_turn else True,
                }
            )

        user_session_records.append(
            {
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
                "session_pred": {
                    k: v.get("pred_flat")
                    for k, v in session_out.items()
                    if (v.get("pred_flat") or {}).get("application_name")
                },
                "session_diffs": s_diffs,
                "turn_ok_count": t_ok_cnt,
                "turn_total_count": t_tot_cnt,
                "turns": turn_records,
                "extracted_events": (mem_after.get("write_result") or {}).get("extracted_events", []),
                "ebbinghaus_decay": (mem_after.get("write_result") or {}).get("ebbinghaus_decay", {}),
                "memory_snapshot": mem_after.get("memory_snapshot", []),
                "elapsed_sec": elapsed,
            }
        )

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


def run_evaluation(selected_dirs: List[Path], work_data_root: Path) -> Dict[str, Any]:
    if work_data_root.exists():
        shutil.rmtree(work_data_root)
    work_data_root.mkdir(parents=True, exist_ok=True)

    old_cwd = os.getcwd()
    os.chdir(str(QWEN_INTENT_DIR))
    try:
        cfg_path = QWEN_INTENT_DIR / "configs" / "config_memory_qwen38_flash.json"
        cfg = load_config(str(cfg_path))
        bridge_probe = MemoryBridge(data_root=work_data_root)

        with ThreadPoolExecutor(max_workers=5) as pool:
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
    from tools.generate_cognitive_samples_html import generate_html_report

    selected = list_all_personas()
    print("Evaluating all personas in cognitive_samples:", [p.name for p in selected], flush=True)
    work_root = QWEN_INTENT_DIR / "data" / "evals" / "cognitive_work_root"
    report = run_evaluation(selected, work_root)
    out_dir = QWEN_INTENT_DIR / "eval_results" / "run_02_cognitive_samples"
    generate_html_report(report, out_dir=out_dir)
    print("\n============================================================", flush=True)
    print(
        f"OVERALL SUMMARY: Session JGA = {report['total_session_ok']}/{report['total_sessions']} "
        f"({report['session_jga']*100:.2f}%), Turn JGA = {report['total_turn_ok']}/{report['total_turns']} "
        f"({report['turn_jga']*100:.2f}%)",
        flush=True,
    )
    print("\nBY MEMORY TIER:", flush=True)
    for t_name, t_st in sorted(report["by_memory_tier"].items()):
        s_rate = t_st["session_ok"] / max(1, t_st["sessions"]) * 100
        tr_rate = t_st["turn_ok"] / max(1, t_st["turns"]) * 100
        print(
            f"  - {t_name}: Session {t_st['session_ok']}/{t_st['sessions']} ({s_rate:.1f}%), "
            f"Turn {t_st['turn_ok']}/{t_st['turns']} ({tr_rate:.1f}%)",
            flush=True,
        )
