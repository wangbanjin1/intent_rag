"""find_noise.py: 客观规则扫描数据集标注噪声。

只采用保守的、客观的规则（宁漏勿滥）:
  R1 时间内部不一致: GT 的 end - start != duration（同一 intent 三字段自相矛盾）
  R2 明说时长矛盾: utterance 明说时长("一个半小时"等)且 session 所有 intent 的 duration 都不符
  R3 明说时段矛盾: utterance 明说"X点到Y点"(整点)且所有 intent 的 start/end 时刻都不符
  R4 画质词矛盾: utterance 明说画质词且 GT resolution 与 train 还原的真实映射表不符
     （查不到的组合跳过不猜; "网络流畅"不算画质词）
  R5 record 选择错误: 需要取 record 的样本, GT 所选 record 违反数据集自己的
     "按指代条件过滤 + 取最近(completed_at 最大)"规则:
       A) 有 evidence.query 的(T3-3): 按 query 过滤用户 records 取最近, 与 selected 不符即噪声
       B) 话语有"上次/记录/最近"但无任何指代条件: GT 必须取 records[0](最近), 否则噪声
       C) 话语恰好一个指代条件(service/app/日期之一): 过滤后取最近, GT 不符即噪声
     （多 record 意图/多条件的一律跳过, 不猜）

输出: data/_noise_exclude.json  {"session_ids": [...], "reasons": {sid: [..]}, "note": ...}
session_eval 默认剔除该清单（--include_noise 可关）。

用法: python find_noise.py [--dataset data/dataset_merged] [--split data/split_merged.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

from case_format import flatten_params

REF_FMT = "%Y年%m月%d日%H时%M分"

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_CORR_WORDS = ("不是", "不对", "错了", "改成", "改为", "换成", "应该是",
               "我说的是", "搞错", "弄错", "重新")


def _parse_ts(s: str):
    from datetime import datetime
    try:
        return datetime.strptime(s, "%Y年%m月%d日%H时%M分")
    except Exception:
        return None


def _parse_dur(s: str):
    if not s:
        return None
    m = re.fullmatch(r"(\d+)min", s)
    return int(m.group(1)) if m else None


def _utt_text(session: dict) -> str:
    return " ".join((ev.get("user") or {}).get("utterance", "")
                    for ev in (session.get("event_sequence") or []))


def _resolved_intents(session: dict) -> list[dict]:
    out = []
    for it in (session.get("intents") or []):
        if it.get("status") != "resolved":
            continue
        flat = flatten_params(it.get("params") or {})
        if flat.get("application_name"):
            out.append(flat)
    return out


def _is_risky(session: dict, text: str) -> bool:
    """有取消/选择疑问/改口的 session, 话语里的字段值可能不属于最终 GT, 只做 R1。"""
    if "还是" in text or any(w in text for w in _CORR_WORDS):
        return True
    for ev in (session.get("event_sequence") or []):
        acts = (ev.get("user") or {}).get("action_types") or []
        if any(a in ("Cancel_Request", "Correct_Previous_Input", "Negate",
                     "Modify_Request") for a in acts):
            return True
    return False


def _duration_expectations(text: str) -> set[int]:
    """从话语提取明说时长(分钟)。排除选择疑问句片段("一个小时还是半小时"由 _is_risky 拦截)。"""
    exp = set()
    for m in re.finditer(r"([一二两三四五六七八九十\d]+)个?(半)?(?:个)?小时", text):
        tok = m.group(1)
        base = _CN_NUM.get(tok, None) if not tok.isdigit() else int(tok)
        if base:
            exp.add(base * 60 + (30 if m.group(2) else 0))
    # "半小时" 仅独立出现时(前面不是 个/数字/一, 排除"一个半小时")
    if re.search(r"(?<![个\d一二两三四五六七八九十])半小时", text):
        exp.add(30)
    for m in re.finditer(r"(\d+)\s*分钟", text):
        exp.add(int(m.group(1)))
    return exp


def _time_range_expectations(text: str) -> set[tuple[int, int]]:
    """提取明说的"X点到Y点"整点时段(小时对, 24h 制)。只处理两侧都是整点。"""
    exp = set()
    pat = re.compile(
        r"(上午|中午|下午|晚上|凌晨)?(\d{1,2})点(?!半)(?:到|至|~|-)(上午|中午|下午|晚上|凌晨)?(\d{1,2})点(?!半)")
    for m in pat.finditer(text):
        per1, per2 = m.group(1) or "", m.group(3) or ""
        h1, h2 = int(m.group(2)), int(m.group(4))
        # 第二时间的时段词缺省时沿用第一时间的(如"下午1点到3点")
        h1 = _apply_period(h1, per1)
        h2 = _apply_period(h2, per2 or per1)
        exp.add((h1, h2))
    return exp


def _apply_period(h: int, per: str) -> int:
    if per in ("下午", "晚上"):
        return h + 12 if h < 12 else h
    if per == "凌晨":
        return 0 if h == 12 else h
    if per == "中午":
        return 12 if h == 12 else (h + 12 if h < 11 else h)
    return h


# 画质词映射: 不手写! 从 train split 的 GT source_ref (resolution_quality_map:app:svc:词)
# 还原真实映射表, 组合不在表内的一律跳过(不猜)。map 是数据集生成规则, train/test 一致。
_RES_MAP_PAT = re.compile(r"resolution_quality_map:([^:]+):([^:]+):(.+)")


def build_quality_map(dataset_dir: str, split_path: str) -> dict[tuple[str, str, str], str]:
    """从 train sessions 的 source_ref 还原 (app, svc, 画质词) -> resolution。"""
    split = json.load(open(split_path, encoding="utf-8"))
    train_ids = set(split.get("train_sessions", []))
    qmap: dict[tuple[str, str, str], str] = {}
    conflict: dict[tuple, set] = {}

    def walk(o):
        if isinstance(o, dict):
            ref = str(o.get("source_ref", ""))
            if ref.startswith("resolution_quality_map"):
                m = _RES_MAP_PAT.match(ref)
                if m and isinstance(o.get("value"), str):
                    key = (m.group(1), m.group(2), m.group(3))
                    if key in qmap and qmap[key] != o["value"]:
                        conflict.setdefault(key, set()).update([qmap[key], o["value"]])
                    else:
                        qmap[key] = o["value"]
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for f in sorted(glob.glob(os.path.join(dataset_dir, "sessions.*"))):
        if "users" in os.path.basename(f):
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                s = json.loads(line)
                if s["session_id"] not in train_ids:
                    continue
                walk(s.get("intents") or [])
                walk(s.get("slot_updates") or [])
    if conflict:
        print(f"[find_noise] 警告: 映射表内部冲突 {conflict}")
    return qmap


_RES_WORDS = ("超高清", "4K", "4k", "蓝光", "高清", "超清", "标清", "流畅")


def _res_expected(word: str, app: str, svc: str, qmap: dict) -> str:
    """真实映射表查询, 查不到返回空(跳过, 不猜)。"""
    return qmap.get((app, svc, word), "")


# ---------------- R5: record 选择错误 ----------------

_SVC_WORDS = ("视频通话", "开直播", "看直播", "云游戏", "视频", "会议")  # 长词优先
_REF_WORDS = ("上次", "记录", "最近", "之前", "那条", "那次", "历史")


def _load_users(dataset_dir: str) -> dict[str, list[dict]]:
    """{user_id: records 按 completed_at 倒序} (与 prompt recent_records 顺序一致)。"""
    out = {}
    for f in sorted(glob.glob(os.path.join(dataset_dir, "users*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                u = json.loads(line)
                out[u.get("user_id")] = sorted(
                    u.get("records") or [],
                    key=lambda r: r.get("completed_at", ""), reverse=True)
    return out


def _iter_leaf_params(params: dict, prefix: str = ""):
    for k, v in params.items():
        if not isinstance(v, dict):
            continue
        path = f"{prefix}.{k}" if prefix else k
        if "value" in v:
            yield path, v
        else:
            yield from _iter_leaf_params(v, path)


def _gt_record_ids(session: dict) -> list[str]:
    """session 所有 resolved intent 里 Record 来源字段的 source_ref (record_id), 去重。"""
    ids: list[str] = []
    for it in (session.get("intents") or []):
        if it.get("status") != "resolved":
            continue
        for _path, info in _iter_leaf_params(it.get("params") or {}):
            if info.get("source_type") == "Record":
                rid = str(info.get("source_ref", ""))
                if rid and rid not in ids:
                    ids.append(rid)
    return ids


def _rec_matches_cond(rec: dict, kind: str, val) -> bool:
    if kind == "service":
        return rec.get("service_name") == val
    if kind == "app":
        return rec.get("application_name") == val
    if kind == "date":
        m, d = val
        return f"{m:02d}月{d:02d}日" in (rec.get("completed_at") or "")
    return False


def _date_conds(text: str) -> list[tuple[int, int]]:
    """话语里的日期指代条件: 仅当日期紧邻"按/那次/那条/记录/上次"(是 record 过滤条件,
    而非新保障的日期)。"""
    out = []
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})[号日]", text):
        ctx = text[max(0, m.start() - 6):m.end() + 6]
        if any(w in ctx for w in ("按", "那次", "那条", "记录", "上次", "之前")):
            out.append((int(m.group(1)), int(m.group(2))))
    return out


def r5_reasons(session: dict, users: dict) -> list[str]:
    """record 选择错误: GT 所选 record 违反数据集自身规则。

    A) T3-3 消歧场景: GT 必须与用户在消歧对话里的显式选择一致
       ("第N条"→candidate_summaries[N-1]; "X月X号那条"→按日期匹配候选)。
       注意不能用"取最近"判——T3-3 的 GT 跟随用户选择, 与 completed_at 无关。
    B/C) 无消歧场景: 指代条件只从指代词(上次/记录/那条/...)±12字符邻域内提取
       (防止把新保障意图的 app/service 误当过滤条件); 无条件→应取画像最近一条;
       恰一个条件→过滤后取最近。
    """
    gt_ids = _gt_record_ids(session)
    if not gt_ids:
        return []
    text = _utt_text(session)

    # 规则 A: T3-3 消歧场景, 校验 GT 与用户显式选择一致
    ev = (session.get("session_meta") or {}).get("evidence") or {}
    cands = ev.get("candidate_summaries") or []
    selected = ev.get("selected_record_id")
    if cands and selected:
        m = re.search(r"第([一二三123])条", text)
        if m:
            idx = "一二三123".index(m.group(1)) % 3
            if idx < len(cands) and cands[idx].get("record_id") != selected:
                return [f"R5-A 用户说第{m.group(1)}条=candidates[{idx}]"
                        f"={cands[idx].get('record_id')}, GT 却选了 {selected}"]
            return []
        dcs = _date_conds(text)
        if len(dcs) == 1:
            m0, d0 = dcs[0]
            hit = [c for c in cands if f"{m0:02d}月{d0:02d}日" in (c.get("completed_at") or "")]
            if len(hit) == 1 and hit[0].get("record_id") != selected:
                return [f"R5-A 用户按日期 {m0}月{d0}号 选了 {hit[0].get('record_id')}, "
                        f"GT 却选了 {selected}"]
        return []

    # 规则 B/C: 无消歧场景
    recs = users.get(session.get("user_id", "")) or []
    if not recs or len(gt_ids) > 1 or not any(w in text for w in _REF_WORDS):
        return []
    # 指代条件只在指代词邻域内提取
    spans = []
    for w in _REF_WORDS:
        for m in re.finditer(re.escape(w), text):
            spans.append(text[max(0, m.start() - 12):m.end() + 12])
    ctx = " ".join(spans)
    conds: list[tuple[str, object]] = []
    for w in _SVC_WORDS:
        if w in ctx:
            conds.append(("service", w))
            break
    from schema import KNOWN_APPLICATIONS
    apps = [a for a in KNOWN_APPLICATIONS if a in ctx]
    if len(apps) == 1:
        conds.append(("app", apps[0]))
    elif len(apps) > 1:
        return []  # 多 app, 条件归属不明, 跳过
    dcs = _date_conds(text)
    if len(dcs) == 1:
        conds.append(("date", dcs[0]))
    elif len(dcs) > 1:
        return []

    if not conds:
        pred, desc = recs[0], "无任何指代条件应取画像最近一条 records[0]"
    elif len(conds) == 1:
        kind, val = conds[0]
        fcands = [r for r in recs if _rec_matches_cond(r, kind, val)]
        if not fcands:
            return []
        pred = max(fcands, key=lambda r: r.get("completed_at", ""))
        desc = f"按 {kind}={val} 过滤后取最近"
    else:
        return []  # 多条件组合, 跳过不猜
    if pred.get("record_id") != gt_ids[0]:
        return [f"R5-{'B' if not conds else 'C'} {desc} = {pred.get('record_id')}"
                f"({pred.get('application_name')}/{pred.get('service_name')} "
                f"completed={pred.get('completed_at')}), GT 却选了 {gt_ids[0]}(更早的记录)"]
    return []


# ---------------- 主检查 ----------------

def check_session(session: dict, qmap: dict, users: dict) -> list[str]:
    """返回该 session 的噪声原因列表(空=干净)。"""
    reasons = []
    intents = _resolved_intents(session)
    if not intents:
        return reasons
    text = _utt_text(session)

    # R1 时间内部不一致
    for flat in intents:
        s, e, d = _parse_ts(flat.get("start_timestamp") or ""), \
            _parse_ts(flat.get("end_timestamp") or ""), _parse_dur(flat.get("duration"))
        if s and e and d is not None:
            if abs((e - s).total_seconds() / 60 - d) > 1:
                reasons.append(f"R1 时间内部不一致: {flat.get('application_name')} "
                               f"start={flat.get('start_timestamp')} end={flat.get('end_timestamp')} "
                               f"duration={flat.get('duration')}")

    # R5 record 选择错误 (独立于 _is_risky: 取消/改口不影响 record 选择规则判定)
    reasons.extend(r5_reasons(session, users))

    # R2/R3/R4 在有取消/选择疑问/改口的 session 里话语字段可能不属于最终 GT, 跳过
    if _is_risky(session, text):
        return reasons

    # R2 明说时长矛盾
    for exp in _duration_expectations(text):
        if not any(_parse_dur(f.get("duration")) == exp for f in intents):
            reasons.append(f"R2 明说时长{exp}min与所有GT不符: durs={[f.get('duration') for f in intents]}")

    # R3 明说时段矛盾
    for h1, h2 in _time_range_expectations(text):
        ok = False
        for f in intents:
            s, e = _parse_ts(f.get("start_timestamp") or ""), _parse_ts(f.get("end_timestamp") or "")
            if s and e and s.hour == h1 and s.minute == 0 and e.hour == h2 and e.minute == 0:
                ok = True
                break
        if not ok:
            reasons.append(f"R3 明说时段{h1}点-{h2}点与所有GT不符: "
                           f"{[(f.get('start_timestamp'), f.get('end_timestamp')) for f in intents]}")

    # R4 画质词矛盾 (仅单意图 session; 用 train 还原的真实映射表, 查不到的组合跳过)
    if len(intents) == 1:
        app = intents[0].get("application_name") or ""
        svc = intents[0].get("service_name") or ""
        # "流畅"仅作画质词(前面接"网络/网速"时是形容网络, 不算)
        text_r4 = re.sub(r"(?:网络|网速)流畅", "", text)
        for w in _RES_WORDS:  # 长词优先, 避免"超高清"先命中"超清"
            if w in text_r4:
                exp = _res_expected(w, app, svc, qmap)
                if exp and (intents[0].get("resolution") or "") != exp:
                    reasons.append(f"R4 画质词'{w}'@{app}/{svc}→{exp}(train映射表)与GT不符: "
                                   f"{intents[0].get('resolution')}")
                break  # 一个话语只按一个画质词判(取先命中的)
    return reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.environ.get("DST_DATASET", "data/dataset_merged"))
    ap.add_argument("--split", default="data/split_merged.json")
    ap.add_argument("--all", action="store_true", help="扫全部 session(默认只扫 test)")
    ap.add_argument("--out", default="data/_noise_exclude.json")
    args = ap.parse_args()

    split = json.load(open(args.split, encoding="utf-8"))
    ids = set(split["train_sessions"] + split["test_sessions"]) if args.all \
        else set(split["test_sessions"])

    qmap = build_quality_map(args.dataset, args.split)
    print(f"画质映射表: {len(qmap)} 条 (从 train split source_ref 还原)")
    users = _load_users(args.dataset)
    print(f"用户画像: {len(users)} 个用户")

    noise: dict[str, list[str]] = {}
    n_scanned = 0
    for f in sorted(glob.glob(os.path.join(args.dataset, "sessions.*"))):
        if "users" in os.path.basename(f):
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                s = json.loads(line)
                if s["session_id"] not in ids:
                    continue
                n_scanned += 1
                rs = check_session(s, qmap, users)
                if rs:
                    noise[s["session_id"]] = rs

    out = {"note": "客观规则噪声清单(find_noise.py R1-R4), session_eval 默认剔除; "
                   "--include_noise 关闭",
           "n_scanned": n_scanned, "session_ids": sorted(noise), "reasons": noise}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"扫描 {n_scanned} session, 噪声 {len(noise)} 个 -> {args.out}")
    for sid, rs in sorted(noise.items()):
        print(f"  {sid}: {rs[0][:100]}")


if __name__ == "__main__":
    main()
