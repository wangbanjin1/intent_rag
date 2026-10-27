# qwen_intent_rag 项目总览

> 更新：2026-10-21 ｜ 当前数据集：**dataset_6_clean**（237 sessions;d5 819 训练/主数据集）+ **dataset_930**（4455 sessions 新一代泛化集,见 [版本谱系](dataset_versions.md)）｜ 当前方法栈 **v28.4**（候选链路语义任务全部 LLM 化：选择轮判定 judge 三枚举 action/pick/scope 语义判别 + 候选列表属性匹配 resolve_listing（规则快路径→LLM 兜底）+ 列举收回 harness 直驱 + 路线乙身份剥离；含 v27.3.x 候选组装并集/时间桩兜底/嵌套解析修复、v27.2.1 QUALITY HINT、v27.2 候选模式、v27.0 时间锚点/证据/四规则，v26.3 基础上）（id 协议 + candidate_map + pending_app_rule v3 文案 + multi_instance_key 实例化），**代码形态 v26.4**（入库重构）+ v27.0 time_extractor.py（时间规则提取器）｜ d6 配置 `configs/config_d6_qwen_plus_best.json`，**mi_on 口径** session **97.86%**（四跑定案：plus 97.44/98.29；qwen3.8-27b 96.79% 二次坐实平替）/turn **97.88%**（97.98/97.78）（状态机 91.39%；vs v27.2 +4.70pp/+3.13pp、累计 vs v26.3-p3 +9.83pp；T3-3 10 例 plus 19/20、q38 20/20；候选管线零稳错，稳错 3 全在 X-2/T2-2/T1-3）（dup-key 6/6 干净判分）｜ **d930 泛化集定案（444×2 互斥子集剔噪交叉验证合并 871 抽样,v28.4）：qwen3.8-27b session 94.60%/turn 95.12%**（s444 94.95/s444b 94.25,反超 plus 92.20%,平替三次复现）｜ d5 最佳仍为 v26.2b 88.67%/88.83%，详见 CHANGELOG

多轮多意图**网络质量保障客服**的对话状态跟踪（DST）系统。论文驱动：RefPyDST (B1) Text-to-Python 范式 + IDIC-DST (B2) 多意图并发/画像增强 + S3-DST PAR。

## 当前水平（当前最佳 = **v27.3.2 @ d6 / v28.4 @ d930**；下表 dataset_5 为训练/主数据集口径，逐轮真实透传，取消计分 v17.1 + 改口豁免 v20.2 + GT 知二推一 v25.1）

**d6 泛化集（剔 3 问题样本 = 234 参评，mi_on 口径，`config_d6_qwen_plus_best.json`）**：

| 配置 | session 级 JGA | turn 级 JGA | 状态机 |
|---|---|---|---|
| **v27.3.2（候选组装+锚点链+时间桩兜底+_fv 修复，当前最佳；plus 四跑定案）** | **97.86%**（97.44/98.29） | **97.88%**（97.98/97.78） | **91.39%**（91.58/91.21） |
| **d930 泛化集（4455 sessions 新一代,444×2 互斥子集剔噪交叉验证合并 871 抽样;qwen3.8-27b v28.4,含未定词槽位判定+TIME RULE 组装分叉+resolve_listing 属性匹配）** | **94.60%**（94.95/94.25） | **95.12%**（95.51/94.71） | 91.29%/90.25% |
| v27.3.2 qwen3.8-27b（同开关平替验证，双跑均值） | 96.79%（97.44/96.15） | 97.58%（97.98/97.17） | 91.21%（91.58/90.84） |
| v27.3（含 _fv bug，被 v27.3.2 取代） | 95.09%（95.30/94.87） | 96.36%（96.97/95.76） | 90.29% |
| v27.2.1（v27.2 三跑均值，v27.3 前最佳） | 93.16%（94.87/91.45/93.16） | 94.75%（95.96/93.74/94.55） | 87.79% |
| v27.2 迭代链 | run1 94.87% → run2 91.45% → run3（harness 分支修复）93.16% | 95.96 → 93.74 → 94.55 | 88.28 → 87.36 → 87.73 |
| **v27.0（六时间开关全开，v27.2 前最佳）** | **92.74% (217/234)** | **93.54% (463/495)** | **86.08%** |
| v27.0 迭代链 | run1 81.20%（大回退）→ fix_run1 91.88% → fix2 91.88% | 84.24% → 93.13% → 92.93% | 79.30% → 85.90% → 86.08% |
| v26.3-p3（v27 前最佳） | 88.03% (206/234) | 89.90% (445/495) | 86.63% |
| v26.3-p1 candmap+pendapp | 87.18% | 88.48% | — |
| v26.2c 基线（d5 检索库直迁） | 84.19% | 87.27% | 85.16% |

**d5 训练集（剔除 7 噪声 322，双跑均值；历史最佳 = v26.2b，v27 未在 d5 重跑）**：

| 口径（qwen-plus + agent_context + status_prompt + d5 画质表 + 消歧规则 + 改口规则 + 记录严格规则 + v26.2b id 协议） | session 级 JGA | turn 级 JGA | 意图状态机 |
|---|---|---|---|
| **v26.2b（d5 历史最佳）** | **88.67% 双跑均值**（285/322=88.51%、286/322=88.82%，极差 0.31pp） | **88.83% 双跑均值**（603/685、614/685） | **85.73%** |
| 对照 v24.1（无 harness 修复，其余全同） | 80.12% (258/322) | 84.67% (580/685) | 81.27% |
| 对照 v19.0（legacy 画质表，其余全同） | 72.05% (232/322) | 75.47% (517/685) | 81.27% |
| 对照 v22.1（无记录规则，其余全同） | 75.16% (242/322) | 78.83% (540/685) | 81.57% |

- v20.0 画质映射表重建（+2.17pp）：从 d5 train 的 GT source_ref 还原规范表（60 条零冲突），修复旧表缺的 腾讯视频/视频、爱奇艺/视频 高清=540p 等条目；config `quality_map_version`（`"d5"` 默认/`"legacy"` 回退）。
- 取消计分口径（方案 b，v17.1 起默认）：取消轮 turn 级 GT 记"应为空"，正确取消计命中；missed_intent 56→36，`cancel_wrong` 仅 2 例（模型几乎总能正确取消）。d4 同口径复算 74.27%，跨版本 turn 比较须同口径。
- 改口错误值轮豁免口径（correction_credit，v20.2 起默认）：T2-3 轮1/T3-2 轮2 GT 记字面错误值（设计口径不动），评估侧豁免——模型拒收（pred 空）或记字面值均计命中，其余字段照常比对。v20.0 复算 turn +2.04pp（14 转正/7 仍错，仍错全是真实错误）。d5 上新旧 turn 口径差约 +2.0pp。
- turn 级 GT 知二推一口径（gt_derive，v25.1 起默认）：GT 标注只在"字面明说"轮登记字段，turn 级中间态 29 处（test 11，全 X-2 duration）缺可推算字段；模型侧 validator 每轮强制知二推一、GT 最终态三字段必齐，模型正确推导反被判错。新口径 = turn GT 比对前同规则补齐（只补缺失）。v24.1 双跑复算 turn +1.61pp/+1.61pp（+11 转正/0 翻错）；session 级不变。`--no_gt_derive` 回退。反向"关模型推导"不可行（判对 turn 16.5% 靠推导）。
- 噪声：find_noise 重扫 d5 有 13 条 R5 系 record 噪声（7 在 test → 剔除口径 322；清单 `data/_noise_exclude_d5_scan.json`，`--noise_file` 指定）。
- 历史：dataset_4 上 v16.0 70.36%/68.44%（旧口径，新口径复算 74.27%）；dataset_merged_2.0 上 68.91%/67.65%；merged 1.0 上 v14.0 73.93%/71.22%。各数据集 test 构成不同，不直接可比。
- v13.0 起新增意图状态机口径（Collecting/Ready/Confirmed/Cancelled，纯旁路四个消融开关），详见 [report_flow.md](report_flow.md)。
- v24.1 上次记录严格规则（**+4.97pp/+4.24pp，项目最大单步收益**，采纳）：用户确认修复后数据集的规则——无约束"上次"严格 = completed_at 最新一条 records[0]。触发式注入 records[0] 字段明细 + 三条纪律；约束识别为"紧邻记录词"结构（v24.0 把话语中任何 app 名误当筛选条件致 PER 原型零覆盖）；排除 T3-3 预选/查询式/日期锚定轮（GT 悬空待消歧）。获救 25/回退 9，触发∩获救=18、触发∩回退=1，强因果。
- v21.1 T3-3 消歧规则（+0.62pp session，采纳）：agent 列候选记录→用户选择轮注入规则（候选 app/service 禁作输出、沿用现有意图头、序数按 agent 话术顺序对齐、禁止覆盖 prev 已有字段），T3-3 破零 0→7/14；触发检测三条件窄触发，test 其他模板零误伤。config `disambiguation_rule.enabled`（v21.0 初版因序数对齐缺陷净负，v21.1 修正后转正，详见 CHANGELOG v21.0/v21.1）。
- v23 time.py 时间规则移植（净负回退）：外部规则库（口语时间/时长归一化）经 GT 不变性回归门控后（3388/3388 零 GT 破坏）接入 pipeline 后处理，全量 v23.0 73.60%/77.66%、v23.1 72.67%/77.08% 均低于基线。归因：规则对真实预测 ≈ 零净作用（回放 1修/0破），波动来自 API 非确定性（temp=0 下 0 处差异可由规则解释、19 处为模型侧差异）。`time_rules.enabled` 默认关；方法论沉淀见 CHANGELOG v23。
- v22.1 改口轮规则（+0.31pp/+1.46pp 双正，采纳）：改口词+prev 非空触发注入——intent 头沿用 prev 旧 key、被更正 app/service 段内赋新值、其余字段不得丢；动机案例 S-000318（key 割裂）获救，T2-3 turn 75.0%/T3-2 turn 78.0%，COR 标签 9→4。③record 不超前取值（always-on 全局规则）合并实验中因保守化副作用（确认轮丢意图、明说字段留空）净有害已回退，教训同 v8.0-a：全局 prompt 变动扰动非目标类；后续若重试应改触发式。
- v25.2 harness 意图对象语义修复（**+6.52pp/+3.35pp 双跑均值，项目最大单步收益**，采纳）：①key 从合并后 final_state 推导（改口单字段不再裂 key）；②`_match_prev_key` 无 app 归并链（单 prev 无 app 必归并/同 svc 唯一归并——补槽轮 header 写全也能找到 prev)；③consumed key 与取消同路退役（幻影意图 26→0）。获救 30/32、双跑一致回退仅 1（模型波动非机制致退）；T2-1 turn +19.4pp、T3-4 +16.7pp、T3-3 +12.9pp。教训：v24.1 时代的 missed_intent 被归因模型能力短板，实为 key 断裂假象——同类结构问题先查 harness。
- 四模型对照（v26.2c，2026-09-09）：同配置下 **qwen3.8-27b 双跑 89.91%/90.29% 反超 plus（88.67%/88.83%，稳对 282 vs 275 净 +7）→ 当前单模型最高分**；沙箱逐行容错+"只写变化字段"两项弱模型适配对 235b +20.11pp/32b +12.11pp/q38 +11.96pp（红利与模型强度反序），模型档差距大幅收窄（plus−235b 26pp→7.3pp）。模型锚点：plus ≈"新一代 27B 指令特化档"。
- v26.2b id 协议补丁链（**+1.39pp/+0.37pp，采纳转正**）：v26.0 裸协议（身份用稳定 id 解耦，遵从 100% 但 −3pp）→ 三轮"回退根因→补丁"迭代：①new 归并校验（无 app 同 svc 互补→prev 填空槽+残骸退役）②cancel 名字化（名字自校验优于任意 id）③身份判定强制化（PAR 式生成前推理，段头前强制 `# 身份判定:` 行）④full-match new 重采样门⑤段去重。**引用解析最终形态 = 模型显式指派(id/名字) + harness 推断兜底(归并/重采样门) + 全场去重审查三层混合**；身份判定收益在"强制写理由"而非"换介质"（v11 选 record PAR 的同构复现）。方法论教训：含 bug 的采样必须从统计中剔除（v26.2 段覆盖 bug 曾污染多数票统计）。
- 上限判断：v26.2b 后剩余错误主线 = REA（多意图时间/参数绑定、跨日推算）、T1-3 同头多意图单轮懒省略（S-000392 型波动）、T3-3 消歧记录值对齐残余、prompt 变长的继承波动尾巴（15 个 v25.2 稳对翻转）。
- v24.5 v24.1 双跑 oracle 模拟（测量）：run2 81.37%/83.94%；翻转 28/673（4.16%），**oracle pass@2 上限 +1.63pp ≤ 3pp 门控 → 采样路线（BoN+投票/verifier）整体关闭**（工具 `_oracle_pass2.py`）。
- v25.0 窄版冲突检查反思（双跑净负回退）：validator 信号分层 + "话语明说值 vs 输出值"确定性冲突触发定向重生成（只报冲突不补全/bounded 1 轮/逐意图采纳/默认关）。触发器收窄后离线 4 触发全真靶 0 误报；双跑 78.88%/82.70% vs 基线 80.75%/83.51%（−1.86pp/−0.80pp，判负回退）；反思日志 触发 10/采纳 1（采纳例还是选择疑问句陷阱），9/10 模型拒绝改动。**校验信号驱动机制信号区结构性太小（上限 +0.6pp），与 v23.2/§6 第三次同构结论；反思/采样路线全线关闭，剩余主线 PER/REA 生成侧。**
- v27.0 时间锚点+明说时间证据注入（2026-09-20，**采纳定案 session 92.74%/turn 93.54%/状态机 86.08%（+4.71pp/+3.64pp），time_wrong 19→5**）：d6 时间错误六类形态驱动（相对日期 off-by-one/明说被 record 覆盖/矛盾口径被调和/画像时段时长冲突/候选未定边界/跨意图传播）；**规则提取器（time_extractor.py）+ 预解析 + 触发型注入**，否决后处理纠错（v23/v9.0 封死）与 LLM 改写轮（误差上移）；论文支持 arXiv:2505.16088/2601.07148/2511.09993/2507.06450/2505.20243；时间散文段压缩对冲变长（系统 prompt 净 −840 字符）；迭代链 run1 81.20%（区间终点省略时段词大回退）→ fix_run1 91.88% → fix2 → **fix3 采纳**；回退 6 经基线五跑核查无机制性。方案 [plan_time_anchor_20260920.md](plan_time_anchor_20260920.md)；经验沉淀 [experience/11_time_rules.md](experience/11_time_rules.md)。

## 目录结构（2026-08-24 整理）

```
qwen_intent_rag/
├── docs/            # 报告文档（本文件/方案/实验/流程/CHANGELOG）
├── configs/         # 消融配置（config_abl_*.json，主配置 config_merged.json 在根目录）
├── data/
│   ├── evals/       # 评估产物（eval_*.jsonl/_summary.json/_errors.html，新评估默认写入此目录）
│   ├── _archive/    # 历史日志与临时分析文件
│   └── dataset*/ + split/case_bank/embed_cache/_noise_exclude.json（代码引用，勿动）
└── *.py             # 代码（session_eval 主评估入口）
```

## 文档导航

| 文档 | 内容 |
|---|---|
| **[report_method.md](report_method.md)** | 方案分析：论文依据与采纳判断、系统架构、方法演进（v1→v26.2b 全量门控方法均入表）、失败教训、数据集规则发现 |
| **[report_experiments.md](report_experiments.md)** | 实验结果：实验设置、全版本结果总表、当前最佳详细结果、错误样本分析、瓶颈与上限、产物清单 |
| **[report_eval.md](report_eval.md)** | **评估口径专册**：session/turn/状态机三口径定义、三层口径修正（取消计分/改口豁免/GT 知二推一）完整记录、跨口径换算表、常见误读 FAQ |
| **[story.md](story.md)** | **汇报版演进讲解**：全部重要改动的形象化机制说明+真实案例前后对比+记分牌，非关键细节一笔带过（2026-09-22） |
| **[report_flow.md](report_flow.md)** | 全流程说明（意图状态机）：输入→上下文编译→生成→执行→状态推导→输出，端到端实例 + **全量门控清单（12 开关，含默认关的方法）** |
| **[codeguide.md](codeguide.md)** | **代码结构和使用文档**：模块地图/数据流/常用命令/意图身份机制/扩展指引/质量纪律清单 |
| **[experience/](experience/00_总览索引.md)** | **经验总结文件夹（11 篇）**：实验方法论/评估口径/引用解析/Prompt 工程/反思校验/检索画像/数据集工程/状态机/弱模型适配/**时间规则**——按主题沉淀全部实证经验，供同类项目参考 |
| [dataset_versions.md](dataset_versions.md) | 数据集版本谱系（当前 dataset_5：规模/模板分布/ID 重叠/噪声清单） |
| [report_papers_crosscheck.md](report_papers_crosscheck.md) | 论文交叉审视（A1/A2/A3/B6/C2/C4/E1/E2）：已论证结论 ↔ 项目实证、可参考项、不适用项（2026-08-27） |
| [report_reflection.md](report_reflection.md) | 反思机制调研与方案（2026-08-28）：Reflexion/Self-Refine/CRITIC/Self-Debugging 选型对比；§6/§7 三次同构证伪 + v25.0 窄版冲突检查实证回退；**§8 实验执行日志（触发器精度迭代/双跑数据/反思日志/代码落点，2026-09-02）** |
| [CHANGELOG.md](CHANGELOG.md) | 逐版本变更归档（每版的改动细节/数字/根因） |
| `data/evals/eval_dual_v27_time_fix3_errors.html` | **当前最佳（v27.0 六时间开关 @ d6，92.74%/93.54%）的错误样本可视化报告**（浏览器直接打开）；历史最佳 v26.3-p3 见 `eval_dual_v263p3_d6_best_run5_errors.html` |
| `data/evals/eval_dual_v26_2b_errors.html` | d5 最佳（v26.2b id 协议）的错误样本可视化报告 |

## 快速复现

```bash
cd qwen_intent_rag
export BAILIAN_API_KEY=...            # 见 keys.txt
export DST_DATASET=data/dataset_5
# 全量评估 + 自动生成错误可视化报告
# d5 当前最佳 = configs/config_d5_qwen_plus_idproto.json；d6 当前最佳 = config_d6_qwen_plus_best.json（v27.0 六时间开关全开）
python session_eval.py --config configs/config_d5_qwen_plus_idproto.json --split data/split_d5.json \
    --noise_file data/_noise_exclude_d5_scan.json --tag <版本tag> --err_report
# d6 泛化集复现 v27.0 最佳（三件套必须同设）
DST_DATASET=data/dataset_6_clean python session_eval.py --config configs/config_d6_qwen_plus_best.json \
    --split data/split_d6_full.json --noise_file data/_noise_exclude_d6_scan.json --tag d6_v27 --err_report
# 已有结果单独补错误报告
python error_report.py --eval data/evals/eval_dual_<tag>.jsonl
# 数据集噪声扫描（更新数据集后重跑刷新清单）
python find_noise.py --dataset data/dataset_5 --split data/split_d5.json --all --out data/_noise_exclude_d5_scan.json
```
