"""MemoryBridge — 连接 D:\\coding\\memory_system 与 D:\\coding\\qwen_intent_rag 意图理解流水线。

职责:
  1. 在每轮对话理解前 (prepare_turn):
     - 调用 MemorySystem.search() 检索当前用户的三维长期记忆
       (periodic_preference / scene_preference / application_business) 与个性化表述映射 (expression)
     - 委托 bridge_factual / bridge_working / bridge_experience 完成三层记忆槽位对齐与规则提示段组装
  2. 在每个会话结束后 (record_session):
     - 调用 MemorySystem.record_session() 执行快慢双通道记忆萃取、投票晋升、艾宾浩斯衰减与归纳

拆分模块:
  - bridge_factual.py:    事实记忆检索增强、相对时间窗锚定、冷启动与多轮补槽规则
  - bridge_working.py:    工作记忆工单挑选、改期/消歧/复用规则与会话后工单抽取
  - bridge_experience.py: 经验记忆卡片合并、动态槽位修正规则与多轮确认锁定规则
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

MEMORY_SYSTEM_SRC = Path(r"D:\coding\memory_system\src")
if str(MEMORY_SYSTEM_SRC) not in sys.path:
    sys.path.insert(0, str(MEMORY_SYSTEM_SRC))

from memory_system.api import _BUILTIN_EXPERIENCE_TEMPLATES, MemorySystem
from memory_system.core.experience_rules import (
    find_holiday_dates,
    holiday_name,
    next_cn_workday_after,
    trigger_matches,
)
from memory_system.core.namespace import user_namespace
from memory_system.core.working_rules import (
    classify_working_record_reference,
    has_explicit_time,
    referenced_day_offset,
    reschedule_shift_minutes,
)
from memory_system.pipeline.extractor import _TEMP_VETO_WORDS
from memory_system.pipeline.updater import compute_ebbinghaus_retention, normalize_ts_str

from bridge_experience import (
    _exp_card,
    evaluate_experience_tier,
    render_experience_rule,
)
from bridge_factual import (
    _APP_CN_MAP,
    _BIZ_CN_DEFAULT,
    _EXPLICIT_RES_RE,
    _EXPLICIT_RTT_RE,
    _MEMORY_TRIGGER_RE,
    _QUOTED_SLANG_RE,
    DT_FMT,
    _explicit_start_dt,
    _explicit_window,
    _mentioned_app,
    _parse_minutes,
    _ref_year,
    anchor_time_window_to_ref,
    build_trace_info,
    enrich_factual_hits,
    render_factual_and_cold_start_rules,
    render_retrieval_header_lines,
    resolve_cn_app_svc,
)
from bridge_working import (
    _LOC_PRONOUNS,
    _LOC_RE,
    _PARAM_VALUE_RE,
    _extract_location,
    _is_temp_record,
    _is_temporary_override_dialogue,
    _pick_working_record,
    _rec_sort_key,
    extract_working_record_from_session,
    render_working_memory_rules,
    select_target_working_record,
)

__all__ = [
    "DT_FMT",
    "MemoryBridge",
    "_APP_CN_MAP",
    "_BIZ_CN_DEFAULT",
    "_BUILTIN_EXPERIENCE_TEMPLATES",
    "_EXPLICIT_RES_RE",
    "_EXPLICIT_RTT_RE",
    "_LOC_PRONOUNS",
    "_LOC_RE",
    "_MEMORY_TRIGGER_RE",
    "_PARAM_VALUE_RE",
    "_QUOTED_SLANG_RE",
    "_TEMP_VETO_WORDS",
    "_exp_card",
    "_explicit_start_dt",
    "_explicit_window",
    "_extract_location",
    "_is_temp_record",
    "_is_temporary_override_dialogue",
    "_mentioned_app",
    "_parse_minutes",
    "_pick_working_record",
    "_rec_sort_key",
    "_ref_year",
    "anchor_time_window_to_ref",
    "classify_working_record_reference",
    "find_holiday_dates",
    "has_explicit_time",
    "holiday_name",
    "next_cn_workday_after",
    "referenced_day_offset",
    "reschedule_shift_minutes",
    "trigger_matches",
]


class MemoryBridge:
    """连接 MemorySystem 与 MultiIntentPipeline 的端到端桥接器。"""

    def __init__(self, data_root: str | Path, config_overrides: dict[str, Any] | None = None) -> None:
        self.ms = MemorySystem(data_root=data_root, config=config_overrides)
        self._current_session_id: str | None = None
        self._session_memory_triggered: bool = False
        self._turn1_filled_memory: dict[str, Any] | None = None
        self._session_wr_mode: str | None = None  # "reuse" | "disambig" | "reschedule"
        self._session_wr_target: dict[str, Any] | None = None
        self._session_exp_triggered: str | None = None
        self._session_turn1_utterance: str = ""

    def reset_session(self, session_id: str | None = None) -> None:
        """重置单会话内的三层记忆触发状态。"""
        self._current_session_id = session_id
        self._session_memory_triggered = False
        self._turn1_filled_memory = None
        self._session_wr_mode = None
        self._session_wr_target = None
        self._session_exp_triggered = None
        self._session_turn1_utterance = ""

    def _resolve_cn_app_svc(self, content: dict[str, Any], utterance: str = "") -> tuple[str, str]:
        return resolve_cn_app_svc(content, utterance)

    def _render_experience_rule(
        self,
        card: dict[str, Any],
        utterance: str,
        reference_time: str,
        all_working_records: list[dict[str, Any]],
    ) -> str:
        return render_experience_rule(card, utterance, reference_time, all_working_records)

    def prepare_turn(
        self,
        user_id: str,
        utterance: str,
        reference_time: str,
        prev_states: dict[str, dict[str, Any]] | None = None,
        turn_index: int = 1,
        tenant_id: str = "default",
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """在单轮意图理解前检索 MemorySystem 并生成 (user_context, memory_prompt_hint, trace_info)。"""
        prev_states = prev_states or {}
        if turn_index == 1:
            self._session_memory_triggered = False
            self._turn1_filled_memory = None
            self._session_wr_mode = None
            self._session_wr_target = None
            self._session_exp_triggered = None
            self._session_turn1_utterance = utterance or ""

        raw_hits = self.ms.search(
            user_id=user_id,
            query=utterance,
            top_k=10,
            levels="L0+L1",
            bump_heat=True,
            now_ts=reference_time,
            tenant_id=tenant_id,
        )

        ns = user_namespace(user_id=user_id, tenant_id=tenant_id)

        param_hits: list[dict[str, Any]] = []
        periodic_hits: list[dict[str, Any]] = []
        service_hits: list[dict[str, Any]] = []
        expression_hits: list[dict[str, Any]] = []
        working_hits: list[dict[str, Any]] = []
        experience_hits: list[dict[str, Any]] = []

        for h in raw_hits:
            st = h.get("subtype", "")
            if h.get("validity") == "temporary":
                continue
            if st in ("preferred_value", "scene_preference", "application_business"):
                param_hits.append(h)
            elif st == "periodic_preference":
                periodic_hits.append(h)
            elif st == "service_to_app":
                service_hits.append(h)
            elif st == "expression":
                expression_hits.append(h)
            elif st == "working_record":
                working_hits.append(h)
            elif st == "experience":
                experience_hits.append(h)

        # 加载当前用户已入库的全部有效 working_record 与 active experience 卡片
        all_working_records: list[dict[str, Any]] = []
        for node in self.ms.filestore.iter_nodes(ns, memory_type="working_memory", subtype="working_record"):
            if node.status in ("archived", "expired", "superseded"):
                continue
            all_working_records.append(dict(node.content))
        all_working_records.sort(key=lambda r: str(r.get("record_id") or r.get("created_at") or ""))

        active_experiences: dict[str, dict[str, Any]] = {}
        for node in self.ms.filestore.iter_nodes(ns, memory_type="experience_memory", subtype="experience"):
            if node.status not in ("active", "reinforced"):
                continue
            eid = str(node.content.get("experience_id") or "").strip()
            if eid:
                active_experiences[eid] = dict(node.content)

        # 建立 (application, business) -> 正交 periodic_preference 时间窗路由列表
        periodic_list_by_app_biz: dict[tuple[str, str], list[dict[str, Any]]] = {}
        seen_pp_keys: set[str] = set()
        for ph in periodic_hits:
            pc = ph.get("content") or {}
            key_ab = (str(pc.get("application") or ""), str(pc.get("business") or ""))
            mk = str(ph.get("memory_key") or "")
            if key_ab[0] and key_ab[1] and mk and mk not in seen_pp_keys:
                seen_pp_keys.add(mk)
                periodic_list_by_app_biz.setdefault(key_ab, []).append(
                    {
                        "memory_id": ph.get("memory_id"),
                        "memory_key": mk,
                        "time_window": str(pc.get("time_window") or pc.get("condition") or ""),
                        "status": ph.get("status"),
                    }
                )
        for node in self.ms.filestore.iter_nodes(ns, memory_type="preference", subtype="periodic_preference"):
            if node.status in ("archived", "expired", "superseded") or node.validity.type == "temporary":
                continue
            key_ab = (str(node.content.get("application") or ""), str(node.content.get("business") or ""))
            mk = str(node.memory_key or "")
            if key_ab[0] and key_ab[1] and mk and mk not in seen_pp_keys:
                seen_pp_keys.add(mk)
                periodic_list_by_app_biz.setdefault(key_ab, []).append(
                    {
                        "memory_id": node.memory_id,
                        "memory_key": mk,
                        "time_window": str(node.content.get("time_window") or node.content.get("condition") or ""),
                        "status": node.status,
                    }
                )

        known_aliases: dict[str, dict[str, Any]] = {}
        for node in self.ms.filestore.iter_nodes(ns, memory_type="preference", subtype="expression"):
            if node.status in ("archived", "expired", "superseded"):
                continue
            al = str(node.content.get("alias") or node.memory_key or "").strip()
            if al:
                known_aliases[al] = dict(node.content)

        major_hits, best_major, user_context = enrich_factual_hits(
            param_hits=param_hits,
            periodic_list_by_app_biz=periodic_list_by_app_biz,
            all_working_records=all_working_records,
            known_aliases=known_aliases,
            utterance=utterance,
            reference_time=reference_time,
            is_temp_fn=_is_temp_record,
        )

        hint_lines = render_retrieval_header_lines(
            major_hits=major_hits,
            periodic_hits=periodic_hits,
            service_hits=service_hits,
            expression_hits=expression_hits,
            working_hits=working_hits,
            experience_hits=experience_hits,
            utterance=utterance,
        )

        # Tier 3: 经验记忆规则 (Experiential Memory)
        self._session_exp_triggered, exp_lines = evaluate_experience_tier(
            turn_index=turn_index,
            utterance=utterance,
            reference_time=reference_time,
            prev_states=prev_states,
            experience_hits=experience_hits,
            active_experiences=active_experiences,
            all_working_records=all_working_records,
            session_exp_triggered=self._session_exp_triggered,
        )
        hint_lines.extend(exp_lines)

        # Tier 2: 工作记忆工单规则 (Working Memory)
        target_wr = select_target_working_record(
            working_hits=working_hits,
            all_working_records=all_working_records,
            best_major=best_major,
            utterance=utterance,
        )
        exp_applied = bool(self._session_exp_triggered) and not str(self._session_exp_triggered).endswith(":LEARNING")
        self._session_wr_mode, self._session_wr_target, wr_lines = render_working_memory_rules(
            turn_index=turn_index,
            utterance=utterance,
            reference_time=reference_time,
            prev_states=prev_states,
            exp_applied=exp_applied,
            target_wr=target_wr,
            all_working_records=all_working_records,
            session_wr_mode=self._session_wr_mode,
            session_wr_target=self._session_wr_target,
            session_turn1_utterance=self._session_turn1_utterance,
        )
        hint_lines.extend(wr_lines)

        # Tier 1: 事实记忆触发规则、冷启动字面提取规则与多轮补槽规则
        has_trigger = bool(_MEMORY_TRIGGER_RE.search(utterance or ""))
        self._session_memory_triggered, filled_mem, factual_lines = render_factual_and_cold_start_rules(
            turn_index=turn_index,
            utterance=utterance,
            has_trigger=has_trigger,
            best_major=best_major,
            session_wr_mode=self._session_wr_mode,
            exp_applied=exp_applied,
            session_memory_triggered=self._session_memory_triggered,
            prev_states=prev_states,
            known_aliases=known_aliases,
        )
        if filled_mem is not None:
            self._turn1_filled_memory = filled_mem
        hint_lines.extend(factual_lines)

        memory_hint = "\n".join(hint_lines)
        trace_info = build_trace_info(
            turn_index=turn_index,
            utterance=utterance,
            reference_time=reference_time,
            has_trigger=has_trigger,
            session_memory_triggered=self._session_memory_triggered,
            session_wr_mode=self._session_wr_mode,
            session_exp_triggered=self._session_exp_triggered,
            raw_hits=raw_hits,
            major_hits=major_hits,
            periodic_hits=periodic_hits,
            service_hits=service_hits,
            expression_hits=expression_hits,
            working_hits=working_hits,
            experience_hits=experience_hits,
            best_major=best_major,
        )
        return user_context, memory_hint, trace_info

    @staticmethod
    def _extract_working_record_from_session(
        session: dict[str, Any], predicted_final: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        return extract_working_record_from_session(session, predicted_final)

    def record_session(
        self,
        session: dict[str, Any],
        tenant_id: str = "default",
        predicted_final: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """会话结束后调用 MemorySystem.record_session() 萃取并更新该用户的长期记忆库。"""
        user_id = str(session.get("user_id", "default_user"))
        session_id = str(session.get("session_id", ""))
        reference_time = str(session.get("reference_time", ""))
        turns = list(session.get("event_sequence") or [])
        metadata: dict[str, Any] = {"reference_time": reference_time}
        wr_to_append = self._extract_working_record_from_session(session, predicted_final)
        if wr_to_append:
            metadata["working_record_to_append"] = wr_to_append

        write_res = self.ms.record_session(
            user_id=user_id,
            agent_id="intent_agent",
            session_id=session_id,
            turns=turns,
            metadata=metadata,
            reference_time=reference_time,
            tenant_id=tenant_id,
            async_mode=False,
        )

        ns = user_namespace(user_id=user_id, tenant_id=tenant_id)
        now_ts = normalize_ts_str(reference_time)
        snapshot: list[dict[str, Any]] = []
        for n in sorted(self.ms.filestore.iter_nodes(ns), key=lambda x: (x.subtype, x.memory_key)):
            spec = self.ms.registry.get(n.subtype)
            base_lam = spec.decay_lambda if spec is not None else 0.02
            ret, stab, _ = compute_ebbinghaus_retention(
                decay_lambda=base_lam,
                last_reinforced_at=n.stats.last_reinforced_at or n.updated_at or n.created_at,
                now_ts=now_ts,
                positive_votes=n.stats.positive_votes,
                reuse_count=n.stats.reuse_count,
            )
            snapshot.append(
                {
                    "memory_id": n.memory_id,
                    "subtype": n.subtype,
                    "memory_key": n.memory_key,
                    "status": n.status,
                    "validity": n.validity.type,
                    "votes": n.stats.votes,
                    "positive_votes": n.stats.positive_votes,
                    "reuse_count": n.stats.reuse_count,
                    "retention": round(ret, 4),
                    "stability": round(stab, 2),
                    "content": dict(n.content),
                    "description": n.description,
                }
            )
        return {
            "write_result": write_res,
            "memory_snapshot": snapshot,
        }
