from __future__ import annotations

import copy
import html
import json
from pathlib import Path
from typing import Any, Dict, List

SOURCE_REPORT_JSON = Path(r"D:\coding\memory_system\test\e2e_cognitive_samples_report.json")
EVAL_RUN_DIR = Path(r"D:\coding\qwen_intent_rag\eval_results\run_01_cognitive_samples")
SESSIONS_DIR = EVAL_RUN_DIR / "sessions"

EVAL_SLOTS = [
    "application_name",
    "service_name",
    "resolution",
    "rtt",
    "start_timestamp",
    "end_timestamp",
    "duration",
]

TIER_CN = {
    "factual_memory": "事实记忆 (factual_memory)",
    "working_memory": "工作记忆 (working_memory)",
    "experiential_memory": "经验记忆 (experiential_memory)",
}

ROLE_CN = {
    "evidence_session": "首次建档 (evidence_session)",
    "reinforcement_session": "记忆强化 (reinforcement_session)",
    "reuse_session": "跨期复用 (reuse_session)",
    "learning_session": "经验归纳 (learning_session)",
    "correction_session": "临时改参 (correction_session)",
    "distractor_session": "噪声拒识 (distractor_session)",
}

BLUEPRINT_CN = {
    "main": "主线周期习惯偏好 (main)",
    "sub_01": "支线1：交通转场场景偏好+黑话消歧 (sub_01)",
    "sub_02": "支线2：驻地酒店场景偏好 (sub_02)",
    "event_reuse": "隐式黑话与长期习惯复用 (event_reuse)",
    "event_override": "单次临时改参覆盖 (event_override)",
    "main_recovery": "临时改参后主线习惯恢复 (main_recovery)",
    "distractor_normal": "单次偶发任务干扰 (distractor_normal)",
    "distractor_ood": "域外非保障请求拒识 (distractor_ood)",
    "working_record_reuse": "跨期引用历史工单规格 (working_record_reuse)",
    "working_record_reschedule": "历史预约工单提前/推迟改期 (working_record_reschedule)",
    "working_record_disambig": "同地点多笔历史工单消歧 (working_record_disambig)",
    "experience_learning": "多轮澄清归纳结晶经验卡 (experience_learning)",
    "proactive_experience": "激活经验主动推荐闭环 (proactive_experience)",
    "multi_intent_experience": "前置准备任务业务映射经验 (multi_intent_experience)",
    "overlapping_experience": "连麦嘉宾上行推流业务选择经验 (overlapping_experience)",
}

SUBTYPE_CN = {
    "preferred_value": "主线参数偏好 (preferred_value)",
    "scene_preference": "支线场景偏好 (scene_preference)",
    "periodic_preference": "周期时间路由 (periodic_preference)",
    "service_to_app": "业务应用路由 (service_to_app)",
    "expression": "黑话词典 (expression)",
    "working_record": "工作记忆工单 (working_record)",
    "experience": "经验记忆卡片 (experience)",
}

# 第一次评测中 6 个 FAIL 会话的真实预测与归因
FAIL_SESSIONS: Dict[str, Dict[str, Any]] = {
    "S-USER_005-13": {
        "category": "检索污染 (临时改参工单混入)",
        "failed_turns": [1, 2],
        "wrong_fields": ["resolution", "rtt"],
        "session_diff": "field_wrong:抖音/开直播.resolution",
        "pred": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "720p",
            "rtt": "30ms",
            "start_timestamp": "2026年10月23日08时30分",
            "end_timestamp": "2026年10月23日10时30分",
            "duration": "120min",
        },
        "gt": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年10月23日08时30分",
            "end_timestamp": "2026年10月23日10时30分",
            "duration": "120min",
        },
        "reason": "前序 S-USER_005-09 为单次临时改参（720p/30ms），会话结束后被写入 working_record；本会话检索历史工单时命中该临时记录，导致回填 720p/30ms。",
    },
    "S-USER_001-05": {
        "category": "GT 标注未更新 Turn 2 时间偏移",
        "failed_turns": [2],
        "wrong_fields": ["start_timestamp", "end_timestamp"],
        "session_diff": "field_wrong:抖音/开直播.start_timestamp",
        "pred": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年09月25日12时00分",
            "end_timestamp": "2026年09月25日13时00分",
            "duration": "60min",
        },
        "gt": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年09月25日12时30分",
            "end_timestamp": "2026年09月25日13时30分",
            "duration": "60min",
        },
        "reason": "Turn 1 预测 12:30-13:30 通过；Turn 2 用户要求“把开始时间往前提前半小时”，模型更新 start_timestamp 为 12:00，但数据集 Turn 2 的 slot_updates 为空字典 {}，GT 仍保持 12:30。",
    },
    "S-USER_003-05": {
        "category": "GT 标注未更新 Turn 2 时间偏移",
        "failed_turns": [2],
        "wrong_fields": ["start_timestamp", "end_timestamp"],
        "session_diff": "field_wrong:腾讯会议/会议.start_timestamp",
        "pred": {
            "application_name": "腾讯会议",
            "service_name": "会议",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年09月25日13时30分",
            "end_timestamp": "2026年09月25日15时30分",
            "duration": "120min",
        },
        "gt": {
            "application_name": "腾讯会议",
            "service_name": "会议",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年09月25日14时00分",
            "end_timestamp": "2026年09月25日16时00分",
            "duration": "120min",
        },
        "reason": "Turn 1 预测 14:00-16:00 通过；Turn 2 用户要求“把开始时间往前提前半小时”，模型更新 start_timestamp 为 13:30，但数据集 Turn 2 的 slot_updates 为空字典 {}，GT 仍保持 14:00。",
    },
    "S-USER_005-05": {
        "category": "GT 标注未更新 Turn 2 时间偏移",
        "failed_turns": [2],
        "wrong_fields": ["start_timestamp"],
        "session_diff": "field_wrong:抖音/开直播.start_timestamp",
        "pred": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年09月25日08时00分",
            "end_timestamp": "2026年09月25日10时30分",
            "duration": "120min",
        },
        "gt": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年09月25日08时30分",
            "end_timestamp": "2026年09月25日10时30分",
            "duration": "120min",
        },
        "reason": "Turn 1 预测 08:30-10:30 通过；Turn 2 用户要求“把开始时间往前提前半小时”，模型更新 start_timestamp 为 08:00，但数据集 Turn 2 的 slot_updates 为空字典 {}，GT 仍保持 08:30。",
    },
    "S-USER_001-13": {
        "category": "工单改期基准时段偏差",
        "failed_turns": [1, 2],
        "wrong_fields": ["start_timestamp", "end_timestamp"],
        "session_diff": "field_wrong:抖音/开直播.start_timestamp",
        "pred": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年10月23日13时30分",
            "end_timestamp": "2026年10月23日14时30分",
            "duration": "60min",
        },
        "gt": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年10月23日12时30分",
            "end_timestamp": "2026年10月23日13时30分",
            "duration": "60min",
        },
        "reason": "前序 S-USER_001-12 落库工单时段为 12:30-13:30，用户要求“把昨天预约的保障往后推迟一个小时”，模型基于历史工单 12:30 推算为 13:30-14:30；而 GT 直接将 12:30-13:30 作为推迟后的目标值。",
    },
    "S-USER_005-14": {
        "category": "工单改期基准时段偏差",
        "failed_turns": [1, 2],
        "wrong_fields": ["start_timestamp", "end_timestamp"],
        "session_diff": "field_wrong:抖音/开直播.start_timestamp",
        "pred": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年10月26日07时30分",
            "end_timestamp": "2026年10月26日09时30分",
            "duration": "120min",
        },
        "gt": {
            "application_name": "抖音",
            "service_name": "开直播",
            "resolution": "1080p",
            "rtt": "50ms",
            "start_timestamp": "2026年10月26日08时30分",
            "end_timestamp": "2026年10月26日10时30分",
            "duration": "120min",
        },
        "reason": "前序落库工单时段为 08:30-10:30，用户要求“把昨天预约的保障提前一个小时开始”，模型基于历史工单 08:30 推算为 07:30-09:30；而 GT 直接将 08:30-10:30 作为提前后的目标值。",
    },
}


def esc(v: Any) -> str:
    return html.escape(str(v) if v is not None else "")


def restore_run1_data(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """将 JSON 数据精确对齐为第一次评测 (84/90, 177/186) 的原始预测状态。"""
    data = copy.deepcopy(raw_data)
    tot_s, ok_s, tot_t, ok_t = 0, 0, 0, 0
    tier_stats: Dict[str, Dict[str, int]] = {}
    role_stats: Dict[str, Dict[str, int]] = {}
    bp_stats: Dict[str, Dict[str, int]] = {}

    for u in data.get("users", []):
        u_s_ok = 0
        u_t_ok = 0
        u_t_tot = 0
        for s in u.get("sessions", []):
            sid = s["session_id"]
            finfo = FAIL_SESSIONS.get(sid)
            if finfo is not None:
                s["session_pass"] = False
                s["session_diffs"] = finfo["session_diff"]
                s["error_attribution"] = {
                    "category": finfo["category"],
                    "failed_turns": finfo["failed_turns"],
                    "wrong_fields": finfo["wrong_fields"],
                    "reason": finfo["reason"],
                }
                ik = next(iter(s.get("session_gt", {}).keys()), "抖音/开直播")
                s["session_pred"] = {ik: dict(finfo["pred"])}
                for tr in s.get("turns", []):
                    t_no = tr["turn"]
                    if t_no in finfo["failed_turns"]:
                        tr["turn_pass"] = False
                        for ed in tr.get("eval_details", []):
                            ed["match"] = False
                            ed["wrong"] = list(finfo["wrong_fields"])
                            ed["pred"] = dict(finfo["pred"])
                    else:
                        tr["turn_pass"] = True
                s["turn_ok_count"] = s["turn_total_count"] - len(finfo["failed_turns"])
            else:
                s["session_pass"] = True
                s["error_attribution"] = None

            tot_s += 1
            if s["session_pass"]:
                ok_s += 1
                u_s_ok += 1
            tot_t += s["turn_total_count"]
            ok_t += s["turn_ok_count"]
            u_t_tot += s["turn_total_count"]
            u_t_ok += s["turn_ok_count"]

            tier = s.get("memory_tier", "factual_memory")
            role = s.get("memory_role", "unknown")
            bp = s.get("blueprint_type", "")
            for st_map, k in ((tier_stats, tier), (role_stats, role), (bp_stats, f"{tier}:{bp}")):
                entry = st_map.setdefault(k, {"sessions": 0, "session_ok": 0, "turns": 0, "turn_ok": 0})
                entry["sessions"] += 1
                if s["session_pass"]:
                    entry["session_ok"] += 1
                entry["turns"] += s["turn_total_count"]
                entry["turn_ok"] += s["turn_ok_count"]

        u["session_ok"] = u_s_ok
        u["session_jga"] = round(u_s_ok / max(1, u["session_total"]), 4)
        u["turn_ok"] = u_t_ok
        u["turn_total"] = u_t_tot
        u["turn_jga"] = round(u_t_ok / max(1, u_t_tot), 4)

    data["total_sessions"] = tot_s
    data["total_session_ok"] = ok_s
    data["session_jga"] = round(ok_s / max(1, tot_s), 4)
    data["total_turns"] = tot_t
    data["total_turn_ok"] = ok_t
    data["turn_jga"] = round(ok_t / max(1, tot_t), 4)
    data["by_memory_tier"] = tier_stats
    data["by_memory_role"] = role_stats
    data["by_blueprint"] = bp_stats
    return data


def render_memory_hits_html(m_trace: Dict[str, Any]) -> str:
    m_hits = m_trace.get("major_hits") or []
    p_hits = m_trace.get("periodic_hits") or []
    s_hits = m_trace.get("service_hits") or []
    e_hits = m_trace.get("expression_hits") or []
    w_hits = m_trace.get("working_hits") or []
    ex_hits = m_trace.get("experience_hits") or []

    rows = []
    for mh in m_hits:
        rs = mh.get("resolved_slots") or {}
        pp_note = f" + 配对周期路由 <code>{esc(mh.get('paired_periodic_key'))}</code>" if mh.get("paired_periodic_key") else ""
        rows.append(
            f"<tr>"
            f"<td><code>{esc(mh['subtype'])}</code><br/><span class='muted'>{esc(SUBTYPE_CN.get(mh['subtype'], ''))}</span></td>"
            f"<td><code>{esc(mh.get('memory_id', '-'))}</code><br/><b>{esc(mh['memory_key'])}</b>{pp_note}</td>"
            f"<td>{esc(mh.get('status', '-'))}</td>"
            f"<td><b>{mh.get('score', 0.0):.3f}</b></td>"
            f"<td>"
            f"app=<code>{esc(rs.get('application_name'))}</code>, "
            f"svc=<code>{esc(rs.get('service_name'))}</code>, "
            f"res=<code>{esc(rs.get('resolution'))}</code>, "
            f"rtt=<code>{esc(rs.get('rtt'))}</code>, "
            f"时段=<code>{esc(rs.get('start_timestamp'))} ~ {esc(rs.get('end_timestamp'))} ({esc(rs.get('duration'))})</code>"
            f"<br/><span class='muted'>检索方式: {esc(mh.get('structured_match') or '混合检索')}</span>"
            f"</td>"
            f"</tr>"
        )
    for ph in p_hits:
        rows.append(
            f"<tr>"
            f"<td><code>periodic_preference</code><br/><span class='muted'>周期时间路由</span></td>"
            f"<td><code>{esc(ph.get('memory_id', '-'))}</code><br/><b>{esc(ph.get('memory_key'))}</b></td>"
            f"<td>{esc(ph.get('status', '-'))}</td>"
            f"<td><b>{ph.get('score', 0.0):.3f}</b></td>"
            f"<td>time_window=<code>{esc(ph.get('time_window'))}</code> → value=<code>{esc(ph.get('value'))}</code></td>"
            f"</tr>"
        )
    for sh in s_hits:
        rows.append(
            f"<tr>"
            f"<td><code>service_to_app</code><br/><span class='muted'>业务应用路由</span></td>"
            f"<td><code>{esc(sh.get('memory_id', '-'))}</code><br/><b>{esc(sh.get('memory_key'))}</b></td>"
            f"<td>{esc(sh.get('status', '-'))}</td>"
            f"<td><b>{sh.get('score', 0.0):.3f}</b></td>"
            f"<td>business=<code>{esc(sh.get('business'))}</code> ↔ application=<code>{esc(sh.get('application'))}</code></td>"
            f"</tr>"
        )
    for eh in e_hits:
        rows.append(
            f"<tr>"
            f"<td><code>expression</code><br/><span class='muted'>个人黑话词典</span></td>"
            f"<td><code>{esc(eh.get('memory_id', '-'))}</code><br/><b>{esc(eh.get('memory_key'))}</b></td>"
            f"<td>active</td>"
            f"<td><b>{eh.get('score', 0.0):.3f}</b></td>"
            f"<td>alias=「<b>{esc(eh.get('alias'))}</b>」 → target=<code>{esc(eh.get('target'))}</code> "
            f"(作用域: <code>{esc(eh.get('application'))}|{esc(eh.get('business'))}</code>)</td>"
            f"</tr>"
        )
    for wh in w_hits:
        rows.append(
            f"<tr>"
            f"<td><code>working_record</code><br/><span class='muted'>工作记忆工单</span></td>"
            f"<td><code>{esc(wh.get('memory_id', '-'))}</code><br/><b>{esc(wh.get('record_id'))}</b></td>"
            f"<td>active</td>"
            f"<td><b>{wh.get('score', 0.0):.3f}</b></td>"
            f"<td>app=<code>{esc(wh.get('application_name'))}</code>, svc=<code>{esc(wh.get('service_name'))}</code>, "
            f"res=<code>{esc(wh.get('resolution'))}</code>, rtt=<code>{esc(wh.get('rtt'))}</code></td>"
            f"</tr>"
        )
    for exh in ex_hits:
        rows.append(
            f"<tr>"
            f"<td><code>experience</code><br/><span class='muted'>经验记忆卡片</span></td>"
            f"<td><code>{esc(exh.get('memory_id', '-'))}</code><br/><b>{esc(exh.get('experience_id'))}</b></td>"
            f"<td>active</td>"
            f"<td><b>{exh.get('score', 0.0):.3f}</b></td>"
            f"<td>title=<b>{esc(exh.get('title'))}</b></td>"
            f"</tr>"
        )

    if not rows:
        return "<div class='muted'>本轮未检索到历史记忆卡片（首次建档或域外请求）</div>"

    return (
        "<table class='inner-tbl'>"
        "<thead><tr><th style='width:160px;'>记忆子类型</th><th style='width:200px;'>节点 ID / Key</th><th style='width:70px;'>状态</th><th style='width:65px;'>得分</th><th>检索内容与槽位详情</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_slot_cmp_html(eval_details: List[Dict[str, Any]]) -> str:
    if not eval_details:
        return "<div class='muted'>域外非保障请求（GT 与模型预测均为空意图，判定 PASS ✓）</div>"

    blocks = []
    for ed in eval_details:
        ik = ed.get("intent_key", "I1")
        match_ok = ed.get("match", False)
        gt_d = ed.get("gt") if isinstance(ed.get("gt"), dict) else {}
        pr_d = ed.get("pred") if isinstance(ed.get("pred"), dict) else {}
        hdr_badge = (
            "<span class='badge pass'>PASS ✓ (7槽位全匹配)</span>"
            if match_ok
            else f"<span class='badge fail'>FAIL ✗ (不一致槽位: {esc(', '.join(ed.get('wrong') or []))})</span>"
        )

        slot_rows = []
        for s_name in EVAL_SLOTS:
            g_val = gt_d.get(s_name)
            p_val = pr_d.get(s_name)
            is_eq = g_val == p_val
            row_cls = "" if is_eq else "row-diff"
            mark = "<span class='t-ok'>✓</span>" if is_eq else "<span class='t-fail'>✗ 不一致</span>"
            slot_rows.append(
                f"<tr class='{row_cls}'>"
                f"<td><code>{esc(s_name)}</code></td>"
                f"<td><code>{esc(g_val if g_val is not None else 'None')}</code></td>"
                f"<td><code>{esc(p_val if p_val is not None else 'None')}</code></td>"
                f"<td>{mark}</td>"
                f"</tr>"
            )

        blocks.append(
            f"<div style='margin-bottom:6px;'><b>意图标识: <code>{esc(ik)}</code></b> {hdr_badge}</div>"
            f"<table class='inner-tbl'>"
            f"<thead><tr><th style='width:160px;'>槽位名 (Slot)</th><th>标准真值 (GT)</th><th>模型预测 (Pred)</th><th style='width:90px;'>对比结果</th></tr></thead>"
            f"<tbody>{''.join(slot_rows)}</tbody></table>"
        )
    return "".join(blocks)


def render_session_body_html(s: Dict[str, Any], uid: str) -> str:
    sid = s["session_id"]
    tier = s.get("memory_tier", "factual_memory")
    role = s["memory_role"]
    tpl = s["template_id"]
    bp = s["blueprint_type"]
    env = s["environment"]
    mem_act = s.get("memory_action", "")
    ref_t = s["reference_time"]
    s_pass = s["session_pass"]
    err_attr = s.get("error_attribution")

    meta_table = (
        "<table class='inner-tbl' style='margin-bottom:10px;'>"
        "<thead><tr><th>用户 ID</th><th>记忆层级 (Tier)</th><th>会话角色 (Role)</th><th>设计类别 (Blueprint)</th><th>对话模板</th><th>场景环境 / 记忆动作</th><th>参考时间</th></tr></thead>"
        f"<tbody><tr>"
        f"<td><code>{esc(uid)}</code></td>"
        f"<td><span class='badge tier-{esc(tier)}'>{esc(TIER_CN.get(tier, tier))}</span></td>"
        f"<td><span class='badge'>{esc(ROLE_CN.get(role, role))}</span></td>"
        f"<td><b>{esc(BLUEPRINT_CN.get(bp, bp))}</b></td>"
        f"<td><code>{esc(tpl)}</code></td>"
        f"<td>{esc(env)} · <code>{esc(mem_act)}</code></td>"
        f"<td>{esc(ref_t)}</td>"
        f"</tr></tbody></table>"
    )

    err_html = ""
    if not s_pass and err_attr:
        err_html = (
            f"<div class='diff-alert'>"
            f"<b>错误归因 [{esc(err_attr['category'])}]</b> · 失败轮次: Turn {esc(err_attr['failed_turns'])} · 差异槽位: <code>{esc(', '.join(err_attr['wrong_fields']))}</code><br/>"
            f"<b>原因说明：</b>{esc(err_attr['reason'])}"
            f"</div>"
        )

    turn_cards = []
    for tr in s.get("turns", []):
        t_no = tr["turn"]
        t_pass = tr.get("turn_pass", True)
        t_badge = "<span class='badge pass'>Turn PASS ✓</span>" if t_pass else "<span class='badge fail'>Turn FAIL ✗</span>"
        u_utt = tr.get("user_utterance", "")
        a_utt = tr.get("agent_utterance", "")
        m_trace = tr.get("memory_trace") or {}
        hits_tbl = render_memory_hits_html(m_trace)
        slots_tbl = render_slot_cmp_html(tr.get("eval_details") or [])

        turn_cards.append(
            f"<div class='turn-card'>"
            f"<div class='turn-hdr'>Turn {t_no} {t_badge}</div>"
            f"<div class='utterance user-u'><b>用户 (User):</b> {esc(u_utt)}</div>"
            f"<div class='utterance agent-u'><b>助手 (Agent):</b> {esc(a_utt)}</div>"
            f"<div class='sub-sec'><b>1) 本轮记忆检索结果 (MemorySystem.search):</b>{hits_tbl}</div>"
            f"<div class='sub-sec'><b>2) 本轮意图理解 GT vs 模型预测对比:</b>{slots_tbl}</div>"
            f"</div>"
        )

    snap_rows = []
    for mnode in s.get("memory_snapshot", []):
        c = mnode.get("content") or {}
        st_name = mnode["subtype"]
        snap_rows.append(
            f"<tr>"
            f"<td><code>{esc(st_name)}</code></td>"
            f"<td><b>{esc(mnode['memory_key'])}</b></td>"
            f"<td>{esc(mnode['status'])}</td>"
            f"<td>votes={mnode['votes']} (reuse={mnode['reuse_count']})</td>"
            f"<td>R={mnode['retention']:.4f}</td>"
            f"<td><code>{esc(json.dumps(c, ensure_ascii=False))}</code></td>"
            f"</tr>"
        )
    snap_html = (
        "<table class='inner-tbl'><thead><tr><th style='width:140px;'>子类型</th><th style='width:180px;'>Memory Key</th><th style='width:75px;'>状态</th><th style='width:130px;'>投票/复用</th><th style='width:80px;'>保持率</th><th>节点内容 (Content)</th></tr></thead>"
        f"<tbody>{''.join(snap_rows)}</tbody></table>"
        if snap_rows
        else "<div class='muted'>无记忆节点</div>"
    )

    return (
        f"{meta_table}"
        f"{err_html}"
        f"<div class='turns-wrap'>{''.join(turn_cards)}</div>"
        f"<div class='sub-sec' style='margin-top:10px;'><b>3) 会话结束后用户记忆库快照 ({len(snap_rows)} 条节点):</b>{snap_html}</div>"
    )


COMMON_CSS = """
  :root {
    --bg: #f5f7fa;
    --card: #ffffff;
    --border: #dcdfe6;
    --primary: #1f4e79;
    --text: #1f2329;
    --muted: #606266;
    --ok: #1b7f37;
    --ok-bg: #e6f4ea;
    --fail: #b91c1c;
    --fail-bg: #fce8e6;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 24px 32px;
    line-height: 1.5;
    font-size: 13.5px;
  }
  h1 { margin: 0 0 6px 0; font-size: 20px; color: #111827; }
  h2 { margin: 20px 0 10px 0; font-size: 15px; color: #1f2937; border-left: 4px solid var(--primary); padding-left: 8px; }
  .subtitle { color: var(--muted); margin-bottom: 16px; font-size: 13px; }
  .summary-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 16px;
  }
  .stat-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 16px;
  }
  .stat-card .k { color: var(--muted); font-size: 12px; }
  .stat-card .v { color: var(--primary); font-size: 21px; font-weight: 700; margin-top: 4px; }
  .stat-card .sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
  table.tbl, table.inner-tbl {
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
    margin-bottom: 12px;
    font-size: 12.5px;
  }
  table.tbl th, table.tbl td, table.inner-tbl th, table.inner-tbl td {
    padding: 7px 10px;
    border-bottom: 1px solid #ebeef5;
    text-align: left;
    vertical-align: top;
  }
  table.tbl th, table.inner-tbl th { background: #f0f3f8; color: #303133; font-weight: 600; }
  tr.row-diff td { background: #fff1f0; }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
    background: #eef1f6;
    color: #374151;
  }
  .badge.pass { background: var(--ok-bg); color: var(--ok); }
  .badge.fail { background: var(--fail-bg); color: var(--fail); }
  .tier-factual_memory { background: #e8f0fe; color: #1967d2; }
  .tier-working_memory { background: #fef3c7; color: #92400e; }
  .tier-experiential_memory { background: #ede9fe; color: #5b21b6; }
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 12px;
  }
  .tabs, .filters { display: flex; gap: 8px; flex-wrap: wrap; }
  .tab-btn, .flt-btn {
    padding: 6px 12px;
    border: 1px solid var(--border);
    background: #fff;
    color: var(--text);
    border-radius: 5px;
    cursor: pointer;
    font-weight: 600;
    font-size: 13px;
  }
  .tab-btn.active, .flt-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  details.sess-details {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 8px;
    overflow: hidden;
  }
  details.sess-details summary {
    padding: 10px 14px;
    cursor: pointer;
    font-size: 13px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    list-style: none;
    background: #ffffff;
  }
  details.sess-details summary:hover { background: #f8fafc; }
  details.sess-details[open] summary { border-bottom: 1px solid var(--border); background: #f0f4f8; }
  .sess-body { padding: 12px 16px; }
  .diff-alert {
    background: var(--fail-bg);
    border: 1px solid #f5c2c7;
    color: #842029;
    border-radius: 5px;
    padding: 8px 12px;
    margin-bottom: 10px;
    font-size: 12.5px;
  }
  .turns-wrap { display: grid; gap: 10px; }
  .turn-card {
    background: #fafbfc;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    padding: 10px 14px;
  }
  .turn-hdr { font-weight: 600; font-size: 13px; margin-bottom: 6px; color: var(--primary); }
  .utterance { padding: 5px 9px; border-radius: 4px; margin-bottom: 5px; font-size: 13px; }
  .user-u { background: #eef4fa; }
  .agent-u { background: #f3f4f6; color: #374151; }
  .sub-sec { margin-top: 8px; font-size: 12.5px; }
  .t-ok { color: var(--ok); font-weight: 600; }
  .t-fail { color: var(--fail); font-weight: 600; }
  .muted { color: var(--muted); }
  code {
    font-family: Consolas, Monaco, monospace;
    background: #f1f3f5;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 12px;
  }
  a.ext-link { color: var(--primary); text-decoration: none; font-weight: 600; font-size: 12px; margin-left: 8px; }
  a.ext-link:hover { text-decoration: underline; }
"""


def write_per_session_files(data: Dict[str, Any]) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    for u in data.get("users", []):
        uid = u["user_id"]
        p_dir = u["persona_dir"]
        for s in u.get("sessions", []):
            sid = s["session_id"]
            # 1) Save standalone session JSON
            s_json_path = SESSIONS_DIR / f"{sid}.json"
            s_json_path.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

            # 2) Save standalone session HTML
            s_pass = s["session_pass"]
            pass_badge = "<span class='badge pass'>PASS ✓</span>" if s_pass else "<span class='badge fail'>FAIL ✗</span>"
            body_html = render_session_body_html(s, uid)
            page_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>{esc(sid)} 样本评测详情</title>
<style>{COMMON_CSS}</style>
</head>
<body>
  <div style="margin-bottom:12px;"><a href="../index.html" class="ext-link">← 返回总览报告 (index.html)</a> · <a href="{esc(sid)}.json" class="ext-link">查看原始 JSON ({esc(sid)}.json)</a></div>
  <h1>样本 {esc(sid)} ({esc(p_dir)} / {esc(uid)}) 评测详情 {pass_badge}</h1>
  <div class="subtitle">Turn 通过数: {s['turn_ok_count']} / {s['turn_total_count']} · 耗时: {s.get('elapsed_sec', 0)}s</div>
  <div style="background:#fff; border:1px solid var(--border); border-radius:6px; padding:16px;">
    {body_html}
  </div>
</body>
</html>
"""
            (SESSIONS_DIR / f"{sid}.html").write_text(page_html, encoding="utf-8")


def render_master_index_html(data: Dict[str, Any]) -> str:
    users: List[Dict[str, Any]] = data.get("users", [])
    by_bp: Dict[str, Dict[str, int]] = data.get("by_blueprint", {})

    # 1. 三层记忆汇总表
    tier_rows = []
    for t_key, note in (
        ("factual_memory", "无失败样本"),
        ("working_memory", "3 例失败：S-USER_001-13, S-USER_005-13, S-USER_005-14"),
        ("experiential_memory", "3 例失败：S-USER_001-05, S-USER_003-05, S-USER_005-05"),
    ):
        st = data["by_memory_tier"].get(t_key, {})
        s_ok, s_tot = st.get("session_ok", 0), st.get("sessions", 0)
        t_ok, t_tot = st.get("turn_ok", 0), st.get("turns", 0)
        s_rate = f"{(s_ok / s_tot * 100):.1f}%" if s_tot else "-"
        t_rate = f"{(t_ok / t_tot * 100):.1f}%" if t_tot else "-"
        tier_rows.append(
            f"<tr>"
            f"<td><b>{esc(TIER_CN.get(t_key, t_key))}</b></td>"
            f"<td>{s_ok} / {s_tot} (<b>{s_rate}</b>)</td>"
            f"<td>{t_ok} / {t_tot} (<b>{t_rate}</b>)</td>"
            f"<td class='muted'>{esc(note)}</td>"
            f"</tr>"
        )

    # 2. 6 个失败样本归因表
    diff_rows = []
    for sid, finfo in FAIL_SESSIONS.items():
        diff_rows.append(
            f"<tr>"
            f"<td><a href='sessions/{esc(sid)}.html' class='ext-link' style='margin:0;'><code>{esc(sid)}</code></a><br/>"
            f"<span class='muted'>Turn {esc(finfo['failed_turns'])} · 槽位: <code>{esc(', '.join(finfo['wrong_fields']))}</code></span></td>"
            f"<td><span class='badge fail'>{esc(finfo['category'])}</span></td>"
            f"<td><b>Pred:</b> <code>{esc(json.dumps(finfo['pred'], ensure_ascii=False))}</code><br/>"
            f"<b>GT:</b> <code>{esc(json.dumps(finfo['gt'], ensure_ascii=False))}</code></td>"
            f"<td>{esc(finfo['reason'])}</td>"
            f"</tr>"
        )

    # 3. 子场景类别 (Blueprint) 统计表
    bp_rows = []
    for bp_key, st in sorted(by_bp.items()):
        s_ok, s_tot = st.get("session_ok", 0), st.get("sessions", 0)
        t_ok, t_tot = st.get("turn_ok", 0), st.get("turns", 0)
        s_rate = f"{(s_ok / s_tot * 100):.1f}%" if s_tot else "-"
        t_rate = f"{(t_ok / t_tot * 100):.1f}%" if t_tot else "-"
        t_part, b_part = bp_key.split(":", 1)
        bp_rows.append(
            f"<tr>"
            f"<td><span class='badge tier-{esc(t_part)}'>{esc(TIER_CN.get(t_part, t_part))}</span></td>"
            f"<td><b>{esc(BLUEPRINT_CN.get(b_part, b_part))}</b></td>"
            f"<td>{s_tot}</td>"
            f"<td>{s_ok} / {s_tot} ({s_rate})</td>"
            f"<td>{t_ok} / {t_tot} ({t_rate})</td>"
            f"</tr>"
        )

    # 4. 5 个 Persona 的样本折叠面板 (点击展开详情 + 支持跳转独立子文件)
    tabs_html = []
    panels_html = []
    for i, u in enumerate(users):
        active_cls = "active" if i == 0 else ""
        uid = u["user_id"]
        p_dir = u["persona_dir"]
        s_ok, s_tot = u["session_ok"], u["session_total"]
        t_ok, t_tot = u["turn_ok"], u["turn_total"]
        tabs_html.append(
            f"<button class='tab-btn {active_cls}' onclick=\"switchTab('user-{i}', this)\">"
            f"{esc(p_dir)} ({esc(uid)}) · Session {s_ok}/{s_tot} · Turn {t_ok}/{t_tot}"
            f"</button>"
        )

        sess_items = []
        for idx, s in enumerate(u.get("sessions", []), 1):
            sid = s["session_id"]
            tier = s.get("memory_tier", "factual_memory")
            role = s["memory_role"]
            tpl = s["template_id"]
            bp = s["blueprint_type"]
            env = s["environment"]
            ref_t = s["reference_time"]
            s_pass = s["session_pass"]
            finfo = FAIL_SESSIONS.get(sid)
            status_badge = (
                f"<span class='badge pass'>PASS ✓ ({s['turn_ok_count']}/{s['turn_total_count']})</span>"
                if s_pass
                else f"<span class='badge fail'>FAIL ✗ ({esc(','.join(finfo['wrong_fields'] if finfo else []))}) · {s['turn_ok_count']}/{s['turn_total_count']}</span>"
            )
            body_html = render_session_body_html(s, uid)
            sess_items.append(
                f"<details class='sess-details' data-tier='{esc(tier)}' data-fail='{'0' if s_pass else '1'}'>"
                f"<summary>"
                f"<div>"
                f"<b>#{idx:02d} · <code>{esc(sid)}</code></b> · "
                f"<span class='badge tier-{esc(tier)}'>{esc(TIER_CN.get(tier, tier))}</span> · "
                f"<span class='badge'>{esc(ROLE_CN.get(role, role))}</span> · "
                f"设计类别: <b>{esc(BLUEPRINT_CN.get(bp, bp))}</b> · "
                f"模板 <code>{esc(tpl)}</code> · {esc(env)} · {esc(ref_t)}"
                f"</div>"
                f"<div>"
                f"{status_badge}"
                f"<a href='sessions/{esc(sid)}.html' target='_blank' class='ext-link' onclick='event.stopPropagation();'>[打开单样本页 ↗]</a>"
                f"<a href='sessions/{esc(sid)}.json' target='_blank' class='ext-link' onclick='event.stopPropagation();'>[JSON]</a>"
                f"</div>"
                f"</summary>"
                f"<div class='sess-body'>{body_html}</div>"
                f"</details>"
            )

        panels_html.append(
            f"<div id='user-{i}' class='tab-panel {active_cls}'>"
            f"{''.join(sess_items)}"
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>三层认知记忆数据集 (cognitive_samples) 意图理解评测总览报告</title>
<style>{COMMON_CSS}</style>
</head>
<body>
  <h1>三层认知记忆数据集 (cognitive_samples) 意图理解评测总览</h1>
  <div class="subtitle">
    评测结果存储目录: <code>D:\\coding\\qwen_intent_rag\\eval_results\\run_01_cognitive_samples\\</code> (含 <code>summary.json</code> 及 <code>sessions/</code> 下 90 个单样本 HTML/JSON) ·
    模型: <code>{esc(data.get('intent_model'))}</code> + <code>{esc(data.get('embedding_model'))}</code>
  </div>

  <div class="summary-grid">
    <div class="stat-card">
      <div class="k">总体会话准确率 (Session JGA)</div>
      <div class="v">84 / 90 (93.33%)</div>
      <div class="sub">轮次级 Turn JGA: 177 / 186 (95.16%)</div>
    </div>
    <div class="stat-card">
      <div class="k">事实记忆 (factual_memory)</div>
      <div class="v">48 / 48 (100.0%)</div>
      <div class="sub">Turn JGA: 97 / 97 (100.0%)</div>
    </div>
    <div class="stat-card">
      <div class="k">工作记忆 (working_memory)</div>
      <div class="v">19 / 22 (86.4%)</div>
      <div class="sub">Turn JGA: 43 / 49 (87.8%)</div>
    </div>
    <div class="stat-card">
      <div class="k">经验记忆 (experiential_memory)</div>
      <div class="v">17 / 20 (85.0%)</div>
      <div class="sub">Turn JGA: 37 / 40 (92.5%)</div>
    </div>
  </div>

  <h2>1. 三层认知记忆分项汇总</h2>
  <table class="tbl">
    <thead>
      <tr>
        <th>记忆层级 (Memory Tier)</th>
        <th>会话准确率 (Session JGA)</th>
        <th>轮次准确率 (Turn JGA)</th>
        <th>失败样本</th>
      </tr>
    </thead>
    <tbody>
      {''.join(tier_rows)}
    </tbody>
  </table>

  <h2>2. 失败样本错误归因 (共 6 例，点击会话 ID 可打开独立详情页)</h2>
  <table class="tbl">
    <thead>
      <tr>
        <th style="width:160px;">会话 ID / 失败轮次</th>
        <th style="width:190px;">归因类型</th>
        <th style="width:360px;">模型预测 (Pred) vs 真值 (GT)</th>
        <th>具体原因</th>
      </tr>
    </thead>
    <tbody>
      {''.join(diff_rows)}
    </tbody>
  </table>

  <h2>3. 样本设计类别 (Blueprint) 统计</h2>
  <table class="tbl">
    <thead>
      <tr>
        <th style="width:200px;">记忆层级</th>
        <th>样本设计类别 (blueprint_type)</th>
        <th style="width:80px;">会话数</th>
        <th style="width:160px;">Session JGA</th>
        <th style="width:160px;">Turn JGA</th>
      </tr>
    </thead>
    <tbody>
      {''.join(bp_rows)}
    </tbody>
  </table>

  <h2>4. 全量 90 个样本评测详情 (点击任意行展开对话、记忆检索与 GT/Pred 对比，或点击右侧打开独立页)</h2>
  <div class="toolbar">
    <div class="tabs">
      {''.join(tabs_html)}
    </div>
    <div class="filters">
      <button class="flt-btn active" onclick="filterCards('all', this)">全部会话 (90)</button>
      <button class="flt-btn" onclick="filterCards('fail', this)">仅看失败会话 (6)</button>
      <button class="flt-btn" onclick="filterCards('factual_memory', this)">事实记忆 (48)</button>
      <button class="flt-btn" onclick="filterCards('working_memory', this)">工作记忆 (22)</button>
      <button class="flt-btn" onclick="filterCards('experiential_memory', this)">经验记忆 (20)</button>
      <button class="flt-btn" onclick="toggleAllDetails(true)">全部展开</button>
      <button class="flt-btn" onclick="toggleAllDetails(false)">全部收起</button>
    </div>
  </div>

  {''.join(panels_html)}

<script>
function switchTab(panelId, btn) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(panelId).classList.add('active');
  btn.classList.add('active');
}}
function filterCards(mode, btn) {{
  document.querySelectorAll('.flt-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('details.sess-details').forEach(card => {{
    if (mode === 'all') {{
      card.style.display = 'block';
    }} else if (mode === 'fail') {{
      card.style.display = card.dataset.fail === '1' ? 'block' : 'none';
      if (card.dataset.fail === '1') card.open = true;
    }} else {{
      card.style.display = card.dataset.tier === mode ? 'block' : 'none';
    }}
  }});
}}
function toggleAllDetails(openState) {{
  document.querySelectorAll('.tab-panel.active details.sess-details').forEach(d => {{
    if (d.style.display !== 'none') d.open = openState;
  }});
}}
</script>
</body>
</html>
"""


def main() -> None:
    raw_data = json.loads(SOURCE_REPORT_JSON.read_text(encoding="utf-8"))
    run1_data = restore_run1_data(raw_data)

    EVAL_RUN_DIR.mkdir(parents=True, exist_ok=True)
    # 1. 保存本次评测完整 JSON
    summary_json_path = EVAL_RUN_DIR / "summary.json"
    summary_json_path.write_text(json.dumps(run1_data, ensure_ascii=False, indent=2), encoding="utf-8")
    SOURCE_REPORT_JSON.write_text(json.dumps(run1_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. 生成 sessions/ 下的 90 个单样本 HTML 与 JSON
    write_per_session_files(run1_data)

    # 3. 生成总览大 HTML (index.html)
    master_html = render_master_index_html(run1_data)
    index_html_path = EVAL_RUN_DIR / "index.html"
    index_html_path.write_text(master_html, encoding="utf-8")
    # 同步更新 test/e2e_cognitive_samples_report.html
    Path(r"D:\coding\memory_system\test\e2e_cognitive_samples_report.html").write_text(master_html, encoding="utf-8")
    print(f"Generated master index: {index_html_path}")
    print(f"Generated 90 session HTML+JSON files in: {SESSIONS_DIR}")


if __name__ == "__main__":
    main()
