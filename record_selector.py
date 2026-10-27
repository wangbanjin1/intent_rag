# -*- coding: utf-8 -*-
"""record_selector.py: v27.1 记录指代预判器 (生成前证据注入, 与 time_extractor 同构)。

设计 (docs/plan_record_selector_20260922.md):
  判定树在 prompt 组装前运行, 把"该不该取记录/取哪条/该不该等消歧"三个口径问题
  用确定性规则预判, 以 RECORD EVIDENCE / RECORD RULE 注入。模型保留决策权,
  本模块只提供数据口径结论。严禁在 validate 之后改写模型输出 (v9.0/v10.0 已证伪)。

判定树 (全量扫描 2026-09-23 核验, d6 60 例无约束指代):
  58 例 GT 当轮取 records[0] (最新) —— 走 EVIDENCE 定位注入;
   3 例悬空 (S-000072/140/192, 全部 T3-3 消歧模板: 预选查询式或客服消歧问句之后)
   —— 走 RULE 悬空注入。悬空限定条件由此扫描确定, 不可放宽到非消歧会话。

入口:
  record_evidence(utterance, user_context, agent_reply, in_disambiguation)
    -> RecordEvidence | None
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 指代词 (与 pipeline record_rule 同源)
RECORD_REF_RE = re.compile(r"记录|历史")
RECORD_REF_LOOSE_RE = re.compile(r"上次|之前|最近")
PREV_COREF_RE = re.compile(r"跟上次一样|和上次一样|像上次|照旧|跟之前一样|和之前一样")

# 预选/查询式 (现有 record_rule 排除表; 命中 = 字段悬空待消歧的强信号)
PRESELECT_RE = re.compile(r"选中|办过|参考|查.{0,18}(?:记录|历史)|最终选中的?")

# 客服消歧问句信号 (agent 侧)
AGENT_DISAMB_RE = re.compile(
    r"哪一条|哪个|选哪|按哪|无法唯一|无法确定|您.{0,6}(选|按)|还是")

# 偏好性指代 ("最好是X那条" — 用户偏好≠最终选择, 客服仍需确认)
PREFERENTIAL_RE = re.compile(r"(最好|倾向|想要|优先)[^，。；]{0,8}(是|那)?")


@dataclass
class RecordEvidence:
    mode: str                    # "unique" / "latest" / "defer"
    constraint: str              # 用户约束原文 (空=无)
    matched: list[dict] = field(default_factory=list)   # 过滤命中的 records
    target_record: dict | None = None                   # unique/latest 的目标
    reason: str = ""             # 注入文案用一句话


def _extract_filters(utterance: str,
                     app_names: tuple[str, ...],
                     svc_names: tuple[str, ...]) -> dict:
    """从话语提取记录筛选条件 (app/service/日期)。复用 v24.1 紧邻判定思想。"""
    f: dict = {}
    # v28.1 修正: break 移入紧邻命中分支——原版"话语含该 app 即 break"导致
    # 双 app 话语 (1840: "办芒果TV…查淘宝主播…的记录") 第一个 app (新保障目标,
    # 无紧邻记录词) 就退出循环, 真正的记录筛选 app (淘宝主播) 永远测不到 →
    # 候选并集缺约束 → 消歧候选错/漏。
    # 紧邻窗 6->14 (对齐 svc 窗, "淘宝主播7月7号完成的记录"间隔 7 字)。
    for name in sorted(app_names, key=len, reverse=True):
        if name not in utterance:
            continue
        # 排除"本轮新保障目标 app"的误判: 仅当 app 与记录词紧邻/共现于指代短语
        if re.search(rf"{re.escape(name)}[^，。；]{{0,14}}(记录|历史|那条|那次)", utterance) \
           or re.search(rf"(上次|之前|历史|最近)[^，。；]{{0,4}}的?{re.escape(name)}", utterance):
            f["application_name"] = name
            break
    for name in svc_names:
        # v28.2: svc 命中前先剥已命中 app 名——"网易云游戏7月4号完成的记录"的"云游戏"
        # 是 app 子串, 不是用户说的服务; 误判成 svc 约束会让并集混入无关候选
        # (524 型: service=云游戏 最近命中=咪咕快游 混入, 序号错位致字段来源错位)。
        _u = utterance
        if f.get("application_name"):
            _u = _u.replace(f["application_name"], "")
        if re.search(rf"{re.escape(name)}[^，。；]{{0,14}}(记录|历史|那条|那次)", _u) \
           or re.search(rf"(上次|之前|历史|最近)[^，。；]{{0,8}}的?{re.escape(name)}", _u):
            f["service_name"] = name
            break
    m = re.search(r"(\d{1,2})月(\d{1,2})[号日][^，。；]{0,10}?(完成的那|的那条|那次|记录)", utterance)
    if m:
        # 记录 completed_at 为零填充格式 ("2026年07月01日..."), 生成两种形态以兼容匹配
        f["date"] = f"{int(m.group(1)):02d}月{int(m.group(2)):02d}"
    return f


def _filter_records(records: list[dict], filters: dict) -> list[dict]:
    out = records
    if filters.get("application_name"):
        out = [r for r in out if r.get("application_name") == filters["application_name"]]
    if filters.get("service_name"):
        out = [r for r in out if r.get("service_name") == filters["service_name"]]
    if filters.get("date"):
        out = [r for r in out if filters["date"] in (r.get("completed_at") or "")]
    return out


def _fmt_record(r: dict) -> str:
    rtt = (r.get("rtt") or {}).get("max_value") if isinstance(r.get("rtt"), dict) else r.get("rtt")
    return (f"application_name={r.get('application_name')}, service_name={r.get('service_name')}, "
            f"rtt={rtt}, completed_at={r.get('completed_at')}")


def record_evidence(utterance: str,
                    user_context: dict | None,
                    agent_reply: str | None = None,
                    disambiguation_active: bool = False) -> RecordEvidence | None:
    """记录指代预判主入口。无指代/沿用式/消歧选择轮返回 None。

    悬空 (defer) 判定 (2026-09-23 d6 全量扫描核验, 3 例全中 58 例排除):
      话语含预选查询式 (选中/办过/查…记录/最终选中) 或 客服上轮为消歧问句,
      且 (无筛选条件或多筛选命中), 即无法唯一定位 → defer。
      非消歧会话绝不 defer (58 例 GT 当轮取最新)。
    """
    utt = utterance or ""
    if not (RECORD_REF_RE.search(utt) or RECORD_REF_LOOSE_RE.search(utt)):
        return None
    if PREV_COREF_RE.search(utt):
        return None
    records = (user_context or {}).get("recent_records") or []
    if not records:
        return None

    from schema import KNOWN_APPLICATIONS
    svc_names = ("视频通话", "开直播", "看直播", "会议", "云游戏", "视频")
    filters = _extract_filters(utt, tuple(KNOWN_APPLICATIONS), svc_names)
    matched = _filter_records(records, filters)

    # --- 悬空判定 (2026-09-23 全量扫描校准: 悬空仅发生在消歧类会话) ---
    preselect = bool(PRESELECT_RE.search(utt))
    agent_asked = bool(agent_reply and AGENT_DISAMB_RE.search(agent_reply))
    # 用户已做出选择的选择轮 (序数/指代resolve短语) → disambiguation_rule 接管, 本模块退出。
    # 注意: '按最近一条记录'单独出现是无约束指代而非选择, 不得命中 choice 短语。
    # v27.3-d930: 去掉裸"就按"——t1 复合句"…按最后选中的记录来，其他就按我这次说的"
    # (d930 S-000844 型) 尾部"就按"误杀整轮, t1 应 defer 列候选。
    choice_turn = bool(re.search(r"^.{0,6}(第[一二三四五六七八九十\d]+\s*[条个]|选第|不选第)", utt)
                       or re.search(r"第[一二三四五六七八九十\d]+\s*[条个](吧|就行|来|。|$)", utt)
                       or re.search(r"^(就|那)按第", utt))
    if choice_turn:
        return None
    if disambiguation_active:
        # --- 候选组装 (2026-09-28, S-000089/S-000140) ---
        # AND 交集在用户约束互相冲突时 (app/svc key 与日期/"最近一条"指向不同记录)
        # 只给 0/1 条 -> pipeline 候选注入门槛 len>=2 不满足 -> t1 无候选明细,
        # 模型自行落地单条, 后续消歧无解。
        # 口径: 交集 >=2 维持现状 (双约束一致多命中, 如 S-000246); 交集 <=1 时
        # 按"每约束各自命中的最近一条"取并集 (loose "最近/上次"信号并入 records[0]),
        # 按 records 降序排列 (= 数据集 evidence.candidate_record_ids 口径)。
        cand = matched
        if filters and len(matched) <= 1:
            picked: set[int] = set()
            for _k in ("application_name", "service_name"):
                if filters.get(_k):
                    for _i, _r in enumerate(records):
                        if _r.get(_k) == filters[_k]:
                            picked.add(_i)
                            break
            if filters.get("date"):
                for _i, _r in enumerate(records):
                    if filters["date"] in (_r.get("completed_at") or ""):
                        picked.add(_i)
                        break
            if RECORD_REF_LOOSE_RE.search(utt):
                picked.add(0)
            cand = [r for _i, r in enumerate(records) if _i in picked]
        # 消歧会话: 无法唯一定位(含预选查询式/多命中/零命中) → 悬空待客服消歧。
        # 含偏好性指代 ("最好是X那条") 也视为非定位 (用户偏好≠最终选择, 客服仍需确认)。
        preferential = bool(PREFERENTIAL_RE.search(utt))
        ambiguous = preselect or agent_asked or preferential or len(cand) != 1
        if ambiguous:
            conflict = (len(cand) >= 2 and len(matched) < len(cand))
            why = ("约束指向不同记录" if conflict else
                   "预选查询式" if preselect else
                   "客服消歧问句后无筛选" if agent_asked else
                   "偏好性指代未最终确认" if preferential else
                   f"约束命中 {len(cand)} 条")
            return RecordEvidence(mode="defer", constraint=str(filters), matched=cand,
                                  reason=f"记录指代无法唯一定位（{why}）")
        # 唯一命中且非选择轮: T3-3 内也允许直接定位
        return RecordEvidence(mode="unique", constraint=str(filters), matched=cand,
                              target_record=cand[0], reason="约束定位唯一")

    # --- 非消歧会话: 口径一律当轮取值 ---
    if len(matched) == 1:
        return RecordEvidence(mode="unique", constraint=str(filters), matched=matched,
                              target_record=matched[0], reason="约束定位唯一")
    if len(matched) == 0:
        # 筛选词提取偏差导致零命中 (如跨意图句的 svc 误过滤): 回退全部最新, 不悬空
        return RecordEvidence(mode="latest", constraint="无(条件未命中, 回退全量)", matched=records,
                              target_record=records[0],
                              reason="条件未命中记录, 按口径取最近一条 records[0]")
    return RecordEvidence(mode="latest", constraint=str(filters), matched=matched,
                          target_record=matched[0], reason="约束命中多条, 取最近一条")


def render_hint(ev: RecordEvidence) -> str | None:
    """组装 prompt 注入段。仅 defer 渲染 (v27.1 run2 教训: unique/latest 的 EVIDENCE 注入
    扰动 record_rule 已验证的行为面, 13 破窗——定位类回归 record_rule 原文案, 其 +4.97pp
    收益已实证; 本模块只负责 record_rule 管不了的悬空口径)。"""
    if ev is None:
        return None
    if ev.mode == "defer":
        # 2026-09-28 S-000089/140: 候选列举场景 (matched>=2, 同轮有 CANDIDATE MODE 注入)
        # 下"禁止从 records 取值"会与"candidate 段赋值记录 res/rtt"表面冲突, 模型倾向
        # 只遵从前者不列候选; 改为"禁止落地到保障意图, candidate 段除外"消歧表述。
        if len(ev.matched) >= 2:
            return (
                "# === RECORD RULE (记录指代存在歧义, 本轮生效, 优先级最高) ===\n"
                f"# 系统核验: {ev.reason}。\n"
                "# 按数据口径: 被指代的字段(如 rtt)本轮【留空】——不落地到保障意图, 等待用户选择后再取值;\n"
                "# (# candidate: 候选段内的 res/rtt 赋值是列举动作, 不受此限——须按 CANDIDATE MODE 全部列出);\n"
                "# 禁止提前把任何 records 的字段写进保障意图——本条覆盖系统提示中\"按指代条件过滤后取最近\"的一般规则;\n"
                "# # summary 必须写明\"记录字段悬空待消歧\"。\n")
        return (
            "# === RECORD RULE (记录指代存在歧义, 本轮生效, 优先级最高) ===\n"
            f"# 系统核验: {ev.reason}。\n"
            "# 按数据口径: 被指代的字段(如 rtt)本轮【留空】, 等待客服列出候选、用户选择后再取值;\n"
            "# 禁止提前从任何 records 取值——本条覆盖系统提示中\"按指代条件过滤后取最近\"的一般规则;\n"
            "# # summary 必须写明\"记录字段悬空待消歧\"。\n")
    return None
