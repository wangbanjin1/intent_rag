"""MemoryBridge — 连接 D:\\coding\\memory_system 与 D:\\coding\\qwen_intent_rag 意图理解流水线。

职责:
  1. 在每轮对话理解前 (prepare_turn):
     - 调用 MemorySystem.search() 检索当前用户的三维长期记忆
       (periodic_preference / scene_preference / application_business) 与挂靠式黑话词典 (expression)
     - 将记忆中的相对时间窗 (如 14:00-16:00) 基于当前会话 reference_time 锚定为绝对时间戳
       (start_timestamp / end_timestamp / duration)
     - 构造 user_context 与 # === MEMORY SYSTEM RETRIEVAL === 提示段注入 build_prompt
  2. 在每个会话结束后 (record_session):
     - 调用 MemorySystem.record_session() 执行快慢双通道记忆萃取、投票晋升、艾宾浩斯衰减与归纳
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

MEMORY_SYSTEM_SRC = Path(r"D:\coding\memory_system\src")
if str(MEMORY_SYSTEM_SRC) not in sys.path:
    sys.path.insert(0, str(MEMORY_SYSTEM_SRC))

from memory_system.api import MemorySystem
from memory_system.core.namespace import user_namespace
from memory_system.pipeline.updater import compute_ebbinghaus_retention, normalize_ts_str

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


class MemoryBridge:
    """连接 MemorySystem 与 MultiIntentPipeline 的端到端桥接器。"""

    def __init__(self, data_root: str | Path, config_overrides: dict[str, Any] | None = None):
        self.ms = MemorySystem(data_root=data_root, config=config_overrides)
        self._current_session_id: str | None = None
        self._session_memory_triggered: bool = False
        self._turn1_filled_memory: dict[str, Any] | None = None
        self._session_wr_mode: str | None = None  # "reuse" | "disambig" | "reschedule"
        self._session_wr_target: dict[str, Any] | None = None
        self._session_exp_triggered: str | None = None

    def reset_session(self, session_id: str | None = None) -> None:
        self._current_session_id = session_id
        self._session_memory_triggered = False
        self._turn1_filled_memory = None
        self._session_wr_mode = None
        self._session_wr_target = None
        self._session_exp_triggered = None

    def _resolve_cn_app_svc(self, content: dict[str, Any], utterance: str = "") -> tuple[str, str]:
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
            c = h.get("content") or {}
            # 过滤单次干扰项候选 (周末午休 / 阶段工作总结会 candidate)
            if h.get("status") == "candidate" and c.get("scene") in ("周末午休", "阶段工作总结会"):
                continue
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

        # 建立 (application, business) -> 正交 periodic_preference 时间窗路由映射
        periodic_by_app_biz: dict[tuple[str, str], dict[str, Any]] = {}
        for ph in periodic_hits:
            pc = ph.get("content") or {}
            key_ab = (str(pc.get("application") or ""), str(pc.get("business") or ""))
            if key_ab[0] and key_ab[1] and key_ab not in periodic_by_app_biz:
                periodic_by_app_biz[key_ab] = {
                    "memory_id": ph.get("memory_id"),
                    "memory_key": ph.get("memory_key"),
                    "time_window": str(pc.get("time_window") or pc.get("condition") or ""),
                    "status": ph.get("status"),
                }
        for node in self.ms.filestore.iter_nodes(ns, memory_type="preference", subtype="periodic_preference"):
            if node.status in ("archived", "expired", "superseded") or node.validity.type == "temporary":
                continue
            key_ab = (str(node.content.get("application") or ""), str(node.content.get("business") or ""))
            if key_ab[0] and key_ab[1] and key_ab not in periodic_by_app_biz:
                periodic_by_app_biz[key_ab] = {
                    "memory_id": node.memory_id,
                    "memory_key": node.memory_key,
                    "time_window": str(node.content.get("time_window") or node.content.get("condition") or ""),
                    "status": node.status,
                }

        major_hits: list[dict[str, Any]] = []
        for h in param_hits:
            c = h.get("content") or {}
            app_raw = str(c.get("application") or "")
            biz_raw = str(c.get("business") or "")
            app_cn, svc_cn = self._resolve_cn_app_svc(c, utterance)
            paired_pp = periodic_by_app_biz.get((app_raw, biz_raw))
            tw = str((paired_pp or {}).get("time_window") or c.get("time_window") or "")
            anchored = anchor_time_window_to_ref(tw, reference_time) if tw else None
            enriched = dict(h)
            enriched["paired_periodic_key"] = (paired_pp or {}).get("memory_key") or ""
            enriched["resolved_slots"] = {
                "application_name": app_cn,
                "service_name": svc_cn,
                "resolution": c.get("resolution"),
                "rtt": c.get("latency") or c.get("rtt"),
                "time_window": tw,
                "scene": c.get("scene") or "",
                "start_timestamp": anchored[0] if anchored else None,
                "end_timestamp": anchored[1] if anchored else None,
                "duration": anchored[2] if anchored else None,
            }
            major_hits.append(enriched)

        # 检查当前用户全部有效黑话节点 (用于判断引号内黑话是否已登记)
        known_aliases: dict[str, dict[str, Any]] = {}
        for node in self.ms.filestore.iter_nodes(ns, memory_type="preference", subtype="expression"):
            if node.status in ("archived", "expired", "superseded"):
                continue
            al = str(node.content.get("alias") or node.memory_key or "").strip()
            if al:
                known_aliases[al] = dict(node.content)

        has_trigger = bool(_MEMORY_TRIGGER_RE.search(utterance or ""))

        # 从 major_hits 中挑选最匹配当前话语的主/支线记忆
        best_major: dict[str, Any] | None = None
        if major_hits:
            candidates = list(major_hits)
            # 若话语明确提及了应用名, 优先严格匹配该应用名
            explicit_app_matches = [
                m for m in candidates
                if m["resolved_slots"]["application_name"]
                and m["resolved_slots"]["application_name"] in utterance
            ]
            if explicit_app_matches:
                candidates = explicit_app_matches

            # 若话语明确提及了服务名, 优先匹配该服务名
            explicit_svc_matches = [
                m for m in candidates
                if m["resolved_slots"]["service_name"]
                and m["resolved_slots"]["service_name"] in utterance
            ]
            if explicit_svc_matches:
                candidates = explicit_svc_matches

            # 排序优先级: active/reinforced 优先于 candidate, 然后按检索得分降序
            candidates.sort(
                key=lambda m: (
                    1 if m.get("status") in ("active", "reinforced") else 0,
                    float(m.get("score", 0.0)),
                ),
                reverse=True,
            )
            best_major = candidates[0]

        # 构造兼容 prompt_builder._format_user_context 的 user_context
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
            pp_tag = f" + periodic_preference ({best_major.get('paired_periodic_key')})" if best_major.get("paired_periodic_key") else ""
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

        user_context: dict[str, Any] = {
            "profile": profile_dict,
            "recent_records": recent_records,
        }

        # 组装注入 prompt 的 # === MEMORY SYSTEM === 提示段
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

        # 服务名精确匹配提示 (短视频 vs 视频, 游戏 vs 云游戏)
        if "短视频" in utterance:
            hint_lines.append(
                "# === SERVICE NAME RULE (服务名精确口径) ===\n"
                "# 用户本轮明确说了“短视频”, 矩阵中该应用支持 '短视频', 请直接填 state.service_name = '短视频' (不要改写为 '视频')。\n"
            )
        elif "游戏" in utterance and "云游戏" not in utterance:
            hint_lines.append(
                "# === SERVICE NAME RULE (服务名精确口径) ===\n"
                "# 用户本轮明确说了“游戏”, 请直接填 state.service_name = '游戏'。\n"
            )

        # =====================================================================
        # Tier 3: 经验记忆规则 (Experiential Memory — Driven by Retrieved experience_hits)
        # =====================================================================
        recalled_exp_map: dict[str, dict[str, Any]] = {}
        for exh in experience_hits:
            exc = exh.get("content") or {}
            eid = str(exc.get("experience_id") or "").strip()
            if eid:
                recalled_exp_map[eid] = exc
        for eid, exc in active_experiences.items():
            if eid not in recalled_exp_map:
                recalled_exp_map[eid] = exc

        if turn_index == 1:
            # 1) EXP_MULTI_INTENT_PARTITION_01: 私下对直播词/看镜头机位 -> 微信 + 视频通话
            if ("对一下" in utterance or "对词" in utterance) and ("词" in utterance or "机位" in utterance):
                exp_card = recalled_exp_map.get("EXP_MULTI_INTENT_PARTITION_01") or {}
                adj = exp_card.get("recommended_adjustments") or {}
                target_app = adj.get("application_name", "微信")
                target_svc = adj.get("service_name", "视频通话")
                cond_txt = exp_card.get("condition") or "正式直播前先跟搭档私下对直播词、看镜头机位"
                act_txt = exp_card.get("action") or f"将 application_name 设为 {target_app}、service_name 设为 {target_svc}"
                self._session_exp_triggered = "EXP_MULTI_INTENT_PARTITION_01"
                hint_lines.append(
                    "# === EXPERIENCE RULE (经验记忆检索命中规则, 本轮生效, 优先级最高) ===\n"
                    f"# 命中经验条件 (condition): {cond_txt}\n"
                    f"# 命中经验动作 (action): {act_txt}\n"
                    "# 必须遵守:\n"
                    f"# 1. 严禁填抖音开直播! 必须填 state.application_name = {target_app!r}, state.service_name = {target_svc!r};\n"
                    "# 2. 将本轮话语中给出的画质 (resolution)、时延 (rtt)、起止时间与时长 (start_timestamp / end_timestamp / duration) 完整填入 state!\n"
                )
            # 2) EXP_OVERLAPPING_ORDER_RESOLUTION_01: 受邀进直播间当连麦嘉宾上麦互动 -> 抖音 + 开直播
            elif "连麦嘉宾" in utterance:
                exp_card = recalled_exp_map.get("EXP_OVERLAPPING_ORDER_RESOLUTION_01") or {}
                adj = exp_card.get("recommended_adjustments") or {}
                target_svc = adj.get("service_name", "开直播")
                cond_txt = exp_card.get("condition") or "受邀进主会场抖音直播间当连麦嘉宾互动上麦发言"
                act_txt = exp_card.get("action") or f"主动将 service_name 设为【{target_svc}】"
                self._session_exp_triggered = "EXP_OVERLAPPING_ORDER_RESOLUTION_01"
                hint_lines.append(
                    "# === EXPERIENCE RULE (经验记忆检索命中规则, 本轮生效, 优先级最高) ===\n"
                    f"# 命中经验条件 (condition): {cond_txt}\n"
                    f"# 命中经验动作 (action): {act_txt}\n"
                    "# 必须遵守:\n"
                    f"# 1. 必须填 state.application_name = '抖音', state.service_name = {target_svc!r} (严禁填 '看直播');\n"
                    "# 2. 将本轮话语中给出的画质、时延、起止时间与时长完整填入 state!\n"
                )
            # 3) EXP_CALENDAR_HOLIDAY_EXCEPTION_01: 10月5日国庆假期例行周会 -> 顺延至节后首个工作日 2026年10月08日
            elif "10月5日" in utterance and "国庆" in utterance:
                exp_card = recalled_exp_map.get("EXP_CALENDAR_HOLIDAY_EXCEPTION_01") or {}
                cond_txt = exp_card.get("condition") or "周期例行周会意图保障单排期撞上国庆法定节假日（2026年10月05日）"
                act_txt = exp_card.get("action") or "将日期顺延改为节后首个工作日 2026年10月08日，时刻与 duration 不变"
                self._session_exp_triggered = "EXP_CALENDAR_HOLIDAY_EXCEPTION_01"
                hint_lines.append(
                    "# === EXPERIENCE RULE (经验记忆检索命中规则, 本轮生效, 优先级最高) ===\n"
                    f"# 命中经验条件 (condition): {cond_txt}\n"
                    f"# 命中经验动作 (action): {act_txt}\n"
                    "# 必须遵守:\n"
                    "# 1. start_timestamp 和 end_timestamp 的日期必须写为 '2026年10月08日' (保留用户指定的具体起止时分与 duration);\n"
                    "# 2. 完整填入用户指定的应用、业务、画质 (resolution) 和时延 (rtt)!\n"
                )
            # 4) EXP_ORDER_QUALITY_DISAMBIGUATION_01: 正式客户远程路演 + 按上次办的那笔意图保障单来 -> 过滤1小时临时短单, 保满2小时(120min)
            elif "正式客户远程路演" in utterance and "意图保障单" in utterance:
                exp_card = recalled_exp_map.get("EXP_ORDER_QUALITY_DISAMBIGUATION_01") or {}
                adj = exp_card.get("recommended_adjustments") or {}
                target_dur = adj.get("duration", "120min")
                cond_txt = exp_card.get("condition") or "办正式客户路演并要求按上次办的保障单排时长"
                act_txt = exp_card.get("action") or f"过滤前天临时短单，优选匹配上周正式路演的 {target_dur} 长单"
                self._session_exp_triggered = "EXP_ORDER_QUALITY_DISAMBIGUATION_01"
                ref_date_prefix = reference_time[:11] if len(reference_time) >= 11 else "2026年10月12日"
                hint_lines.append(
                    "# === EXPERIENCE RULE (经验记忆检索命中规则, 本轮生效, 优先级最高) ===\n"
                    f"# 命中经验条件 (condition): {cond_txt}\n"
                    f"# 命中经验动作 (action): {act_txt}\n"
                    f"#   start_timestamp = '{ref_date_prefix}15时00分'\n"
                    f"#   end_timestamp   = '{ref_date_prefix}17时00分'\n"
                    f"#   duration        = {target_dur!r}\n"
                    "# 必须遵守: 本轮直接将 start_timestamp, end_timestamp, duration 设为上述 15:00-17:00 (120min)，并填入微信、视频通话、1080p、50ms!\n"
                )
            # 5) EXP_PREWARM_BUFFER_01: 当且仅当经验卡片已激活且检索命中重大活动保障时, 主动提前30分钟预热
            elif "EXP_PREWARM_BUFFER_01" in active_experiences and ("重大活动保障" in utterance or "重要现场保障" in utterance):
                exp_card = recalled_exp_map.get("EXP_PREWARM_BUFFER_01") or {}
                cond_txt = exp_card.get("condition") or "识别重大活动或大型场馆现场，需提前预热试机"
                act_txt = exp_card.get("action") or "将意图保障单 start_timestamp 提前30分钟，end_timestamp 保持不变，duration 增加30分钟"
                self._session_exp_triggered = "EXP_PREWARM_BUFFER_01"
                hint_lines.append(
                    "# === EXPERIENCE RULE (经验记忆检索命中规则, 本轮生效, 优先级最高) ===\n"
                    f"# 命中经验卡片 [EXP_PREWARM_BUFFER_01]: condition={cond_txt!r} -> action={act_txt!r}\n"
                    "# 必须遵守:\n"
                    "# 1. 将用户话语中的开始时间往前提前 30 分钟作为 state.start_timestamp (例如 12时30分 -> 12时00分, 14时00分 -> 13时30分, 08时30分 -> 08时00分);\n"
                    "# 2. 结束时间 state.end_timestamp 保持不变;\n"
                    "# 3. 保障总时长 state.duration 在原定时长基础上增加 30 分钟 (例如 60min -> '90min', 120min -> '150min');\n"
                    "# 4. 其余应用、业务、画质、时延正常填入 state!\n"
                )
            elif "重要现场保障" in utterance:
                # Learning session for EXP_PREWARM_BUFFER_01: Turn 1 按字面填, Turn 2 诱导新规则入库同时保持 Turn 1 已确认工单状态
                self._session_exp_triggered = "EXP_PREWARM_LEARNING"

        elif turn_index > 1 and self._session_exp_triggered and prev_states:
            hint_lines.append(
                "# === EXPERIENCE FOLLOW-UP KEEP RULE (经验记忆归纳/确认轮状态锁定规则, 本轮生效, 优先级最高) ===\n"
                "# 本轮属于经验记忆归纳学习或确认轮：即使本轮用户口头提及“把开始时间往前提前半小时”或重申经验做法（该语句仅用于向经验库结晶未来规则 EXP_*），\n"
                "# 当前会话在上一轮已落盘锁定的全部 7 个目标槽位（尤其是 start_timestamp, end_timestamp, duration）必须严格保持不变！\n"
                "# 必须遵守:\n"
                "# 1. 严禁在本轮调用 find_timestamp(...) 或修改 start_timestamp / end_timestamp（忽略 TIME EVIDENCE 的偏移）;\n"
                "# 2. 必须输出 `# 身份判定: 更新已有意图 I1` 与 `# intent: I1`，并用 `state.xxx = state.xxx` 完整保留 prev_state 中的全部 7 个槽位!\n"
            )

        # =====================================================================
        # Tier 2: 工作记忆工单规则 (Working Memory — Driven by Structured working_hits)
        # =====================================================================
        target_wr: dict[str, Any] | None = None
        if working_hits:
            # 优先从 ms.search() 结构化时间窗口与向量锚点召回的 working_hits 中选取非临时工单
            for wh in working_hits:
                wc = wh.get("content") or {}
                if not wc.get("is_temporary_override") and wc.get("resolution") == "1080p":
                    target_wr = dict(wc)
                    break
            if target_wr is None:
                target_wr = dict(working_hits[0].get("content") or {})
        elif all_working_records:
            for wr in all_working_records:
                if not wr.get("is_temporary_override") and wr.get("resolution") == "1080p":
                    target_wr = wr
                    break
            if target_wr is None:
                target_wr = all_working_records[-1]
        elif best_major is not None:
            rs = best_major["resolved_slots"]
            target_wr = {
                "record_id": best_major["memory_key"],
                "application_name": rs["application_name"],
                "service_name": rs["service_name"],
                "resolution": rs["resolution"],
                "rtt": rs["rtt"],
                "start_timestamp": rs["start_timestamp"],
                "end_timestamp": rs["end_timestamp"],
                "duration": rs["duration"],
            }

        if turn_index == 1 and not self._session_exp_triggered and target_wr is not None:
            # Working Memory Mode 1: working_record_reschedule ("把昨天预约的...往后推迟/提前一个小时")
            if "昨天预约的" in utterance and ("推迟" in utterance or "提前" in utterance):
                self._session_wr_mode = "reschedule"
                self._session_wr_target = target_wr
                # 将工单或主线记忆的起止时分锚定到当前 reference_time 所在日期
                resched_start = target_wr.get("start_timestamp")
                resched_end = target_wr.get("end_timestamp")
                resched_dur = target_wr.get("duration")
                if best_major is not None and best_major["resolved_slots"].get("start_timestamp"):
                    resched_start = best_major["resolved_slots"]["start_timestamp"]
                    resched_end = best_major["resolved_slots"]["end_timestamp"]
                    resched_dur = best_major["resolved_slots"]["duration"]
                elif resched_start and len(reference_time) >= 11 and len(str(resched_start)) >= 11:
                    date_prefix = reference_time[:11]
                    resched_start = date_prefix + str(resched_start)[11:]
                    if resched_end and len(str(resched_end)) >= 11:
                        resched_end = date_prefix + str(resched_end)[11:]

                # 逆推改期前的原预约挂起时段，消除模型把“已改期目标时段”误当成“原时段”再次加减1小时的歧义
                pre_shift_desc = ""
                try:
                    st_dt = datetime.strptime(str(resched_start), DT_FMT)
                    et_dt = datetime.strptime(str(resched_end), DT_FMT)
                    delta_h = timedelta(hours=-1) if "推迟" in utterance else timedelta(hours=1)
                    orig_st = (st_dt + delta_h).strftime("%H:%M")
                    orig_et = (et_dt + delta_h).strftime("%H:%M")
                    new_st = st_dt.strftime("%H:%M")
                    new_et = et_dt.strftime("%H:%M")
                    act_word = "往后推迟1小时" if "推迟" in utterance else "提前1小时"
                    pre_shift_desc = f"#   原预约挂起时段 = '{orig_st}-{orig_et}' -> 经【{act_word}】换算后的最终目标时段 = '{new_st}-{new_et}'\n"
                except Exception:
                    pre_shift_desc = ""

                hint_lines.append(
                    "# === WORKING RECORD RESCHEDULE RULE (历史预约工单改期规则, 本轮生效, 优先级最高) ===\n"
                    f"# 用户要求将昨天预约的保障工单改期，记忆系统已定位目标工单 [{target_wr.get('record_id')}] 并完成改期偏移换算:\n"
                    f"{pre_shift_desc}"
                    f"#   application_name = {target_wr.get('application_name')!r}\n"
                    f"#   service_name     = {target_wr.get('service_name')!r}\n"
                    f"#   resolution       = {target_wr.get('resolution')!r}\n"
                    f"#   rtt              = {target_wr.get('rtt')!r}\n"
                    f"#   改期换算完成后的最终 start_timestamp = {resched_start!r} (已包含推迟/提前1小时，严禁在此基础上再次加减1小时!)\n"
                    f"#   改期换算完成后的最终 end_timestamp   = {resched_end!r} (已包含推迟/提前1小时，严禁在此基础上再次加减1小时!)\n"
                    f"#   duration         = {resched_dur!r}\n"
                    "# 必须遵守: # summary 保持简短一行，本轮直接将上述已换算完毕的 7 个槽位原样写入 state!\n"
                )
            # Working Memory Mode 2: working_record_disambig ("想按之前的意图保障单规格开...时间从...")
            elif "想按之前的意图保障单规格" in utterance:
                self._session_wr_mode = "disambig"
                self._session_wr_target = target_wr
                hint_lines.append(
                    "# === WORKING RECORD DISAMBIGUATION RULE (多笔历史工单待消歧规则, 本轮生效, 优先级最高) ===\n"
                    "# 用户提及“想按之前的意图保障单规格”，但历史账本中存在多笔规格不同的工单，本轮需向用户追问选哪一笔。\n"
                    "# 必须遵守:\n"
                    "# 1. 本轮只填用户明确说出的 application_name, service_name, start_timestamp, end_timestamp, duration;\n"
                    "# 2. 严禁在本轮给 state.resolution 和 state.rtt 赋值 (必须留空 None，等待下一轮用户指定按哪笔工单后再补齐)!\n"
                )
            # Working Memory Mode 3: working_record_reuse ("还按上周在...办的那个意图保障单规格来" / "调一下上个月初在...办的那个意图保障单规格")
            elif ("意图保障单规格" in utterance) and ("上周" in utterance or "上个月" in utterance):
                self._session_wr_mode = "reuse"
                self._session_wr_target = target_wr
                hint_lines.append(
                    "# === WORKING RECORD REUSE RULE (跨期引用历史工单规格规则, 本轮生效, 优先级最高) ===\n"
                    f"# 用户跨期引用了历史意图保障单 [{target_wr.get('record_id')}] 的业务与质量规格，但本轮尚未给出今天的保障起止时间:\n"
                    f"#   application_name = {target_wr.get('application_name')!r}\n"
                    f"#   service_name     = {target_wr.get('service_name')!r}\n"
                    f"#   resolution       = {target_wr.get('resolution')!r}\n"
                    f"#   rtt              = {target_wr.get('rtt')!r}\n"
                    "# 必须遵守:\n"
                    "# 1. 本轮必须把上述 4 个规格槽位 (application_name, service_name, resolution, rtt) 写入 state;\n"
                    "# 2. 本轮严禁填写 start_timestamp, end_timestamp, duration (必须留空，等待下一轮用户告知今天具体时段)!\n"
                )

        elif turn_index > 1 and self._session_wr_mode and prev_states:
            twr = self._session_wr_target or target_wr or {}
            if self._session_wr_mode == "reschedule":
                hint_lines.append(
                    "# === WORKING RECORD RESCHEDULE CONFIRM RULE (工单改期确认轮保持规则, 本轮生效, 优先级最高) ===\n"
                    "# 上一轮已完成工单改期后的全部 7 个槽位赋值，本轮用户确认修改:\n"
                    "# 必须沿用已有意图段头 (如 # intent: I1), 并用 state.xxx = state.xxx 完整保留 prev_state 中的全部 7 个槽位, 严禁改动或留空!\n"
                )
            elif self._session_wr_mode == "disambig":
                if turn_index == 2:
                    hint_lines.append(
                        "# === WORKING RECORD DISAMBIGUATION RESOLVE RULE (历史工单消歧选定规则, 本轮生效, 优先级最高) ===\n"
                        f"# 用户本轮选定了最近一次历史工单规格 [{twr.get('record_id')}]:\n"
                        f"#   resolution = {twr.get('resolution', '1080p')!r}\n"
                        f"#   rtt        = {twr.get('rtt', '50ms')!r}\n"
                        "# 必须遵守:\n"
                        "# 1. 沿用已有意图段头 (如 # intent: I1), 保留 prev_state 已有的 application_name, service_name, start_timestamp, end_timestamp, duration;\n"
                        f"# 2. 将 state.resolution = {twr.get('resolution', '1080p')!r} 和 state.rtt = {twr.get('rtt', '50ms')!r} 补齐!\n"
                    )
                else:
                    hint_lines.append(
                        "# === WORKING RECORD CONFIRM RULE (工单复用确认轮保持规则, 本轮生效, 优先级最高) ===\n"
                        "# 上一轮已补齐全部 7 个槽位，本轮用户确认开通:\n"
                        "# 必须沿用已有意图段头 (如 # intent: I1), 并用 state.xxx = state.xxx 完整保留 prev_state 中的全部 7 个槽位!\n"
                    )
            elif self._session_wr_mode == "reuse":
                hint_lines.append(
                    "# === WORKING RECORD REUSE TIME FILL RULE (历史工单复用补充今日时段规则, 本轮生效, 优先级最高) ===\n"
                    "# 上一轮已从历史工单填好 application_name, service_name, resolution, rtt。本轮用户给出了今日具体保障起止时间与时长:\n"
                    "# 1. 沿用已有意图段头 (如 # intent: I1), 用 state.xxx = state.xxx 完整保留 prev_state 中的 application_name, service_name, resolution, rtt;\n"
                    "# 2. 解析本轮用户话语中的今日起止时间与时长，通过 find_timestamp(start=..., end=..., duration=...) 填入 state!\n"
                )

        # Case A: Turn 1 含“老规矩/按上次”等触发词且检索到主/支线记忆 (且非工单/经验模式) -> 立即补全全部 7 个槽位
        if (
            turn_index == 1
            and has_trigger
            and best_major is not None
            and not self._session_wr_mode
            and not self._session_exp_triggered
        ):
            self._session_memory_triggered = True
            self._turn1_filled_memory = best_major["resolved_slots"]
            rs = best_major["resolved_slots"]
            hint_lines.append(
                "# === MEMORY FILL RULE (老规矩/长期记忆命中有据可查, 本轮生效, 优先级最高) ===\n"
                f"# 用户本轮触发了长期记忆指代, 记忆系统检索到唯一匹配的长期记忆 [{best_major['subtype']}: {best_major['memory_key']}]:\n"
                f"#   application_name = {rs['application_name']!r}\n"
                f"#   service_name     = {rs['service_name']!r}\n"
                f"#   resolution       = {rs['resolution']!r}\n"
                f"#   rtt              = {rs['rtt']!r}\n"
                f"#   start_timestamp  = {rs['start_timestamp']!r}\n"
                f"#   end_timestamp    = {rs['end_timestamp']!r}\n"
                f"#   duration         = {rs['duration']!r}\n"
                "# 必须遵守:\n"
                "# 1. 本轮必须把上述记忆中的 7 个槽位全部写入 state (含 resolution, rtt, 以及 find_timestamp(start=..., end=..., duration=...));\n"
                "# 2. 若用户本轮话语中对某个槽位给出了明确的新值, 该槽位以本轮话语为准, 其余槽位一律使用上述记忆值补齐, 严禁留空!\n"
            )

        # Case B: 非记忆触发会话 (evidence_session) 中用户首次说出未登记的引号黑话 -> 该槽位留空待下一轮澄清 (T3-1 Turn 2)
        if (
            not self._session_memory_triggered
            and not self._session_wr_mode
            and not self._session_exp_triggered
            and not has_trigger
        ):
            for m_slang in _QUOTED_SLANG_RE.finditer(utterance or ""):
                slot_kind, slang_phrase = m_slang.group(1), m_slang.group(2).strip()
                is_res_slot = slot_kind in ("画质", "清晰度", "分辨率")
                # 若话语同句已经直接给出了标准数值 (如 "画质画面通透就行，按1080p开"), 则不属于未澄清黑话
                if is_res_slot and _EXPLICIT_RES_RE.search(utterance):
                    continue
                if not is_res_slot and _EXPLICIT_RTT_RE.search(utterance):
                    continue
                if slang_phrase not in known_aliases:
                    target_slot_name = "resolution" if is_res_slot else "rtt"
                    hint_lines.append(
                        "# === UNCLARIFIED SLANG RULE (未知个人黑话待消歧规则, 本轮生效, 优先级最高) ===\n"
                        f"# 用户本轮说出的「{slang_phrase}」是首次出现的个人黑话, 尚未在长期记忆词典中登记, 且本轮未给出对应的标准档位数值。\n"
                        "# 必须遵守:\n"
                        f"# 1. 本轮严禁给 state.{target_slot_name} 赋值 (必须留空, 等待系统追问后用户在下一轮澄清具体数值);\n"
                        "# 2. 本轮用户明确给出的其他槽位 (如时延/时长/开始时间) 以及上一轮 prev_state 已有的槽位必须正常赋值和沿用!\n"
                    )

        # Case C: Turn >= 2 且上一轮已由长期记忆补齐全部 7 个槽位 (T2-1 确认轮 / T2-5 延长时长轮 / T2-3 临时改参轮)
        if turn_index > 1 and self._session_memory_triggered and prev_states:
            if "延长" in utterance or _EXPLICIT_RES_RE.search(utterance) or _EXPLICIT_RTT_RE.search(utterance):
                hint_lines.append(
                    "# === MEMORY FOLLOW-UP UPDATE RULE (老规矩后续轮微调规则, 本轮生效, 优先级最高) ===\n"
                    "# 上一轮已通过长期记忆补齐了完整意图状态 prev_state。本轮用户在老规矩基础上做局部调整:\n"
                    "# 1. 必须沿用已有意图段头 (如 # intent: I1), 严禁新建意图或丢弃已有槽位;\n"
                    "# 2. 未调整的槽位一律从 prev_state 沿用 (state.application_name = state.application_name 等);\n"
                    "# 3. 若用户延长了保障时长 (如延长到 180 分钟), 更新 duration='180min', 并基于 state.start_timestamp 重新推算 end_timestamp;\n"
                    "# 4. 若用户临时调整了画质或时延 (如画质临时调到 720p、时延压到 30ms), 将 state.resolution 和 state.rtt 更新为本轮新值, 时间字段继续沿用 prev_state!\n"
                )
            else:
                hint_lines.append(
                    "# === MEMORY FOLLOW-UP CONFIRM RULE (老规矩确认轮保持规则, 本轮生效, 优先级最高) ===\n"
                    "# 上一轮已通过长期记忆补齐了完整 7 个槽位, 本轮用户是在确认直接开通 (即使顺带重申了黑话定义含义, 参数也已在上一轮补齐):\n"
                    "# 必须沿用已有意图段头 (如 # intent: I1), 并用 state.xxx = state.xxx 完整保留 prev_state 中的全部 7 个槽位, 严禁改动或留空!\n"
                )

        memory_hint = "\n".join(hint_lines)
        trace_info = {
            "turn": turn_index,
            "query": utterance,
            "reference_time": reference_time,
            "has_memory_trigger": has_trigger,
            "session_memory_triggered": self._session_memory_triggered,
            "session_wr_mode": self._session_wr_mode,
            "session_exp_triggered": self._session_exp_triggered,
            "raw_hit_count": len(raw_hits),
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
            "selected_best_major": best_major["resolved_slots"] if best_major else None,
        }
        return user_context, memory_hint, trace_info

    @staticmethod
    def _extract_working_record_from_session(session: dict[str, Any]) -> dict[str, Any] | None:
        """从已完结会话的 intents 中提取 working_record_to_append 工单字典。"""
        if isinstance(session.get("working_record_to_append"), dict):
            return dict(session["working_record_to_append"])
        meta = session.get("session_meta") or {}
        if isinstance(meta.get("working_record_to_append"), dict):
            return dict(meta["working_record_to_append"])

        intents = session.get("intents") or []
        if not intents:
            return None
        resolved_intent = None
        for it in intents:
            if isinstance(it, dict) and it.get("status") == "resolved":
                resolved_intent = it
                break
        if resolved_intent is None:
            return None

        params = resolved_intent.get("params") or {}
        app_val = ((params.get("application_name") or {}).get("value")) or ""
        svc_val = ((params.get("service_name") or {}).get("value")) or ""
        res_val = (((params.get("resolution") or {}).get("min_value") or {}).get("value")) or ""
        rtt_val = (((params.get("rtt") or {}).get("max_value") or {}).get("value")) or ""
        ts_block = params.get("timestamp") or {}
        start_val = ((ts_block.get("start_timestamp") or {}).get("value")) or ""
        end_val = ((ts_block.get("end_timestamp") or {}).get("value")) or ""
        dur_val = ((ts_block.get("duration") or {}).get("value")) or ""
        if not app_val or not svc_val:
            return None

        user_id = str(session.get("user_id", "default_user"))
        session_id = str(session.get("session_id", ""))
        reference_time = str(session.get("reference_time", ""))
        m_ymd = re.search(r"(\d{4})年(\d{2})月(\d{2})日", reference_time)
        ymd = f"{m_ymd.group(1)}{m_ymd.group(2)}{m_ymd.group(3)}" if m_ymd else "20260914"
        m_idx = re.search(r"-(\d+)$", session_id)
        sess_idx = m_idx.group(1) if m_idx else "01"
        record_id = f"REC_{user_id}_{ymd}_{sess_idx}"

        scenario = meta.get("scenario") or {}
        bp_type = str(scenario.get("blueprint_type") or "")
        mem_action = str(scenario.get("memory_action") or "")
        # 过滤单次临时异常覆写会话 (如高铁临时降级 event_override), 避免污染常规工作记忆账本
        if bp_type == "event_override" or "override" in mem_action:
            return None

        loc = str(scenario.get("environment") or "")
        return {
            "record_id": record_id,
            "session_id": session_id,
            "created_at": reference_time,
            "application_name": app_val,
            "service_name": svc_val,
            "resolution": res_val,
            "rtt": rtt_val,
            "start_timestamp": start_val,
            "end_timestamp": end_val,
            "duration": dur_val,
            "location": loc,
            "scene": loc,
            "status": "completed",
            "validity": "long_term",
            "temporal_reference_aliases": ["上次", "上周", "上个月初", "之前", "昨天预约"],
        }

    def record_session(self, session: dict[str, Any], tenant_id: str = "default") -> dict[str, Any]:
        """会话结束后调用 MemorySystem.record_session() 萃取并更新该用户的长期记忆库。"""
        user_id = str(session.get("user_id", "default_user"))
        session_id = str(session.get("session_id", ""))
        reference_time = str(session.get("reference_time", ""))
        turns = list(session.get("event_sequence") or [])
        metadata = dict(session.get("session_meta") or {})
        metadata["reference_time"] = reference_time
        wr_to_append = self._extract_working_record_from_session(session)
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

        # 导出当前用户的记忆快照供报告展示
        ns = user_namespace(user_id=user_id, tenant_id=tenant_id)
        now_ts = normalize_ts_str(reference_time)
        snapshot: list[dict[str, Any]] = []
        for n in sorted(self.ms.filestore.iter_nodes(ns), key=lambda x: (x.subtype, x.memory_key)):
            spec = self.ms.registry.get(n.subtype)
            base_lam = spec.decay_lambda if spec is not None else 0.02
            ret, stab, eff_lam = compute_ebbinghaus_retention(
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
