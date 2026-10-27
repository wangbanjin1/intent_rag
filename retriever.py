"""Diverse Retriever (MMR) — 论文 §3.2.2。

E*_k = argmax_{E_k ⊂ D} Σ cos(emb(x_t), emb(x_i)) − α Σ_{i≠j} cos(emb(x_i), emb(x_j))

贪心近似: 先取 top-N, 再迭代选 k 个, 每步最大化
    rel(x_i, x_t) − α · max_{x_j∈E} cos(x_i, x_j)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrieval_bank import CaseBank, _dot


@dataclass
class RetrievedCase:
    case_id: str
    utterance: str
    intent: str
    prev_state: dict[str, Any]
    delta: list[dict[str, Any]]
    validity: str
    invalid_reason: str | None
    reference_time: str
    score: float
    emb_score: float


class DiverseRetriever:
    def __init__(
        self,
        bank: CaseBank,
        top_k: int = 5,
        pool_n: int = 50,
        alpha: float = 0.3,
    ):
        self.bank = bank
        self.top_k = top_k
        self.pool_n = pool_n
        self.alpha = alpha  # 不相似因子, 0 = 普通top-k

    def retrieve(
        self,
        utterance: str,
        prev_state: dict[str, Any] | None = None,
    ) -> list[RetrievedCase]:
        if len(self.bank) == 0:
            return []

        qvec = self.bank.query_vec(prev_state or {}, utterance)
        sims = self.bank.embedding_scores(qvec)  # [N] 余弦

        # 1) 先取 top-N 候选池 (|D| >> N >> k)
        n_pool = min(self.pool_n, len(self.bank))
        pool_idx = sorted(range(len(sims)), key=lambda idx: sims[idx], reverse=True)[:n_pool]

        # 2) 贪心 MMR 迭代
        chosen: list[int] = []
        chosen_sims_to_query: list[float] = []
        pool_vecs = [self.bank.matrix[idx] for idx in pool_idx]  # type: ignore[index]

        # 第一条: 取候选池里与 query 最相似的
        first = 0
        chosen.append(first)
        chosen_sims_to_query.append(float(sims[pool_idx[first]]))

        while len(chosen) < self.top_k and len(chosen) < len(pool_idx):
            best_i, best_score = -1, -1e18
            for li in range(len(pool_idx)):
                if li in chosen:
                    continue
                rel = float(sims[pool_idx[li]])  # cos(x_i, x_t)
                max_sim_chosen = max(_dot(pool_vecs[c], pool_vecs[li]) for c in chosen)
                mmr = rel - self.alpha * max_sim_chosen
                if mmr > best_score:
                    best_score, best_i = mmr, li
            if best_i < 0:
                break
            chosen.append(best_i)
            chosen_sims_to_query.append(float(sims[pool_idx[best_i]]))

        out: list[RetrievedCase] = []
        for rank, li in enumerate(chosen):
            c = self.bank.cases[pool_idx[li]]
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
                    score=float(chosen_sims_to_query[rank]),
                    emb_score=float(chosen_sims_to_query[rank]),
                )
            )
        return out
