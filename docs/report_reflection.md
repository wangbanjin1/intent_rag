# 反思机制（Self-Reflection / Self-Correction）论文调研与本项目方案

日期：2026-08-28　状态：方案设计（未实施）→ **2026-08-31 代码核验后修正：实施暂缓，见 §6**

## 1. 问题定义

当前 pipeline（B1 RefPyDST 主线）：检索 → text-to-Python 生成 → 沙箱执行 → 规则 validator 校验 → 输出 final。
**缺失环节**：validator 检出 invalid_reason（7 类）后直接放行/标错，**没有把失败信号反馈给 LLM 重来一次**。现有"校验回路"仅是 PAR 重采样（缺 summary/缺 # cancel 时盲重采 1 次，无错误信息回传），不算真正的反思。

目标：让系统在输出出错时能"看到自己的错误并修正"，且**不引入 GT 作弊、不破坏现有最优 v20.0 基线**。

## 2. 论文调研

### 2.1 候选机制谱系

| 论文 | arXiv | 反思信号来源 | 是否需外部反馈 | 对本项目适配度 |
|---|---|---|---|---|
| **Reflexion** (Shinn et al.) | 2303.11366 | verbal 反思存入 episodic memory，跨 trial 复用 | 需 env reward / 执行信号 | 中：跨轮 memory 机制可借，但我们的错误多在同轮内 |
| **Self-Refine** (Madaan et al.) | 2303.17651 | 同一 LLM 自评→自改，纯 verbal | **无外部信号** | 低：见 2.2 反面证据 |
| **CRITIC** (Gou et al.) | 2305.11738 | 调用外部工具（计算器/搜索/解释器）产出 critique 再修正 | 工具反馈 | **高**：我们已有沙箱执行 + 规则校验器，天然是"工具" |
| **Self-Debugging** (Chen et al.) | 2304.05128 | 代码执行结果/报错反馈给 LLM 重改代码 | 执行反馈 | **高**：我们就是代码生成式 DST，范式完全同构 |
| **LLMs Cannot Self-Correct Reasoning Yet** (Huang et al.) | 2310.01798 | —（批评性综述/实证） | — | **关键约束**：无外部反馈时自纠不升反降；提升几乎全来自 oracle 标签或外部工具 |
| SelfCheckGPT | 2303.08896 | 多次采样一致性检测幻觉 | 采样方差 | 低：检测型非修正型，且 temperature=0 下采样一致性无意义 |
| ReAct | 2210.03629 | 推理-行动交替，env 观察进轨迹 | env 观察 | 中：思想借鉴（把校验结果当 observation），但完整 ReAct 改造过重 |
| LATS | 2310.04406 | MCTS + env reward 搜索 | env reward | 低：搜索成本高，单轮一次出错的场景用不上 |

### 2.2 关键结论（决定方案选型的三条）

1. **纯 verbal 自评自改（Self-Refine 式）对本项目大概率无效**。2310.01798 的核心发现：LLM 在没有外部信号时无法可靠判断自己对错，自纠常常把对的改错。这与本项目两次后处理失败教训（v9.0 显式选择器、v5.0 规则补全，均净负回退）方向一致——**没有可靠错误信号源的"二次干预"是负收益**。
2. **本项目恰恰拥有别人没有的外部信号**：沙箱执行报错 + 规则 validator 的 7 类 invalid_reason + 取消校验。CRITIC/Self-Debugging 的成立前提（工具交互反馈）我们已免费具备。这是反思机制在本项目区别于纯文本任务的根本优势。
3. **v20.1 错误归因**：PER 52＞REA 39＞INT 26＞CU 25。PER+REA=55% 是能力短板（画像 record 消歧无话语信号），反思无法凭空创造信息；但 INT/CU/时间/格式类错误**有明确的 validator 可捕获信号**，是反思的有效靶区。

## 3. 推荐方案：校验反馈反思回路（CRITIC × Self-Debugging 式）

### 3.1 机制

```
生成 raw code
  → execute_program_multi（沙箱）
  → validate（规则校验，apply Δy → y_t）
  → 若触发反思条件：
        把 [原始 code + validator 的 invalid_reason + 出错 intent_key] 回灌 prompt
        LLM 重生成 1 次（bounded，最多 1 轮）
  → 重新执行 + 校验
  → 采用修正后结果
```

与现有 PAR 重采样回路的本质区别：**重采样是盲重试（同 prompt 再抽一次），反思是带错误信号的定向修正（prompt 里写明哪错了）**。

### 3.2 触发条件（关键设计：只在有可靠信号时触发）

只对 validator **确定检出**的错误触发反思，不对"疑似不对"触发（避免 v9.0 式误干预）：

| 触发源 | 对应错误类 | 反思可修性 |
|---|---|---|
| 沙箱执行异常 | FMT/代码错 | 高（Self-Debugging 主场） |
| invalid_reason ∈ {时间字段矛盾, schema 越界, invalid_resolution_unit 等规则类} | 时间推算/值域 | 中高 |
| 取消词命中但无 # cancel（现有 _cancel_check_ok 升级为回传而非盲重采） | SR 取消 | 高 |
| CU 消歧轮误建新意图（修补②T3-3 规则落地后可检测） | CU/INT | 中 |

**明确不触发**：record 画像选值（PER）——无话语信号时 validator 检不出"选错条"，强行反思=无信号自评=2310.01798 失效区。PER 类交给修补③/画像消歧规则解决，不靠反思。

### 3.3 与历史失败的隔离

- **不是后处理选值**：反思让 LLM 重新生成完整 code（生成侧），不是 validate 后篡改 delta（v9.0 失败路线）。
- **不是盲重采样**：错误信号显式进 prompt。
- **bounded 1 轮**：与 PAR 回路同预算，成本可控；不做 Reflexion 式多 trial 循环。
- **默认关**：config `reflection.enabled`（默认 false），消融开关，与 v13 以来四开关惯例一致。

### 3.4 预期收益与诚实上限

- 有效靶区估算：v20.0 的 166 错误 turn 中，时间 37 + 部分 INT 26 + SR 2 + 执行/格式残余 ≈ **40-60 turn 处于可捕获信号区**，其中反思能修正的比例按 CRITIC/Self-Debugging 论文幅度（10-30% 相对提升）保守估计 **10-20 turn → turn 级 +1.5~3pp**。session 级收益更小（累积误差下需全轮对）。
- PER 52 + REA 39 基本不在射程内——**反思机制不改变能力上限，只回收"已检出但未修正"的错误**。
- 风险：反思翻转正确样本（2310.01798 警示）。缓解：触发条件保守 + 逐样本 rescued/regressed 统计（沿用 err_report 口径）。

## 4. 否决项与备选

- **Self-Refine 纯自评**：否决（2.2 结论 1）。
- **Reflexion 跨轮 episodic memory**：暂缓。机制上对应"本轮错了、下轮记住教训"，但我们的错误 83% 在当轮可检测，跨轮记忆收益面小、prompt 膨胀代价大。若 3.1 落地后仍有同类错误反复出现，再评估。
- **LATS/MCTS 搜索**：否决，成本不成比例。

## 5. 实施计划（按工作流）

1. 落地顺序：先做修补②（T3-3 消歧规则，首要）→ 反思回路与其正交，可在②之后单开消融。
2. 实现：`pipeline.run_multi` 在 validate 后加 `reflection.enabled` 分支；新 `prompt_builder.build_reflection_prompt(code, errors)`；config 开关 + `max_rounds=1`。
3. 评估：d5 剔除 322 口径，qwen-plus + v20.0 最佳配置 ± reflection，tag `v21_reflect`；逐样本 rescued/regressed；error_report 重跑看靶区桶变化。
4. 归档：CHANGELOG v21.0 + 三册报告更新。
5. 判负条件（预先约定）：session 或 turn 任一主指标净负即回退，与 v9.0/v12 同样纪律。

---

## 6. 代码核验修正（2026-08-31，用户审阅时触发，实施暂缓）

§2 论文选型结论**维持不变**（CRITIC×Self-Debugging 选型、否决纯自评的依据与项目 v5.0/v9.0 实证互洽）。但 §3 的触发设计对照实际代码核验后发现三个前提不成立：

**① "invalid 即错误"假设不成立（最严重）**。`validator._check` 的 `missing_required_slot` 在 app/service/start_timestamp（schema.py REQUIRED_SLOTS 三项）任一缺失时判 invalid——但多轮中间态（Collecting，槽位本未收齐）和域外意图（评估口径"输出空或 invalid 都算对"）**都是 invalid，invalid 是常态而非错误信号**。按 §3.2 以 invalid_reason 触发会大面积误触发，且"修正"方向=补齐缺失槽位=**恰是 v22.0 ③已证伪回退的"超前补齐"**。

**② "沙箱执行异常"信号源不存在**。`python_state.execute_program_multi` L293 `except Exception: delta = {}`——执行错误被静默吞掉，无报错可回传；且 v20.1 实测 FMT=0，qwen-plus 几乎不产出执行/格式错误，该触发源建好也几乎不命中。

**③ 时间错误大部分 validator 检不出**。validator 只能抓 `time_inconsistent`/`time_out_of_range`；且 `_complete_timestamp` 知二推一在校验前已自动修掉一部分矛盾。实测时间错（37~44/685）多数是"推算错但自洽"（日期推错、内部一致），validator 无从发现——§3.4 把"时间 37"整桶算入靶区偏乐观。

另有两个靶区已过时：T3-3 行假设"②落地后可检测"，但 v21.1 落地后剩余 7 败是 PER 序数/日期对齐（正在 §3.2 自己声明的"不触发区"）；取消缺 #cancel 实测仅 2 例（cancel_wrong=2），无收益空间。

**修正后靶区重估**：可靠触发信号只剩 time_inconsistent / time_out_of_range / 部分 slot_value_invalid，**<15 turn，预期 +0.3~0.8pp，低于 API 噪声带（±1.5pp）**——与 v23.2 时间规则"收益上限 +0.62pp < 噪声带"同结论。

**结论**：实施暂缓。反思成立的前提是"校验器能区分正常未收齐 vs 真实错误"，当前 validator 做不到；先升级 validator 错误信号（只对确定性矛盾出信号）后靶区仍太小，投入产出不划算。v22.1 之后剩余错误 ~55% 在 PER/REA 无信号区（2310.01798 失效区），资源优先给 PER 方向。若未来重提反思机制：先解决 validator 信号分层，再按本文 §3 的 bounded-1-round + 触发式 + config 默认关形态实施。

---

## 7. 二次复核与采样路线定量封死（2026-09-01）

本节归档三份新证据：①2024-2026 新文献复核；②用户早期单轮项目"对照原文两遍核验"有效经验的定性；③基于 v19 同配置双跑产物的采样多样性实测。结论：**暂缓判断维持不变，且 BoN+投票路线被定量封死；仅剩两条有前提的出口（§7.5）**。

### 7.1 新文献复核（2024 中 ~ 2026）

| 论文 | arXiv | 与本项目结论的关系 |
|---|---|---|
| When Can LLMs Actually Correct Their Own Mistakes（TACL 综述） | 2406.01297 | **支持**：无外部反馈的 prompt 式自纠无可靠证据，2310.01798 两年未被推翻 |
| SCoRe（DeepMind） | 2409.12917 | 细化：内在自纠可经多轮 RL 训练获得，但 prompt 唤起式无效；qwen-plus 为 API 不可训，不适用 |
| DeepSeek-R1 | 2501.12948 | 同上：反思能力以可验证奖励训练涌现，非 prompt 唤起 |
| Confidence v.s. Critique | 2412.19513 | 机制解释：自纠="保住对的"×"修错的"天然冲突 → 翻转正确样本的结构性根源 |
| Self-Correction Bench | 2507.02778 | **64.5% 自纠盲点**：模型对自己的错看不出；追加 "Wait" 消 89%（条件化触发的证据） |
| LLM Monkeys | 2407.21787 | pass@k 覆盖率随 N 增长，但无 verifier 无法兑现 |
| TTRL | 2504.16084 | 多数投票作伪标签，天花板 ≈ pass@k |
| GenRM（DeepMind, ICLR'25） | 2408.15240 | 生成式 verifier，Best-of-N 大幅超判别式 RM——BoN+verifier 路线标杆 |
| CorrectionLM / ReacTOD | 2410.18209 / 2605.19077 | DST 专用自纠，与我们"代码生成+规则校验"同构，方案对标对象 |

**总判断**："无外部信号自纠无效、有外部信号才有效"未被推翻，仅被细化。原调研遗漏的三类机制：生成式 verifier、self-consistency 伪标签测试时学习、条件化触发（"何时反思"）。

### 7.2 用户早期"对照原文两遍核验"经验的定性

用户在早期单轮单意图项目做过"第一遍抽取 → 第二遍对照原文查冲突"，实测有提升。这与本文暂缓结论**不矛盾**：该做法的原文是独立于生成器的外部参照信号，属 CRITIC 谱系有效区，而非 2310.01798 否决的纯自评。其有效的三条件：

1. **输入自包含**：单轮单意图下全部字段真值都在原文内，"对照原文"是完备信号；
2. **核验比生成容易**：判断冲突的可靠性高于抽取本身；
3. **错误全部可对照**：抽取错=与原文矛盾，无一例外。

多轮多意图下三条件均不成立：真值分散于话语/prev_state/画像 records/时间规则/agent 话术五处（三路扫描实证用户从不复述记录数值，PER 错误与原文永远自洽）；中间轮缺槽是设计而非错误（对照原文会误报"漏填"）；PER/REA 难在推理而非抽取（同一模型核验不优于生成）。

### 7.3 采样多样性实测（v19 同配置双跑，temp=0）

产物：`eval_dual_v19_d5_qwen_plus_status.jsonl`（剔除跑 322）vs `eval_dual_v19_d5_qwen_plus_status_full.jsonl`（全集跑 329），重叠 322 session / 673 turn-intent case：

| 指标 | 数值 |
|---|---|
| 两跑都对 / 都错 | 487 (72.4%) / 136 (20.2%) |
| **翻转（一对一错）** | **50 (7.43%)**，A对B错 30 / A错B对 20（1.4σ 内=硬币噪声） |
| pred 文本不同（含等效差异） | 74 (11.0%) |
| session 级翻转 | 30/322 |
| **oracle pass@2 上限** | +4.46pp（30 turn，需完美选择器） |

翻转特征：①**高度聚集**——同 session 多轮连翻（turn1 翻→prev_state 带错→下游连翻），独立翻转源头 ~30；②**字段集中 rtt 28 / resolution 27 / 时间三字段 63**——画像/推算来源值，选择器同样无信号判别对错；③模板分布弥散（T3-4×10 最多，无单一集中点）。

### 7.4 BoN+投票的定量封死

**投票的数学本质 = 估计答案分布的众数；贪婪解码（temp=0）输出 ≈ 众数。** 投票要有效须满足"正确是全局众数但贪婪中途出轨"，这在长 CoT 常见、在每轮 10~20 行短程序生成中不成立（FMT=0）。

定量：翻转 turn 即正确率 p≈0.5 的边缘 turn。N=3 多数投票命中 f(p)=3p²−2p³，**净增益 f(p)−p**：p=0.5→0；p=0.6→+0.048；p=0.7→+0.084。50 个翻转 turn 期望增益 **+0~4 turn ≈ 0~0.6pp，全在噪声带（±1.5~2pp）内**，且需 3~5× 推理成本。

结构性障碍：①整程序投票平票常态（11% 文本已不同）；②逐字段投票重组破坏字段耦合（start/end/duration 知二推一、intent key↔app/service）= v9.0 选择器失败路线重演；③翻转值分布是**多稳定吸引子竞争**（画像习惯值 vs records[0] vs records[1]），非"正确收敛错误发散"，投票仍选出贪婪已选的吸引子。

**BoN+verifier 与投票的本质区别：排序信号来自模型分布之外。** 唯一能兑现 oracle 上限的形态是 Best-of-N + 训练独立 verifier（GenRM/CorrectionLM 路线；历史 eval 标注可作训练数据），但这是训练判别模型的新投入，非 prompt 技巧。

### 7.5 后续路线与门控（2026-09-01 定）

1. **P1 主线不变**：PER/REA 生成侧喂信息（v24.1 records[0] 规则 +4.97pp 的成功模式），含 S-000369 残余修补（RECORD RULE 段补"prev 其他字段保持不变"）。
2. **P2 零开发先导**：v24.1 双跑模拟完美选择器 oracle 上限（复用 §7.3 对比脚本）。**判据：>3pp 保留 verifier 路线；≤3pp 采样路线整体关闭**。
3. **P3 validator 信号分层**：只对确定性矛盾出信号（话语明说值 vs 输出值冲突、时间三字段互斥、画质表冲突），区分"正常未收齐 vs 真实错误"——一切核验/反思形态的地基，同时提升 err_report 归因精度。
4. **P4 条件触发**：窄版"对照原文冲突检查"（依赖 P3）：只报冲突不补全、窄触发（话语含显式数值/画质词）、bounded 1 轮、config 默认关、双跑取均值。靶区估 20~40 turn，预期 +0.9~2.9pp，噪声带边缘。

**P2/P3/P4 执行结果（2026-09-02，CHANGELOG v24.5/v25.0）**：
- **P2 门控**：v24.1 双跑 run2 81.37%/83.94%，翻转 28/673（4.16%），**oracle pass@2 = +1.63pp ≤ 3pp → BoN+verifier 路线关闭，采样类路线（投票/verifier/TTRL）整体封死**。
- **P3 落地**：validator `signal_level`（hard/soft 分层）+ `detect_utterance_conflicts`（值-意图近邻绑定、上下界跳过、改口轮跳过、日期只比 start）。初版离线误报 88/106，逐项收窄后 4 触发全真靶 0 误报。
- **P4 实证（v25.0，双跑）**：78.88%/82.70% vs 基线 80.75%/83.51%，双跑一致净负回退；反思日志触发 10/采纳 1（采纳例还是选择疑问句陷阱），9/10 模型拒绝改动。**靶区上限 +0.6pp < 噪声带，校验信号驱动机制信号区结构性太小——§6、v23.2、v25.0 三次同构结论，反思/采样路线全线关闭，不再重提。**

**明确不做**：BoN+投票；纯自评/"are you sure"式反思（sycophancy 翻转风险）；always-on 全局规则（v22.0③）；后处理选值（v5.0/v9.0）；调温换多样性（无 verifier 无法兑现）。

## 8. 实验执行日志（v25.0 全链路存档，2026-09-02）

> 本节为 §7.5 P2/P3/P4 门控计划的执行留痕；结论已在 P2/P3/P4 框内，此处记录实验过程数据与代码落点，供复现。

### 8.1 代码落点（config conflict_check.enabled，qmap 最佳配置默认 false）

- `validator.py`：`signal_level()`（hard=time_inconsistent/time_out_of_range/slot_value_invalid/matrix_out_*/unresolved_coreference；soft=missing_required_slot——"正常未收齐"不再是错误信号）；`detect_utterance_conflicts(utterance, flats)`（rtt/resolution/duration 含中文数词/"N个半小时"/日期只比 start 防跨日误报；值-意图近邻绑定 ±40 字符最近前方 app、窗口内双 app 放弃；上下界前后缀跳过；改口/否定轮整轮跳过）。
- `pipeline.py`：`run_multi` 尾部 `_conflict_check_round`——冲突明细+上一次输出回灌 prompt 重生成 1 轮（bounded），逐意图采纳（conflicts2 为空才替换，非冲突意图保持第一次输出）。
- `session_eval.py`：turn_outputs 新增 `reflection` 字段（触发 turn 的冲突明细+采纳清单，err_report 可视化）。
- 消融配置：`configs/config_d5_qwen_plus_ccheck.json`（enabled=true，已回退不入最佳配置）。

### 8.2 触发器精度迭代（离线扫描 v24.1 run1，322 剔除口径）

| 版本 | 触发 | 误报 | 收窄手段 |
|---|---|---|---|
| 初版 | 106 | 88（83%） | — |
| +值-意图近邻绑定 | — | −40 | ±40 字符最近前方 app，双 app 放弃 |
| +上下界跳过 | — | −20 | "不超过50ms"/"20毫秒以内" |
| +改口轮跳过 | — | −18 | 新旧值同在必误报（改口轮已由 v22 correction_rule 覆盖） |
| +中文数词/时段归一 | — | −6 | "一个半小时"误算 30min |
| **终版** | **4** | **0** | 4 触发全真靶 |

### 8.3 全量双跑结果（v25.0 ccheck vs v24.1 基线，同条件）

| 配置 | session | turn | 状态机 |
|---|---|---|---|
| v24.1 run1 / run2 | 80.12% / 81.37% | 83.07% / 83.94% | 81.27% / 81.57% |
| v25 ccheck run1 / run2 | 78.88% / 78.88% | 82.34% / 83.36% | 81.12% / 81.42% |

- 双跑均值 session −1.86pp / turn −0.80pp，双跑一致净负 → 触发预设判负条件，回退。
- 反思日志：全量触发 10 turn / 采纳仅 1（S-000328 rtt 100→20ms——但 GT 是画像记录值，话语"设20ms还是按上次记录"是选择疑问句，20ms 是被选项，错→错）；9/10 模型拒绝改动（2412.19513 置信侧占优）。
- session 级差 258→254 与反思无关（采纳仅 1 例），是纯运行噪声。

### 8.4 靶区测算

离线真靶区 4 turn ≈ +0.6pp（即使全修也低于 ±1.5pp 噪声带）——与 §6、v23.2（+0.62pp）第三次同构：**校验信号驱动类机制在本数据集的信号区结构性太小**。

### 8.5 教训（进经验库 docs/experience/05）

1. 离线触发扫描必须先于全量跑（初版 88 误报若直接跑全量即 v22.0③ 重演）；
2. "话语明说值"在有画像记录兜底的场景语义不可靠（选择疑问句/被选项/上限表述），字面比对天花板即此；
3. 模型面对"校验器说你错"以拒绝为主——置信/批判冲突实证；
4. 同一套校验信号（signal_level/detect_utterance_conflicts）后续复用于 v26.2 身份判定 PAR 校验与 err_report 归因——**信号用于生成前决策有效，用于生成后纠错无效**。

### 8.6 产物

`eval_dual_v25_ccheck(.jsonl/_summary/_errors.html)`（run1）、`eval_dual_v25_ccheck_run2(.jsonl/_summary/_errors.html)`（run2，含 reflection 日志）、冒烟 ×3；消融配置 `configs/config_d5_qwen_plus_ccheck.json`。
