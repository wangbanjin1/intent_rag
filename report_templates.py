"""report_templates.py — 错误分析报告 HTML/CSS/JS 模板与组件渲染。

职责:
  1. 委托 prompts.report_html_templates 提供页面样式 (_CSS) 与交互脚本 (_JS)
  2. 渲染 Record 选取对照表、逐轮状态演进面板、对话气泡流与 GT vs 预测差异表
  3. 组装完整单文件 HTML 错误分析报告 (render_report_page)
"""
from __future__ import annotations

import collections
import html
import json
import re
from typing import Any

from prompts.report_html_templates import (
    DUP_KEY_BADGE,
    REPORT_CSS,
    REPORT_JS,
    format_report_html_document,
)
from report_metrics import (
    CAT_META,
    CAT_ORDER,
    EVAL_SLOTS,
    FIELD_LABEL,
    REC_MATCH_FIELDS,
    TAG_META,
    TAG_ORDER,
    TURN_CATS,
    _cum_gt_by_turn,
    _pred_by_turn,
    _rec_brief,
    _rec_flat,
    _walk_gt_leaves,
    latest_source,
)

_CSS = REPORT_CSS
_JS = REPORT_JS
_MI = [False]


def record_analysis(
    session: dict[str, Any],
    d: dict[str, Any],
    diffs: list[str],
    src_map: dict[str, Any],
    users: dict[str, list[dict[str, Any]]],
) -> str:
    """record_wrong 样本：完整 records 列表 + GT 选择/模型选择行高亮对照。"""
    uid = session.get("user_id", "")
    recs = users.get(uid) or []
    if not recs:
        return ""
    k, turn = d["intent_key"], d["turn"]

    gt_ref = None
    for f in diffs:
        src = latest_source(src_map, k, f, turn)
        if src and src[0] == "Record":
            gt_ref = src[1]
            break
    gt_idx = next((i for i, r in enumerate(recs) if r.get("record_id") == gt_ref), None)

    pred = d["pred"]
    best_i, best_score, best_hits = None, 0, []
    for i, r in enumerate(recs):
        rf = _rec_flat(r)
        hits = [
            f
            for f in REC_MATCH_FIELDS
            if rf.get(f) is not None and (pred.get(f) or None) == rf.get(f)
        ]
        if len(hits) > best_score:
            best_i, best_score, best_hits = i, len(hits), hits
    if best_score < 2:
        best_i = None

    esc = html.escape
    lines = []
    if gt_idx is not None:
        lines.append(f"<b>GT 应取:</b> {esc(_rec_brief(recs[gt_idx], gt_idx))}")
    else:
        lines.append(f"<b>GT 应取:</b> record_id={esc(str(gt_ref))}（未在用户 records 中找到）")
    if best_i is not None:
        mark = "（与 GT 同一条 ✓ 取值却错，疑部分字段未取）" if best_i == gt_idx else ""
        lines.append(
            f"<b>模型取了:</b> {esc(_rec_brief(recs[best_i], best_i))}"
            f" — 匹配字段: {esc(','.join(best_hits))}{esc(mark)}"
        )
    else:
        lines.append("<b>模型取了:</b> 与任何 record 都不匹配（未取值/取值幻觉，pred 多字段为空或自定义）")

    rows = []
    for i, r in enumerate(recs):
        f = _rec_flat(r)
        cls = ""
        if i == gt_idx:
            cls = ' class="rec-gt"'
        elif i == best_i:
            cls = ' class="rec-pred"'
        gt_mark = "<b>✓ GT</b>" if i == gt_idx else ""
        pred_mark = (
            f"<b>✓ 模型</b><span class='en'>匹配{len(best_hits)}字段</span>"
            if i == best_i
            else ""
        )
        rows.append(
            f"<tr{cls}><td>{i}</td><td>{esc(str(r.get('record_id')))}</td>"
            f"<td>{esc(str(f['application_name']))}</td><td>{esc(str(f['service_name']))}</td>"
            f"<td>{esc(str(f['resolution']))}</td><td>{esc(str(f['rtt']))}</td>"
            f"<td>{esc(str(f['completed_at']))}</td>"
            f"<td class='rec-mark-gt'>{gt_mark}</td><td class='rec-mark-pred'>{pred_mark}</td></tr>"
        )
    lines.append(
        '<table class="diff rec-table"><thead><tr><th>#</th><th>record_id</th>'
        "<th>应用</th><th>业务</th><th>清晰度</th><th>时延</th><th>完成时间</th>"
        "<th>GT 选择</th><th>模型选择</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>'
    )
    return '<div class="rec-cmp">' + "<br>".join(lines) + "</div>"


def _state_pane_html(
    gt_s: dict[str, Any], pred_s: dict[str, Any], prev_pred: dict[str, Any]
) -> str:
    """单轮状态面板: 每意图 7 字段 GT vs pred 紧凑表; 较上一轮变化的 pred 字段蓝色高亮。"""
    keys = sorted(set(gt_s) | set(pred_s))
    if not keys:
        return "<p class='no-raw'>（该轮无保障意图状态）</p>"
    boxes = []
    for k in keys:
        g, p = gt_s.get(k), pred_s.get(k)
        cancelled = isinstance(g, dict) and g.get("__cancelled__")
        chip = ""
        if cancelled:
            chip = "<span class='tag' style='background:#d35400;color:#fff'>应已取消</span>"
        elif g is None:
            noapp = not (p or {}).get("application_name")
            chip = (
                "<span class='tag' style='background:#b9770e;color:#fff'>模型独有·空app头</span>"
                if noapp
                else "<span class='tag' style='background:#b9770e;color:#fff'>模型独有</span>"
            )
        elif p is None or not p:
            chip = "<span class='tag' style='background:#8e44ad;color:#fff'>模型缺失</span>"
        exempt = (g or {}).get("__exempt__") if isinstance(g, dict) else None
        if exempt:
            chip += (
                f"<span class='tag' style='background:#c0392b;color:#fff' "
                f"title='改口错误值轮: 豁免字段豁免比对'>改口轮豁免: {html.escape(str(exempt))}</span>"
            )
        rows = []
        for s in EVAL_SLOTS:
            gv = (g or {}).get(s) if isinstance(g, dict) else None
            pv = (p or {}).get(s)
            bad = g is not None and p and not cancelled and (gv or None) != (pv or None)
            chg = (prev_pred.get(k) or {}).get(s) != pv and pv is not None
            cls = ' class="bad"' if bad else (' class="chg"' if chg else "")
            mark = "✗" if bad else ("✓" if (g is not None and p and not cancelled) else "—")
            rows.append(
                f"<tr{cls}><td>{FIELD_LABEL.get(s, s)}</td>"
                f"<td>{html.escape(str(gv)) if gv is not None else '<span class=no-raw>—</span>'}</td>"
                f"<td>{html.escape(str(pv)) if pv is not None else '<span class=no-raw>—</span>'}</td>"
                f"<td>{mark}</td></tr>"
            )
        boxes.append(
            f"<div class='evo-intent'><b>{html.escape(k)}</b> {chip}"
            f"<table class='diff evo-tbl'><thead><tr><th>字段</th><th>GT</th>"
            f"<th>模型</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )
    return "".join(boxes)


def state_evolution_html(
    session: dict[str, Any], r: dict[str, Any], focus_turn: int, cid: str
) -> str:
    """状态演进交互模块: 左侧对话轮(可点击), 右侧显示截止该轮的累积状态 (GT vs 模型)。"""
    gt_map = _cum_gt_by_turn(session, mi_on=_MI[0])
    pred_map = _pred_by_turn(r)
    raw_by_turn = {p["turn"]: (p.get("llm_raw") or "") for p in (r.get("turn_outputs") or [])}
    if not raw_by_turn:
        raw_by_turn = {td["turn"]: (td.get("llm_raw") or "") for td in (r.get("turn_detail") or [])}
    rows_html = []
    panes_html = []
    prev_pred: dict[str, Any] = {}
    gt_acc: dict[str, Any] = {}
    pred_acc: dict[str, Any] = {}
    for ev in session.get("event_sequence") or []:
        t = ev.get("turn", 0)
        u = html.escape((ev.get("user") or {}).get("utterance", ""))
        a = html.escape((ev.get("agent") or {}).get("utterance", ""))
        cur = " current" if t == focus_turn else ""
        model_html = ""
        if raw_by_turn.get(t):
            model_html = (
                f"<details class='model-out'><summary>模型实时输出（第{t}轮）▸</summary>"
                f"<pre class='raw'>{html.escape(raw_by_turn[t])}</pre></details>"
            )
        rows_html.append(
            f"<div class='msg-row evo-turn{cur}' data-turn='{t}' "
            f"onclick=\"evoShow('{cid}',{t})\"><div class='msg-turn'>第{t}轮</div>"
            f"<div class='msg-bubbles'><div class='bubble user'>{u}</div>"
            f"<div class='bubble agent'>{a}</div>{model_html}</div></div>"
        )
        gt_acc = gt_map.get(t, gt_acc)
        pred_acc = pred_map.get(t, pred_acc)
        disp = "" if t == focus_turn else " style='display:none'"
        panes_html.append(
            f"<div class='evo-pane' data-turn='{t}'{disp}>"
            f"<div class='evo-pane-title'>截止第{t}轮的累积状态"
            f"<span class='legend'><i class='lg bad'></i>与GT不一致 <i class='lg chg'></i>本轮模型改动</span></div>"
            + _state_pane_html(gt_acc, pred_acc, prev_pred)
            + "</div>"
        )
        prev_pred = pred_acc
    return (
        f"<div class='evo' id='{cid}'><div class='evo-conv'>{''.join(rows_html)}</div>"
        f"<div class='evo-state'>{''.join(panes_html)}</div></div>"
    )


def conversation_html(
    session: dict[str, Any],
    upto_turn: int,
    turn_detail: list[dict[str, Any]] | None = None,
) -> str:
    """对话内容：user/agent 气泡来自数据集剧本；每轮追加该轮模型实时输出与注入 prompt。"""
    raw_by_turn = {
        td["turn"]: td.get("llm_raw") or ""
        for td in (turn_detail or [])
        if td.get("llm_raw")
    }
    rows = []
    for ev in session.get("event_sequence") or []:
        if ev.get("turn", 0) > upto_turn:
            break
        u = html.escape((ev.get("user") or {}).get("utterance", ""))
        a = html.escape((ev.get("agent") or {}).get("utterance", ""))
        t = ev.get("turn", 0)
        mark = " current" if t == upto_turn else ""
        model_html = ""
        if raw_by_turn.get(t):
            model_html = (
                f"<details class='model-out'><summary>模型实时输出（第{t}轮）▸</summary>"
                f"<pre class='raw'>{html.escape(raw_by_turn[t])}</pre></details>"
            )
        _ptxt = turn_detail or [{}]
        _pt = next(
            (
                d.get("task_prompt")
                for d in _ptxt
                if isinstance(d, dict) and d.get("turn") == t and d.get("task_prompt")
            ),
            "",
        )
        if _pt:
            _ci = _pt.find("CANDIDATE MODE")
            _show = _pt[max(0, _ci - 60) : _ci + 2200] if _ci >= 0 else _pt[-2600:]
            model_html += (
                f"<details class='model-out'><summary>系统注入 prompt（第{t}轮"
                f"{'，含候选列表' if _ci >= 0 else ''}）▸</summary>"
                f"<pre class='raw'>{html.escape(_show)}</pre></details>"
            )
        rows.append(
            f'<div class="msg-row{mark}"><div class="msg-turn">第{t}轮</div>'
            f'<div class="msg-bubbles"><div class="bubble user">{u}</div>'
            f'<div class="bubble agent">{a}</div>{model_html}</div></div>'
        )
    return f'<div class="conv">{"".join(rows)}</div>'


def diff_table_html(
    gt: dict[str, Any],
    pred: dict[str, Any],
    diffs: list[str],
    src_map: dict[str, Any],
    intent_key: str,
    turn: int,
    derived: list[str] | None = None,
) -> str:
    derived_set = set(derived or [])
    rows = []
    for s in EVAL_SLOTS:
        g, p = gt.get(s), pred.get(s)
        bad = s in diffs
        if s in derived_set:
            src_txt = "GT自动推算"
            g_cell = (
                f"{html.escape(str(g))} "
                f"<span class='derived-badge' title='GT 未登记该字段, 评估口径按另两时间字段知二推一补齐 (v25.1)'>推算</span>"
            )
        else:
            src = latest_source(src_map, intent_key, s, turn)
            src_txt = f"{src[0]}:{src[1]}" if src else "—"
            g_cell = html.escape(str(g))
        src_cls = " class='src-derived'" if s in derived_set else ""
        cls = ' class="bad"' if bad else ""
        rows.append(
            f"<tr{cls}><td>{FIELD_LABEL.get(s, s)}<span class='en'>{s}</span></td>"
            f"<td>{g_cell}</td>"
            f"<td>{html.escape(str(p))}</td>"
            f"<td{src_cls}>{html.escape(src_txt)}</td></tr>"
        )
    return (
        '<table class="diff"><thead><tr><th>字段</th><th>GT 标准答案</th>'
        f"<th>模型预测</th><th>GT 来源(≤第{turn}轮)</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def llm_raw_html(d: dict[str, Any] | None) -> str:
    raw = (d or {}).get("llm_raw") or ""
    if not raw:
        return (
            '<p class="no-raw">本次评估未保存 LLM 原始输出（旧版 eval 文件）。'
            "重新运行 session_eval 后自动包含。</p>"
        )
    return f'<pre class="raw">{html.escape(raw)}</pre>'


def slot_updates_table_html(session: dict[str, Any]) -> str:
    """GT slot_updates 全量表: intent_id / 轮 / 字段 / 值 / 来源类型 / 来源引用。"""
    rows = []
    for su in session.get("slot_updates") or []:
        iid = su.get("intent_id")
        for tu in su.get("turn_updates") or []:
            turn = tu.get("turn")
            for field, leaf in _walk_gt_leaves(tu.get("params") or {}):
                st = leaf.get("source_type") or "—"
                sr = leaf.get("source_ref") or "—"
                src_cls = f" src-{str(st).lower()}" if st != "—" else ""
                rows.append(
                    f"<tr><td>{html.escape(str(iid))}</td><td>{turn}</td>"
                    f"<td>{html.escape(str(field))}</td>"
                    f"<td>{html.escape(str(leaf.get('value')))}</td>"
                    f"<td class='{src_cls.strip()}'>{html.escape(str(st))}</td>"
                    f"<td>{html.escape(str(sr))}</td></tr>"
                )
    if not rows:
        return "<p class='no-raw'>（无 slot_updates）</p>"
    return (
        '<table class="diff gt-src"><thead><tr><th>意图</th><th>轮</th><th>字段</th>'
        "<th>GT 值</th><th>来源类型</th><th>来源引用</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def raw_data_html(
    session: dict[str, Any],
    users: dict[str, list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
) -> str:
    """数据集原始信息全量: session meta + 画像 profile + records 列表 + GT slot_updates + 全量 JSON。"""
    esc = html.escape
    if not session:
        return "<p class='no-raw'>（数据集中未找到该 session）</p>"
    uid = session.get("user_id", "")
    meta_rows = "".join(
        [
            f"<tr><th>session_id</th><td>{esc(str(session.get('session_id')))}</td></tr>",
            f"<tr><th>user_id</th><td>{esc(str(uid))}</td></tr>",
            f"<tr><th>reference_time</th><td>{esc(str(session.get('reference_time')))}</td></tr>",
            f"<tr><th>intents 数</th><td>{len(session.get('intents') or [])}</td></tr>",
            f"<tr><th>对话轮数</th><td>{len(session.get('event_sequence') or [])}</td></tr>",
        ]
    )
    meta = f'<table class="diff meta">{meta_rows}</table>'

    prof = profiles.get(uid) or {}
    prof_html = (
        "<p class='no-raw'>（无画像 profile）</p>"
        if not prof
        else '<table class="diff meta">'
        + "".join(f"<tr><th>{esc(str(k))}</th><td>{esc(str(v))}</td></tr>" for k, v in prof.items())
        + "</table>"
    )

    recs = users.get(uid) or []
    if recs:
        rec_rows = "".join(
            f"<tr><td>{i}</td><td>{esc(str(r.get('record_id')))}</td>"
            f"<td>{esc(str(r.get('application_name')))}</td><td>{esc(str(r.get('service_name')))}</td>"
            f"<td>{esc(str((r.get('resolution') or {}).get('min_value')))}</td>"
            f"<td>{esc(str((r.get('rtt') or {}).get('max_value')))}</td>"
            f"<td>{esc(str((r.get('timestamp') or {}).get('start_timestamp')))}</td>"
            f"<td>{esc(str((r.get('timestamp') or {}).get('end_timestamp')))}</td>"
            f"<td>{esc(str((r.get('timestamp') or {}).get('duration')))}</td>"
            f"<td>{esc(str(r.get('completed_at')))}</td></tr>"
            for i, r in enumerate(recs)
        )
        rec_html = (
            '<table class="diff gt-src"><thead><tr><th>#</th><th>record_id</th>'
            "<th>应用</th><th>业务</th><th>清晰度</th><th>时延</th>"
            "<th>开始</th><th>结束</th><th>时长</th><th>完成时间</th></tr></thead>"
            f"<tbody>{rec_rows}</tbody></table>"
        )
    else:
        rec_html = "<p class='no-raw'>（无 records）</p>"

    full_json = esc(json.dumps(session, ensure_ascii=False, indent=1))
    return (
        "<details class='rawdata'><summary>数据集原始信息（session meta / 画像 / records / GT slot_updates / 全量 JSON，点击展开）</summary>"
        f"<h5>session meta</h5>{meta}"
        f"<h5>用户画像 profile（{esc(str(uid))}）</h5>{prof_html}"
        f"<h5>用户 records（{len(recs)} 条，completed_at 降序 = 模型所见顺序）</h5>{rec_html}"
        f"<h5>GT slot_updates（逐轮逐字段，含来源）</h5>{slot_updates_table_html(session)}"
        f"<h5>session 全量 JSON</h5><pre class='raw'>{full_json}</pre>"
        "</details>"
    )


def analysis_text(
    cat: str,
    d: dict[str, Any],
    diffs: list[str],
    src_map: dict[str, Any],
    near: list[str] | None = None,
) -> str:
    k, turn = d["intent_key"], d["turn"]
    parts = []
    if cat == "cancel_wrong":
        parts.append(
            f"第{turn}轮用户明确取消意图「{k}」（GT 要求移除），但模型输出中该意图仍存在，取消未执行。"
        )
    elif cat == "missed_intent":
        if near:
            parts.append(
                f"第{turn}轮模型未输出 key 为「{k}」的意图，但最终态存在近邻意图："
                f"{'、'.join(near)}。属 intent 头 key 错位（空 app 头/改口轮残留旧 key/消歧选错记录），"
                f"非完全漏识别——修 key 一致性即可救回。"
            )
        else:
            parts.append(
                f"第{turn}轮模型未输出意图「{k}」（GT 存在），最终态也无近邻 key，属于真·意图漏识别。"
            )
        if d.get("gt", {}).get("application_name"):
            src = latest_source(src_map, k, "application_name", turn)
            if src and src[0] in ("Record", "Memory"):
                parts.append(
                    f"注：该意图的 app/service 来自{src[0]}({src[1]})，话语本就不复述名称"
                    f"（T3-3 消歧/记忆复用场景设计如此），GT 无标注错误。"
                )
    else:
        for f in diffs:
            g, p = d["gt"].get(f), d["pred"].get(f)
            src = latest_source(src_map, k, f, turn)
            src_txt = f"，GT 来源 {src[0]}:{src[1]}" if src else ""
            parts.append(f"字段 {FIELD_LABEL.get(f, f)}({f}): GT={g!r} vs 预测={p!r}{src_txt}")
    verdict = {
        "record_wrong": "错字段来自用户历史记录（record 指代），模型 record 提取/选择错误。",
        "memory_wrong": "错字段来自用户画像（Memory），画像引用错误。",
        "time_wrong": "时间三字段推算错误。",
        "appsvc_wrong": "应用/服务识别错误（疑似多意图张冠李戴）。",
        "value_wrong": "字段取值与本轮表述/GT 不一致。",
    }.get(cat)
    if verdict:
        parts.append("⇒ " + verdict)
    return "<br>".join(html.escape(x) for x in parts)


def _render_summary_banner(
    eval_path: str,
    dataset_dir: str,
    metrics: dict[str, Any],
    noise_map: dict[str, list[str]],
) -> str:
    """渲染报告顶部横幅、核心统计卡片网格与视图切换导航条。"""
    esc = html.escape
    n_sess = metrics["n_sess"]
    n_sess_match = metrics["n_sess_match"]
    n_turn = metrics["n_turn"]
    n_turn_match = metrics["n_turn_match"]
    sess_set_mismatch = metrics["sess_set_mismatch"]
    sess_field_wrong = metrics["sess_field_wrong"]
    n_err_turn = sum(len(v) for v in metrics["cases"].values())
    rate_s = n_sess_match / max(n_sess, 1)
    rate_t = n_turn_match / max(n_turn, 1)
    noise_stat = (
        f'<div class="stat"><div class="num">{len(noise_map)}</div>'
        '<div class="lbl">数据集噪声样本（已剔除，见文末）</div></div>'
        if noise_map
        else ""
    )
    return (
        '<div class="hero">'
        "<h1>错误样本分析报告</h1>"
        f'<div class="meta">评估文件 <code>{esc(eval_path)}</code> ｜ 数据集 <code>{esc(dataset_dir)}</code></div>'
        "</div>"
        '<div class="stat-grid">'
        f'<div class="stat"><div class="num">{rate_s:.2%}</div><div class="lbl">session 级 JGA（{n_sess_match}/{n_sess}）</div></div>'
        f'<div class="stat"><div class="num">{rate_t:.2%}</div><div class="lbl">turn 级 JGA（{n_turn_match}/{n_turn}）</div></div>'
        f'<div class="stat red"><div class="num">{n_err_turn}</div><div class="lbl">错误 turn 样本</div></div>'
        f'<div class="stat red"><div class="num">{n_sess - n_sess_match}</div><div class="lbl">错误 session（集合不一致 {len(sess_set_mismatch)} + 字段错 {len(sess_field_wrong)}）</div></div>'
        f"{noise_stat}"
        "</div>"
        '<div class="viewbar">'
        f'<button id="vb-turn" class="viewbtn active" onclick="switchView(\'turn\')">turn 级视图<small>逐轮累积态比对 ｜ 错误 {n_err_turn} 样本 ｜ turn JGA {rate_t:.2%}</small></button>'
        f'<button id="vb-sess" class="viewbtn" onclick="switchView(\'sess\')">session 级视图<small>最终意图集合严格全等 ｜ 错误 {n_sess - n_sess_match} session ｜ session JGA {rate_s:.2%}</small></button>'
        "</div>"
    )


def _render_category_breakdown_table(
    by_tpl: Any,
    by_tpl_num: Any,
    by_tpl_den: Any,
    title: str,
) -> str:
    """渲染按模板分桶的 turn 级或 session 级正确率面板。"""
    rows = "".join(
        f"<tr><td><b>{t}</b></td><td>{by_tpl_num[t]} / {by_tpl_den[t]}</td>"
        f"<td><div class='bar'><i style='width:{by_tpl_num[t]/max(by_tpl_den[t],1)*100:.0f}%'></i></div></td>"
        f"<td>{by_tpl_num[t]/max(by_tpl_den[t],1):.1%}</td></tr>"
        for t in sorted(by_tpl)
    )
    return (
        f"<div class='panel'><h3>{html.escape(title)}</h3>"
        f"<table class='tpl'><tr><th>模板</th><th>正确 / 总数</th><th></th><th>正确率</th></tr>{rows}</table></div>"
    )


def _render_field_accuracy_section(r: dict[str, Any]) -> str:
    """渲染单 session 最终意图集合中每个意图 7 字段的 GT vs 预测对照框。"""
    esc = html.escape
    gts, preds = r.get("gt_intents") or {}, r.get("pred_intents") or {}
    keys = sorted(set(gts) | set(preds))
    if not keys:
        return "<p class='no-raw'>（无意图）</p>"
    boxes = []
    for idx, k in enumerate(keys, 1):
        g, p = gts.get(k), preds.get(k)
        if g is not None and p is not None:
            bad_fields = [s for s in EVAL_SLOTS if (g.get(s) or None) != (p.get(s) or None)]
            rel = (
                ("intent-ok", "✓ 字段全等")
                if not bad_fields
                else ("intent-bad", f"✗ 字段不一致: {'、'.join(FIELD_LABEL.get(f, f) for f in bad_fields)}")
            )
        elif g is not None:
            rel = ("intent-bad", "✗ GT 独有（模型漏识别）")
        else:
            noapp = not (p or {}).get("application_name")
            rel = (
                "intent-bad",
                "✗ 模型独有（空 app 头 / key 断裂残骸）"
                if noapp
                else "✗ 模型独有（多识别 / 未取消）",
            )
        rows = []
        for s in EVAL_SLOTS:
            gv = (g or {}).get(s)
            pv = (p or {}).get(s)
            bad = g is not None and p is not None and (gv or None) != (pv or None)
            cls = ' class="bad"' if bad else ""
            mark = ("✗" if bad else "✓") if (g is not None and p is not None) else "—"
            rows.append(
                f"<tr{cls}><td>{FIELD_LABEL.get(s, s)}<span class='en'>{s}</span></td>"
                f"<td>{esc(str(gv)) if gv is not None else '<span class=no-raw>—</span>'}</td>"
                f"<td>{esc(str(pv)) if pv is not None else '<span class=no-raw>—</span>'}</td>"
                f"<td>{mark}</td></tr>"
            )
        boxes.append(
            f"<div class='intent-box {rel[0]}'><div class='intent-head'>"
            f"意图{idx}：<b>{esc(k)}</b> <span class='intent-rel'>{esc(rel[1])}</span></div>"
            f"<table class='diff'><thead><tr><th>字段</th><th>GT</th>"
            f"<th>模型预测</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )
    return "".join(boxes)


def _render_session_status_table(r: dict[str, Any]) -> str:
    """渲染单 session 意图状态机轨迹对照表。"""
    esc = html.escape
    sd = r.get("status_detail") or []
    if not sd:
        return (
            "<p class='no-raw'>本次评估未保存状态机明细"
            "（config status_tracking.enabled=false 或旧版产物）。</p>"
        )
    rows = []
    for e in sd:
        cls = "" if e.get("match") else ' class="bad"'
        missed = "（模型侧该意图缺失）" if e.get("missed") else ""
        rows.append(
            f"<tr{cls}><td>{esc(str(e.get('intent_key')))}</td>"
            f"<td>第{e.get('turn')}轮</td>"
            f"<td>{esc(str(e.get('gt_status')))}</td>"
            f"<td>{esc(str(e.get('pred_status')))}{esc(missed)}</td>"
            f"<td>{'✓' if e.get('match') else '✗'}</td></tr>"
        )
    return (
        '<table class="diff"><thead><tr><th>意图</th><th>判定轮</th>'
        "<th>GT 状态</th><th>模型状态</th><th></th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>'
        f"<p style='font-size:12px;color:var(--sub)'>状态机命中率: "
        f"{r.get('status_match')}/{r.get('status_total')}</p>"
    )


def _render_tag_filter_buttons(
    counter: collections.Counter[str], view_id: str, attr: str
) -> str:
    """渲染二级错误标签过滤按钮组。"""
    esc = html.escape
    btns = (
        f"<button class='tagfilter-btn active' data-t='' "
        f"onclick=\"filterTag('','{view_id}','{attr}')\">全部</button>"
    )
    for t in TAG_ORDER:
        n = counter.get(t, 0)
        if not n:
            continue
        m = TAG_META[t]
        btns += (
            f"<button class='tagfilter-btn' data-t='{t}' "
            f"style='border-color:{m['color']};color:{m['color']}' "
            f"onclick=\"filterTag('{t}','{view_id}','{attr}')\">{t} {esc(m['name'])} ×{n}</button>"
        )
    return btns


def _render_error_taxonomy_section(metrics: dict[str, Any]) -> tuple[str, str, str, str]:
    """渲染一级错误桶分布表与二级原因标签分类面板 (turn 级与 session 级)。"""
    esc = html.escape
    cases = metrics["cases"]
    tag_counter = metrics["tag_counter"]
    tag_x_cat = metrics["tag_x_cat"]
    sess_tag_counter = metrics["sess_tag_counter"]
    sess_set_mismatch = metrics["sess_set_mismatch"]
    sess_field_wrong = metrics["sess_field_wrong"]

    max_tag = max(tag_counter.values(), default=1)
    cat_short = {
        "missed_intent": "漏意图", "cancel_wrong": "取消", "record_wrong": "record",
        "memory_wrong": "画像", "time_wrong": "时间", "appsvc_wrong": "应用服务", "value_wrong": "值",
    }
    tag_rows = ""
    for t in TAG_ORDER:
        n = tag_counter.get(t, 0)
        if not n:
            continue
        m = TAG_META[t]
        cross = "，".join(f"{cat_short.get(c, c)}×{cnt}" for c, cnt in tag_x_cat[t].most_common())
        tag_rows += (
            f"<tr><td><span class='tag' style='background:{m['color']};color:#fff'>"
            f"{t}</span> <b>{esc(m['name'])}</b></td><td>{n}</td>"
            f"<td><div class='bar'><i style='width:{n/max_tag*100:.0f}%;background:{m['color']}'></i></div></td>"
            f"<td style='color:var(--sub)'>{esc(cross)}</td></tr>"
        )
    tag_desc = "".join(
        f"<li><b>{t} {esc(TAG_META[t]['name'])}</b>：{esc(TAG_META[t]['desc'])}</li>"
        for t in TAG_ORDER if tag_counter.get(t)
    )
    tag_panel = (
        "<div class='panel'><h3>原因分布（二级标签：为什么错 &gt; 错在哪里，参照 A1 ESF-Bench 分类法）</h3>"
        f"<table class='tpl'><tr><th>标签</th><th>样本数</th><th></th><th>落在一级桶</th></tr>{tag_rows}</table>"
        f"<div class='tagfilter'><b>按标签过滤样本：</b>{_render_tag_filter_buttons(tag_counter, 'view-turn', 'data-tag')}"
        "<span class='tagfilter-hint'>（只显示选中标签的 turn 级错误样本卡片，跨一级错误桶生效）</span></div>"
        "<details style='margin-top:8px'><summary style='cursor:pointer;font-size:13px;color:var(--sub)'>"
        f"标签定义与修补指向</summary><ul style='font-size:12.5px;line-height:1.8'>{tag_desc}</ul></details></div>"
    )

    max_cat = max((len(cases[c]) for c in TURN_CATS if cases.get(c)), default=1)
    dist_rows = ""
    for c in CAT_ORDER:
        if not cases.get(c):
            continue
        m = CAT_META[c]
        n = len(cases[c])
        dist_rows += (
            f"<tr><td><span class='tag' style='background:{m['color']};color:#fff'>{esc(m['title'])}</span></td><td>{n}</td>"
            f"<td><div class='bar'><i style='width:{n/max_cat*100:.0f}%;background:{m['color']}'></i></div></td>"
            f"<td style='color:var(--sub);font-size:12px'>{esc(m['desc'][:48])}…</td>"
            f"<td><a href='#view-turn' onclick=\"document.getElementById('{c}').scrollIntoView()\">查看样本</a></td></tr>"
        )
    turn_dist_panel = (
        "<div class='panel'><h3>错误分布（一级桶，按错字段位置归类）</h3>"
        f"<table class='tpl'><tr><th>错误类型</th><th>样本数</th><th></th><th>说明</th><th></th></tr>{dist_rows}</table></div>"
    )

    sess_dist_panel = (
        "<div class='panel'><h3>错误分布（session 级 = 最终意图集合严格全等）</h3>"
        "<table class='tpl'><tr><th>错误类型</th><th>session 数</th><th>说明</th></tr>"
        f"<tr><td><span class='tag' style='background:{CAT_META['intent_set_mismatch']['color']};color:#fff'>意图集合不一致</span></td>"
        f"<td>{len(sess_set_mismatch)}</td><td style='color:var(--sub);font-size:12px'>多识别（幻觉/未取消）或漏识别整个意图</td></tr>"
        f"<tr><td><span class='tag' style='background:#5d6d7e;color:#fff'>字段错误</span></td>"
        f"<td>{len(sess_field_wrong)}</td><td style='color:var(--sub);font-size:12px'>意图集合一致，但至少一个意图的字段值不全等</td></tr></table></div>"
    )

    max_stag = max(sess_tag_counter.values(), default=1)
    stag_rows = "".join(
        f"<tr><td><span class='tag' style='background:{TAG_META[t]['color']};color:#fff'>{t}</span> <b>{esc(TAG_META[t]['name'])}</b></td>"
        f"<td>{sess_tag_counter[t]}</td><td><div class='bar'><i style='width:{sess_tag_counter[t]/max_stag*100:.0f}%;background:{TAG_META[t]['color']}'></i></div></td></tr>"
        for t in TAG_ORDER if sess_tag_counter.get(t)
    )
    sess_tag_panel = (
        "<div class='panel'><h3>原因分布（session 级主因 = 该 session 失败 turn 的众数标签）</h3>"
        f"<table class='tpl'><tr><th>标签</th><th>session 数</th><th></th></tr>{stag_rows}</table>"
        f"<div class='tagfilter'><b>按主因过滤样本：</b>{_render_tag_filter_buttons(sess_tag_counter, 'view-sess', 'data-stag')}"
        "<span class='tagfilter-hint'>（只显示主因匹配的 session 样本卡片）</span></div></div>"
    )
    return turn_dist_panel, tag_panel, sess_dist_panel, sess_tag_panel


def _render_turn_sample_card(
    c: dict[str, Any],
    cat: str,
    dupkeys: set[str],
    src_maps: dict[str, dict[str, Any]],
    users: dict[str, list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
    card_ctr: list[int],
) -> str:
    """渲染单张 turn 级错误下钻卡片。"""
    esc = html.escape
    r, d, diffs, sraw = c["session"], c["detail"], c["diffs"], c["session_raw"]
    tag = c.get("tag", "")
    near = c.get("near") or []
    tmeta = TAG_META.get(tag, {})
    tag_badge = (
        f'<span class="tag" style="background:{tmeta.get("color", "#999")};color:#fff" '
        f'title="{esc(tmeta.get("desc", ""))}">{esc(tag)}·{esc(tmeta.get("name", ""))}</span>'
        if tag
        else ""
    )
    near_badge = (
        f'<span class="tag" style="background:#b9770e;color:#fff" '
        f'title="模型最终态存在近邻意图 {esc("、".join(near))}，非完全漏识别">'
        f'key错位→{esc(near[0].split("（")[0])}</span>'
        if near
        else ""
    )
    head = (
        f'<span class="sid">{r["session_id"]}</span>'
        f'<span class="tag tpl">{r["template"]}</span>'
        f'<span class="tag turn">第{d["turn"]}轮 · {esc(d["intent_key"])}</span>'
        + (DUP_KEY_BADGE if r["session_id"] in dupkeys else "")
        + tag_badge
        + near_badge
        + "".join(f'<span class="tag field">{FIELD_LABEL.get(f, f)}</span>' for f in diffs)
    )
    card_ctr[0] += 1
    evo = state_evolution_html(sraw, r, d["turn"], cid=f"evo-t{card_ctr[0]}")
    table = diff_table_html(
        d["gt"], d["pred"], diffs, src_maps[r["session_id"]], d["intent_key"], d["turn"], derived=d.get("gt_derived")
    )
    ana = analysis_text(cat, d, diffs, src_maps[r["session_id"]], near=near)
    rec_block = ""
    if cat == "record_wrong":
        rec_block = (
            "<div class='block full'><h4>Record 选取对照（GT 应取 vs 模型实取）</h4>"
            + record_analysis(sraw, d, diffs, src_maps[r["session_id"]], users)
            + "</div>"
        )
    return (
        f"<details class='sample' data-tag='{esc(tag)}'><summary>{head}</summary>"
        f"<div class='sample-body'>"
        f"<div class='block full'><h4>状态演进（点击每轮查看截止该轮的累积状态 · 红=与GT不一致 · 蓝=本轮模型改动）</h4>{evo}</div>"
        f"<div class='block'><h4>本错误轮 GT vs 预测</h4>{table}</div>"
        f"<div class='block'><h4>错误分析</h4><div class='ana'>{ana}</div></div>"
        f"{rec_block}"
        f"<div class='block full'><h4>数据集原始信息</h4>{raw_data_html(sraw, users, profiles)}</div>"
        f"<div class='block full'><h4>LLM 原始输出</h4>{llm_raw_html(d)}</div>"
        f"</div></details>"
    )


def _render_session_drilldown_cards(
    e: dict[str, Any],
    kind: str,
    dupkeys: set[str],
    users: dict[str, list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
    card_ctr: list[int],
) -> str:
    """渲染单张 session 级错误下钻卡片。"""
    esc = html.escape
    r, sraw = e["session"], e["session_raw"]
    tag = e.get("tag", "")
    tmeta = TAG_META.get(tag, {})
    tag_badge = (
        f'<span class="tag" style="background:{tmeta.get("color", "#999")};color:#fff" '
        f'title="{esc(tmeta.get("desc", ""))}">主因 {esc(tag)}·{esc(tmeta.get("name", ""))}</span>'
        if tag
        else ""
    )
    dist = e.get("tag_dist") or {}
    dist_txt = (
        "；该 session 失败 turn 原因分布: "
        + "，".join(f"{t}×{n}" for t, n in sorted(dist.items(), key=lambda x: -x[1]))
        if dist
        else ""
    )
    head = (
        f'<span class="sid">{r["session_id"]}</span>'
        f'<span class="tag tpl">{r["template"]}</span>'
        f'<span class="tag field">{esc(kind)}</span>'
        + (DUP_KEY_BADGE if r["session_id"] in dupkeys else "")
        + tag_badge
    )
    last_turn = max((ev.get("turn", 0) for ev in (sraw.get("event_sequence") or [])), default=10**9)
    card_ctr[0] += 1
    evo = state_evolution_html(sraw, r, last_turn, cid=f"evo-s{card_ctr[0]}")
    ana = (
        f"<b>session_error_type:</b> {esc(r.get('session_error_type', ''))}<br>"
        f"turn 级命中 {r['turn_match']}/{r['turn_total']}；"
        f"turn 级逐轮明细见上方「turn 级错误」对应类别。"
    )
    last = max((t for t in r["turn_detail"] if t.get("llm_raw")), key=lambda t: t["turn"], default=None)
    return (
        f"<details class='sample' data-stag='{esc(tag)}'><summary>{head}</summary>"
        f"<div class='sample-body'>"
        f"<div class='block full'><h4>状态演进（点击每轮查看截止该轮的累积状态 · 红=与GT不一致 · 蓝=本轮模型改动）</h4>{evo}</div>"
        f"<div class='block'><h4>意图状态机对照</h4>{_render_session_status_table(r)}</div>"
        f"<div class='block'><h4>错误分析</h4><div class='ana'>{ana}{esc(dist_txt)}</div></div>"
        f"<div class='block full'><h4>最终结果（每意图 7 字段，GT vs 预测）</h4>{_render_field_accuracy_section(r)}</div>"
        f"<div class='block full'><h4>数据集原始信息</h4>{raw_data_html(sraw, users, profiles)}</div>"
        f"<div class='block full'><h4>LLM 原始输出（最后一轮）</h4>{llm_raw_html(last)}</div>"
        f"</div></details>"
    )


def _render_category_section(
    sec_id: str,
    title: str,
    color: str,
    desc: str,
    unit_label: str,
    entries: list[dict[str, Any]],
    cards_html: str,
) -> str:
    """渲染带展开/收起按钮的错误分类折叠区块。"""
    esc = html.escape
    tpl_stat = collections.Counter(x["session"]["template"] for x in entries)
    tpl_txt = "，".join(f"{t}×{n}" for t, n in tpl_stat.most_common())
    return (
        f"<section class='cat' id='{sec_id}'>"
        f"<div class='cat-head' style='background:{color}'>"
        f"<h2>总结：{esc(title)}<span class='cnt'>{len(entries)} {unit_label}</span></h2>"
        f"<p class='desc'>描述：{esc(desc)}</p>"
        f"<div class='tpls'>涉及模板：{esc(tpl_txt)}</div></div>"
        f"<div class='cat-tools'><button onclick=\"toggleAll('{sec_id}',true)\">全部展开</button> "
        f"<button onclick=\"toggleAll('{sec_id}',false)\">全部收起</button></div>"
        f"<div class='cat-body'>{cards_html}</div></section>"
    )


def _render_noise_record_table(
    sraw: dict[str, Any], recs: list[dict[str, Any]], reasons: list[str]
) -> str:
    """渲染噪声样本中的 Record 错选 vs 应取对照表。"""
    if not sraw or not recs:
        return ""
    esc = html.escape
    gt_wrong_ids = set()
    for su in sraw.get("slot_updates") or []:
        for tu in su.get("turn_updates") or []:
            for _p, leaf in _walk_gt_leaves(tu.get("params") or {}):
                if leaf.get("source_type") == "Record" and leaf.get("source_ref"):
                    gt_wrong_ids.add(str(leaf["source_ref"]))
    reason_ids = set(re.findall(r"R-[A-Za-z0-9\-]+", " ".join(reasons)))
    correct_ids = reason_ids - gt_wrong_ids
    rows = []
    for i, rec in enumerate(recs):
        f = _rec_flat(rec)
        rid = str(rec.get("record_id"))
        is_wrong = rid in gt_wrong_ids
        is_correct = rid in correct_ids
        cls = ' class="rec-pred"' if is_wrong else (' class="rec-gt"' if is_correct else "")
        gt_mark = "<b>✗ GT 错选</b>" if is_wrong else ""
        ok_mark = "<b>✓ 应取</b>" if is_correct else ""
        rows.append(
            f"<tr{cls}><td>{i}</td><td>{esc(rid)}</td>"
            f"<td>{esc(str(f['application_name']))}</td><td>{esc(str(f['service_name']))}</td>"
            f"<td>{esc(str(f['resolution']))}</td><td>{esc(str(f['rtt']))}</td>"
            f"<td>{esc(str(f['completed_at']))}</td>"
            f"<td class='rec-mark-gt'>{ok_mark}</td><td class='rec-mark-pred'>{gt_mark}</td></tr>"
        )
    return (
        "<div class='block full'><h4>Record 选取对照（GT 错选 vs 应取，completed_at 降序 = 模型所见顺序）</h4>"
        '<div class="rec-cmp">'
        '<table class="diff rec-table"><thead><tr><th>#</th><th>record_id</th>'
        "<th>应用</th><th>业务</th><th>清晰度</th><th>时延</th><th>完成时间</th>"
        "<th>应取</th><th>GT 选择</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table></div></div>'
    )


def _render_noise_card(
    sid: str,
    noise_map: dict[str, list[str]],
    sessions: dict[str, dict[str, Any]],
    noise_in_eval: list[dict[str, Any]],
    users: dict[str, list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
) -> str:
    """渲染单张数据集噪声样本卡片。"""
    from case_format import flatten_params as _fp

    esc = html.escape
    reasons = noise_map.get(sid, [])
    sraw = sessions.get(sid) or {}
    r = next((x for x in noise_in_eval if x["session_id"] == sid), None)
    tpl = sraw.get("session_meta", {}).get("template_id", "?")
    status = (
        "本次评估已剔除，未参与打分"
        if r is None
        else "本次参与了评估：判定" + ("正确（巧合匹配）" if r["session_match"] else "错误（被噪声 GT 坑）")
    )
    conv = conversation_html(
        sraw, 10**9, (r or {}).get("turn_outputs") or (r or {}).get("turn_detail")
    )
    gt_rows = ""
    for it in sraw.get("intents") or []:
        if it.get("status") != "resolved":
            continue
        flat = _fp(it.get("params") or {})
        if not flat.get("application_name"):
            continue
        gt_rows += "<tr>" + "".join(f"<td>{esc(str(flat.get(s)))}</td>" for s in EVAL_SLOTS) + "</tr>"
    gt_table = (
        '<table class="diff"><thead><tr>'
        + "".join(f"<th>{FIELD_LABEL.get(s, s)}</th>" for s in EVAL_SLOTS)
        + f"</tr></thead><tbody>{gt_rows}</tbody></table>"
    )
    pred_txt = (
        "<h4>模型预测（仅供参考，GT 本身有错）</h4><pre class='raw'>"
        + esc(json.dumps(r["pred_intents"], ensure_ascii=False, indent=1))
        + "</pre>"
        if r is not None
        else ""
    )
    rs_html = "".join(f"<li>{esc(x)}</li>" for x in reasons)
    recs = users.get(sraw.get("user_id", "")) or []
    rec_block = _render_noise_record_table(sraw, recs, reasons)
    return (
        f"<details class='sample'><summary>"
        f"<span class='sid'>{sid}</span><span class='tag tpl'>{tpl}</span>"
        f"<span class='tag noise'>噪声</span></summary>"
        f"<div class='sample-body'>"
        f"<div class='block full'><h4>噪声原因</h4><div class='ana noise-ana'><ul>{rs_html}</ul>"
        f"<b>{esc(status)}</b></div></div>"
        f"<div class='block'><h4>对话内容</h4>{conv}</div>"
        f"<div class='block'><h4>GT 标注（有错）</h4>{gt_table}</div>"
        f"{rec_block}"
        f"{pred_txt}"
        f"<div class='block full'><h4>数据集原始信息</h4>{raw_data_html(sraw, users, profiles)}</div>"
        f"</div></details>"
    )


def _render_noise_section(
    noise_map: dict[str, list[str]],
    sessions: dict[str, dict[str, Any]],
    noise_in_eval: list[dict[str, Any]],
    users: dict[str, list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
) -> str:
    """渲染数据集标注噪声展示区块。"""
    if not noise_map:
        return ""
    cards = "".join(
        _render_noise_card(sid, noise_map, sessions, noise_in_eval, users, profiles)
        for sid in sorted(noise_map)
    )
    return (
        "<h2 class='part'>三、数据集噪声样本（不参与评估，单拎展示）</h2>"
        "<section class='cat' id='noise'>"
        "<div class='cat-head' style='background:#95a5a6'>"
        f"<h2>总结：数据集标注噪声<span class='cnt'>{len(noise_map)} 个 session</span></h2>"
        "<p class='desc'>描述：find_noise.py 客观规则检出的 GT 标注错误（时间内部矛盾/明说时长时段不符/"
        "画质词映射矛盾）。这些样本 GT 本身有错，模型答对也会被误判，已从评估中剔除，"
        "此处仅展示供数据集修订参考。清单文件 data/_noise_exclude.json。</p></div>"
        f"<div class='cat-body'>{cards}</div></section>"
    )


def _render_session_category_sections(
    sess_set_mismatch: list[dict[str, Any]],
    sess_field_wrong: list[dict[str, Any]],
    dupkeys: set[str],
    users: dict[str, list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
    card_ctr: list[int],
) -> tuple[list[str], str]:
    """渲染 session 视图下的意图集合不一致与字段错误两个分类区块。"""
    sess_sections = []
    nav_sess = ""
    if sess_set_mismatch:
        sess_sections.append(
            _render_category_section(
                "sess_set",
                CAT_META["intent_set_mismatch"]["title"],
                CAT_META["intent_set_mismatch"]["color"],
                CAT_META["intent_set_mismatch"]["desc"],
                "个 session",
                sess_set_mismatch,
                "".join(
                    _render_session_drilldown_cards(e, "意图集合不一致", dupkeys, users, profiles, card_ctr)
                    for e in sess_set_mismatch
                ),
            )
        )
        nav_sess += (
            f"<a href='#sess_set' style='background:{CAT_META['intent_set_mismatch']['color']}'>"
            f"意图集合不一致 <span class='n'>{len(sess_set_mismatch)}</span></a>"
        )
    if sess_field_wrong:
        sess_sections.append(
            _render_category_section(
                "sess_field",
                "字段错误（session 级，意图集合一致）",
                "#5d6d7e",
                "意图集合与 GT 全等，但至少一个意图的字段值不全等（session 级比 turn 级更严格，任一轮累积错即传导到最终态）。逐轮出错位置见上方 turn 级分类。",
                "个 session",
                sess_field_wrong,
                "".join(
                    _render_session_drilldown_cards(e, "字段错误", dupkeys, users, profiles, card_ctr)
                    for e in sess_field_wrong
                ),
            )
        )
        nav_sess += f"<a href='#sess_field' style='background:#5d6d7e'>session字段错误 <span class='n'>{len(sess_field_wrong)}</span></a>"
    return sess_sections, nav_sess


def render_report_page(
    eval_path: str,
    dataset_dir: str,
    metrics: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    users: dict[str, list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
    src_maps: dict[str, dict[str, Any]],
    noise_map: dict[str, list[str]],
    noise_in_eval: list[dict[str, Any]],
) -> str:
    """组装错误分析报告各子视图并渲染完整 HTML 页面。"""
    esc = html.escape
    cases = metrics["cases"]
    dupkeys = metrics["dupkeys"]
    card_ctr = [0]

    summary_banner_html = _render_summary_banner(eval_path, dataset_dir, metrics, noise_map)
    turn_dist_panel, tag_panel, sess_dist_panel, sess_tag_panel = _render_error_taxonomy_section(metrics)
    turn_tpl_panel = _render_category_breakdown_table(
        metrics["by_tpl"], metrics["by_tpl_tm"], metrics["by_tpl_tt"], "按模板 turn 正确率"
    )
    sess_tpl_panel = _render_category_breakdown_table(
        metrics["by_tpl"], metrics["by_tpl_m"], metrics["by_tpl"], "按模板 session 正确率"
    )

    turn_sections = [
        _render_category_section(
            c, CAT_META[c]["title"], CAT_META[c]["color"], CAT_META[c]["desc"], "个样本", cases[c],
            "".join(
                _render_turn_sample_card(item, c, dupkeys, src_maps, users, profiles, card_ctr)
                for item in cases[c]
            ),
        )
        for c in TURN_CATS
        if cases.get(c)
    ]
    nav_turn = "".join(
        f"<a href='#{c}' style='background:{CAT_META[c]['color']}'>"
        f"{esc(CAT_META[c]['title'])} <span class='n'>{len(cases[c])}</span></a>"
        for c in TURN_CATS
        if cases.get(c)
    )
    sess_sections, nav_sess = _render_session_category_sections(
        metrics["sess_set_mismatch"], metrics["sess_field_wrong"], dupkeys, users, profiles, card_ctr
    )

    turn_view_html = (
        f'<div id="view-turn">\n{turn_dist_panel}\n{tag_panel}\n{turn_tpl_panel}\n'
        f'<div class="nav">{nav_turn}</div>\n{"".join(turn_sections)}\n</div>'
    )
    sess_view_html = (
        f'<div id="view-sess" style="display:none">\n{sess_dist_panel}\n{sess_tag_panel}\n{sess_tpl_panel}\n'
        f'<div class="nav">{nav_sess}</div>\n{"".join(sess_sections)}\n</div>'
    )
    noise_section_html = _render_noise_section(noise_map, sessions, noise_in_eval, users, profiles)
    return format_report_html_document(
        eval_path, summary_banner_html, turn_view_html, sess_view_html, noise_section_html
    )
