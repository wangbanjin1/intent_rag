# -*- coding: utf-8 -*-
"""conflict_detector.py: v25 窄版冲突检查的话语-输出冲突检测器 (v26.4 归档留档)。

历史结论 (v25.0, report_reflection.md §8): 双跑一致净负 (−1.86pp/−0.80pp) 回退;
反思路线全线关闭 (触发 10/采纳 1, 9/10 模型拒绝改动, 靶区上限 +0.6pp < 噪声带)。
v26.4 重构自 validator.py 原样移出 (零改动), 仅供历史实验复现/复核使用, 不再被主链路引用。

用法: 将本文件放回项目根目录改名 validator.py 的对应段, 或独立:
    from tools._archive.conflict_detector import detect_utterance_conflicts
"""
import re
from datetime import timedelta  # noqa: F401  (原段保留)

# 复现依赖: parse_dt 来自原 validator.py (dt = parse_dt(...) 于 L194 附近), 复现时:
#   sys.path.insert(0, '<项目根>'); from validator import parse_dt

# ---- 话语明说值 vs 输出值 冲突检测 (v25 窄版冲突检查的信号源) ----
# 原则: 只报确定性矛盾, 绝不推断"该填什么"。
# 值-意图近邻绑定: 显式值归属最近的 app 提及 (±40 字符), 多意图话语不串扰;
# 归属不明 (无邻近 app 且多意图) 则放弃该次检测。
# PER 区天然不触发: 用户从不复述画像 record 数值 (三路扫描实证)。

_UTT_RTT_RE = re.compile(r"(\d+)\s*(?:ms|毫秒)")
_UTT_RES_RE = re.compile(r"(\d{3,4})\s*[pP]")
_UTT_DUR_MIN_RE = re.compile(r"(\d+)\s*分钟")
_UTT_DUR_H_RE = re.compile(r"([0-9一二两三四五六七八九十]+)\s*(?:个)?小时")
_UTT_DUR_HALF_H_RE = re.compile(r"([0-9一二两三四五六七八九十]+)个半小时|(?<![0-9一二两三四五六七八九十个])半小时")
_UTT_DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})[号日]")
# 日期作记录锚定而非时间约束: "6月16日的那条记录" / "7月4号完成的那次会议"
_DATE_RECORD_CTX_RE = re.compile(
    r"\d{1,2}月\d{1,2}[号日]\s*(的?\s*(那条|那次|那场|记录|会议记录)|[^，。；]{0,6}(那条|那次|记录))")
# 上界/下界表述是约束不是目标值 ("不超过50ms" / "20毫秒以内" / "至少30分钟")
_BOUND_PREFIX = r"(?:不超过|别超过|最多|以内|以下|不少于|至少|不低于|在)"
_BOUND_SUFFIX = r"(?:以内|以下|之内|以上)"
_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_BIND_WINDOW = 40  # 值-意图绑定窗 (字符)


def _cn_num(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    return _CN_DIGIT.get(s)


def _is_bound(utt: str, span: tuple[int, int], unit_rx: str) -> bool:
    """该数值提及是否处于上界/下界表述中 (前缀紧贴或后缀紧跟)。"""
    lo = max(0, span[0] - 6)
    ctx_pre = utt[lo:span[0]]
    ctx_post = utt[span[1]:span[1] + 4]
    if re.search(_BOUND_PREFIX + r"$", ctx_pre):
        return True
    return bool(re.match(_BOUND_SUFFIX, ctx_post))


def _app_positions(utt: str) -> list[tuple[int, str]]:
    """话语中全部 app 名提及位置 (长名优先去重覆盖)。"""
    out: list[tuple[int, str]] = []
    for name in sorted(KNOWN_APPLICATIONS, key=len, reverse=True):
        start = 0
        while True:
            i = utt.find(name, start)
            if i < 0:
                break
            # 已被更长名覆盖的位置跳过 (如 "哔哩哔哩" 覆盖 "哩")
            if not any(a <= i < a + len(n) for a, n in out):
                out.append((i, name))
            start = i + 1
    return out


def _bind_intent(pos: int, apps: list[tuple[int, str]],
                 flats: dict[str, dict[str, Any]]) -> str | None:
    """把位置 pos 的显式值绑定到最近的 app 对应 intent; 绑不动返回 None。

    话语模式是"app 在前, 参数值在后" → 优先取前方最近 app; 前方没有才取后方。
    窗口内有 2 个不同 app → 归属不明, 放弃 (多意图列举场景防串扰)。
    """
    if not flats:
        return None
    if not apps:
        return next(iter(flats)) if len(flats) == 1 else None
    near = [ap for ap in apps if abs(ap[0] - pos) <= _BIND_WINDOW]
    if len({n for _, n in near}) >= 2:
        return None  # 窗口内多个不同 app, 归属不明
    before = [ap for ap in near if ap[0] <= pos]
    cand = max(before, key=lambda ap: ap[0]) if before else (min(near, key=lambda ap: ap[0]) if near else None)
    if cand is None:
        return next(iter(flats)) if len(flats) == 1 else None
    for k in flats:
        if k.startswith(cand[1] + "/") or k == cand[1]:
            return k
    return None


# 改口/否定轮: 话语同时含被拒绝的旧值与新值, 值语义不可靠 → 该轮不做值冲突检测
# (改口轮已由 v22 correction_rule 覆盖; v20.2 实证 GT 在此类轮记字面错误值)
_NEG_CTX_RE = re.compile(
    r"不是|不对|错了|改成|改为|换成|应该是|我说的是|搞错|弄错|重新|不要|别|太高|太贵|算了")


def detect_utterance_conflicts(
    utterance: str,
    flats: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """检测话语显式值与各意图 final state 的确定性冲突。

    flats: {intent_key: flatten_params(final_state)}。
    返回 {intent_key: [冲突描述...]}; 无信号返回 {}。只报冲突, 不做修正。
    """
    utt = utterance or ""
    out: dict[str, list[str]] = {}
    if not flats:
        return out
    apps = _app_positions(utt)

    # 改口/否定轮: 旧值新值同在话语中, 字面比对必然误报 → 整轮跳过
    if _NEG_CTX_RE.search(utt):
        return out

    def _add(key: str | None, msg: str) -> None:
        if key:
            out.setdefault(key, []).append(msg)

    # (field_type, mentions) 先按绑定 intent 归组, 同一 intent 同类显式值恰 1 个才比
    # (同 intent 2 个不同值 = 改口/多约束等, 放弃)

    # rtt
    rtt_mentions: dict[str, list[int]] = {}
    for m in _UTT_RTT_RE.finditer(utt):
        if _is_bound(utt, m.span(), "ms"):
            continue
        k = _bind_intent(m.start(), apps, flats)
        if k:
            rtt_mentions.setdefault(k, []).append(int(m.group(1)))
    for k, vs in rtt_mentions.items():
        if len(set(vs)) == 1:
            pred = re.fullmatch(r"(\d+)ms", str(flats[k].get("rtt") or ""))
            if pred and int(pred.group(1)) != vs[0]:
                _add(k, f"用户明说时延 {vs[0]}ms, 但输出 rtt={flats[k].get('rtt')}")

    # resolution ("至少360P" 等下界表述跳过)
    res_mentions: dict[str, list[str]] = {}
    for m in _UTT_RES_RE.finditer(utt):
        if _is_bound(utt, m.span(), "p"):
            continue
        k = _bind_intent(m.start(), apps, flats)
        if k:
            res_mentions.setdefault(k, []).append(m.group(1).lower() + "p")
    for k, vs in res_mentions.items():
        if len(set(vs)) == 1:
            pred = str(flats[k].get("resolution") or "").lower()
            if pred and pred != vs[0]:
                _add(k, f"用户明说清晰度 {vs[0]}, 但输出 resolution={flats[k].get('resolution')}")

    # duration: "N分钟" / "N小时"(含中文数词) / "N个半小时" / 独立"半小时"
    dur_mentions: dict[str, list[int]] = {}

    def _dur(k: str | None, v: int | None) -> None:
        if k and v:
            dur_mentions.setdefault(k, []).append(v)

    half_spans: list[tuple[int, int]] = []
    for m in _UTT_DUR_HALF_H_RE.finditer(utt):
        base = _cn_num(m.group(1)) if m.group(1) else 0
        if base is not None:
            half_spans.append(m.span())
            _dur(_bind_intent(m.start(), apps, flats), base * 60 + 30)
    for m in _UTT_DUR_MIN_RE.finditer(utt):
        if _is_bound(utt, m.span(), "min"):
            continue
        _dur(_bind_intent(m.start(), apps, flats), int(m.group(1)))
    for m in _UTT_DUR_H_RE.finditer(utt):
        if any(a <= m.start() < b for a, b in half_spans):
            continue  # "N个半小时" 已计, 防 "小时" 重复计数
        v = _cn_num(m.group(1))
        _dur(_bind_intent(m.start(), apps, flats), v * 60 if v else None)
    for k, vs in dur_mentions.items():
        if len(set(vs)) == 1:
            pred_min = parse_duration_minutes(flats[k].get("duration"))
            if pred_min is not None and pred_min != vs[0]:
                _add(k, f"用户明说时长 {vs[0]}min, 但输出 duration={flats[k].get('duration')}")

    # date: 话语 "X月X号" (非记录锚定) vs 输出 start 的月日
    # (只比 start: "晚上11点到凌晨1点"跨日场景 end 天然是次日, 比 end 会误报)
    if not _DATE_RECORD_CTX_RE.search(utt):
        date_mentions: dict[str, list[tuple[int, int]]] = {}
        for m in _UTT_DATE_RE.finditer(utt):
            k = _bind_intent(m.start(), apps, flats)
            if k:
                date_mentions.setdefault(k, []).append((int(m.group(1)), int(m.group(2))))
        for k, vs in date_mentions.items():
            uniq = set(vs)
            if len(uniq) == 1:
                (um, ud), = uniq
                dt = parse_dt(flats[k].get("start_timestamp"))
                if dt and (dt.month, dt.day) != (um, ud):
                    _add(k, f"用户明说日期 {um}月{ud}号, 但输出开始时间为 "
                            f"{flats[k].get('start_timestamp')}")
    return out
