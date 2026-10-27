# T3-3 候选模式方案（2026-09-23，v27.0 错误驱动，替代 plan_t33_record_landing 初版）

## 0. 用户定案的核心洞察

复盘 S-000040 确认完整失效链：t1 模型在两条候选（爱奇艺/拼多多，均满足"6月29日+看直播"）中自作主张"取最近"new 了拼多多 → 爱奇艺意图从未存在 → t2 用户选爱奇艺时模型只能在错误的拼多多意图上补参数 → t3 确认轮被 pending-app 规则锁死输出 none → 整 case 挂。

**正确形态（用户提出）**：候选列举本身必须是模型输出的一部分——
- **t1**：把约束命中的**全部记录**列为候选（标记 candidate，而非 new）
- **t2**：按用户选择 **select**（转正）/ **cancel**（移除未选候选）
- **t3**：**confirm**

动机不止修判分：后续系统形态是"模型输出驱动客服回复"——t1 若不输出候选，下游回复生成器就无法产出 golden answer 那样的消歧问句。候选列举是信息流的一环，不是判分技巧。

## 1. GT 分型核验（13 个 T3-3 全量）

| 型 | 数量 | t1 GT 字段 | t2 选择语 | candidate 方案映射 |
|---|---|---|---|---|
| **A 纯查询型** | 10（040/072/089/106/140/157/174/210/228/246） | 只有 timestamp（app 悬空） | "第N条/第一条不要" | t1: I1(明说时间) + candidate×N（记录全字段）；t2: 选中候选的字段合并进 I1 + 其余候选 cancel |
| **B 明说型** | 3（056/123/192） | app/svc/res/timestamp 齐全，只悬空 rtt | "选第N条" | t1: I1（完整身份，rtt 悬空）+ candidate×N（仅 rtt 来源）；t2: 选中候选的 rtt 落入 I1 |

评估对齐验证：A 型 t1 若模型输出 I1(时间)+2 个 candidate，候选不入 pred → 累积态={I1:时间} 与 GT 完全一致 ✓；t2 select 后 I1=时间+爱奇艺全字段 = GT t2 ✓。**候选旁路化后判分与 GT 天然对齐**。

## 2. 机制设计

### 2.1 新段类型 `# candidate: <app>/<svc>`（协议扩展）
```
# summary: 6月29日看直播记录命中2条, 列出候选供用户选择
# candidate: 爱奇艺/看直播
state.resolution = "1080p"
state.rtt = "150ms"
state.candidate_ref = "R-U0041-001"

# candidate: 拼多多/看直播
state.resolution = "1080p"
state.rtt = "20ms"
state.candidate_ref = "R-U0041-002"
```
- harness 侧**候选账本**（CandidateBook）：记录候选的 app/svc/字段/ref，**不入 JGA 主链**（类似 __cancel__ 旁路）
- 评估豁免：candidate 段不进 pred 集合、不参与 turn/session 判分（错误报告可视化展示）
- 触发条件（消歧会话信号域内，复用 v27.1 的 session_disambig）：记录指代 + 约束命中 ≥2 → 提示模型列候选（候选明细由 record_selector 判定树从画像预计算注入，含 record_id 引用）
- 命中 =1 时无需候选模式（现有 record_rule 路径）

### 2.2 select/cancel 语义（t2）
- 用户"第N条/那条"→ 对齐客服话术序（agent_context 提供话术）→ 选中候选**解引用落地**：
  - A 型：选中候选的 app/svc/res/rtt 写入 I1（桩意图），其余候选 `# cancel: candidate` 移除
  - B 型：选中候选的悬空字段（rtt）落入 I1，其余候选 cancel
- 段头写法：选中候选落地走 `# intent: I1`（桩已存在）或 `# intent: new`（无桩）；未选候选 `# cancel: 爱奇艺/看直播`(候选身份)——harness 识别 CandidateBook 移除
### 2.3 confirm（t3）—— 现有 confirm_rule 已覆盖，无改动

## 3. 与 v27.1 遗产的关系
- defer 悬空规则保留（t1 不取值的口径依据）
- 消歧 4.5 补值条款 → 被 select 落地语义取代（保留兼容）
- record_selector 判定树保留（候选明细预计算的引擎），EVIDENCE 定位类维持关闭

## 4. 评估侧改动
- session_eval/turn_gt：candidate 段豁免（不入 pred/不算 missed）；gt_build 不变（GT 无候选概念）
- error_report：候选段可视化（展示"模型列举了哪些候选"供人工核验列举完备性）
- mi_on 实例化：CandidateBook 独立于 IntentIdBook，无冲突

## 5. 验证计划
1. 协议单测：candidate 段解析/账本/豁免/select 落地/cancel 移除（含 A/B 两型各一端到端 mock）
2. 回放：13 个 T3-3 全量 + T2-3/T2-6 对照（消歧规则共用面，盯机制桶）
3. 全量单跑：超 ±2pp 且净救>净破 → 双跑定案
4. 文档：CHANGELOG v27.2 / 三册 / story / 经验

## 6. 风险与边界
- **协议扩展面大**：新段类型涉及 parse/沙箱/账本/评估/报告五处——但都是旁路新增，主链零改动
- **模型遵从风险**：candidate 是全新段类型，需在 system prompt 教格式 + 消歧会话信号域内注入示例；模型不输出候选时回退现状（defer 已保底）
- **候选完备性依赖 record_selector 判定树**（约束提取错误→漏列）——判定树 GT 核验 79/81 已达标
- B 型候选只有 rtt 一个有效字段，candidate 段近似冗余——可只在 A 型触发（t1 明说 app 时不进候选模式），缩小扰动面
