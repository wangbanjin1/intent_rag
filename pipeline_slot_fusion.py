"""pipeline_slot_fusion.py — 意图理解前置证据融合与提示词注入段组装。

职责:
  1. 改口轮检测与纠正类示例检索 (_is_correction, _retrieve_correction_demos)
  2. 消歧轮检测与候选列表话术顺序解析 (_is_disambiguation_turn, _parse_disamb_candidates, _resolve_listing)
  3. 记录引用规则、画质映射高亮、候选列举/选择模式与待补应用名规则段组装
  4. 时间锚点、明说时间证据与时间口径规则段组装 (_build_time_hint)
"""
from __future__ import annotations

import json
import re
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

import time_extractor as _tex
from prompts.memory_hint_templates import (
    TIME_EXPLICIT_FIRST_RULE,
    TIME_PARTIAL_CANDIDATE_RULE,
    TIME_UNDECIDED_CANDIDATE_RULE,
    format_candidate_existing_app_branch,
    format_candidate_listing_mode_block,
    format_candidate_placeholder_branch,
    format_candidate_selection_mode_block,
    format_candidate_stub_full_branch,
    format_candidate_stub_scoped_branch,
    format_pending_app_rule_block,
    format_quality_hint_block,
    format_time_anchor_block,
    format_time_contradiction_rule,
    format_time_evidence_block,
    format_time_profile_conflict_rule,
)
from retriever import DiverseRetriever, RetrievedCase
from schema import KNOWN_APPLICATIONS

# v12: 用户改口/否定词 (Correct_Previous_Input / Negate / Modify_Request)
_CORR_WORDS = (
    "不是", "不对", "错了", "改成", "改为", "换成", "应该是",
    "我说的是", "搞错", "弄错", "重新",
)


def _is_correction(utterance: str) -> bool:
    return any(w in (utterance or "") for w in _CORR_WORDS)


def _is_correction_case(case: Any) -> bool:
    """纠正类 demo: utterance 含改口词 且 delta 覆盖 prev_state 已有字段为新值。"""
    if not _is_correction(case.utterance):
        return False
    from case_format import flatten_params
    prev = flatten_params(case.prev_state or {})
    delta = flatten_params(case.delta or {})
    for k, v in delta.items():
        vv = v.get("ref") if isinstance(v, dict) else v
        pv = prev.get(k)
        if isinstance(vv, str) and isinstance(pv, str) and pv and pv != vv:
            return True
    return False


def _retrieve_correction_demos(
    retriever: DiverseRetriever,
    utterance: str,
    prev_state: dict[str, Any],
    n: int,
    exclude: set[str],
) -> list[RetrievedCase]:
    """从全库选纠正类 demo (v12), 按 embedding 相似度排序, 跳过已检索到的。"""
    bank = retriever.bank
    if len(bank) == 0 or np is None:
        return []
    if not hasattr(bank, "_corr_flags"):
        bank._corr_flags = [_is_correction_case(c) for c in bank.cases]  # type: ignore[attr-defined]
    qvec = bank.query_vec(prev_state or {}, utterance)
    sims = bank.embedding_scores(qvec)
    out: list[RetrievedCase] = []
    for idx in np.argsort(-np.asarray(sims)):
        if len(out) >= n:
            break
        if not bank._corr_flags[idx]:  # type: ignore[attr-defined]
            continue
        c = bank.cases[idx]
        if c.case_id in exclude:
            continue
        out.append(
            RetrievedCase(
                case_id=c.case_id,
                utterance=c.utterance,
                intent=c.intent,
                prev_state=c.prev_state,
                delta=c.delta,
                validity=c.validity,
                invalid_reason=c.invalid_reason,
                reference_time=c.reference_time,
                score=float(sims[idx]),
                emb_score=float(sims[idx]),
            )
        )
    return out


# v21: 消歧轮检测 (修补② T3-3)
_DISAMB_AGENT_RE = re.compile(r"记录")
_DISAMB_AGENT_Q_RE = re.compile(r"哪一?条|哪条|按哪|哪一个|哪次|还是.{0,30}(呢|？|\?)")
_DISAMB_USER_RE = re.compile(
    r"第[一二三四五六七八九十\d]+\s*[条个]|候选\s*[一二三四五六七八九十\d]+|就按|就选|就要|按.{0,12}(那条|该条|这条)"
    r"|\d{1,2}月\d{1,2}[号日].{0,8}(那条|这条|那条记录)|\d{1,2}[号日].{0,6}那条|最新的?一?条|最近的?一?条"
)


def _is_disambiguation_turn(
    utterance: str, agent_reply: str | None, prev_states: dict[str, Any]
) -> bool:
    """消歧回答轮: agent 上轮列出记录候选问选哪条 + 用户本轮做选择 + 有待沿用意图。"""
    if not prev_states or not agent_reply:
        return False
    ar = agent_reply or ""
    if not (_DISAMB_AGENT_RE.search(ar) and _DISAMB_AGENT_Q_RE.search(ar)):
        return False
    return bool(_DISAMB_USER_RE.search(utterance or ""))


_DISAMB_QCUT_RE = re.compile(r"请问|您看|您想|要选|要选择|要按")
_DISAMB_SPLIT_RE = re.compile(
    r"分别是|，和|、|；|，还有|还有|；另一条|，另一条|；而|，而|和(?!平)"
    r"|，(?=\d{1,2}月\d{1,2}[号日])"
)
_DISAMB_PREFIX_RES = [
    re.compile(r"^.*?条记录是"),
    re.compile(r"^.*?条是"),
    re.compile(r"^.*?记录有"),
    re.compile(r"^.*?分别是"),
    re.compile(r"^.*?[两三四五六七八九\d]条[^，；]*?记录[，,]?"),
]
_CN_NUM = "一二三四五六七八九"
_DAYPART_HOUR = {"凌晨": 0, "早上": 0, "上午": 0, "中午": 12, "下午": 12, "晚上": 12}


def _llm_pick(llm: Any, utterance: str, listing: list[dict[str, Any]]) -> int | None:
    """v27.4: 规则解析失败时的 LLM 兜底序号抽取。"""
    import candidate_llm as _cl
    return _cl.pick_index_llm(llm, utterance, listing)


def _cl_judge_selection(llm: Any, utterance: str, listing: list[dict[str, Any]]) -> dict[str, Any]:
    """v27.6: 选择轮判定+选中解析一体化 LLM 调用。"""
    import candidate_llm as _cl
    return _cl.judge_selection_turn(llm, utterance, listing)


def _resolve_listing(llm: Any, agent_reply: str | None, records: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """v28.4: 候选列表属性匹配 (规则快路径→LLM 兜底)。"""
    if not agent_reply:
        return None
    import candidate_llm as _cl
    _r = _cl.resolve_listing_rule(agent_reply, records)
    if _r:
        return _r
    return _cl.resolve_listing_llm(llm, agent_reply, records)


def _llm_parse_listing(llm: Any, agent_reply: str, records: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """v27.4: 话术解析失败时的 LLM 兜底顺序解析。"""
    import candidate_llm as _cl
    return _cl.parse_listing_llm(llm, agent_reply, records)


_ORD_SEL_RE = re.compile(
    r"(?:就按|就选|就要|就取|就|选|按|用|取)\s*(?:第([一二三四五六七八九十\d]+)\s*[条个]|候选\s*([一二三四五六七八九十\d]+))"
)
_ORD_NEG_RE = re.compile(
    r"(?:不选|不要|排除|去掉|取消)\s*(?:第([一二三四五六七八九十\d]+)\s*[条个]|候选\s*([一二三四五六七八九十\d]+))"
)
_ORD_ANY_RE = re.compile(r"第([一二三四五六七八九十\d]+)\s*[条个]|候选\s*([一二三四五六七八九十\d]+)")
_ORD_LATEST_RE = re.compile(r"最近的?一?条|最新的?一?条")


def _cn2int(s: str) -> int:
    return int(s) if s.isdigit() else _CN_NUM.index(s) + 1


def _parse_pick_ord(utterance: str) -> int | None:
    """选择轮序数解析 -> 1-based 候选序号 或 None。"""
    utt = utterance or ""
    neg = {_cn2int(m.group(1) or m.group(2)) for m in _ORD_NEG_RE.finditer(utt)}
    neg |= {
        int(_cn2int(m.group(1) or m.group(2)))
        for m in re.finditer(r"候选\s*([一二三四五六七八九十\d]+)\s*(?:不要|不选|不要了|排除)", utt)
    }
    neg |= {
        _cn2int(m.group(1))
        for m in re.finditer(r"第([一二三四五六七八九十\d]+)\s*[条个]\s*(?:不要|不选|排除)", utt)
    }
    for m in re.finditer(
        r"((?:候选\s*[一二三四五六七八九十\d]+\s*和\s*)+候选\s*[一二三四五六七八九十\d]+\s*)不要", utt
    ):
        neg |= {int(_cn2int(x)) for x in re.findall(r"候选\s*([一二三四五六七八九十\d]+)", m.group(1))}
    m = _ORD_SEL_RE.search(utt)
    if m:
        n = _cn2int(m.group(1) or m.group(2))
        if n not in neg:
            return n
    ords = [_cn2int(x.group(1) or x.group(2)) for x in _ORD_ANY_RE.finditer(utt)]
    rest = [n for n in ords if n not in neg]
    if len(rest) == 1:
        return rest[0]
    return None


def _cand_attrs(seg: str) -> dict[str, Any]:
    """从候选描述片段抽取可匹配属性: app/service/rtt/resolution/日期/时点(小时)。"""
    a: dict[str, Any] = {}
    for name in sorted(KNOWN_APPLICATIONS, key=len, reverse=True):
        if name in seg:
            a["app"] = name
            break
    for svc in ("视频通话", "开直播", "看直播", "云游戏", "视频", "会议"):
        if svc in seg:
            a["svc"] = svc
            break
    m = re.search(r"(\d+)\s*(?:ms|毫秒)", seg)
    if m:
        a["rtt"] = int(m.group(1))
    m = re.search(r"(\d{3,4})p", seg, re.I)
    if m:
        a["res"] = m.group(1).lower() + "p"
    m = re.search(r"(\d{1,2})月(\d{1,2})[号日]", seg)
    if m:
        a["md"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"(凌晨|早上|上午|中午|下午|晚上)(\d{1,2})点", seg)
    if m:
        a["hour"] = (_DAYPART_HOUR[m.group(1)] + int(m.group(2))) % 24
    return a


def _rec_match_score(attrs: dict[str, Any], rec: dict[str, Any]) -> int:
    """候选片段 vs 画像 record(recent_records 扁平格式) 的属性匹配分。"""
    s = 0
    if attrs.get("app") and attrs["app"] == rec.get("application_name"):
        s += 3
    if attrs.get("svc") and attrs["svc"] == rec.get("service_name"):
        s += 2
    if attrs.get("rtt") is not None:
        m = re.search(r"(\d+)", str(rec.get("rtt") or ""))
        if m and int(m.group(1)) == attrs["rtt"]:
            s += 3
    if attrs.get("res") and attrs["res"] == str(rec.get("resolution") or "").lower():
        s += 2
    ca = str(rec.get("completed_at") or "")
    m = re.search(r"(\d{1,2})月(\d{1,2})日(\d{1,2})时", ca)
    if m:
        if attrs.get("md") and attrs["md"] == (int(m.group(1)), int(m.group(2))):
            s += 2
        if attrs.get("hour") is not None and attrs["hour"] == int(m.group(3)):
            s += 2
    return s


def _parse_disamb_candidates(
    agent_reply: str | None, records: list[dict[str, Any]] | None
) -> list[dict[str, Any]] | None:
    """把 agent 话术里的记录候选按话术顺序映射到画像 records。"""
    if not agent_reply or not records:
        return None
    text = _DISAMB_QCUT_RE.split(agent_reply or "")[0]
    segs = [s.strip("，。；、 ") for s in _DISAMB_SPLIT_RE.split(text)]
    merged: list[str] = []
    for seg in segs:
        _mhead = re.match(r"^(\d{1,4}\s*(?:ms|毫秒)|\d{3,4}\s*[pP])[，,、]\s*(.+)$", seg)
        if _mhead and merged:
            merged[-1] += "，" + _mhead.group(1)
            seg = _mhead.group(2).strip("，。；、 ")
            if not seg:
                continue
        for pre in _DISAMB_PREFIX_RES:
            new = pre.sub("", seg, count=1)
            if new != seg:
                _ba, _aa = _cand_attrs(seg), _cand_attrs(new)
                if _ba.get("app") and not _aa.get("app"):
                    new = _ba["app"] + " " + new
                seg = new.strip("，。；、 ")
                break
        if not seg:
            continue
        a = _cand_attrs(seg)
        if set(a) == {"app"} and re.search(r"还是|哪条|哪一|对不上|吗|？|\?", seg):
            continue
        frag = set(a) <= {"rtt", "res"}
        if frag and merged:
            pa = _cand_attrs(merged[-1])
            if not (set(a) & set(pa)):
                merged[-1] += "，" + seg
                continue
        if set(a) <= {"md", "hour"} and merged:
            merged[-1] += "，" + seg
            continue
        merged.append(seg)
    declared = re.search(r"([两三四五六七八九\d])条", agent_reply)
    decl_n = None
    if declared:
        g = declared.group(1)
        decl_n = int(g) if g.isdigit() else (2 if g == "两" else _CN_NUM.index(g) + 1)
    cands: list[dict[str, Any]] = []
    taken: set[int] = set()
    for seg in merged:
        attrs = _cand_attrs(seg)
        if not any(k in attrs for k in ("app", "rtt", "res", "md")):
            continue
        best_i, best_s = None, 0
        for i, rec in enumerate(records):
            if i in taken:
                continue
            sc = _rec_match_score(attrs, rec)
            if sc > best_s:
                best_i, best_s = i, sc
        if best_i is None or best_s < 3:
            return None
        taken.add(best_i)
        cands.append(records[best_i])
    if len(cands) < 2:
        return None
    if decl_n is not None and decl_n != len(cands):
        return None
    return cands


# v24: 无约束"按上次记录"严格规则
_RECORD_REF_RE = re.compile(r"记录|历史")
_RECORD_REF_LOOSE_RE = re.compile(r"上次|之前")
_PREV_COREF_RE = re.compile(r"跟上次一样|和上次一样|像上次|照旧|跟之前一样|和之前一样")


def _record_constraint_hit(utterance: str) -> bool:
    """记录指代短语内是否有筛选条件 (app/service/日期/序数紧邻记录词)。"""
    utt = utterance or ""
    if re.search(r"第[一二三四五六七八九十\d]+\s*[条个]", utt):
        return True
    if re.search(r"\d{1,2}月\d{1,2}[号日]\s*的?\s*(那条|那次|那场|记录)", utt):
        return True
    if re.search(r"\d{1,2}月\d{1,2}[号日][^，。；]{0,8}?(那条|那次|那个|记录)", utt):
        return True
    names = sorted(
        KNOWN_APPLICATIONS | {"视频通话", "视频", "开直播", "看直播", "会议", "云游戏"},
        key=len,
        reverse=True,
    )
    for name in names:
        if re.search(rf"(上次|之前|历史)\s*的?\s*{re.escape(name)}", utt):
            return True
        if re.search(rf"{re.escape(name)}\s*的?\s*(那条|那次|那场)", utt):
            return True
    return False


def _needs_latest_record_rule(utterance: str, user_context: dict[str, Any] | None) -> bool:
    utt = utterance or ""
    recs = (user_context or {}).get("recent_records") or []
    if not recs:
        return False
    if _PREV_COREF_RE.search(utt):
        return False
    if re.search(r"选中|办过|参考|查.{0,18}(记录|历史)", utt):
        return False
    if not (_RECORD_REF_RE.search(utt) or _RECORD_REF_LOOSE_RE.search(utt)):
        return False
    return not _record_constraint_hit(utt)


_OOS_APPS = ("豆瓣", "京东", "知乎", "美团", "网易云音乐")
_SERVICE_WORDS = ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频")


def _utterance_mentions_app(utterance: str) -> bool:
    """话语是否明说任何 app (矩阵内 + 域外)。"""
    utt = utterance or ""
    return any(a in utt for a in list(KNOWN_APPLICATIONS) + list(_OOS_APPS))


def _pending_stubs(id_states: dict[str, dict[str, Any]] | None) -> list[tuple[str, str]]:
    """已有意图中"应用名待补"的桩: [(iid, 扁平描述)]。"""
    from case_format import flatten_params
    out = []
    for iid, v in sorted(
        (id_states or {}).items(),
        key=lambda x: int(x[0][1:]) if x[0].startswith("I") else 999,
    ):
        f = flatten_params(v or {})
        if not f.get("application_name"):
            desc = "/".join(x for x in ("?", f.get("service_name")) if x and x != "?") or "(空)"
            out.append((iid, f"应用待补/{desc}"))
    return out


_PROFILE_REF_RE = re.compile(r"平时|习惯")
_RECORD_REF_ANY_RE = re.compile(r"上次|最近.{0,4}条|记录|平时|习惯")


def _profile_slot_conflict(user_context: dict[str, Any] | None) -> tuple[str, str, int] | None:
    """画像"常用开始时段"与"常用保障时长"数值冲突 -> (时段, 时长, 时段跨度分钟)。"""
    prof = (user_context or {}).get("profile") or {}
    slot, dur = prof.get("常用开始时段"), prof.get("常用保障时长")
    if not slot or not dur:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", str(slot).strip())
    dm = re.fullmatch(r"(\d+)min", str(dur).strip())
    if not m or not dm:
        return None
    span = (int(m.group(3)) * 60 + int(m.group(4))) - (int(m.group(1)) * 60 + int(m.group(2)))
    if span <= 0:
        span += 24 * 60
    if span == int(dm.group(1)):
        return None
    return str(slot), str(dur), span


def _format_extracted_time_evidence(ev: Any) -> str | None:
    """将 time_extractor 抽取的绝对时间提及与持续时长格式化为 TIME EVIDENCE 块。"""
    lines: list[str] = []
    for mn in ev.mentions:
        abs_str = _tex._fmt(mn.dt) if mn.dt else f"时刻{mn.clock}(日期按语境定)"
        role = f" [{mn.role}]" if mn.role in ("start", "end") else ""
        lines.append(f'#   "{mn.raw}" -> {abs_str}{role}')
    for raw, dmin in ev.durations:
        lines.append(f'#   "{raw}" -> duration={dmin}min')
    if ev.start is not None and ev.end is not None and not ev.has_contradiction:
        dur_txt = f", duration={ev.duration_min}min" if ev.duration_min is not None else ""
        lines.append(f"# 组装(一致): start={_tex._fmt(ev.start)}, end={_tex._fmt(ev.end)}{dur_txt}")
    return format_time_evidence_block(lines) if lines else None


def _collect_time_rules(
    features: Any,
    utterance: str,
    user_context: dict[str, Any] | None,
    ev: Any,
) -> list[str]:
    """收集矛盾时间、画像时段冲突、明说时间优先及候选时段未定规则段。"""
    rules: list[str] = []
    if features.time_rule_contradiction and ev is not None and ev.has_contradiction:
        rules.append(
            format_time_contradiction_rule(_tex._fmt(ev.start), _tex._fmt(ev.end), ev.duration_min)
        )
    if features.time_rule_profile_slot and _PROFILE_REF_RE.search(utterance) and "时段" in utterance:
        conflict = _profile_slot_conflict(user_context)
        if conflict:
            slot, dur, span = conflict
            rules.append(format_time_profile_conflict_rule(slot, dur, span))
    if (
        features.time_rule_explicit_first
        and ev is not None
        and any(mn.dt for mn in ev.mentions)
        and _RECORD_REF_ANY_RE.search(utterance)
    ):
        rules.append(TIME_EXPLICIT_FIRST_RULE)
    if features.time_rule_candidate and ev is not None and ev.has_candidate:
        if ev.start is not None or ev.end is not None:
            rules.append(TIME_PARTIAL_CANDIDATE_RULE)
        else:
            rules.append(TIME_UNDECIDED_CANDIDATE_RULE)
    return rules


def _build_time_hint(
    features: Any,
    utterance: str,
    reference_time: str,
    user_context: dict[str, Any] | None,
) -> tuple[str | None, Any]:
    """组装时间相关 prompt 注入段 (TIME ANCHOR / TIME EVIDENCE / TIME RULE)。"""
    if not (
        features.time_anchor
        or features.time_extractor
        or features.time_rule_contradiction
        or features.time_rule_profile_slot
        or features.time_rule_explicit_first
        or features.time_rule_candidate
    ):
        return None, None
    ref_dt = _tex.parse_reference_time(reference_time)
    if ref_dt is None:
        return None, None

    need_ev = (
        features.time_extractor
        or features.time_rule_contradiction
        or features.time_rule_explicit_first
        or features.time_rule_candidate
    )
    ev = _tex.extract(utterance, ref_dt) if need_ev else None

    parts: list[str] = []
    if features.time_anchor:
        table = _tex.build_date_table(ref_dt, utterance)
        if table:
            rows = "、".join(f"{w}={d}" for w, d in table)
            parts.append(format_time_anchor_block(rows))
    if features.time_extractor and ev is not None:
        ev_block = _format_extracted_time_evidence(ev)
        if ev_block:
            parts.append(ev_block)
    parts.extend(_collect_time_rules(features, utterance, user_context, ev))

    return ("\n".join(parts) if parts else None), ev


def build_record_hints(
    pipe: Any,
    utterance: str,
    user_context: dict[str, Any] | None,
    agent_reply: str | None,
    disamb_keys: list[str] | None,
) -> tuple[str | None, str | None]:
    """构建 record_rule_hint 与 record_ev_hint。"""
    record_rule_hint: str | None = None
    record_ev_hint: str | None = None
    if pipe.features.record_selector:
        import record_selector as _rse
        ev = _rse.record_evidence(
            utterance,
            user_context,
            agent_reply,
            disambiguation_active=bool(disamb_keys is not None) or getattr(pipe, "_session_is_t33", False),
        )
        if ev is not None:
            record_ev_hint = _rse.render_hint(ev)
        if (
            record_ev_hint is None
            and pipe.features.record_rule
            and disamb_keys is None
            and _needs_latest_record_rule(utterance, user_context)
        ):
            r0 = (user_context or {}).get("recent_records", [{}])[0]
            record_rule_hint = (
                f"application_name={r0.get('application_name')}, service_name={r0.get('service_name')}, "
                f"resolution={r0.get('resolution')}, rtt={r0.get('rtt')}, "
                f"completed_at={r0.get('completed_at')}"
            )
    elif (
        pipe.features.record_rule
        and disamb_keys is None
        and _needs_latest_record_rule(utterance, user_context)
    ):
        r0 = (user_context or {}).get("recent_records", [{}])[0]
        record_rule_hint = (
            f"application_name={r0.get('application_name')}, service_name={r0.get('service_name')}, "
            f"resolution={r0.get('resolution')}, rtt={r0.get('rtt')}, "
            f"completed_at={r0.get('completed_at')}"
        )
    return record_rule_hint, record_ev_hint


def build_quality_hint(
    features: Any,
    utterance: str,
    record_rule_hint: str | None,
    record_ev_hint: str | None,
) -> str | None:
    """构建 v27.2.1 QUALITY HINT 画质词精确映射高亮。"""
    if not getattr(features, "quality_hint", False):
        return None
    from schema import KNOWN_APPLICATIONS as _KA, QUALITY_MAP as _QMAP
    _qw_set = {m.group(1) for m in re.finditer("(超清|高清|标清|蓝光|流畅)", utterance)}
    _apps_in_utt = [a for a in _KA if a in utterance]
    _hits = []
    for _qw in _qw_set:
        _cands: dict[str, list[tuple[str, str]]] = {}
        for (a, sv, w), val in _QMAP.items():
            if w == _qw and a in _apps_in_utt:
                _cands.setdefault(a, []).append((sv, val))
        for a, sv_vals in _cands.items():
            if len(sv_vals) == 1:
                _hits.append((a, sv_vals[0][0], _qw, sv_vals[0][1]))
            else:
                _best = None
                for sv, val in sv_vals:
                    _pos_svc = utterance.find(sv)
                    _pos_w = utterance.find(_qw)
                    if _pos_svc >= 0:
                        _d = abs(_pos_svc - _pos_w)
                        if _best is None or _d < _best[0]:
                            _best = (_d, sv, val)
                if _best:
                    _hits.append((a, _best[1], _qw, _best[2]))
        if not _cands and (record_rule_hint or record_ev_hint):
            _src = record_rule_hint or record_ev_hint or ""
            _m = re.search(r"application_name=([^,]+), service_name=([^,]+)", _src)
            if _m:
                _ra, _rsv = _m.group(1).strip(), _m.group(2).strip()
                _val = _QMAP.get((_ra, _rsv, _qw))
                if _val:
                    _hits.append((_ra, _rsv, _qw, _val))
    if not _hits:
        return None
    _qlines = [f'#   {a}/{sv} 的"{w}" = {v}' for a, sv, w, v in _hits]
    return format_quality_hint_block(_qlines)


def _fv(_v: Any, _k: str) -> Any:
    """从嵌套或扁平状态字典提取标量槽位值。"""
    _x = (_v or {}).get(_k)
    if isinstance(_x, dict):
        if "value" in _x:
            return _x.get("value")
        for _sub in ("min_value", "max_value"):
            _y = _x.get(_sub)
            if isinstance(_y, dict) and "value" in _y:
                return _y.get("value")
        return None
    return _x


def _format_intent_time_keep(idst: dict[str, Any], iid: str | None) -> str:
    """格式化已有意图的时间字段保护提示行。"""
    if not iid:
        return ""
    _sv = idst.get(iid) or {}
    _ts = _sv.get("timestamp") or {}
    _tp = []
    for _tk, _tn in (
        ("start_timestamp", "start"),
        ("end_timestamp", "end"),
        ("duration", "duration"),
    ):
        _tv = _fv(_ts, _tk)
        if _tv:
            _tp.append(f"{_tn}={_tv}")
    if not _tp:
        return ""
    return (
        "#    该意图时间字段已是用户明说值 ("
        + ", ".join(_tp)
        + ")——落地候选后【原样保留, 禁止用记录的时间覆盖, 也禁止从画像时段编造】。\n"
    )


def _gather_memory_candidates(
    pipe: Any,
    utterance: str,
    user_context: dict[str, Any] | None,
    agent_reply: str | None,
    disamb_cands: list[dict[str, Any]] | None,
    judge: dict[str, Any],
) -> tuple[list[dict[str, Any]], int | None, dict[str, Any] | None]:
    """收集并对齐客服话术候选列表与用户选中的目标记录。"""
    listing = disamb_cands or getattr(pipe, "_cand_listing", None) or []
    if disamb_cands is None and agent_reply and pipe.features.candidate_map:
        recs = (user_context or {}).get("recent_records") or []
        rl = _resolve_listing(pipe.llm, agent_reply, recs)
        if rl:
            listing = rl
        else:
            pl = _llm_parse_listing(pipe.llm, agent_reply, recs)
            if pl:
                listing = pl

    pick_i = judge.get("pick")
    if pick_i is None:
        pick_i = _parse_pick_ord(utterance)
    if pick_i is None and _ORD_LATEST_RE.search(utterance) and listing:
        pick_i = (
            max(
                range(len(listing)),
                key=lambda _i: str(listing[_i].get("completed_at") or ""),
            )
            + 1
        )
    pick_rec = (
        listing[pick_i - 1]
        if (pick_i is not None and 1 <= pick_i <= len(listing))
        else None
    )
    return listing, pick_i, pick_rec


def _detect_candidate_conflicts(
    idst: dict[str, Any],
    listing: list[dict[str, Any]],
    pick_rec: dict[str, Any] | None,
) -> tuple[list[str], list[str], list[str]]:
    """检测当前已有意图中的空应用桩、未被选中的抢跑占位意图及悬空待补字段。"""
    stubs = [k for k, v in idst.items() if not _fv(v, "application_name")]
    placeholders: list[str] = []
    if pick_rec is not None:
        pk = (pick_rec.get("application_name"), pick_rec.get("service_name"))
        listing_pairs = {(r.get("application_name"), r.get("service_name")) for r in listing}
        for k, v in idst.items():
            kv = (_fv(v, "application_name"), _fv(v, "service_name"))
            if kv[0] and kv in listing_pairs and kv != pk:
                placeholders.append(k)

    miss_lines: list[str] = []
    if pick_rec is not None and not stubs and not placeholders:
        for k, v in idst.items():
            if not _fv(v, "application_name"):
                continue
            for fk in ("rtt", "resolution"):
                if not _fv(v, fk):
                    pr = pick_rec.get(fk)
                    pv = (
                        ((pr or {}).get("max_value") if fk == "rtt" else (pr or {}).get("min_value"))
                        if isinstance(pr, dict)
                        else pr
                    )
                    if pv:
                        miss_lines.append(
                            f'#    意图 {k} 的 {fk} 悬空 → 直接从选中记录补: state.{fk} = "{pv}";\n'
                        )
    return stubs, placeholders, miss_lines


def _format_candidate_prompt_block(
    idst: dict[str, Any],
    listing: list[dict[str, Any]],
    pick_i: int | None,
    pick_rec: dict[str, Any] | None,
    judge: dict[str, Any],
    stubs: list[str],
    placeholders: list[str],
    miss_lines: list[str],
) -> str:
    """格式化候选选择轮的候选明细、选中记录摘要与状态落地分支块。"""
    anchor = ""
    if listing:
        alines = []
        for i, rr in enumerate(listing, 1):
            rtt = (rr.get("rtt") or {}).get("max_value") if isinstance(rr.get("rtt"), dict) else rr.get("rtt")
            res = (
                (rr.get("resolution") or {}).get("min_value")
                if isinstance(rr.get("resolution"), dict)
                else rr.get("resolution")
            )
            alines.append(
                f"#   第{i}条: application_name={rr.get('application_name')}, "
                f"service_name={rr.get('service_name')}, resolution={res}, "
                f"rtt={rtt}, completed_at={rr.get('completed_at')}"
            )
        anchor = (
            '# 候选明细 (与客服上轮列出顺序一致, 用户说的"第N条"按此对齐, 禁止数 records 列表):\n'
            + "\n".join(alines)
            + "\n"
        )

    pick_txt = ""
    if pick_rec is not None:
        prtt = (
            (pick_rec.get("rtt") or {}).get("max_value")
            if isinstance(pick_rec.get("rtt"), dict)
            else pick_rec.get("rtt")
        )
        pres = (
            (pick_rec.get("resolution") or {}).get("min_value")
            if isinstance(pick_rec.get("resolution"), dict)
            else pick_rec.get("resolution")
        )
        pick_txt = (
            f"# 系统解析: 用户本轮选中 = 第{pick_i}条 ("
            f"application_name={pick_rec.get('application_name')}, "
            f"service_name={pick_rec.get('service_name')}, resolution={pres}, rtt={prtt})。\n"
        )

    if stubs:
        scope = judge.get("scope") or ["app", "svc", "res", "rtt"]
        sc_map = {"app": "application_name", "svc": "service_name", "res": "resolution", "rtt": "rtt"}
        sc_flds = [sc_map[s] for s in scope if s in sc_map]
        t_keep = _format_intent_time_keep(idst, stubs[0] if len(stubs) == 1 else None)
        if set(scope) == {"app", "svc", "res", "rtt"}:
            branch = format_candidate_stub_full_branch(stubs, t_keep)
        else:
            fld_txt = "/".join(sc_flds)
            assigns = "；".join(
                f"state.{f} = 选中记录的{f}"
                + (" (rtt.max_value)" if f == "rtt" else " (resolution.min_value)" if f == "resolution" else "")
                for f in sc_flds
            )
            branch = format_candidate_stub_scoped_branch(
                json.dumps(scope, ensure_ascii=False), fld_txt, assigns, t_keep
            )
    elif placeholders:
        t_keep = _format_intent_time_keep(idst, placeholders[0] if len(placeholders) == 1 else None)
        branch = format_candidate_placeholder_branch(placeholders, t_keep)
    else:
        branch = format_candidate_existing_app_branch(miss_lines)

    return format_candidate_selection_mode_block(anchor, pick_txt, branch)


def build_candidate_mode_hint(
    pipe: Any,
    utterance: str,
    user_context: dict[str, Any] | None,
    agent_reply: str | None,
    disamb_cands: list[dict[str, Any]] | None,
) -> tuple[str | None, bool]:
    """构建 v27.2+ CANDIDATE MODE (列举轮 / 选择轮) 提示段。返回 (cand_mode_hint, listed_this_turn)。"""
    if not (getattr(pipe.features, "candidate_mode", False) and getattr(pipe, "_session_is_t33", False)):
        return None, False

    import record_selector as _rsmod
    _pending = getattr(pipe, "_cand_listing", None)
    _judge: dict[str, Any] = {}
    if _pending:
        _judge = _cl_judge_selection(pipe.llm, utterance, _pending)
        if not _judge["is_selection"]:
            pipe._cand_listing = None
            _pending = None

    if _pending:
        idst = getattr(pipe, "_id_states", None) or {}
        listing, pick_i, pick_rec = _gather_memory_candidates(
            pipe, utterance, user_context, agent_reply, disamb_cands, _judge
        )
        pipe._cand_listing = None
        stubs, placeholders, miss_lines = _detect_candidate_conflicts(idst, listing, pick_rec)
        cand_mode_hint = _format_candidate_prompt_block(
            idst, listing, pick_i, pick_rec, _judge, stubs, placeholders, miss_lines
        )
        return cand_mode_hint, False

    _ev = _rsmod.record_evidence(utterance, user_context, agent_reply, disambiguation_active=True)
    if _ev is not None and _ev.mode == "defer" and len(_ev.matched) >= 2:
        pipe._cand_listing = _ev.matched
        _lines = []
        for _i, _rr in enumerate(_ev.matched, 1):
            _rtt = (
                (_rr.get("rtt") or {}).get("max_value")
                if isinstance(_rr.get("rtt"), dict)
                else _rr.get("rtt")
            )
            _res = (
                (_rr.get("resolution") or {}).get("min_value")
                if isinstance(_rr.get("resolution"), dict)
                else _rr.get("resolution")
            )
            _lines.append(
                f"#   第{_i}条: application_name={_rr.get('application_name')}, "
                f"service_name={_rr.get('service_name')}, resolution={_res}, "
                f"rtt={_rtt}, completed_at={_rr.get('completed_at')}"
            )
        has_pref = bool(_rsmod.PREFERENTIAL_RE.search(utterance))
        return format_candidate_listing_mode_block(_lines, has_pref), True
    return None, False


def build_pending_app_hint(
    features: Any, utterance: str, id_states: dict[str, dict[str, Any]] | None
) -> str | None:
    """构建 v26.3 P1 PENDING-APP RULE 应用名未明说规则段。"""
    if not (features.pending_app_rule and not _utterance_mentions_app(utterance)):
        return None
    stubs = _pending_stubs(id_states)
    stub_txt = "、".join(f"{iid}({desc})" for iid, desc in stubs) if stubs else "无"
    id_list = "、".join(sorted((id_states or {}).keys())) if (id_states or {}) else "无"
    return format_pending_app_rule_block(id_list, stub_txt)
