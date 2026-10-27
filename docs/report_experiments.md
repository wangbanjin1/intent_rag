# 实验结果报告：设置、结果与错误样本分析

> 更新：2026-08-27 ｜ 姊妹篇：[report_method.md](report_method.md)（方案分析）｜ 逐版细节：[CHANGELOG.md](CHANGELOG.md)

## 1. 实验设置

| 项 | 配置 |
|---|---|
| 数据集 | **dataset_5**（819 sessions，2026-08-27 起，谱系见 [dataset_versions.md](dataset_versions.md)），`users_3000.jsonl` 画像（已拷入 dataset_5 目录） |
| 划分 | `data/split_d5.json`（seed=42，test_ratio=0.4 分层）：train 490（进检索库 `case_bank_train_d5.jsonl`）/ **test 329，同 split 内结果可直接对比** |
| 噪声剔除 | find_noise 重扫 d5 有 13 条 R5 系 record 噪声（7 在 test → 剔除口径 322 / 全集 329 并列）；清单 `data/_noise_exclude_d5_scan.json`（`--noise_file` 指定，旧清单保留） |
| 模型 | **qwen-plus**（百炼 dashscope，temperature=0，max_tokens=2048）；Embedding text-embedding-v3 |
| 检索 | DiverseRetriever（MMR，top_k=5，pool_n=50，α=0.3），query = prev_state‖utterance |
| 输出范式 | python_par（Text-to-Python + 强制 `# summary` PAR）；xml_par 对比后弃用 |

（历史：v7–v14 在 dataset_merged 1.0（882，test 352）、v15 在 dataset_merged_2.0（920，test 368）、v16–v18 在 dataset_4（702，test 280）上评估，见 §2 总表下半。）

### 评估口径（双口径并列，逐轮真实透传）

- **真实透传**：每轮 prev_states 用模型上一轮自己的 final_state（不用 GT），误差累积，复现真实部署。
- **session 级 JGA**：1 session = 1 样本。模型最终意图集合（按 `app/service` 键）与 GT 严格全等，且每意图 7 字段全等。cancelled/rejected 意图不计入 GT。
- **turn 级 JGA**：每个 (轮, 意图) case 比对"截止该轮的累积态"7 字段全等，定位错在哪轮哪个字段。
- **取消计分（方案 b，2026-08-27 起生效，v17.1）**：cancelled 意图在取消轮的 turn 级 GT 记为"应为空"，模型正确移除算命中（旧口径保留历史态，模型正确取消反被判 missed）。v16.0 最佳版复算：turn 68.44% → **74.27%（+5.83pp，34/34 取消 case 全部转正，零回退）**；session 级不变。旧口径可用 `--no_cancel_credit` 恢复。
- **改口错误值轮豁免（correction_credit，2026-08-28 起生效，v20.2）**：T2-3 轮1 / T3-2 轮2 的逐轮 GT 按设计记字面错误值，模型正确拒收非法组合（pred 空）反被判错。新口径：错误值轮 pred 空 = 命中；pred 非空则豁免被更正字段后比对其余 7 字段（记字面值也算对，其余字段错仍判错）。定位信号：用户 action 同含 `Negate`+`Correct_Previous_Input`（d5 全量核验零误命中）。v20.0 复算：turn 75.77% → **77.81%（+2.04pp，14 转正/7 仍错，仍错全是真实错误）**；session 级不变。旧口径可用 `--no_correction_credit` 恢复。
- **turn 级 GT 知二推一（gt_derive，2026-09-03 起生效，v25.1）**：GT 标注只把字段登记在"字面明说"轮次，turn 级中间态有 29 处（test 11，全 X-2 duration）只有 start+end 无 duration；而模型侧 validator 每轮强制知二推一、GT 最终态三字段必齐（session 级 0 缺口），模型在中间轮的正确推导反被判错。新口径：turn 级 GT 比对前先做与 validator 同规则的知二推一（只补缺失不覆盖）。v24.1 双跑复算：turn 83.07%/83.94% → **84.67%/85.55%（均 +1.61pp，+11 转正/0 翻错，双跑转正集合一致，全为 X-2 轮2 duration）**；session 级不变。反向（关模型推导）不可行：判对 turn 的 16.5%（83/502）duration 靠推导。旧口径可用 `--no_gt_derive` 恢复。
- 旧口径（GT prev_state 不累积错误，70.50%）仅作乐观上界参考，不作主口径。
- 主评估器 `session_eval.py`；每次评估后 `error_report.py` 生成错误样本可视化 HTML（`--err_report` 自动触发）。

## 2. 全版本结果总表

**当前数据集 dataset_5**（test 329；† = 剔除 7 噪声口径 322/685；取消计分口径 v17.1 起默认，turn 级与旧口径不可直接比）。

| 版本 | session 级 | turn 级 | 状态 |
|---|---|---|---|
| v19.0 qwen-plus + agent_context + status_prompt | †72.05% (232/322)；全集 70.52% (232/329) | †75.47% (517/685)；全集 73.32% (514/701) | ✅（状态口径 †81.27%；取消计分首次全量生效） |
| v20.0 + d5 画质规范表 | †74.22% (239/322) | †75.77% (519/685)（v20.2 口径复算 77.81% (533/685)） | ✅（状态口径 †81.57%；+2.17pp，获救 14/回退 7） |
| v21.1 + T3-3 消歧规则 | †74.84% (241/322) | 77.37% (530/685)（v20.2 口径离线复算） | ✅（状态口径 80.66%；session +0.62pp，T3-3 破零 0→7/14；v21.0 初版规则因序数对齐缺陷净负，v21.1 修正后转正） |
| v22.1 + 改口轮规则 | †75.16% (242/322) | †78.83% (540/685)（原生 v20.2 口径） | ✅（状态口径 81.57%；+0.31pp/+1.46pp 双正，获救 12/回退 11、turn 27/17；S-000318 key 割裂获救，T2-3 turn 75.0%/T3-2 turn 78.0%；③record 不超前取值合并实验 v22.0 打平、拆因后判定③净有害已回退） |

| v23.0 + time.py 时间规则（fill on） | †73.60% (237/322) | †77.66% (532/685) | ↩️ 回退（获救 14/回退 19；规则回放对真实预测 1修/6破——fill 对 X-2/T3-3 共享时间话术 GT 常无 duration 反多错） |
| v23.1 + 时间规则（fill off） | †72.67% (234/322) | †77.08% (528/685) | ↩️ 回退（获救 11/回退 19；规则回放 1修/0破 ≈ 零净作用；两次全量 −2.5pp/−1.6pp 波动归因 API 非确定性——0 处预测差异可由规则解释、19 处为模型侧差异；GT 不变性回归 3388/3388 零破坏，规则本身无害但无益） |
| v23.2 时间规则精简入库（3 条重推导规则） | 未全量重跑（离线回放） | 未全量重跑（离线回放） | ✅ 入库待验证（v22.1 存档回放 **3修/0破**：S-000829 end 21:00→21:30、S-000862 日期+1天两字段；两 case 均唯一差时间字段 → session 理论翻正 +2/322 ≈ +0.62pp，低于 API 噪声带故不单独重跑；GT 不变性 3388/3388 零改动，含跨夜弃权门控——"用N小时到明天0点"场景；v23 移植全量版归档 data/_archive/time_rules_v23_full.py） |
| v23.3 qwen3-32b 弱模型对照 | **61.18% (197/322)** | **67.01% (459/685)** | 📊 模型档差距 −14pp/−11.8pp；时间错误 347 vs 241（+44%）但增量仍在推理/绑定层——规则回放：new3 case 1修/0破（session +1，S-000829 翻正）、full 0修/3破（session −1）——**"弱模型规则有效"假设证伪，时间后处理两模型档均封顶** |
| v24.0 + 记录严格规则（触发器过窄） | †72.36% (233/322) | †76.64% (525/685) | ↩️ 中间版（约束识别误把新意图目标 app 当筛选条件，PER 原型零覆盖；T3-3 预选轮被误注入 7/14→4/14） |
| v24.1 + 上次记录严格规则 records[0] | **†80.12% (258/322)** | **†83.07% (569/685)**（v25.1 gt_derive 口径复算 **84.67% (580/685)**） | ✅ **当前最佳**（状态口径 81.27%；**+4.97pp/+4.24pp 项目最大单步收益**，获救 25/回退 9、触发∩获救=18/触发∩回退=1；9 个 PER 原型 7 获救；触发轮 GT 核验 100%=records[0]） |
| v24.4 + 消歧候选顺序对照（candidate_map） | †79.50% (256/322) | †82.63% (566/685) | ⚖️ d5 打平未采纳（后续 2026-09-11 在 d6 转正 +2.99pp）（−0.62pp/−0.44pp 在 ±2pp API 噪声带内；**因果面：机制只触 14 个 T3-3 消歧轮，T3-3 turn 12→14/31、session 6→7/14，S-000303/S-000319 获救，0 例由本机制致退**——唯一 T3-3 回退 S-000606 为 turn 1 时间推算波动非本机制；其余 10 获救/12 回退全在非触发模板=运行波动。离线解析 14 例：13 成功且顺序 100% 符 GT、1 安全回退、0 错位） |
| v24.5 v24.1 双跑（oracle pass@2 门控测量） | run2 †81.37% (262/322) | run2 †83.94% (575/685) | 📊 双跑差 +1.25pp/+0.87pp（噪声带内）；翻转 28/673（4.16%）；**oracle pass@2 上限 +1.63pp ≤ 3pp → BoN+投票/verifier 采样路线整体关闭** |
| v25.0 + 窄版冲突检查反思（conflict_check） | †78.88% / †78.88%（双跑） | †82.34% / †83.36%（双跑） | ↩️ **双跑一致净负回退**（均值 78.88%/82.70% vs 基线 80.75%/83.51%，−1.86pp/−0.80pp）；反思日志：触发 10 turn/采纳 1（S-000328，选择疑问句陷阱错→错），9/10 模型拒绝改动；离线触发器收窄后 4 触发全真靶 0 误报，靶区上限 +0.6pp < 噪声带 |
| v25.1 turn 级 GT 知二推一口径（gt_derive，评估口径非模型改动） | 不变（session 级无缺口） | v24.1 双跑复算 **84.67% / 85.55%（+1.61pp/+1.61pp，+11 转正/0 翻错）** | ✅ **口径采纳默认开**（X-2 turn 76.2%→93.7%；REA·时间推算桶移除 11 例假错误；`--no_gt_derive` 回退；详见 CHANGELOG v25.1） |
| **v25.2 harness 意图对象语义修复（key 合并态推导+无app归并+consumed 退役）** | **86.65% (279/322) / 87.89% (283/322)**（均值 **87.27%**） | **88.32% (605/685) / 88.61% (607/685)**（均值 **88.47%**） | ✅ **当前最佳**（状态口径 84.44%/84.44%；**+6.52pp/+3.35pp 项目最大单步收益**；获救 30/32、双跑一致回退仅 1 且为模型波动；幻影意图 26→0/1；详见 CHANGELOG v25.2） |
| v26.0 intent-id 协议（身份与内容解耦，`# intent: I1`/`new`） | 83.54% (269/322) / 83.54% (269/322) | 85.69% (587/685) / 85.55% (586/685) | ↩️ 裸协议净负（−3.11pp/−4.34pp；遵从 100% 但补全型共指模型承担不了；进入三轮补丁链） |
| **v26.2b id 协议补丁链（+归并校验/cancel 名字化/身份判定强制化/段去重，d5 当前最佳）** | **88.51% (285/322) / 88.82% (286/322)**（均值 **88.67%**） | **88.03% (603/685) / 89.64% (614/685)**（均值 **88.83%**） | ✅ **当前最佳**（状态口径 85.73%；对 v25.2 **+1.39pp/+0.37pp** 且 session 极差 0.31pp 更小；稳对 275 vs 267 净 +8；迭代链 v26.1 归并+cancel 名字化→v26.2 身份判定→v26.2b 段去重；含 bug 的 v26.2 一次跑（86.65%）已从统计剔除；详见 CHANGELOG v26.1/v26.2/v26.2b） |

**dataset_6_clean（237 sessions 泛化性测试集，qwen3.8-flash 生成器渲染；剔 3 R5-A 问题样本=234 参评；单跑口径）**：

| 版本 | session 级 | turn 级 | 状态 |
|---|---|---|---|
| v26.2c 基线（d5 casebank 检索库） | 84.19% (197/234) | 87.27% (432/495) | d6 基线（状态口径 85.16%；−4.5pp vs d5 在跨数据集波动内，泛化保持） |
| v26.2c + candidate_map 转正 | **87.18% (204/234)** | 88.48% (438/495) | ✅ d6 转正（T3-3 0.30→0.50；获救 12/回退 5 全非消歧轮零误伤；`config_d6_qwen_plus_candmap.json`） |
| v26.3-p1 pending_app_rule v1/v2（中间版） | 85.47% / 85.47% | 87.27% / 87.07% | ↩️ 中间版（v1 更新段丢 app S-000184；v2 补参数轮误判新建 S-000220——规则边界须与模型可见信息对齐） |
| **v26.3-p1 pending_app_rule v3【默认更新,谨慎新建】** | **90.60% / 86.32%（双跑均值 88.46%）** | **90.51% / 88.48%（均值 89.49%）** | ✅ **采纳**（+1.28pp/+1.01pp；稳对 8：幻觉 app 桩归并/T2-2 歧义留空/补槽挂靠三类；run2 独有错 10 全为画质/时间波动；`config_d6_qwen_plus_best.json`） |
| **v26.3-p3 + multi_instance_key（d6 口径基准，v27 前最佳）** | **88.03% (206/234)**（五跑迭代 88.03/75.64/86.32/88.03/88.03） | **89.90% (445/495)** | ✅ **采纳**（dup-key 6/6 全判对，塌缩假阴性消除——S-000046 暴露真实画质错；cancel 实例定位端到端通；判分更严暴露真错误与修回 dup-key 相抵后与 mi_off 持平；四条 key 路径 bug 修复史见 CHANGELOG） |
| v27.0 run1（初版提取器） | 81.20% (190/234) | 84.24% (417/495) | ↩️ **大回退 −6.8pp**（区间终点省略时段词被解析成 AM+误触发矛盾规则让模型照抄错值；30 破窗全同型——三处修复：子句级时段继承/跨夜滚动/无锚兜底；教训：回放对照须盯 turn 级 time_wrong 增量） |
| v27.0 fix_run1（修复版） | 91.88% (215/234) | 93.13% (461/495) | ✅ 超噪声带（time_wrong 36→3；获救 15/回退 6；profile_slot 触发过宽 S-000080/125 已定位） |
| v27.0 fix2（+触发收窄+时长候选） | 91.88% (215/234) | 92.93% (460/495) | ✅（S-000133 时长候选 dur_candidate 修复；time_wrong 6；与 fix_run1 极差 0.2pp——v27 自身方差小） |
| v27.1 run1（EVIDENCE 全注入） | 81.20% (189/234) | 84.65% (419/495) | ↩️ 候选数暴露诱导模型自行过滤换记录（S-000005 型） |
| v27.1 run2（latest 措辞加固） | 88.46% (207/234) | 91.52% (453/495) | ↩️ 定位类注入仍扰动 record_rule 行为面（救 3/破 13） |
| v27.1 run3（defer-only） | 89.32% (209/234) | 90.51% (448/495) | ↩️ **路线关闭**（T3-3 家族救 5 机制性转正 ✓ 但非目标扰动 13，净负）；画质表补缺独立采纳 |
| v27.1 record_selector（三轮否定关闭） | 81.20% / 88.46% / 89.32% | — | ↩️ 路线关闭（扰动面>靶区；详见 CHANGELOG v27.1） |
| v27.2 run1（候选模式） | 94.87% (222/234) | 95.96% (475/495) | 单跑（状态口径 88.28%；救 10 破 5） |
| v27.2 run2 | 91.45% (214/234) | 93.74% (464/495) | （T3-3 分支二义暴露：三例各走错一支） |
| v27.2 run3（+harness 分支修复） | 93.16% (218/234) | 94.55% (468/495) | （分支判定上移 harness：选择轮提示注入确定性 a/b 结论） |
| v27.2.1 QUALITY HINT（画质条目高亮） | 定点验证 5/5 修复 ✅（并入 v27.2） | — | 表遵从波动用条目高亮治（schema.QUALITY_MAP 79 条标准表；话语 app/画质词查表注入；全 case 平等不挑反常识） |
| **v27.2 三跑均值** | **93.16%** | **94.75%** | ✅ **采纳**（状态口径 87.79%；vs v27.0 **+0.42pp/+1.21pp** turn 超带边缘；稳救 4 稳破 3 净+1；稳破=画质表遵从不稳×2 + T2-3 改口 defer 复合边界；config_d6_qwen_plus_best.json） |
| **v27.3 run1（候选组装修复+选择轮锚点）** | **95.30% (223/234)** | **96.97% (480/495)** | 状态口径 91.03% |
| **v27.3 run2** | **94.87% (222/234)** | **95.76% (474/495)** | 状态口径 89.56% |
| **v27.3 双跑均值（含 _fv bug，被 v27.3.2 取代）** | **95.09%** | **96.36%** | ⚠️ 数字含 _fv 机制性错误（056/192"画质波动"实为嵌套解析 bug 致记录值覆盖明说） |
| v27.3.1 时间桩兜底（列举轮 candidate-only 输出补明说时间桩） | 定点 A 型补桩/B 型排除 ✅ | — | 四重收窄（列举轮+ev完整+零意图段+无明说 app）；性质同 prev_state 兜底/知二推一 |
| v27.3.2 _fv 嵌套解析修复（qwen38 全量暴露） | 定点 056/192 修复前后 0/4→4/4 ✅ | — | harness bug：resolution/rtt {"min_value":{...}} 嵌套被误判悬空→记录值覆盖明说 |
| **v27.3.2 plus run1** | **97.44% (228/234)** | **97.98% (485/495)** | 状态口径 91.58% |
| **v27.3.2 plus run2** | **98.29% (230/234)** | **97.78% (485/495)** | 状态口径 91.21% |
| **v27.3.2 plus 均值（d6 当前最佳）** | **97.86%** | **97.88%** | ✅ **采纳**（状态口径 91.39%；vs v27.2 **+4.70pp/+3.13pp**；vs v27.3 修复收益 +2.77pp/+1.52pp；T3-3 19/20 run2 满分；稳错 3 全非候选链路（059 X-2 串扰/203 T2-2 分裂/218 T1-3 时间形态），候选管线零稳错） |
| **v27.3.2 qwen3.8-27b 均值（平替验证）** | **96.79%**（97.44/96.15） | **97.58%**（97.98/97.17） | ✅ 二次坐实平替（差 plus 1.07pp/0.30pp；T3-3 双跑 20/20 全对；零特有补丁——_fv 为共享 harness bug，line_tolerant 等弱模型配套 v26.2c 已就位；稳错 6 全字段级波动） |

| **v27.0 fix3（v27.2 前最佳）** | **92.74% (217/234)** | **93.54% (463/495)** | ✅ **采纳**（状态口径 86.08%；**+4.71pp/+3.64pp** 单跑超噪声带 2 倍用户拍板免双跑；time_wrong 19→5；11 目标 case 8 转正；回退 6 经基线五跑核查无机制性回退；T1-1/T1-2 零变化；`config_d6_qwen_plus_best.json` 六时间开关全开） |

**dataset_930 泛化性攻坚（4455 sessions 新一代泛化集；qwen3.8-27b 剔噪口径；444×2 互斥分层子集）**：

| 版本（d930 444 子集） | session | turn | 备注 |
|---|---|---|---|
| 首跑（v27.3 初版代码） | 91.43%（有效段）/ 全量含欠费空分 64.56% | — | 暴露 id 沙箱断裂+话术穿透；1390 API 欠费空分后充值重扫 |
| v2731（+id 别名/Ellipsis/候选N/svc 窗） | 92.34% / run2 91.89% | 93.24% | T2-2 63.8→72.7%（id 沙箱修复收益） |
| v2732/v2733（+话术解析合并） | 91.67% / 92.12% | — | T3-3 洼地主因"候选N"话术 |
| v274（方案 B LLM 兜底，plus） | 91.67% | 93.96% | 暴露列举顺序污染（1130 型） |
| v275（列举收回 harness） | 90.32% | 93.24% | 深挖出触发链断链（候选N 三层正则） |
| v276/v2761（judge LLM 化+状态闭环） | 91.22% | 92.94→93.45% | 764/1789 确认轮劫持修复，T3-3 14/26 |
| v277/v2771（路线乙剥离+明说豁免） | 90.99/90.32% | — | 1c 退役；剥离过宽误杀修正 |
| **v28（scope 三枚举 judge）** | **92.43%** | **94.26%** | **524 翻正零翻错**（引用语义判别） |
| **v28.1（悬空压制+selector break 修正）** plus | 92.20% | 94.05% | T3-3 16/25，943 翻正 |
| **v28.1 q38（定案单跑）** | **92.66%** | **94.15%** | **T3-3 17/25，反超 plus（平替三次复现）** |
| **v28.1 q38 s444b（互斥子集交叉验证）** | **92.41%** | **93.42%** | T3-3 16/24=67% 与 s444 68% 一致 |
| **v28.2（未定词槽位限定修正+selector 子串修正）q38 s444** | **93.81%** | **94.78%** | +1.15pp（T2 类 16 例机制性修复）；524 翻正 |
| **v28.2 q38 s444b** | **92.87%** | **93.64%** | +0.46pp 跨子集复现 |
| **v28.3（TIME RULE 组装分叉）q38 s444** | **94.72%** | **95.30%** | +0.91pp；T2 类 13→3（透传缺口收口） |
| **v28.3 q38 s444b** | **94.48%** | **94.82%** | 跨子集复现 |
| **v28.4（resolve_listing 属性匹配）q38 s444** | **94.95%** | **95.51%** | +0.23pp 纯增益翻错 0；rule 路径 177/258 精确命中,歧义升级 LLM |
| **v28.4 q38 s444b** | **94.25%** | **94.71%** | 跨子集复现 |
| **合并 871 抽样定案** | **94.60%** | **95.12%** | ✅ **d930 泛化性定案（剔噪口径）** |

**d930 错误结构（65 例逐例报告,docs/report_errors_d930_v281.md）**：T2 澄清轮明说字段未透传 16（25%,下一靶区）/ T1 时间解析 11 / T5 多意图串扰 9 / T8 scope 落地缺失 8（终极修法=字段值 harness 查表直填,可并灭 T6）/ T4 画质查表 7 / T6 候选取值错位 5 / T3 改口 4 / T7 2 / T9 GT 噪声 3。

**历史：dataset_4**（test 280 全集口径；16 条噪声全在 train 侧；turn 级为旧口径，v17.1 新口径复算见注）。

| 版本 | session 级 | turn 级 | 状态 |
|---|---|---|---|
| v16.0 baseline（qwen-plus + agent_context） | 68.93% (193/280) | 67.58% (394/583) | 基线（状态口径 81.58%） |
| v16.0 +status_prompt（d4 时代最佳） | 70.36% (197/280) | 68.44% (399/583)（v17.1 口径复算 74.27%） | ✅（状态口径 82.28%；+1.43pp 微正） |
| v17.0 32b baseline | 56.79% (159/280) | 56.95% (332/583) | 基线（状态口径 77.54%） |
| v17.0 32b +status_prompt | 57.50% (161/280) | 58.66% (342/583) | ✅ 跨模型验证：两模型同向为正（32b +0.71pp/+1.71pp） |
| v18.0 qwen3-235b-a22b-instruct-2507（最佳配置单跑） | 62.50% (175/280) | 62.95% (367/583) | ◯ 旗舰不如 plus（−7.86pp）；record 42 最低、T3-3 3/12 最佳 |

（模型差距跨数据集稳定：plus−32b = +12.14pp @ d4 / +12.04pp @ 2.0 / +7.92pp @ 1.0。Qwen2.5-72B-Instruct 对标因 API key 无权限（403）未跑，配置已备待开通。）

**四模型对照（2026-09-09，同 v26.2b+弱模型容错配置，详见 CHANGELOG v26.2c）**：

| 模型 | session | turn | 状态机 | vs 各自旧配置 |
|---|---|---|---|---|
| qwen-plus（双跑） | 88.67% | 88.83% | 85.73% | — |
| **qwen3.8-27b（双跑）** | **89.91% (89.13/90.68)** | **90.29% (89.93/90.95)** | 85.13% | +11.96pp（v1 77.95%，无容错） |
| qwen3-235b-a22b-instruct-2507 | 82.61% | 83.65% | 83.38% | **+20.11pp**（v18 62.50%） |
| qwen3-32b | 73.29% | 77.37% | 80.06% | +12.11pp（v23.3 61.18%） |

- 沙箱逐行容错 + "只写变化字段" prompt 对所有代模型普遍兑现且与模型强度反序（最弱的 235b 涨最多）；模型档差距大幅收窄（plus−235b 26pp→7.3pp、plus−32b 27pp→15.4pp）。
- **qwen3.8-27b 双跑坐实反超 plus（89.91% vs 88.67%，稳对 282 vs 275 净 +7）→ 当前单模型最高分**；模型锚点修正：plus ≈"新一代 27B 指令特化档"，且该档位在好 harness 下可平替 plus。

**跨数据集泛化（2026-09-10/11，dataset_6 = 237 全新渲染 session，生成器 qwen3.8-flash）**：

| 口径（plus + v26.2c） | d6 全量（剔 3 问题样本 = 234） | d5（对照） |
|---|---|---|
| session | 84.19% (197/234) | 88.67% |
| turn | 87.27% (432/495) | 88.83% |
| 状态机 | 85.16% | 85.73% |

泛化基本保持（−4.5pp 跨数据集波动内，零崩溃性失败）。问题样本 3 个 R5-A 单列（S-000106/210/228）。**candidate_map 在 d6 转正**：T3-3 洼地 0.30 实测开启后 session +2.99pp（87.18%）、T3-3 0.30→0.50，获救 12/回退 5（全部无消歧轮，零机制误伤）；`config_d6_qwen_plus_candmap.json`。教训：①换数据集三件套（DST_DATASET/--split/--noise_file）缺一即静默错配；②d6 的 201 个 session_id 各有两份不同渲染（id 冲突非版本迭代），render_meta 优先去重。

**历史：dataset_merged_2.0**（test 368；† = 剔除 16 残留噪声口径 357/742；与 d4 数字不直接可比）。

| 版本 | session 级 | turn 级 | 状态 |
|---|---|---|---|
| v15.0 qwen3-32b（2.0 基线） | 55.98% (206/368)；†56.86% (203/357) | 56.75% (437/770)；†58.63% (435/742) | 基线（状态口径 †78.10%） |
| v15.0 qwen-plus | 65.49% (241/368)；†68.91% (246/357) | 65.45% (504/770)；†67.65% (502/742) | ✅（状态口径 †82.15%，2.0 时代最佳） |

**历史：dataset_merged 1.0**（test 352；† = 剔除 49 噪声口径 303/629，v13 起为默认口径；与 2.0/d4 数字不直接可比）。

| 版本 | session 级 | turn 级 | 状态 |
|---|---|---|---|
| v7.0（v6.0 方案 merged 基线） | 51.42% (181/352) | 52.80% (387/733) | 基线 |
| v6 baseline（v11 同期复跑） | 50.28% (177/352) | 52.25% (383/733) | 基线复跑 |
| v8.0-a（prompt 加取消 demo） | 50.00% | 52.11% | ↩️ 净负回退 |
| v8.0-b（取消校验回路） | 52.27% (184/352) | 52.80% (387/733) | ✅ |
| v9.0（record 选择器 vote） | 49.72% | 51.98% | ↩️ 回退 |
| v10.0（record 选择器 reinject） | 49.15% | 51.43% | ↩️ 回退 |
| v11.0（选择链 demo） | 52.27% (184/352) | 55.12% (404/733) | ✅ |
| v12.0（纠正 demo，demo 无头） | 47.16% | 50.48% | ↩️ 回退 |
| v12.1（demo 补头+兜底+纠正 demo） | 55.97% (197/352)；†62.71% | 56.07% (411/733)；†60.10% | ✅ |
| v12.2（真实画质映射表） | 54.55% (192/352)；†61.39% | 56.75% (416/733)；†61.05% | ◯ 打平（开关保留） |
| v13.0（意图状态机旁路 status_obs） | †62.71% (190/303) | †61.84% (389/629) | ◯ 旁路上线（状态口径 78.74%，无副作用实证） |
| v13.1（agent_context 进 prompt） | †66.01% (200/303) | †65.66% (413/629) | ✅（状态口径 80.19%） |
| v14.0（v13.1 配置 + 换模 qwen-plus） | 68.18% (240/352)；†73.93% (224/303) | 66.71% (489/733)；†71.22% (448/629) | ✅（状态口径 †81.00%，1.0 时代最佳） |

参考（dataset_2，163 test，小样本偏乐观）：v6.0 56.44%/56.85%。

## 3. 历史最佳 v24.1 详细结果（dataset_5 时代；v25.2/v26.2b 详见 §2 总表，d6 详见上表）

| 口径（dataset_5 剔除 7 噪声 322，cancel_credit + correction_credit on） | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| **v24.1（+上次记录严格规则，当前最佳）** | **80.12% (258/322)** | **83.07% (569/685)** | **81.27%** |
| v22.1（无记录规则，其余全同） | 75.16% (242/322) | 78.83% (540/685) | 81.57% |
| v24.0（触发器过窄中间版，已迭代） | 72.36% (233/322) | 76.64% (525/685) | 80.97% |

- **v24.1 上次记录严格规则 records[0]（+4.97pp/+4.24pp，项目最大单步收益）**：用户确认修复后数据集的规则——无约束"上次"严格 = completed_at 最新一条 records[0]（触发轮 GT 逐意图核验 100% 符合）。触发式注入 records[0] 字段明细 + 三条纪律（被指代字段必须取 records[0] 值/禁止 profile 习惯值或其他记录替代/用户明说字段不覆盖，整单照抄才连 app/service 一起取）。两个关键设计：①约束识别为"紧邻记录词"结构（`上次X那条`/`按X的记录`/`X那条`）——话语中远离记录词的 app 名是新意图目标不是筛选条件（v24.0 在此翻车，9 个 PER 原型零覆盖）；②排除 T3-3 预选/查询式/日期锚定轮（`查…记录`/`选中|办过|参考`/`X月X号完成的那条记录`——GT 记录字段悬空待消歧，S-000351 实证，v24.0 误注入致 T3-3 7/14→4/14）。获救 25/回退 9，触发∩获救=18、触发∩回退=1（S-000369 触发轮丢 prev 时间字段，已知残余）。9 个 PER 原型 7 获救。详见 CHANGELOG v24.0/v24.1。

- **v22.1 改口轮规则（+0.31pp/+1.46pp 双正）**：改口词+prev_state 非空触发注入 CORRECTION RULE 段——`# intent:` 头沿用 prev 旧 key（coreference 正常解析）、被更正的 app/service 段内赋新值（最终 key 由 delta 值推导自动更新）、只改被更正字段、其余一律沿用不得丢字段；显式覆盖旧共享规则"改 service 必须用新头"（key 割裂根源）。机制验证：S-000318 获救；T2-3 turn 20/36→27/36、T3-2 turn 30/50→39/50；COR 标签 9→4；意图漏识别 46→34；T3-3 session 7/14 保持。获救 12/回退 11（session 噪声带内平衡）、turn 27/17 明确为正。
- **v22.0 ③record 不超前取值（回退）**：always-on system 规则"无指代词禁止从画像取值"在合并实验中打平（−0.62pp/+0.44pp），拆因后确认③净有害（关掉③ turn +7）——保守化副作用：S-000468 确认轮丢意图、S-000029 新增意图漏识别、S-000357 明说的 360P 被留空。教训同 v8.0-a：全局 prompt 变动扰动非目标类；③后续若重试应改触发式（prev 空+无指代词才注入）。详见 CHANGELOG v22.0/v22.1。
- **v21.1 消歧规则（session +0.62pp，T3-3 破零 0→7/14）**：三条件窄触发（上轮 agent 消歧问句 + 本轮选择回答 + prev_state 非空）→ prompt 注入 DISAMBIGUATION RULE 段（候选 app/service 禁作输出、沿用现有意图头、序数按 agent 话术顺序对齐禁止 records[N] 数序数、禁止覆盖 prev 已有字段尤其时间）。触发扫描：test 命中 14 轮全在 T3-3（14/14 session 覆盖）、其他模板零误伤。record 提取错误 58→42。v21.0 初版规则净负（序数对齐缺陷致 S-000239/762/906 回退），修正后转正——教训同 v12.0→v12.1：注入规则必须与模型可见信息的结构（agent 话术顺序）对齐。详见 CHANGELOG v21.0/v21.1。
- **v20.0 画质表重建（+2.17pp，获救 14/回退 7）**：d5 train GT source_ref 还原规范表（60 条零冲突）∪ 非冲突遗留条目；与旧表仅 4 条差异（腾讯视频/视频、爱奇艺/视频 高清=540p 等），但表补显式条目后模型查表遵从度提升，间接受益明显（S-000392/S-000519/S-000856）；回退 7 例核验均为运行波动（差异条目无关）。resolution 错 36→27。config `quality_map_version`（`"d5"` 默认 / `"legacy"` 回退）。
- **取消计分口径（v17.1 方案 b）**：turn 级 missed_intent 由 d4 旧口径 56 降至 34；`cancel_wrong` 仅 2 例——模型几乎总能正确执行取消，此前该类 turn 错全是口径误罚。
- **改口错误值轮豁免（v20.2 correction_credit，+2.04pp，14 转正/7 仍错）**：T3-2 turn 50.0%→70.0%、T2-3 turn 61.1%→72.2%；14 转正 = 10 例模型拒收 pred 空 + 4 例记字面值其余字段全对；仍错 7 例全是真实错误（rtt 取值、时间推算、duration=-30min），口径不误赦。详见 CHANGELOG v20.2。
- 错误分布（turn，剔除口径 166 错）：value_wrong 90（rtt 57 仍最大单字段、resolution 27）、time_wrong 40、missed_intent 34、cancel_wrong 2；session 级 83 败 = field_wrong 43 + intent_set_mismatch 40。
- 分模板（session，剔除口径，v21.1）：满分/近满分 T1-2、T1-3、X-1；**T3-3 破零 7/14**（剩余 7 败为序数/日期→记录值对齐的 PER 能力短板）；弱项 T2-3、T2-1、T2-2、T3-4。
- 附带发现（数据集设计）：d5 新增 `invalid_resolution_unit` 改口场景（"1080m"→自我纠正"1080p"），turn 级 GT 记字面值不动（设计口径），评估侧由 v20.2 correction_credit 豁免（S-000656/S-000869 已转正）。
- 产物：v22.1（当时最佳）`data/evals/eval_dual_v22_1_fix4.jsonl`/`_summary`/`_errors.html`、v22.0（③+④ 合并）`eval_dual_v22_fix34.jsonl`/`_errors.html`、冒烟 `eval_dual_v22_fix34_smoke.jsonl`；v21.1 `eval_dual_v21_1_disamb.jsonl`/`_summary`/`_errors.html`；v20.0 `eval_dual_v20_d5_qmap.jsonl`；v20.2 口径复算产物 `eval_dual_v20_d5_qmap_corrcredit.jsonl`；当时最佳配置 `configs/config_d5_qwen_plus_qmap.json`（`disambiguation_rule`+`correction_rule` 双开；现为门控回退用），消融配置 `configs/config_d5_qwen_plus_disamb.json`（仅消歧）/`config_d5_qwen_plus_fix34.json`（③+④）/`config_d5_qwen_plus_fix4.json`（仅④）。

## 3''. 历史：v19.0 详细结果（dataset_5 首基线，legacy 画质表）

| 口径（dataset_5，cancel_credit on） | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| 剔除 7 噪声（322，主口径） | 72.05% (232/322) | 75.47% (517/685) | 81.27% (538/662) |
| 全集（329） | 70.52% (232/329) | 73.32% (514/701) | 80.86% |

- **取消计分口径（v17.1 方案 b）首次全量生效**：turn 级 missed_intent 由 d4 旧口径 56 降至 36；新增 `cancel_wrong` 桶仅 2 例——模型几乎总能正确执行取消，此前该类 turn 错全是口径误罚。
- 错误分布（turn，剔除口径 168 错）：value_wrong 93（rtt 57 仍最大单字段、resolution 36）、time_wrong 37、missed_intent 36、cancel_wrong 2；session 级 90 败 = field_wrong 51 + intent_set_mismatch 39。
- 分模板（session，剔除口径）：满分 T1-1 21/22(95.5%)、X-1 6/6、T2-5 15/17；弱项 **T3-3 0/14 全灭**（10 例 intent_set_mismatch 中 9 例 record 候选被误建成新意图，与 d4 归因一致）、T2-3 9/18、T2-2 10/19、T3-2 11/17。
- 状态机分桶：Cancelled 39/41=95.1%、Ready 87.1%、Confirmed 76.5%、Collecting 55.3%（仍最弱）。
- 产物：`data/evals/eval_dual_v19_d5_qwen_plus_status.jsonl`/`_summary`/`_errors.html`（剔除口径）、`_full`（全集）、冒烟 `eval_dual_v19_d5_smoke.jsonl`。

## 3'. 历史：v16.0 详细结果（dataset_4 + qwen-plus + agent_context + status_prompt，旧口径）

| 口径（dataset_4 test 280 全集） | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| baseline（status_prompt off） | 68.93% (193/280) | 67.58% (394/583) | 81.58% (465/570) |
| **+status_prompt（当前最佳）** | **70.36% (197/280)** | **68.44% (399/583)** | **82.28% (469/570)** |

status_prompt Δ：session +1.43pp、turn +0.86pp、状态机 +0.70pp；逐样本翻转**获救 16 / 回退 12**（净 +4）。幅度在 ±1.5pp 波动带边缘，但主指标同向为正且机制可解释（见下）。

**机制实证（状态快照如何辅助判断）**：S-000294 baseline 对"按上次记录"拒填 service（"utterance 未明说业务"→漏意图），status 版从 records[0] 补全；S-000215 baseline 丢 prev 时间槽（"本轮未提时间故不赋值"），status 版显式沿用。**状态快照让模型感知意图已有累积态 → 槽位沿用/补全更稳、幻觉与漏段减少**（获救 16 例含 3 例意图集合修复）。与 1.0+32b 消融（−0.66pp 打平微负）方向翻转——更强模型更能利用状态信息。

按模板（session / turn，括号内为 baseline 成绩）：

| 模板 | session | turn | 场景 |
|---|---|---|---|
| T1-1 | 20/20 = 100% | 100% | 单轮单意图 |
| T1-2 | 20/20 = 100% | — | 单轮非保障 |
| T1-3 | 32/33 = 97.0% | 91.1% | 单轮多值 |
| T1-4 | 12/14 = 85.7%（78.6%） | 85.7% | 首轮画像 coreference |
| T2-1 | 8/20 = 40.0%（45.0%）↓ | 51.7% | 多轮补充 |
| T2-2 | 7/15 = 46.7%（53.3%）↓ | 60.7% | 多轮补充轮 |
| T2-3 | 8/16 = 50.0%（37.5%）↑ | 75.0% | 改口+否定 |
| T2-4 | 19/28 = 67.9%（75.0%）↓ | 83.9% | 多意图并行 |
| T2-5 | 12/12 = 100%（91.7%）↑ | 100% | 修改请求 |
| T2-6 | 24/28 = 85.7% | 63.6% | 取消意图 |
| T3-1 | 9/15 = 60.0% | 68.4% | 多轮多意图 |
| T3-2 | 4/14 = 28.6%（14.3%）↑ | 33.3% | 改口+否定 |
| T3-3 | 1/12 = 8.3%（持平） | 31.0% | record 消歧（状态辅助不治，见 §5） |
| T3-4 | 8/15 = 53.3% | 60.0% | 多轮画像 |
| X-1 | 4/6 = 66.7% | 66.7% | 域外意图 |
| X-2 | 9/12 = 75.0%（58.3%）↑ | 58.0% | 域外+多意图 |

历史存档：v15.0 @ 2.0 qwen-plus 剔除噪声 68.91%/67.65%（vs 32b 56.86%/58.63%，+12.04pp）；v14.0 @ 1.0 73.93%/71.22%。各数据集 test 构成不同，不直接可比。

## 4. 错误样本分析（历史：v16.0 +status_prompt @ d4；v19.0 @ d5 错误分布见 §3）

工具：`error_report.py`（规则基于 GT `slot_updates` 的 source_type 判定，非猜测）。每次评估自动生成同名单文件 HTML：**`data/evals/eval_dual_v16_d4_qwen_plus_status_errors.html`**（浏览器直接打开，自包含可转发）。

### 4.0 时间错误专项画像（v23 动机分析，2026-08-29）

v22.1 turn 级 109 错中 27 个涉时间字段（26 个纯时间错；X-2 占 11 最多），五类模式：①凌晨/下午 ±12h 混淆（S-000446）②明天/后天日期推算错（S-000862）③多时间候选选错（S-000090，多意图话语）④口语时长未换算 ⑤结束时间算错（S-000829）。另 10 个 GT=None 的 duration "错" 实为模型多填字段（turn 级 GT 按当轮 delta 累积）。

v23 err_report 对比（v23.0 vs v22.1）：time_wrong 29 vs 27、REA 39 vs ~43、PER 39 vs 41、CU 26 vs 21——时间类无净改善，消歧类反升，均在波动带内。详见 `eval_dual_v23_timerules_errors.html`。

### 4.1 错误分类分布（err_report 口径；turn 级 status 版 184 个 / baseline 189 个错误样本）

| 类别 | baseline | +status | Δ | 典型成因 |
|---|---|---|---|---|
| 意图漏识别 | 59 | 56 | −3 | 误差透传：前轮错→后轮状态沿用；短补充轮漏段 |
| record 提取错误 | 55 | 53 | −2 | 消歧/多记录取值带偏（plus 模型行为，见 §5） |
| 时间推算错误 | 53 | 58 | **+5** | prompt 变长挤压时间推算（与 v12.2 同形态） |
| 其他字段值错误 | 21 | 16 | −5 | 画质映射边界、单位格式 |
| 画像 Memory 错误 | 1 | 1 | 0 | — |
| （session 级）意图集合不一致 | 37 | 34 | −3 | 取消后未移除 / 幻觉多出意图 |
| （session 级）字段错误 | 50 | 49 | −1 | 上述 turn 级错误传导到最终态 |

### 4.2 报告界面说明

- 顶部：双口径 JGA 大数字 + 按模板正确率表；分类色 pill 导航（turn 级 / session 级分组）。
- 每类错误一个区块：**总结（类别+数量）→ 描述（成因+涉及模板分布）→ 全部错误样本卡片**。
- 样本卡片（可展开）：完整对话（出错轮高亮）、GT vs 预测 7 字段对照表（错字段标红，附 GT 来源如 `Record:R-U0057-001`）、逐字段错误分析、**LLM 原始输出**（v12 起每轮保存）。
- record 类样本额外有**选取对照**：GT 应取第几条（record_id+内容）vs 模型实取第几条（预测值反查画像匹配），并排字段对照。
- 文末灰色区块：**数据集噪声样本**（2.0 残留 16 个，含噪声原因、对话、GT 标注、模型预测），已从统计剔除仅供数据集修订参考。

## 5. 瓶颈与上限判断（v27.0 修订 @ d6）

**v19.0 时代判断的后续验证**（详见 CHANGELOG）：①record 消歧 → v21.1 消歧规则 + v24.3/v24.4 candidate_map 已修掉"误建新意图"子类，序数→记录值对齐（PER）仍靠 v24.1 规则打掉大头；②意图漏识别 → v25.2/v26 证明大半是 key 断裂假象；③时间推算 → **v27.0 已打破"无低成本修法"判断**——预计算注入路线 time_wrong 19→5（+4.71pp/+3.64pp），六类形态中五类被规则提取器+触发注入治住，残余为 GT 设计字段/数据侧问题；④同 key 双意图塌缩 → v26.3-p3 实例化修复。
**d6 时代剩余瓶颈**：T3-3 消歧 record 对齐残余（fix3 2/10，v27 下 T3-3 仍是唯一两位数洼地——解析器覆盖 45%，扩 parser 是后续项）、PER/REA 真能力残余（value_wrong 13/missed_intent 12）、X-2 实例边界波动。方法侧时间类已收尾，再往上 = T3-3 解析器扩充或更强模型。

---

## 5'. 历史版本（v19.0 修订）

1. **record 取值/消歧 = 首要瓶颈**（d5 T3-3 全灭 0/14、rtt 57 最大单字段错）：T3-3 失败形态在 d5 复现——record 消歧候选被误建成新意图（9/10 intent_set_mismatch 为 extra 幻觉意图）；强模型更积极引用画像 record，消歧/多记录场景反被带偏（v14/v15/v16/v19 四版连续复现）。status_prompt 辅助对此无效。**方法侧修补计划（2026-08-26 归因）：②T3-3 消歧回答归属规则为首要，③record 多候选不超前取值，①画质映射表重建（确定性），④改口轮沿用**。
2. **意图漏识别（56，大类）**：误差透传的下游表现；status_prompt 有小幅缓解（−3，槽位沿用更稳）。
3. **时间推算（58，status 版 +5）**：prompt 变长挤压的老问题（v12.2 同形态），加上下文段需权衡长度。
4. **record 噪声**：16 条残留全在 train 侧（检索库内，demo 教旧规则，影响有限）；test 无污染。
5. **上限**：上限主要来自模型而非方法栈。同模型下 prompt 侧增益已入收窄区（status_prompt +1.43pp 微正）；再往上：数据集修订（record 消歧规则 + 16 噪声）> 更强模型 > prompt 工程。

## 6. 产物清单

| 版本 | 文件（data/evals/ 下，均有 `_summary.json`） |
|---|---|
| **v27.0 @ d6（当前最佳）** | 终版 `eval_dual_v27_time_fix3.jsonl` + `_errors.html`（92.74%/93.54%）；迭代链 `eval_dual_v27_time_run1`（回退诊断用 81.20%）→ `eval_dual_v27_time_fix_run1`（91.88%）→ `eval_dual_v27_time_fix2`（91.88%/92.93%）；离线回放 `tools/replay_time_cases.py` + `_replay_v27_time.json`；提取器扫描 `_extractor_scan2.txt`/`_anchorless_scan.txt`；配置 `configs/config_d6_qwen_plus_time.json`（已转正为 best）；方案 `docs/plan_time_anchor_20260920.md` |
| v26.4（重构，行为零变更） | — | — | 代码清理+features.py/gt_build.py+tools/package.py（dist/qwen_intent_rag_v26.4.zip，133 文件）；产物与 v26.3-p3 run5 同口径 |
| v26.3 @ d6（v27 前最佳） | `eval_dual_v263p3_d6_best_run1~run5.jsonl` + `_errors.html`（pending_app_rule v3 + multi_instance_key；五跑迭代全档）；中间版 `eval_dual_v263p1_d6_pendapp{,_v2,_v3}_run1`、`eval_dual_v263p1_d6_pendapp_v3_run2`；基线 `eval_dual_v262c_d6_full237`（84.19%）/`eval_dual_v262c_d6_candmap`（87.18%） |
| v25.0（回退） | `eval_dual_v25_ccheck.jsonl` + `_errors.html`（run1）；`eval_dual_v25_ccheck_run2.jsonl` + `_errors.html`（run2，含 reflection 日志）；配置 `configs/config_d5_qwen_plus_ccheck.json`（不入最佳配置） |
| v24.5（测量） | `eval_dual_v24_1_run2.jsonl` + `_errors.html`；工具 `_oracle_pass2.py` |
| v23.0/v23.1 @ d5（回退） | v23.0 `eval_dual_v23_timerules.jsonl` + `_errors.html`；v23.1 `eval_dual_v23_1_timerules_nofill.jsonl` + `_errors.html`；回归工具 `tools/gt_invariance_timerules.py` / `tools/ablation_timerules.py`；配置 `configs/config_d5_qwen_plus_timerules.json`（v23.1 门控版，enabled=true 但最佳配置不引用） |
| **v20.0 @ d5（当前最佳）** | `eval_dual_v20_d5_qmap.jsonl` + `_errors.html`（剔除 7 噪声）；冒烟 `eval_dual_v20_d5_qmap_smoke.jsonl` |
| v19.0 @ d5 | `eval_dual_v19_d5_qwen_plus_status.jsonl` + `_errors.html`（剔除 7 噪声）；全集 `eval_dual_v19_d5_qwen_plus_status_full.jsonl` + `_errors.html`；冒烟 `eval_dual_v19_d5_smoke.jsonl` |
| v16.0 @ d4 | `eval_dual_v16_d4_qwen_plus_status.jsonl` + `_errors.html`（+status_prompt）；baseline `eval_dual_v16_d4_qwen_plus.jsonl` + `_errors.html`；冒烟 `eval_dual_v16_d4_smoke.jsonl` |
| v17.0 @ d4（32b 两版） | `eval_dual_v17_d4_qwen3_32b.jsonl` / `eval_dual_v17_d4_qwen3_32b_status.jsonl` + `_errors.html`；72b 冒烟（权限拒绝）`eval_dual_v17_d4_72b_smoke.jsonl` |
| v18.0 @ d4（235b 单跑） | `eval_dual_v18_d4_qwen3_235b.jsonl` + `_errors.html`；冒烟 `eval_dual_v18_d4_235b_smoke.jsonl` |
| v15.0 @ 2.0 | `eval_dual_v15_d2_qwen_plus.jsonl` + `_errors.html`（剔除噪声）；`eval_dual_v15_d2_qwen_plus_full.jsonl`（全集）；冒烟 `eval_dual_v15_d2_qp_smoke.jsonl` |
| v15.0 @ 2.0（32b 基线） | `eval_dual_v15_d2_qwen3_32b.jsonl` + `_errors.html`；`eval_dual_v15_d2_qwen3_32b_full.jsonl`（全集） |
| v14.0 @ 1.0 | `eval_dual_v14_qwen_plus.jsonl` + `_errors.html`（剔除噪声）；`eval_dual_v14_qwen_plus_full.jsonl`（全集）；冒烟 `eval_dual_v14_qp_smoke.jsonl` |
| v13.1（agent_context） | `eval_dual_v13_agentctx.jsonl` + `_errors.html`；叠加消融 `eval_dual_v13_all.jsonl` |
| v13.0（status_obs） | `eval_dual_v13_status_obs.jsonl`；冒烟 `eval_smoke_status.jsonl` / `eval_smoke_status_full.jsonl` |
| v12.1 | `eval_dual_v12_1_correction_demo.jsonl` + `_errors.html` |
| v12.2 | `eval_dual_v12_2_full_qmap.jsonl` + `_errors.html` |
| v12.0（失败回退） | `eval_dual_v12_correction_demo.jsonl` + `_errors.html` |
| v11.0 | `eval_dual_v11_demo_chain.jsonl` |
| v9.0/v10.0（回退） | `eval_dual_v8_selector_vote.jsonl` / `eval_dual_v9_selector_reinject.jsonl` / 基线 `eval_dual_v6_baseline_merge.jsonl` |
| v8.0-a/b | `eval_dual_merged_v8_full.jsonl` / `eval_dual_merged_cur_full4.jsonl` |
| v7.0 | `eval_dual_merged_v6_full.jsonl` |
| dataset_2 对照 | v6.0 `eval_dual_recrule_full.jsonl`；v4.0 `eval_dual_cancel_full.jsonl`；v5.0 `eval_dual_supp_full.jsonl`；v3.0 `eval_dual_dual_py_full.jsonl` |

数据/配置（当前 dataset_5）：`configs/config_d5_qwen_plus_qmap.json`（最佳，显式 `quality_map_version:"d5"`）/ `config_d5_qwen_plus_status.json`（v19.0 用，现默认也走 d5 表）、`data/split_d5.json`、`data/case_bank_train_d5.jsonl`、`data/embed_cache_d5.json`、`data/_noise_exclude_d5_scan.json`（13 条 R5 系噪声：test 7 / train 6）。历史 d4：`configs/config_d4_qwen_plus_status.json`、`data/split_d4.json`、`data/case_bank_train_d4.jsonl`、`data/embed_cache_d4.json` / `_status.json`、`data/_noise_exclude.json`（merged_2.0 时代 16 条清单，对 d5 已失效）。

旧口径参考：`eval_result_s3_py_full`（GT prev_state 70.50%）、`eval_session_xml_full`（Variant B session 40.49%）。
