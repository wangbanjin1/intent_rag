# -*- coding: utf-8 -*-
"""time_rules.py: 时间后处理规则 (v23.2 精简版)

只保留经 v22.1 离线回放 (tools/replay_three_rules.py) 实证有效的三条
"话术重推导" 规则——判断依据全部在话术原文, 不猜模型的规范输出哪里错:

  day_offset_rederive      明天/后天日期推算: 话术相对日词唯一 -> 日期=ref+offset
                           (回放实证修 S-000862: 07-27 01:00 -> 07-28 01:00)
  halfday_fix              凌晨/下午 ±12h 纠偏: 唯一带时段标记时间 vs pred
                           整差 12h -> 纠偏 (计分轮回放未命中; 计分轮外及
                           弱模型仍可能命中, GT 不变性零破坏故保留)
  end_from_start_duration  显式 start+时长 推 end: pred end 与 start+话术
                           唯一时长不一致 -> 重算 (实证修 S-000829: 21:00->21:30)

回放结论 (v22.1 存档, 644 case): 三条合计 3修/0破, GT 不变性 0 改动。
收益上限 session +2/322 (+0.62pp), 低于 API 噪声带 (±1.5~2.5pp),
不单独全量重跑, 随下次全量顺带验证。

v23 移植的 time.py 全量规则 (40 函数) 对 qwen-plus 已规范输出为恒等变换
(回放 0修/0破), 已归档 data/_archive/time_rules_v23_full.py, 不再入库。
若未来评弱模型 (如 qwen3-32b), 其格式/换算类浅层错误占比升高, 归档版
规则可能重新有价值, 届时用同一回放方法论先行测量再决定。

接入: pipeline.run_multi 解析后、validate 前对 delta timestamp 应用;
config time_rules.enabled (默认关) + disabled_rules 细粒度门控;
任何异常静默回退原 delta。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

PRESERVED_LABELS = {"missing", "invalid", "inherit", "unclear", "follow_up"}

_DT_FULL = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日(\d{1,2})时(\d{1,2})分")


# ---------------------------------------------------------------------------
# 基础解析
# ---------------------------------------------------------------------------

def _normalize_text(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _parse_full_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    m = _DT_FULL.fullmatch(value.strip())
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


def _format_datetime(value: datetime) -> str:
    return f"{value.year:04d}年{value.month:02d}月{value.day:02d}日{value.hour:02d}时{value.minute:02d}分"


def _parse_duration_minutes(value: Any) -> Optional[int]:
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"(\d+)min", value)
    return int(m.group(1)) if m else None


def _is_full_ts(x: Any) -> bool:
    return _parse_full_timestamp(x) is not None


def parse_reference_time(reference_time: str) -> Optional[datetime]:
    return _parse_full_timestamp(reference_time)


def build_context(reference_time: str) -> Optional[Dict[str, Any]]:
    """由 session reference_time 构造规则需要的 context。"""
    dt = parse_reference_time(reference_time)
    if dt is None:
        return None
    return {
        "base_year": dt.year,
        "base_month": dt.month,
        "base_day": dt.day,
        "reference_time": reference_time,
    }


# ---------------------------------------------------------------------------
# 中文数字 / 口语时长
# ---------------------------------------------------------------------------

_CHINESE_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _parse_small_number(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if value in {"一刻", "刻"}:
        return 15
    if value == "三刻":
        return 45
    if value == "半":
        return 30
    if len(value) == 2 and value[0] == "零" and value[1] in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value[1]]
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1 if left == "" else None)
        ones = 0 if right == "" else _CHINESE_DIGITS.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return _CHINESE_DIGITS.get(value)


def _replace_chinese_duration_numbers(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        parsed = _parse_small_number(match.group(1))
        return str(parsed) if parsed is not None else match.group(1)
    value = re.sub(r"([零一二三四五六七八九十两]{1,4})(?=个?半?小时|个?小时半|时)", replace, value)
    return re.sub(r"(?<=(?:小时|钟头))([零一二三四五六七八九十两]{1,4})(?=分钟|分)", replace, value)


def _normalize_unit(value: Any) -> Any:
    """口语时长 -> Nmin ("一个半小时"->90min, "三个小时"->180min)。"""
    normalized = _normalize_text(value)
    if not isinstance(normalized, str) or normalized in PRESERVED_LABELS:
        return normalized
    normalized = re.sub(r"(?:左右|大约|大概)$", "", normalized)
    normalized = _replace_chinese_duration_numbers(normalized)
    if re.fullmatch(r"(?:半小时|半个小时|半个钟头|半钟头)", normalized):
        return "30min"
    if re.fullmatch(r"(?:1|一)个?半小时|(?:1|一)个?小时半", normalized):
        return "90min"
    half_hour_match = re.fullmatch(r"(\d+)个?半小时|(\d+)个?小时半", normalized)
    if half_hour_match:
        hours = int(half_hour_match.group(1) or half_hour_match.group(2))
        return f"{hours * 60 + 30}min"
    minute_match = re.fullmatch(r"(\d+)\s*(?:分钟|分)", normalized)
    if minute_match:
        return f"{int(minute_match.group(1))}min"
    hour_minute_match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(?:个?小时|时|h|hour|hours)\s*(?:(\d+)\s*(?:分钟|分|m|min))?",
        normalized, re.IGNORECASE)
    if hour_minute_match:
        minutes = float(hour_minute_match.group(1)) * 60 + int(hour_minute_match.group(2) or 0)
        return f"{int(minutes)}min" if minutes.is_integer() else f"{minutes:g}min"
    return normalized


_DURATION_TOKEN = (
    r"(?:半小时|半个小时|半个钟头|半钟头|[零一二三四五六七八九十两\d]+个?半小时"
    r"|[零一二三四五六七八九十两\d]+个?小时半|[零一二三四五六七八九十两\d]+(?:个?小时|小时|h|分钟|分|min)"
    r"|[零一二三四五六七八九十两\d]+时[零一二三四五六七八九十两\d]+分)"
)


def _duration_match_is_inside_timestamp(normalized_query: str, start: int) -> bool:
    prefix = normalized_query[max(0, start - 2):start]
    lead_prefix = normalized_query[max(0, start - 4):start]
    suffix = normalized_query[start:start + 12]
    looks_like_start_time = (
        bool(re.match(r"[零一二三四五六七八九十两\d]+时[零一二三四五六七八九十两\d]+分(?:到|至)", suffix))
        and not any(marker in lead_prefix for marker in ("持续", "时长", "历时", "共")))
    return (bool(prefix) and prefix[-1] in {"日", "号", "月", "年"}) or looks_like_start_time


def _extract_query_duration(query: Optional[str]) -> Any:
    """从话术提取显式时长 -> Nmin (无则 None)。"""
    if not isinstance(query, str):
        return None
    normalized = query.replace(" ", "")
    lead = r"(?:持续|时长|历时|历经|一共|共计|共|用|刷|连着玩|连看|连播|连着|连续|弄|要保障|保障|过|往后)"
    patterns = [
        lead + r"(半小时|半个小时|半个钟头|半钟头)",
        lead + r"([零一二三四五六七八九十两\d]+个?半小时)",
        lead + r"([零一二三四五六七八九十两\d]+个?小时半)",
        lead + r"([零一二三四五六七八九十两\d]+(?:个?小时|小时|h)(?:[零一二三四五六七八九十两\d]+(?:分钟|分|m|min))?)",
        lead + r"([零一二三四五六七八九十两\d]+时[零一二三四五六七八九十两\d]+分)",
        r"([零一二三四五六七八九十两\d]+个?半小时)(?:到|直到|直至|至|，|,|。|$)",
        r"([零一二三四五六七八九十两\d]+(?:个?小时|小时|h)(?:[零一二三四五六七八九十两\d]+(?:分钟|分|m|min))?)(?:到|直到|直至|至|，|,|。|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            if _duration_match_is_inside_timestamp(normalized, match.start(1)):
                continue
            parsed = _normalize_unit(match.group(1))
            if _parse_duration_minutes(parsed) is not None:
                return parsed
    return None


def _unique_duration_candidates(query: str) -> bool:
    """话术中的时长表达归一后是否唯一 (end_from_start_duration 前置)。"""
    if not isinstance(query, str):
        return False
    vals = set()
    for m in re.finditer(_DURATION_TOKEN, query.replace(" ", "")):
        parsed = _normalize_unit(m.group(0))
        if isinstance(parsed, str) and _parse_duration_minutes(parsed) is not None:
            vals.add(parsed)
    return len(vals) == 1


# ---------------------------------------------------------------------------
# 话术时间表达提取 (带时段标记 -> 24h)
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(
    r"(凌晨|早上|上午|中午|下午|晚上|今晚|明晚)?"
    r"([零一二三四五六七八九十两\d]{1,3})\s*(?:点|时)"
    r"(半|一刻|三刻|[零一二三四五六七八九十两\d]{1,4}分)?"
)


def _marked_times(query: Optional[str]) -> list[tuple[Optional[str], int, int]]:
    """提取话术中所有时间表达 -> [(marker, hour24, minute)]。"""
    out = []
    if not isinstance(query, str):
        return out
    for m in _TIME_RE.finditer(query):
        marker, htok, mtok = m.group(1), m.group(2), m.group(3)
        h = _parse_small_number(htok)
        if h is None or h > 24:
            continue
        if marker in ("下午", "晚上", "今晚", "明晚") and h < 12:
            h += 12
        elif marker == "凌晨" and h == 12:
            h = 0
        if mtok == "半":
            mi = 30
        elif mtok:
            mi = _parse_small_number(mtok[:-1])
            if mi is None:
                continue
        else:
            mi = 0
        out.append((marker, h, mi))
    return out


_DAY_WORDS = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}

# 更正话语标记: 用户在改口, 时间字段保持模型输出, 不做话术重推断
_CORRECTION_MARKERS = ("说错", "弄错", "搞错", "记错", "不对，", "哦不", "是我算错", "改一下", "改成")


def _has_correction_marker(query: str) -> bool:
    return any(m in query for m in _CORRECTION_MARKERS)


# ---------------------------------------------------------------------------
# 规则注册
# ---------------------------------------------------------------------------

RULE_DEFAULTS: Dict[str, bool] = {
    "day_offset_rederive": True,      # 明天/后天日期重推导 (回放 2修/0破, S-000862)
    "halfday_fix": True,              # 凌晨/下午 ±12h 纠偏 (回放 0修/0破, 留待计分轮外/弱模型)
    "end_from_start_duration": True,  # 显式 start+时长 推 end (回放 1修/0破, S-000829)
}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def normalize_timestamp_fields(
    timestamp_delta: Dict[str, Any],
    query: str,
    context: Optional[Dict[str, Any]],
    disabled_rules: Optional[set] = None,
) -> Dict[str, Any]:
    """对 delta 的 timestamp 子对象应用时间规则 (只动本轮显式给的键)。

    保守原则:
      * 只处理规范形全时间戳/Nmin 值; None/ref/preserved 不动。
      * 三条规则均要求话术对应候选唯一 (多意图话语弃权)。
      * 话术含更正词 (T2-3/T3-2 改口场景) 时整体跳过。
      * 任何异常静默返回原 delta。
    """
    ts = timestamp_delta if isinstance(timestamp_delta, dict) else {}
    off = set(disabled_rules or ())
    q = query if isinstance(query, str) else ""

    def on(rule: str) -> bool:
        return RULE_DEFAULTS.get(rule, False) and rule not in off

    if _has_correction_marker(q):
        return ts

    def _val(x: Any) -> Any:
        return x.get("value") if isinstance(x, dict) and "value" in x else x

    try:
        new_s = _val(ts.get("start_timestamp"))
        new_e = _val(ts.get("end_timestamp"))
        new_d = _val(ts.get("duration"))
        marked = _marked_times(q)

        # ---- day_offset_rederive: 相对日词唯一 + 时间候选唯一 -> 日期 = ref+offset
        if on("day_offset_rederive") and context and len(marked) == 1:
            days = [_DAY_WORDS[w] for w in _DAY_WORDS if w in q]
            if len(days) == 1:
                ref_dt = parse_reference_time(context.get("reference_time") or "")
                s_dt = _parse_full_timestamp(new_s)
                e_dt = _parse_full_timestamp(new_e)
                # 跨夜 (起止不同日, 如 "用两小时到明天0点") 日词绑 end 而非 start,
                # 归属歧义, 弃权 (GT 不变性回归实证: S-000863/S-000301 误改)
                cross_midnight = bool(s_dt and e_dt and s_dt.date() != e_dt.date())
                if ref_dt and s_dt and not cross_midnight:
                    want_date = ref_dt.date() + timedelta(days=days[0])
                    dd = (want_date - s_dt.date()).days
                    if dd != 0:
                        new_s = _format_datetime(s_dt + timedelta(days=dd))
                        if e_dt:
                            new_e = _format_datetime(e_dt + timedelta(days=dd))

        # ---- halfday_fix: 唯一带标记时间 vs start 整差 12h -> 起止同步纠偏
        if on("halfday_fix") and len(marked) == 1:
            _, h, mi = marked[0]
            s_dt = _parse_full_timestamp(new_s)
            if s_dt and (h - s_dt.hour) % 24 == 12 and s_dt.minute == mi:
                new_s = _format_datetime(s_dt + timedelta(hours=h - s_dt.hour))
                e_dt = _parse_full_timestamp(new_e)
                if e_dt:
                    new_e = _format_datetime(e_dt + timedelta(hours=h - s_dt.hour))

        # ---- end_from_start_duration: 话术时长唯一 + 时间候选唯一 +
        #      pred duration==话术时长 -> end = start + duration
        if on("end_from_start_duration") and len(marked) == 1 \
                and _unique_duration_candidates(q):
            qd = _extract_query_duration(q)
            s_dt = _parse_full_timestamp(new_s)
            if qd and s_dt and new_d == qd:
                computed = _format_datetime(
                    s_dt + timedelta(minutes=_parse_duration_minutes(qd)))
                if computed != new_e:
                    new_e = computed
    except Exception:
        return ts  # 规则层任何异常都不影响主链路

    def _wrap_back(orig: Any, new_val: Any) -> Any:
        if not isinstance(orig, dict) or "value" not in orig:
            return orig
        if new_val == orig.get("value") or new_val is None:
            return orig
        out_dict = dict(orig)
        out_dict["value"] = new_val
        return out_dict

    out: Dict[str, Any] = {}
    for key, val in (("start_timestamp", new_s), ("end_timestamp", new_e), ("duration", new_d)):
        orig = ts.get(key)
        if isinstance(orig, dict) and "value" in orig:
            out[key] = _wrap_back(orig, val)
    return out
