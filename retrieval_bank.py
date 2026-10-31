"""Retrieval Bank: 加载原始 session JSONL, 用 case_format 抽取 turn-level case。

案例库文件每行是一个完整 session(用户原始格式)。load 时调用
extract_turn_cases 拆出每个 turn 的 case(prev_state + delta),用于检索。
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any

from case_format import extract_turn_cases, flatten_params
from embedder import Embedder


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 1e-12:
        return list(vec)
    return [x / norm for x in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class Case:
    case_id: str
    session_id: str
    turn: int
    utterance: str
    intent: str
    prev_state: dict[str, Any]
    delta: list[dict[str, Any]]
    validity: str
    invalid_reason: str | None
    reference_time: str
    flat_params: dict[str, Any] = field(default_factory=dict)
    slot_set: frozenset[str] = field(default_factory=frozenset)
    duration_minutes: int | None = None
    has_relative_time: bool = False
    embedding: list[float] | None = None


@dataclass
class CaseBank:
    cases: list[Case] = field(default_factory=list)
    matrix: list[list[float]] | None = None
    embedder: Embedder | None = None

    @classmethod
    def load(cls, path: str, embedder: Embedder) -> "CaseBank":
        bank = cls(embedder=embedder)
        if not os.path.exists(path):
            return bank
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                session = json.loads(line)
                for c in extract_turn_cases(session):
                    bank.cases.append(cls._to_case(c))
        bank._build_index()
        return bank

    @staticmethod
    def _to_case(c: dict[str, Any]) -> Case:
        # delta 现在是嵌套 params dict (turn_updates.params 原样)
        delta = c.get("delta", {}) or {}
        slot_set = frozenset(delta.keys())
        # duration 在 timestamp.duration.value
        dur = None
        ts = delta.get("timestamp", {}) if isinstance(delta, dict) else {}
        if isinstance(ts, dict):
            dur_spec = ts.get("duration", {})
            if isinstance(dur_spec, dict) and isinstance(dur_spec.get("value"), str):
                dur = CaseBank._parse_duration(dur_spec["value"])
        rel_words = ["明天", "后天", "今天", "现在", "下午", "上午", "晚上", "中午"]
        has_rel = any(w in c.get("utterance", "") for w in rel_words)
        return Case(
            case_id=c["case_id"],
            session_id=c.get("session_id", ""),
            turn=c.get("turn", 1),
            utterance=c["utterance"],
            intent=c.get("intent", "unknown"),
            prev_state=c.get("prev_state", {}),
            delta=delta,
            validity=c.get("validity", "valid"),
            invalid_reason=c.get("invalid_reason"),
            reference_time=c.get("reference_time", ""),
            flat_params=c.get("flat_params", {}),
            slot_set=slot_set,
            duration_minutes=dur,
            has_relative_time=has_rel,
        )

    @staticmethod
    def _parse_duration(s: str | None) -> int | None:
        if not s:
            return None
        try:
            if s.endswith("min"):
                return int(s[:-3])
            if s.endswith("h"):
                return int(s[:-1]) * 60
        except ValueError:
            return None
        return None

    @staticmethod
    def _case_text(c: Case) -> str:
        """论文 x_t = [y_{t-1}, (A_t, U_t)] 拼接。"""
        return f"prev_state={json.dumps(c.prev_state, ensure_ascii=False)} utterance={c.utterance}"

    def _build_index(self) -> None:
        if not self.cases or self.embedder is None:
            return
        if hasattr(self.embedder, "_key") and hasattr(self.embedder, "_cache"):
            cached_cases = [
                c for c in self.cases
                if self.embedder._key(self._case_text(c)) in self.embedder._cache
            ]
            if len(cached_cases) >= 100:
                self.cases = cached_cases
        texts = [self._case_text(c) for c in self.cases]
        vecs = self.embedder.embed_batch(texts)
        self.matrix = [_l2_normalize(v) for v in vecs]
        for c, v in zip(self.cases, vecs):
            c.embedding = v

    def query_vec(self, prev_state: dict, utterance: str) -> list[float]:
        text = f"prev_state={json.dumps(prev_state or {}, ensure_ascii=False)} utterance={utterance}"
        v = self.embedder.embed(text)
        return _l2_normalize(v)

    def embedding_scores(self, query_vec: list[float]) -> list[float]:
        if not self.matrix or len(self.cases) == 0:
            return []
        return [_dot(row, query_vec) for row in self.matrix]

    def __len__(self) -> int:
        return len(self.cases)
