"""report_templates.py — 错误分析报告 HTML/CSS/JS 模板与组件渲染。

职责:
  1. 维护报告页面样式 (_CSS) 与交互脚本 (_JS)
  2. 渲染 Record 选取对照表、逐轮状态演进面板、对话气泡流与 GT vs 预测差异表
  3. 组装完整单文件 HTML 错误分析报告 (render_report_page)
"""
from __future__ import annotations

import collections
import html
import json
import os
import re
from typing import Any

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

_CSS = """
:root { --bg:#f0f2f5; --card:#fff; --ink:#1f2329; --sub:#646a73; --line:#e5e6eb; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei","PingFang SC",sans-serif; margin:0; background:var(--bg); color:var(--ink); }
.wrap { max-width: 1180px; margin: 0 auto; padding: 24px 20px 80px; }
.hero { background: linear-gradient(135deg,#2b3a55 0%,#40629b 100%); color:#fff;
        border-radius:14px; padding:26px 30px; box-shadow:0 4px 16px rgba(30,50,90,.18); }
.hero h1 { margin:0 0 6px; font-size:24px; }
.hero .meta { opacity:.85; font-size:13px; }
.hero code { background:rgba(255,255,255,.15); padding:1px 7px; border-radius:5px; font-size:12px; }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin:18px 0; }
.stat { background:var(--card); border-radius:12px; padding:16px 18px; box-shadow:0 1px 4px rgba(0,0,0,.06); }
.stat .num { font-size:26px; font-weight:700; }
.stat .lbl { color:var(--sub); font-size:13px; margin-top:2px; }
.stat.red .num { color:#c0392b; }
.panel { background:var(--card); border-radius:12px; padding:18px 22px; margin-top:16px;
         box-shadow:0 1px 4px rgba(0,0,0,.06); }
.panel h3 { margin:0 0 10px; font-size:16px; }
table.tpl { border-collapse:collapse; font-size:13px; width:100%; }
table.tpl th, table.tpl td { border-bottom:1px solid var(--line); padding:6px 10px; text-align:left; }
table.tpl th { color:var(--sub); font-weight:600; }
.bar { height:8px; border-radius:4px; background:#e9ecef; overflow:hidden; min-width:90px; }
.bar i { display:block; height:100%; border-radius:4px; background:#40629b; }
.nav { position:sticky; top:0; z-index:9; background:rgba(240,242,245,.92); backdrop-filter:blur(6px);
       padding:10px 0; margin:14px -4px 0; display:flex; flex-wrap:wrap; gap:8px; }
.nav a { text-decoration:none; font-size:13px; padding:5px 12px; border-radius:999px;
         color:#fff; box-shadow:0 1px 3px rgba(0,0,0,.15); }
.nav a .n { opacity:.85; font-weight:700; }
.nav-grp { align-self:center; font-size:13px; font-weight:700; color:var(--sub); margin:0 2px 0 6px; }
h2.part { margin:26px 0 4px; font-size:17px; color:#2b3a55; }
.rec-cmp { background:#fdf2f0; border:1px solid #f5c6c0; border-radius:10px; padding:10px 14px;
           font-size:13px; line-height:1.9; }
.rec-cmp table { margin-top:8px; background:#fff; }
.rec-table { width:100%; font-size:12px; }
tr.rec-gt td { background:#e8f6ec; }
tr.rec-pred td { background:#fdecea; }
.rec-mark-gt { color:#1e8449; }
.rec-mark-pred { color:#c0392b; }
.rec-mark-pred .en { display:block; font-size:10px; color:var(--sub); }
/* 二级标签过滤 */
.tagfilter { margin-top:12px; padding-top:10px; border-top:1px dashed var(--line); font-size:13px; }
.tagfilter-btn { cursor:pointer; margin:3px 4px 0 0; padding:4px 12px; border-radius:999px;
                 border:1.5px solid #aab; background:#fff; font-size:12.5px; }
.tagfilter-btn.active { background:#2b3a55; border-color:#2b3a55; color:#fff !important; }
.tagfilter-hint { color:var(--sub); font-size:12px; margin-left:8px; }
/* 数据集原始信息 */
details.rawdata { margin-top:4px; }
details.rawdata > summary { cursor:pointer; color:#40629b; font-size:12.5px; }
details.rawdata h5 { margin:12px 0 4px; font-size:13px; color:#2b3a55; }
table.gt-src { width:100%; font-size:12px; }
table.meta th { width:130px; color:var(--sub); }
.src-record { color:#c0392b; font-weight:600; }
.src-memory { color:#8e44ad; }
.src-turn { color:#1e8449; }
.src-context { color:#2980b9; }
.src-derived { color:#6c3483; font-weight:600; }
.derived-badge { display:inline-block; font-size:10.5px; background:#f4ecf7; color:#6c3483;
                 border:1px solid #d7bde2; border-radius:4px; padding:0 5px; margin-left:4px;
                 vertical-align:middle; cursor:help; }
/* session 最终结果: 每意图 7 字段 */
.intent-box { border:1px solid var(--line); border-radius:10px; margin:10px 0; overflow:hidden; }
.intent-box .intent-head { padding:8px 14px; font-size:13.5px; background:#f7f8fa;
                           border-bottom:1px solid var(--line); }
.intent-box.intent-ok .intent-head { background:#e8f6ec; }
.intent-box.intent-bad .intent-head { background:#fdecea; }
.intent-box .intent-rel { font-size:12px; color:var(--sub); margin-left:8px; }
.intent-box table { border:none; }
/* 状态演进模块 */
.evo { display:grid; grid-template-columns:1.15fr 1fr; gap:14px; align-items:start; }
@media (max-width:900px){ .evo { grid-template-columns:1fr; } }
.evo-conv { display:flex; flex-direction:column; gap:8px; max-height:420px; overflow:auto; padding-right:4px; }
.evo-turn { cursor:pointer; border:1px solid transparent; border-radius:8px; }
.evo-turn:hover { background:#f0f4f9; }
.evo-turn.current { background:#fff8e1; outline:1px solid #f0dfa0; }
.evo-state { position:sticky; top:64px; max-height:480px; overflow:auto; background:#fbfcfe;
             border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
.evo-pane-title { font-size:12.5px; font-weight:700; color:#2b3a55; margin-bottom:8px; }
.evo-pane-title .legend { float:right; font-weight:400; font-size:11px; color:var(--sub); }
.legend .lg { display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 4px 0 10px; }
.legend .lg.bad { background:#f5b7b1; }
.legend .lg.chg { background:#aed6f1; }
.evo-intent { margin-bottom:10px; font-size:12.5px; }
table.diff.evo-tbl { font-size:11.5px; margin-top:4px; }
table.diff tr.chg td { background:#e3f0ff; }
/* turn/session 视图切换 */
.viewbar { display:flex; gap:12px; margin:20px 0 4px; position:sticky; top:0; z-index:10;
           background:rgba(240,242,245,.95); backdrop-filter:blur(6px); padding:10px 0; }
.viewbtn { flex:1; max-width:340px; cursor:pointer; border:2px solid #c5ccd6; background:#fff;
           border-radius:12px; padding:12px 18px; font-size:15px; font-weight:700; color:var(--sub);
           text-align:left; box-shadow:0 1px 4px rgba(0,0,0,.06); }
.viewbtn small { display:block; font-size:12px; font-weight:400; margin-top:3px; }
.viewbtn.active { border-color:#2b3a55; background:#2b3a55; color:#fff; }
.viewbtn.active small { color:rgba(255,255,255,.8); }
.cat { margin-top:26px; border-radius:14px; background:var(--card); overflow:hidden;
       box-shadow:0 2px 8px rgba(0,0,0,.07); }
.cat-head { padding:16px 22px; color:#fff; }
.cat-head h2 { margin:0; font-size:18px; }
.cat-head h2 .cnt { font-size:14px; opacity:.85; margin-left:8px; }
.cat-head .desc { margin:8px 0 0; font-size:13px; line-height:1.7; opacity:.94; }
.cat-head .tpls { margin-top:6px; font-size:12px; opacity:.8; }
.cat-body { padding:12px 18px 18px; }
.cat-tools { padding:10px 22px 0; }
.cat-tools button { font-size:12px; border:1px solid var(--line); background:#f7f8fa; border-radius:6px;
                    padding:3px 10px; cursor:pointer; color:var(--sub); }
details.sample { border:1px solid var(--line); border-radius:10px; margin:10px 0; overflow:hidden; }
details.sample[open] { box-shadow:0 2px 8px rgba(0,0,0,.06); }
details.sample summary { cursor:pointer; padding:10px 14px; background:#fafbfc; font-size:13.5px;
                         font-weight:600; list-style:none; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
details.sample summary::-webkit-details-marker { display:none; }
details.sample summary::before { content:"▸"; color:var(--sub); transition:.15s; }
details.sample[open] summary::before { content:"▾"; }
details.sample summary:hover { background:#f0f4f9; }
.sid { color:#40629b; }
.tag { font-size:11px; border-radius:4px; padding:1px 7px; font-weight:600; }
.tag.tpl { background:#e8eef9; color:#2f5aa8; }
.tag.turn { background:#eaf4ee; color:#1e7e46; }
.tag.field { background:#fdecea; color:#a93226; }
.tag.noise { background:#7f8c8d; color:#fff; }
.noise-ana ul { margin:4px 0 8px; padding-left:20px; }
.sample-body { padding:14px 16px; display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:900px){ .sample-body { grid-template-columns:1fr; } }
.block h4 { margin:0 0 8px; font-size:13px; color:var(--sub); text-transform:uppercase; letter-spacing:.5px; }
.block.full { grid-column:1 / -1; }
.conv { display:flex; flex-direction:column; gap:8px; max-height:340px; overflow:auto; padding-right:4px; }
.msg-row { display:flex; gap:8px; padding:6px 8px; border-radius:8px; }
.msg-row.current { background:#fff8e1; outline:1px solid #f0dfa0; }
.msg-turn { flex:0 0 44px; color:var(--sub); font-size:12px; padding-top:8px; }
.msg-bubbles { display:flex; flex-direction:column; gap:5px; flex:1; }
.bubble { padding:7px 11px; border-radius:10px; font-size:13px; line-height:1.6; }
.bubble.user { background:#e8eef9; align-self:flex-start; }
.bubble.agent { background:#f2f3f5; color:#454a52; align-self:flex-start; }
details.model-out { align-self:stretch; }
details.model-out > summary { cursor:pointer; font-size:11.5px; color:#8a5a00; }
details.model-out > summary:hover { color:#b37400; }
details.model-out pre.raw { max-height:260px; margin:4px 0 0; font-size:11.5px; }
table.diff { border-collapse:collapse; font-size:12.5px; width:100%; }
table.diff th, table.diff td { border:1px solid var(--line); padding:5px 9px; text-align:left; }
table.diff thead th { background:#f7f8fa; color:var(--sub); }
table.diff .en { display:block; color:#9aa0a6; font-size:11px; }
table.diff tr.bad td { background:#fdecea; color:#a93226; font-weight:600; }
.ana { background:#fffbeb; border:1px solid #f0dfa0; border-radius:10px; padding:10px 14px;
       font-size:13px; line-height:1.8; }
pre.raw { background:#1e2430; color:#d7e0ea; border-radius:10px; padding:14px 16px; font-size:12.5px;
          line-height:1.65; max-height:380px; overflow:auto; white-space:pre-wrap; word-break:break-all;
          font-family:Consolas,"Courier New",monospace; }
.no-raw { color:#9aa0a6; font-size:12.5px; font-style:italic; }
#top-btn { position:fixed; right:26px; bottom:26px; border:none; border-radius:50%; width:44px; height:44px;
           background:#40629b; color:#fff; font-size:18px; cursor:pointer; box-shadow:0 3px 10px rgba(0,0,0,.25); }
"""

_JS = """
function switchView(v) {
  document.getElementById('view-turn').style.display = v === 'turn' ? '' : 'none';
  document.getElementById('view-sess').style.display = v === 'sess' ? '' : 'none';
  document.getElementById('vb-turn').classList.toggle('active', v === 'turn');
  document.getElementById('vb-sess').classList.toggle('active', v === 'sess');
  window.scrollTo({top: document.querySelector('.viewbar').offsetTop - 8, behavior: 'smooth'});
}
function evoShow(cid, t) {
  var root = document.getElementById(cid);
  if (!root) return;
  root.querySelectorAll('.evo-pane').forEach(function(p){
    p.style.display = p.getAttribute('data-turn') == t ? '' : 'none';
  });
  root.querySelectorAll('.evo-turn').forEach(function(r){
    r.classList.toggle('current', r.getAttribute('data-turn') == t);
  });
}
function toggleAll(cat, open) {
  document.querySelectorAll('#'+cat+' details.sample').forEach(d => d.open = open);
}
function filterTag(tag, viewId, attr) {
  var root = document.getElementById(viewId);
  root.querySelectorAll('details.sample['+attr+']').forEach(function(el){
    el.style.display = (!tag || el.getAttribute(attr) === tag) ? '' : 'none';
  });
  root.querySelectorAll('section.cat').forEach(function(sec){
    var tagged = sec.querySelectorAll('details.sample['+attr+']');
    if (!tagged.length) return;
    var any = Array.prototype.some.call(tagged, function(el){ return el.style.display !== 'none'; });
    var untagged = sec.querySelectorAll('details.sample:not(['+attr+'])').length;
    sec.style.display = (any || untagged > 0) ? '' : 'none';
  });
  root.querySelectorAll('.tagfilter-btn').forEach(function(b){
    b.classList.toggle('active', b.getAttribute('data-t') === tag);
  });
}
window.addEventListener('scroll', () => {
  document.getElementById('top-btn').style.display = window.scrollY > 500 ? 'block' : 'none';
});
"""

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
    """渲染完整的错误样本分析 HTML 页面。"""
    esc = html.escape
    cases = metrics["cases"]
    tag_counter = metrics["tag_counter"]
    tag_x_cat = metrics["tag_x_cat"]
    sess_tag_counter = metrics["sess_tag_counter"]
    sess_set_mismatch = metrics["sess_set_mismatch"]
    sess_field_wrong = metrics["sess_field_wrong"]
    n_sess = metrics["n_sess"]
    n_sess_match = metrics["n_sess_match"]
    n_turn = metrics["n_turn"]
    n_turn_match = metrics["n_turn_match"]
    dupkeys = metrics["dupkeys"]
    by_tpl = metrics["by_tpl"]
    by_tpl_m = metrics["by_tpl_m"]
    by_tpl_tm = metrics["by_tpl_tm"]
    by_tpl_tt = metrics["by_tpl_tt"]

    rate_s = n_sess_match / max(n_sess, 1)
    rate_t = n_turn_match / max(n_turn, 1)
    n_err_turn = sum(len(v) for v in cases.values())

    _dup_badge = (
        '<span class="tag" style="background:#6c3483;color:#fff" '
        'title="GT 含同 app+service 的多条意图，session 级 gt/pred 按意图 key(应用/服务) '
        "建 dict 会互相覆盖(塌缩)，集合比对与 7 字段比对只看塌缩后的一条——"
        'session 判定与最终结果对照可能失真，判读时须结合逐轮明细与数据集原始信息">同key双意图·塌缩</span>'
    )

    card_ctr = [0]

    def sample_card(c: dict[str, Any], cat: str) -> str:
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
            + (_dup_badge if r["session_id"] in dupkeys else "")
            + tag_badge
            + near_badge
            + "".join(f'<span class="tag field">{FIELD_LABEL.get(f, f)}</span>' for f in diffs)
        )
        card_ctr[0] += 1
        evo = state_evolution_html(sraw, r, d["turn"], cid=f"evo-t{card_ctr[0]}")
        table = diff_table_html(
            d["gt"],
            d["pred"],
            diffs,
            src_maps[r["session_id"]],
            d["intent_key"],
            d["turn"],
            derived=d.get("gt_derived"),
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

    def session_final_html(r: dict[str, Any]) -> str:
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

    def session_status_html(r: dict[str, Any]) -> str:
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

    def session_card(e: dict[str, Any], kind: str) -> str:
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
            + (_dup_badge if r["session_id"] in dupkeys else "")
            + tag_badge
        )
        last_turn = max(
            (ev.get("turn", 0) for ev in (sraw.get("event_sequence") or [])),
            default=10**9,
        )
        card_ctr[0] += 1
        evo = state_evolution_html(sraw, r, last_turn, cid=f"evo-s{card_ctr[0]}")
        ana = (
            f"<b>session_error_type:</b> {esc(r.get('session_error_type', ''))}<br>"
            f"turn 级命中 {r['turn_match']}/{r['turn_total']}；"
            f"turn 级逐轮明细见上方「turn 级错误」对应类别。"
        )
        last = max(
            (t for t in r["turn_detail"] if t.get("llm_raw")),
            key=lambda t: t["turn"],
            default=None,
        )
        return (
            f"<details class='sample' data-stag='{esc(tag)}'><summary>{head}</summary>"
            f"<div class='sample-body'>"
            f"<div class='block full'><h4>状态演进（点击每轮查看截止该轮的累积状态 · 红=与GT不一致 · 蓝=本轮模型改动）</h4>{evo}</div>"
            f"<div class='block'><h4>意图状态机对照</h4>{session_status_html(r)}</div>"
            f"<div class='block'><h4>错误分析</h4><div class='ana'>{ana}{esc(dist_txt)}</div></div>"
            f"<div class='block full'><h4>最终结果（每意图 7 字段，GT vs 预测）</h4>{session_final_html(r)}</div>"
            f"<div class='block full'><h4>数据集原始信息</h4>{raw_data_html(sraw, users, profiles)}</div>"
            f"<div class='block full'><h4>LLM 原始输出（最后一轮）</h4>{llm_raw_html(last)}</div>"
            f"</div></details>"
        )

    def cat_section(cat: str, clist: list[dict[str, Any]]) -> str:
        meta = CAT_META[cat]
        tpl_stat = collections.Counter(c["session"]["template"] for c in clist)
        tpl_txt = "，".join(f"{t}×{n}" for t, n in tpl_stat.most_common())
        body = "".join(sample_card(c, cat) for c in clist)
        return (
            f"<section class='cat' id='{cat}'>"
            f"<div class='cat-head' style='background:{meta['color']}'>"
            f"<h2>总结：{esc(meta['title'])}<span class='cnt'>{len(clist)} 个样本</span></h2>"
            f"<p class='desc'>描述：{esc(meta['desc'])}</p>"
            f"<div class='tpls'>涉及模板：{esc(tpl_txt)}</div></div>"
            f"<div class='cat-tools'><button onclick=\"toggleAll('{cat}',true)\">全部展开</button> "
            f"<button onclick=\"toggleAll('{cat}',false)\">全部收起</button></div>"
            f"<div class='cat-body'>{body}</div></section>"
        )

    def sess_section(
        sec_id: str, title: str, color: str, desc: str, entries: list[dict[str, Any]], kind: str
    ) -> str:
        tpl_stat = collections.Counter(e["session"]["template"] for e in entries)
        tpl_txt = "，".join(f"{t}×{n}" for t, n in tpl_stat.most_common())
        body = "".join(session_card(e, kind) for e in entries)
        return (
            f"<section class='cat' id='{sec_id}'>"
            f"<div class='cat-head' style='background:{color}'>"
            f"<h2>总结：{esc(title)}<span class='cnt'>{len(entries)} 个 session</span></h2>"
            f"<p class='desc'>描述：{esc(desc)}</p>"
            f"<div class='tpls'>涉及模板：{esc(tpl_txt)}</div></div>"
            f"<div class='cat-tools'><button onclick=\"toggleAll('{sec_id}',true)\">全部展开</button> "
            f"<button onclick=\"toggleAll('{sec_id}',false)\">全部收起</button></div>"
            f"<div class='cat-body'>{body}</div></section>"
        )

    turn_sections = [cat_section(c, cases[c]) for c in TURN_CATS if cases.get(c)]
    sess_sections = []
    if sess_set_mismatch:
        sess_sections.append(
            sess_section(
                "sess_set",
                CAT_META["intent_set_mismatch"]["title"],
                CAT_META["intent_set_mismatch"]["color"],
                CAT_META["intent_set_mismatch"]["desc"],
                sess_set_mismatch,
                "意图集合不一致",
            )
        )
    if sess_field_wrong:
        sess_sections.append(
            sess_section(
                "sess_field",
                "字段错误（session 级，意图集合一致）",
                "#5d6d7e",
                "意图集合与 GT 全等，但至少一个意图的字段值不全等（session 级比 turn 级更严格，"
                "任一轮累积错即传导到最终态）。逐轮出错位置见上方 turn 级分类。",
                sess_field_wrong,
                "字段错误",
            )
        )

    nav_turn = "".join(
        f"<a href='#{c}' style='background:{CAT_META[c]['color']}'>"
        f"{esc(CAT_META[c]['title'])} <span class='n'>{len(cases[c])}</span></a>"
        for c in TURN_CATS
        if cases.get(c)
    )
    nav_sess = ""
    if sess_set_mismatch:
        nav_sess += (
            f"<a href='#sess_set' style='background:{CAT_META['intent_set_mismatch']['color']}'>"
            f"意图集合不一致 <span class='n'>{len(sess_set_mismatch)}</span></a>"
        )
    if sess_field_wrong:
        nav_sess += (
            f"<a href='#sess_field' style='background:#5d6d7e'>"
            f"session字段错误 <span class='n'>{len(sess_field_wrong)}</span></a>"
        )

    def noise_card(sid: str) -> str:
        reasons = noise_map.get(sid, [])
        sraw = sessions.get(sid) or {}
        r = next((x for x in noise_in_eval if x["session_id"] == sid), None)
        tpl = sraw.get("session_meta", {}).get("template_id", "?")
        status = (
            "本次评估已剔除，未参与打分"
            if r is None
            else (
                "本次参与了评估：判定"
                + ("正确（巧合匹配）" if r["session_match"] else "错误（被噪声 GT 坑）")
            )
        )
        conv = conversation_html(
            sraw, 10**9, (r or {}).get("turn_outputs") or (r or {}).get("turn_detail")
        )
        gt_rows = ""
        if sraw:
            from case_format import flatten_params as _fp
            for it in sraw.get("intents") or []:
                if it.get("status") != "resolved":
                    continue
                flat = _fp(it.get("params") or {})
                if not flat.get("application_name"):
                    continue
                gt_rows += (
                    "<tr>"
                    + "".join(f"<td>{esc(str(flat.get(s)))}</td>" for s in EVAL_SLOTS)
                    + "</tr>"
                )
        gt_table = (
            '<table class="diff"><thead><tr>'
            + "".join(f"<th>{FIELD_LABEL.get(s, s)}</th>" for s in EVAL_SLOTS)
            + f"</tr></thead><tbody>{gt_rows}</tbody></table>"
        )
        pred_txt = ""
        if r is not None:
            pred_txt = (
                "<h4>模型预测（仅供参考，GT 本身有错）</h4><pre class='raw'>"
                + esc(json.dumps(r["pred_intents"], ensure_ascii=False, indent=1))
                + "</pre>"
            )
        rs_html = "".join(f"<li>{esc(x)}</li>" for x in reasons)
        rec_block = ""
        recs = users.get(sraw.get("user_id", "")) or []
        if sraw and recs:
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
                cls = (
                    ' class="rec-pred"'
                    if is_wrong
                    else (' class="rec-gt"' if is_correct else "")
                )
                gt_mark = "<b>✗ GT 错选</b>" if is_wrong else ""
                ok_mark = "<b>✓ 应取</b>" if is_correct else ""
                rows.append(
                    f"<tr{cls}><td>{i}</td><td>{esc(rid)}</td>"
                    f"<td>{esc(str(f['application_name']))}</td><td>{esc(str(f['service_name']))}</td>"
                    f"<td>{esc(str(f['resolution']))}</td><td>{esc(str(f['rtt']))}</td>"
                    f"<td>{esc(str(f['completed_at']))}</td>"
                    f"<td class='rec-mark-gt'>{ok_mark}</td><td class='rec-mark-pred'>{gt_mark}</td></tr>"
                )
            rec_block = (
                "<div class='block full'><h4>Record 选取对照（GT 错选 vs 应取，completed_at 降序 = 模型所见顺序）</h4>"
                '<div class="rec-cmp">'
                '<table class="diff rec-table"><thead><tr><th>#</th><th>record_id</th>'
                "<th>应用</th><th>业务</th><th>清晰度</th><th>时延</th><th>完成时间</th>"
                "<th>应取</th><th>GT 选择</th></tr></thead>"
                f'<tbody>{"".join(rows)}</tbody></table></div></div>'
            )
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

    noise_section = ""
    if noise_map:
        cards = "".join(noise_card(sid) for sid in sorted(noise_map))
        noise_section = (
            "<h2 class='part'>三、数据集噪声样本（不参与评估，单拎展示）</h2>"
            "<section class='cat' id='noise'>"
            "<div class='cat-head' style='background:#95a5a6'>"
            f"<h2>总结：数据集标注噪声<span class='cnt'>{len(noise_map)} 个 session</span></h2>"
            "<p class='desc'>描述：find_noise.py 客观规则检出的 GT 标注错误（时间内部矛盾/明说时长时段不符/"
            "画质词映射矛盾）。这些样本 GT 本身有错，模型答对也会被误判，已从评估中剔除，"
            "此处仅展示供数据集修订参考。清单文件 data/_noise_exclude.json。</p></div>"
            f"<div class='cat-body'>{cards}</div></section>"
        )

    tpl_rows_turn = "".join(
        f"<tr><td><b>{t}</b></td><td>{by_tpl_tm[t]} / {by_tpl_tt[t]}</td>"
        f"<td><div class='bar'><i style='width:{by_tpl_tm[t]/max(by_tpl_tt[t],1)*100:.0f}%'></i></div></td>"
        f"<td>{by_tpl_tm[t]/max(by_tpl_tt[t],1):.1%}</td></tr>"
        for t in sorted(by_tpl)
    )
    tpl_rows_sess = "".join(
        f"<tr><td><b>{t}</b></td><td>{by_tpl_m[t]} / {by_tpl[t]}</td>"
        f"<td><div class='bar'><i style='width:{by_tpl_m[t]/by_tpl[t]*100:.0f}%'></i></div></td>"
        f"<td>{by_tpl_m[t]/by_tpl[t]:.1%}</td></tr>"
        for t in sorted(by_tpl)
    )

    max_tag = max(tag_counter.values(), default=1)
    cat_short = {
        "missed_intent": "漏意图",
        "cancel_wrong": "取消",
        "record_wrong": "record",
        "memory_wrong": "画像",
        "time_wrong": "时间",
        "appsvc_wrong": "应用服务",
        "value_wrong": "值",
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
            f"<td><div class='bar'><i style='width:{n/max_tag*100:.0f}%;"
            f"background:{m['color']}'></i></div></td>"
            f"<td style='color:var(--sub)'>{esc(cross)}</td></tr>"
        )
    tag_desc = "".join(
        f"<li><b>{t} {esc(TAG_META[t]['name'])}</b>：{esc(TAG_META[t]['desc'])}</li>"
        for t in TAG_ORDER
        if tag_counter.get(t)
    )

    def _tag_btns(counter: collections.Counter[str], view_id: str, attr: str) -> str:
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

    tag_panel = (
        "<div class='panel'><h3>原因分布（二级标签：为什么错 &gt; 错在哪里，参照 A1 ESF-Bench 分类法）</h3>"
        f"<table class='tpl'><tr><th>标签</th><th>样本数</th><th></th><th>落在一级桶</th></tr>"
        f"{tag_rows}</table>"
        f"<div class='tagfilter'><b>按标签过滤样本：</b>{_tag_btns(tag_counter, 'view-turn', 'data-tag')}"
        f"<span class='tagfilter-hint'>（只显示选中标签的 turn 级错误样本卡片，跨一级错误桶生效）</span></div>"
        f"<details style='margin-top:8px'><summary style='cursor:pointer;font-size:13px;color:var(--sub)'>"
        f"标签定义与修补指向</summary><ul style='font-size:12.5px;line-height:1.8'>{tag_desc}</ul>"
        "</details></div>"
    )

    max_cat = max((len(cases[c]) for c in TURN_CATS if cases.get(c)), default=1)
    dist_rows = ""
    for c in CAT_ORDER:
        if not cases.get(c):
            continue
        m = CAT_META[c]
        n = len(cases[c])
        dist_rows += (
            f"<tr><td><span class='tag' style='background:{m['color']};color:#fff'>"
            f"{esc(m['title'])}</span></td><td>{n}</td>"
            f"<td><div class='bar'><i style='width:{n/max_cat*100:.0f}%;"
            f"background:{m['color']}'></i></div></td>"
            f"<td style='color:var(--sub);font-size:12px'>{esc(m['desc'][:48])}…</td>"
            f"<td><a href='#view-turn' onclick=\"document.getElementById('{c}').scrollIntoView()\">查看样本</a></td></tr>"
        )
    turn_dist_panel = (
        "<div class='panel'><h3>错误分布（一级桶，按错字段位置归类）</h3>"
        f"<table class='tpl'><tr><th>错误类型</th><th>样本数</th><th></th><th>说明</th><th></th></tr>"
        f"{dist_rows}</table></div>"
    )

    sess_dist_panel = (
        "<div class='panel'><h3>错误分布（session 级 = 最终意图集合严格全等）</h3>"
        f"<table class='tpl'><tr><th>错误类型</th><th>session 数</th><th>说明</th></tr>"
        f"<tr><td><span class='tag' style='background:{CAT_META['intent_set_mismatch']['color']};color:#fff'>意图集合不一致</span></td>"
        f"<td>{len(sess_set_mismatch)}</td>"
        f"<td style='color:var(--sub);font-size:12px'>多识别（幻觉/未取消）或漏识别整个意图</td></tr>"
        f"<tr><td><span class='tag' style='background:#5d6d7e;color:#fff'>字段错误</span></td>"
        f"<td>{len(sess_field_wrong)}</td>"
        f"<td style='color:var(--sub);font-size:12px'>意图集合一致，但至少一个意图的字段值不全等</td></tr>"
        f"</table></div>"
    )
    max_stag = max(sess_tag_counter.values(), default=1)
    stag_rows = ""
    for t in TAG_ORDER:
        n = sess_tag_counter.get(t, 0)
        if not n:
            continue
        m = TAG_META[t]
        stag_rows += (
            f"<tr><td><span class='tag' style='background:{m['color']};color:#fff'>"
            f"{t}</span> <b>{esc(m['name'])}</b></td><td>{n}</td>"
            f"<td><div class='bar'><i style='width:{n/max_stag*100:.0f}%;"
            f"background:{m['color']}'></i></div></td></tr>"
        )
    sess_tag_panel = (
        "<div class='panel'><h3>原因分布（session 级主因 = 该 session 失败 turn 的众数标签）</h3>"
        f"<table class='tpl'><tr><th>标签</th><th>session 数</th><th></th></tr>{stag_rows}</table>"
        f"<div class='tagfilter'><b>按主因过滤样本：</b>{_tag_btns(sess_tag_counter, 'view-sess', 'data-stag')}"
        f"<span class='tagfilter-hint'>（只显示主因匹配的 session 样本卡片）</span></div></div>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>错误样本分析报告 - {esc(os.path.basename(eval_path))}</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<div class="hero">
<h1>错误样本分析报告</h1>
<div class="meta">评估文件 <code>{esc(eval_path)}</code> ｜ 数据集 <code>{esc(dataset_dir)}</code></div>
</div>
<div class="stat-grid">
<div class="stat"><div class="num">{rate_s:.2%}</div><div class="lbl">session 级 JGA（{n_sess_match}/{n_sess}）</div></div>
<div class="stat"><div class="num">{rate_t:.2%}</div><div class="lbl">turn 级 JGA（{n_turn_match}/{n_turn}）</div></div>
<div class="stat red"><div class="num">{n_err_turn}</div><div class="lbl">错误 turn 样本</div></div>
<div class="stat red"><div class="num">{n_sess - n_sess_match}</div><div class="lbl">错误 session（集合不一致 {len(sess_set_mismatch)} + 字段错 {len(sess_field_wrong)}）</div></div>
{f'<div class="stat"><div class="num">{len(noise_map)}</div><div class="lbl">数据集噪声样本（已剔除，见文末）</div></div>' if noise_map else ''}
</div>
<div class="viewbar">
<button id="vb-turn" class="viewbtn active" onclick="switchView('turn')">turn 级视图<small>逐轮累积态比对 ｜ 错误 {n_err_turn} 样本 ｜ turn JGA {rate_t:.2%}</small></button>
<button id="vb-sess" class="viewbtn" onclick="switchView('sess')">session 级视图<small>最终意图集合严格全等 ｜ 错误 {n_sess - n_sess_match} session ｜ session JGA {rate_s:.2%}</small></button>
</div>

<div id="view-turn">
{turn_dist_panel}
{tag_panel}
<div class="panel"><h3>按模板 turn 正确率</h3>
<table class="tpl"><tr><th>模板</th><th>正确 / 总数</th><th></th><th>正确率</th></tr>{tpl_rows_turn}</table></div>
<div class="nav">{nav_turn}</div>
{''.join(turn_sections)}
</div>

<div id="view-sess" style="display:none">
{sess_dist_panel}
{sess_tag_panel}
<div class="panel"><h3>按模板 session 正确率</h3>
<table class="tpl"><tr><th>模板</th><th>正确 / 总数</th><th></th><th>正确率</th></tr>{tpl_rows_sess}</table></div>
<div class="nav">{nav_sess}</div>
{''.join(sess_sections)}
</div>

{noise_section}
</div>
<button id="top-btn" style="display:none" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>{_JS}</script>
</body></html>"""
