# -*- coding: utf-8 -*-
"""record_selector.py: v27.1 记录指代预判器 (生成前证据注入, 与 time_extractor 同构)。

设计 (docs/plan_record_selector_20260922.md):
  判定树在 prompt 组装前运行, 把"该不该取记录/取哪条/该不该等消歧"三个口径问题
  用确定性规则预判, 以 RECORD EVIDENCE / RECORD RULE 注入。模型保留决策权,
  本模块只提供数据口径结论。严禁在 validate 之后改写模型输出 (v9.0/v10.0 已证伪)。

入口:
  record_evidence(utterance, user_context, agent_reply, in_disambiguation)
    -> RecordEvidence | None
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from prompts.memory_hint_templates import format_record_defer_rule_block

RECORD_REF_RE = re.compile(r"记录|历史")
RECORD_REF_LOOSE_RE = re.compile(r"上次|之前|最近")
PREV_COREF_RE = re.compile(r"跟上次一样|和上次一样|像上次|照旧|跟之前一样|和之前一样")
PRESELECT_RE = re.compile(r"选中|办过|参考|查.{0,18}(?:记录|历史)|最终选中的?")
AGENT_DISAMB_RE = re.compile(r"哪一条|哪个|选哪|按哪|无法唯一|无法确定|您.{0,6}(选|按)|还是")
PREFERENTIAL_RE = re.compile(r"(最好|倾向|想要|优先)[^，。；]{0,8}(是|那)?")


@dataclass
class RecordEvidence:
    mode: str                    # "unique" / "latest" / "defer"
    constraint: str              # 用户约束原文 (空=无)
    matched: list[dict[str, Any]] = field(default_factory=list)
    target_record: dict[str, Any] | None = None
    reason: str = ""


def _extract_filters(
    utterance: str,
    app_names: tuple[str, ...],
    svc_names: tuple[str, ...],
) -> dict[str, str]:
    """从话语提取记录筛选条件 (app/service/日期)。复用 v24.1 紧邻判定思想。"""
    f: dict[str, str] = {}
    for name in sorted(app_names, key=len, reverse=True):
        if name not in utterance:
            continue
        if (
            re.search(rf"{re.escape(name)}[^，。；]{{0,14}}(记录|历史|那条|那次)", utterance)
            or re.search(rf"(上次|之前|历史|最近)[^，。；]{{0,4}}的?{re.escape(name)}", utterance)
        ):
            f["application_name"] = name
            break
    for name in svc_names:
        _u = utterance
        if f.get("application_name"):
            _u = _u.replace(f["application_name"], "")
        if (
            re.search(rf"{re.escape(name)}[^，。；]{{0,14}}(记录|历史|那条|那次)", _u)
            or re.search(rf"(上次|之前|历史|最近)[^，。；]{{0,8}}的?{re.escape(name)}", _u)
        ):
            f["service_name"] = name
            break
    m = re.search(r"(\d{1,2})月(\d{1,2})[号日][^，。；]{0,10}?(完成的那|的那条|那次|记录)", utterance)
    if m:
        f["date"] = f"{int(m.group(1)):02d}月{int(m.group(2)):02d}"
    return f


def _filter_records(records: list[dict[str, Any]], filters: dict[str, str]) -> list[dict[str, Any]]:
    out = records
    if filters.get("application_name"):
        out = [r for r in out if r.get("application_name") == filters["application_name"]]
    if filters.get("service_name"):
        out = [r for r in out if r.get("service_name") == filters["service_name"]]
    if filters.get("date"):
        out = [r for r in out if filters["date"] in (r.get("completed_at") or "")]
    return out


def _fmt_record(r: dict[str, Any]) -> str:
    rtt = (r.get("rtt") or {}).get("max_value") if isinstance(r.get("rtt"), dict) else r.get("rtt")
    return (
        f"application_name={r.get('application_name')}, service_name={r.get('service_name')}, "
        f"rtt={rtt}, completed_at={r.get('completed_at')}"
    )


def _build_disambig_union_candidates(
    records: list[dict[str, Any]],
    matched: list[dict[str, Any]],
    filters: dict[str, str],
    utt: str,
) -> list[dict[str, Any]]:
    """当消歧会话中多条件 AND 交集 <=1 时, 按各约束各自命中的最近一条取并集。"""
    if not filters or len(matched) > 1:
        return matched
    picked: set[int] = set()
    for _k in ("application_name", "service_name"):
        if filters.get(_k):
            for _i, _r in enumerate(records):
                if _r.get(_k) == filters[_k]:
                    picked.add(_i)
                    break
    if filters.get("date"):
        for _i, _r in enumerate(records):
            if filters["date"] in (_r.get("completed_at") or ""):
                picked.add(_i)
                break
    if RECORD_REF_LOOSE_RE.search(utt):
        picked.add(0)
    return [r for _i, r in enumerate(records) if _i in picked]


def record_evidence(
    utterance: str,
    user_context: dict[str, Any] | None,
    agent_reply: str | None = None,
    disambiguation_active: bool = False,
) -> RecordEvidence | None:
    """记录指代预判主入口。无指代/沿用式/消歧选择轮返回 None。"""
    utt = utterance or ""
    if not (RECORD_REF_RE.search(utt) or RECORD_REF_LOOSE_RE.search(utt)):
        return None
    if PREV_COREF_RE.search(utt):
        return None
    records = (user_context or {}).get("recent_records") or []
    if not records:
        return None

    from schema import KNOWN_APPLICATIONS

    svc_names = ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频")
    filters = _extract_filters(utt, tuple(KNOWN_APPLICATIONS), svc_names)
    matched = _filter_records(records, filters)

    preselect = bool(PRESELECT_RE.search(utt))
    agent_asked = bool(agent_reply and AGENT_DISAMB_RE.search(agent_reply))
    choice_turn = bool(
        re.search(r"^.{0,6}(第[一二三四五六七八九十\d]+\s*[条个]|选第|不选第)", utt)
        or re.search(r"第[一二三四五六七八九十\d]+\s*[条个](吧|就行|来|。|$)", utt)
        or re.search(r"^(就|那)按第", utt)
    )
    if choice_turn:
        return None

    if disambiguation_active:
        cand = _build_disambig_union_candidates(records, matched, filters, utt)
        preferential = bool(PREFERENTIAL_RE.search(utt))
        ambiguous = preselect or agent_asked or preferential or len(cand) != 1
        if ambiguous:
            conflict = len(cand) >= 2 and len(matched) < len(cand)
            why = (
                "约束指向不同记录"
                if conflict
                else "预选查询式"
                if preselect
                else "客服消歧问句后无筛选"
                if agent_asked
                else "偏好性指代未最终确认"
                if preferential
                else f"约束命中 {len(cand)} 条"
            )
            return RecordEvidence(
                mode="defer",
                constraint=str(filters),
                matched=cand,
                reason=f"记录指代无法唯一定位（{why}）",
            )
        return RecordEvidence(
            mode="unique",
            constraint=str(filters),
            matched=cand,
            target_record=cand[0],
            reason="约束定位唯一",
        )

    if len(matched) == 1:
        return RecordEvidence(
            mode="unique",
            constraint=str(filters),
            matched=matched,
            target_record=matched[0],
            reason="约束定位唯一",
        )
    if len(matched) == 0:
        return RecordEvidence(
            mode="latest",
            constraint="无(条件未命中, 回退全量)",
            matched=records,
            target_record=records[0],
            reason="条件未命中记录, 按口径取最近一条 records[0]",
        )
    return RecordEvidence(
        mode="latest",
        constraint=str(filters),
        matched=matched,
        target_record=matched[0],
        reason="约束命中多条, 取最近一条",
    )


def render_hint(ev: RecordEvidence | None) -> str | None:
    """组装 prompt 注入段。仅 defer 渲染。"""
    if ev is None or ev.mode != "defer":
        return None
    return format_record_defer_rule_block(ev.reason, is_multi_cand=(len(ev.matched) >= 2))
