# qwen_intent_rag

多轮多意图**网络质量保障客服**的对话状态跟踪（DST）系统（代码 v27.2，方法栈 v27.0+候选模式；入库包由 `python tools/package.py` 生成）。论文驱动实现：

- **RefPyDST (arXiv:2307.01453)**：Text-to-Python 状态表示，LLM 输出对 `state` 赋值的 Python 程序表达状态变更 Δy，受限沙箱执行，`y_t = apply(y_{t-1}, Δy_t)` 逐轮透传；MMR 多样性检索 demonstrations
- **IDIC-DST (B2)**：多意图并发（一次 LLM 调用输出多个 intent 程序段）+ 用户画像增强（profile / 历史保障 records 的指代消解）
- **S3-DST (arXiv:2309.08827)**：PAR（Pre-Analytical Recollection，每个 intent 段前强制 `# summary` 回顾证据）+ 缺 summary 重采样校验回路
- **意图状态机**：Collecting / Ready / Confirmed / Cancelled 四状态旁路跟踪与评估
- **触发式规则注入**（v21–v24）：消歧轮规则、改口轮规则、无约束"按上次记录"严格规则，窄触发后注入 prompt 规则段
- **intent-id 协议（v26）**：意图身份用稳定 id（I1/I2…）与内容 key 解耦，段头 `# intent: I1`/`new`，身份判定强制行；harness 侧 new 归并校验、cancel 名字化、段去重三层兜底
- **弱模型适配（v26.2c）**：沙箱逐行容错（coreference 失败跳行不毁段）+ "只写变化字段" prompt 约束
- **应用名待补规则（v26.3-p1）**：话语未明说 app 时禁止从记录/矩阵猜测，默认挂靠已有意图（桩→归并补全），消除补槽轮幻影意图
- **同 key 多意图实例化（v26.3-p3）**：同应用同服务的多份保障各自独立判分（`app/svc#N` 内部实例键），取消定位支持 id/时段/序数消歧
- **时间锚点+明说时间证据注入（v27.0）**：`time_extractor.py` 规则提取器预解析明说时间（日期表/时段继承/跨夜滚动/矛盾与候选检测），TIME ANCHOR/EVIDENCE/四条 TIME RULE 触发式注入 prompt（计算给规则、理解给模型）；时间散文段压缩对冲变长（系统 prompt 净 −840 字符）；**严禁后处理覆盖 pred**（v23 教训）

**dataset_6_clean（当前主战场）最佳 = v27.2：session 93.16%（三跑均值）/ turn 94.75% / 状态机 87.79%（candidate_mode 开，mi_on 口径，vs v27.0 +0.42pp/+1.21pp）**。

## d5 训练集历史水平（v26.2b 配置，逐轮真实透传，剔除 7 噪声 = 322 test，双跑均值）

| 模型 | session 级 JGA | turn 级 JGA | 状态机 |
|---|---|---|---|
| **qwen3.8-27b** | **89.91%** (89.13/90.68) | **90.29%** | 85.13% |
| qwen-plus | 88.67% (88.51/88.82) | 88.83% | 85.73% |
| qwen3-235b-a22b-instruct-2507 | 82.61% | 83.65% | 83.38% |
| qwen3-32b | 73.29% | 77.37% | 80.06% |

评估口径含三层修正（取消计分 v17.1 / 改口豁免 v20.2 / GT 知二推一 v25.1），详见 `docs/report_eval.md`。

**跨数据集泛化（dataset_6_clean，237 sessions 泛化性测试集，qwen-plus）**：

| 配置 | session 级 | turn 级 | 说明 |
|---|---|---|---|
| v26.2c 基线 | 84.19% | 87.27% | d5 检索库零适配直迁 |
| + candidate_map | 87.18% | 88.48% | T3-3 消歧洼地修复 |
| v26.3（candmap + pending_app_rule + multi_instance_key） | 88.03% | 89.90% | mi_on 口径（判分更严：塌缩假阴性消除，dup-key 6/6 干净判分） |
| **v27.0 fix3（+ 六时间开关：日期表/证据/矛盾保字面/画像时段优先/明说>record/候选不落槽，当前最佳）** | **92.74% (217/234)** | **93.54% (463/495)** | **d6 最佳**（vs v26.3 +4.71pp/+3.64pp，time_wrong 19→5；迭代链 run1 81.20%→fix_run1 91.88%→fix2→fix3；回退 6 经基线五跑核查无机制性回退） |
| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，三跑均值）** | **93.16%**（94.87/91.45/93.16） | **94.75%**（95.96/93.74/94.55） | **d6 最佳**（vs v27.0 +0.42pp/+1.21pp；稳救 4（T3-3 靶区）稳破 3 净+1） |
| **v27.2.1（+ QUALITY HINT 画质条目高亮）** | 定点验证 5/5 修复 | — | 并入 v27.2 采纳（治反常识条目表遵从波动，全 case 平等） |

主配置：`configs/config_d6_qwen_plus_best.json`（v27.2.1 六时间开关+候选模式+quality_hint 全开；d5 时代配置 `configs/config_d5_qwen_plus_qmap.json` 保留）。方案与错误归类：`docs/plan_time_anchor_20260920.md`；经验沉淀：`docs/experience/11_time_rules.md`。

## 目录结构

```
├── session_eval.py      # 主评估入口（session+turn 双口径 + 状态机口径，逐轮真实透传）
├── pipeline.py          # 编排：检索 → 触发式规则检测 → 生成 → PAR 校验回路 → 沙箱执行 → 规则校验
├── prompt_builder.py    # prompt 构建（schema/矩阵/画像/demo/规则注入段）
├── python_state.py      # Text-to-Python 沙箱执行、多意图切分、# cancel/# summary 解析
├── intent_status.py     # 意图状态机：状态推导、Affirm/选择疑问规则、GT 轨迹重建
├── schema.py            # 应用×服务合法矩阵 + 槽位定义
├── validator.py         # 规则校验（含时间三字段知二推一）
├── case_format.py       # 原始嵌套 params 的抽取/扁平化/apply
├── retriever.py / retrieval_bank.py / embedder.py / llm_client.py
├── error_report.py      # 错误样本归类分析 + 单文件 HTML 可视化报告
├── make_split.py / build_casebank.py   # train/test 分层划分 / 检索案例库构建
├── features.py / gt_build.py           # 机制开关单一事实源 / turn 级 GT 构建层
├── time_extractor.py                    # v27 时间规则提取器（日期表/明说时间预解析，生成侧注入）
├── main.py / selftest.py               # 端到端 demo / 离线自检（不调 LLM）
├── tools/package.py                    # 入库打包脚本（白名单+自检断言）
├── tools/               # 离线分析与回放脚本（规则回放、GT 不变性回归、口径复算等）
├── configs/             # dataset_5 时代的评估/消融配置
├── docs/                # 全部报告文档（总览/方案/实验/流程/CHANGELOG）
└── data/                # dataset_5、split、case_bank、噪声清单（评估产物见 .gitignore）
```

## 快速开始

```bash
pip install -r requirements.txt
export BAILIAN_API_KEY=<your-dashscope-key>   # 通义百炼（qwen-plus + text-embedding-v3）

# 离线自检（不调 API）
python selftest.py

# 全量评估（qwen-plus 当前最佳；--err_report 生成错误可视化 HTML；默认剔噪+三口径修正）
python session_eval.py --config configs/config_d5_qwen_plus_idproto.json --tag my_run --err_report

# dataset_6 泛化性评估（当前最佳配置；换数据集三件套必须一起设）
DST_DATASET=data/dataset_6_clean python session_eval.py --config configs/config_d6_qwen_plus_best.json --split data/split_d6_full.json --noise_file data/_noise_exclude_d6_scan.json --tag d6_run --err_report

# 其他模型档（qwen3.8-27b 为当前单模型最高分）
python session_eval.py --config configs/config_d5_qwen38_27b_idproto.json --tag q38_run --err_report

# 消融：编辑 configs/ 副本开关对应段（disambiguation_rule / correction_rule / record_rule /
#       intent_id_protocol / time_anchor / time_extractor / time_rule_* 等），
#       或加 CLI 旗标 --no_cancel_credit 等
```

评估产物默认写入 `data/evals/`（已在 .gitignore 中排除）。首次运行会自动构建 embedding 缓存 `data/embed_cache_d5.json`（需调用 embedding API，之后复用）。

## 评估口径

- 7 字段比对：`application_name, service_name, resolution, rtt, start_timestamp, end_timestamp, duration`
- 域外意图输出空或 invalid 均算对；session 级要求意图集合严格全等
- `cancel_credit`（默认开）：取消轮 GT 记"应为空"，正确移除计命中（`--no_cancel_credit` 回退旧口径）
- `correction_credit`（默认开）：改口错误值轮豁免被更正字段（`--no_correction_credit` 回退）
- 噪声剔除：`data/_noise_exclude_d5_scan.json`（`--include_noise` 关闭剔除）

## 文档

| 文档 | 内容 |
|---|---|
| [docs/report.md](docs/report.md) | 项目总览 |
| [docs/report_method.md](docs/report_method.md) | 方案分析：论文依据、系统架构、方法演进、失败教训 |
| [docs/report_experiments.md](docs/report_experiments.md) | 实验结果：全版本总表、错误样本分析、瓶颈与上限 |
| [docs/report_flow.md](docs/report_flow.md) | 意图状态机全流程说明 |
| [docs/report_eval.md](docs/report_eval.md) | 评估口径专册（三口径修正/换算表/FAQ） |
| [docs/codeguide.md](docs/codeguide.md) | 代码结构和使用文档（模块地图/命令/扩展指引） |
| [docs/experience/](docs/experience/00_总览索引.md) | 经验总结（11 篇按主题，含弱模型适配/引用解析/实验方法论） |
| [docs/ai_assisted_rd_sharing.md](docs/ai_assisted_rd_sharing.md) | AI 辅助研发协作模式分享（分工模型/AI 失败模式与拦截点/可持续机制，面向外部分享） |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 逐版本变更归档（v1.0 → v26.2c） |
