# -*- coding: utf-8 -*-
"""features.py: v26.4 机制开关单一事实源 (Feature Switch Layer)。

所有机制类 config 开关 (`xxx.enabled`) 在此一次性解析为不可变 Features 对象,
pipeline / session_eval 统一读取, 消除散布各处的 `cfg.get(k, {}).get("enabled")` 重复解析。

扩展指引 (docs/codeguide.md §8 同步):
  1. 本类加一字段 + from_config 一行解析 (含联动/派生逻辑——联动规则只写在这里);
  2. 使用处读 `features.xxx`;
  3. config json 加对应键。
禁止在 features.py 之外再做 `cfg.get(...).get("enabled")` 机制开关解析。
"""
from __future__ import annotations

from dataclasses import dataclass


def _on(cfg: dict, key: str) -> bool:
    return bool((cfg.get(key) or {}).get("enabled"))


@dataclass(frozen=True)
class Features:
    """机制开关快照 (一个 config 实例化一次, 只读)。"""

    # ---- 上下文注入 ----
    agent_context: bool          # 上一轮 agent 回复进 prompt (v13.1)
    status_prompt: bool          # 各意图状态快照进 prompt (v16.0)
    # ---- 状态机 ----
    status_tracking: bool        # 状态轨迹推导 + 状态口径评估, 纯旁路 (v13.0)
    confirm_rule: bool           # Affirm 规则识别 (v13.0)
    # ---- 检索 / demo ----
    correction_demo: bool        # 改口类 demo 注入 (v12.1)
    # ---- 触发式规则 (prompt 规则段注入) ----
    disambiguation_rule: bool    # 消歧轮规则 (v21.1)
    candidate_map: bool          # 消歧候选顺序对照 (v24.4; 派生: 消歧开 + candidate_map)
    correction_rule: bool        # 改口轮规则 (v22.1)
    record_rule: bool            # 无约束"上次记录"严格规则 (v24.1)
    pending_app_rule: bool       # 应用名待补规则 (v26.3-p1)
    # ---- 身份/实例化协议 ----
    intent_id_protocol: bool     # 意图 id 协议 (v26.0→v26.2b)
    multi_instance_key: bool     # 同 key 双意图实例化 (v26.3-p3; 派生: 依赖 id 协议)
    # ---- 时间锚点/证据/口径规则 (v27, docs/plan_time_anchor_20260920.md) ----
    time_anchor: bool            # 预计算日期表注入 + 系统 prompt 时间散文段压缩
    time_extractor: bool         # 明说时间提取证据 (TIME EVIDENCE) 注入
    time_rule_contradiction: bool  # 矛盾保字面规则
    time_rule_profile_slot: bool   # 画像"常用开始时段"优先于"常用保障时长"规则
    time_rule_explicit_first: bool # 本轮明说时间优先于 records/profile 规则
    time_rule_candidate: bool      # 候选时段未定不落槽规则
    # ---- 记录指代预判 (v27.1, docs/plan_record_selector_20260922.md) ----
    record_selector: bool        # 记录指代预判 (RECORD EVIDENCE/悬空 RULE 生成前注入)
    # ---- 候选模式 (v27.2, docs/plan_t33_candidate_mode_20260923.md) ----
    candidate_mode: bool         # T3-3 候选列举/选择/落地 (candidate 段旁路 + select/cancel)
    quality_hint: bool           # 画质条目高亮 (话语 app/画质词查标准表注入)

    @classmethod
    def from_config(cls, cfg: dict) -> "Features":
        """从 config dict 一次性解析; 联动/派生规则唯一实现处。"""
        return cls(
            agent_context=_on(cfg, "agent_context"),
            status_prompt=_on(cfg, "status_prompt"),
            status_tracking=_on(cfg, "status_tracking"),
            confirm_rule=_on(cfg, "confirm_rule"),
            correction_demo=_on(cfg, "correction_demo"),
            disambiguation_rule=_on(cfg, "disambiguation_rule"),
            candidate_map=_on(cfg, "disambiguation_rule")
                and bool((cfg.get("disambiguation_rule") or {}).get("candidate_map")),
            correction_rule=_on(cfg, "correction_rule"),
            record_rule=_on(cfg, "record_rule"),
            pending_app_rule=_on(cfg, "pending_app_rule"),
            intent_id_protocol=_on(cfg, "intent_id_protocol"),
            multi_instance_key=_on(cfg, "multi_instance_key")
                and _on(cfg, "intent_id_protocol"),   # 实例化前提是 id 协议
            time_anchor=_on(cfg, "time_anchor"),
            time_extractor=_on(cfg, "time_extractor"),
            time_rule_contradiction=_on(cfg, "time_rule_contradiction"),
            time_rule_profile_slot=_on(cfg, "time_rule_profile_slot"),
            time_rule_explicit_first=_on(cfg, "time_rule_explicit_first"),
            time_rule_candidate=_on(cfg, "time_rule_candidate"),
            record_selector=_on(cfg, "record_selector"),
            candidate_mode=_on(cfg, "candidate_mode"),
            quality_hint=_on(cfg, "quality_hint"),
        )

    def switches(self) -> dict[str, bool]:
        """summary.json 落盘用: 全量开关快照。"""
        return {f: getattr(self, f) for f in self.__dataclass_fields__}
