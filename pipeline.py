"""Pipeline 编排 — RefPyDST + S3-DST PAR 全流程。

输出范式: python_par (Text-to-Python + 强制 # summary PAR)。
拆分模块:
  - pipeline_slot_fusion.py: 改口/消歧/记录/画质/候选模式/时间证据融合与提示段构造
  - pipeline_turn_runner.py: PAR 校验回路、候选抢跑剥离、兜底与段级状态归并

  utterance + prev_state
      │
      ▼ Embedding(prev_state || utterance)
      ▼ DiverseRetriever (MMR)  →  E_k
      ▼ build_prompt → (system, task_prompt)
      ▼ LLM.gen_raw_code → 原始输出
      ▼ execute_program_multi (沙箱执行得 Δy)
      ▼ PAR 校验回路 (缺 summary 触发重采样1次) + record 兜底
      ▼ Rule Validator (apply Δy → y_t → 校验, 含时间知二推一)
      ▼ final JSON {intent, delta, validity, invalid_reason, final_state}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from embedder import Embedder
from features import Features
from llm_client import LLMClient
from pipeline_slot_fusion import (
    _CN_NUM,
    _CORR_WORDS,
    _DAYPART_HOUR,
    _DISAMB_AGENT_Q_RE,
    _DISAMB_AGENT_RE,
    _DISAMB_PREFIX_RES,
    _DISAMB_QCUT_RE,
    _DISAMB_SPLIT_RE,
    _DISAMB_USER_RE,
    _OOS_APPS,
    _ORD_ANY_RE,
    _ORD_LATEST_RE,
    _ORD_NEG_RE,
    _ORD_SEL_RE,
    _PREV_COREF_RE,
    _PROFILE_REF_RE,
    _RECORD_REF_ANY_RE,
    _RECORD_REF_LOOSE_RE,
    _RECORD_REF_RE,
    _SERVICE_WORDS,
    _build_time_hint,
    _cand_attrs,
    _cl_judge_selection,
    _cn2int,
    _is_correction,
    _is_correction_case,
    _is_disambiguation_turn,
    _llm_parse_listing,
    _llm_pick,
    _needs_latest_record_rule,
    _parse_disamb_candidates,
    _parse_pick_ord,
    _pending_stubs,
    _profile_slot_conflict,
    _rec_match_score,
    _record_constraint_hit,
    _resolve_listing,
    _retrieve_correction_demos,
    _utterance_mentions_app,
    build_candidate_mode_hint,
    build_pending_app_hint,
    build_quality_hint,
    build_record_hints,
)
from pipeline_turn_runner import (
    _CANCEL_WORDS,
    _DAYPART_HOUR_C,
    _base_key,
    _cancel_check_ok,
    _delta_from_record,
    _disambiguate_cancel,
    _identity_ok,
    _instanced_key,
    _need_par,
    _parse_ordinal,
    _parse_time_hint,
    _resolve_cancel_key,
    _summaries_ok,
    apply_record_and_time_stub_fallbacks,
    check_id_headers_ok,
    handle_cancel_segment,
    intent_key,
    is_suspicious_new_segment,
    process_id_segment,
    process_key_segment,
    strip_cand_listing_jump_run,
)
from prompt_builder import build_prompt
from python_state import execute_program_multi
from retrieval_bank import CaseBank
from retriever import DiverseRetriever
from validator import validate

__all__ = [
    "IntentPipeline",
    "MultiIntentPipeline",
    "PipelineResult",
    "_CANCEL_WORDS",
    "_CN_NUM",
    "_CORR_WORDS",
    "_DAYPART_HOUR",
    "_DAYPART_HOUR_C",
    "_DISAMB_AGENT_Q_RE",
    "_DISAMB_AGENT_RE",
    "_DISAMB_PREFIX_RES",
    "_DISAMB_QCUT_RE",
    "_DISAMB_SPLIT_RE",
    "_DISAMB_USER_RE",
    "_OOS_APPS",
    "_ORD_ANY_RE",
    "_ORD_LATEST_RE",
    "_ORD_NEG_RE",
    "_ORD_SEL_RE",
    "_PREV_COREF_RE",
    "_PROFILE_REF_RE",
    "_RECORD_REF_ANY_RE",
    "_RECORD_REF_LOOSE_RE",
    "_RECORD_REF_RE",
    "_SERVICE_WORDS",
    "_base_key",
    "_build_time_hint",
    "_cancel_check_ok",
    "_cand_attrs",
    "_cl_judge_selection",
    "_cn2int",
    "_delta_from_record",
    "_disambiguate_cancel",
    "_identity_ok",
    "_instanced_key",
    "_is_correction",
    "_is_correction_case",
    "_is_disambiguation_turn",
    "_llm_parse_listing",
    "_llm_pick",
    "_need_par",
    "_needs_latest_record_rule",
    "_parse_disamb_candidates",
    "_parse_ordinal",
    "_parse_pick_ord",
    "_parse_time_hint",
    "_pending_stubs",
    "_profile_slot_conflict",
    "_rec_match_score",
    "_record_constraint_hit",
    "_resolve_cancel_key",
    "_resolve_listing",
    "_retrieve_correction_demos",
    "_summaries_ok",
    "_utterance_mentions_app",
    "intent_key",
    "load_config",
]


@dataclass
class PipelineResult:
    utterance: str
    reference_time: str
    prev_state: dict[str, Any]
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    best: dict[str, Any] = field(default_factory=dict)
    final: dict[str, Any] = field(default_factory=dict)


class IntentPipeline:
    """单意图 pipeline (main.py demo 用)。评估用 MultiIntentPipeline。"""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.features = Features.from_config(cfg)
        self.embedder = Embedder(cfg["embedder"])
        self.bank = CaseBank.load(cfg["retriever"]["case_bank_path"], self.embedder)
        rcfg = cfg["retriever"]
        self.retriever = DiverseRetriever(
            self.bank,
            top_k=rcfg["top_k"],
            pool_n=rcfg["pool_n"],
            alpha=rcfg["alpha"],
        )
        self.llm = LLMClient(cfg["llm"])

    def run(
        self,
        utterance: str,
        reference_time: str,
        prev_state: dict[str, Any] | None = None,
    ) -> PipelineResult:
        prev_state = prev_state or {}
        retrieved = self.retriever.retrieve(utterance, prev_state)

        system, task_prompt = build_prompt(
            utterance, reference_time, prev_state, retrieved, None
        )
        raw = self.llm.gen_raw_code(system, task_prompt)
        segs = execute_program_multi(raw, prev_state or None)

        best_delta = segs[0][1] if segs else {}
        llm_output = {"intent": "unknown", "params": best_delta}
        final = validate(llm_output, reference_time, prev_state)

        return PipelineResult(
            utterance=utterance,
            reference_time=reference_time,
            prev_state=prev_state,
            retrieved=[
                {
                    "case_id": r.case_id,
                    "intent": r.intent,
                    "validity": r.validity,
                    "score": round(r.score, 4),
                }
                for r in retrieved
            ],
            best=llm_output,
            final=final,
        )


def load_config(path: str = "configs/config_d6_qwen_plus_best.json") -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class MultiIntentPipeline(IntentPipeline):
    """方案 A: 一次 LLM 调用输出多意图 (B2 IDIC-DST 多意图并发范式)。"""

    def _resolve_turn_disambig_and_corr(
        self,
        utterance: str,
        agent_reply: str | None,
        prev_states: dict[str, dict[str, Any]],
        user_context: dict[str, Any] | None,
    ) -> tuple[list[str] | None, list[dict[str, Any]] | None, list[str] | None]:
        """解析消歧轮候选列表与改口轮意图键列表。"""
        disamb_keys: list[str] | None = None
        disamb_cands: list[dict[str, Any]] | None = None
        if self.features.disambiguation_rule and _is_disambiguation_turn(
            utterance, agent_reply, prev_states
        ):
            disamb_keys = sorted(prev_states.keys())
            if self.features.candidate_map:
                disamb_cands = _resolve_listing(
                    self.llm, agent_reply, (user_context or {}).get("recent_records") or []
                )
                if disamb_cands is None:
                    disamb_cands = _parse_disamb_candidates(
                        agent_reply, (user_context or {}).get("recent_records")
                    )

        corr_keys: list[str] | None = None
        if self.features.correction_rule and prev_states and _is_correction(utterance):
            corr_keys = sorted(prev_states.keys())
        return disamb_keys, disamb_cands, corr_keys

    def _prepare_multi_turn_context(
        self,
        utterance: str,
        reference_time: str,
        prev_states: dict[str, dict[str, Any]],
        user_context: dict[str, Any] | None,
        agent_reply: str | None,
        prev_statuses: dict[str, str] | None,
        memory_hint: str | None,
    ) -> dict[str, Any]:
        """检索示例、解析消歧/改口/时间证据并构造系统提示与任务提示词。"""
        merged_prev: dict[str, Any] = {}
        for v in prev_states.values():
            merged_prev.update(v or {})

        retrieved = self.retriever.retrieve(utterance, merged_prev)
        if self.features.correction_demo and _is_correction(utterance):
            extra = _retrieve_correction_demos(
                self.retriever, utterance, merged_prev,
                n=int((self.cfg.get("correction_demo") or {}).get("n", 2)),
                exclude={c.case_id for c in retrieved},
            )
            retrieved = extra + retrieved

        disamb_keys, disamb_cands, corr_keys = self._resolve_turn_disambig_and_corr(
            utterance, agent_reply, prev_states, user_context
        )
        record_rule_hint, record_ev_hint = build_record_hints(
            self, utterance, user_context, agent_reply, disamb_keys
        )
        quality_hint = build_quality_hint(
            self.features, utterance, record_rule_hint, record_ev_hint
        )
        cand_mode_hint, listed_this_turn = build_candidate_mode_hint(
            self, utterance, user_context, agent_reply, disamb_cands
        )

        id_on = self.features.intent_id_protocol
        idbook = getattr(self, "_id_book", None) if id_on else None
        id_states = getattr(self, "_id_states", None) if id_on else None
        self._mi_on = self.features.multi_instance_key

        pending_app_hint = build_pending_app_hint(self.features, utterance, id_states)
        time_hint, time_ev = _build_time_hint(
            self.features, utterance, reference_time, user_context
        )

        import candidate_book as _cb
        system, task_prompt = build_prompt(
            utterance, reference_time, merged_prev, retrieved, user_context,
            agent_reply=agent_reply if self.features.agent_context else None,
            prev_statuses=prev_statuses if self.features.status_prompt else None,
            disamb_keys=disamb_keys, disamb_cands=disamb_cands, corr_keys=corr_keys,
            record_rule_hint=record_rule_hint, record_ev_hint=record_ev_hint,
            cand_mode_hint=cand_mode_hint, quality_hint=quality_hint,
            pending_app_hint=pending_app_hint, time_hint=time_hint,
            time_anchor_on=self.features.time_anchor,
            cand_landing=bool(cand_mode_hint) and _cb.select_phrase(utterance),
            idbook=idbook, id_states=id_states, memory_hint=memory_hint,
        )
        return {
            "merged_prev": merged_prev, "id_on": id_on, "idbook": idbook,
            "listed_this_turn": listed_this_turn, "time_ev": time_ev,
            "system": system, "task_prompt": task_prompt,
        }

    def _execute_and_fuse_multi_turn(
        self,
        utterance: str,
        prev_states: dict[str, dict[str, Any]],
        user_context: dict[str, Any] | None,
        ctx: dict[str, Any],
    ) -> list[tuple[str, Any]]:
        """调用大模型生成代码、执行 PAR 校验重采样、沙箱运行与兜底补齐。"""
        system = ctx["system"]
        task_prompt = ctx["task_prompt"]
        merged_prev = ctx["merged_prev"]
        id_on = ctx["id_on"]
        idbook = ctx["idbook"]
        listed_this_turn = ctx["listed_this_turn"]
        time_ev = ctx["time_ev"]

        raw = self.llm.gen_raw_code(system, task_prompt)
        if (
            (_need_par(utterance, merged_prev) and (not _summaries_ok(raw) or not _cancel_check_ok(utterance, raw)))
            or not check_id_headers_ok(raw, id_on, idbook)
            or not _identity_ok(raw, id_on)
            or is_suspicious_new_segment(raw, utterance, id_on, idbook, prev_states, self._mi_on)
        ):
            raw2 = self.llm.gen_raw_code(system, task_prompt)
            if (
                _summaries_ok(raw2)
                and _cancel_check_ok(utterance, raw2)
                and check_id_headers_ok(raw2, id_on, idbook)
                and _identity_ok(raw2, id_on)
                and not is_suspicious_new_segment(raw2, utterance, id_on, idbook, prev_states, self._mi_on)
            ):
                raw = raw2

        exec_prevs = prev_states
        if id_on and idbook is not None and prev_states:
            exec_prevs = dict(prev_states)
            for _iid, _ck in list(idbook._by_id.items()):
                if _ck in prev_states and _iid not in exec_prevs:
                    exec_prevs[_iid] = prev_states[_ck]

        segs = execute_program_multi(raw, exec_prevs)
        segs = strip_cand_listing_jump_run(
            segs, utterance, listed_this_turn, getattr(self, "_cand_listing", None)
        )

        self.last_raw = raw
        self.last_task_prompt = getattr(self.llm, "last_task_prompt", "")
        self.last_system_prompt = getattr(self.llm, "last_system_prompt", "")

        return apply_record_and_time_stub_fallbacks(
            segs, utterance, user_context, merged_prev, listed_this_turn, time_ev
        )

    def _finalize_multi_turn_result(
        self,
        segs: list[tuple[str, Any]],
        utterance: str,
        reference_time: str,
        prev_states: dict[str, dict[str, Any]],
        idbook: Any | None,
    ) -> dict[str, dict[str, Any]]:
        """归并取消段、ID 协议段或内容键段并返回多意图结果字典。"""
        results: dict[str, dict[str, Any]] = {}
        for key, delta in segs:
            if key == "__cancel__":
                self._handle_cancel_segment(
                    delta, idbook, prev_states, results, utterance=utterance
                )
                continue
            if idbook is not None:
                self._process_id_segment(
                    key, delta, idbook, prev_states, reference_time, results
                )
            else:
                self._process_key_segment(
                    key, delta, prev_states, reference_time, results
                )
        return results

    def run_multi(
        self,
        utterance: str,
        reference_time: str,
        prev_states: dict[str, dict[str, Any]] | None = None,
        user_context: dict[str, Any] | None = None,
        agent_reply: str | None = None,
        prev_statuses: dict[str, str] | None = None,
        session_disambig: bool = False,
        memory_hint: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        prev_states = prev_states or {}
        ctx = self._prepare_multi_turn_context(
            utterance=utterance,
            reference_time=reference_time,
            prev_states=prev_states,
            user_context=user_context,
            agent_reply=agent_reply,
            prev_statuses=prev_statuses,
            memory_hint=memory_hint,
        )
        segs = self._execute_and_fuse_multi_turn(utterance, prev_states, user_context, ctx)
        return self._finalize_multi_turn_result(
            segs, utterance, reference_time, prev_states, ctx["idbook"]
        )

    def _handle_cancel_segment(
        self,
        delta: Any,
        idbook: Any | None,
        prev_states: dict[str, dict[str, Any]],
        results: dict[str, dict[str, Any]],
        utterance: str = "",
    ) -> None:
        handle_cancel_segment(
            delta,
            idbook,
            prev_states,
            results,
            mi_on=getattr(self, "_mi_on", False),
            utterance=utterance,
        )

    def _process_id_segment(
        self,
        key: str,
        delta: dict[str, Any],
        idbook: Any,
        prev_states: dict[str, dict[str, Any]],
        reference_time: str,
        results: dict[str, dict[str, Any]],
    ) -> None:
        process_id_segment(
            key,
            delta,
            idbook,
            prev_states,
            reference_time,
            results,
            mi_on=getattr(self, "_mi_on", False),
        )

    def _process_key_segment(
        self,
        key: str,
        delta: dict[str, Any],
        prev_states: dict[str, dict[str, Any]],
        reference_time: str,
        results: dict[str, dict[str, Any]],
    ) -> None:
        process_key_segment(key, delta, prev_states, reference_time, results)
