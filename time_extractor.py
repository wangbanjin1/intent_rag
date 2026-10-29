# -*- coding: utf-8 -*-
"""time_extractor.py: v27 明说时间规则提取器 (生成侧证据注入, 非后处理)。

设计 (docs/plan_time_anchor_20260920.md):
  规则负责算(确定性、零误差), 模型负责理解(哪段话语对应哪个意图、该不该落槽)。
  本模块对当前轮 utterance 做时间表达式提取+预解析, 产出 TimeEvidence,
  由 pipeline 组装为 TIME ANCHOR / TIME EVIDENCE / TIME RULE 段注入 prompt。
  严禁用于覆盖/改写模型 pred (v23/v9.0 已证后处理路线净负)。

宁缺毋滥: 任何一步解析不确定即放弃该条候选, 不产出; 全程不调 LLM。
正则模式借鉴 ref_code/time_rule_full.py (另一项目的后处理版本), 此处只取
"提取+预解析"部分, 不做 output 改写。

入口:
  parse_reference_time(s) -> datetime | None
  build_date_table(ref_dt, utterance) -> list[(label, date_str)] | None  # 相对日期词触发
  extract(utterance, ref_dt) -> TimeEvidence | None
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

DT_FMT = "%Y年%m月%d日%H时%M分"

_WEEKDAYS = "一二三四五六日"

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# 相对日期词 -> 偏移天数 (date_table 触发词表, d6 词汇扫描 2026-09-20:
# 明天130/凌晨87/今天58/后天19/今晚2, 无大后天/昨天/周X; 大后天保留兜底)
_RELATIVE_DAYS = {"今天": 0, "今晚": 0, "明天": 1, "后天": 2, "大后天": 3}

_UNDECIDED_WORDS = ("还没定", "没定", "不确定", "先确认", "待定", "还没想好")

_NEGATE_WORDS = ("不是", "而非", "而不是")

# 槽位命名 -> 角色 (改口轮直判, 如 "结束时间不是8点半, 改成今天中午12点")
_SLOT_ROLE = (("开始时间", "start"), ("结束时间", "end"), ("截止时间", "end"))


def parse_reference_time(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, DT_FMT)
    except (ValueError, TypeError):
        return None


def _fmt(dt: datetime) -> str:
    return dt.strftime(DT_FMT)


def _cn_num(s: str | None) -> int | None:
    """中文/阿拉伯小数字 -> int。"""
    if s is None:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s == "半":
        return 30
    if s == "一刻":
        return 15
    if s == "三刻":
        return 45
    if "十" in s:
        left, _, right = s.partition("十")
        tens = _CN_DIGITS.get(left, 1 if left == "" else None)
        ones = _CN_DIGITS.get(right, 0 if right == "" else None)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return _CN_DIGITS.get(s)


@dataclass
class Mention:
    """一条明说时间点。"""
    raw: str                 # 原文出处
    dt: datetime | None      # 预解析绝对时间 (无日期锚时为 None, 仅时刻)
    clock: str               # "HH:MM" 展示用
    role: str = ""           # "start" / "end" / "" (邻近标记词判定)
    day_inherited: bool = False  # 日期锚来自句级继承而非本短语
    cand_adj: bool = False   # 紧邻 或者/还是, 属候选选项 (候选模式下不参与组装)
    period: str | None = None  # 原文时段词 (跨夜滚动判定用)
    hour_raw: int = 0        # 原文小时 (未做时段位移)
    minute_raw: int = 0
    pm_inherited: bool = False  # 裸时刻被句级 PM 主导时段位移过 ("晚上11点到1点" 重解读用)


@dataclass
class TimeEvidence:
    mentions: list[Mention] = field(default_factory=list)
    durations: list[tuple[str, int]] = field(default_factory=list)  # (原文, 分钟)
    start: datetime | None = None   # 组装的 start (明说或 end-duration 倒推)
    end: datetime | None = None
    duration_min: int | None = None  # 仅当全句唯一时长时取值
    start_derived: bool = False
    end_derived: bool = False
    has_candidate: bool = False      # 多候选时段(时间点)+"或者/还没定"模式
    dur_candidate: bool = False      # 时长候选 (S-000133 "选三个半小时还是三个小时"): 只抑制时长断言, 不影响 start/end
    has_contradiction: bool = False  # start>end 或 三要素互斥
    has_relative_day: bool = False   # 含相对日期词


# ---------------- 候选时间点提取 ----------------

_HOUR = r"(?:[零一二三四五六七八九十两]{1,3}|\d{1,2})"
_MIN = r"(?:一刻|三刻|半|[零一二三四五六七八九十两]{1,4}|\d{1,2})"
_POINT_RE = re.compile(
    rf"(?P<date>\d{{1,2}}月\d{{1,2}}[号日])?"
    rf"(?P<day>今天|今晚|明天|后天|大后天)?"
    rf"(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上|晚|今晚)?"
    rf"(?P<hour>{_HOUR})[点时]"
    rf"(?P<minute>{_MIN}分?|{_MIN}刻)?"
)
_DATE_ONLY_RE = re.compile(r"\d{1,2}月\d{1,2}[号日]")


_PM_WORDS = ("下午", "傍晚", "晚上", "今晚")
_AM_WORDS = ("上午", "早上", "凌晨", "中午")
_PERIOD_SCAN_RE = re.compile(r"下午|傍晚|晚上|今晚|上午|早上|凌晨|中午")
_CLAUSE_SEPS = "，。；！？\n"


def _local_pm(utterance: str, pos: int, default_pm: bool) -> bool:
    """裸时刻的 PM 判定: 优先所在子句内最近的时段词 (混合时段句, S-000075
    "今天晚上7点到8点…今天上午6点到9点" 第二段须跟随上午), 子句没有回退句级主导。"""
    l = max(utterance.rfind(c, 0, pos) for c in _CLAUSE_SEPS)
    clause_before = utterance[l + 1:pos]
    last = None
    for mm in _PERIOD_SCAN_RE.finditer(clause_before):
        last = mm.group(0)
    if last is None:
        return default_pm
    return last in _PM_WORDS


def _resolve_clock(period: str | None, hour: int,
                   default_pm: bool = False) -> int | None:
    """时段词 -> 24h 小时。凌晨12点=0 (S-000104), 中午12点=12, 下午/晚上 X<12 +12。
    default_pm: 句级主导时段为下午/晚上类时, 裸时刻 X<12 继承 +12
    (区间终点常省略时段词: "晚上7点到8点半"=19:00-20:30, v27 run1 破窗根因)。"""
    if period == "凌晨":
        if hour == 12:
            return 0
        return hour if 0 <= hour <= 6 else None  # "凌晨9点" 非法, 放弃
    if period in ("早上", "上午"):
        return hour if 0 < hour < 12 else None
    if period == "中午":
        if hour in (11, 12):
            return hour
        return hour + 12 if hour in (1, 2) else None
    if period in ("下午", "傍晚"):
        if hour == 12:
            return 12
        return hour + 12 if 0 < hour < 12 else None
    if period in ("晚上", "晚", "今晚"):
        if hour == 12:
            return 24  # 晚上12点=次日00:00, 由调用方 timedelta 自然滚动
        return hour + 12 if 0 < hour < 12 else None
    # 无时段词: 句级 PM 主导时 X<12 继承 +12; 否则原样 (0~23), 歧义留给模型
    if default_pm and 0 < hour < 12:
        return hour + 12
    return hour if 0 <= hour <= 23 else None


def _parse_date_str(s: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{1,2})月(\d{1,2})[号日]", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _role_of(utterance: str, start: int, end: int) -> str:
    """由邻近标记词判定 start/end 角色。优先级: 槽位命名 > 前/后置标记。"""
    # 槽位命名 (改口轮 "结束时间不是X, 改成Y" 型, 槽位名可能离时间点较远, 窗口放宽)
    before16 = utterance[max(0, start - 16):start]
    slot_pos = -1
    role = ""
    for name, r in _SLOT_ROLE:
        p = before16.rfind(name)
        if p > slot_pos:
            slot_pos, role = p, r
    if role:
        return role
    before = utterance[max(0, start - 8):start]
    after = utterance[end:end + 10]
    if re.search(r"(从|自|起于|始于|开始|就定|定在|定为)$", before):
        return "start"
    if re.search(r"(到|至|直到|截止|止于|结束)$", before):
        return "end"
    if re.match(r"^(开始|起)", after):
        return "start"
    if re.match(r"^(结束|截止)", after):
        return "end"
    if re.match(r"^(到|至)", after):
        return "start"  # "A到B" 结构中 A 是起点
    return ""


def _negated(utterance: str, start: int) -> bool:
    return any(w in utterance[max(0, start - 6):start] for w in _NEGATE_WORDS)


def _extract_mentions(utterance: str, ref_dt: datetime) -> tuple[list[Mention], bool]:
    """提取全部时间点候选并预解析。返回 (mentions, has_relative_day)。"""
    anchors: list[tuple[str, object]] = []
    has_relative = False
    for m in _DATE_ONLY_RE.finditer(utterance):
        md = _parse_date_str(m.group(0))
        if md and ("abs", md) not in anchors:
            anchors.append(("abs", md))
    for w, off in _RELATIVE_DAYS.items():
        if w in utterance:
            has_relative = True
            anchors.append(("rel", off))
    uniq = {a[1] for a in anchors}
    default_anchor = anchors[0] if len(uniq) == 1 else None
    if default_anchor is None and not anchors:
        # 全句无任何日期锚 -> 兜底锚定今天 (d6 扫描 0 例, 纯稳健性兜底, 跨夜滚动依赖 dt)
        default_anchor = ("rel", 0)
    # 句级主导时段: 区间终点常省略时段词 ("晚上7点到8点半"), 裸时刻继承 PM 语义
    default_pm = bool(re.search(r"下午|傍晚|晚上|今晚", utterance))
    # 全句无任何时段词 (上午/下午/晚上…): 用于参考时间感知的裸时刻 PM 消歧
    any_period = bool(_PERIOD_SCAN_RE.search(utterance))

    mentions: list[Mention] = []
    seen_spans: list[tuple[int, int]] = []
    for m in _POINT_RE.finditer(utterance):
        span = m.span()
        if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
            continue
        if _negated(utterance, span[0]):
            continue
        hour = _cn_num(m.group("hour"))
        if hour is None:
            continue
        minute_raw = m.group("minute")
        minute = 0
        if minute_raw:
            minute = _cn_num(minute_raw.rstrip("分"))
            if minute is None or not (0 <= minute <= 59):
                continue
        bare_pm = _local_pm(utterance, span[0], default_pm) if m.group("period") is None else False
        clock_h = _resolve_clock(m.group("period"), hour, default_pm=bare_pm)
        if clock_h is None:
            continue
        if (m.group("period") is None and m.group("day") is None
                and m.group("date") is None and default_anchor is None):
            continue  # 裸 "X点" 且无句级日期锚可继承: 歧义大, 放弃 (宁缺毋滥)

        day_inherited = False
        base_date: datetime | None = None
        if m.group("date"):
            md = _parse_date_str(m.group("date"))
            if md:
                base_date = ref_dt.replace(month=md[0], day=md[1], hour=0, minute=0)
        elif m.group("day"):
            base_date = ref_dt.replace(hour=0, minute=0) + timedelta(days=_RELATIVE_DAYS[m.group("day")])
        elif default_anchor is not None:
            if default_anchor[0] == "abs":
                base_date = ref_dt.replace(month=default_anchor[1][0],
                                           day=default_anchor[1][1], hour=0, minute=0)
            else:
                base_date = ref_dt.replace(hour=0, minute=0) + timedelta(days=default_anchor[1])
            day_inherited = True

        dt: datetime | None = None
        if base_date is not None:
            dt = base_date + timedelta(hours=clock_h, minutes=minute)
        # 参考时间感知的裸时刻消歧: 全句无时段词、锚定当天、按字面已早于参考时间 (容差 60min),
        # 而 +12h 后不早于参考时间 -> 按下午/晚上理解。
        # 例: 参考时间 13:45 说 "今天是从两点弄到四点" -> 14:00-16:00 (而非已过去的 02:00-04:00)
        if (dt is not None and m.group("period") is None and not any_period and not bare_pm
                and 0 < clock_h < 12 and dt.date() == ref_dt.date()
                and dt < ref_dt - timedelta(minutes=60)
                and dt + timedelta(hours=12) >= ref_dt - timedelta(minutes=60)):
            dt = dt + timedelta(hours=12)
            clock_h += 12
            bare_pm = True
        clock = f"{clock_h % 24:02d}:{minute:02d}"
        mentions.append(Mention(
            raw=m.group(0), dt=dt, clock=clock,
            role=_role_of(utterance, span[0], span[1]),
            day_inherited=day_inherited,
            cand_adj=("或者" in utterance[max(0, span[0] - 4):span[0]]
                      or "还是" in utterance[max(0, span[0] - 4):span[0]]
                      or "或者" in utterance[span[1]:span[1] + 4]
                      or "还是" in utterance[span[1]:span[1] + 4]),
            period=m.group("period"), hour_raw=hour, minute_raw=minute,
            pm_inherited=(bare_pm and 0 < hour < 12),
        ))
        seen_spans.append(span)
    return mentions, has_relative


# ---------------- 时长提取 ----------------

_DUR_MIN = rf"(?:{_HOUR}|\d{{1,4}})"
_DUR_UNIT = rf"(?:半个?(?:小时|钟头)|{_HOUR}个?半(?:小时|钟头)|{_HOUR}个?(?:小时|钟头)半|{_HOUR}\s*个?(?:小时|钟头)|{_DUR_MIN}\s*分钟|\d{{1,4}}\s*min(?![a-zA-Z]))"
_DUR_RE = re.compile(rf"(?P<dur>{_DUR_UNIT})")
_DUR_LEADS = ("持续", "时长", "历时", "一共", "总共", "共计", "用", "办", "开", "播",
              "看", "玩", "保障", "延长", "改成", "改为", "定")


def _duration_minutes(raw: str) -> int | None:
    s = raw.replace("钟头", "小时")
    if re.fullmatch(r"半个?小时", s):
        return 30
    m = re.fullmatch(rf"({_HOUR})个?半小时", s) or re.fullmatch(rf"({_HOUR})个?小时半", s)
    if m:
        h = _cn_num(m.group(1))
        return h * 60 + 30 if h is not None else None
    m = re.fullmatch(rf"({_HOUR})\s*个?小时", s)
    if m:
        h = _cn_num(m.group(1))
        return h * 60 if h is not None else None
    m = re.fullmatch(rf"({_DUR_MIN})\s*分钟", s)
    if m:
        raw_v = m.group(1)
        return int(raw_v) if raw_v.isdigit() else _cn_num(raw_v)
    m = re.fullmatch(r"(\d{1,4})\s*min", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _extract_durations(utterance: str) -> list[tuple[str, int]]:
    """时长候选: lead 词在 6 字内 / 后接语气或标点 / 紧邻"还是|或者|选"候选语境, 满足其一才收。"""
    out: list[tuple[str, int]] = []
    for m in _DUR_RE.finditer(utterance):
        raw = m.group("dur")
        before = utterance[max(0, m.start() - 6):m.start()]
        after = utterance[m.end():m.end() + 1]
        after2 = utterance[m.end():m.end() + 2]
        cand_ctx = (after2.startswith(("还是", "或者"))
                    or before2_check(before))
        if not (any(w in before for w in _DUR_LEADS) or after in "吧了，。,;；、"
                or after == "" or cand_ctx):
            continue
        d = _duration_minutes(raw)
        if d is not None:
            out.append((raw, d))
    return out


def before2_check(before: str) -> bool:
    return before.endswith(("选", "还是", "或者"))


# ---------------- 主入口 ----------------

_UNDECIDED_SLOT_SCOPED_RE = re.compile(r"(开始|结束|截止)时间[^，。；]{0,4}$")
# v28.2: 槽位限定词扩展——"应用和时延还没定"/"时长我还没定"/"时延上限我还没定"等
# 限定的是非时间槽位, 不应抑制已明说时间的组装 (d930 T2 类 9 例实证:
# 时间明说完整但被"全局未定"误判整体抑制, t2 澄清后明说时间永久丢失)。
_UNDECIDED_NON_TIME_KW = ("应用", "时延", "rtt", "时长", "画质", "分辨率", "清晰度",
                          "走", "选")


def _known_apps() -> tuple:
    try:
        from schema import KNOWN_APPLICATIONS
        return tuple(KNOWN_APPLICATIONS)
    except Exception:
        return ()


_KNOWN_APPS_SNAPSHOT = _known_apps()


def _generic_undecided(utterance: str) -> bool:
    """未定词是否为全局("时间还没定")而非槽位限定("开始时间我还不确定")。

    v28.2 口径: 未定词前文含 (开始|结束|截止)时间 或 任一非时间槽位关键词
    (应用/时延/时长/画质/分辨率/清晰度/走/选) → 该未定词是槽位限定, 不算全局;
    全部未定词都被限定 → 时间未定不成立, 已明说时间照常组装。
    仅当存在【悬空】未定词 (前文既非时间槽位也非其他槽位) → 判全局
    ("时间还没定"/"什么时候还没定"型)。
    """
    for w in _UNDECIDED_WORDS:
        i = utterance.find(w)
        while i >= 0:
            # v28.2: 双向槽位限定判定。
            # 前向紧邻 (≤6 字): "应用还没定"/"时长我还没定"。
            # 前向隔值 (6~16 字, 关键词后无逗号句号): "时延上限30ms，我还没定"——
            #   值插在关键词与未定词之间 (1850 型); "画质要流畅，时间还没定" 的画质
            #   已用"要流畅"收尾、与未定词之间有逗号 → 不命中 (S-000185 真候选时段保持抑制)。
            # 后向: "…我还没定，是1080p配时延上限…"——未定对象在未定词之后且带
            #   "是…配" 选项列举形态 (1684 型, 时间已完整明说在未定词之前)。
            # 另: 前文含任一已知 app 名 → 未定的是 app 二选一 (1276 型)。
            ctx_fwd = utterance[max(0, i - 16):i]
            ctx_bwd = utterance[i + len(w):i + len(w) + 16]
            fwd_hit = _UNDECIDED_SLOT_SCOPED_RE.search(ctx_fwd) is not None
            if not fwd_hit:
                for _k in _UNDECIDED_NON_TIME_KW:
                    _j = ctx_fwd.rfind(_k)
                    if _j >= 0:
                        if i - 16 + _j >= i - 6:
                            fwd_hit = True  # 紧邻
                        elif not re.search(r"[，。；]", ctx_fwd[_j:]):
                            fwd_hit = True  # 隔值 (关键词与未定词之间无标点)
                        break
            bwd_hit = any(k in ctx_bwd for k in _UNDECIDED_NON_TIME_KW) \
                and re.search(r"是[^，。；]{0,12}配", ctx_bwd)
            app_hit = any(a in ctx_fwd for a in _KNOWN_APPS_SNAPSHOT)
            if not (fwd_hit or bwd_hit or app_hit):
                return True  # 悬空未定词 -> 全局未定 (时间真未定)
            i = utterance.find(w, i + 1)
    return False


def build_date_table(ref_dt: datetime, utterance: str) -> list[tuple[str, str]] | None:
    """相对日期词触发的预计算日期表。utterance 不含相对词时返回 None (不注入)。"""
    if not any(w in utterance for w in _RELATIVE_DAYS):
        return None
    rows = []
    for w in ("今天", "明天", "后天", "大后天"):
        d = ref_dt + timedelta(days=_RELATIVE_DAYS[w])
        rows.append((w, f"{d.month}月{d.day}日(周{_WEEKDAYS[d.weekday()]})"))
    return rows


def extract(utterance: str, ref_dt: datetime) -> TimeEvidence | None:
    """提取+预解析+组装三元组+信号检测。无任何明说时间时返回 None。"""
    ev = TimeEvidence()
    mentions, has_relative = _extract_mentions(utterance, ref_dt)
    ev.mentions = mentions
    ev.has_relative_day = has_relative
    ev.durations = _extract_durations(utterance.replace(" ", ""))
    if len(ev.durations) == 1:
        ev.duration_min = ev.durations[0][1]

    # 候选信号: 时间点候选("或者/还是"连接) 或未定词 -> has_candidate(抑制组装+候选规则);
    # 时长候选单独标 dur_candidate (只抑制时长断言, start/end 照常, S-000133)
    if len(mentions) >= 2 and ("或者" in utterance or "还是" in utterance):
        ev.has_candidate = True
    if len(ev.durations) >= 2 and ("或者" in utterance or "还是" in utterance):
        ev.dur_candidate = True
    if any(w in utterance for w in _UNDECIDED_WORDS) and mentions:
        ev.has_candidate = True

    if not mentions and not ev.durations:
        return None

    # 三元组组装: 只在角色唯一明确时进行; 多意图多锚点句不组装 (绑定是模型的职责)
    starts = [m for m in mentions if m.role == "start" and m.dt]
    ends = [m for m in mentions if m.role == "end" and m.dt]
    if ev.has_candidate:
        # 候选模式: 或者/还是 紧邻的选项不参与组装; 全局未定("时间还没定")全部不组装;
        # 仅跨子句的确立字段允许组装 (S-000099 轮1: start 二选一未定, end=21:30 明说)
        if _generic_undecided(utterance):
            starts, ends = [], []
        else:
            starts = [m for m in starts if not m.cand_adj]
            ends = [m for m in ends if not m.cand_adj]
    if len(starts) == 1 and len(ends) == 1:
        ev.start, ev.end = starts[0].dt, ends[0].dt
    elif len(ends) == 1 and not starts:
        ev.end = ends[0].dt  # 唯一明说 end (如 S-000099 轮1), 其余候选无角色
    elif len(starts) == 1 and not ends:
        ev.start = starts[0].dt
    elif len(mentions) == 1 and mentions[0].dt:
        if mentions[0].role == "end":
            ev.end = mentions[0].dt
        elif mentions[0].role == "start":
            ev.start = mentions[0].dt

    if ev.duration_min is not None and not ev.has_candidate and not ev.dur_candidate:
        if ev.end and ev.start is None:
            ev.start = ev.end - timedelta(minutes=ev.duration_min)
            ev.start_derived = True
        elif ev.start and ev.end is None:
            ev.end = ev.start + timedelta(minutes=ev.duration_min)
            ev.end_derived = True

    # 跨夜滚动 (矛盾检测前, 合法解读优先): end<=start 时
    # ① end 自带"凌晨"时段词 -> 次日凌晨 ("晚上11点到凌晨1点")
    # ② end 裸时刻被 PM 继承位移后仍 <=start -> 改按"次日凌晨"重解读 ("晚上11点到1点")
    if ev.start and ev.end and ev.end <= ev.start and not ev.has_candidate:
        end_m = ends[0] if len(ends) == 1 else (mentions[0] if len(mentions) == 1 else None)
        if end_m is not None:
            if end_m.period == "凌晨":
                ev.end = ev.end + timedelta(days=1)
                end_m.dt = ev.end
            elif end_m.pm_inherited:
                alt = (ev.start.replace(hour=0, minute=0)
                       + timedelta(days=1, hours=end_m.hour_raw, minutes=end_m.minute_raw))
                if alt > ev.start:
                    ev.end = alt
                    end_m.dt = alt
                    end_m.clock = f"{end_m.hour_raw:02d}:{end_m.minute_raw:02d}"

    # 矛盾检测: start>end, 或 三要素齐了但 start+duration != end
    if ev.start and ev.end:
        if ev.start > ev.end:
            ev.has_contradiction = True
        elif ev.duration_min is not None:
            actual = int((ev.end - ev.start).total_seconds() // 60)
            if actual != ev.duration_min:
                ev.has_contradiction = True

    return ev
