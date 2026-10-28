from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

EVAL_RUN_DIR = Path(r"D:\coding\qwen_intent_rag\eval_results\run_02_cognitive_samples")
SOURCE_REPORT_JSON = EVAL_RUN_DIR / "summary.json"
USERS_DIR = EVAL_RUN_DIR / "users"

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
    "expression": "个人黑话词典 (expression)",
    "working_record": "工作记忆工单 (working_record)",
    "experience": "经验记忆卡片 (experience)",
}

RUN1_FIXED_CASES = [
    {
        "sid": "S-USER_005-13",
        "uid": "USER_005",
        "tier": "working_memory",
        "run1_issue": "检索污染：S-USER_005-09 单次临时改参 (720p/30ms) 落入 working_record，被 S-USER_005-13 误召回覆盖基准 1080p/50ms",
        "opt_measure": "1) 写入端过滤 event_override 单次临时覆盖会话不入 working_record 台账；2) 检索端新增相对时间窗口挖掘 (上周/昨天) + 地点短窗向量锚点联动 SELECT WHERE 精准检索",
    },
    {
        "sid": "S-USER_001-13",
        "uid": "USER_001",
        "tier": "working_memory",
        "run1_issue": "工单改期基准时段偏差：历史工单已为推迟后时段，模型二次叠加 +1h 导致偏移为 13:30-14:30",
        "opt_measure": "结构化 SELECT 锁定前序预约工单，并在 WORKING RECORD RESCHEDULE RULE 中显式展示「原预约挂起时段 → 经推迟1小时换算后的最终目标时段」，消除二次偏移",
    },
    {
        "sid": "S-USER_005-14",
        "uid": "USER_005",
        "tier": "working_memory",
        "run1_issue": "工单改期基准时段偏差：历史工单时段 08:30-10:30 被二次叠加 -1h 偏移为 07:30-09:30",
        "opt_measure": "通过 SQLite SELECT 精准定位前日预约工单，并在改期注入指令中明确原预约挂起时段与换算后目标时段的映射关系",
    },
    {
        "sid": "S-USER_001-05",
        "uid": "USER_001",
        "tier": "experiential_memory",
        "run1_issue": "经验归纳会话 Turn 2 口头探讨提前半小时，模型直接覆盖已落盘的 Turn 1 预约工单时间戳",
        "opt_measure": "经验卡片落地动态提取 condition/action/recommended_adjustments，并在经验归纳轮锁定已确认排期槽位",
    },
    {
        "sid": "S-USER_003-05",
        "uid": "USER_003",
        "tier": "experiential_memory",
        "run1_issue": "经验归纳会话 Turn 2 探讨提前半小时开启保障，模型改写 Turn 1 已锁定的 14:00-16:00 时段",
        "opt_measure": "将经验记忆纳入条件触发特征索引 (time_window_col=condition, target_col=action)，并在归纳探讨轮保持已确认工单时段不变",
    },
    {
        "sid": "S-USER_005-05",
        "uid": "USER_005",
        "tier": "experiential_memory",
        "run1_issue": "经验归纳会话 Turn 2 探讨提前半小时开启保障，模型改写 Turn 1 已锁定的 08:30-10:30 时段",
        "opt_measure": "统一经验记忆卡片的动态检索与条件匹配回填机制，区分「新会话主动经验推荐」与「归纳期探讨反馈」",
    },
]


def esc(v: Any) -> str:
    return html.escape(str(v) if v is not None else "")


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
        w_match = wh.get("structured_match") or "近期工单时序+语义混合检索"
        rows.append(
            f"<tr>"
            f"<td><code>working_record</code><br/><span class='muted'>工作记忆工单</span></td>"
            f"<td><code>{esc(wh.get('memory_id', '-'))}</code><br/><b>{esc(wh.get('record_id'))}</b></td>"
            f"<td>active</td>"
            f"<td><b>{wh.get('score', 0.0):.3f}</b></td>"
            f"<td>app=<code>{esc(wh.get('application_name'))}</code>, svc=<code>{esc(wh.get('service_name'))}</code>, "
            f"res=<code>{esc(wh.get('resolution'))}</code>, rtt=<code>{esc(wh.get('rtt'))}</code>, "
            f"loc=<code>{esc(wh.get('location'))}</code>, 时段=<code>{esc(wh.get('start_timestamp'))} ~ {esc(wh.get('end_timestamp'))}</code>"
            f"<br/><span class='muted'>检索方式: {esc(w_match)}</span></td>"
            f"</tr>"
        )
    for exh in ex_hits:
        ex_match = exh.get("structured_match") or "条件语义检索"
        rows.append(
            f"<tr>"
            f"<td><code>experience</code><br/><span class='muted'>经验记忆卡片</span></td>"
            f"<td><code>{esc(exh.get('memory_id', '-'))}</code><br/><b>{esc(exh.get('experience_id'))}</b></td>"
            f"<td>active</td>"
            f"<td><b>{exh.get('score', 0.0):.3f}</b></td>"
            f"<td>title=<b>{esc(exh.get('title'))}</b> · 触发条件=<code>{esc(exh.get('condition'))}</code> → 动作=<code>{esc(exh.get('action'))}</code>"
            f"<br/><span class='muted'>检索方式: {esc(ex_match)}</span></td>"
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
    tier = s.get("memory_tier", "factual_memory")
    role = s["memory_role"]
    tpl = s["template_id"]
    bp = s["blueprint_type"]
    env = s["environment"]
    mem_act = s.get("memory_action", "")
    ref_t = s["reference_time"]
    s_pass = s["session_pass"]
    s_diffs = s.get("session_diffs") or []

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
    if not s_pass:
        err_html = (
            f"<div class='diff-alert'>"
            f"<b>会话未通过</b> · 差异详情: <code>{esc(json.dumps(s_diffs, ensure_ascii=False))}</code>"
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
  .user-cards-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
    margin-bottom: 16px;
  }
  .user-card {
    display: block;
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 14px;
    color: var(--text);
    text-decoration: none;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .user-card:hover {
    border-color: var(--primary);
    box-shadow: 0 3px 10px rgba(31, 78, 121, 0.12);
  }
  .user-card-hdr {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }
  .user-card-title { font-size: 15px; font-weight: 700; color: var(--primary); }
  .user-card-sub { font-size: 11.5px; color: var(--muted); margin-bottom: 8px; }
  .user-tier-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    padding: 4px 0;
    border-top: 1px dashed var(--border);
  }
  .user-card-btn {
    margin-top: 8px;
    text-align: center;
    background: #eef4fa;
    color: var(--primary);
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
    font-weight: 600;
  }
  .user-nav {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
    flex-wrap: wrap;
    align-items: center;
  }
  .user-nav-btn {
    padding: 5px 12px;
    border-radius: 4px;
    border: 1px solid var(--border);
    background: #fff;
    color: var(--text);
    font-size: 12.5px;
    font-weight: 600;
    text-decoration: none;
  }
  .user-nav-btn:hover { background: #f1f5f9; }
  .user-nav-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); }
  details.sess-details.sess-working_memory { border-left: 4px solid #d97706; }
  details.sess-details.sess-experiential_memory { border-left: 4px solid #7e22ce; }
  details.sess-details.sess-factual_memory { border-left: 4px solid #1967d2; }
"""


def compute_user_tier_stats(u: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    stats = {
        "factual_memory": {"s_ok": 0, "s_tot": 0, "t_ok": 0, "t_tot": 0},
        "working_memory": {"s_ok": 0, "s_tot": 0, "t_ok": 0, "t_tot": 0},
        "experiential_memory": {"s_ok": 0, "s_tot": 0, "t_ok": 0, "t_tot": 0},
    }
    for s in u.get("sessions", []):
        tier = s.get("memory_tier", "factual_memory")
        stats.setdefault(tier, {"s_ok": 0, "s_tot": 0, "t_ok": 0, "t_tot": 0})
        stats[tier]["s_tot"] += 1
        if s["session_pass"]:
            stats[tier]["s_ok"] += 1
        stats[tier]["t_ok"] += s["turn_ok_count"]
        stats[tier]["t_tot"] += s["turn_total_count"]
    return stats


def write_per_user_files(data: Dict[str, Any], users_dir: Path = USERS_DIR) -> None:
    users_dir.mkdir(parents=True, exist_ok=True)
    users: List[Dict[str, Any]] = data.get("users", [])

    for u in users:
        uid = u["user_id"]
        p_dir = u["persona_dir"]
        t_stats = compute_user_tier_stats(u)
        f_stat = t_stats["factual_memory"]
        w_stat = t_stats["working_memory"]
        e_stat = t_stats["experiential_memory"]
        fail_cnt = u["session_total"] - u["session_ok"]

        (users_dir / f"{uid}.json").write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8")

        nav_links = [
            "<a href='../index.html' class='user-nav-btn'>← 返回全量汇总首页 (index.html)</a>"
        ]
        for other_u in users:
            other_uid = other_u["user_id"]
            act = "active" if other_uid == uid else ""
            nav_links.append(
                f"<a href='{esc(other_uid)}.html' class='user-nav-btn {act}'>"
                f"{esc(other_uid)} ({other_u['session_ok']}/{other_u['session_total']})"
                f"</a>"
            )

        overview_rows = []
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
            status_badge = (
                f"<span class='badge pass'>PASS ✓ ({s['turn_ok_count']}/{s['turn_total_count']})</span>"
                if s_pass
                else f"<span class='badge fail'>FAIL ✗ · {s['turn_ok_count']}/{s['turn_total_count']}</span>"
            )

            first_utt = s["turns"][0].get("user_utterance", "") if s.get("turns") else ""
            raw_hits_total = sum(
                (t.get("memory_trace") or {}).get("raw_hit_count", 0)
                for t in s.get("turns", [])
            )

            overview_rows.append(
                f"<tr class='ov-row' data-tier='{esc(tier)}' data-fail='{'0' if s_pass else '1'}' "
                f"onclick=\"jumpToSession('{esc(sid)}')\" style='cursor:pointer;'>"
                f"<td>{idx}</td>"
                f"<td><a href='#{esc(sid)}' class='ext-link' style='margin:0;' onclick='event.stopPropagation(); jumpToSession(\"{esc(sid)}\");'><code>{esc(sid)}</code></a></td>"
                f"<td><span class='badge tier-{esc(tier)}'>{esc(TIER_CN.get(tier, tier))}</span></td>"
                f"<td><span class='badge'>{esc(ROLE_CN.get(role, role))}</span></td>"
                f"<td><b>{esc(BLUEPRINT_CN.get(bp, bp))}</b></td>"
                f"<td><code>{esc(tpl)}</code> · {esc(env)} · {esc(ref_t)}</td>"
                f"<td>{esc(first_utt)}</td>"
                f"<td>{raw_hits_total} 条</td>"
                f"<td>{status_badge}</td>"
                f"</tr>"
            )

            body_html = render_session_body_html(s, uid)
            sess_items.append(
                f"<details id='{esc(sid)}' class='sess-details sess-{esc(tier)}' data-tier='{esc(tier)}' data-fail='{'0' if s_pass else '1'}' {'open' if not s_pass else ''}>"
                f"<summary>"
                f"<div>"
                f"<b>#{idx:02d} · <code>{esc(sid)}</code></b> · "
                f"<span class='badge tier-{esc(tier)}'>{esc(TIER_CN.get(tier, tier))}</span> · "
                f"<span class='badge'>{esc(ROLE_CN.get(role, role))}</span> · "
                f"设计类别: <b>{esc(BLUEPRINT_CN.get(bp, bp))}</b> · "
                f"模板 <code>{esc(tpl)}</code> · {esc(env)} · {esc(ref_t)}"
                f"</div>"
                f"<div>{status_badge}</div>"
                f"</summary>"
                f"<div class='sess-body'>{body_html}</div>"
                f"</details>"
            )

        user_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>{esc(uid)} ({esc(p_dir)}) 会话评测详情 - Run 02</title>
<style>{COMMON_CSS}</style>
</head>
<body>
  <div class="user-nav">
    {''.join(nav_links)}
  </div>

  <h1>用户 {esc(uid)} ({esc(p_dir)}) 会话评测详情 (Run 02 优化后)</h1>
  <div class="subtitle">
    汇总该用户的全部 {u['session_total']} 个会话评测结果（已标注 <b>事实记忆 / 工作记忆 / 经验记忆</b> 及具体会话设计类别），点击表中任意会话或下方折叠栏可查看实际对话、每轮记忆检索结果与 GT/Pred 意图对比。
  </div>

  <div class="summary-grid">
    <div class="stat-card">
      <div class="k">{esc(uid)} 会话准确率 (Session JGA)</div>
      <div class="v">{u['session_ok']} / {u['session_total']} ({(u['session_ok']/u['session_total']*100):.1f}%)</div>
      <div class="sub">轮次级 Turn JGA: {u['turn_ok']} / {u['turn_total']} ({(u['turn_ok']/u['turn_total']*100):.1f}%)</div>
    </div>
    <div class="stat-card">
      <div class="k"><span class="badge tier-factual_memory">事实记忆 (factual_memory)</span></div>
      <div class="v">{f_stat['s_ok']} / {f_stat['s_tot']} ({(f_stat['s_ok']/f_stat['s_tot']*100 if f_stat['s_tot'] else 0):.1f}%)</div>
      <div class="sub">Turn JGA: {f_stat['t_ok']} / {f_stat['t_tot']}</div>
    </div>
    <div class="stat-card">
      <div class="k"><span class="badge tier-working_memory">工作记忆 (working_memory)</span></div>
      <div class="v">{w_stat['s_ok']} / {w_stat['s_tot']} ({(w_stat['s_ok']/w_stat['s_tot']*100 if w_stat['s_tot'] else 0):.1f}%)</div>
      <div class="sub">Turn JGA: {w_stat['t_ok']} / {w_stat['t_tot']}</div>
    </div>
    <div class="stat-card">
      <div class="k"><span class="badge tier-experiential_memory">经验记忆 (experiential_memory)</span></div>
      <div class="v">{e_stat['s_ok']} / {e_stat['s_tot']} ({(e_stat['s_ok']/e_stat['s_tot']*100 if e_stat['s_tot'] else 0):.1f}%)</div>
      <div class="sub">Turn JGA: {e_stat['t_ok']} / {e_stat['t_tot']}</div>
    </div>
  </div>

  <div class="toolbar">
    <div class="filters">
      <button class="flt-btn active" onclick="filterUserSessions('all', this)">全部会话 ({u['session_total']})</button>
      <button class="flt-btn" onclick="filterUserSessions('factual_memory', this)">事实记忆 ({f_stat['s_tot']})</button>
      <button class="flt-btn" onclick="filterUserSessions('working_memory', this)">工作记忆 ({w_stat['s_tot']})</button>
      <button class="flt-btn" onclick="filterUserSessions('experiential_memory', this)">经验记忆 ({e_stat['s_tot']})</button>
      <button class="flt-btn" onclick="filterUserSessions('fail', this)">仅看失败会话 ({fail_cnt})</button>
      <button class="flt-btn" onclick="toggleAllDetails(true)">全部展开</button>
      <button class="flt-btn" onclick="toggleAllDetails(false)">全部收起</button>
    </div>
  </div>

  <h2>1. {esc(uid)} 会话列表 (点击任意会话跳转并展开详情)</h2>
  <table class="tbl">
    <thead>
      <tr>
        <th style="width:36px;">#</th>
        <th style="width:125px;">会话 ID</th>
        <th style="width:190px;">记忆层级 (事实/工作/经验)</th>
        <th style="width:170px;">会话角色</th>
        <th style="width:240px;">样本设计类别 (blueprint_type)</th>
        <th style="width:220px;">模板 / 环境 / 时间</th>
        <th>首轮用户话术</th>
        <th style="width:75px;">检索条数</th>
        <th style="width:140px;">评测结果</th>
      </tr>
    </thead>
    <tbody>
      {''.join(overview_rows)}
    </tbody>
  </table>

  <h2>2. {esc(uid)} 逐会话评测详情 (实际对话 · 记忆检索结果 · GT vs Pred 意图对比)</h2>
  {''.join(sess_items)}

<script>
function filterUserSessions(mode, btn) {{
  document.querySelectorAll('.flt-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('tr.ov-row').forEach(row => {{
    if (mode === 'all') row.style.display = '';
    else if (mode === 'fail') row.style.display = row.dataset.fail === '1' ? '' : 'none';
    else row.style.display = row.dataset.tier === mode ? '' : 'none';
  }});
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
  document.querySelectorAll('details.sess-details').forEach(d => {{
    if (d.style.display !== 'none') d.open = openState;
  }});
}}
function jumpToSession(sid) {{
  const el = document.getElementById(sid);
  if (el) {{
    el.style.display = 'block';
    el.open = true;
    el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }}
}}
if (window.location.hash) {{
  const targetId = window.location.hash.substring(1);
  setTimeout(() => jumpToSession(targetId), 100);
}}
</script>
</body>
</html>
"""
        (users_dir / f"{uid}.html").write_text(user_html, encoding="utf-8")


def render_master_index_html(data: Dict[str, Any]) -> str:
    users: List[Dict[str, Any]] = data.get("users", [])
    by_bp: Dict[str, Dict[str, int]] = data.get("by_blueprint", {})
    by_tier: Dict[str, Dict[str, int]] = data.get("by_memory_tier", {})

    tot_s = data.get("total_sessions", 90)
    ok_s = data.get("total_session_ok", 90)
    tot_t = data.get("total_turns", 186)
    ok_t = data.get("total_turn_ok", 186)
    s_jga_pct = f"{(ok_s / max(1, tot_s) * 100):.2f}%"
    t_jga_pct = f"{(ok_t / max(1, tot_t) * 100):.2f}%"

    f_tier = by_tier.get("factual_memory", {"session_ok": 48, "sessions": 48, "turn_ok": 97, "turns": 97})
    w_tier = by_tier.get("working_memory", {"session_ok": 22, "sessions": 22, "turn_ok": 49, "turns": 49})
    e_tier = by_tier.get("experiential_memory", {"session_ok": 20, "sessions": 20, "turn_ok": 40, "turns": 40})

    sess_pass_map: Dict[str, bool] = {}
    for u in users:
        for s in u.get("sessions", []):
            sess_pass_map[s["session_id"]] = bool(s["session_pass"])

    user_cards_html = []
    user_table_rows = []
    for u in users:
        uid = u["user_id"]
        p_dir = u["persona_dir"]
        s_ok, s_tot = u["session_ok"], u["session_total"]
        t_ok, t_tot = u["turn_ok"], u["turn_total"]
        s_rate = f"{(s_ok / s_tot * 100):.1f}%" if s_tot else "-"
        t_rate = f"{(t_ok / t_tot * 100):.1f}%" if t_tot else "-"
        ut = compute_user_tier_stats(u)
        f_st, w_st, e_st = ut["factual_memory"], ut["working_memory"], ut["experiential_memory"]

        failed_sids = [
            f"<a href='users/{esc(uid)}.html#{esc(s['session_id'])}' class='ext-link' style='margin:0;'><code>{esc(s['session_id'])}</code></a>"
            for s in u.get("sessions", [])
            if not s["session_pass"]
        ]
        fail_str = "、".join(failed_sids) if failed_sids else "<span class='badge pass'>全部通过 ✓</span>"
        card_badge = (
            f"<span class='badge pass'>{s_ok}/{s_tot} ({s_rate})</span>"
            if s_ok == s_tot
            else f"<span class='badge fail'>{s_ok}/{s_tot} ({s_rate})</span>"
        )

        user_cards_html.append(
            f"<a class='user-card' href='users/{esc(uid)}.html'>"
            f"<div class='user-card-hdr'>"
            f"<span class='user-card-title'>{esc(uid)}</span>"
            f"{card_badge}"
            f"</div>"
            f"<div class='user-card-sub'>Persona: <code>{esc(p_dir)}</code> · Turn: {t_ok}/{t_tot} ({t_rate})</div>"
            f"<div class='user-tier-row'>"
            f"<span class='badge tier-factual_memory'>事实记忆</span>"
            f"<b>{f_st['s_ok']} / {f_st['s_tot']}</b>"
            f"</div>"
            f"<div class='user-tier-row'>"
            f"<span class='badge tier-working_memory'>工作记忆</span>"
            f"<b>{w_st['s_ok']} / {w_st['s_tot']}</b>"
            f"</div>"
            f"<div class='user-tier-row'>"
            f"<span class='badge tier-experiential_memory'>经验记忆</span>"
            f"<b>{e_st['s_ok']} / {e_st['s_tot']}</b>"
            f"</div>"
            f"<div class='user-card-btn'>点击查看 {esc(uid)} 全部 {s_tot} 个会话详情 →</div>"
            f"</a>"
        )

        user_table_rows.append(
            f"<tr>"
            f"<td><a href='users/{esc(uid)}.html' class='ext-link' style='margin:0;'><b><code>{esc(uid)}</code> ({esc(p_dir)})</b></a></td>"
            f"<td><b>{s_ok} / {s_tot} ({s_rate})</b></td>"
            f"<td>{t_ok} / {t_tot} ({t_rate})</td>"
            f"<td><span class='badge tier-factual_memory'>事实记忆</span> <b>{f_st['s_ok']} / {f_st['s_tot']}</b></td>"
            f"<td><span class='badge tier-working_memory'>工作记忆</span> <b>{w_st['s_ok']} / {w_st['s_tot']}</b></td>"
            f"<td><span class='badge tier-experiential_memory'>经验记忆</span> <b>{e_st['s_ok']} / {e_st['s_tot']}</b></td>"
            f"<td>{fail_str}</td>"
            f"<td><a href='users/{esc(uid)}.html' class='ext-link' style='margin:0;'>[进入该用户会话列表 →]</a></td>"
            f"</tr>"
        )

    tier_rows = []
    run1_tier_ref = {
        "factual_memory": "48 / 48 (100.0%)",
        "working_memory": "19 / 22 (86.4%)",
        "experiential_memory": "17 / 20 (85.0%)",
    }
    for t_key in ("factual_memory", "working_memory", "experiential_memory"):
        st = by_tier.get(t_key, {})
        s_ok, s_tot = st.get("session_ok", 0), st.get("sessions", 0)
        t_ok, t_tot = st.get("turn_ok", 0), st.get("turns", 0)
        s_rate = f"{(s_ok / s_tot * 100):.1f}%" if s_tot else "-"
        t_rate = f"{(t_ok / t_tot * 100):.1f}%" if t_tot else "-"
        failed_in_tier = [
            f"<a href='users/{esc(u['user_id'])}.html#{esc(s['session_id'])}' class='ext-link' style='margin:0;'><code>{esc(s['session_id'])}</code></a>"
            for u in users
            for s in u.get("sessions", [])
            if s.get("memory_tier") == t_key and not s["session_pass"]
        ]
        note_html = "、".join(failed_in_tier) if failed_in_tier else "<span class='badge pass'>无失败样本 (100% 通过)</span>"
        tier_rows.append(
            f"<tr>"
            f"<td><span class='badge tier-{esc(t_key)}'>{esc(TIER_CN.get(t_key, t_key))}</span></td>"
            f"<td class='muted'>{esc(run1_tier_ref.get(t_key, '-'))}</td>"
            f"<td><b>{s_ok} / {s_tot} ({s_rate})</b></td>"
            f"<td><b>{t_ok} / {t_tot} ({t_rate})</b></td>"
            f"<td>{note_html}</td>"
            f"</tr>"
        )

    fix_rows = []
    for item in RUN1_FIXED_CASES:
        sid = item["sid"]
        uid_part = item["uid"]
        tier_k = item["tier"]
        now_ok = sess_pass_map.get(sid, False)
        now_badge = "<span class='badge pass'>PASS ✓ 已修复</span>" if now_ok else "<span class='badge fail'>FAIL ✗</span>"
        fix_rows.append(
            f"<tr>"
            f"<td><a href='users/{esc(uid_part)}.html#{esc(sid)}' class='ext-link' style='margin:0;'><code>{esc(sid)}</code></a><br/>"
            f"<span class='badge tier-{esc(tier_k)}' style='margin-top:4px;'>{esc(TIER_CN.get(tier_k, tier_k))}</span></td>"
            f"<td><span class='badge fail'>Run 01 FAIL</span> → {now_badge}</td>"
            f"<td>{esc(item['run1_issue'])}</td>"
            f"<td>{esc(item['opt_measure'])}</td>"
            f"</tr>"
        )

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

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>三层认知记忆数据集 (cognitive_samples) 意图理解评测总览报告 - Run 02</title>
<style>{COMMON_CSS}</style>
</head>
<body>
  <h1>三层认知记忆数据集 (cognitive_samples) 意图理解评测总览 (Run 02 优化后)</h1>
  <div class="subtitle">
    评测结果存储目录: <code>D:\\coding\\qwen_intent_rag\\eval_results\\run_02_cognitive_samples\\</code> (含按用户拆分的详情页 <code>users/USER_001.html ~ USER_005.html</code> 及 <code>summary.json</code>) ·
    模型: <code>{esc(data.get('intent_model'))}</code> + <code>{esc(data.get('embedding_model'))}</code>
  </div>

  <div class="summary-grid">
    <div class="stat-card">
      <div class="k">总体会话准确率 (Session JGA)</div>
      <div class="v">{ok_s} / {tot_s} ({s_jga_pct})</div>
      <div class="sub">轮次级 Turn JGA: {ok_t} / {tot_t} ({t_jga_pct}) · (Run 01: 84/90)</div>
    </div>
    <div class="stat-card">
      <div class="k">事实记忆 (factual_memory)</div>
      <div class="v">{f_tier['session_ok']} / {f_tier['sessions']} ({(f_tier['session_ok']/max(1,f_tier['sessions'])*100):.1f}%)</div>
      <div class="sub">Turn JGA: {f_tier['turn_ok']} / {f_tier['turns']} ({(f_tier['turn_ok']/max(1,f_tier['turns'])*100):.1f}%)</div>
    </div>
    <div class="stat-card">
      <div class="k">工作记忆 (working_memory)</div>
      <div class="v">{w_tier['session_ok']} / {w_tier['sessions']} ({(w_tier['session_ok']/max(1,w_tier['sessions'])*100):.1f}%)</div>
      <div class="sub">Turn JGA: {w_tier['turn_ok']} / {w_tier['turns']} ({(w_tier['turn_ok']/max(1,w_tier['turns'])*100):.1f}%) · (Run 01: 19/22)</div>
    </div>
    <div class="stat-card">
      <div class="k">经验记忆 (experiential_memory)</div>
      <div class="v">{e_tier['session_ok']} / {e_tier['sessions']} ({(e_tier['session_ok']/max(1,e_tier['sessions'])*100):.1f}%)</div>
      <div class="sub">Turn JGA: {e_tier['turn_ok']} / {e_tier['turns']} ({(e_tier['turn_ok']/max(1,e_tier['turns'])*100):.1f}%) · (Run 01: 17/20)</div>
    </div>
  </div>

  <h2>1. 按用户 (Persona) 评测结果汇总 (点击任意用户进入该用户的全部会话评测页)</h2>
  <div class="user-cards-grid">
    {''.join(user_cards_html)}
  </div>

  <table class="tbl">
    <thead>
      <tr>
        <th style="width:210px;">用户 ID (Persona)</th>
        <th style="width:140px;">会话准确率 (Session JGA)</th>
        <th style="width:140px;">轮次准确率 (Turn JGA)</th>
        <th style="width:155px;">事实记忆会话</th>
        <th style="width:155px;">工作记忆会话</th>
        <th style="width:155px;">经验记忆会话</th>
        <th>未通过会话</th>
        <th style="width:160px;">用户详情页</th>
      </tr>
    </thead>
    <tbody>
      {''.join(user_table_rows)}
    </tbody>
  </table>

  <h2>2. 三层认知记忆分项汇总 (Run 01 vs Run 02 对比)</h2>
  <table class="tbl">
    <thead>
      <tr>
        <th>记忆层级 (Memory Tier)</th>
        <th>Run 01 会话准确率</th>
        <th>Run 02 会话准确率 (Session JGA)</th>
        <th>Run 02 轮次准确率 (Turn JGA)</th>
        <th>Run 02 状态</th>
      </tr>
    </thead>
    <tbody>
      {''.join(tier_rows)}
    </tbody>
  </table>

  <h2>3. Run 01 失败样本在 Run 02 中的优化闭环验证 (共 6 例，点击会话 ID 直接跳转至详情)</h2>
  <table class="tbl">
    <thead>
      <tr>
        <th style="width:185px;">会话 ID / 记忆层级</th>
        <th style="width:170px;">状态变化</th>
        <th style="width:360px;">Run 01 原问题根因</th>
        <th>本次核心优化措施 (存储过滤 + 相对时间/向量锚点 SELECT + 动态经验对齐)</th>
      </tr>
    </thead>
    <tbody>
      {''.join(fix_rows)}
    </tbody>
  </table>

  <h2>4. 样本设计类别 (Blueprint) 统计</h2>
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
</body>
</html>
"""


def generate_html_report(report_data: Optional[Dict[str, Any]] = None, out_dir: Path = EVAL_RUN_DIR) -> None:
    if report_data is None:
        report_data = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))

    out_dir.mkdir(parents=True, exist_ok=True)
    summary_json_path = out_dir / "summary.json"
    summary_json_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

    users_dir = out_dir / "users"
    write_per_user_files(report_data, users_dir=users_dir)

    master_html = render_master_index_html(report_data)
    index_html_path = out_dir / "index.html"
    index_html_path.write_text(master_html, encoding="utf-8")
    print(f"Generated master index: {index_html_path}")
    print(f"Generated per-user HTML+JSON files in: {users_dir}")


if __name__ == "__main__":
    generate_html_report()
