# 全流程说明：意图状态机（Collecting / Ready / Confirmed / Cancelled）

> 版本：v13.0（2026-08-24）初稿；**v26.2b（2026-09-08）更新主流程**——生成协议从 `# intent: app/svc` 内容头切换为 intent-id 协议（`# intent: I1`/`new` + 身份判定），harness 增加 new 归并校验 / cancel 名字化 / 段去重；**v27.0（2026-09-20）新增时间证据支线**——pipeline `_build_time_hint` 在 prompt 组装前注入 TIME ANCHOR/EVIDENCE/四条 TIME RULE（time_extractor.py 预解析，d6 最佳 92.74%/93.54%/状态机 86.08%），主流程本身不变；**v27.3（2026-09-28）新增候选模式支线**（§1.3）——d6 四跑定案最佳 97.86%/97.88%/状态机 91.39%；**v27.4→v28.4（2026-10-12~21,d930 泛化性攻坚八轮）候选链路语义任务全部 LLM 化**：LLM judge 三枚举（action/pick/scope 语义判别）+ 候选列表属性匹配（resolve_listing,规则快路径→LLM 兜底）+ 列举收回 harness 直驱 + 路线乙身份剥离（1c 退役）+ TIME RULE 组装分叉 + 未定词槽位限定三层判定（详见 §1.3 与 CHANGELOG v27.4~v28.4）。d930 泛化性定案（444×2 互斥子集剔噪交叉验证合并 871 抽样）：q38 **94.60%/95.12%**（单子集 94.95/94.25）,反超 plus（92.20%）,平替三次复现。
> 本文档说明"意图状态跟踪"在 B1 RefPyDST 主线上的完整流程：
> 从一轮用户输入开始，到产出槽位状态（JGA 口径）**和**每个意图的状态机状态（新口径）为止。
> 阅读对象：后续改 pipeline / 做消融 / 扩充数据集的人。
> 配套：实现 `intent_status.py` + `intent_ids.py`；改动点 `pipeline.py` / `prompt_builder.py` / `session_eval.py`；
> 消融开关见 §4；端到端实例见 §5、§6（实例为 v13 时代素材，身份机制看 §1.1、时间支线看 §1.2）。

---

## 1. 全流程总览（v27.3 现行，v26.2b 基础 + 时间证据支线 + 候选模式支线）

```
                 ┌─────────────────────────── 每一轮 (turn) ───────────────────────────┐
                 │                                                                      │
  输入: 当前用户话语 U_t                                                                │
        上一轮 agent 回复 A_{t-1}     ──开关 agent_context.enabled                      │
        上一轮累积状态 prev_states     (逐轮真实透传, 不用 GT, 内容键 app/svc[#N])       │
        上一轮各意图状态 prev_statuses ──开关 status_prompt.enabled                      │
        用户画像 user_context          (profile + records)                              │
        id 账本 IntentIdBook          ──开关 intent_id_protocol.enabled (v26.2b=true)   │
        列举存档 _cand_listing        ──候选模式 t1 存档, 选择轮序数兜底 (v27.3,          │
                                       session 级重置)                                    │
                 │                                                                      │
                 ▼                                                                      │
  [0] 时间证据  pipeline._build_time_hint (v27.0 新增, 在检索前并行):                    │
        time_extractor.extract(U_t, ref) 预解析明说时间（纯规则, 宁缺毋滥;                │
        v28.2 未定词槽位限定三层判定: 槽位关键词紧邻/隔值/后向选项列举+app 名命中          │
        → "应用和时延还没定"不再误判为时间未定, 已明说时间照常组装）:                      │
        ├─ TIME ANCHOR      ──开关 time_anchor.enabled: 相对词触发预计算日期表            │
        │                     (今天/明天/后天/大后天+星期, 查表禁止算术)                   │
        ├─ TIME EVIDENCE    ──开关 time_extractor.enabled: 明说时间点预解析绝对时间       │
        │                     +[start]/[end] 角色 + 时长候选 (只给证据不绑定)             │
        ├─ TIME RULE ×4     ──开关 time_rule_{contradiction/profile_slot/                │
        │                     explicit_first/candidate}: 矛盾保字面/画像时段优先/         │
        │                     明说>record/候选不落槽 (各自窄触发, 互斥豁免条款)           │
        └─ 子句级时段继承 + 跨夜滚动 + 无锚兜底 + 否定词防呆 (run1 −6.8pp 修复)           │
        → 返回 (hint, ev): ev 供候选列举轮时间桩兜底复用 (v27.3.1, 与模型所见同一值)      │
                 │                                                                      │
                 ▼                                                                      │
  [0.5] 候选模式状态机 (v27.2→v28, 详见 §1.3):                                         │
        ──开关 candidate_mode.enabled + _session_is_t33 (T3-3 模板信号)                  │
        待选择状态 (_cand_listing 非空, 列举轮后进入):                                   │
          LLM judge 三枚举判定 (v28, candidate_llm.judge_selection_turn):                │
          action(select/confirm/new_request/chitchat) + pick(选中序号) +                 │
          scope(落地字段范围 app/svc/res/rtt)——一次调用, schema 校验失败安全回退          │
          ├─ 非选择 → 清存档退出候选模式, 正常流程 (v27.6.1 生命周期闭环)                 │
          └─ select → 选择轮锚点链: 候选明细(话术序: 正则→LLM 兜底→存档) +                │
             选中明示 + **scope 驱动分支**: 全四字段→全量落地到桩(含 _time_keep           │
             明说时间锚点); 子集→只允许写该字段, 严禁当新保障办理 (v28, 治引用语义型)      │
        列举轮 (selector defer ≥2): 候选明细(话术序)注入 + 模型【零列举】(v27.5 方案B:   │
          身份字段一律悬空) + 偏好条款 + record 兜底抑制 + 执行后身份字段强制剥离          │
          (v27.7 路线乙: app∈候选且非话语明说目标 → 剥离, 抢跑不可表达, 1c 退役);         │
          record_selector 消歧分支候选并集(约束冲突时各约束最近一条 ∪ loose records[0])   │
                 │                                                                      │
                 ▼                                                                      │
  [1] 上下文编译  prompt_builder.build_prompt(time_hint, time_anchor_on)                │
        SCHEMA / 矩阵 / 活跃app掩码 / 画像 / DEMOS(检索, id 协议渲染)                     │
        + INTENT STATUS 段  + AGENT LAST REPLY 段                                        │
        + 规则段(消歧/改口/记录/应用名待补/画质高亮 quality_hint v27.2.1, 触发式)          │
        — 引用意图用 id(协议开)或 key(关)                                                │
        + TIME ANCHOR/EVIDENCE/RULE 段 (time_hint 原样渲染; time_anchor_on 时             │
          系统 prompt 用压缩版时间散文段, 9924→9084 字符对冲变长)                         │
        + CANDIDATE MODE 段 (候选模式触发时, 渲染在最前优先级最高)                        │
        + TASK: 已有意图按 id 列出 "I1 = {...}" + 段头协议 + 身份判定强制行               │
                 │                                                                      │
                 ▼                                                                      │
  [2] 检索      DiverseRetriever (MMR), query = embed(prev_state || utterance)          │
                (+ 纠正类 demo 注入, correction_demo.enabled)                            │
                 │                                                                      │
                 ▼                                                                      │
  [3] 生成      LLM (qwen-plus / qwen3.8-27b) → Text-to-Python                          │
                每段: # 身份判定: ... → # intent: I1(更新) / new(新建) → state.xxx 赋值    │
                取消: # cancel: <应用/服务名>(名字, 不写 id; 同类多份可写 id I2)           │
                候选列举轮: 额外输出 # candidate: <app>/<svc> 段 (CandidateBook 旁路,      │
                            不入 JGA; 每条赋该记录 res/rtt; 明说字段仍落意图段)           │
                时间槽: TIME EVIDENCE 结论直接引用勿重算; 矛盾照字面/候选不落槽            │
                PAR 校验回路: 缺 summary / 缺 # cancel / 段头非 id / 缺身份判定行 /        │
                              full-match new 可疑 → 重采样 1 次                          │
                 │                                                                      │
                 ▼                                                                      │
  [4] 执行      python_state.execute_program_multi (沙箱)                               │
                → 每段 (header, Δy); # cancel 段 → __cancel__ 信号                       │
                candidate 段剥离 (不入 JGA 主链, 候选记账旁路)                            │
                v27.3-d930: 沙箱 prev 挂 id 别名视图 (_by_id: I1→内容键 prev),            │
                修段头 I1 vs 内容键 prev 失配 → state.xxx 自引用崩 → delta 静默清零       │
                 │                                                                      │
                 ▼                                                                      │
  [4.5] 段处理  pipeline run_multi id 分支 (v26.2b 核心 + v26.3-p3 实例化):              │
                # intent: I1  → idbook.key_of 查表取 prev → State(prev) 执行              │
                # intent: new → 对空 prev 执行 → **归并校验**(无app同svc互补/唯一悬置:     │
                              prev 填空槽+残骸 _consumed_key 退役)                        │
                              → **实例化**(同 base key 已存在 → app/svc#N 编号,            │
                                prior_keys 含跨轮实例; mi_on)                             │
                              → **段去重**(产出 key 已在本轮 results → 丢弃冗余段)         │
                列举轮零意图段兜底 (v27.3.1): TIME EVIDENCE 完整 + 话语无新目标 app        │
                → harness 补建 new 时间桩 (四重收窄, 时间提取规则化闭环)                   │
                内容 key 从合并后 final_state 推导; base 相同=未改名(防伪改名自吞);        │
                改名走 idbook.rename(id 不变)                                             │
                 │                                                                      │
                 ▼                                                                      │
  [5] 校验      validator.validate: apply(Δy)+prev → final_state                        │
                时间知二推一(负 duration 不产, v27) / 矩阵校验 → validity                 │
                 │                                                                      │
                 ▼                                                                      │
  [6] 状态机推导 intent_status.derive_status  ──开关 status_tracking.enabled             │
                规则识别 Affirm            ──开关 confirm_rule.enabled                   │
                输出: 本轮每个 intent 的状态 Collecting/Ready/Confirmed/Cancelled        │
                 │                                                                      │
                 ▼                                                                      │
  输出: final_state (进 JGA 双口径评估)  +  status_pred (进状态机口径评估)                │
        prev_states / prev_statuses 透传给下一轮;                                        │
        cancelled ∪ consumed(被归并/改名) 的 key 从 session_out/prev_states 移除          │
                 └──────────────────────────────────────────────────────────────────────┘
```

### 1.1 意图身份机制（v26.2b，`intent_id_protocol.enabled=true`）

- **账本**：每 session 一本 `IntentIdBook`——id（I1/I2…，创建序分配）↔ 内容 key（app/svc）双向映射；改口改名走 `rename`，**id 永不变**。
- **prompt 呈现**：`#   I1 = {"application_name": "京东", ...}` 逐行列出已有意图；段头协议三条硬约束（I1 更新 / new 新建 / cancel 写名字）；**身份判定强制行**（每段头前 `# 身份判定: ...` ≤20 字，写明对应哪个 id 或为何无关，PAR 校验 `_identity_ok` 行数不足重采样）。
- **state 绑定**：id 在段头解析成内容键 → 取该意图 prev → `State(prev)` 构造——state 仍是"当前段所属意图"的匿名投影，代码体（`state.xxx`/find_timestamp）与协议无关。
- **三层兜底**（引用解析责任分配的落地形态）：模型显式指派（id/名字）+ harness 推断兜底（归并校验两级信号 + full-match new 重采样门）+ 全场去重审查（同 key 段先到先得）。
- **取消**：prompt 强制写名字（名字自校验；id 分配序与模型心智模型可能错位且无法从格式识别），写 id 时查表回退名字归一化。
- 遵从率实测 100%（729/729 段头）；非标准头（如全角冒号变体）防御性丢弃（qwen3.8-27b 暴露）。

关键性质：**状态机仍是纯旁路**。步骤 [6] 不修改 [0]-[5] 的任何结果。
步骤 [4.5] 是 harness 侧逻辑（不属于状态机），影响的是 prev_state 透传内容；步骤 [0] 是 prompt 支线（只加信息不加约束之外的新规则时遵守触发式纪律），同样不触碰评估判分。

### 1.2 时间证据支线（v27.0，六开关）

- **设计原则**：计算给规则、理解给模型——日期算术/时段映射由 `time_extractor.py` 确定性预解析，模型引用结论并负责"哪段话语对应哪个意图、该不该落槽"的绑定。**严禁后处理覆盖 pred**（v23/v9.0 封死）。
- **宁缺毋滥**：解析不确定 → 不产出 → prompt 与现状逐字节一致；提取器组装值 vs GT 一致率 99.4%（360/362）是放行门槛——注入机制的错误是乘性的（run1：提取器把"晚上7点到8点半"终点解析成 AM → 矛盾规则指示照抄 → −6.8pp）。
- **口径规则窄触发**：矛盾保字面（start>end 确定性检测，且与跨夜滚动互斥——合法解读优先）；画像时段优先（"按平时+时段"同现）；明说>record（记录指代+明说时间同现）；候选不落槽（"或者/还没定"；时长候选 dur_candidate 只抑制时长断言）。
- **效果**：d6 time_wrong 19→5，session 92.74%（详见 CHANGELOG v27.0 / experience/11_time_rules.md）。

### 1.3 候选模式支线（v27.2 引入，v27.3 锚点链完善）

- **触发域**：`candidate_mode.enabled` + `_session_is_t33`（T3-3 模板信号）。
- **t1 列举轮**：record_selector 判定树 defer（候选≥2）→ 注入 CANDIDATE MODE 列举指令 + 候选明细 + 偏好条款（"最好是X"≠最终选择）。模型输出 `# candidate: <app>/<svc>` 段（CandidateBook 旁路账本，python_state 剥离不入 JGA）；用户明说字段正常落意图段。
  - **时间桩兜底（v27.3）**：模型只列候选、漏落意图段 → 明说时间无家可归（S-000140 run2 型）。harness 用 TIME EVIDENCE 预解析值补建 `new` 时间桩（app/svc/res/rtt 全空）。四重收窄：列举轮 + ev 明说时间完整无矛盾无候选 + 零意图段（candidate 剥离后）+ 话语无新保障目标 app（记录筛选词不算，"最好是抖音那条"≠"用YY开直播"——B 型排除防选择轮 1a 全量落地劫持明说身份）。性质同 prev_state 兜底/知二推一：harness 代执行模型已认可的机械落地，时间提取规则化闭环。列举轮同时抑制 record coreference 兜底（防 records[0] 抢跑落地）。
- **t2 选择轮**：harness 状态机 + LLM judge（v27.6 起，v28 schema 化）——
  - **触发**：`_cand_listing` 非空（列举轮后的待选择状态）→ 无条件调 judge;非选择 → 清存档退出候选模式（v27.6.1 生命周期闭环）;选择落地后也清存档（防 t3 确认轮二次劫持）。话术正则退化为兜底。
  - **judge 三枚举**（v28,业界 schema 约束模式）：`{"action": select|confirm|new_request|chitchat, "pick": N, "scope": [app/svc/res/rtt]}`——判定/序号/落地范围一次调用;校验失败安全回退。scope 判断依据=话语动词限定词（"时延就按候选X"=["rtt"];"按X记录来配"=全量）。
  - 候选明细锚点：**话术序优先**（`_parse_disamb_candidates` 正则 → `parse_listing_llm` LLM 兜底（v27.4,治 svc+日期切开变体）→ 列举存档 `_cand_listing` 兜底）;
  - **分支由 scope 驱动**（v28,取代 1a/1b 硬编码）：scope=全四字段 → 全量落地到桩（含 `_time_keep` 明说时间锚点）;scope=子集 → "只允许写该字段,严禁把选中记录当新保障办理"（治 524 引用语义型）;1c 占位改写已随 v27.7 路线乙退役（列举轮身份字段强制剥离+明说豁免,抢跑不可表达）;
  - 选中项明示注入（"系统解析: 用户选中 = 第N条"）。
- **_parse_disamb_candidates 话术变体覆盖（v27.3）**：段首纯属性残片切回上一段（"50ms，抖音那条是…"的 50ms 属前候选）；`^.*?条是` 剥离吞 app 时回补（"抖音那条是开直播"）；单 app 问句残段跳过（"您选虎牙还是钉钉这条？"）。
- **效果**：d6 双跑 95.09%/96.36%（vs v27.2 +1.93pp/+1.61pp），T3-3 靶区稳救（089 基线三跑全错→双跑全对），稳破 0（详见 CHANGELOG v27.3）。

---

## 2. 状态机定义与推导规则

### 2.1 四状态

| 状态 | 定义 | 数据信号（dataset_merged 实证） |
|---|---|---|
| Collecting | 已识别，但存在阻塞性缺失/模糊/错误 | agent `Request_Slot` 231 轮 / `Request_Disambiguation` 78 轮 |
| Ready | 参数完整，并通过规则校验 | agent `Acknowledge` 912 例全部五槽齐（见 §2.2） |
| Confirmed | 用户明确确认 | user `Affirm` 117 例，全部末轮，全部单意图 |
| Cancelled | 用户取消 | user `Cancel_Request` 86 例 = intents status cancelled 86 例 |

终止态：Confirmed、Cancelled（Cancelled 后意图从状态移除；Confirmed 锁存，参数再改退回 Ready）。
域外 / rejected 意图（豆瓣/京东/知乎/美团等，118 例 invalid + 54 例 rejected）**不进入**四状态机
（与 JGA 口径"域外输出空或 invalid 都算对"对齐；若未来需要可作为第五态 Rejected 单列）。

### 2.2 "参数完整"的操作定义（数据实证，不是拍脑袋）

对全量 799 sessions 做"每轮末 agent 动作 × 累积槽位齐缺"交叉表：

| agent 当轮动作 | core 三槽齐(app/svc/start) | 五槽齐(+resolution+rtt) | 例数 |
|---|---|---|---|
| Acknowledge | ✓ | ✓ | **912（全部）** |
| Request_Slot | ✓ | ✗ | 89 |
| Request_Slot | ✗ | ✗ | 39 |
| Confirm_Slot/Hypothesis | ✓ | ✓ | 177（另 7 例 ✗） |
| Request_Disambiguation | 混合 | 混合 | 38 |

结论：**Ready 的充要近似 = app/svc/start/resolution/rtt 五槽齐 且 validator 通过**（≈99%）。
`requested_params` 标注印证：agent 请求的正是 `resolution.min_value` / `rtt.max_value` /
`timestamp.*`（如"请问您需要什么清晰度？对时延有要求吗？"）。

### 2.3 推导规则（`intent_status.derive_status`，pred/GT 共用同一视角）

优先级：**Cancelled > Confirmed > Ready > Collecting**

```
if 本轮被取消:            → Cancelled
elif 本轮识别为 Affirm 且五槽齐: → Confirmed
elif 上轮 == Confirmed 且本轮无参数变更: → Confirmed (锁存)
elif 五槽齐 且 validity 通过 且话语无选择疑问: → Ready
else:                   → Collecting
```

三个输入信号，全部零 LLM 成本：

1. **取消信号**：复用 pipeline 已有 `# cancel:` → `__cancel__` 机制（v4.0）。
2. **Affirm 规则识别** `is_affirm`：起点词（对/是的/好的/可以/确认/没问题/没错/嗯…）
   且不含否定/疑问/寒暄词。**实证：召回 117/117 = 100%，误报 0**
   （"没别的问题/谢谢/先这样"是 End_Dialogue 寒暄，数据集不标 Affirm，已排除）。
3. **选择疑问检测** `has_pending_choice`：话语含"是A还是B呢/我还没想好/还在犹豫/纠结"等，
   槽位有值但语义未定 → Collecting。**实证：对 agent Request_Disambiguation 召回 48/78 = 62%，误报 0**。
   覆盖不了的 30 例全是 record 消歧（"就按那条记录来"——话语无犹豫词，需画像候选>1 才能判，
   属已知边界，见 §7）。

### 2.4 GT 状态轨迹离线重建（`build_gt_status_trajectory`）

从 session 标注重建 `{turn: {intent_key: status}}`，与 pred 同规则、同系统视角（无未来信息）：

- 累积槽位：`slot_updates[intent].turn_updates` 逐轮 apply → 五槽齐判 Ready；
- agent 当轮 `Request_Slot`/`Request_Disambiguation` → 强制 Collecting
  （消歧场景槽位可能已齐，如"2点还是2点半我还没想好"，槽位规则会误判 Ready）；
- `Cancel_Request` 轮 + `related_intent_ids` 指向 status=cancelled 的 intent → Cancelled；
- `Affirm` 轮 + `related_intent_ids` → Confirmed。

全量重建分布（799 sessions）：**Ready 1075 / Collecting 224 / Confirmed 119 / Cancelled 86**。

### 2.5 链路上限（sanity check，`tools/_sanity_status.py`）

用"完美假模型"（直接吐 GT 槽位 + GT 取消信号）驱动完整评估链路（不调 LLM）：

```
状态准确率 1448/1504 = 96.28%
  Ready:     1075/1075 = 100%
  Cancelled:   86/86   = 100%
  Confirmed:  117/119  = 98.3%
  Collecting: 170/224  = 75.9%   ← 54 例全是 record 消歧 (utterance 规则覆盖不了)
```

含义：**槽位全对的前提下，状态机口径的可达上限 ≈ 96%**；剩余 4% 是规则边界
（record 消歧的 Collecting 判定需要画像候选信息，utterance 无信号）。
真实模型的状态准确率会随槽位识别错误进一步下降——状态口径与 JGA 联合看才能区分
"参数错"与"状态流转错"。

---

## 3. 评估口径（新增第三个口径，与 JGA 并列）

session_eval 输出（`status_tracking.enabled=true` 时）：

- **意图状态机准确率**：逐 (turn, intent_key) 比对 pred/GT 状态，报整体 + 按 GT 状态分桶
  （Collecting/Ready/Confirmed/Cancelled 各自召回）+ 混淆对（如 `Collecting->Ready`）。
- 产物 jsonl 每 session 增加 `status_detail`；summary 增加 `status_by_gt` / `status_confusion`
  / `switches`（记录各开关状态，保证消融可溯源）。

pred 的 key 缺失（意图识别错/张冠李戴）记 `missed=True`——状态口径**继承** JGA 的识别错误，
所以两个口径联合分析：JGA 错 + 状态错 = 识别问题；JGA 对 + 状态错 = 状态流转问题。

---

## 4. 独立消融开关（全量门控清单，v13.0 起 13 个 → v26.3 至 15 个 → v27.0 至 21 个）

> 规则：所有带门控的方法（无论最终默认开/关）都必须收录本表与 method 报告；默认关 ≠ 不生效于实验史。

| 开关 | 作用 | 改动位置 | 默认(qmap) | 引入版本 | 状态 |
|---|---|---|---|---|---|
| `agent_context.enabled` | 上一轮 agent 回复进 prompt。对症 T3-3 消歧信息不对称 | prompt_builder / pipeline / session_eval | true | v13.1 | ✅ 采纳（+3.30pp） |
| `status_prompt.enabled` | 各意图状态快照进 prompt（INTENT STATUS 段） | prompt_builder / pipeline / session_eval | true | v16.0 | ✅ 采纳（+1.43pp，强模型受益） |
| `status_tracking.enabled` | 推导 pred 状态轨迹 + 状态口径评估（纯旁路） | session_eval | true | v13.0 | ✅ 采纳（零副作用实证） |
| `confirm_rule.enabled` | Affirm 规则识别（117/117 召回 0 误报） | session_eval | true | v13.0 | ✅ 采纳 |
| `disambiguation_rule.enabled` | T3-3 消歧轮规则注入（候选禁作输出/沿用意图头/序数对齐/禁覆盖 prev） | pipeline `_is_disambiguation_turn` + prompt_builder | true | v21.1 | ✅ 采纳（T3-3 破零） |
| `disambiguation_rule.candidate_map` | 消歧候选顺序对照表（harness 解析 agent 话术注入"第N条=明细"） | pipeline `_parse_disamb_candidates` | **true(best)** | v24.4 | ✅ 采纳（d5 打平；d6 +2.99pp 转正） |
| `correction_rule.enabled` | 改口轮规则注入（沿用旧 key/只改被更正字段/其余沿用） | pipeline `_is_correction` + prompt_builder | true | v22.1 | ✅ 采纳（key 割裂获救） |
| `record_rule.enabled` | 无约束"按上次记录"严格规则（records[0] 字段明细注入） | pipeline `_needs_latest_record_rule` | true | v24.1 | ✅ 采纳（+4.97pp） |
| `correction_demo.enabled` | 纠正类 demo 注入（改口词触发检索改口案例前置） | pipeline `_retrieve_correction_demos` | true | v12.1 | ✅ 采纳 |
| `record_selector.enabled` | 显式 record 选择器（vote/reinject 二次 LLM） | pipeline（已删码，CONFIG 留档） | false | v9.0 | ↩️ 两次净负回退（后处理选值路线封死） |
| `conflict_check.enabled` | 窄版冲突检查反思回路（validator 信号分层 + 话语明说值比对 + bounded 1 轮重生成，逐意图采纳；触发明细入 turn_outputs.reflection） | validator `detect_utterance_conflicts` / pipeline `_conflict_check_round` / session_eval | false | v25.0 | ↩️ 双跑一致净负回退（反思路线全线关闭，详见 report_reflection.md §8）；**v26.4 代码移 tools/_archive/conflict_detector.py，config 键删除** |
| `intent_id_protocol.enabled` | 意图 id 协议（身份与内容解耦：prev 按 id 呈现/段头 I1/new/cancel 名字化/new 归并校验/身份判定强制化/段去重） | intent_ids.py + pipeline + prompt_builder + session_eval | false(qmap) / **true(idproto)** | v26.0→v26.2b | ✅ **v26.2b 采纳为当前最佳** |
| `pending_app_rule.enabled` | 应用名待补规则（话语无 app 时禁止猜 app + 桩列表 + 【默认更新,谨慎新建】；v3 文案双跑 88.46% 均值 +1.28pp，稳对 8/稳错 3） | pipeline `_utterance_mentions_app`/`_pending_stubs` | **true(best)** | v26.3 | ✅ 采纳（2026-09-15） |
| `multi_instance_key.enabled` | 同 app/svc 多意图实例化 key（`app/svc#N`，harness 内部后缀模型不可见；GT 按 intent_id 锚定；cancel id>时段>序数消歧；修 __len__ truthiness 陷阱） | pipeline `_instanced_key` + intent_ids + session_gt/turn_gt + intent_status | **true(best)** | v26.3-p3 | ✅ 采纳（dup-key 6/6 判对，五跑迭代修四条 key 路径 bug；d6 口径基准切换为 mi_on） |
| `time_anchor.enabled` | 预计算日期表（TIME ANCHOR：今天/明天/后天/大后天+星期，相对词触发）注入 + 时间散文段压缩版（系统 prompt 9924→9084 字符） | pipeline `_build_time_hint` + prompt_builder `time_anchor_on` | **true(best)** | v27.0 | ✅ 采纳（治相对日期 off-by-one；查表代替算术，arXiv:2601.07148 依据） |
| `time_extractor.enabled` | 明说时间预解析证据注入（TIME EVIDENCE：mentions 预解析绝对时间+[start]/[end] 角色+时长候选；只给证据不绑定） | time_extractor.py `extract` + pipeline `_build_time_hint` | **true(best)** | v27.0 | ✅ 采纳（提取值 vs GT 一致 360/362=99.4%；多意图句不组装，绑定留模型） |
| `time_rule_contradiction.enabled` | 明说时间矛盾保字面（确定性检测 start>end/三要素互斥 → 注入"照字面录入勿调和"） | time_extractor `has_contradiction` + pipeline | **true(best)** | v27.0 | ✅ 采纳（S-000066/227 start/end 转正；duration=180min 为 GT 设计字段不可达） |
| `time_rule_profile_slot.enabled` | 画像"常用开始时段"与"常用保障时长"冲突 → 以时段为准（触发=按平时+时段词同现，收窄防误触发） | pipeline `_profile_slot_conflict` | **true(best)** | v27.0 | ✅ 采纳（S-000063/165/237 转正；v1 触发过宽致 S-000080/125 回退已收窄） |
| `time_rule_explicit_first.enabled` | 本轮明说时间优先于 records/profile（"按上次记录"+明说时间同现触发） | pipeline（记录指代词+TIME EVIDENCE 同现） | **true(best)** | v27.0 | ✅ 采纳（S-000140 型 record completed_at 覆盖明说时间） |
| `quality_hint.enabled` | 画质条目高亮：话语 app/画质词查 schema.QUALITY_MAP（79 条标准表）注入精确值；话语无 app 时用 record_rule 定位记录的 app/svc 查表 | pipeline QUALITY HINT 块 + schema.QUALITY_MAP | **true(best)** | v27.2.1 | ✅ 采纳（5 失败 case 定点验证 5/5；治表遵从波动，全 case 平等） |
| `record_selector.enabled` | 记录指代预判（悬空 defer/定位/选择轮退出判定树；消歧会话信号=T3-3 模板或 agent Request_Disambiguation） | record_selector.py + pipeline 触发域重构 + session_eval 会话信号 | **false（默认关）** | v27.1 | ↩️ 三轮全量净负关闭（81.20/88.46/89.32%）；T3-3 家族 5 例机制性转正但扰动面>靶区；代码留档 |
| `candidate_mode.enabled` | T3-3 候选模式：t1 全部命中记录列 `# candidate:` 段（CandidateBook 旁路账本，不入 JGA）→ t2 用户 select 选中候选解引用落地（1a 空桩全量落地/1b 明说只补悬空/1c 占位改写）+ cancel 其余 → t3 confirm；触发域=T3-3 模板信号；v27.3 锚点链（候选明细话术序/序数解析/选中明示/时间锚点/悬空直给）+ 时间桩兜底（§1.3） | candidate_book.py + python_state 剥离 + pipeline `_session_is_t33`/`_parse_pick_ord` + prompt_builder 1a/1b/1c 分支 | **true(best)** | v27.2/v27.3 | ✅ 采纳（v27.3 双跑 95.09%/96.36%/状态机 90.29%；稳救 4 稳破 0） |
| `time_rule_candidate.enabled` | 候选时段未定不落槽（"或者/还是/还没定"触发；时长候选 dur_candidate 只抑制时长断言不抑制 start/end）。**v28.2/v28.3**: 未定词槽位限定三层判定（非时间槽位未定不抑制已明说时间）+ TIME RULE 文案随组装结果分叉（组装成功→"照常落地"；时间本身二选一→"留空"） | time_extractor `_generic_undecided` + pipeline ③d | **true(best)** | v27.0/v28.2 | ✅ 采纳（S-000185 保持抑制；533/1732 等透传型 5 例修复,T2 类 16→3） |

沙箱行为开关（python_state.execute_program，代码级默认）：`line_tolerant=True`（v26.2c）——单行 coreference 失败跳过该行保留已赋值，动机=弱模型"过度沿用"整段崩（qwen3.8-27b 实测 +11pp）；对 plus 零回归（冒烟验证）。配套 prompt 约束："只写变化的字段，勿写 state.x=state.x 沿用"。

评估口径类开关（session_eval CLI，非 config）：`--no_cancel_credit`（v17.1 取消计分）、`--no_correction_credit`（v20.2 改口豁免）、`--no_gt_derive`（v25.1 GT 知二推一）——三者默认开，均有离线复算工具（`tools/recompute_*.py`）。

已整体关闭的路线（不设开关，结论存档）：time_rules 时间后处理（v23 净负/两模型档封顶——注意与 v27.0 前处理注入的区别：v23 在 validate 后改 pred，v27 在生成前喂证据）、BoN/投票/verifier 采样（v24.5 oracle 门控 +1.63pp≤3pp）、反思/自纠（v25.0，见上）、后处理选值（v5.0/v9.0）、record_restraint 全局规则（v22.0③）、LLM 时间改写轮（v27.0 形态决策否决：改写器与 DST 共享日期算术弱点）。

消融建议顺序（每步独立可验证；历史消融在 dataset_merged 时代执行，现.dataset_5 复现命令如下）：

```bash
export DST_DATASET=data/dataset_5
# 0) 现行基线（v26.2b 全开）
python session_eval.py --config configs/config_d5_qwen_plus_idproto.json --tag abl_v262b
# 1) 关 id 协议（回 v25.2 内容头协议对照）
python session_eval.py --config configs/config_d5_qwen_plus_qmap.json --tag abl_no_idproto
# 2) 关状态口径（status_tracking+confirm_rule=off，JGA 不变，纯观察状态机贡献）
# 3) 关 agent_context / status_prompt（信息注入逐项消融）
# 4) 评估口径回退：--no_cancel_credit / --no_correction_credit / --no_gt_derive（CLI 旗标）
```

**口径说明（agent 回复是否作弊）**：agent 话术是对话上下文的输入侧信息（真实部署中就是系统
自己产出的），不属于状态 GT；与 MultiWOZ 等 DST 评估提供 system utterance 同口径。
已知近似：数据集的 agent 话术由 GT state 生成，复读参数的话术存在理论泄漏；
若需严格口径，后续可换"由 pred state 生成话术"。

---

## 5. 端到端实例 ①：S-000112（Collecting → Ready → Confirmed 完整生命周期）

模板 T3-1（补充 + 确认），reference_time = `2026年07月21日14时00分`。
冒烟配置：四开关全开。本例状态轨迹 pred 3/3 与 GT 全等，session JGA 也对。

### 原始对话

```
T1 用户: 帮我做个咪咕视频的保障，时间是今天晚上8点到8点半。
   agent[Request_Slot]: 好的，为您办理咪咕视频保障…请问您需要什么清晰度？对时延有要求吗？
T2 用户: 蓝光的，时延不超过150毫秒。
   agent[Confirm_Slot]: 好的，为您确认一下，清晰度为蓝光，时延不超过150毫秒…请问是否确认？
T3 用户: 对，没错。
   agent[Acknowledge]: 好的，已为您办理咪咕视频保障，清晰度为1080P，时延不超过150毫秒…
```

### T1 流程展开

**输入**：utt="帮我做个咪咕视频的保障，时间是今天晚上8点到8点半。"；prev_states={}（首轮）；
prev_agent_reply=None（首轮无 agent 段）。

**LLM 输出**（检索到相似 demo 后，text-to-Python）：

```python
# summary: 用户要保障咪咕视频，时间是今天晚上8点到8点半，无coreference或record指代
# intent: 咪咕/视频
state.application_name = "咪咕"
state.service_name = "视频"
state.timestamp = find_timestamp(start="2026年07月21日20时00分", end="2026年07月21日20时30分", duration="30min")
```

**执行+校验**：Δy = {app=咪咕, svc=视频, start/end/duration}；validator → valid。
final_state 缺 resolution/rtt。

**状态推导** `derive_status(prev=None, flat, "valid", affirmed=False, pending_choice=False)`：

```
五槽齐? app✓ svc✓ start✓ resolution✗ rtt✗ → 不齐 → Collecting
```

**GT 对比**：GT 侧同轮 agent 动作 = Request_Slot（请求 resolution/rtt）→ Collecting。**match ✓**

### T2 流程展开

**输入**：utt="蓝光的，时延不超过150毫秒。"；prev_states={咪咕/视频: T1 的 final_state}；
prev_agent_reply="…请问您需要什么清晰度？对时延有要求吗？"（开关①进 prompt）；
prev_statuses={咪咕/视频: Collecting}（开关②进 prompt，展示为 `# 咪咕/视频 = Collecting`）。

**LLM 输出**（补充轮，prev 单意图 → 无头段也兜底归并）：

```python
# summary: 用户补充咪咕视频的画质和时延要求，蓝光对应1080p，时延150ms
# intent: 咪咕/视频
state.resolution = "1080p"
state.rtt = "150ms"
```

（画质映射：咪咕/视频 的"蓝光"→1080p，见 prompt 中逐 app/服务真实映射表。）

**执行+校验**：apply 后 final_state = T1 ∪ {resolution=1080p, rtt=150ms} → 五槽齐，valid。

**状态推导**：五槽齐 ✓ 且 valid ✓ 且无选择疑问 → **Ready**。GT 同轮 agent = Confirm_Slot → Ready。**match ✓**

### T3 流程展开

**输入**：utt="对，没错。"；prev_statuses={咪咕/视频: Ready}。

**规则识别**：`is_affirm("对，没错。")` → 起点词"对"命中、无否定词 → **affirmed=True**。

**LLM 输出**（确认轮无新参数，全 coreference 沿用）：

```python
# summary: 用户确认上轮状态, 无新字段或修改
# intent: 咪咕/视频
state.application_name = state.application_name
...
```

**状态推导**：affirmed=True 且五槽齐 → **Confirmed**。GT 侧 Affirm 轮 related=[I1] → Confirmed。**match ✓**

### 本例要点

- 三轮三种状态全部来自**旁路推导**，LLM 不需要知道状态机的存在；
- T3 的 LLM 输出对状态没有任何影响（全沿用），Confirmed 完全由 `is_affirm` 规则产生——
  这正是"确认话语模板化"红利的体现（100% 召回、0 误报）；
- agent 回复段（开关①）在 T2 起作用：模型看到"请问您需要什么清晰度？"，明确当前在补哪两个槽。

---

## 6. 端到端实例 ②：S-000008（多意图 + 取消 → Cancelled）

模板 T2-6（取消），reference_time = `2026年07月23日00时00分`。状态轨迹 pred 4/4 全对。

```
T1 用户: 帮我做个网络保障，明天上午8点看半小时哔哩哔哩视频，标清就行，延迟控制在30毫秒内；
        另外8点一刻还要看半小时优酷视频，得是1080p的，延迟别超过150毫秒。
   agent[Acknowledge]: 好的，为您受理两项视频保障…
T2 用户: 8点一刻看优酷那个半小时的保障不用了，帮我取消吧，哔哩哔哩那个保留。
   agent[Acknowledge]: 好的，已经为您取消…优酷视频保障了…
```

**T1**：LLM 一次输出两个 intent 段（哔哩哔哩/视频 540p/30ms/08:00 起 30min；优酷/视频 1080p/150ms/08:15 起 30min）。
两个 intent 五槽均齐 → **Ready, Ready**（GT 同，match ✓✓）。

**T2**：LLM 输出：

```python
# summary: 用户取消优酷视频保障，保留哔哩哔哩的
# cancel: 优酷/视频
# summary: 用户保留哔哩哔哩视频保障，沿用上轮状态
# intent: 哔哩哔哩/视频
state.application_name = state.application_name
...
```

- `# cancel:` 段 → `__cancel__` 信号 → `_resolve_cancel_key` 归一化到 prev 里的 `优酷/视频`
  → 该 intent 从 session_out / prev_states 移除，状态机记 **Cancelled**（GT 同，match ✓）；
- 哔哩哔哩段全沿用 → 五槽齐 → **Ready**（GT 同，match ✓）。

最终 session 意图集合 = {哔哩哔哩/视频}，与 GT（cancelled 不计入）严格全等 → session JGA ✓。

---

## 7. 已知边界（诚实清单）

1. **record 消歧的 Collecting 判定覆盖 62%**：选择疑问规则对"是A还是B呢/没想好"类召回 48/78、
   误报 0；剩余 30 例是"就按那条记录来"类——话语无犹豫词，歧义在画像候选数>1，
   需要画像信息才能判，本轮未做。这是 sanity 上限 96.28% 的全部主要损失。
2. **Confirmed 的确认对象**：当前数据 Affirm 全部单意图（117/117），"确认全部 Ready 意图"的
   规则无歧义。数据集扩充出现多意图确认时，需要显式确认对象标注（已在扩充需求清单）。
3. **域外/rejected 意图不进状态机**：118 例 matrix_out_application + 54 例 rejected。
   其中 34 个 resolved-域外 session（如 S-000127 知乎/云游戏）的 Affirm 因此不在状态口径内。
   若未来定义第五态 Rejected 可纳入。
4. **agent 话术的理论泄漏**：见 §4 口径说明。
5. **cancel 后 turn 级 JGA 的口径边界**：取消后 GT 仍记历史态，pred 空被判 missed（v4.0 已确认，
   非回退）；状态机口径不受影响（Cancelled 是显式状态）。
6. **T2 轮 LLM 输出缺 intent 头的兜底**：prev 单意图时无头段归并（v12.1），多意图时仍会丢段——
   状态口径会体现为 missed。

## 8. 产物与复现

| 文件 | 说明 |
|---|---|
| `intent_status.py` | 状态机模块：is_affirm / has_pending_choice / derive_status / build_gt_status_trajectory / judge_status |
| `tools/_sanity_status.py` | 完美模型链路自洽冒烟（不调 LLM）：`python tools/_sanity_status.py` |
| `configs/config_smoke_status.json` | 四开关全开的冒烟配置示例 |
| `data/split_mini_status.json` | T3-1×2 + T2-6×2 迷你 split（演示/调试用） |
| `data/evals/eval_smoke_status_full.jsonl` | 全开关冒烟产物（本文 §5 §6 素材来源） |

冒烟（4 session，全开关）：8/9 = 88.9%（Confirmed 1/1、Cancelled 2/2、Collecting 1/1、Ready 4/5）；
8 session（仅状态两开关）：19/25 = 76%（Cancelled 5/5）。

**全量回归（status_obs，303 sessions 剔除噪声，2026-08-24）**：
JGA 与基线打平（session 62.71% / turn 61.84%，旁路无副作用实证）。
首个全量状态口径基线：

| GT 状态 | 准确率 | 说明 |
|---|---|---|
| 整体 | **489/621 = 78.74%** | sanity 上限 96.28%，差距=槽位识别错误传导 |
| Cancelled | 37/38 = 97.4% | 取消机制稳定 |
| Ready | 373/438 = 85.2% | |
| Confirmed | 32/43 = 74.4% | Affirm 轮参数/key 错则无法 Confirmed |
| Collecting | 47/102 = 46.1% | 最弱，主因 `Collecting->Ready` 混淆：record 消歧 30 例无话语信号（已知边界）+ 模型超前补齐槽位（pred 五槽齐判 Ready，GT 按剧本 agent 仍 Request_Slot 判 Collecting——口径固有张力，非纯错误） |
