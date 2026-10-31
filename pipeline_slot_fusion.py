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
            parts.append(
                "# === TIME ANCHOR (本轮日期锚点, 系统已预计算) ===\n"
                f"# {rows}\n"
                "# 相对日期一律按上表查取, 禁止自行加减天数推算。\n"
            )
    if features.time_extractor and ev is not None:
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
        if lines:
            parts.append(
                "# === TIME EVIDENCE (本轮话语明说时间的系统预解析, 直接引用勿重算) ===\n"
                + "\n".join(lines)
                + "\n"
            )
    if features.time_rule_contradiction and ev is not None and ev.has_contradiction:
        parts.append(
            "# === TIME RULE (检测到明说时间互相矛盾, 本轮生效, 优先级最高) ===\n"
            f"# 系统解析: start={_tex._fmt(ev.start)}, end={_tex._fmt(ev.end)}"
            + (f", duration={ev.duration_min}min" if ev.duration_min is not None else "")
            + ", 存在矛盾。\n"
            "# 按口径照字面录入用户明说值, 禁止自行调和/翻转/倒推覆盖, 等待用户后续更正。\n"
        )
    if features.time_rule_profile_slot and _PROFILE_REF_RE.search(utterance) and "时段" in utterance:
        conflict = _profile_slot_conflict(user_context)
        if conflict:
            slot, dur, span = conflict
            parts.append(
                "# === TIME RULE (画像时段/时长不一致, 本轮生效, 优先级最高) ===\n"
                f'# 画像"常用开始时段"={slot}(跨度{span}min) 与 "常用保障时长"={dur} 不一致。\n'
                '# 按口径一律以"常用开始时段"为准: start/end 取时段起止, '
                f'duration={span}min, 忽略"常用保障时长"。\n'
            )
    if (
        features.time_rule_explicit_first
        and ev is not None
        and any(mn.dt for mn in ev.mentions)
        and _RECORD_REF_ANY_RE.search(utterance)
    ):
        parts.append(
            "# === TIME RULE (本轮明说时间优先, 本轮生效, 优先级最高) ===\n"
            "# 用户本轮明说了时间(见 TIME EVIDENCE)。时间槽(start/end/duration)一律以\n"
            "# 本轮明说为准, 禁止取 records 的 completed_at 或画像时段覆盖/替代明说时间。\n"
        )
    if features.time_rule_candidate and ev is not None and ev.has_candidate:
        if ev.start is not None or ev.end is not None:
            parts.append(
                "# === TIME RULE (部分槽位候选未定, 本轮生效, 优先级最高) ===\n"
                "# 用户话语含未定词, 但时间本体已明说且无歧义 (见 TIME EVIDENCE 组装结论)。\n"
                "# 未定的只是其他槽位 (如应用/时延)——start/end 按 TIME EVIDENCE 组装结论\n"
                "# 【照常落地】, 不要因其他槽位未定而把时间一并留空。\n"
            )
        else:
            parts.append(
                "# === TIME RULE (候选时段未定, 本轮生效, 优先级最高) ===\n"
                '# 用户给出多个候选时段且尚未最终确定("或者/还没定/不确定")。\n'
                "# start/end 一律留空, 禁止自行挑选某个候选落槽; 只登记无歧义字段(如明说 duration)。\n"
            )

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
    return (
        "# === QUALITY HINT (画质词精确映射, 系统查表, 优先级最高) ===\n"
        + "\n".join(_qlines)
        + "\n# 上表为标准映射的精确值, 直接采用; 禁止使用默认值覆盖。\n"
    )


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

    from record_selector import record_evidence as _rev
    _pending = getattr(pipe, "_cand_listing", None)
    _judge: dict[str, Any] = {}
    if _pending:
        _judge = _cl_judge_selection(pipe.llm, utterance, _pending)
        if not _judge["is_selection"]:
            pipe._cand_listing = None
            _pending = None

    if _pending:
        def _fv(_v: Any, _k: str) -> Any:
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

        _idst = getattr(pipe, "_id_states", None) or {}

        def _time_keep(_iid: str | None) -> str:
            if not _iid:
                return ""
            _sv = _idst.get(_iid) or {}
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
            return (
                "#    该意图时间字段已是用户明说值 ("
                + ", ".join(_tp)
                + ")——落地候选后【原样保留, 禁止用记录的时间覆盖, 也禁止从画像时段编造】。\n"
                if _tp
                else ""
            )

        _stubs = [k for k, v in _idst.items() if not _fv(v, "application_name")]
        _listing = disamb_cands or getattr(pipe, "_cand_listing", None) or []
        if disamb_cands is None and agent_reply and pipe.features.candidate_map:
            _recs = (user_context or {}).get("recent_records") or []
            _rl = _resolve_listing(pipe.llm, agent_reply, _recs)
            if _rl:
                _listing = _rl
            else:
                _pl = _llm_parse_listing(pipe.llm, agent_reply, _recs)
                if _pl:
                    _listing = _pl
        _anchor = ""
        if _listing:
            _alines = []
            for _i, _rr in enumerate(_listing, 1):
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
                _alines.append(
                    f"#   第{_i}条: application_name={_rr.get('application_name')}, "
                    f"service_name={_rr.get('service_name')}, resolution={_res}, "
                    f"rtt={_rtt}, completed_at={_rr.get('completed_at')}"
                )
            _anchor = (
                '# 候选明细 (与客服上轮列出顺序一致, 用户说的"第N条"按此对齐, 禁止数 records 列表):\n'
                + "\n".join(_alines)
                + "\n"
            )
        _pick_i = _judge.get("pick")
        if _pick_i is None:
            _pick_i = _parse_pick_ord(utterance)
        if _pick_i is None and _ORD_LATEST_RE.search(utterance) and _listing:
            _pick_i = (
                max(
                    range(len(_listing)),
                    key=lambda _i: str(_listing[_i].get("completed_at") or ""),
                )
                + 1
            )
        _pick_rec = (
            _listing[_pick_i - 1]
            if (_pick_i is not None and 1 <= _pick_i <= len(_listing))
            else None
        )
        _pick_txt = ""
        if _pick_rec is not None:
            _prtt = (
                (_pick_rec.get("rtt") or {}).get("max_value")
                if isinstance(_pick_rec.get("rtt"), dict)
                else _pick_rec.get("rtt")
            )
            _pres = (
                (_pick_rec.get("resolution") or {}).get("min_value")
                if isinstance(_pick_rec.get("resolution"), dict)
                else _pick_rec.get("resolution")
            )
            _pick_txt = (
                f"# 系统解析: 用户本轮选中 = 第{_pick_i}条 ("
                f"application_name={_pick_rec.get('application_name')}, "
                f"service_name={_pick_rec.get('service_name')}, resolution={_pres}, rtt={_prtt})。\n"
            )
        pipe._cand_listing = None
        _placeholders: list[str] = []
        if _pick_rec is not None:
            _pk = (_pick_rec.get("application_name"), _pick_rec.get("service_name"))
            for _k, _v in _idst.items():
                _kv = (_fv(_v, "application_name"), _fv(_v, "service_name"))
                if (
                    _kv[0]
                    and _kv in {(r.get("application_name"), r.get("service_name")) for r in _listing}
                    and _kv != _pk
                ):
                    _placeholders.append(_k)
        if _stubs:
            _scope = _judge.get("scope") or ["app", "svc", "res", "rtt"]
            _sc_map = {
                "app": "application_name",
                "svc": "service_name",
                "res": "resolution",
                "rtt": "rtt",
            }
            _sc_flds = [_sc_map[s] for s in _scope if s in _sc_map]
            if set(_scope) == {"app", "svc", "res", "rtt"}:
                _branch = (
                    "# 系统核验: 当前桩意图 "
                    + "、".join(_stubs)
                    + " 的 application_name 为空(用户从未明说 app)。\n"
                    + "# 本轮选中候选后, 必须把选中记录的 application_name/service_name/resolution/rtt\n"
                    + "# 【全量落地】到该桩意图(段头写其 id); 时间等用户明说字段保持不变。\n"
                    + _time_keep(_stubs[0] if len(_stubs) == 1 else None)
                )
            else:
                _fld_txt = "/".join(_sc_flds)
                _assigns = "；".join(
                    (
                        f"state.{_f} = 选中记录的{_f}"
                        + (
                            " (rtt.max_value)"
                            if _f == "rtt"
                            else " (resolution.min_value)"
                            if _f == "resolution"
                            else ""
                        )
                    )
                    for _f in _sc_flds
                )
                _branch = (
                    "# 系统核验: 用户本轮仅要求按选中记录取部分字段 (语义判定 scope="
                    + json.dumps(_scope, ensure_ascii=False)
                    + ")。\n"
                    + "# 本轮【只允许】把 "
                    + _fld_txt
                    + " 写入已有意图 (段头写其 id): "
                    + _assigns
                    + ";\n"
                    + "# 【严禁】落地未列出的字段, 尤其严禁把选中记录的 application_name/service_name\n"
                    + "#  当作新保障意图办理——选中记录只是字段来源, 不是新办业务。\n"
                    + '# 【记录指代已消解】用户明确选定了候选, RECORD RULE 的"悬空待消歧"条款\n'
                    + '#  自本轮起失效——scope 列出的字段【必须照常落地】, 不得再以"悬空"为由拒写;\n'
                    + "#  也【严禁新建意图】承接选中记录——字段写入已有意图, 不另办业务。\n"
                    + _time_keep(_stubs[0] if len(_stubs) == 1 else None)
                )
        elif _placeholders:
            _branch = (
                "# 系统核验: 意图 "
                + "、".join(_placeholders)
                + " 的 app/svc 是候选列表中【未被选中】的候选 (t1 抢跑占位, 非用户明说)。\n"
                + "# 按用户本轮选择, 把该意图身份【改写为选中候选】并全量落地 app/svc/resolution/rtt;\n"
                + "# 时间等用户明说字段保持不变; 未被选中的候选不再保留, 也不另建意图。\n"
                + _time_keep(_placeholders[0] if len(_placeholders) == 1 else None)
            )
        else:
            _miss_txt = ""
            if _pick_rec is not None:
                _mlines = []
                for _k, _v in _idst.items():
                    if not _fv(_v, "application_name"):
                        continue
                    for _fk in ("rtt", "resolution"):
                        if not _fv(_v, _fk):
                            _pr = _pick_rec.get(_fk)
                            _pv = (
                                ((_pr or {}).get("max_value") if _fk == "rtt" else (_pr or {}).get("min_value"))
                                if isinstance(_pr, dict)
                                else _pr
                            )
                            if _pv:
                                _mlines.append(
                                    f'#    意图 {_k} 的 {_fk} 悬空 → 直接从选中记录补: state.{_fk} = "{_pv}";\n'
                                )
                if _mlines:
                    _miss_txt = (
                        "# 逐意图悬空字段补齐指令 (选中记录只是字段来源; 其 app/svc 与意图不一致是正常的,\n"
                        "# 禁止因身份不一致而拒绝补字段):\n" + "".join(_mlines)
                    )
            _branch = (
                "# 系统核验: 当前各意图的 application_name 均为用户此前明说。\n"
                + "# 本轮选中候选后, 【严禁改写任何意图的身份】, 只把选中记录中此前悬空的字段\n"
                + "# (如 rtt)写入对应意图; 用户明说的其他字段严禁被记录值覆盖。\n"
                + _miss_txt
            )
        cand_mode_hint = (
            "# === CANDIDATE MODE (选择轮, 本轮生效, 优先级最高) ===\n"
            "# 用户在选定此前列出的候选记录。必须遵守:\n"
            + _anchor
            + _pick_txt
            + _branch
            + "# 2. 未被选中的候选: 不再保留, 也不为其输出任何段;\n"
            "# 3. 记录的 completed_at 是记录完成时间, 不是保障时间, 禁止当作 start/end。\n"
        )
        return cand_mode_hint, False

    import record_selector as _rsmod
    _ev = _rev(utterance, user_context, agent_reply, disambiguation_active=True)
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
        _prefer = (
            '# 5. 用户话语里的偏好 ("最好是X那条") 不是最终选择, 仍须全部列出等用户确认;\n'
            "#    禁止只落地偏好那条。\n"
            if _rsmod.PREFERENTIAL_RE.search(utterance)
            else ""
        )
        cand_mode_hint = (
            "# === CANDIDATE MODE (候选列举轮, 本轮生效, 优先级最高) ===\n"
            "# 用户要求按记录办理但系统核验候选不唯一。候选列表如下 (按客服话术顺序):\n"
            + "\n".join(_lines)
            + "\n"
            "# 必须遵守:\n"
            "# 1. 本轮【不要输出 # candidate: 段】; 记录身份字段 (application_name/service_name/\n"
            "#    resolution/rtt) 一律悬空, 不落地到任何保障意图;\n"
            "# 2. 用户本轮明说的字段 (如时间/画质) 正常落地到意图段 (意图段只含明说字段);\n"
            "# 3. # summary 写明: 约束命中 N 条候选, 等待用户选择。\n"
            + _prefer
        )
        return cand_mode_hint, True
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
    return (
        "# === PENDING-APP RULE (应用名未明说, 本轮生效, 优先级最高) ===\n"
        "# 用户本轮话语没有提到任何具体应用名。必须遵守:\n"
        "# 1. 禁止猜测 application_name: 不得从画像 records、受理矩阵或 schema 列表里挑一个填上;\n"
        "#    已知的服务/时间/画质/时延等字段照常提取赋值。\n"
        f"# 2. 当前已有意图: {id_list}; 其中待补应用名的桩: {stub_txt}。\n"
        "#    【默认更新, 谨慎新建】本轮无 app 时, 若话语内容(补充的参数/时段/画质/时延,\n"
        "#    或确认/改口)对应某个已有意图的未收齐槽位或延续其语境, 必须段头写该意图的 id\n"
        "#    (已有意图唯一时就是它; 多个时按参数对齐), 只赋本轮新给的字段;\n"
        "#    application_name 必须用 coreference 沿用(state.application_name = state.application_name),\n"
        "#    绝对不能让已有意图的 application_name/service_name 因本轮不赋值而丢失或被搁置。\n"
        "#    仅当用户明确另提一个全新保障(新服务词+新时段且与所有已有意图都对不上)才写 # intent: new;\n"
        "#    拿不准是更新还是新建时, 一律按更新处理。\n"
        "# 3. # summary 写明: 应用名用户未明说(更新已有意图/新建留空)。\n\n"
    )
