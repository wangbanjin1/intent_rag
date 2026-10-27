# 评估口径专册（report_eval.md）

> 更新：2026-09-22 ｜ 配套：session_eval.py（评估器）、tools/recompute_*.py（离线复算）、error_report.py（可视化）、tools/replay_time_cases.py（时间机制回放）
> 本册回答四个问题：JGA 怎么算的、三层口径修正为什么必要、跨版本比较的换算规则、v27.0 时间机制的判分边界。

## 1. 主口径定义（当前生效版本）

### 1.1 session 级 JGA
- 单元 = 1 个 session。模型最终意图集合（`app/service` 键）与 GT **严格全等**（集合相等 + 每意图 7 字段相等）。
- GT 集合 = intents[] 中 `status=resolved` 且有 application_name 的意图（cancelled/rejected/域外无 app 不计）。
- 错误类型：`intent_set_mismatch`（集合不一致，missing/extra）/ `field_wrong`（集合一致但字段错）。
- **空 app 头的 pred 意图不计入 pred 集合**（judge_session；v26.2b 后该形态已被机制消灭，残留 `new`/空 key 幻影仅来自模型输出残缺段）。

### 1.2 turn 级 JGA（真实透传）
- 单元 = (轮, 意图) case。比对**截止该轮的累积态**（prev_states 用模型上一轮真实 final_state，不用 GT——误差累积，复现真实部署）。
- GT 累积态 = slot_updates 按 intent_id 累积（case_format.extract_turn_cases）+ **v25.1 gt_derive 知二推一**（见 §2.3）。
- 特殊 case：
  - **取消 case**（v17.1 cancel_credit）：cancelled 意图在取消轮的 GT 记 `__cancelled__`，模型该轮无此 key = 命中；定位 = 用户 action 含 `Cancel_Request`（88/88 核验）。
  - **改口 case**（v20.2 correction_credit）："用户说错"错误值轮（T2-3 轮1/T3-2 轮2，定位 = action 同含 `Negate`+`Correct_Previous_Input`）GT 记字面错误值——模型拒收（pred 空）= 命中；pred 非空则豁免被更正字段后比其余字段。

### 1.3 意图状态机口径（旁路）
- 四状态 Collecting/Ready/Confirmed/Cancelled，在意图收官轮比 GT 状态 vs 模型状态（详见 report_flow.md）。
- Cancelled>Confirmed>Ready>Collecting 优先级；Affirm 规则识别（117/117，0 误报）；域外/rejected 不进状态机。

### 1.4 双跑与采纳门槛
- 单跑噪声 ±1.5pp（temp=0 亦有，服务端数值/路由非确定性）；候选版本双跑取均值；
- 采纳判据：均值提升且**双跑方向一致** + 翻转归因显示机制因果面（双跑一致翻转集合）；
- ±2pp 内的表观差异不作采纳依据，须看稳对集（双跑都对）与机制归因；
- **例外（v27.0 先例，2026-09-20 用户拍板）**：单跑效应量超噪声带 2 倍+（v27 fix3 +4.71pp/+3.64pp）且方向明确时，可单跑定案，以**基线多跑稳定性核查替代第二跑**（v27 回退 6 个 session 逐一对照基线五跑结果，确认全部为波动边界而非机制致退）。

## 2. 三层口径修正的完整记录

| 口径 | 版本 | 修的问题 | 假错误机制 | 修正动作 | 收益 | 回退开关 |
|---|---|---|---|---|---|---|
| 取消计分 | v17.1 | 模型正确取消反被判 missed | 取消后 GT 仍记历史态，"应为空"的意图被判 missed_intent | 取消轮 case GT 记 `__cancelled__` | turn +5.83pp（34/34 转正） | `--no_cancel_credit` |
| 改口豁免 | v20.2 | 模型正确拒收"用户说错"的字面错误值反被判错 | 设计口径记字面值 vs validator 正确拒收 | 错误值轮 pred 空=命中；非空豁免被更正字段 | turn +2.04pp（14 转正/7 仍错全真错） | `--no_correction_credit` |
| GT 知二推一 | v25.1 | 模型中间轮正确推导被罚 | GT 只在"字面明说"轮登记（X-2 共享时长子句只绑被取消意图，22 session），模型 validator 每轮强制推导、GT 最终态三字段必齐 | turn GT 比对前同规则补齐（`_derive_time_flat`，只补缺失不覆盖） | turn +1.61pp（+11/−0，双跑集合一致） | `--no_gt_derive` |

**修正的统一原则**：GT 的每一步"加工"（取消移除、错误值豁免、字段推导）只要模型侧对称做了（validator 每轮推导），评估侧就必须同步；否则模型越"对齐 GT 最终态"越被中间轮惩罚。

**反向证伪记录**（口径不能随意反向）："关掉模型侧知二推一"实测不可行——判对 turn 的 16.5%（83/502）duration 靠 validator 推导补齐，全翻错 −12pp。

## 3. 跨口径换算规则（历史数字对照表）

d5（test 322，剔除 7 噪声）turn 级累计口径差：

| 版本节点 | 当时的 turn 数字 | v25.1 现行口径复算 |
|---|---|---|
| v19.0 | 75.47% | （未复算，三层叠加约 +9.4pp） |
| v20.0 | 75.77% | 77.81%（v20.2 复算） |
| v21.1 | 77.37% | — |
| v22.1 | 78.83% | — |
| v24.1 | 83.07% | **84.67%**（v25.1 复算） |
| v24.1 run2 | 83.94% | 85.55% |
| v25.2 | 88.32% | （原生 v25.1 口径） |
| **v26.2b** | **88.03% / 89.64%** | （原生） |

- **跨版本比较 turn 级必须同口径**；报告用 † 标注剔除噪声口径。
- 旧 eval 产物可用 `tools/recompute_correction_credit.py` / `tools/recompute_gt_derive.py` 离线复算（不重跑模型）。
- session 级不受三层修正影响（取消/改口/GT推导全是中间轮现象；session 比最终态，GT 最终态三字段必齐）。

## 4. 7 字段与槽位约定

- EVAL_SLOTS = application_name, service_name, resolution, rtt, start_timestamp, end_timestamp, duration（bandwidth 不出现）。
- 嵌套路径映射：resolution.min_value→resolution、rtt.max_value→rtt、timestamp.*→三时间字段。
- 域外意图（豆瓣/京东/知乎/美团/网易云音乐等不在受理矩阵）：输出空或 invalid 都按口径处理（域外 resolved 意图进 session GT——"按原话赋值+校验器判 matrix_out"是设计行为）。
- 时间三字段知二推一规则：start+end→duration、start+duration→end、end+duration→start（validator `_complete_timestamp` 与 eval `_derive_time_flat` 同规则）；**v27.0 起 validator 不再产生负 duration**（矛盾场景字面 start/end 落槽、duration 留明说值，负差值不写回 state）。

## 5. 噪声剔除

- `find_noise.py --all` 客观规则：R1 时间内部矛盾 / R2 明说时长不符 / R3 明说时段不符 / R4 画质映射矛盾 / R5 record 选择规则。
- d5 检出 13 条（新变体 R5-A/R5-C），test 7 条 → 剔除口径 322；清单 `data/_noise_exclude_d5_scan.json`，session_eval 默认剔除（`--include_noise` 关闭）。
- **d6**：3 条 R5-A 问题样本（S-000106/210/228，record 选取 GT 错位）→ 参评口径 234；清单 `data/_noise_exclude_d6_scan.json`（`--noise_file` 指定）。
- 噪声样本在 error_report 单列区块（含 record 选取对照），不计入任何统计。
- **数据集版本升级必须重扫噪声**（R5 依赖生成器/评估顺序约定，约定变噪声就变）。

## 6. error_report 可视化约定

- turn/session 双视图切换，各含"错误分布 + 原因分布（二级标签）+ 样本卡"。
- 二级标签（原因维度，参照 ESF-Bench）：FMT 形式差异 / SR 取消 / CU 消歧 / COR 改口 / PER 画像 / REA 推理 / CTX 沿用 / XFER 串扰 / INT 意图 / GEN 兜底。
- 样本卡必备：对话（含逐轮模型实时输出折叠块）、GT vs 预测 7 字段表（**GT 推算值打"推算"徽章**）、错误分析（含 key 错位标注、画像来源注释）、数据集原始信息折叠、状态机对照（session 卡）、最终结果每意图 7 字段（session 卡）。
- 状态演进模块：逐轮全量累积态（GT 按 intent_id 折叠继承；模型侧优先 turn_outputs 全量 preds）。

## 7. 常见误读澄清（FAQ）

1. **GT 列的值在 slot_updates 里查不到？** → v25.1 gt_derive 推算补齐，看"推算"徽章与"GT自动推算"来源列。
2. **"漏识别"但对话里明明提到了？** → 看 key 错位徽章：模型最终态有近邻意图（空 app 头/改口残留/消歧选错记录），非真漏识别。
3. **session 卡少了模型输出的意图？** → 无 app 的 pred 意图曾不显示；v25.1 报告后已展示并标注"空 app 头 / key 断裂残骸"。
4. **turn 数字和旧报告对不上？** → 口径差：v17.1 +5.8pp / v20.2 +2.0pp / v25.1 +1.6pp 三层叠加，见 §3 换算表。
5. **状态机明明该 Confirmed 却显示 Collecting？** → confirm_rule 关闭时不产生 Confirmed（消融设计）；qmap/idproto 配置默认开。
6. **（v27）pred 的 start 晚于 end 但判对了/判错了？** → 矛盾轮判分与 TIME RULE 口径一致：模型照字面录入 = 与 GT 一致判对；自行调和反而判错。先查该轮是否 T2-3/T3-2 设计内矛盾轮（correction_turn_map 豁免被更正字段）。
7. **（v27）某 session 的 duration 与话语不符？** → 先看是否 GT 骨架设计字段（如 S-000066 duration=180min 话语从未明说，不可达，报数据侧）；再看画像时段/时长不一致场景是否按"时段为准"判（time_rule_profile_slot 口径）。
8. **（v27）同话语两次跑结果不同？** → 先查 TIME EVIDENCE 是否触发（触发轮 prompt 变长且规则段生效，波动源多一重）；replay 工具 `tools/replay_time_cases.py` 可双配置对照单 session。

## 8. 增补（v26.3，2026-09-15）：multi_instance_key 口径（d6 当前基准）

> **v27.0 基准更新（2026-09-20）**：mi_on 口径下 d6 新最佳 = v27.0 fix3 **session 92.74% (217/234) / turn 93.54% (463/495) / 状态机 86.08%**（`config_d6_qwen_plus_best.json` 六时间开关全开）；v26.3-p3 88.03%/89.90% 为 v27 前基准。

**变更**：`multi_instance_key.enabled=true` 时，同 app+service 的多条意图各自独立判分——GT 按 intent_id 实例化（`app/svc#N`，编号基于全量 intents[] 含 cancelled 的 base 首现序），pred 侧 harness 同步实例化，session/turn/状态机三口径与 error_report source_map 全部切换。

**与 mi_off 口径的关系**：
- mi_off（v26.2c~v26.3-p1 时代）：同 key 双意图在 dict 中塌缩——5/6 的 dup-key session 因塌缩巧合判对（假阴性），1 个全错；S-000046 第二档真实画质错被掩盖。
- mi_on（v26.3-p3 起）：塌缩消除，真错误暴露 + dup-key 干净判分，总分持平（88.03% vs 88.46%）但**诚实度不可比**。
- **d6 数字自 v263p3_d6_best_run5 起以 mi_on 为基准**；与 mi_off 时代数字对比时注意 6 个 dup-key session 的判定变化。

**触发条件**：config 需同时 `multi_instance_key.enabled=true` 与 `intent_id_protocol.enabled=true`（单开 mi 降级为 false 并打印警告）；summary.json 落盘 `multi_instance_key` 字段，error_report 自动识别产物口径。

## 9. 增补（v27.0，2026-09-20）：时间错误口径备注

v27.0 后 time_wrong 桶从 19 降至 5。剩余 5 个 time_wrong 的构成需注意两点口径：

1. **GT 设计字段不可达**：S-000066 的 duration=180min 在话语中从未明说（"今天9点开始、8点半结束"为设计内矛盾轮，T2-3 correction_credit 豁免只覆盖被更正字段），属 GT 骨架字段——此类不计入"机制可修"错误。
2. **矛盾保字面口径**：v27.0 TIME RULE 指示模型对明说矛盾"照字面录入、等待更正"，与 GT 口径一致；后续轮的改口由 correction_rule 接力。若看到某轮 start>end 仍判错，先查该轮是否属 T2-3/T3-2 设计内矛盾轮（correction_turn_map 覆盖）。

跨时间口径比较：v27 前后 turn 级数字差主要来自 time_wrong 收窄（−14）与 profile_slot 收窄的次生影响，其余模板判分逻辑零变更。
