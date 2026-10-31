"""prompts/memory_hint_templates.py — 三层记忆（事实/工作/经验）与槽位融合规则提示词模板。

职责:
  1. 维护经验记忆规则段模板 (EXPERIENCE RULE / EXPERIENCE FOLLOW-UP KEEP RULE)
  2. 维护工作记忆规则段模板 (WORKING RECORD RESCHEDULE / DISAMBIGUATION / REUSE RULE)
  3. 维护事实记忆与冷启动规则段模板 (MEMORY FILL / COLD-START / SLANG / FOLLOW-UP RULE)
  4. 维护时间证据、画质映射、候选列举/选择模式与待补应用名规则段模板
"""
from __future__ import annotations

from typing import Any

# ====================================================================
# 1. 经验记忆 (Experiential Memory) 规则模板
# ====================================================================

EXPERIENCE_DEFAULT_TAIL = "# 按上述经验动作调整, 其余槽位以用户本轮话语为准!\n"

EXPERIENCE_FOLLOWUP_KEEP_RULE = (
    "# === EXPERIENCE FOLLOW-UP KEEP RULE (经验记忆归纳/确认轮状态锁定规则, 本轮生效, 优先级最高) ===\n"
    "# 本轮属于经验记忆归纳学习或确认轮：即使本轮用户口头提及“把开始时间往前提前半小时”、“平时对词我们都是打微信视频电话”或重申经验做法（该语句仅用于向经验库结晶未来规则 EXP_*），\n"
    "# 当前会话在上一轮已落盘锁定的全部 7 个目标槽位（application_name, service_name, resolution, rtt, start_timestamp, end_timestamp, duration）必须严格保持不变！\n"
    "# 必须遵守:\n"
    "# 1. 严禁在本轮调用 find_timestamp(...) 或修改 start_timestamp / end_timestamp（忽略 TIME EVIDENCE 的偏移）;\n"
    "# 2. 严禁修改 state.resolution 或 state.rtt（严禁从【用户习惯画像】或长期记忆中拉取其他参数如 100ms 覆盖 prev_state 已有的 resolution 和 rtt）;\n"
    "# 3. 必须输出 `# 身份判定: 更新已有意图 I1` 与 `# intent: I1`，并用 `state.xxx = state.xxx` 完整保留 prev_state 中的全部 7 个槽位!\n"
)


def format_experience_rule_header(experience_id: str, cond_txt: str, act_txt: str) -> str:
    """构造经验记忆规则通用头部。"""
    return (
        "# === EXPERIENCE RULE (经验记忆检索命中规则, 本轮生效, 优先级最高) ===\n"
        f"# 命中经验 [{experience_id}] 条件 (condition): {cond_txt}\n"
        f"# 命中经验动作 (action): {act_txt}\n"
        "# 必须遵守:\n"
    )


def format_experience_app_service_rule(
    head: str,
    assign_lines: list[str],
    st_str: str | None = None,
    et_str: str | None = None,
    dur_min: int | None = None,
) -> str:
    """渲染应用/业务载体替换类经验规则。"""
    time_lock = ""
    if st_str is not None and et_str is not None and dur_min is not None:
        time_lock = (
            f"#   start_timestamp = '{st_str}'  (来源: 用户本轮明说时刻, 严禁套用长期记忆中的其他时段)\n"
            f"#   end_timestamp   = '{et_str}'  (来源: 用户本轮明说时刻)\n"
            f"#   duration        = '{dur_min}min'\n"
        )
    return (
        head
        + time_lock
        + f"# 1. 必须填 {', '.join(assign_lines)} (按经验识别真实业务载体, 不要被字面词误导);\n"
        + "# 2. 将本轮话语中给出的画质 (resolution)、时延 (rtt)、起止时间与时长 (start_timestamp / end_timestamp / duration) 完整填入 state (严禁混入 MEMORY SYSTEM RETRIEVAL 中其他会话的时段)!\n"
    )


def format_experience_holiday_shift_rule(
    head: str,
    orig_date_str: str | None = None,
    h_name: str | None = None,
    next_workday_str: str | None = None,
) -> str:
    """渲染法定节假日顺延类经验规则。"""
    if orig_date_str and next_workday_str:
        return (
            head
            + f"#   原定日期 {orig_date_str} 为{h_name or '法定节假日'}假期 -> 节后首个工作日 {next_workday_str}\n"
            + f"# 1. start_timestamp 和 end_timestamp 的日期必须写为 '{next_workday_str}' (保留用户指定的具体起止时分与 duration);\n"
            + "# 2. 完整填入用户指定的应用、业务、画质 (resolution) 和时延 (rtt)!\n"
        )
    return (
        head
        + "# 1. 若用户指定日期为法定节假日, 将 start_timestamp / end_timestamp 的日期顺延至节后首个工作日 (保留起止时分与 duration);\n"
        + "# 2. 完整填入用户指定的应用、业务、画质 (resolution) 和时延 (rtt)!\n"
    )


def format_experience_formal_duration_rule(
    head: str,
    st_str: str | None = None,
    et_str: str | None = None,
    target_dur: str = "",
    src_note: str = "",
    rec_id: str = "formal",
) -> str:
    """渲染历史正式工单时长消歧类经验规则。"""
    if st_str and et_str and target_dur:
        return (
            head
            + f"#   start_timestamp = '{st_str}'  (来源: {src_note})\n"
            + f"#   end_timestamp   = '{et_str}'  (= start + {target_dur})\n"
            + f"#   duration        = {target_dur!r}  (来源: 历史正式工单 [{rec_id}], 过滤近期临时短单)\n"
            + "# 1. 本轮直接将 start_timestamp, end_timestamp, duration 设为上述值;\n"
            + "# 2. application_name / service_name / resolution / rtt 一律按用户本轮话语填写 (经验不改写这些槽位)!\n"
        )
    return (
        head
        + "# 1. 过滤近期临时短单，按历史正式工单的 duration 推算 end_timestamp = 用户给出的开始时刻 + duration;\n"
        + "# 2. application_name / service_name / resolution / rtt 一律按用户本轮话语填写!\n"
    )


def format_experience_start_offset_rule(
    head: str,
    off: int,
    add: int,
    new_st_str: str | None = None,
    orig_hm_str: str | None = None,
    et_str: str | None = None,
    new_dur_min: int | None = None,
) -> str:
    """渲染起保时刻偏移（提前预热）类经验规则。"""
    if new_st_str and orig_hm_str and et_str and new_dur_min is not None:
        return (
            head
            + f"#   start_timestamp = '{new_st_str}'  (= 用户开始时刻 {orig_hm_str} {off:+d}min)\n"
            + f"#   end_timestamp   = '{et_str}'  (保持不变)\n"
            + f"#   duration        = '{new_dur_min}min'  (原定时长 +{add}min)\n"
            + "# 1. 本轮直接将 start_timestamp, end_timestamp, duration 设为上述值;\n"
            + "# 2. 其余应用、业务、画质、时延按用户话语正常填入 state!\n"
        )
    return (
        head
        + f"# 1. 将用户话语中的开始时间偏移 {off:+d} 分钟作为 state.start_timestamp;\n"
        + "# 2. 结束时间 state.end_timestamp 保持不变;\n"
        + f"# 3. 保障总时长 state.duration 在原定时长基础上增加 {add} 分钟;\n"
        + "# 4. 其余应用、业务、画质、时延正常填入 state!\n"
    )


# ====================================================================
# 2. 工作记忆 (Working Memory) 规则模板
# ====================================================================

WORKING_RECORD_DISAMBIG_RULE = (
    "# === WORKING RECORD DISAMBIGUATION RULE (多笔历史工单待消歧规则, 本轮生效, 优先级最高) ===\n"
    "# 用户引用了历史意图保障单规格并给出了今日时间，但历史账本中存在多笔工单，本轮需向用户确认按哪一笔的规格。\n"
    "# 必须遵守:\n"
    "# 1. 本轮只填 application_name, service_name 以及用户给出的今日时间 (start_timestamp, end_timestamp, duration);\n"
    "# 2. 本轮严禁给 state.resolution 和 state.rtt 赋值 (必须留空，即使话语末尾带有『抓紧别掉链子』等口头催促词也不要查黑话词典填 rtt，必须等下一轮用户选定按哪一笔历史工单后再补齐画质与时延)!\n"
)

WORKING_RECORD_RESCHEDULE_CONFIRM_RULE = (
    "# === WORKING RECORD RESCHEDULE CONFIRM RULE (工单改期确认轮保持规则, 本轮生效, 优先级最高) ===\n"
    "# 上一轮 (Turn 1) 已经完成了提前/推迟换算，prev_state 中的 start_timestamp 与 end_timestamp 已经是改期后的最终时间!\n"
    "# 本轮用户只是口头确认（如『确认提前并按新时间修改』），严禁再次调用 find_timestamp 减去或加上 1 小时!\n"
    "# 必须沿用已有意图段头 (如 # intent: I1), 代码块严格原样写:\n"
    "#   state.application_name = state.application_name\n"
    "#   state.service_name = state.service_name\n"
    "#   state.resolution = state.resolution\n"
    "#   state.rtt = state.rtt\n"
    "#   state.start_timestamp = state.start_timestamp\n"
    "#   state.end_timestamp = state.end_timestamp\n"
    "#   state.duration = state.duration\n"
)

WORKING_RECORD_CONFIRM_RULE = (
    "# === WORKING RECORD CONFIRM RULE (工单复用确认轮保持规则, 本轮生效, 优先级最高) ===\n"
    "# 上一轮已补齐全部 7 个槽位，本轮用户确认开通（即使附带『画面要透亮就行』『顺滑』等口头语，也严禁修改 resolution 或 rtt）:\n"
    "# 必须沿用已有意图段头 (如 # intent: I1), 并用 state.xxx = state.xxx 完整保留 prev_state 中的全部 7 个槽位!\n"
)


def format_working_reschedule_rule(
    target_wr: dict[str, Any],
    resched_start: str | None,
    resched_end: str | None,
    resched_dur: Any,
    pre_shift_desc: str = "",
) -> str:
    """渲染工作记忆历史预约工单改期规则。"""
    time_lines = (
        f"{pre_shift_desc}"
        f"#   改期后最终 start_timestamp = {resched_start!r}\n"
        f"#   改期后最终 end_timestamp   = {resched_end!r}\n"
        f"#   duration                   = {resched_dur!r}\n"
        if resched_start and resched_end
        else "#   (目标工单缺少可用时段: 时间按用户话语与工单时段自行换算)\n"
    )
    return (
        "# === WORKING RECORD RESCHEDULE RULE (历史预约工单改期规则, 本轮生效, 优先级最高) ===\n"
        f"# 用户要求将之前预约的保障工单改期，记忆系统已定位目标工单 [{target_wr.get('record_id')}] 并完成改期时段对齐:\n"
        f"#   application_name = {target_wr.get('application_name')!r}\n"
        f"#   service_name     = {target_wr.get('service_name')!r}\n"
        f"#   resolution       = {target_wr.get('resolution')!r}\n"
        f"#   rtt              = {target_wr.get('rtt')!r}\n"
        f"{time_lines}"
        "# 必须遵守:\n"
        "# 1. 本轮是当前对话的第 1 轮 (prev_state 为空)，段头必须写 # intent: new (严禁写 # intent: I1);\n"
        "# 2. 代码块中必须显式写出全部 7 个槽位赋值 (严禁省略 application_name, service_name, resolution, rtt):\n"
        f"#    state.application_name = {target_wr.get('application_name')!r}\n"
        f"#    state.service_name = {target_wr.get('service_name')!r}\n"
        f"#    state.resolution = {target_wr.get('resolution')!r}\n"
        f"#    state.rtt = {target_wr.get('rtt')!r}\n"
        f"#    state.timestamp = find_timestamp(start={resched_start!r}, end={resched_end!r}, duration={resched_dur!r})\n"
        "# 3. 严禁对 start_timestamp 再次做提前/推迟换算!\n"
    )


def format_working_reuse_rule(target_wr: dict[str, Any]) -> str:
    """渲染工作记忆跨期引用历史工单规格规则。"""
    return (
        "# === WORKING RECORD REUSE RULE (跨期引用历史工单规格规则, 本轮生效, 优先级最高) ===\n"
        f"# 用户跨期引用了历史意图保障单 [{target_wr.get('record_id')}] 的业务与质量规格，但本轮尚未给出今天的保障起止时间:\n"
        f"#   application_name = {target_wr.get('application_name')!r}\n"
        f"#   service_name     = {target_wr.get('service_name')!r}\n"
        f"#   resolution       = {target_wr.get('resolution')!r}\n"
        f"#   rtt              = {target_wr.get('rtt')!r}\n"
        "# 必须遵守:\n"
        "# 1. 本轮必须把上述历史工单的 4 个规格槽位原样写入 state:\n"
        f"#    state.application_name = {target_wr.get('application_name')!r}\n"
        f"#    state.service_name     = {target_wr.get('service_name')!r}\n"
        f"#    state.resolution       = {target_wr.get('resolution')!r}\n"
        f"#    state.rtt              = {target_wr.get('rtt')!r}\n"
        "#    注意: 即使话语中带有『画质画面通透就行』『特写清晰』『顺滑』等口语化形容，也是对该历史工单规格的复述确认，严禁将其降级替换为通用默认值 '720p'!\n"
        "# 2. 本轮严禁填写 start_timestamp, end_timestamp, duration (必须留空，等待下一轮用户告知今天具体时段)!\n"
    )


def format_working_disambig_resolve_rule(twr: dict[str, Any]) -> str:
    """渲染工作记忆多工单消歧选定规则。"""
    res_v, rtt_v = twr.get("resolution"), twr.get("rtt")
    rule2 = (
        f"# 2. 无论本轮用户话语是说『按最近那次』还是重申今日起止时段，代码块中都必须显式写出:\n"
        f"#    state.resolution = {res_v!r}\n"
        f"#    state.rtt = {rtt_v!r}\n"
        "#    严禁漏写 state.resolution 和 state.rtt!\n"
        if res_v and rtt_v
        else "# 2. 账本中该工单缺少画质/时延记录, 不要臆造取值, 仅按用户话语填写!\n"
    )
    return (
        "# === WORKING RECORD DISAMBIGUATION RESOLVE RULE (历史工单消歧选定规则, 本轮生效, 优先级最高) ===\n"
        f"# 用户本轮已完成历史工单消歧确认，锁定目标历史工单规格 [{twr.get('record_id')}]:\n"
        f"#   resolution = {res_v!r}\n"
        f"#   rtt        = {rtt_v!r}\n"
        "# 必须遵守:\n"
        "# 1. 沿用已有意图段头 (如 # intent: I1), 保留 prev_state 已有的 application_name, service_name, start_timestamp, end_timestamp, duration;\n"
        + rule2
    )


def format_working_reuse_time_fill_rule(
    twr: dict[str, Any],
    rec_st_str: str | None = None,
    rec_et_str: str | None = None,
) -> str:
    """渲染工作记忆历史工单复用补充今日时段规则。"""
    if rec_st_str and rec_et_str:
        return (
            "# === WORKING RECORD REUSE TIME FILL RULE (历史工单复用补充今日时段规则, 本轮生效, 优先级最高) ===\n"
            f"# 用户本轮未报新的时段, 而是沿用历史工单 [{twr.get('record_id')}] 的时段 (已锚定到今天):\n"
            f"#   start_timestamp = '{rec_st_str}'\n"
            f"#   end_timestamp   = '{rec_et_str}'\n"
            f"#   duration        = {twr.get('duration')!r}\n"
            "# 1. 沿用已有意图段头 (如 # intent: I1), 保留 prev_state 中的 application_name, service_name, resolution, rtt;\n"
            "# 2. 将上述时段写入 state!\n"
        )
    return (
        "# === WORKING RECORD REUSE TIME FILL RULE (历史工单复用补充今日时段规则, 本轮生效, 优先级最高) ===\n"
        "# 上一轮已从历史工单填好 application_name, service_name, resolution, rtt。本轮用户给出了今日具体保障起止时间与时长:\n"
        "# 1. 沿用已有意图段头 (如 # intent: I1), 用 state.xxx = state.xxx 完整保留 prev_state 中的 application_name, service_name, resolution, rtt;\n"
        "# 2. 解析本轮用户话语中的今日起止时间与时长，通过 find_timestamp(start=..., end=..., duration=...) 填入 state!\n"
    )


# ====================================================================
# 3. 事实记忆 (Factual Memory) 与冷启动规则模板
# ====================================================================

SERVICE_NAME_SHORT_VIDEO_RULE = (
    "# === SERVICE NAME RULE (服务名精确口径) ===\n"
    "# 用户本轮明确说了“短视频”, 矩阵中该应用支持 '短视频', 请直接填 state.service_name = '短视频' (不要改写为 '视频')。\n"
)

SERVICE_NAME_GAME_RULE = (
    "# === SERVICE NAME RULE (服务名精确口径) ===\n"
    "# 用户本轮明确说了“游戏”, 请直接填 state.service_name = '游戏'。\n"
)

COLD_START_LITERAL_RULE = (
    "# === COLD-START LITERAL EXTRACTION RULE (非记忆触发轮字面提取规则, 本轮生效, 优先级最高) ===\n"
    "# 用户本轮未说『老规矩/按上次』等记忆触发词，也未提及画质或时延。本轮必须仅提取用户字面说出的槽位:\n"
    "# 严禁从长期记忆或用户画像中自动借用 resolution、rtt、end_timestamp 或 duration (用户没说的槽位一律留空，等待下一轮追问补充)!\n"
)

MULTI_TURN_SLOT_COMPLETION_RULE = (
    "# === MULTI-TURN SLOT COMPLETION RULE (多轮证据补槽规则, 本轮生效, 优先级最高) ===\n"
    "# 若本轮用户补充了持续时长 (如『预计持续90min』『持续2个小时』) 或结束时间，而 prev_state 已有 start_timestamp:\n"
    "# 注意: TIME EVIDENCE 只是系统提供给你的参考文本，不会自动写入 state! 你必须在下方 Python 代码块中显式调用:\n"
    "#   state.timestamp = find_timestamp(start=..., end=..., duration=...)\n"
    "# 把 start_timestamp、end_timestamp 与 duration 一并写入 state，严禁只写 resolution/rtt 而漏写 state.timestamp!\n"
)

MEMORY_FOLLOWUP_UPDATE_RULE = (
    "# === MEMORY FOLLOW-UP UPDATE RULE (老规矩后续轮微调规则, 本轮生效, 优先级最高) ===\n"
    "# 上一轮已通过长期记忆补齐了完整意图状态 prev_state。本轮用户在老规矩基础上做局部调整:\n"
    "# 1. 必须沿用已有意图段头 (如 # intent: I1), 严禁新建意图或丢弃已有槽位;\n"
    "# 2. 未调整的槽位一律从 prev_state 沿用 (state.application_name = state.application_name 等);\n"
    "# 3. 若用户延长了保障时长 (如延长到 180 分钟), 更新 duration='180min', 并基于 state.start_timestamp 重新推算 end_timestamp;\n"
    "# 4. 若用户临时调整了画质或时延 (如画质临时调到 720p、时延压到 30ms), 将 state.resolution 和 state.rtt 更新为本轮新值, 时间字段继续沿用 prev_state!\n"
)

MEMORY_FOLLOWUP_CONFIRM_RULE = (
    "# === MEMORY FOLLOW-UP CONFIRM RULE (老规矩确认轮保持规则, 本轮生效, 优先级最高) ===\n"
    "# 上一轮已通过长期记忆补齐了完整 7 个槽位, 本轮用户是在确认直接开通 (即使顺带重申了黑话定义含义, 参数也已在上一轮补齐):\n"
    "# 必须沿用已有意图段头 (如 # intent: I1), 并用 state.xxx = state.xxx 完整保留 prev_state 中的全部 7 个槽位, 严禁改动或留空!\n"
)


def format_memory_fill_rule(subtype: str, memory_key: str, rs: dict[str, Any]) -> str:
    """渲染老规矩/长期记忆命中补槽规则。"""
    return (
        "# === MEMORY FILL RULE (老规矩/长期记忆命中有据可查, 本轮生效, 优先级最高) ===\n"
        f"# 用户本轮触发了长期记忆指代, 记忆系统检索到唯一匹配的长期记忆 [{subtype}: {memory_key}]:\n"
        f"#   application_name = {rs['application_name']!r}\n"
        f"#   service_name     = {rs['service_name']!r}\n"
        f"#   resolution       = {rs['resolution']!r}\n"
        f"#   rtt              = {rs['rtt']!r}\n"
        f"#   start_timestamp  = {rs['start_timestamp']!r}\n"
        f"#   end_timestamp    = {rs['end_timestamp']!r}\n"
        f"#   duration         = {rs['duration']!r}\n"
        "# 必须遵守:\n"
        "# 1. 本轮必须把上述记忆中的 7 个槽位全部写入 state (含 resolution, rtt, 以及 find_timestamp(start=..., end=..., duration=...));\n"
        "# 2. 若用户本轮话语中对某个槽位给出了明确的标准档位新值 (如 720p/1080p/30ms/50ms) 或命中了下方【个人黑话词典】映射值, 该槽位以新值为准; 若话语中只是用未带具体数值的口语词 (如『人像要细腻』『画面通透』『顺滑』) 修饰, 严禁将其降级为默认 720p, 一律保持上述记忆值 resolution="
        f"{rs['resolution']!r}, rtt={rs['rtt']!r}!\n"
    )


def format_unclarified_slang_rule(slang_phrase: str, target_slot_name: str) -> str:
    """渲染未知个人自定义表述待消歧规则。"""
    return (
        "# === UNCLARIFIED SLANG RULE (未知个人黑话待消歧规则, 本轮生效, 优先级最高) ===\n"
        f"# 用户本轮说出的「{slang_phrase}」是首次出现的个人黑话, 尚未在长期记忆词典中登记, 且本轮未给出对应的标准档位数值。\n"
        "# 必须遵守:\n"
        f"# 1. 本轮严禁给 state.{target_slot_name} 赋值 (代码块中严禁写 state.{target_slot_name} = ...，严禁按通用规则兜底填 '720p'，必须留空等待系统追问后用户在下一轮澄清具体数值);\n"
        "# 2. 本轮用户明确给出的其他槽位 (如时延/时长/开始时间) 以及上一轮 prev_state 已有的槽位必须正常赋值和沿用!\n"
    )


# ====================================================================
# 4. 时间锚点、画质映射、候选列举/选择模式与待补应用名规则模板
# ====================================================================

TIME_EXPLICIT_FIRST_RULE = (
    "# === TIME RULE (本轮明说时间优先, 本轮生效, 优先级最高) ===\n"
    "# 用户本轮明说了时间(见 TIME EVIDENCE)。时间槽(start/end/duration)一律以\n"
    "# 本轮明说为准, 禁止取 records 的 completed_at 或画像时段覆盖/替代明说时间。\n"
)

TIME_PARTIAL_CANDIDATE_RULE = (
    "# === TIME RULE (部分槽位候选未定, 本轮生效, 优先级最高) ===\n"
    "# 用户话语含未定词, 但时间本体已明说且无歧义 (见 TIME EVIDENCE 组装结论)。\n"
    "# 未定的只是其他槽位 (如应用/时延)——start/end 按 TIME EVIDENCE 组装结论\n"
    "# 【照常落地】, 不要因其他槽位未定而把时间一并留空。\n"
)

TIME_UNDECIDED_CANDIDATE_RULE = (
    "# === TIME RULE (候选时段未定, 本轮生效, 优先级最高) ===\n"
    '# 用户给出多个候选时段且尚未最终确定("或者/还没定/不确定")。\n'
    "# start/end 一律留空, 禁止自行挑选某个候选落槽; 只登记无歧义字段(如明说 duration)。\n"
)


def format_time_anchor_block(rows: str) -> str:
    """渲染 TIME ANCHOR 预计算日期锚点段。"""
    return (
        "# === TIME ANCHOR (本轮日期锚点, 系统已预计算) ===\n"
        f"# {rows}\n"
        "# 相对日期一律按上表查取, 禁止自行加减天数推算。\n"
    )


def format_time_evidence_block(lines: list[str]) -> str:
    """渲染 TIME EVIDENCE 明说时间系统预解析段。"""
    return (
        "# === TIME EVIDENCE (本轮话语明说时间的系统预解析, 直接引用勿重算) ===\n"
        + "\n".join(lines)
        + "\n"
    )


def format_time_contradiction_rule(
    start_str: str, end_str: str, duration_min: int | None
) -> str:
    """渲染明说时间互相矛盾规则段。"""
    dur_part = f", duration={duration_min}min" if duration_min is not None else ""
    return (
        "# === TIME RULE (检测到明说时间互相矛盾, 本轮生效, 优先级最高) ===\n"
        f"# 系统解析: start={start_str}, end={end_str}{dur_part}, 存在矛盾。\n"
        "# 按口径照字面录入用户明说值, 禁止自行调和/翻转/倒推覆盖, 等待用户后续更正。\n"
    )


def format_time_profile_conflict_rule(slot: str, dur: str, span: int) -> str:
    """渲染画像常用时段与常用时长不一致规则段。"""
    return (
        "# === TIME RULE (画像时段/时长不一致, 本轮生效, 优先级最高) ===\n"
        f'# 画像"常用开始时段"={slot}(跨度{span}min) 与 "常用保障时长"={dur} 不一致。\n'
        '# 按口径一律以"常用开始时段"为准: start/end 取时段起止, '
        f'duration={span}min, 忽略"常用保障时长"。\n'
    )


def format_quality_hint_block(qlines: list[str]) -> str:
    """渲染 QUALITY HINT 画质词精确映射段。"""
    return (
        "# === QUALITY HINT (画质词精确映射, 系统查表, 优先级最高) ===\n"
        + "\n".join(qlines)
        + "\n# 上表为标准映射的精确值, 直接采用; 禁止使用默认值覆盖。\n"
    )


def format_candidate_stub_full_branch(stubs: list[str], time_keep_txt: str) -> str:
    """渲染候选选择轮：桩意图全量落地分支。"""
    return (
        "# 系统核验: 当前桩意图 "
        + "、".join(stubs)
        + " 的 application_name 为空(用户从未明说 app)。\n"
        + "# 本轮选中候选后, 必须把选中记录的 application_name/service_name/resolution/rtt\n"
        + "# 【全量落地】到该桩意图(段头写其 id); 时间等用户明说字段保持不变。\n"
        + time_keep_txt
    )


def format_candidate_stub_scoped_branch(
    scope_json: str, fld_txt: str, assigns: str, time_keep_txt: str
) -> str:
    """渲染候选选择轮：桩意图按 scope 部分字段落地分支。"""
    return (
        f"# 系统核验: 用户本轮仅要求按选中记录取部分字段 (语义判定 scope={scope_json})。\n"
        f"# 本轮【只允许】把 {fld_txt} 写入已有意图 (段头写其 id): {assigns};\n"
        "# 【严禁】落地未列出的字段, 尤其严禁把选中记录的 application_name/service_name\n"
        "#  当作新保障意图办理——选中记录只是字段来源, 不是新办业务。\n"
        '# 【记录指代已消解】用户明确选定了候选, RECORD RULE 的"悬空待消歧"条款\n'
        '#  自本轮起失效——scope 列出的字段【必须照常落地】, 不得再以"悬空"为由拒写;\n'
        "#  也【严禁新建意图】承接选中记录——字段写入已有意图, 不另办业务。\n"
        + time_keep_txt
    )


def format_candidate_placeholder_branch(placeholders: list[str], time_keep_txt: str) -> str:
    """渲染候选选择轮：抢跑占位意图覆盖落地分支。"""
    return (
        "# 系统核验: 意图 "
        + "、".join(placeholders)
        + " 的 app/svc 是候选列表中【未被选中】的候选 (t1 抢跑占位, 非用户明说)。\n"
        + "# 按用户本轮选择, 把该意图身份【改写为选中候选】并全量落地 app/svc/resolution/rtt;\n"
        + "# 时间等用户明说字段保持不变; 未被选中的候选不再保留, 也不另建意图。\n"
        + time_keep_txt
    )


def format_candidate_existing_app_branch(miss_lines: list[str]) -> str:
    """渲染候选选择轮：已有应用意图补齐悬空字段分支。"""
    miss_txt = (
        "# 逐意图悬空字段补齐指令 (选中记录只是字段来源; 其 app/svc 与意图不一致是正常的,\n"
        "# 禁止因身份不一致而拒绝补字段):\n" + "".join(miss_lines)
        if miss_lines
        else ""
    )
    return (
        "# 系统核验: 当前各意图的 application_name 均为用户此前明说。\n"
        "# 本轮选中候选后, 【严禁改写任何意图的身份】, 只把选中记录中此前悬空的字段\n"
        "# (如 rtt)写入对应意图; 用户明说的其他字段严禁被记录值覆盖。\n"
        + miss_txt
    )


def format_candidate_selection_mode_block(anchor: str, pick_txt: str, branch: str) -> str:
    """组装 CANDIDATE MODE 选择轮完整提示块。"""
    return (
        "# === CANDIDATE MODE (选择轮, 本轮生效, 优先级最高) ===\n"
        "# 用户在选定此前列出的候选记录。必须遵守:\n"
        + anchor
        + pick_txt
        + branch
        + "# 2. 未被选中的候选: 不再保留, 也不为其输出任何段;\n"
        "# 3. 记录的 completed_at 是记录完成时间, 不是保障时间, 禁止当作 start/end。\n"
    )


def format_candidate_listing_mode_block(lines: list[str], has_preferential: bool) -> str:
    """组装 CANDIDATE MODE 候选列举轮完整提示块。"""
    prefer = (
        '# 5. 用户话语里的偏好 ("最好是X那条") 不是最终选择, 仍须全部列出等用户确认;\n'
        "#    禁止只落地偏好那条。\n"
        if has_preferential
        else ""
    )
    return (
        "# === CANDIDATE MODE (候选列举轮, 本轮生效, 优先级最高) ===\n"
        "# 用户要求按记录办理但系统核验候选不唯一。候选列表如下 (按客服话术顺序):\n"
        + "\n".join(lines)
        + "\n"
        "# 必须遵守:\n"
        "# 1. 本轮【不要输出 # candidate: 段】; 记录身份字段 (application_name/service_name/\n"
        "#    resolution/rtt) 一律悬空, 不落地到任何保障意图;\n"
        "# 2. 用户本轮明说的字段 (如时间/画质) 正常落地到意图段 (意图段只含明说字段);\n"
        "# 3. # summary 写明: 约束命中 N 条候选, 等待用户选择。\n"
        + prefer
    )


def format_pending_app_rule_block(id_list: str, stub_txt: str) -> str:
    """组装 PENDING-APP RULE 应用名未明说规则块。"""
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


def format_record_defer_rule_block(reason: str, is_multi_cand: bool) -> str:
    """组装 RECORD RULE 记录指代存在歧义规则块。"""
    if is_multi_cand:
        return (
            "# === RECORD RULE (记录指代存在歧义, 本轮生效, 优先级最高) ===\n"
            f"# 系统核验: {reason}。\n"
            "# 按数据口径: 被指代的字段(如 rtt)本轮【留空】——不落地到保障意图, 等待用户选择后再取值;\n"
            "# (# candidate: 候选段内的 res/rtt 赋值是列举动作, 不受此限——须按 CANDIDATE MODE 全部列出);\n"
            '# 禁止提前把任何 records 的字段写进保障意图——本条覆盖系统提示中"按指代条件过滤后取最近"的一般规则;\n'
            '# # summary 必须写明"记录字段悬空待消歧"。\n'
        )
    return (
        "# === RECORD RULE (记录指代存在歧义, 本轮生效, 优先级最高) ===\n"
        f"# 系统核验: {reason}。\n"
        "# 按数据口径: 被指代的字段(如 rtt)本轮【留空】, 等待客服列出候选、用户选择后再取值;\n"
        '# 禁止提前从任何 records 取值——本条覆盖系统提示中"按指代条件过滤后取最近"的一般规则;\n'
        '# # summary 必须写明"记录字段悬空待消歧"。\n'
    )
