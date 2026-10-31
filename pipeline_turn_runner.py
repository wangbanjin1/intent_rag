"""pipeline_turn_runner.py — 单轮沙箱执行后处理、PAR 重采样判定与多意图段路由。

职责:
  1. PAR 校验回路 (_identity_ok, _need_par, _summaries_ok, _cancel_check_ok, check_id_headers_ok, is_suspicious_new_segment)
  2. 候选列举轮抢跑剥离与兜底补齐 (strip_cand_listing_jump_run, apply_record_and_time_stub_fallbacks)
  3. 多实例 intent_key 命名与同 base 多实例取消消歧 (intent_key, _base_key, _instanced_key, _resolve_cancel_key, _disambiguate_cancel)
  4. # cancel / # intent (id 协议与内容 key 协议) 段状态更新与校验落地
"""
from __future__ import annotations

import re
from typing import Any

import time_extractor as _tex
from python_state import _match_prev_key, parse_summaries
from validator import validate

_CANCEL_WORDS = (
    "不用了", "取消", "撤了", "不弄了", "不用保障", "不用办",
    "算了", "只留", "只保留", "照旧", "那个不用", "去掉",
)


def _identity_ok(code: str, id_on: bool) -> bool:
    """v26.2 身份判定 PAR 校验: id 协议下每个 # intent 段头前必须有 '# 身份判定:' 行。"""
    if not id_on:
        return True
    segs = re.split(r"#\s*intent:", code)[1:]
    if not segs:
        return True
    n_id = len(re.findall(r"#\s*身份判定", code))
    n_hdr = len(re.findall(r"#\s*intent:", code))
    return n_id >= n_hdr


def _need_par(utterance: str, merged_prev: dict[str, Any]) -> bool:
    """是否触发 PAR 校验:
    1) utterance 含"上次/平时/记录"且 prev_state 空 (首轮 coreference)
    2) prev_state 非空 + utterance 含取消词 (取消意图场景, 需校验输出 # cancel)
    """
    if merged_prev:
        return any(w in utterance for w in _CANCEL_WORDS)
    return any(w in utterance for w in ("上次", "平时", "记录", "历史"))


def _summaries_ok(code: str) -> bool:
    """Variant A PAR 校验: 每个 intent 段前都有 summary。"""
    summaries = parse_summaries(code)
    if not summaries:
        return True
    for _, txt in summaries.items():
        if not txt:
            return False
    return True


def _cancel_check_ok(utterance: str, code: str) -> bool:
    """取消场景校验: utterance 含取消词时, 输出必须含 # cancel 段。"""
    if not any(w in utterance for w in _CANCEL_WORDS):
        return True
    from python_state import parse_cancels
    return len(parse_cancels(code)) > 0


def check_id_headers_ok(code: str, id_on: bool, idbook: Any | None) -> bool:
    """v26 id 协议段头遵从性校验。"""
    if not id_on or not idbook:
        return True
    from intent_ids import ID_RE
    for hk in re.findall(r"#\s*intent:\s*(.+)", code):
        h = hk.strip()
        if h == "none" or ID_RE.match(h) or h.lower() in ("new", ""):
            continue
        return False
    for ck in re.findall(r"#\s*cancel:\s*(.+)", code):
        c = ck.strip()
        if not ID_RE.match(c):
            return False
    return True


def is_suspicious_new_segment(
    code: str,
    utterance: str,
    id_on: bool,
    idbook: Any | None,
    prev_states: dict[str, dict[str, Any]],
    mi_on: bool,
) -> bool:
    """v26.2 full-match new 门: new 段的 app+svc 与某个 prev 意图完全同名且话语无新增词时触发重采样。"""
    if not id_on or not idbook or not prev_states:
        return False
    add_words = ("再", "加", "还要", "另外", "办个", "再来", "多个", "也")
    if any(w in utterance for w in add_words):
        return False
    import python_state as _ps
    from case_format import flatten_params as _fpn
    for key, seg in _ps._split_intent_segments(code):
        if key.strip().lower() != "new":
            continue
        try:
            delta = _ps.execute_program(seg, {}, timeout=1.5)
        except Exception:
            continue
        f = _fpn(delta)
        app, svc = f.get("application_name"), f.get("service_name")
        if not app or not svc:
            continue
        for pk in prev_states:
            pf = _fpn(prev_states[pk])
            if pf.get("application_name") == app and pf.get("service_name") == svc:
                if mi_on:
                    fields = ("resolution", "rtt", "start_timestamp", "end_timestamp", "duration")
                    differing = [
                        s for s in fields
                        if (f.get(s) or None) and (f.get(s) or None) != (pf.get(s) or None)
                    ]
                    if differing:
                        break
                return True
    return False


def strip_cand_listing_jump_run(
    segs: list[tuple[str, Any]],
    utterance: str,
    listed_this_turn: bool,
    cand_listing: list[dict[str, Any]] | None,
) -> list[tuple[str, Any]]:
    """v27.7: 候选列举轮剥离模型违令抢跑落地的候选身份字段。"""
    if not (listed_this_turn and cand_listing):
        return segs
    import record_selector as _rs7
    from schema import KNOWN_APPLICATIONS as _KAP7
    _f7 = _rs7._extract_filters(
        utterance,
        tuple(_KAP7),
        ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频"),
    ).get("application_name")
    _cand_apps = {r.get("application_name") for r in cand_listing}
    _stripped: list[tuple[str, Any]] = []
    for _k, _d in segs:
        if _k == "__cancel__" or not isinstance(_d, dict):
            _stripped.append((_k, _d))
            continue
        _da = _d.get("application_name")
        _da_v = _da.get("value") if isinstance(_da, dict) else _da
        if _da_v in _cand_apps and not (_da_v and _da_v in utterance and _da_v != _f7):
            _d = {
                kk: vv
                for kk, vv in _d.items()
                if kk not in ("application_name", "service_name", "resolution", "rtt")
            }
        _stripped.append((_k, _d))
    return _stripped


def apply_record_and_time_stub_fallbacks(
    segs: list[tuple[str, Any]],
    utterance: str,
    user_context: dict[str, Any] | None,
    merged_prev: dict[str, Any],
    listed_this_turn: bool,
    time_ev: Any,
) -> list[tuple[str, Any]]:
    """执行首轮 record coreference 兜底与候选列举轮时间桩兜底。"""
    all_empty = all(not d for _, d in segs) or not segs
    if all_empty and user_context and not merged_prev and not listed_this_turn:
        rec_words = ("上次", "平时", "记录", "历史")
        if any(w in utterance for w in rec_words):
            recs = user_context.get("recent_records") or []
            if recs:
                delta = _delta_from_record(recs[0])
                segs = [("", delta)]

    if (
        listed_this_turn
        and all_empty
        and time_ev is not None
        and time_ev.start is not None
        and time_ev.end is not None
        and not time_ev.has_contradiction
        and not time_ev.has_candidate
    ):
        import record_selector as _rs2
        from schema import KNOWN_APPLICATIONS as _KAPPS
        _f_app = _rs2._extract_filters(
            utterance,
            tuple(_KAPPS),
            ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频"),
        ).get("application_name")
        _new_app = any(name in utterance and name != _f_app for name in _KAPPS)
        if not _new_app:
            _ts: dict[str, Any] = {
                "start_timestamp": {
                    "value": _tex._fmt(time_ev.start),
                    "source_type": "Turn",
                    "source_ref": "derived",
                },
                "end_timestamp": {
                    "value": _tex._fmt(time_ev.end),
                    "source_type": "Turn",
                    "source_ref": "derived",
                },
            }
            if time_ev.duration_min is not None:
                _ts["duration"] = {
                    "value": f"{time_ev.duration_min}min",
                    "source_type": "Turn",
                    "source_ref": "derived",
                }
            segs = [("new", {"timestamp": _ts})]
    return segs


def intent_key(app: str | None, svc: str | None) -> str:
    """统一 intent_key 格式: app/service。"""
    a = (app or "").strip()
    s = (svc or "").strip()
    if not a and not s:
        return ""
    if not s:
        return a
    return f"{a}/{s}"


def _base_key(k: str) -> str:
    """剥实例后缀: '淘宝/看直播#2' -> '淘宝/看直播'。"""
    return k.split("#", 1)[0] if "#" in k else k


def _instanced_key(
    k: str,
    results: dict[str, Any],
    enabled: bool,
    prior_keys: dict[str, Any] | list[str] | None = None,
) -> str:
    """同 base key 已存在(本轮 results 或历史 prior_keys)时分配下一个实例后缀。"""
    if not enabled:
        return k
    base = _base_key(k)
    pool = set(results)
    if prior_keys:
        pool |= set(prior_keys)
    existing = {(_base_key(x), x) for x in pool if not x.startswith("__cancel__")}
    same = [x for b, x in existing if b == base]
    if not same:
        return k
    n = 2
    while f"{base}#{n}" in pool:
        n += 1
    return f"{base}#{n}"


def _resolve_cancel_key(ck: str, prev_states: dict[str, dict[str, Any]]) -> str:
    """把 cancel 信号归一化到 prev_states 里真实存在的 intent_key。"""
    if not ck:
        return ""
    if ck in prev_states:
        return ck
    app = ck.split("/")[0]
    for k in prev_states:
        if k == app or k.startswith(app + "/"):
            return k
    return ck


_DAYPART_HOUR_C = {"凌晨": 0, "早上": 0, "上午": 0, "中午": 12, "下午": 12, "晚上": 12}


def _parse_time_hint(utterance: str) -> set[tuple[int, int]]:
    """从 cancel 话语提取提及的时刻 (时,分) 集合, 供与实例 start/end 匹配。"""
    out: set[tuple[int, int]] = set()
    for m in re.finditer(
        r"(凌晨|早上|上午|中午|下午|晚上)?(\d{1,2})[点:：时](半|一刻|(?:\d{1,2})分?)?",
        utterance or "",
    ):
        h = int(m.group(2))
        if m.group(1):
            h = (_DAYPART_HOUR_C[m.group(1)] + h) % 24
        mm = 0
        tail = m.group(3)
        if tail == "半":
            mm = 30
        elif tail == "一刻":
            mm = 15
        elif tail:
            mm = int(re.sub(r"\D", "", tail) or 0)
        out.add((h, mm))
    return out


def _parse_ordinal(utterance: str) -> int | None:
    """话语中的序数 ("第一个/第1个/第一份") -> 1-based 序号; 无则 None。"""
    m = re.search(r"第([一二三四五六七八九\d]+)[个份条]", utterance or "")
    if not m:
        return None
    g = m.group(1)
    if g.isdigit():
        return int(g)
    return "一二三四五六七八九".index(g) + 1


def _disambiguate_cancel(
    utterance: str, inst_keys: list[str], prev_states: dict[str, dict[str, Any]]
) -> str | None:
    """同 base 多实例 cancel 消歧: 时段文本匹配 -> 序数 (按 start 排序)。"""
    from case_format import flatten_params
    from validator import parse_dt
    hints = _parse_time_hint(utterance)
    if hints:
        hits = []
        for k in inst_keys:
            f = flatten_params(prev_states.get(k) or {})
            times = set()
            for s in ("start_timestamp", "end_timestamp"):
                dt = parse_dt(f.get(s) or "")
                if dt:
                    times.add((dt.hour, dt.minute))
            if times and (times & hints) == times:
                hits.append(k)
        if len(hits) == 1:
            return hits[0]
    ordinal = _parse_ordinal(utterance)
    if ordinal and len(inst_keys) >= ordinal:
        ranked = sorted(
            inst_keys,
            key=lambda k: (flatten_params(prev_states.get(k) or {}).get("start_timestamp") or ""),
        )
        return ranked[ordinal - 1]
    return None


def _delta_from_record(rec: dict[str, Any]) -> dict[str, Any]:
    """把画像 record (扁平) 转成嵌套 delta (Record 来源字段)。"""
    src = {"source_type": "Record", "source_ref": "derived"}
    delta: dict[str, Any] = {}
    if rec.get("application_name"):
        delta["application_name"] = {"value": rec["application_name"], **src}
    if rec.get("service_name"):
        delta["service_name"] = {"value": rec["service_name"], **src}
    if rec.get("resolution"):
        delta["resolution"] = {"min_value": {"value": rec["resolution"], **src}}
    if rec.get("rtt"):
        delta["rtt"] = {"max_value": {"value": rec["rtt"], **src}}
    return delta


def handle_cancel_segment(
    delta: Any,
    idbook: Any | None,
    prev_states: dict[str, dict[str, Any]],
    results: dict[str, dict[str, Any]],
    mi_on: bool = False,
    utterance: str = "",
) -> None:
    """处理 # cancel 段: 归一化被取消意图的 key 并写入取消信号。"""
    from intent_ids import parse_id_header
    ck = delta if isinstance(delta, str) else ""
    if idbook:
        iid, _ = parse_id_header(ck)
        if iid and idbook.key_of(iid):
            ck_full = idbook.key_of(iid)
        else:
            ck_full = _resolve_cancel_key(ck, prev_states)
            if mi_on and utterance:
                cands = idbook.keys_by_base(_base_key(ck_full) or ck_full)
                if len(cands) > 1:
                    hit = _disambiguate_cancel(utterance, cands, prev_states)
                    if hit:
                        ck_full = hit
    else:
        ck_full = _resolve_cancel_key(ck, prev_states)
    results[f"__cancel__:{ck_full}"] = {"__cancel__": True, "cancel_key": ck_full}


def process_id_segment(
    key: str,
    delta: dict[str, Any],
    idbook: Any,
    prev_states: dict[str, dict[str, Any]],
    reference_time: str,
    results: dict[str, dict[str, Any]],
    mi_on: bool = False,
) -> None:
    """v26 id 协议段处理: 段头是 I1/I2...(更新)或 new(新建)。"""
    from case_format import flatten_params
    from intent_ids import parse_id_header

    iid, is_new = parse_id_header(key)
    if is_new and (not key or key == "none" or key in prev_states) and len(prev_states) == 1:
        sole_ck = next(iter(prev_states))
        sole_iid = idbook.iid_of(sole_ck)
        if sole_iid:
            iid, is_new = sole_iid, False
    if is_new or (iid and idbook.key_of(iid) is None):
        final = validate({"intent": "unknown", "params": delta}, reference_time, {})
        fflat = flatten_params(final.get("final_state") or {})
        k = intent_key(fflat.get("application_name"), fflat.get("service_name"))
        if not k:
            results[key or "__nokey__"] = final
            return
        merge_cands = [
            pk
            for pk, pv in prev_states.items()
            if not flatten_params(pv).get("application_name")
            and flatten_params(pv).get("service_name") == fflat.get("service_name")
        ]
        if not merge_cands:
            noapp = [pk for pk, pv in prev_states.items() if not flatten_params(pv).get("application_name")]
            if len(noapp) == 1 and len(prev_states) == 1:
                merge_cands = noapp
        if len(merge_cands) == 1:
            mk = merge_cands[0]
            final = validate({"intent": "unknown", "params": delta}, reference_time, prev_states[mk])
            fflat = flatten_params(final.get("final_state") or {})
            k = intent_key(fflat.get("application_name"), fflat.get("service_name")) or mk
            if k != mk:
                idbook.rename(mk, k)
            else:
                idbook.ensure(k, idbook.iid_of(k) or idbook.alloc())
            final["_iid"] = idbook.iid_of(k)
            final["_consumed_key"] = mk
            k_final = idbook.key_of(idbook.iid_of(k)) or k
            results[k_final] = final
            return
        new_iid = idbook.alloc()
        k2 = _instanced_key(
            k, results, mi_on, prior_keys=list(prev_states) + list(idbook._by_key)
        )
        idbook.ensure(k2, new_iid)
        final["_iid"] = new_iid
        if k2 != k and k2 in results:
            return
        if k2 == k and k in results:
            return
        results[k2] = final
    else:
        cur_key = idbook.key_of(iid)
        final = validate(
            {"intent": "unknown", "params": delta},
            reference_time,
            prev_states.get(cur_key, {}),
        )
        fflat = flatten_params(final.get("final_state") or {})
        k = intent_key(fflat.get("application_name"), fflat.get("service_name")) or cur_key
        if not k:
            return
        if _base_key(k) == _base_key(cur_key):
            k = cur_key
        if k != cur_key:
            idbook.rename(cur_key, k)
            final["_consumed_key"] = cur_key
        final["_iid"] = iid
        k_final = idbook.key_of(iid) or cur_key
        results[k_final] = final


def process_key_segment(
    key: str,
    delta: dict[str, Any],
    prev_states: dict[str, dict[str, Any]],
    reference_time: str,
    results: dict[str, dict[str, Any]],
) -> None:
    """旧协议(v25.2)段处理: 段头是内容 key(app/svc), harness 内容匹配归并。"""
    from case_format import flatten_params
    if (not key or key == "none") and len(prev_states) == 1:
        key = next(iter(prev_states))
    mkey = _match_prev_key(key, prev_states)
    prev = prev_states[mkey] if mkey else {}
    final = validate({"intent": "unknown", "params": delta}, reference_time, prev)
    fflat = flatten_params(final.get("final_state") or {})
    k = intent_key(fflat.get("application_name"), fflat.get("service_name")) or key
    if mkey and mkey != k:
        final["_consumed_key"] = mkey
    results[k] = final
