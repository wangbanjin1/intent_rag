"""report_metrics.py — 错误评估数据加载、GT 来源溯源与错误分类统计。

职责:
  1. 定义评估槽位与一级/二级错误分类元数据 (EVAL_SLOTS, CAT_META, TAG_META)
  2. 加载评估产物、数据集 session、用户画像/记录与噪声清单 (load_eval, load_sessions, load_users, load_user_profiles, load_noise)
  3. 构建 GT 来源映射与逐轮累积状态 (build_source_map, latest_source, _cum_gt_by_turn, _pred_by_turn)
  4. 执行 turn 级与 session 级错误归类与统计聚合 (categorize, scenario_tag, aggregate_eval_metrics)
"""
from __future__ import annotations

import collections
import copy
import glob
import json
import os
import re
from typing import Any, Iterator

import gt_build as _gt

EVAL_SLOTS = [
    "application_name", "service_name", "resolution", "rtt",
    "start_timestamp", "end_timestamp", "duration",
]

# 嵌套参数路径 -> 扁平评估字段（slot_updates 的 params 是嵌套 dict）
NESTED_TO_FLAT = {
    "application_name": "application_name",
    "service_name": "service_name",
    "resolution.min_value": "resolution",
    "resolution": "resolution",
    "rtt.max_value": "rtt",
    "rtt": "rtt",
    "timestamp.start_timestamp": "start_timestamp",
    "timestamp.end_timestamp": "end_timestamp",
    "duration": "duration",
}

TIME_FIELDS = {"start_timestamp", "end_timestamp", "duration"}
APPSVC_FIELDS = {"application_name", "service_name"}

CAT_ORDER = [
    "missed_intent", "cancel_wrong", "record_wrong", "memory_wrong",
    "time_wrong", "appsvc_wrong", "value_wrong", "intent_set_mismatch",
]

TURN_CATS = [
    "record_wrong", "memory_wrong", "missed_intent", "time_wrong",
    "appsvc_wrong", "value_wrong",
]

CAT_META: dict[str, dict[str, str]] = {
    "cancel_wrong": {
        "title": "取消未执行（方案 b 口径）",
        "color": "#d35400",
        "desc": (
            "用户在该轮明确取消某意图（GT 要求移除），但模型输出中该意图仍存在。"
            "取消计分口径: 取消轮 GT 记为'应为空'，模型正确移除算命中。"
        ),
    },
    "record_wrong": {
        "title": "record 提取错误",
        "color": "#e74c3c",
        "desc": (
            "用户用“按上次/按某次记录/平时”等指代表述时，模型未从用户画像 records 中取出正确字段值"
            "（取错 record、未取值或指代消解失败）。GT 中这些字段 source_type=Record。"
            "已知瓶颈：v5.0/v9.0 两次证明后处理无法修复，须生成侧解决（v11 选择链方向）。"
        ),
    },
    "memory_wrong": {
        "title": "画像（Memory）提取错误",
        "color": "#e67e22",
        "desc": "字段 GT 来源为用户画像 profile（source_type=Memory），模型未正确引用画像信息。",
    },
    "missed_intent": {
        "title": "意图漏识别",
        "color": "#8e44ad",
        "desc": "模型该轮没有输出 GT 中存在的意图（输出为空或意图键未命中），prev_state 兜底也未覆盖。",
    },
    "time_wrong": {
        "title": "时间推算错误",
        "color": "#2980b9",
        "desc": "start_timestamp/end_timestamp/duration 三字段知二推一或“明天/跨日”等相对时间推算错误。",
    },
    "appsvc_wrong": {
        "title": "应用/服务错配",
        "color": "#16a085",
        "desc": "application_name/service_name 与 GT 不一致，多为多意图场景张冠李戴或域外 app 误判。",
    },
    "value_wrong": {
        "title": "其他字段值错误",
        "color": "#7f8c8d",
        "desc": "resolution/rtt 等取值与 GT 不一致，且 GT 来源非 Record/Memory（多为本轮表述理解错误）。",
    },
    "intent_set_mismatch": {
        "title": "意图集合不一致（session 级）",
        "color": "#c0392b",
        "desc": (
            "session 最终意图集合与 GT 不严格全等：missing=漏识别的意图，extra=多识别/幻觉意图"
            "（含取消后未移除）。session 级判错，turn 级明细见对应样本。"
        ),
    },
}

FIELD_LABEL = {
    "application_name": "应用",
    "service_name": "服务",
    "resolution": "清晰度",
    "rtt": "时延",
    "start_timestamp": "开始时间",
    "end_timestamp": "结束时间",
    "duration": "时长",
}

TAG_ORDER = ["FMT", "SR", "CU", "COR", "PER", "REA", "CTX", "XFER", "INT", "GEN"]

TAG_META: dict[str, dict[str, str]] = {
    "FMT": {
        "name": "形式差异（疑假错误）",
        "color": "#95a5a6",
        "desc": (
            "归一化（大小写/空格/数值等价）后 GT 与预测一致——疑似评估口径造成的假错误，"
            "非模型能力问题（A2 宽松匹配+ITN 思想）。占比高说明该补评估归一化而非改模型。"
        ),
    },
    "SR": {
        "name": "取消/槽位重置",
        "color": "#d35400",
        "desc": "A1 SR 类：用户取消/重置意图或槽位，系统需移除已填内容。",
    },
    "CU": {
        "name": "消歧/会话理解",
        "color": "#8e44ad",
        "desc": (
            "A1 CU 类：回应助手提议、从候选中选择（“第二条吧”）。T3-3 属此类。"
            "A1 实证高能力模型此类最强（89.4%）——此类大量出错说明缺的是信息（看不到 agent 轮候选），"
            "不是模型能力，修补方向=把消歧候选注入 prompt。"
        ),
    },
    "COR": {
        "name": "改口/修正覆盖",
        "color": "#c0392b",
        "desc": (
            "A1 COR 类：用户中途改值，GT 本轮覆盖了早前轮的旧值。模型常残留旧值或丢字段"
            "（B5 实证 LLM 偏向先提旧值）。"
        ),
    },
    "PER": {
        "name": "画像/记录引用",
        "color": "#e74c3c",
        "desc": (
            "A1 PER 类：值来自用户画像 records/profile（“按上次记录”）。"
            "A1 实证此类是全模型最弱项（55.9%），属能力性短板，prompt 只能缓解。"
        ),
    },
    "REA": {
        "name": "推理/时间推算",
        "color": "#2980b9",
        "desc": (
            "A1 REA 类：时间跨日/知二推一等需多步推理的字段。"
            "对 prompt 变长最敏感（v16.0 实证），prompt 增量需盯此类回归。"
        ),
    },
    "CTX": {
        "name": "状态沿用失败",
        "color": "#16a085",
        "desc": (
            "本轮无新信息（GT 沿用早前轮的值），但模型把 prev_state 里的正确值丢了/改了。"
            "E1 实证的状态跟踪短板：误差累积型错误，agent_context/status_prompt 对症。"
        ),
    },
    "XFER": {
        "name": "多意图串扰",
        "color": "#f39c12",
        "desc": "多意图场景张冠李戴：A 意图的值填到 B 意图。E2 意图链（显式 # intent: 边界）对症。",
    },
    "INT": {
        "name": "意图识别",
        "color": "#5d6d7e",
        "desc": (
            "意图漏识别/误建新意图（且非消歧场景）。A3 实证意图层远易于槽位层，"
            "此类占比高通常指向 prompt/示例未覆盖该表达模式。"
        ),
    },
    "GEN": {
        "name": "本轮表述理解",
        "color": "#7f8c8d",
        "desc": "兜底类：值错且信息源自本轮话语本身（非画像/非沿用/非改口），属当轮语言理解错误。",
    },
}

_SEL_RE = re.compile(
    r"第[一二三四五六七八九十\d]+\s*[条个项]|就[它那这第]|刚才(那个|说的)|还是(那个|刚才)|"
    r"[那这][条个]吧|都行|随便|听你的"
)

REC_MATCH_FIELDS = ["application_name", "service_name", "resolution", "rtt"]


def load_eval(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_sessions(dataset_dir: str) -> dict[str, dict[str, Any]]:
    """dataset 目录下所有 sessions.* -> {session_id: session}。"""
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(glob.glob(os.path.join(dataset_dir, "sessions.*"))):
        if "users" in os.path.basename(f):
            continue
        with open(f, encoding="utf-8") as fh:
            if f.endswith(".json"):
                for s in json.load(fh):
                    out[s["session_id"]] = s
            else:
                for line in fh:
                    line = line.strip()
                    if line:
                        s = json.loads(line)
                        out[s["session_id"]] = s
    return out


def load_users(dataset_dir: str) -> dict[str, list[dict[str, Any]]]:
    """{user_id: [record, ...]} 按 completed_at 倒序。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(glob.glob(os.path.join(dataset_dir, "users*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                u = json.loads(line)
                recs = sorted(
                    u.get("records") or [],
                    key=lambda r: r.get("completed_at", ""),
                    reverse=True,
                )
                out[u.get("user_id")] = recs
    return out


def load_user_profiles(dataset_dir: str) -> dict[str, dict[str, Any]]:
    """{user_id: {画像key: value}}（users*.jsonl 的 profile 段）。"""
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(glob.glob(os.path.join(dataset_dir, "users*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                u = json.loads(line)
                out[u.get("user_id")] = {
                    p.get("key"): p.get("value") for p in (u.get("profile") or [])
                }
    return out


def load_noise(path: str) -> dict[str, list[str]]:
    """加载 find_noise.py 的噪声清单 {session_id: [原因]}，无文件返回空。"""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {sid: d.get("reasons", {}).get(sid, []) for sid in d.get("session_ids", [])}


def _iter_leaf_params(params: dict[str, Any], prefix: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    """递归遍历嵌套 params，yield (点路径, 含 value/source_type 的叶子 dict)。"""
    for k, v in params.items():
        if not isinstance(v, dict):
            continue
        path = f"{prefix}.{k}" if prefix else k
        if "value" in v:
            yield path, v
        else:
            yield from _iter_leaf_params(v, path)


def _walk_gt_leaves(params: dict[str, Any], prefix: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    """遍历 slot_updates 的嵌套 params，产出 (字段路径, 叶子dict含value/source_type/source_ref)。"""
    for k, v in (params or {}).items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            if "value" in v and not isinstance(v.get("value"), dict):
                yield path, v
            else:
                yield from _walk_gt_leaves(v, path)
        else:
            yield path, {"value": v}


def build_source_map(
    session: dict[str, Any], mi_on: bool = False
) -> dict[str, dict[str, list[tuple[int, str, str]]]]:
    """session -> {intent_key: {flat_field: [(turn, source_type, source_ref), ...]}} 按 turn 升序。"""
    id2key: dict[Any, str] = {}
    base_cnt: collections.Counter[str] = collections.Counter()
    for it in session.get("intents") or []:
        p = it.get("params") or {}
        app = (p.get("application_name") or {}).get("value")
        svc = (p.get("service_name") or {}).get("value")
        if app:
            k = f"{app}/{svc}"
            if mi_on:
                base_cnt[k] += 1
                if base_cnt[k] > 1:
                    k = f"{k}#{base_cnt[k]}"
            id2key[it.get("intent_id")] = k

    out: dict[str, dict[str, list[tuple[int, str, str]]]] = {}
    for su in session.get("slot_updates") or []:
        key = id2key.get(su.get("intent_id"))
        if not key:
            continue
        fmap = out.setdefault(key, {})
        for tu in su.get("turn_updates") or []:
            turn = tu.get("turn", 0)
            for path, info in _iter_leaf_params(tu.get("params") or {}):
                flat = NESTED_TO_FLAT.get(path)
                if flat:
                    fmap.setdefault(flat, []).append(
                        (turn, info.get("source_type", ""), info.get("source_ref", ""))
                    )
    for fmap in out.values():
        for lst in fmap.values():
            lst.sort()
    return out


def latest_source(
    src_map: dict[str, Any], intent_key: str, field: str, turn: int
) -> tuple[str, str] | None:
    """截至 turn，该字段最近一次 GT 更新的 (source_type, source_ref)；无则 None。"""
    lst = (src_map.get(intent_key) or {}).get(field) or []
    hit = None
    for t, st, ref in lst:
        if t <= turn:
            hit = (st, ref)
    return hit


def diff_fields(gt: dict[str, Any], pred: dict[str, Any]) -> list[str]:
    return [s for s in EVAL_SLOTS if (gt.get(s) or None) != (pred.get(s) or None)]


def categorize(d: dict[str, Any], src_map: dict[str, Any]) -> tuple[str, list[str]]:
    """对一个失败 turn case 归类，返回 (category, diff_field_list)。"""
    if d.get("cancel_case"):
        return "cancel_wrong", []
    if d.get("missed_intent"):
        return "missed_intent", diff_fields(d["gt"], d["pred"])
    diffs = diff_fields(d["gt"], d["pred"])
    k, turn = d["intent_key"], d["turn"]
    for f in diffs:
        src = latest_source(src_map, k, f, turn)
        if src and src[0] == "Record":
            return "record_wrong", diffs
    for f in diffs:
        src = latest_source(src_map, k, f, turn)
        if src and src[0] == "Memory":
            return "memory_wrong", diffs
    if any(f in TIME_FIELDS for f in diffs):
        return "time_wrong", diffs
    if any(f in APPSVC_FIELDS for f in diffs):
        return "appsvc_wrong", diffs
    return "value_wrong", diffs


def near_keys(gt_app: Any, gt_svc: Any, pred_intents: dict[str, Any] | None) -> list[str]:
    """missed_intent 的近邻检测: 模型(最终态)输出里与 GT 意图同 app 或同 svc 的 key。"""
    out = []
    for k, v in (pred_intents or {}).items():
        pa, ps = (v or {}).get("application_name"), (v or {}).get("service_name")
        if gt_app and pa == gt_app and ps != gt_svc:
            out.append(f"{k}（同应用异服务）")
        elif gt_svc and ps == gt_svc and pa != gt_app:
            out.append(f"{k}（{'空应用头' if not pa else '异应用同服务'}）")
    return out


def dup_key_sessions(results: list[dict[str, Any]], sessions: dict[str, dict[str, Any]]) -> set[str]:
    """v26.3 P2: 检测 GT 侧同 app/svc 双意图 session (dict 塌缩口径受损)。"""
    out: set[str] = set()
    for r in results:
        s = sessions.get(r["session_id"])
        if not s:
            continue
        pair_cnt: collections.Counter[tuple[str, str]] = collections.Counter()
        for su in s.get("slot_updates") or []:
            app = svc = None
            for tu in su.get("turn_updates") or []:
                p = tu.get("params") or {}
                if "application_name" in p and app is None:
                    app = (p["application_name"] or {}).get("value")
                if "service_name" in p and svc is None:
                    svc = (p["service_name"] or {}).get("value")
            if app and svc:
                pair_cnt[(app, svc)] += 1
        if any(n > 1 for n in pair_cnt.values()):
            out.add(r["session_id"])
    return out


def _norm_val(v: Any) -> Any:
    """宽松归一：大小写/空格无关 + 数值等价。"""
    if v is None:
        return None
    s = re.sub(r"\s+", "", str(v)).lower()
    try:
        return float(s)
    except ValueError:
        return s


def _turn_utterance(sraw: dict[str, Any], turn: int) -> str:
    for ev in sraw.get("event_sequence") or []:
        if ev.get("turn") == turn:
            return (ev.get("user") or {}).get("utterance", "") or ""
    return ""


def scenario_tag(
    cat: str,
    d: dict[str, Any],
    diffs: list[str],
    src_map: dict[str, Any],
    sraw: dict[str, Any],
    template: str,
) -> str:
    """二级场景标签：判定错误的原因维度。优先级自上而下，单标签。"""
    k, turn = d["intent_key"], d["turn"]

    if cat not in ("missed_intent", "cancel_wrong") and diffs:
        if all(_norm_val(d["gt"].get(f)) == _norm_val(d["pred"].get(f)) for f in diffs):
            return "FMT"

    if cat == "cancel_wrong":
        return "SR"

    utt = _turn_utterance(sraw, turn)
    if template == "T3-3" or (utt and len(utt) <= 30 and _SEL_RE.search(utt)):
        return "CU"

    for f in diffs:
        hist = (src_map.get(k) or {}).get(f) or []
        turns = {t for t, _, _ in hist}
        if turn in turns and any(t < turn for t in turns):
            return "COR"

    if cat in ("record_wrong", "memory_wrong"):
        return "PER"

    if cat == "time_wrong":
        return "REA"

    if diffs and cat != "missed_intent":
        def _has_update_this_turn(f_name: str) -> bool:
            return any(t == turn for t, _, _ in ((src_map.get(k) or {}).get(f_name) or []))

        if not any(_has_update_this_turn(f) for f in diffs):
            return "CTX"

    if cat == "appsvc_wrong":
        return "XFER"

    if cat == "missed_intent":
        return "INT"

    return "GEN"


def _rec_flat(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "application_name": rec.get("application_name"),
        "service_name": rec.get("service_name"),
        "resolution": (rec.get("resolution") or {}).get("min_value"),
        "rtt": (rec.get("rtt") or {}).get("max_value"),
        "completed_at": rec.get("completed_at"),
    }


def _rec_brief(rec: dict[str, Any], idx: int) -> str:
    f = _rec_flat(rec)
    return (
        f"第{idx + 1}条 ({rec.get('record_id')}): "
        f"{f['application_name']}/{f['service_name']} "
        f"清晰度={f['resolution']} 时延={f['rtt']} 完成于={f['completed_at']}"
    )


def _cum_gt_by_turn(session: dict[str, Any], mi_on: bool = False) -> dict[int, dict[str, dict[str, Any]]]:
    """每轮截止的 GT 全量累积态 {turn: {intent_key: flat}} (v25.1 gt_derive 口径)。"""
    acc: dict[str, tuple[str, dict[str, Any]]] = {}
    out: dict[int, dict[str, dict[str, Any]]] = {}
    for g in _gt.turn_gt(session, mi_on=mi_on):
        t = g["turn"]
        iid = g.get("intent_id") or g["intent_key"]
        flat = g["gt_flat"]
        if flat == _gt.CANCEL_GT:
            acc[iid] = (g["intent_key"], {"__cancelled__": True})
        else:
            if isinstance(flat, dict) and _gt.CORRECT_MARK in flat:
                real = dict(flat["__gt__"])
                real["__exempt__"] = "、".join(FIELD_LABEL.get(f, f) for f in flat[_gt.CORRECT_MARK])
                flat = real
            acc[iid] = (g["intent_key"], flat)
        out[t] = copy.deepcopy({k: v for k, v in acc.values()})
    for t in sorted(out):
        for k in [k for k, v in out[t].items() if isinstance(v, dict) and v.get("__cancelled__")]:
            for tt in sorted(out):
                if tt > t:
                    out[tt].pop(k, None)
    return out


def _pred_by_turn(r: dict[str, Any]) -> dict[int, dict[str, dict[str, Any]]]:
    """每轮截止的模型全量累积态 {turn: {intent_key: flat}}。"""
    if r.get("turn_outputs"):
        return {p["turn"]: (p.get("preds") or {}) for p in r["turn_outputs"]}
    acc: dict[str, dict[str, Any]] = {}
    out: dict[int, dict[str, dict[str, Any]]] = {}
    for td in r.get("turn_detail") or []:
        t = td["turn"]
        pred = td.get("pred") or {}
        if td.get("cancel_case"):
            acc.pop(td["intent_key"], None)
        elif pred:
            acc[td["intent_key"]] = pred
        out[t] = copy.deepcopy(acc)
    return out


def _collect_session_turn_errors(
    r: dict[str, Any],
    session: dict[str, Any],
    smap: dict[str, Any],
    cases: dict[str, list[dict[str, Any]]],
    tag_counter: collections.Counter[str],
    tag_x_cat: dict[str, collections.Counter[str]],
) -> collections.Counter[str]:
    """统计单条 session 的 turn 级错误并更新聚合容器。"""
    sess_tag_c: collections.Counter[str] = collections.Counter()
    for d in r["turn_detail"]:
        if d["match"]:
            continue
        cat, diffs = categorize(d, smap)
        tag = scenario_tag(cat, d, diffs, smap, session, r.get("template", ""))
        tag_counter[tag] += 1
        tag_x_cat[tag][cat] += 1
        sess_tag_c[tag] += 1
        nk = []
        if cat == "missed_intent":
            nk = near_keys(
                (d.get("gt") or {}).get("application_name"),
                (d.get("gt") or {}).get("service_name"),
                r.get("pred_intents"),
            )
        cases[cat].append(
            {
                "session": r,
                "detail": d,
                "diffs": diffs,
                "session_raw": session,
                "tag": tag,
                "near": nk,
            }
        )
    return sess_tag_c


def aggregate_eval_metrics(
    results: list[dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
    src_maps: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """聚合 turn 级与 session 级错误样本、二级标签分布与模板统计。"""
    cases: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    tag_counter: collections.Counter[str] = collections.Counter()
    tag_x_cat: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    sess_tag_counter: collections.Counter[str] = collections.Counter()
    sess_set_mismatch: list[dict[str, Any]] = []
    sess_field_wrong: list[dict[str, Any]] = []

    n_sess = len(results)
    n_sess_match = sum(1 for r in results if r["session_match"])
    n_turn = sum(r["turn_total"] for r in results)
    n_turn_match = sum(r["turn_match"] for r in results)
    dupkeys = dup_key_sessions(results, sessions)

    for r in results:
        sid = r["session_id"]
        session = sessions.get(sid) or {}
        sess_tag_c = _collect_session_turn_errors(r, session, src_maps[sid], cases, tag_counter, tag_x_cat)
        if not r["session_match"]:
            et = r.get("session_error_type", "")
            primary = sess_tag_c.most_common(1)[0][0] if sess_tag_c else "INT"
            entry = {
                "session": r,
                "session_raw": session,
                "tag": primary,
                "tag_dist": dict(sess_tag_c),
            }
            sess_tag_counter[primary] += 1
            if et.startswith("intent_set_mismatch"):
                sess_set_mismatch.append(entry)
            else:
                sess_field_wrong.append(entry)

    by_tpl: collections.Counter[str] = collections.Counter()
    by_tpl_m: collections.Counter[str] = collections.Counter()
    by_tpl_tm: collections.Counter[str] = collections.Counter()
    by_tpl_tt: collections.Counter[str] = collections.Counter()
    for r in results:
        by_tpl[r["template"]] += 1
        by_tpl_m[r["template"]] += int(r["session_match"])
        by_tpl_tm[r["template"]] += r["turn_match"]
        by_tpl_tt[r["template"]] += r["turn_total"]

    return {
        "cases": cases,
        "tag_counter": tag_counter,
        "tag_x_cat": tag_x_cat,
        "sess_tag_counter": sess_tag_counter,
        "sess_set_mismatch": sess_set_mismatch,
        "sess_field_wrong": sess_field_wrong,
        "n_sess": n_sess,
        "n_sess_match": n_sess_match,
        "n_turn": n_turn,
        "n_turn_match": n_turn_match,
        "dupkeys": dupkeys,
        "by_tpl": by_tpl,
        "by_tpl_m": by_tpl_m,
        "by_tpl_tm": by_tpl_tm,
        "by_tpl_tt": by_tpl_tt,
    }
