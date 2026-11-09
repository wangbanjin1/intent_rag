"""bridge_working.py — 工作记忆（Working Memory）工单选型、规则渲染与会话后工单抽取。

职责:
  1. 数据驱动地挑选目标工作记忆工单 (_pick_working_record)
  2. 渲染工作记忆三模式规则 (reschedule / disambig / reuse 及其多轮确认规则)
  3. 会话结束后基于系统预测状态抽取工作记忆工单 (extract_working_record_from_session)
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_PARENT = Path(__file__).resolve().parent.parent
for _cand_ms in (
    Path(os.environ["MEMORY_SYSTEM_DIR"]) / "src" if os.environ.get("MEMORY_SYSTEM_DIR") else None,
    _REPO_PARENT / "memory_system" / "src",
    _REPO_PARENT / "mem_system" / "src",
    Path(r"D:\coding\memory_system\src"),
):
    if _cand_ms is not None and _cand_ms.exists() and str(_cand_ms) not in sys.path:
        sys.path.insert(0, str(_cand_ms))
        break

from memory_system.core.working_rules import (
    classify_working_record_reference,
    has_explicit_time,
    reschedule_shift_minutes,
)
from memory_system.pipeline.extractor import _TEMP_VETO_WORDS

import time_extractor as _tex
from bridge_factual import DT_FMT, _explicit_window
from prompts.memory_hint_templates import (
    WORKING_RECORD_CONFIRM_RULE,
    WORKING_RECORD_DISAMBIG_RULE,
    WORKING_RECORD_RESCHEDULE_CONFIRM_RULE,
    format_working_disambig_resolve_rule,
    format_working_reschedule_rule,
    format_working_reschedule_time_fill_rule,
    format_working_reuse_rule,
    format_working_reuse_time_fill_rule,
)

_EXPLICIT_SHIFT_UNIT_RE = re.compile(
    r"(?:推迟|延后|往后挪|往后推|顺延|提前|往前挪|往前提)\s*(?:半|一|两|二|三|四|\d+)\s*个?\s*(?:半)?\s*(?:小时|钟头|分钟)"
)

_LOC_RE = re.compile(
    r"(?:在|到)([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9]{1,23}?)"
    r"(?=这边|这儿|那边|那儿|这里|，|,|。|；|！|\s|办|开|用|做|搞|弄|准备|帮我|给|跟|要|有|了|拍|带队|打算|想)"
)
_LOC_PRONOUNS = {"这儿", "这边", "那边", "那儿", "这里", "那里", "线上", "路上", "现场"}
_PARAM_VALUE_RE = re.compile(r"\d+\s*[pP]\b|\d+\s*(?:ms|毫秒)|[24][kK]|调到|压到|改成|改为|换成|降到|提到")


def _is_temp_record(rec: dict[str, Any]) -> bool:
    """判断工单是否为单次临时覆写记录。"""
    return bool(rec.get("is_temporary_override")) or rec.get("validity") == "temporary"


def _rec_sort_key(rec: dict[str, Any]) -> str:
    """工单时间/编号排序键。"""
    return str(rec.get("created_at") or rec.get("record_id") or "")


def _pick_working_record(
    candidates: list[dict[str, Any]], utterance: str, prefer_latest: bool = False
) -> dict[str, Any] | None:
    """数据驱动地挑选目标工单: 排除临时覆写与静态预置 -> 地点命中 -> 应用命中 -> (最近 | 检索排序第一)。"""
    if not candidates:
        return None
    pool = [
        c for c in candidates
        if not _is_temp_record(c) and not c.get("is_preseed_record")
    ] or [c for c in candidates if not _is_temp_record(c)] or list(candidates)
    loc_hits = [
        c for c in pool
        if str(c.get("location") or "").strip() and str(c["location"]).strip() in (utterance or "")
    ]
    pool = loc_hits or pool
    app_hits = [
        c for c in pool
        if c.get("application_name") and str(c["application_name"]) in (utterance or "")
    ]
    pool = app_hits or pool
    if prefer_latest:
        return dict(max(pool, key=_rec_sort_key))
    return dict(pool[0])


def _extract_location(text: str) -> str:
    """仅从用户话语中抽取地点 ("今天在X办…" / "到X了"), 不读取数据集 scenario 元数据。"""
    for m in _LOC_RE.finditer(text or ""):
        cand = m.group(1)
        if cand in _LOC_PRONOUNS or re.search(r"\d|ms|以内|以下|时|分钟|小时|之前|上次|上周", cand):
            continue
        return cand
    return ""


def _is_temporary_override_dialogue(user_texts: list[str]) -> bool:
    """仅凭用户话语判断本次是否为单次临时改参: 同一子句内同时出现临时否决词与具体参数改动。"""
    for txt in user_texts:
        for clause in re.split(r"[，,。；;！!？?\n]", txt or ""):
            if any(w in clause for w in _TEMP_VETO_WORDS) and _PARAM_VALUE_RE.search(clause):
                return True
    return False


def select_target_working_record(
    working_hits: list[dict[str, Any]],
    all_working_records: list[dict[str, Any]],
    best_major: dict[str, Any] | None,
    utterance: str,
) -> dict[str, Any] | None:
    """从检索命中的 working_hits 中数据驱动地挑选目标工作记忆工单。"""
    if working_hits:
        return _pick_working_record(
            [dict(wh.get("content") or {}) for wh in working_hits], utterance, prefer_latest=False
        )
    if all_working_records:
        return _pick_working_record(all_working_records, utterance, prefer_latest=False)
    return None


def _format_reschedule_constraints(
    utterance: str,
    reference_time: str,
    target_wr: dict[str, Any],
    all_working_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """计算历史预约工单改期后的起止时间并格式化改期规则段。"""
    ref_dt = _tex.parse_reference_time(reference_time)
    pool = [r for r in all_working_records if not _is_temp_record(r)]
    picked = _pick_working_record(pool, utterance, prefer_latest=False)
    if picked:
        target_wr = picked
    resched_start = resched_end = None
    pre_shift_desc = ""
    has_concrete_shift = bool(_EXPLICIT_SHIFT_UNIT_RE.search(utterance or "")) or has_explicit_time(utterance)
    if has_concrete_shift:
        try:
            o_st = datetime.strptime(str(target_wr.get("start_timestamp")), DT_FMT)
            o_et = datetime.strptime(str(target_wr.get("end_timestamp")), DT_FMT)
            base = ref_dt if ref_dt is not None else o_st
            a_st = base.replace(hour=o_st.hour, minute=o_st.minute, second=0, microsecond=0)
            a_et = a_st + (o_et - o_st)
            resched_start, resched_end = a_st.strftime(DT_FMT), a_et.strftime(DT_FMT)
            shift_m = reschedule_shift_minutes(utterance)
            if shift_m:
                pre_st = a_st - timedelta(minutes=shift_m)
                pre_et = a_et - timedelta(minutes=shift_m)
                pre_shift_desc = (
                    f"#   (注: 原预约基准时段为 {pre_st.strftime('%H:%M')}~{pre_et.strftime('%H:%M')}, "
                    f"系统已执行完您要求的{'提前' if shift_m < 0 else '推迟'}{abs(shift_m)}分钟偏移, "
                    f"最终改期时段即下方 {a_st.strftime('%H:%M')}~{a_et.strftime('%H:%M')}, 严禁在下方时段基础上再次加减1小时!)\n"
                )
        except (TypeError, ValueError):
            resched_start = resched_end = None
    resched_dur = target_wr.get("duration")
    rule_txt = format_working_reschedule_rule(
        target_wr, resched_start, resched_end, resched_dur, pre_shift_desc
    )
    return target_wr, rule_txt


def _format_disambiguation_candidates(
    turn_index: int,
    utterance: str,
    session_turn1_utterance: str,
    twr: dict[str, Any],
    all_working_records: list[dict[str, Any]],
) -> str:
    """渲染工作记忆多工单待消歧（首轮悬空 / 次轮选定 / 确认轮保持）规则段。"""
    if turn_index == 1:
        return WORKING_RECORD_DISAMBIG_RULE
    if turn_index == 2:
        latest = _pick_working_record(
            all_working_records, session_turn1_utterance or utterance, prefer_latest=True
        )
        if latest:
            twr = latest
        return format_working_disambig_resolve_rule(twr)
    return WORKING_RECORD_CONFIRM_RULE


def _format_matched_working_record(
    turn_index: int,
    utterance: str,
    reference_time: str,
    twr: dict[str, Any],
) -> str:
    """渲染工作记忆单笔工单跨期复用（首轮继承规格 / 次轮补充今日时段）规则段。"""
    if turn_index == 1:
        return format_working_reuse_rule(twr)
    u_st, _ = _explicit_window(utterance, reference_time)
    ref_dt = _tex.parse_reference_time(reference_time)
    rec_st = rec_et = None
    if u_st is None and ref_dt is not None and twr.get("start_timestamp"):
        try:
            _s = datetime.strptime(str(twr.get("start_timestamp")), DT_FMT)
            _e = datetime.strptime(str(twr.get("end_timestamp")), DT_FMT)
            rec_st = ref_dt.replace(hour=_s.hour, minute=_s.minute, second=0, microsecond=0)
            rec_et = rec_st + (_e - _s)
        except (TypeError, ValueError):
            rec_st = rec_et = None
    if rec_st is not None and rec_et is not None and not has_explicit_time(utterance):
        return format_working_reuse_time_fill_rule(
            twr, rec_st.strftime(DT_FMT), rec_et.strftime(DT_FMT)
        )
    return format_working_reuse_time_fill_rule(twr)


def render_working_memory_rules(
    turn_index: int,
    utterance: str,
    reference_time: str,
    prev_states: dict[str, dict[str, Any]],
    exp_applied: bool,
    target_wr: dict[str, Any] | None,
    all_working_records: list[dict[str, Any]],
    session_wr_mode: str | None,
    session_wr_target: dict[str, Any] | None,
    session_turn1_utterance: str,
) -> tuple[str | None, dict[str, Any] | None, list[str]]:
    """根据当前轮次和工单引用类型渲染 Tier 2 工作记忆规则。"""
    hint_lines: list[str] = []
    wr_act = classify_working_record_reference(utterance) if turn_index == 1 else None

    if turn_index == 1 and not exp_applied and target_wr is not None:
        if wr_act == "reschedule":
            target_wr, rule_txt = _format_reschedule_constraints(
                utterance, reference_time, target_wr, all_working_records
            )
            session_wr_mode = "reschedule"
            session_wr_target = target_wr
            hint_lines.append(rule_txt)
        elif wr_act == "disambig":
            session_wr_mode = "disambig"
            session_wr_target = target_wr
            hint_lines.append(
                _format_disambiguation_candidates(
                    1, utterance, session_turn1_utterance, target_wr, all_working_records
                )
            )
        elif wr_act == "reuse":
            session_wr_mode = "reuse"
            session_wr_target = target_wr
            hint_lines.append(_format_matched_working_record(1, utterance, reference_time, target_wr))

    elif turn_index > 1 and session_wr_mode and prev_states:
        twr = session_wr_target or target_wr or {}
        if session_wr_mode == "reschedule":
            u_st, u_et = _explicit_window(utterance, reference_time)
            if u_st is not None and u_et is not None:
                hint_lines.append(
                    format_working_reschedule_time_fill_rule(
                        twr, u_st.strftime(DT_FMT), u_et.strftime(DT_FMT)
                    )
                )
            else:
                hint_lines.append(WORKING_RECORD_RESCHEDULE_CONFIRM_RULE)
        elif session_wr_mode == "disambig":
            hint_lines.append(
                _format_disambiguation_candidates(
                    turn_index, utterance, session_turn1_utterance, twr, all_working_records
                )
            )
        elif session_wr_mode == "reuse":
            hint_lines.append(
                _format_matched_working_record(turn_index, utterance, reference_time, twr)
            )

    return session_wr_mode, session_wr_target, hint_lines


def extract_working_record_from_session(
    session: dict[str, Any], predicted_final: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """用【系统自身预测的最终意图状态】生成本会话的 working_record (工作记忆账本条目)。"""
    if not predicted_final:
        return None
    cands = [
        dict(v) for v in predicted_final.values()
        if isinstance(v, dict) and v.get("application_name") and v.get("service_name")
    ]
    if not cands:
        return None
    pf = cands[-1]

    user_id = str(session.get("user_id", "default_user"))
    session_id = str(session.get("session_id", ""))
    reference_time = str(session.get("reference_time", ""))
    user_texts: list[str] = []
    for t in session.get("event_sequence") or []:
        u = t.get("user") if isinstance(t, dict) else None
        utt = u.get("utterance", "") if isinstance(u, dict) else (str(u) if u else "")
        if utt:
            user_texts.append(utt)

    m_ymd = re.search(r"(\d{4})年(\d{2})月(\d{2})日", reference_time)
    m_idx = re.search(r"-(\d+)$", session_id)
    sess_idx = m_idx.group(1) if m_idx else (session_id or "00")
    if m_ymd:
        record_id = f"REC_{user_id}_{m_ymd.group(1)}{m_ymd.group(2)}{m_ymd.group(3)}_{sess_idx}"
    else:
        record_id = f"REC_{user_id}_{sess_idx}"

    loc = ""
    for utt in user_texts:
        loc = _extract_location(utt)
        if loc:
            break
    is_temp = _is_temporary_override_dialogue(user_texts)
    return {
        "record_id": record_id,
        "session_id": session_id,
        "created_at": reference_time,
        "application_name": pf.get("application_name") or "",
        "service_name": pf.get("service_name") or "",
        "resolution": pf.get("resolution") or "",
        "rtt": pf.get("rtt") or "",
        "start_timestamp": pf.get("start_timestamp") or "",
        "end_timestamp": pf.get("end_timestamp") or "",
        "duration": pf.get("duration") or "",
        "location": loc,
        "scene": loc,
        "status": "completed",
        "is_temporary_override": is_temp,
        "validity": "temporary" if is_temp else "long_term",
        "temporal_reference_aliases": ["上次", "上周", "上个月初", "之前", "昨天预约"],
    }


_SLANG_QUOTE_RE = re.compile(r"[‘'“\"「『]([^’'”\"」』]{2,16})[’'”\"」』]")


def synthesize_predicted_agent_reply(
    turn_pred: dict[str, dict[str, Any]] | None, user_utt: str = ""
) -> str:
    """仅根据【模型自身预测的当前轮槽位状态】合成系统回复，严禁读取数据集静态客服台词。"""
    if not turn_pred:
        return "收到，请问您需要办理哪项应用与业务的网络保障？"

    slang_m = _SLANG_QUOTE_RE.search(user_utt or "")
    parts_per_intent: list[str] = []
    missing_all: list[str] = []
    for pf in turn_pred.values():
        if not isinstance(pf, dict) or not pf.get("application_name"):
            continue
        app_svc = f"{pf.get('application_name', '')}{pf.get('service_name', '')}"
        known: list[str] = []
        missing: list[str] = []
        if pf.get("resolution"):
            known.append(f"画质{pf['resolution']}")
        else:
            missing.append("画质")
        if pf.get("rtt"):
            known.append(f"时延{pf['rtt']}以内")
        else:
            missing.append("时延")
        st = str(pf.get("start_timestamp") or "")
        et = str(pf.get("end_timestamp") or "")
        dur = str(pf.get("duration") or "")
        st_hm = st.split("日")[-1] if "日" in st else st
        et_hm = et.split("日")[-1] if "日" in et else et
        if st_hm and et_hm:
            dur_str = f"（持续{dur}）" if dur else ""
            known.append(f"{st_hm}至{et_hm}{dur_str}")
        elif st_hm:
            known.append(f"从{st_hm}开始")
            missing.append("预计持续时长")
        else:
            missing.append("保障起止时间")
        known_txt = "、".join(known) if known else "基础业务"
        parts_per_intent.append(f"【{app_svc}】（{known_txt}）")
        missing_all.extend(m for m in missing if m not in missing_all)

    if not parts_per_intent:
        return "收到，请问您需要办理哪项应用与业务的网络保障？"
    summary_txt = "；".join(parts_per_intent)
    if slang_m and ("画质" in missing_all or "时延" in missing_all):
        phrase = slang_m.group(1)
        slot_cn = "画质档位" if "画质" in missing_all else "时延上限"
        return f"收到，已为您记录{summary_txt}。您说的‘{phrase}’具体是指哪档{slot_cn}？"
    if missing_all:
        return f"收到，已为您锁定{summary_txt}。请问您需要保障的{'、'.join(missing_all)}分别是多少呢？"
    return f"收到，已为您准备好{summary_txt}意图保障单配置，确认按这套配置开通吗？"


def build_predicted_session_turns(
    raw_turns: list[dict[str, Any]],
    per_turn_pred: list[dict[str, Any]] | None,
    predicted_final: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """构造写回记忆系统的对话轮次：保留用户原话，将客服回复替换为模型自身预测合成文本。"""
    pred_by_turn: dict[int, dict[str, dict[str, Any]]] = {}
    for ptp in per_turn_pred or []:
        t_idx = int(ptp.get("turn", 0))
        if t_idx > 0 and isinstance(ptp.get("preds"), dict):
            pred_by_turn[t_idx] = ptp["preds"]

    sanitized: list[dict[str, Any]] = []
    for idx, ev in enumerate(raw_turns, 1):
        if not isinstance(ev, dict):
            continue
        t_no = int(ev.get("turn", idx))
        u_obj = ev.get("user") if isinstance(ev.get("user"), dict) else {}
        u_utt = str(u_obj.get("utterance") or "")
        t_preds = pred_by_turn.get(t_no) or (predicted_final if idx == len(raw_turns) else {})
        synth_agent = synthesize_predicted_agent_reply(t_preds, u_utt)
        ev_copy = dict(ev)
        ev_copy["agent"] = {"utterance": synth_agent}
        sanitized.append(ev_copy)
    return sanitized


def preseed_user_static_memory(
    ms: Any,
    user_id: str,
    preseed_data: dict[str, Any],
    tenant_id: str = "default",
) -> None:
    """在会话序列开始前（Stage 0）将外部静态画像（Profile）与历史工单（Records）写入 MemorySystem。"""
    from memory_system.core.models import Event, EventType
    from memory_system.core.namespace import user_namespace

    ns = user_namespace(user_id=user_id, tenant_id=tenant_id)
    ev_ts = str(preseed_data.get("event_time") or "2026-04-01T00:00:00+08:00")
    for p in preseed_data.get("profiles") or []:
        if not isinstance(p, dict):
            continue
        app = str(p.get("application") or p.get("application_name") or "").strip()
        biz = str(p.get("business") or p.get("service_name") or "").strip()
        if not app or not biz:
            continue
        pv_content: dict[str, Any] = {
            "application": app,
            "business": biz,
            "app_cn": str(p.get("application_name") or app),
            "service_cn": str(p.get("service_name") or biz),
            "declaration_mode": "preseed_profile",
            "validity": "long_term",
        }
        for k_src, k_dst in (
            ("resolution", "resolution"),
            ("latency", "latency"),
            ("rtt", "latency"),
            ("duration", "duration"),
            ("default_duration", "default_duration"),
            ("time_window", "time_window"),
        ):
            if p.get(k_src) not in (None, ""):
                pv_content[k_dst] = p[k_src]
        if pv_content.get("duration") and not pv_content.get("default_duration"):
            pv_content["default_duration"] = pv_content["duration"]
        for ev_type in (EventType.PREFERENCE_SET.value, EventType.PREFERENCE_CONFIRM.value):
            ev = Event(
                event_type=ev_type,
                subtype="preferred_value",
                memory_key_fields={"application": app, "business": biz},
                content=dict(pv_content),
                validity="long_term",
                confidence=0.95,
                user_id=user_id,
                tenant_id=tenant_id,
                event_time=ev_ts,
            )
            ms.updater.apply_event(ns, ev)
        tw = str(p.get("time_window") or "").strip()
        if tw:
            val_str = f"{app}:{biz}"
            pp_content = {
                "condition": tw,
                "value": val_str,
                "time_window": tw,
                "application": app,
                "business": biz,
                "app_cn": pv_content["app_cn"],
                "service_cn": pv_content["service_cn"],
                "declaration_mode": "preseed_profile",
                "validity": "long_term",
            }
            for ev_type in (EventType.PREFERENCE_SET.value, EventType.PREFERENCE_CONFIRM.value):
                ev_pp = Event(
                    event_type=ev_type,
                    subtype="periodic_preference",
                    memory_key_fields={"condition": tw, "value": val_str},
                    content=dict(pp_content),
                    validity="long_term",
                    confidence=0.95,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    event_time=ev_ts,
                )
                ms.updater.apply_event(ns, ev_pp)

    recs = [dict(r, is_preseed_record=True) for r in (preseed_data.get("records") or []) if isinstance(r, dict)]
    if recs:
        ms.ingest_working_records(tenant_id, user_id, recs)
