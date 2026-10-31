"""prompts/candidate_prompts.py — 候选列表解析与选择轮判定 LLM 提示词模板。"""
from __future__ import annotations

import json
from typing import Any

CANDIDATE_PICK_SYSTEM_PROMPT = (
    "你是一个选择解析器。用户话语是对候选记录的选择回复, 客服此前按序列出了候选。"
    "从用户话语提取选中的候选序号 (1-based)。否定该序号不算选中。"
    "若无法确定, 输出 0。只输出一个整数。"
)

JUDGE_SELECTION_SYSTEM_PROMPT = (
    "你是客服对话状态分析器。判断用户回复属于哪一类, 只输出一个 JSON 对象:\n"
    '- action: "select"(从候选中选了一条, 序数/指代/否定其余后选定/\'按最近那条\'都算; '
    '字段限定式选择如\'时延就按候选2\'也算) | "confirm"(纯确认已有方案, 如\'对就这样\') '
    '| "new_request"(另提了无关新保障) | "chitchat"(闲聊)\n'
    "- pick: 选中候选的序号(仅 action=select 时给 1-based 整数, 否则 null)。"
    "'最近一条/最新那条'=completed_at 完成时间最晚的那条。\n"
    "- scope: 用户要从选中记录取哪些字段(仅 action=select 时给, 否则 null)。"
    '枚举值: "app"/"svc"/"res"/"rtt" 的非空数组。判断依据是用户话语的动词与限定词:\n'
    '  · \'时延就按候选X\'/\'按X记录里的时延来\' → ["rtt"]\n'
    '  · \'按候选X这条记录来配/办理\' → ["app","svc","res","rtt"] 全量\n'
    "  · '其他都按这次说的' → 记录只补话语未明说的字段\n"
    "  · '应用业务清晰度时延都按X' → 全量"
)

CANDIDATE_SYSTEM_PROMPT = JUDGE_SELECTION_SYSTEM_PROMPT

PARSE_LISTING_SYSTEM_PROMPT = (
    "你是客服话术解析器。从客服回复中提取其列出的候选记录, 按话术提及顺序输出。"
    "每条给出 application_name/service_name/completed_at(从话术的日期描述推出,"
    "格式 YYYY-MM-DD 或 YYYY年MM月DD日) 供匹配。无法提取候选时输出空数组 []。"
    "只输出 JSON 数组。"
)

RESOLVE_LISTING_SYSTEM_PROMPT = (
    "你是客服话术与记录的匹配器。客服回复里列出了若干候选记录(每条带属性:"
    "应用/服务/清晰度/时延/完成日期)。把话术里的每个候选与 records 表精确匹配,"
    "按话术提及顺序输出匹配到的 record_id 数组。属性要对上(时延数值/清晰度/"
    "完成日期都要一致, 不要按'最近'臆测)。匹配不上就少输出。"
    '只输出 JSON 数组, 如 ["R-U1959-001", "R-U1959-003"]。'
)


def fmt_candidate_line(i: int, r: dict[str, Any]) -> str:
    """格式化单条候选记录摘要行。"""
    rtt = (r.get("rtt") or {}).get("max_value") if isinstance(r.get("rtt"), dict) else r.get("rtt")
    res = (
        (r.get("resolution") or {}).get("min_value")
        if isinstance(r.get("resolution"), dict)
        else r.get("resolution")
    )
    return (
        f"候选{i}: {r.get('application_name')}/{r.get('service_name')}, "
        f"{res}, {rtt}, 完成于{r.get('completed_at')}"
    )


def format_pick_task_prompt(utterance: str, listing: list[dict[str, Any]]) -> str:
    """构造候选序号抽取任务提示词。"""
    lines = "\n".join(fmt_candidate_line(i + 1, r) for i, r in enumerate(listing))
    return f"候选列表:\n{lines}\n\n用户: {utterance}\n\n选中序号:"


def format_judge_selection_task_prompt(utterance: str, listing: list[dict[str, Any]]) -> str:
    """构造选择轮判定与字段范围抽取任务提示词。"""
    lines = "\n".join(fmt_candidate_line(i + 1, r) for i, r in enumerate(listing))
    return f"候选列表:\n{lines}\n\n用户: {utterance}\n\nJSON (action/pick/scope 三个字段都要给):"


def format_parse_listing_task_prompt(agent_reply: str, slim_records: list[dict[str, Any]]) -> str:
    """构造客服话术候选顺序解析任务提示词。"""
    return (
        f"records:\n{json.dumps(slim_records, ensure_ascii=False)}\n\n"
        f"客服话术:\n{agent_reply}\n\n候选 (按提及顺序, JSON):"
    )


def format_resolve_listing_task_prompt(agent_reply: str, slim_records: list[dict[str, Any]]) -> str:
    """构造客服话术与记录 ID 精确匹配任务提示词。"""
    return (
        f"records:\n{json.dumps(slim_records, ensure_ascii=False)}\n\n"
        f"客服话术:\n{agent_reply}\n\nrecord_id 数组:"
    )
