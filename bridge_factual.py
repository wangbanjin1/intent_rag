"""bridge_factual.py — 事实记忆（Factual Memory）检索增强、时间锚定与规则渲染。

职责:
  1. 相对时间窗锚定 (anchor_time_window_to_ref) 与话语明说时段提取 (_explicit_window)
  2. 应用/业务中英文映射与周期偏好配对 (resolve_cn_app_svc, enrich_factual_hits)
  3. 事实记忆与冷启动规则提示渲染 (render_factual_and_cold_start_rules)
  4. 记忆检索追踪快照构造 (build_trace_info)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import time_extractor as _tex
from prompts.memory_hint_templates import (
    COLD_START_LITERAL_RULE,
    MEMORY_FOLLOWUP_CONFIRM_RULE,
    MEMORY_FOLLOWUP_UPDATE_RULE,
    MULTI_TURN_SLOT_COMPLETION_RULE,
    SERVICE_NAME_GAME_RULE,
    SERVICE_NAME_SHORT_VIDEO_RULE,
    format_memory_fill_rule,
    format_unclarified_slang_rule,
)

DT_FMT = "%Y年%m月%d日%H时%M分"

_MEMORY_TRIGGER_RE = re.compile(
    r"老规矩|按上次|按平时|按默认|照旧|按之前|习惯|例行|老时间|老地方|上次的|像上次|跟上次"
)
_QUOTED_SLANG_RE = re.compile(
    r"(画质|清晰度|分辨率|时延|延迟)(?:标准|档位)?(?:只要|就按|先按|都按|要|按|用)?[‘'“\"]([^’'”\"]+)[’'”\"]"
)
_EXPLICIT_RES_RE = re.compile(r"\b(360p|480p|540p|720p|1080p|2160p|2k|4k)\b", re.IGNORECASE)
_EXPLICIT_RTT_RE = re.compile(r"\b(\d+)\s*(?:ms|毫秒)\b", re.IGNORECASE)

_APP_CN_MAP: dict[str, str] = {
    "douyin": "抖音",
    "kuaishou": "快手",
    "bilibili": "哔哩哔哩",
    "wechat": "微信",
    "tencent_meeting": "腾讯会议",
    "dingtalk": "钉钉",
    "feishu": "飞书",
    "xiaohongshu": "小红书",
    "wangzherongyao": "王者荣耀",
    "gaode": "高德地图",
    "huya": "虎牙",
    "douyu": "斗鱼直播",
    "hepingjingying": "和平精英",
    "qq": "QQ",
    "taobao": "淘宝",
    "taobaozhubo": "淘宝主播",
    "pdd": "拼多多",
    "iqiyi": "爱奇艺",
    "youku": "优酷",
    "tencentvideo": "腾讯视频",
    "migu": "咪咕",
    "mango": "芒果TV",
    "xigua": "西瓜视频",
    "yy": "YY",
    "yunshixun": "云视讯",
    "wangyiyunyou": "网易云游戏",
    "migukuaiyou": "咪咕快游",
    "telegram": "Telegram",
    "facetime": "FaceTime",
}

_BIZ_CN_DEFAULT: dict[str, str] = {
    "openlive": "开直播",
    "live": "看直播",
    "watch_live": "看直播",
    "video": "短视频",
    "short_video": "短视频",
    "long_video": "视频",
    "meeting": "会议",
    "video_call": "视频通话",
    "game": "游戏",
}


def anchor_time_window_to_ref(time_window: str, reference_time: str) -> tuple[str, str, str] | None:
    """将 '14:00-16:00' 形式的时间窗锚定到 reference_time 所在日期, 返回 (start_ts, end_ts, duration)。"""
    if not time_window or "-" not in time_window or not reference_time:
        return None
    try:
        ref_dt = datetime.strptime(reference_time.strip(), DT_FMT)
    except ValueError:
        return None
    parts = time_window.strip().split("-", 1)
    m1 = re.match(r"^(\d{1,2}):(\d{2})$", parts[0].strip())
    m2 = re.match(r"^(\d{1,2}):(\d{2})$", parts[1].strip())
    if not m1 or not m2:
        return None
    h1, min1 = int(m1.group(1)), int(m1.group(2))
    h2, min2 = int(m2.group(1)), int(m2.group(2))
    start_dt = ref_dt.replace(hour=h1, minute=min1, second=0, microsecond=0)
    end_dt = ref_dt.replace(hour=h2, minute=min2, second=0, microsecond=0)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    dur_min = int((end_dt - start_dt).total_seconds() // 60)
    return start_dt.strftime(DT_FMT), end_dt.strftime(DT_FMT), f"{dur_min}min"


def _ref_year(reference_time: str) -> int | None:
    """从 reference_time 中提取四位年份。"""
    m = re.match(r"(\d{4})年", reference_time or "")
    return int(m.group(1)) if m else None


def _explicit_window(utterance: str, reference_time: str) -> tuple[datetime | None, datetime | None]:
    """从当前话语解析用户明说的 (开始, 结束) 时刻 (复用 time_extractor, 不写死任何时刻)。"""
    ref_dt = _tex.parse_reference_time(reference_time)
    if ref_dt is None:
        return None, None
    ev = _tex.extract(utterance or "", ref_dt)
    if ev is None or ev.start is None:
        return None, None
    st, et = ev.start, ev.end
    has_period = any(mn.period for mn in ev.mentions)
    if (
        not has_period
        and st.date() == ref_dt.date()
        and st < ref_dt
        and st.hour < 12
        and st + timedelta(hours=12) >= ref_dt
    ):
        st = st + timedelta(hours=12)
        if et is not None and et.hour < 12:
            et = et + timedelta(hours=12)
    if et is not None and et <= st:
        et = None
    return st, et


def _explicit_start_dt(utterance: str, reference_time: str) -> datetime | None:
    """从话语提取明说的开始时刻。"""
    return _explicit_window(utterance, reference_time)[0]


def _parse_minutes(dur: Any) -> int | None:
    """将 '120min' 等时长字符串解析为分钟整数。"""
    m = re.search(r"(\d+)", str(dur or ""))
    return int(m.group(1)) if m else None


def _mentioned_app(utterance: str) -> str | None:
    """返回话语中明确提及的应用中文名 (最长匹配), 用于经验规则保留用户自己的应用。"""
    names = sorted({v for v in _APP_CN_MAP.values() if v}, key=len, reverse=True)
    for nm in names:
        if nm in (utterance or ""):
            return nm
    return None


def resolve_cn_app_svc(content: dict[str, Any], utterance: str = "") -> tuple[str, str]:
    """将记忆节点中的英文/内部应用与业务标识解析为标准中文名称。"""
    app_raw = str(content.get("application") or "")
    biz_raw = str(content.get("business") or "")
    app_cn = str(content.get("app_cn") or _APP_CN_MAP.get(app_raw.lower(), app_raw))
    svc_cn = str(content.get("service_cn") or "")
    if not svc_cn:
        if biz_raw == "openlive":
            svc_cn = "开直播"
        elif biz_raw in ("live", "watch_live"):
            svc_cn = "开直播" if "开直播" in utterance else "看直播"
        elif biz_raw in ("video", "short_video"):
            svc_cn = "短视频" if "短视频" in utterance else "视频"
        else:
            svc_cn = _BIZ_CN_DEFAULT.get(biz_raw, biz_raw)
    return app_cn, svc_cn


def select_best_periodic(
    app_r: str,
    biz_r: str,
    app_c: str,
    svc_c: str,
    scene_str: str,
    periodic_list_by_app_biz: dict[tuple[str, str], list[dict[str, Any]]],
    all_working_records: list[dict[str, Any]],
    ref_minutes_now: int | None,
    is_temp_fn: Any,
) -> dict[str, Any] | None:
    """为指定应用与业务匹配最佳周期时间窗记忆。"""
    pp_cands = periodic_list_by_app_biz.get((app_r, biz_r)) or []
    if not pp_cands:
        return None
    if len(pp_cands) == 1:
        return pp_cands[0]
    if scene_str:
        sc_wrs = [
            r for r in all_working_records
            if not is_temp_fn(r)
            and r.get("application_name") == app_c
            and r.get("service_name") == svc_c
            and scene_str in str(r.get("location") or "")
        ]
        for r in sc_wrs:
            try:
                _s = datetime.strptime(str(r["start_timestamp"]), DT_FMT)
                _e = datetime.strptime(str(r["end_timestamp"]), DT_FMT)
                wr_tw = f"{_s.strftime('%H:%M')}-{_e.strftime('%H:%M')}"
                for pp in pp_cands:
                    if pp.get("time_window") == wr_tw:
                        return pp
            except (KeyError, TypeError, ValueError):
                continue
    if ref_minutes_now is not None:
        def _pp_dist(pp: dict[str, Any]) -> tuple[int, int]:
            tw_s = str(pp.get("time_window") or "")
            m_tw = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", tw_s.strip())
            if not m_tw:
                return (1, 9999)
            st_m = int(m_tw.group(1)) * 60 + int(m_tw.group(2))
            already_past = 1 if st_m < ref_minutes_now - 30 else 0
            return (already_past, abs(st_m - ref_minutes_now))

        return min(pp_cands, key=_pp_dist)
    return pp_cands[0]


def _enrich_single_param_hit(
    h: dict[str, Any],
    periodic_list_by_app_biz: dict[tuple[str, str], list[dict[str, Any]]],
    all_working_records: list[dict[str, Any]],
    utterance: str,
    reference_time: str,
    ref_minutes_now: int | None,
    is_temp_fn: Any,
) -> dict[str, Any]:
    """将单条参数偏好记忆与二阶段周期偏好及工作记忆配对补全。"""
    c = h.get("content") or {}
    app_raw = str(c.get("application") or "")
    biz_raw = str(c.get("business") or "")
    sc_val = str(c.get("scene") or "").strip()
    app_cn, svc_cn = resolve_cn_app_svc(c, utterance)
    paired_pp = select_best_periodic(
        app_raw, biz_raw, app_cn, svc_cn, sc_val,
        periodic_list_by_app_biz, all_working_records, ref_minutes_now, is_temp_fn,
    )
    tw = str((paired_pp or {}).get("time_window") or c.get("time_window") or "")
    res_val = c.get("resolution")
    rtt_val = c.get("latency") or c.get("rtt")
    if not res_val or not rtt_val or not tw:
        matching_wrs = [
            r for r in all_working_records
            if not is_temp_fn(r)
            and r.get("application_name") == app_cn
            and r.get("service_name") == svc_cn
        ]
        if matching_wrs:
            sc_wrs = [r for r in matching_wrs if sc_val and sc_val in str(r.get("location") or "")]
            ref_wr = (sc_wrs or matching_wrs)[0]
            if not res_val and ref_wr.get("resolution"):
                res_val = ref_wr.get("resolution")
            if not rtt_val and ref_wr.get("rtt"):
                rtt_val = ref_wr.get("rtt")
            if not tw and ref_wr.get("start_timestamp") and ref_wr.get("end_timestamp"):
                try:
                    _s = datetime.strptime(str(ref_wr["start_timestamp"]), DT_FMT)
                    _e = datetime.strptime(str(ref_wr["end_timestamp"]), DT_FMT)
                    tw = f"{_s.strftime('%H:%M')}-{_e.strftime('%H:%M')}"
                except (TypeError, ValueError):
                    pass
    anchored = anchor_time_window_to_ref(tw, reference_time) if tw else None
    enriched = dict(h)
    enriched["paired_periodic_key"] = (paired_pp or {}).get("memory_key") or ""
    enriched["resolved_slots"] = {
        "application_name": app_cn,
        "service_name": svc_cn,
        "resolution": res_val,
        "rtt": rtt_val,
        "time_window": tw,
        "scene": sc_val,
        "start_timestamp": anchored[0] if anchored else None,
        "end_timestamp": anchored[1] if anchored else None,
        "duration": anchored[2] if anchored else None,
    }
    return enriched


def _select_best_major_hit(
    major_hits: list[dict[str, Any]], utterance: str
) -> dict[str, Any] | None:
    """按话语明说的应用、业务、场景过滤并按激活态与检索得分选出最佳主干事实记忆。"""
    if not major_hits:
        return None
    candidates = list(major_hits)
    for slot_key in ("application_name", "service_name", "scene"):
        explicit_matches = [
            m for m in candidates
            if m["resolved_slots"].get(slot_key)
            and m["resolved_slots"][slot_key] in utterance
        ]
        if explicit_matches:
            candidates = explicit_matches

    candidates.sort(
        key=lambda m: (
            1 if m.get("status") in ("active", "reinforced") else 0,
            float(m.get("score", 0.0)),
        ),
        reverse=True,
    )
    return candidates[0]


def _build_user_context_dict(
    best_major: dict[str, Any] | None,
    major_hits: list[dict[str, Any]],
    all_working_records: list[dict[str, Any]],
    known_aliases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """根据最佳事实记忆、历史工单与个性化表述映射组装 user_context。"""
    recent_records: list[dict[str, Any]] = []
    for wr in all_working_records[-5:]:
        recent_records.append(
            {
                "application_name": wr.get("application_name"),
                "service_name": wr.get("service_name"),
                "resolution": wr.get("resolution"),
                "rtt": wr.get("rtt"),
                "start_timestamp": wr.get("start_timestamp"),
                "end_timestamp": wr.get("end_timestamp"),
                "duration": wr.get("duration"),
                "completed_at": f"working_record:{wr.get('record_id', '')}",
            }
        )
    for m in major_hits[:5]:
        rs = m["resolved_slots"]
        recent_records.append(
            {
                "application_name": rs["application_name"],
                "service_name": rs["service_name"],
                "resolution": rs["resolution"],
                "rtt": rs["rtt"],
                "start_timestamp": rs["start_timestamp"],
                "end_timestamp": rs["end_timestamp"],
                "duration": rs["duration"],
                "completed_at": f"{m['subtype']}:{m['memory_key']}",
            }
        )

    profile_dict: dict[str, Any] = {}
    if best_major is not None:
        brs = best_major["resolved_slots"]
        pp_tag = (
            f" + periodic_preference ({best_major.get('paired_periodic_key')})"
            if best_major.get("paired_periodic_key")
            else ""
        )
        profile_dict = {
            "匹配记忆类型": f"{best_major['subtype']} ({best_major['memory_key']}){pp_tag}",
            "常用应用": brs["application_name"],
            "常用业务": brs["service_name"],
            "默认分辨率": brs["resolution"],
            "常用时延要求": brs["rtt"],
            "常用开始时段": brs["time_window"],
            "常用保障时长": brs["duration"],
        }
    if known_aliases:
        profile_dict["个人黑话词典"] = {
            k: f"{v.get('target')} (作用域: {v.get('application', '')}|{v.get('business', '')})"
            for k, v in known_aliases.items()
        }

    return {
        "profile": profile_dict,
        "recent_records": recent_records,
    }


def enrich_factual_hits(
    param_hits: list[dict[str, Any]],
    periodic_list_by_app_biz: dict[tuple[str, str], list[dict[str, Any]]],
    all_working_records: list[dict[str, Any]],
    known_aliases: dict[str, dict[str, Any]],
    utterance: str,
    reference_time: str,
    is_temp_fn: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any]]:
    """将检索到的参数偏好与周期时间窗配对补全, 并构造 user_context。"""
    ref_dt_now = _tex.parse_reference_time(reference_time)
    ref_minutes_now = (ref_dt_now.hour * 60 + ref_dt_now.minute) if ref_dt_now is not None else None

    major_hits = [
        _enrich_single_param_hit(
            h, periodic_list_by_app_biz, all_working_records,
            utterance, reference_time, ref_minutes_now, is_temp_fn,
        )
        for h in param_hits
    ]
    best_major = _select_best_major_hit(major_hits, utterance)
    user_context = _build_user_context_dict(
        best_major, major_hits, all_working_records, known_aliases
    )
    return major_hits, best_major, user_context


def render_retrieval_header_lines(
    major_hits: list[dict[str, Any]],
    periodic_hits: list[dict[str, Any]],
    service_hits: list[dict[str, Any]],
    expression_hits: list[dict[str, Any]],
    working_hits: list[dict[str, Any]],
    experience_hits: list[dict[str, Any]],
    utterance: str,
) -> list[str]:
    """生成 # === MEMORY SYSTEM RETRIEVAL === 与 # === SERVICE NAME RULE === 提示行。"""
    hint_lines: list[str] = []
    if major_hits or periodic_hits or service_hits or expression_hits or working_hits or experience_hits:
        hint_lines.append("# === MEMORY SYSTEM RETRIEVAL (长期记忆系统检索结果) ===")
        for idx, m in enumerate(major_hits[:4], 1):
            rs = m["resolved_slots"]
            exp = m.get("explain") or {}
            pp_note = f", paired_periodic={m['paired_periodic_key']!r}" if m.get("paired_periodic_key") else ""
            hint_lines.append(
                f"#   [参数记忆{idx}] {m['subtype']} ({m['memory_key']}, status={m['status']}, score={m['score']:.3f}{pp_note}): "
                f"application_name={rs['application_name']!r}, service_name={rs['service_name']!r}, "
                f"resolution={rs['resolution']!r}, rtt={rs['rtt']!r}, "
                f"start_timestamp={rs['start_timestamp']!r}, end_timestamp={rs['end_timestamp']!r}, "
                f"duration={rs['duration']!r} | 命中原因: {exp.get('structured_match') or '混合检索'}"
            )
        for idx, ph in enumerate(periodic_hits[:4], 1):
            pc = ph.get("content") or {}
            hint_lines.append(
                f"#   [周期路由{idx}] periodic_preference ({ph['memory_key']}, status={ph['status']}, score={ph['score']:.3f}): "
                f"time_window={pc.get('time_window')!r} -> {pc.get('application')}:{pc.get('business')}"
            )
        for idx, sh in enumerate(service_hits[:4], 1):
            sc = sh.get("content") or {}
            hint_lines.append(
                f"#   [业务路由{idx}] service_to_app ({sh['memory_key']}, status={sh['status']}, score={sh['score']:.3f}): "
                f"{sc.get('business')} <-> {sc.get('application')}"
            )
        for idx, eh in enumerate(expression_hits[:4], 1):
            ec = eh.get("content") or {}
            hint_lines.append(
                f"#   [黑话词典{idx}] expression ({eh['memory_key']}): 「{ec.get('alias')}」 -> {ec.get('target')!r} "
                f"(作用域: {ec.get('application')}|{ec.get('business')})"
            )
        for idx, wh in enumerate(working_hits[:4], 1):
            wc = wh.get("content") or {}
            w_exp = (wh.get("explain") or {}).get("structured_match") or "向量/时间窗口检索"
            hint_lines.append(
                f"#   [工作记忆工单{idx}] working_record ({wc.get('record_id')}, score={wh.get('score', 0.0):.3f}): "
                f"application_name={wc.get('application_name')!r}, service_name={wc.get('service_name')!r}, "
                f"resolution={wc.get('resolution')!r}, rtt={wc.get('rtt')!r}, "
                f"start_timestamp={wc.get('start_timestamp')!r}, end_timestamp={wc.get('end_timestamp')!r}, "
                f"duration={wc.get('duration')!r}, location={wc.get('location')!r} | 命中原因: {w_exp}"
            )
        for idx, exh in enumerate(experience_hits[:4], 1):
            exc = exh.get("content") or {}
            ex_exp = (exh.get("explain") or {}).get("structured_match") or "经验条件向量匹配"
            hint_lines.append(
                f"#   [经验记忆卡片{idx}] experience ({exc.get('experience_id')} - {exc.get('title')}, score={exh.get('score', 0.0):.3f}): "
                f"condition={exc.get('condition')!r} -> action={exc.get('action')!r} | 命中原因: {ex_exp}"
            )
        hint_lines.append("")

    if "短视频" in utterance:
        hint_lines.append(SERVICE_NAME_SHORT_VIDEO_RULE)
    elif "游戏" in utterance and "云游戏" not in utterance:
        hint_lines.append(SERVICE_NAME_GAME_RULE)
    return hint_lines


def _collect_cold_start_and_slang_rules(
    turn_index: int,
    utterance: str,
    has_trigger: bool,
    prev_states: dict[str, dict[str, Any]],
    known_aliases: dict[str, dict[str, Any]],
) -> list[str]:
    """收集非记忆触发会话的冷启动字面提取、多轮补槽与未知自定义表述消歧规则。"""
    lines: list[str] = []
    if (
        turn_index == 1
        and not has_trigger
        and not _EXPLICIT_RES_RE.search(utterance)
        and not _EXPLICIT_RTT_RE.search(utterance)
    ):
        lines.append(COLD_START_LITERAL_RULE)
    elif turn_index > 1 and prev_states:
        lines.append(MULTI_TURN_SLOT_COMPLETION_RULE)

    if not has_trigger:
        for m_slang in _QUOTED_SLANG_RE.finditer(utterance or ""):
            slot_kind, slang_phrase = m_slang.group(1), m_slang.group(2).strip()
            is_res_slot = slot_kind in ("画质", "清晰度", "分辨率")
            if is_res_slot and _EXPLICIT_RES_RE.search(utterance):
                continue
            if not is_res_slot and _EXPLICIT_RTT_RE.search(utterance):
                continue
            if slang_phrase not in known_aliases:
                target_slot_name = "resolution" if is_res_slot else "rtt"
                lines.append(format_unclarified_slang_rule(slang_phrase, target_slot_name))
    return lines


def render_factual_and_cold_start_rules(
    turn_index: int,
    utterance: str,
    has_trigger: bool,
    best_major: dict[str, Any] | None,
    session_wr_mode: str | None,
    exp_applied: bool,
    session_memory_triggered: bool,
    prev_states: dict[str, dict[str, Any]],
    known_aliases: dict[str, dict[str, Any]],
) -> tuple[bool, dict[str, Any] | None, list[str]]:
    """渲染 Tier 1 事实记忆触发规则、非触发冷启动规则及多轮微调/确认规则。"""
    hint_lines: list[str] = []
    filled_memory: dict[str, Any] | None = None

    if (
        turn_index == 1
        and has_trigger
        and best_major is not None
        and not session_wr_mode
        and not exp_applied
    ):
        session_memory_triggered = True
        filled_memory = best_major["resolved_slots"]
        hint_lines.append(
            format_memory_fill_rule(
                best_major["subtype"], best_major["memory_key"], best_major["resolved_slots"]
            )
        )

    if not session_memory_triggered and not session_wr_mode and not exp_applied:
        hint_lines.extend(
            _collect_cold_start_and_slang_rules(
                turn_index, utterance, has_trigger, prev_states, known_aliases
            )
        )

    if turn_index > 1 and session_memory_triggered and prev_states:
        is_param_mod = (
            any(w in utterance for w in ("临时", "改", "调到", "压到", "降到", "提到", "换成"))
            and "以后也照这个标准" not in utterance
            and "平时说的" not in utterance
            and bool(_EXPLICIT_RES_RE.search(utterance) or _EXPLICIT_RTT_RE.search(utterance))
        )
        if "延长" in utterance or is_param_mod:
            hint_lines.append(MEMORY_FOLLOWUP_UPDATE_RULE)
        else:
            hint_lines.append(MEMORY_FOLLOWUP_CONFIRM_RULE)

    return session_memory_triggered, filled_memory, hint_lines


def _serialize_factual_trace_hits(
    major_hits: list[dict[str, Any]],
    periodic_hits: list[dict[str, Any]],
    service_hits: list[dict[str, Any]],
    expression_hits: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """序列化事实记忆层的四类检索命中快照。"""
    return {
        "major_hits": [
            {
                "memory_id": m["memory_id"],
                "subtype": m["subtype"],
                "memory_key": m["memory_key"],
                "paired_periodic_key": m.get("paired_periodic_key", ""),
                "status": m["status"],
                "score": m["score"],
                "resolved_slots": m["resolved_slots"],
                "structured_match": (m.get("explain") or {}).get("structured_match", ""),
            }
            for m in major_hits
        ],
        "periodic_hits": [
            {
                "memory_id": p["memory_id"],
                "subtype": p["subtype"],
                "memory_key": p["memory_key"],
                "status": p["status"],
                "score": p["score"],
                "time_window": (p.get("content") or {}).get("time_window"),
                "value": (p.get("content") or {}).get("value"),
            }
            for p in periodic_hits
        ],
        "service_hits": [
            {
                "memory_id": s["memory_id"],
                "subtype": s["subtype"],
                "memory_key": s["memory_key"],
                "status": s["status"],
                "score": s["score"],
                "business": (s.get("content") or {}).get("business"),
                "application": (s.get("content") or {}).get("application"),
            }
            for s in service_hits
        ],
        "expression_hits": [
            {
                "memory_id": e["memory_id"],
                "memory_key": e["memory_key"],
                "alias": (e.get("content") or {}).get("alias"),
                "target": (e.get("content") or {}).get("target"),
                "application": (e.get("content") or {}).get("application"),
                "business": (e.get("content") or {}).get("business"),
                "scene": (e.get("content") or {}).get("scene"),
                "score": e["score"],
            }
            for e in expression_hits
        ],
    }


def _serialize_working_and_exp_trace_hits(
    working_hits: list[dict[str, Any]],
    experience_hits: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """序列化工作记忆与经验记忆层的检索命中快照。"""
    return {
        "working_hits": [
            {
                "memory_id": w["memory_id"],
                "memory_key": w["memory_key"],
                "record_id": (w.get("content") or {}).get("record_id"),
                "application_name": (w.get("content") or {}).get("application_name"),
                "service_name": (w.get("content") or {}).get("service_name"),
                "resolution": (w.get("content") or {}).get("resolution"),
                "rtt": (w.get("content") or {}).get("rtt"),
                "location": (w.get("content") or {}).get("location"),
                "created_at": (w.get("content") or {}).get("created_at"),
                "score": w["score"],
                "structured_match": (w.get("explain") or {}).get("structured_match", ""),
            }
            for w in working_hits
        ],
        "experience_hits": [
            {
                "memory_id": ex["memory_id"],
                "memory_key": ex["memory_key"],
                "experience_id": (ex.get("content") or {}).get("experience_id"),
                "title": (ex.get("content") or {}).get("title"),
                "condition": (ex.get("content") or {}).get("condition"),
                "action": (ex.get("content") or {}).get("action"),
                "score": ex["score"],
                "structured_match": (ex.get("explain") or {}).get("structured_match", ""),
            }
            for ex in experience_hits
        ],
    }


def build_trace_info(
    turn_index: int,
    utterance: str,
    reference_time: str,
    has_trigger: bool,
    session_memory_triggered: bool,
    session_wr_mode: str | None,
    session_exp_triggered: str | None,
    raw_hits: list[dict[str, Any]],
    major_hits: list[dict[str, Any]],
    periodic_hits: list[dict[str, Any]],
    service_hits: list[dict[str, Any]],
    expression_hits: list[dict[str, Any]],
    working_hits: list[dict[str, Any]],
    experience_hits: list[dict[str, Any]],
    best_major: dict[str, Any] | None,
) -> dict[str, Any]:
    """构造单轮记忆检索与触发状态追踪字典。"""
    trace: dict[str, Any] = {
        "turn": turn_index,
        "query": utterance,
        "reference_time": reference_time,
        "has_memory_trigger": has_trigger,
        "session_memory_triggered": session_memory_triggered,
        "session_wr_mode": session_wr_mode,
        "session_exp_triggered": session_exp_triggered,
        "raw_hit_count": len(raw_hits),
    }
    trace.update(
        _serialize_factual_trace_hits(major_hits, periodic_hits, service_hits, expression_hits)
    )
    trace.update(_serialize_working_and_exp_trace_hits(working_hits, experience_hits))
    trace["selected_best_major"] = best_major["resolved_slots"] if best_major else None
    return trace
