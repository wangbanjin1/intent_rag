# -*- coding: utf-8 -*-
"""candidate_llm.py: v27.4 候选模块 LLM 兜底 (方案 B, 用户 2026-10-12 定案)。

设计原则 (与封死路线的边界):
  - 规则优先 (高频快路径零成本), LLM 只兜长尾 (正则解析失败时升级)
  - LLM 只做【信息抽取】(选中序号 / 话术提及顺序), 不做状态写操作;
    落地仍走确定性锚点链 (字段值从 records 查表, 不经 LLM)
  - 失败安全回退 (LLM 输出非法/超时 -> 返回 None, 退回既有行为)
  - 输入只有当前话语 + 候选明细 (由 records 构造), 测试集永不进训练/检索
接口:
  pick_index_llm(llm, utterance, listing) -> int | None  (1-based)
  parse_listing_llm(llm, agent_reply, records) -> list[dict] | None
"""
from __future__ import annotations

import json
import re
from typing import Any

from prompts.candidate_prompts import (
    CANDIDATE_PICK_SYSTEM_PROMPT,
    CANDIDATE_SYSTEM_PROMPT,
    JUDGE_SELECTION_SYSTEM_PROMPT,
    PARSE_LISTING_SYSTEM_PROMPT,
    RESOLVE_LISTING_SYSTEM_PROMPT,
    fmt_candidate_line,
    format_judge_selection_task_prompt,
    format_parse_listing_task_prompt,
    format_pick_task_prompt,
    format_resolve_listing_task_prompt,
)

__all__ = [
    "CANDIDATE_PICK_SYSTEM_PROMPT",
    "CANDIDATE_SYSTEM_PROMPT",
    "JUDGE_SELECTION_SYSTEM_PROMPT",
    "PARSE_LISTING_SYSTEM_PROMPT",
    "RESOLVE_LISTING_SYSTEM_PROMPT",
    "_ATTR_W",
    "_NUM",
    "_fmt_cand",
    "_slim_record",
    "judge_selection_turn",
    "parse_listing_llm",
    "pick_index_llm",
    "resolve_listing_llm",
    "resolve_listing_rule",
]

_NUM = "一二三四五六七八九"
_fmt_cand = fmt_candidate_line


def pick_index_llm(llm: Any, utterance: str, listing: list[dict[str, Any]]) -> int | None:
    """LLM 兜底选中序号。返回 1-based 序号, 失败/非法 -> None。"""
    if not listing:
        return None
    task = format_pick_task_prompt(utterance, listing)
    try:
        raw = llm.gen_raw_code(CANDIDATE_PICK_SYSTEM_PROMPT, task)
        m = re.search(r"\d+", raw or "")
        if not m:
            return None
        n = int(m.group(0))
        return n if 1 <= n <= len(listing) else None
    except Exception:
        return None


def judge_selection_turn(llm: Any, utterance: str, listing: list[dict[str, Any]]) -> dict[str, Any]:
    """v28 选择轮判定+选中序号+落地范围一体化 (schema 约束枚举)。"""
    out: dict[str, Any] = {"is_selection": False, "pick": None, "scope": None}
    if not listing:
        return out
    task = format_judge_selection_task_prompt(utterance, listing)
    try:
        raw = llm.gen_raw_code(JUDGE_SELECTION_SYSTEM_PROMPT, task)
        m = re.search(r"\{[\s\S]*?\}", raw or "")
        if not m:
            return out
        j = json.loads(m.group(0))
        action = str(j.get("action") or "")
        pick = j.get("pick")
        if isinstance(pick, str) and pick.isdigit():
            pick = int(pick)
        scope = j.get("scope")
        if action not in ("select", "confirm", "new_request", "chitchat"):
            return out
        if action == "select":
            if not (isinstance(pick, int) and 1 <= pick <= len(listing)):
                return out
            if not (
                isinstance(scope, list)
                and scope
                and all(s in ("app", "svc", "res", "rtt") for s in scope)
            ):
                return out
        else:
            pick, scope = None, None
        return {"is_selection": action == "select", "pick": pick, "scope": scope}
    except Exception:
        return out


def parse_listing_llm(
    llm: Any, agent_reply: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """LLM 兜底解析客服话术中的候选顺序。返回有序 record 列表, 失败 -> None。"""
    if not agent_reply or not records:
        return None
    slim = [
        {
            "record_id": r.get("record_id"),
            "application_name": r.get("application_name"),
            "service_name": r.get("service_name"),
            "completed_at": r.get("completed_at"),
            "rtt": (r.get("rtt") or {}).get("max_value")
            if isinstance(r.get("rtt"), dict)
            else r.get("rtt"),
        }
        for r in records
    ]
    task = format_parse_listing_task_prompt(agent_reply, slim)
    try:
        raw = llm.gen_raw_code(PARSE_LISTING_SYSTEM_PROMPT, task)
        m = re.search(r"\[[\s\S]*?\]", raw or "")
        if not m:
            return None
        arr = json.loads(m.group(0))
        if not arr or not isinstance(arr, list):
            return None
        out: list[dict[str, Any]] = []
        used: set[str] = set()
        for item in arr:
            app = item.get("application_name")
            svc = item.get("service_name")
            ca = item.get("completed_at") or ""
            ca_norm = re.sub(r"\D", "", str(ca))
            best = None
            for r in records:
                if r.get("record_id") in used:
                    continue
                ra = re.sub(r"\D", "", str(r.get("completed_at") or ""))[:8]
                if app and r.get("application_name") != app:
                    continue
                if svc and r.get("service_name") != svc:
                    continue
                if ca_norm and not ra.startswith(ca_norm[:8]):
                    continue
                best = r
                break
            if best is None:
                return None
            used.add(best["record_id"])
            out.append(best)
        return out if len(out) >= 2 else None
    except Exception:
        return None


_ATTR_W = ("application_name", "service_name", "rtt", "resolution", "completed_at")


def _slim_record(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": r.get("record_id"),
        "application_name": r.get("application_name"),
        "service_name": r.get("service_name"),
        "resolution": (r.get("resolution") or {}).get("min_value")
        if isinstance(r.get("resolution"), dict)
        else r.get("resolution"),
        "rtt": (r.get("rtt") or {}).get("max_value")
        if isinstance(r.get("rtt"), dict)
        else r.get("rtt"),
        "completed_at": r.get("completed_at"),
    }


def _extract_rule_segment_attrs(seg: str) -> dict[str, str]:
    """从单个候选文本段抽取应用、业务、时延、分辨率与完成日期属性。"""
    from schema import KNOWN_APPLICATIONS

    attrs: dict[str, str] = {}
    for a in sorted(KNOWN_APPLICATIONS, key=len, reverse=True):
        if a in seg:
            attrs["application_name"] = a
            break
    for sv in ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频"):
        if re.search(rf"{re.escape(sv)}", seg):
            u2 = seg.replace(attrs.get("application_name", ""), "")
            if sv in u2:
                attrs["service_name"] = sv
                break
    m = re.search(r"(\d+)\s*(?:ms|毫秒)", seg)
    if m:
        attrs["rtt"] = f"{m.group(1)}ms"
    m = re.search(r"(\d{3,4})\s*[pP]", seg)
    if m:
        attrs["resolution"] = f"{m.group(1)}p"
    m = re.search(r"(\d{1,2})月(\d{1,2})[号日]", seg)
    if m:
        attrs["date"] = f"{int(m.group(1)):02d}月{int(m.group(2)):02d}"
    return attrs


def resolve_listing_rule(
    agent_reply: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """规则快路径: 从话术逐候选拆属性 (app/svc/rtt/res/完成日期), 与 records 精确匹配。"""
    if not agent_reply or not records:
        return None
    text = re.split(r"请问|您选|您看|要选", agent_reply)[0]
    segs = [s for s in re.split(r"[；;]|，(?=候选|第)|。(?=候选|第)", text) if s.strip()]
    cand_segs: list[str] = []
    for s in segs:
        m = re.search(r"(候选|第)\s*[一二三1234]\s*[条个]?[是为]?", s)
        if m:
            seg = s[m.start() :]
            seg = re.split(r"，您|。您|您说的", seg)[0].strip("，。；、 ")
            cand_segs.append(seg)
    if len(cand_segs) < 2:
        return None

    slim = [_slim_record(r) for r in records]
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for seg in cand_segs:
        attrs = _extract_rule_segment_attrs(seg)
        if not any(k in attrs for k in ("application_name", "rtt", "resolution", "date")):
            return None
        hits = []
        for r in slim:
            if r["record_id"] in used:
                continue
            ok = True
            for k, v in attrs.items():
                if k == "date":
                    if v not in (r.get("completed_at") or ""):
                        ok = False
                elif str(r.get(k) or "").lower() != str(v).lower():
                    ok = False
            if ok:
                hits.append(r)
        if len(hits) != 1:
            return None
        used.add(hits[0]["record_id"])
        out.append(hits[0])
    return out if len(out) >= 2 else None


def resolve_listing_llm(
    llm: Any, agent_reply: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """LLM 兜底: 话术属性匹配失败 (属性歧义/描述形态超规则覆盖) 时升级。"""
    if not agent_reply or not records:
        return None
    slim = [_slim_record(r) for r in records]
    task = format_resolve_listing_task_prompt(agent_reply, slim)
    try:
        raw = llm.gen_raw_code(RESOLVE_LISTING_SYSTEM_PROMPT, task)
        m = re.search(r"\[[\s\S]*?\]", raw or "")
        if not m:
            return None
        ids = json.loads(m.group(0))
        if not isinstance(ids, list) or len(ids) < 2:
            return None
        by_id = {r.get("record_id"): r for r in records}
        out = []
        for rid in ids:
            r = by_id.get(rid)
            if r is None:
                return None
            out.append(r)
        return out
    except Exception:
        return None
