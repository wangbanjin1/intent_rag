"""prompts/report_html_templates.py — 错误分析报告静态 CSS、JS 与 HTML 文档骨架模板。"""
from __future__ import annotations

import html
import os

REPORT_CSS = """
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

REPORT_JS = """
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

DUP_KEY_BADGE = (
    '<span class="tag" style="background:#6c3483;color:#fff" '
    'title="GT 含同 app+service 的多条意图，session 级 gt/pred 按意图 key(应用/服务) '
    "建 dict 会互相覆盖(塌缩)，集合比对与 7 字段比对只看塌缩后的一条——"
    'session 判定与最终结果对照可能失真，判读时须结合逐轮明细与数据集原始信息">同key双意图·塌缩</span>'
)


def format_report_html_document(
    eval_path: str,
    summary_banner_html: str,
    turn_view_html: str,
    sess_view_html: str,
    noise_section_html: str,
) -> str:
    """组装错误分析报告顶层单文件 HTML 文档。"""
    esc = html.escape
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>错误样本分析报告 - {esc(os.path.basename(eval_path))}</title>
<style>{REPORT_CSS}</style></head><body>
<div class="wrap">
{summary_banner_html}
{turn_view_html}
{sess_view_html}
{noise_section_html}
</div>
<button id="top-btn" style="display:none" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>{REPORT_JS}</script>
</body></html>"""
