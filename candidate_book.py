# -*- coding: utf-8 -*-
"""candidate_book.py: v27.2 候选模式 — T3-3 查询-选择-落地链路的旁路支撑。

设计 (docs/plan_t33_candidate_mode_20260923.md):
  t1 查询轮: 模型把约束命中的全部记录列为 `# candidate: <app>/<svc>` 段(旁路, 不入 JGA),
            由 CandidateBook 记账(供下游/客服回复驱动使用)。
  t2 选择轮: 用户选定 → 选中候选解引用落地(字段合并进桩意图 I1 或 new), 未选候选 cancel 移除。
  t3 确认轮: 现有 confirm_rule 覆盖, 无改动。

评估口径: candidate 段不入 pred 集合、不参与 turn/session 判分(与 __cancel__ 同构的旁路)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_CAND_HDR = re.compile(r"^#\s*candidate\s*:\s*(.+?)\s*$", re.MULTILINE)
# 选择语: 须含序数选择/指代选择, 排除纯确认语 ("就按这个/就这样/按这个处理" 是 confirm 非选择)
# v27.3-d930: "候选N" 指称 (d930 话术主形态) 并入选择语族
_CAND_SELECT = re.compile(
    r"第\s*[一二三四五六七八九十\d]+\s*[条个]|候选\s*[一二三四五六七八九十\d]+|(就|我|先)?按最近(一条)?来|(第一|第二|第三)条(不要|吧)")
_CAND_CONFIRM_RE = re.compile(r"就按这个|就这样|按这个(处理|来|继续)|确认按?这个")


@dataclass
class Candidate:
    key: str                  # app/svc
    fields: dict              # 记录字段 (app/svc/res/rtt/...)
    record_ref: str = ""      # record_id 引用
    seg_index: int = 0        # 客服话术序号 (1-based, 对齐用)


class CandidateBook:
    """候选账本: 每 session 一本, 记录模型列出的候选与状态(pending/selected/discarded)。"""

    def __init__(self) -> None:
        self.candidates: list[Candidate] = []
        self.resolved: dict[str, str] = {}   # key -> 落地到的 intent_id (select 后)

    def add(self, cand: Candidate) -> None:
        cand.seg_index = len(self.candidates) + 1
        self.candidates.append(cand)

    def pending(self) -> list[Candidate]:
        return [c for c in self.candidates if c.key not in self.resolved]

    def select_by_index(self, index: int) -> Candidate | None:
        pend = self.pending()
        return pend[index - 1] if 0 < index <= len(pend) else None

    def resolve(self, key: str, intent_id: str) -> None:
        self.resolved[key] = intent_id

    def discard_except(self, keep_key: str) -> list[str]:
        """select 后移除其余 pending 候选, 返回被移除的 key 列表。"""
        removed = []
        for c in self.pending():
            if c.key != keep_key:
                removed.append(c.key)
        # 标记为已处理(不再 pending)
        for k in removed:
            self.resolved[k] = "__discarded__"
        return removed


def parse_candidates(code: str) -> list[tuple[str, str]]:
    """提取所有 # candidate: <app>/<svc> 段。返回 [(key, segment_code), ...]。
    candidate 段结构与 intent 段相同 (summary + 赋值程序)。"""
    code = re.sub(r"\r\n", "\n", code)
    out: list[tuple[str, str]] = []
    matches = list(_CAND_HDR.finditer(code))
    for i, m in enumerate(matches):
        key = m.group(1).strip()
        start = m.end()
        # 段终止: 下一个 candidate/intent/cancel 头或文末
        stops = [x.start() for x in
                 re.finditer(r"^#\s*(?:candidate|intent|cancel)\s*:", code[m.end():], re.MULTILINE)]
        end = m.end() + stops[0] if stops else len(code)
        seg = code[start:end].strip()
        if seg:
            out.append((key, seg))
    return out


def select_phrase(utterance: str) -> bool:
    """t2 选择语检测 (用户在选候选)。纯确认语 (confirm) 不算选择。"""
    u = utterance or ""
    if _CAND_CONFIRM_RE.search(u):
        return False
    return bool(_CAND_SELECT.search(u))
