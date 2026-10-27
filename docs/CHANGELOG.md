# 变更归档（方法 / 数据 / 实验）

> 所有方法、数据、实验变更都记录于此，便于跨版本比较。
> 命名规则：`vN.M` 版本号 + 日期 + 一句话摘要。
> 每条记录：背景动机 / 改动内容 / 涉及文件 / 实验结果（与上一版对比）/ 结论。

---

## v1.0 — 2026-08-07~10  适配真实数据集 baseline

**背景**：从原 10-case 手写版适配到真实 dataset_2（814 sessions + users_3000.jsonl）。

**方法改动**：
- schema 重建 26 app × 6 service 合法矩阵（豆瓣/京东/知乎/美团是域外 app → matrix_out_application）
- 多意图并发（B2 IDIC-DST）：一次 LLM 调用输出多 intent 段，execute_program_multi 切分
- 用户画像接入（users.jsonl：profile + records，"按上次记录" coreference）
- validator 时间三字段知二推一（_complete_timestamp）
- prev_state 兜底（LLM 未输出某 intent = 状态沿用）
- 修百炼 logprobs=True 时 n=1 的 bug

**数据**：dataset_2，814 sessions 80/20 split（651 train 检索库 + 163 test）。test_cases.jsonl 拆成 turn-level case。

**实验结果（v1 数据集，347 sessions test 167 cases）**：
- 严格 68.9% / 宽松 71.3%

**实验结果（v2 数据集，163 test sessions，turn 级 383 cases）**：
- v2_full3：严格 70.2% / 宽松 72.1%
- v3_full2：严格 70.50% / 宽松 73.11%（baseline）

---

## v2.0 — 2026-08-14  S3-DST PAR 引入 + PMI 死代码清理

**背景**：三大瓶颈中瓶颈2（prev_state 空 + "按上次/平时" coreference 全空）最严重（T1-4 只对 1/8）。引入 S3-DST 的 PAR 对症。

**方法改动**：
- 引入 S3-DST PAR（Pre-Analytical Recollection）：输出前强制 summary 回指 + 选择理由
- 实现 Variant A（python_par）：Text-to-Python + 强制 `# summary` 注释 + PAR 校验回路（缺 summary 触发重采样）
- 实现 Variant B（xml_par）：S3-DST Figure 2 原样 XML 结构化 I/O + summary
- 两变体共享 _SHARED_RULES（时间/画质/应用拆分/coreference 规则），仅输出壳层不同
- **清理 PMI_β 死代码**：删 _canonicalize/_logprior/_gen_one_candidate/generate_with_pmi、inverted_prompt、config 的 pmi 字段。原因：数据池小 + canonicalize 只解单 intent + 32b 先验噪声，无收益。

**涉及文件**：llm_client.py（删 PMI）、prompt_builder.py（重写，加 PAR）、pipeline.py（删 IntentPipeline.run PMI 路径，run_multi 按 output_mode 分支）、python_state.py（加 parse_summaries）、xml_parse.py（新）、eval.py（适配）、config.json（删 pmi 加 output_mode）、main.py。

**实验结果（turn 级，GT prev_state 不累积错误，383 cases）**：
| 配置 | 严格 | 宽松 | T1-4 |
|------|------|------|------|
| baseline v3_full2（无 PAR） | 70.50% | 73.11% | 1/8=12.5% |
| Variant A（python_par+PAR） | 70.50% | 73.89% | 2/8=25% |
| Variant B（xml_par+PAR） | 68.67% | 71.28% | 1/8=12.5% |

**结论**：Variant A 宽松 +0.78pp、T1-4 改善；Variant B 全面略降。选定 Variant A 为默认 output_mode。瓶颈2 的 T3-3 未动（根因是 GT record 选择噪声）。

---

## v3.0 — 2026-08-15  session 级评估口径 + 逐轮真实透传

**背景**：用户要求评估单元=1 session（163 sessions），模型结合上下文得到意图集合+状态，而非拆成 turn-level case。且强调"每个 turn 更新，不能吞整个 session"——即 prev_states 必须用模型真实输出透传（误差累积），不能用 GT 作弊。

**方法改动**：
- 新增 session_eval.py：逐轮 run_multi，prev_states 用上一轮真实 final_state 透传
- session 级 GT = 该 session intents[] 里 status=resolved 的 intent 最终 params 7字段，按 intent_key 标识；cancelled/rejected 不算 GT
- session 级 JGA = 模型输出意图集合 == GT 意图集合（每意图 7 字段全等）才算对
- 多轮单意图（T2/T3）补充/确认轮自然合并（同 intent_key 累积覆盖）
- eval.py 降级为参考（旧 GT prev_state 逻辑，不累积错误）

**涉及文件**：session_eval.py（新，主评估器）、eval.py（降级）。

**实验结果（逐轮真实透传，163 sessions / 336 turn-intent cases）**：
| 口径 | Variant A | Variant B |
|------|-----------|-----------|
| turn 级 JGA（真实透传） | 61.01% (205/336) | — |
| session 级 JGA | 47.24% (77/163) | 40.49% (66/163) |

**口径对比（Variant A）**：
| 口径 | 结果 | 说明 |
|------|------|------|
| 旧 turn 级（GT prev_state 不累积错误） | 70.50% | 偏乐观 |
| turn 级（真实透传，误差累积） | 61.01% | 真实多轮 DST |
| session 级（真实透传） | 47.24% | 最终集合正确性 |

**结论**：真实透传后 turn 级降 9.5pp（误差累积代价）。session 级暴露 turn 级掩盖的真问题：取消意图处理缺失（T2-6 session 全 0/16，但 turn 75.9% 说明单轮态对、只是取消没处理）、多轮补全不稳定（T2-2）、多记录选择噪声（T3-3 turn 18.8%）。

---

## v3.1 — 2026-08-15  错误分析 + 数据集噪声清单

**背景**：分析双口径错误样本，定位可修复根因。

**错误分析结果**（见 report.md §5.5）：

session 级错误（86 个）：
| 错误类型 | 数量 | 根因 |
|---------|------|------|
| intent_set_mismatch | 49 | 意图集合对不上 |
| ├─ extra（多意图/取消没处理） | 44 | 取消意图没处理（31 个纯 extra） |
| ├─ missing（漏意图） | 18 | 漏输出意图 |
| field_wrong | 37 | 意图对上但字段错（resolution 21 + rtt 12） |

turn 级错误（131 个）：
| 错误类型 | 数量 | 根因 |
|---------|------|------|
| value_wrong | 74 | 字段值错（resolution 42 + rtt 32，多为 pred=None 丢字段） |
| missed_intent | 38 | 模型该轮没输出该意图（prev_state 空 + coreference 场景） |
| time_wrong | 19 | 时间字段错 |

**数据集噪声扫描**（_gen_noise_report.py → data/_record_noise_problems.md）：
- 类别A 17 条：record_sourced_slots 标错（GT 值来自 profile 却标成 record）
- 类别B 5 条：指代"最近一条"但 GT 取的不是 records[0]（按 completed_at 降序）
- 类别C 35 条：含 record 指代词且判错（含 A/B 子集 + LLM 自身错误）

**下一步优先级**（按收益/成本）：
1. 取消意图处理（救 T2-6 16 个 session，session 级预期 +8pp）
2. 补充轮丢字段（resolution/rtt pred=None，prompt + 校验回路）
3. 数据去噪 / 显式 record 选择器（T3-3）

---

## v4.0 — 2026-08-15  取消意图处理（RefPyDST Δy 删除操作扩展到 intent 级）

**背景**：session 级头号错误是取消意图没处理（44/86=51%）。数据集标注完整（session_meta.cancelled_intent_id、intents[].status=cancelled、closure_path=cancelled），pipeline 缺取消信号。RefPyDST 的 Δy 原生支持删除操作，扩展到 intent 级即可。

**方法改动**：
- prompt_builder.py：SYSTEM_PROMPT_PY 加【取消意图】段，定义 `# cancel: <app>/<service>` 语义 + 示例
- python_state.py：加 `_CANCEL_HDR` 正则 + `parse_cancels()`；`execute_program_multi` 对取消段返回 `("__cancel__", <key>)` 标记，`_split_intent_segments` 跳过 cancel 段
- pipeline.py：run_multi 处理 `__cancel__` 段，用 `_resolve_cancel_key` 归一化（LLM 可能只写 app）到 prev_states 真实 key，标记为 `{"__cancel__": True, "cancel_key": ck}`
- session_eval.py：透传逻辑识别 `__cancel__:` 前缀，从 session_out 和 prev_states 移除被取消意图；`_run_multi_no_retrieve` 同步处理

**涉及文件**：prompt_builder.py、python_state.py、pipeline.py、session_eval.py

**实验结果（逐轮真实透传，163 sessions / 336 turn-intent cases）**：
| 口径 | v3.0（无取消） | v4.0（有取消） | 变化 |
|------|--------------|--------------|------|
| session 级 JGA | 47.24% (77/163) | **55.83%** (91/163) | **+8.59pp** |
| turn 级 JGA | 61.01% (205/336) | 58.04% (195/336) | −2.97pp |

按 template（session 级）关键变化：
| template | v3.0 | v4.0 | 说明 |
|----------|------|------|------|
| **T2-6（取消场景）** | 0/16=0% | **7/16=43.8%** | 取消处理生效 |
| T2-4 | 80% | 86.7% | 略升 |
| T2-5 | 57.1% | 85.7% | 升 |
| T3-2 | 44.4% | 66.7% | 升 |
| X-2 | 42.9% | 71.4% | 升 |
| T3-3（噪声） | 0% | 0% | 未动（数据噪声） |
| T1-4 | 25% | 12.5% | 略降（采样波动） |

session 级错误分布：intent_set_mismatch 49→37（取消 extra 大幅减少）、field_wrong 35。
turn 级 missed_intent 38→57（取消后该意图从后续轮移除，但 GT turn 级仍记录取消前累积态，pred 空被判 missed —— turn 级口径与取消语义的边界问题，session 级才是正确衡量）。

**结论**：取消意图处理成功，session 级 +8.59pp，验证"RefPyDST Δy 删除操作扩展到 intent 级"有效。turn 级略降是口径边界（取消后 GT 仍记历史态），非回退。剩余 session 级错误：field_wrong 35（补充轮丢字段）+ intent_set_mismatch 37（漏意图/取消未识别）。

**下一步**：补充轮丢字段（resolution/rtt pred=None）——session 级 field_wrong 大头，prompt + PAR 校验回路扩展到补充轮。

---

## v5.0 — 2026-08-17  补充轮丢字段修复（失败，已回退）

**背景**：v4.0 后 session 级剩余错误 field_wrong 35，其中 resolution 21 + rtt 12，多为补充轮 pred=None（丢字段）。尝试三处小改解决。

**方法改动尝试**：
1. pipeline.py：加 `_complete_prev_fields` 后处理——prev_state 非空但 delta 漏了沿用字段（application_name/service_name/resolution/rtt），自动补 `{"ref":"state.<slot>"}` coreference；扩展 `_need_par` 到补充轮（`_is_supplement_turn` 启发式）
2. session_eval.py：`_run_multi_no_retrieve` 同步应用 `_complete_prev_fields`
3. prompt_builder.py：【多轮补充轮】段加"补充轮自检"规则，强制沿用 prev_state 所有非空字段

**涉及文件**：pipeline.py、session_eval.py、prompt_builder.py

**实验结果（逐轮真实透传，163 sessions / 336 turn-intent cases）**：
| 口径 | v4.0（取消） | v5.0（+补字段） | v5.0 修订版（仅 prompt 自检+summary 校验扩展） |
|------|------------|---------------|----------------------------------------------|
| session 级 JGA | 55.83% (91/163) | 55.21% (90/163) | 55.21% (90/163) |
| turn 级 JGA | 58.04% (195/336) | 53.87% (181/336) | 53.27% (179/336) |

turn 级变好 session 6 个，变差 15 个（净负）。

**失败原因分析**：
1. **`_complete_prev_fields` 过于激进**：对新意图段（prev_state 为空的新 intent）也误触发，补了空 coreference 反而干扰正确赋值；对 prev 空字段场景不该补却补了。
2. **根因判断错误**：丢字段不是 LLM"忘了"沿用，而是它认为该意图是新提出的不需要沿用，或该字段本轮确实不该有——补全逻辑强行加回，破坏了正确输出。
3. **prompt 自检 + summary 校验扩展无效**：补充轮 summary 校验触发的重采样没改对，反而增加干扰。

**结论**：**v5.0 整体无效，净负效果，已回退**。补充轮丢字段问题不能用"自动补全 coreference"的规则方法解决——它需要更精细的"该意图是否该沿用 prev"的判断，而非无脑补全。

**回退状态**：代码回到 v4.0（取消意图处理）。保留的函数定义（`_complete_prev_fields`/`_is_supplement_turn`）未调用，prompt 自检规则已移除。

**下一步方向修正**：补充轮丢字段需换思路——
1. 显式 record/意图选择器（精准判断沿用关系，而非无脑补全）
2. 数据去噪（T3-3，GT 噪声清单已整理）
3. 不再用激进的规则后处理改 delta，改在 prompt 侧用 demonstration 引导（展示正确的补充轮 coreference 示例）

---

## v6.0 — 2026-08-17  噪声报告修正 + prompt record 选择规则对齐数据集

**背景**：尝试数据去噪时发现 v3.1 的噪声清单是脚本 bug 误报。精确扫描后发现数据集无标注噪声，真问题是模型 record 选择策略错误（"按上次记录"≠取 records[0]）。

**重要修正（数据集认知）**：
- **v3.1 噪声清单是误报**：`_gen_noise_report.py` v1 有两处 bug：
  1. `record_sourced_slots` 含 Memory 来源（make_split:115 `if st in ('Record','Memory')`），Memory 值本不在 records，误判为"找不到"
  2. 类别B 把数据集指代消歧设计（evidence.query）误判为"指代最近但取非records[0]的噪声"
- **精确扫描结果**（`_gen_noise_report.py` v2）：Record source_ref 值不匹配 0 条、record_id 不存在 0 条 → **数据集无标注噪声**
- **GT record 选择规则分析**（`_analyze_record_rule.py`，已删）：
  - 63 个 Record 字段，GT 选 records[0] 仅 8 个（13%），选非 records[0] 55 个（87%）
  - GT record app 匹配 intent app 仅 26%，svc 匹配 39% — 不是按 app/svc 选
  - idx 分布散在 0-11 — 不是按最近选
  - 只有 T3-3（7个）有 evidence.query 规则（按 service/date 过滤）；其他 template 的 record 选择规则数据集未暴露
  - 模型取 records[0] 字段值与 GT 一致率仅 38%

**方法改动**：
- `_gen_noise_report.py` 重写 v2：修取值 bug，重新生成 `data/_record_noise_problems.md`，标注类别A/C 为 0（无噪声），类别B 为数据集指代消歧设计（非噪声）+ v1 误报说明
- `prompt_builder.py`【记录选择规则】新段：把"按上次记录=取 records[0]"改为"按用户指代条件（service/app/date）过滤候选再选最近"；【选择链推理】示例改为展示过滤逻辑（用户指代云游戏→过滤 service=云游戏→取最近）

**涉及文件**：`_gen_noise_report.py`（重写）、`prompt_builder.py`、`data/_record_noise_problems.md`（重新生成）

**实验结果（逐轮真实透传，163 sessions / 336 turn-intent cases）**：
| 口径 | v4.0 | v6.0（record 规则对齐） | 变化 |
|------|------|----------------------|------|
| session 级 JGA | 55.83% (91/163) | 56.44% (92/163) | +0.61pp |
| turn 级 JGA | 58.04% (195/336) | 56.85% (191/336) | −1.19pp |

按 template 关键变化：T2-4 session 80%→93.3%、T1-4 session 25%→25%（持平）、T3-3 session 0%→0%（仍全错）。

**结论**：session 级小幅提升，turn 级略降（prompt 变长引入噪声）。核心价值是修正了错误规则（"取 records[0]" 87% 是错的）和澄清数据集无噪声。T3-3 仍全错说明模型执行过滤不稳定——GT 的 record 选择规则对非 T3-3 不可预测（无 evidence），prompt 对齐帮助有限。

**下一步**：
1. T3-3 的 evidence.query 可注入 prompt（test 时模型能看到指代条件提示）— 但 evidence 是 GT 元信息，注入有作弊风险，需谨慎
2. 补充轮丢字段仍需解决（显式选择器，非规则补全）
3. 当前最佳仍为 v6.0（session 56.44% / turn 56.85%）

---

## v7.0 — 2026-08-18  dataset_merged 评估（test_ratio=0.4，样本量翻倍）

**背景**：用户提供新数据集 dataset_merged（882 sessions，比 dataset_2 的 814 多 68，结构一致），并要求增大 test 比例重新评估当前 v6.0 方案。

**数据改动**：
- `data/dataset_merged/`：882 sessions，16 template，users_3000.jsonl（结构同 dataset_2，全 .jsonl 格式）
- split：test_ratio **0.2 → 0.4**，seed=42 分层抽样
  - train 530（检索库，原 651）→ `data/case_bank_train_merged.jsonl`
  - test 352（原 163）→ `data/test_cases_merged.jsonl`（827 turn-level cases）
- 新建 `config_merged.json`：embed cache 指向 `data/embed_cache_merged.json`（避免和 dataset_2 的 cache 混）
- `make_split.py` / `build_casebank.py` / `session_eval.py` 均通过 `DST_DATASET` 环境变量 + `--config`/`--split` 参数支持新数据集，不改 dataset_2 数据

**方法改动**：无（v6.0 方案：取消意图处理 + record 规则对齐 + PAR）。仅 `session_eval.py` 加 `--config` 参数。

**涉及文件**：`config_merged.json`（新）、`data/split_merged.json`、`data/test_cases_merged.jsonl`、`data/case_bank_train_merged.jsonl`、`data/embed_cache_merged.json`、`session_eval.py`（加 --config）

**实验结果（逐轮真实透传，dataset_merged 352 sessions / 733 turn-intent cases）**：
| 口径 | dataset_merged (352) | dataset_2 (163, v6.0) | 差异 |
|------|---------------------|----------------------|------|
| session 级 JGA | 51.42% (181/352) | 56.44% (92/163) | −5.02pp |
| turn 级 JGA | 52.80% (387/733) | 56.85% (191/336) | −4.05pp |

按 template（dataset_merged session 级）：
- T1-1 86.4%、T1-2 100%、T1-3 69.4%、T1-4 29.4%、T2-4 55.6%、T2-6 44.1%、T3-3 **0/18=0%**、X-1 70.6%
- 与 dataset_2 一致的趋势：T1-2/T2-4/T1-1 高，T3-3 全错、T1-4/T2-2/T2-6 低

错误分布（按比例与 dataset_2 一致）：session field_wrong 88(24.5%)/intent_set_mismatch 83(23.0%)；turn value_wrong 151/missed_intent 131/time_wrong 64。

**结论**：
1. session 级 51.42%，比 dataset_2(56.44%) 降 5pp，主因：①样本量翻倍后难题占比增大（T2-6 16→34、T3-3 7→18）②train 库缩小（651→530，test_ratio 0.2→0.4）检索质量略降。
2. 方法表现一致（错误分布同比例），无回退，是数据规模放大难度。
3. T3-3 仍全错（0/18），record 选择规则不可预测问题在新数据集同样存在。
4. **当前方案在更大样本上的真实表现：session 级 ~51%、turn 级 ~53%**，比小样本(163)的 56% 更保守可信。

**下一步**：
1. 仍待解决 T3-3（record 选择）和补充轮丢字段——方法层面未突破。
2. dataset_merged 作为更大 test 基准，后续改进在此评估更可信。

---

## v8.0 — 2026-08-18  取消意图识别增强（prompt demo 扩展失败 + 校验回路保留）

**背景**：dataset_merged 错误分析显示 session 级头号错误是取消意图没处理（extra 类 59 个），T2-6 取消场景仅 15/34=44.1%。v4.0 取消机制已就位但识别不全。尝试增强取消识别。

**方法改动尝试**：
1. `prompt_builder.py`【取消意图】段扩展：加 3 个取消 demonstration（"撤了吧/不弄了/照旧"等）+ 扩展取消触发词列表
2. `pipeline.py`：新增 `_CANCEL_WORDS` + `_cancel_check_ok()`，取消场景（utterance 含取消词）下若输出缺 `# cancel` 段则触发重采样1次；`_need_par` 扩展到取消场景

**涉及文件**：prompt_builder.py、pipeline.py

**实验结果（逐轮真实透传，dataset_merged 352 sessions / 733 turn-intent cases）**：

| 配置 | session 级 | turn 级 | 说明 |
|------|-----------|---------|------|
| v7.0（v6.0 方案 merged 基线） | 51.42% (181/352) | 52.80% (387/733) | `eval_dual_merged_v6_full` |
| v8.0-a（prompt 加 3 demo + 校验回路） | 50.00% (176/352) | 52.11% (382/733) | **净负，已回退 prompt** |
| **v8.0-b（回退 prompt，仅保留校验回路，当前最佳）** | **52.27%** (184/352) | 52.80% (387/733) | `eval_dual_merged_cur_full4` |

按 template（session 级，v7.0→v8.0-b）：T2-6 取消场景 15/34(44.1%)→17/34(50.0%)、T2-4 20/36→22/36、X-1 12/17→12/17、T3-3 0/18→0/18（仍全错）。

**失败与成功分析**：
- **v8.0-a 净负 −1.42pp**：prompt 加 3 个取消 demonstration 变长，影响非取消类（T2-3/T2-5/T2-1 回退），抵消 T2-6 取消小提升。变好 21 vs 变差 26 session。
- **v8.0-b +0.85pp**：回退 prompt 后，保留 `_cancel_check_ok` 校验回路（取消场景触发重采样，无害），T2-6 取消场景 44.1%→50.0%。
- **教训**：prompt 加 demonstration 对 32b 是双刃剑——针对性帮一类，噪声扰其他类。校验回路（规则触发重采样）比 prompt 加 demo 更安全。

**结论**：v8.0-b（v6.0 prompt + 取消校验回路）为 dataset_merged 当前最佳：session 52.27% / turn 52.80%，比 v7.0 +0.85pp。取消识别增强部分成功——校验回路保留，prompt demo 回退。

**当前代码状态**：
- `prompt_builder.py` 取消段：已回退到 v6.0 版本（去掉 3 个 demo）
- `pipeline.py`：保留 `_CANCEL_WORDS` + `_cancel_check_ok` + 校验回路扩展（取消场景触发重采样）

**下一步**：
1. T3-3 record 选择（仍 0/18，evidence.query 注入有作弊风险）
2. 补充轮丢字段（field_wrong 88，显式选择器非规则补全）
3. missed_intent 129（取消场景的保留意图输出不稳定）

---

## v9.0 — 2026-08-18  显式 record 选择器（策略A vote / 策略B reinject，均失败回退）

**背景**：v8.0-b 后 T2-6 仍剩 17/34 错、T3-3 仍 0/18。再分类 T2-6 失败发现真根因不是取消识别（仅 1 个 extra），而是 **record coreference**：turn1 含"时延按上次的记录"取 rtt，prev_state 空 + 多候选 record 时 LLM 全空(null_pred)或选错(field_wrong)。v5.0"自动补全 coreference"已失败，本次换"显式选择器"思路（用户指定）。

**关键验证（基于真实数据，指导设计）**：
1. 评估比字段值不比 record_id → 选择器选哪条 record 不重要，字段值==GT 即判对。
2. GT 字段值 100% 在画像 recent_records 存在（T3-3 全 44 样本验证；T2-6 8 null_pred 的 GT rtt 都在画像里）。
3. 当前 `_delta_from_record` 兜底仅在 `all_empty` 触发且只取 recs[0]，而 recs[0] 命中率低（T3-3 各 shape 0/8~3/5）。
4. T3-3 evidence.query 的 6 shape 等价条件可从 utterance 抽，但 query 是 GT 元信息不能注入 prompt（作弊）；且 GT 选中 record 的 record_id 不符合 utterance 过滤条件，但字段值维度 100% 在画像存在。
5. Plan agent 诚实预估上限：T2-6 rtt ~42%，T3-3 60-80%。

**方法改动**：
- `pipeline.py` 新增显式 record 选择器（config 开关 `record_selector.enabled` + `mode`）：
  - `_extract_record_filters(utterance)`：从 utterance 抽 service/app/date/ordinal/history_range 过滤条件
  - `_select_record_field_vote(filters, records, slot)`：策略A，加权众数（全局x2+app-filter x2+svc-filter x1+最近 x1）
  - `_reinject_candidates(pipe, ...)`：策略B，候选 records 明细注入 prompt 调 LLM 二次选择
  - `_missing_record_slots(delta, final_flat, prev_flat)`：丢字段检测（形态①delta空 / 形态②final=None）
  - `_merge_delta(existing, fill_flat)`：只补缺失不覆盖（复用 case_format.nested_from_flat）
  - `_apply_record_selector(segs, finals, ...)`：统一入口，三重触发（record coref 词 + prev 缺字段 + 真丢字段）
- `pipeline.py` `run_multi`：segs 循环 validate 后插入 `_apply_record_selector`，重新 validate
- `session_eval.py` `_run_multi_no_retrieve`：同步调用 `_apply_record_selector`（保证 on/off retrieve 口径一致）
- `config_merged.json`：加 `record_selector {enabled, mode}`
- 避开 v5.0 误触发坑：三重触发条件 + 只补缺失不覆盖

**涉及文件**：pipeline.py、session_eval.py、config_merged.json

**实验结果（逐轮真实透传，dataset_merged 352 sessions / 733 turn-intent cases）**：

| 配置 | session 级 | turn 级 | T2-6 sess | T3-3 sess | T3-3 turn |
|------|-----------|---------|-----------|-----------|-----------|
| v6 baseline（selector off，本次复跑） | 50.28% (177/352) | 52.25% (383/733) | 15/34 | 0/18 | 4/40 |
| v9.0 策略A（vote） | 49.72% (176/352) | 51.98% (381/733) | 15/34 | 0/18 | 6/40 |
| v10.0 策略B（reinject） | 49.15% (173/352) | 51.43% (377/733) | 14/34 | 0/18 | 5/40 |

session 错误分布（baseline→vote→reinject）：field_wrong 94→92→93；intent_set_mismatch 81→84→86。

**失败分析（vote 翻转样本）**：rescued 5 / regressed 6，净 -1 session。
- 4 个 regression 是 `intent_set_mismatch missing`：选择器回填 app/svc/resolution 后重新 validate，导致 intent_key 漂移或某意图消失（拼多多/快手等被吞）。
- 2 个 regression 是 `field_wrong` 时间字段：回填非时间字段后 validator 重新算时间，破坏原本正确的时间。
- 根因：选择器在 validate 后二次干预，引入 LLM 输出之外的值，`_merge_delta`"只补缺失"保护不住"重新 validate 连带破坏时间/intent_key"。

**结论**：两策略均未达基线（vote -0.56pp、reinject -1.13pp session），**已回退**（config `enabled=false`）。验证了 Plan agent 预估——选择器修对的丢字段被新引入的错抵消反超。T3-3 仍 0/18，record 选择问题方法层面仍未突破。

**回退状态**：`config_merged.json` record_selector.enabled=false（完全走 v6.0+v8.0-b 路径）。选择器函数定义保留但 `enabled=false` 时不调用。

**教训**：
1. "只补缺失不覆盖"保护不住"重新 validate 连带破坏"——二次干预破坏了 LLM 输出与 validator 的一致性。
2. GT record 选择规则在字段值维度虽 100% 在画像存在，但选择器看不到 GT，众数/投票策略命中率不足以覆盖副作用。
3. 显式选择器（代码侧选值）对本数据集不可行，与 v5.0（规则补全）结论一致：record coreference 丢字段无法靠后处理修复，需在生成侧（更大模型/更好的 demonstration）解决。

**下一步方向修正**：
1. 不再用后处理选值修复 record coreference（v5.0/v9.0 两次失败）。
2. 若再优化，方向是生成侧：更大模型、或检索库注入更精准的 record 选择 demonstration（而非 GT evidence.query）。
3. 当前最佳仍为 v8.0-b（session 52.27% / turn 52.80%，本次复跑 50.28%/52.25% 受 LLM 随机性影响）。

---

## v11.0 — 2026-08-19  选择链 demonstration（生成侧，+1.99pp 首次显著超基线）

**背景**：接续 v9.0 失败教训——record coreference 丢字段（T3-3 0/18、T2-6 record coref 类 null_pred/field_wrong）无法靠后处理选值修复，须在生成侧用更精准 demonstration 解决。本版转向生成侧：对 record coref 类 demo 自动生成"选择链 summary"注入 prompt，让 LLM 看到正确的 record 选择推理过程，而非靠选择器回填。

**方法改动**：
- `prompt_builder.py` 新增 `_build_selection_chain(c)`：仅当 retrieved demo 的 delta 含 Record/Memory sourced 字段时，自动生成选择链 summary——先用 utterance 指代词判来源（Record="上次/记录/那条" vs Memory="平时/习惯"），再抽取指代条件（service/app/date/取最近），最后回溯 delta 字段值拼成"用户话语含画像指代(X), 取字段: ..., 选择链: 按条件过滤→取字段值"。
- **零作弊设计**：只读 utterance 指代词 + delta 已有字段值回溯，**不读 GT evidence.query**（区别于 v9.0 选择器看候选 record）。chain 为空时回退占位"(示例, 省略)"，不影响非 record coref 类 demo。
- `_format_case` 改用 `_build_selection_chain` 产出的 chain 作 demonstration 的 summary，使检索到的正/负例在 prompt 里展示完整的选择链推理。
- `_SHARED_RULES` 新增【选择链推理 — PAR 核心】段 + 2 个示例（用户指代云游戏→过滤 service=云游戏→取最近；用户无指代→取 records[0]），与 demonstration 形成规则+示例双引导。
- 取消 v8.0-a 的"prompt 加取消 demo"路线（已证明净负），v11 demo 只针对 record coref。

**涉及文件**：`prompt_builder.py`（`_build_selection_chain` / `_format_case` / `_SHARED_RULES` 选择链段）、`config_merged.json`（record_selector 保持 enabled=false，仍走 v6.0+v8.0-b 基线 + v11 demo 注入）。

**实验状态**：dataset_merged 352 sessions / 733 turn-intent cases，逐轮真实透传双口径，retrieve=on，top_k=3。
- 命令：`python session_eval.py --config config_merged.json --retrieve on --tag v11_demo_chain --split data/split_merged.json`
- tag `v11_demo_chain`，**已完成**。产物路径 `data/evals/eval_dual_v11_demo_chain.jsonl` / `_summary.json`。

**实验结果（逐轮真实透传，dataset_merged 352 sessions / 733 turn-intent cases）**：

| 配置 | session 级 | turn 级 | T2-6 sess | T3-3 turn | T2-4 sess | field_wrong | missed_intent |
|------|-----------|---------|-----------|-----------|-----------|-------------|---------------|
| v6 baseline（本次复跑） | 50.28% (177/352) | 52.25% (383/733) | 15/34 | 4/40 | 20/36 | 94 | 128 |
| **v11.0 选择链 demo** | **52.27% (184/352)** | **55.12% (404/733)** | **18/34** | **9/40** | **23/36** | 91 | 117 |

- session +1.99pp、turn +2.87pp，**首次显著超越基线**。
- 分类全面改善：T2-6 session 15→18（+3）、T3-3 turn 4→9（+5，session 仍 0/18 但累积态提升）、T2-4 session 20→23（+3）、T2-4 turn 56→59、T2-5 session 9→10、T2-3 turn 21→24。
- 错误分布：session field_wrong 94→91、intent_set_mismatch 81→77；turn value_wrong 160→146、missed_intent 128→117、time_wrong 61→66（略升）。
- **非目标类持平**（T1-1/T1-2/T1-3/T1-4/T2-1/T3-1/T3-4/X-1 session 不变），验证隔离正确——未重蹈 v8.0-a"加 demo 扰非目标类"净负覆辙。

**结论**：生成侧选择链 demo 成功。验证 v9.0 失败教训方向正确——record coreference 丢字段须在生成侧解决，而非后处理选值。零作弊（不读 GT evidence.query，仅用 utterance 指代词 + delta 已有值回溯），选择链 summary 注入 prompt 让 LLM 看到正确的 record 选择推理。**v11 为 dataset_merged 当前最佳：session 52.27% / turn 55.12%**。

**残留问题**：T3-3 session 级仍 0/18（turn 级提升说明累积态改善但最终集合仍未全对，GT record 选择规则 record_id 维度不可预测）；time_wrong 61→66 略升（demo 变长轻微影响时间推算）。

**当前代码状态**：`prompt_builder.py` 保留 `_build_selection_chain` + `_format_case` 改用 chain summary；`config_merged.json` record_selector.enabled=false（v9.0 选择器仍回退不调用）。

---


## v12.0 纠正类 demonstration 注入（失败回退，2026-08-19）

**动机**：T2-3/T3-2（Correct_Previous_Input+Negate 全量模板）v11 仅 38%/33%。论文调研（B5 RoSDistillation 实证 base LLM 天然偏向先提旧值；B1 delta 覆盖语义）→ 生成侧注入"后值覆盖前值"的纠正类 demo。

**改动**：
- `pipeline.py`：新增 `_is_correction`（改口词：不是/不对/错了/改成/改为/换成/应该是/搞错等）+ `_is_correction_case`（utterance 含改口词且 delta 覆盖 prev_state 同名字段为新值，零作弊只用 prev/delta 回溯）+ `_retrieve_correction_demos`（全库按 embedding 相似度选 top-n 纠正 demo，前置注入 retrieved）。config 开关 `correction_demo.enabled`（回退置 false）。
- `prompt_builder.py`：`_SHARED_RULES` 新增【改口/否定】规则段（必须赋新值覆盖、summary 写明"X→Y"、service 改口时 intent 头用新 service）；`_format_case` 增加 `_build_correction_chain` 改口 summary（"用户改口/否定: slot: 旧值→新值"）。
- 冒烟：train bank 1272 cases 中检出纠正类 58 条，注入命中准确（如 S-000108-T2 "不是视频通话，是腾讯会议" sim=0.934）。

**实验结果（dataset_merged 352，同 v11 条件，tag v12_correction_demo）**：

| 配置 | session 级 | turn 级 | T2-3 sess | T2-5 sess | T3-2 sess |
|------|-----------|---------|-----------|-----------|-----------|
| v11.0（最佳） | 52.27% (184/352) | 55.12% (404/733) | 8/21 | 10/16 | 6/18 |
| **v12.0 纠正 demo** | **47.16% (166/352)** | **50.48% (370/733)** | **0/21** | **4/16** | **4/18** |

- 全局 -5.11pp/-4.64pp，目标模板反被砸穿：T2-3 session 8→0、T2-5 10→4、T3-2 6→4。**净负，已回退（correction_demo.enabled=false）**。

**失败根因（llm_raw 逐案分析，v12 起 eval 已存每轮原始输出）**：
- 注入的纠正 demo 是"单字段 delta 短输出"且 demonstration 格式本身不含 `# intent:` 头 → 模型模仿成"summary + 赋值、省略 intent 头"。execute_program_multi 对无头段 key="" → _match_prev_state 返回 {} → **delta 整段丢弃，prev_state 原样沿用**。
- 统计（非首轮）：72/376 输出缺 `# intent` 头，其中 63 个失败；T2-3 15/21 无头全败、T2-5 11/16 无头全败、T3-2 20/36 无头 19 败。
- 典型案例 S-000123：raw 里 summary 正确（"resolution 从 2160p 改为 1080p"）、赋值正确（state.resolution='1080p'），但无 `# intent` 头 → pred 仍是 prev 的 2160p。
- 连带：record 70→77、memory 16→21、value 45→62（demo 变长+改口规则"不要沿用旧值"误伤正常补充轮 coreference）。

**教训（第三次生成侧 demo 类失败，与 v8.0-a 同型）**：
1. demonstration 的输出格式必须与被要求的目标格式严格一致——demo 无 `# intent` 头而系统要求有头，注入强相似短 demo 会把模型拉向 demo 的"裸赋值"格式。**后续任何 demo 注入须先在 `_case_to_program` 补 `# intent:` 头再试**。
2. 改口问题的另一个方向是 v13 del 操作符（显式 `del state.xxx` + 执行/校验侧支持），或先做"无头段兜底归并到唯一 prev intent"的解析侧修复（低成本，可独立验证）。

**当前代码状态**：config_merged.json correction_demo.enabled=false（代码保留，不调用）；dataset_merged 当前最佳仍 v11（session 52.27% / turn 55.12%）。产物 `data/evals/eval_dual_v12_correction_demo.jsonl`/`_summary.json`/`_errors.html`。

## v12.1 demo 补 intent 头 + 无头段兜底（成功，当前最佳，2026-08-20）

**动机**：修复 v12.0 失败根因（注入 demo 无 `# intent` 头 → 模型模仿省略 → 无头段 delta 丢弃）。

**改动**：
- `prompt_builder._case_to_program`：demonstration 输出补 `# intent: app/svc` 头（从 prev+delta 最终态推导，无 app 则 none），demo 格式与目标格式严格一致。
- `python_state.execute_program_multi`：无头段（key=""）且 prev 只有一个意图时**执行前**归并到该意图（coreference 对真实 prev 解析，delta 正常落盘）；多意图 prev 不归并（保守）。`pipeline.run_multi` 同步加一层兜底。
- `config_merged.json`：correction_demo.enabled=true（v12.0 的纠正 demo 注入保留开启）。

**实验结果（dataset_merged 352，同 v11 条件，tag v12_1_correction_demo）**：

| 配置 | session 级 | turn 级 | T2-3 sess | T2-5 sess | T3-2 sess | T3-4 sess |
|------|-----------|---------|-----------|-----------|-----------|-----------|
| v11.0（旧最佳） | 52.27% (184/352) | 55.12% (404/733) | 8/21 | 10/16 | 6/18 | 7/18 |
| v12.0（失败） | 47.16% | 50.48% | 0/21 | 4/16 | 4/18 | — |
| **v12.1** | **55.97% (197/352)** | **56.07% (411/733)** | **9/21** | **11/16** | **10/18** | **11/18** |

- **session +3.70pp、turn +0.95pp，全面超越 v11 创新高**；剔除 record 口径 63.92%/62.62%（v11 同口径 59.09%/61.26%）。
- 目标模板：T3-2 session 6→10（+4）、T2-5 10→11、T2-3 8→9（turn 24→22 略降）；非目标模板 T3-4 7→11、T2-1 4→7（兜底红利），T2-2 9→7 略降。
- **机制验证**：非首轮无 intent 头输出 72/376 → **2/376**，demo 补头根治格式模仿；兜底作为安全网（X-2 残留 2 例）。

**结论**：v12.0 失败方向没错、错在 demo 格式不一致。改口/否定场景（Correct_Previous_Input+Negate）通过"纠正 demo + 格式对齐"可解，无需 del 操作符（v13 暂缓）。**v12.1 为 dataset_merged 当前最佳：session 55.97% / turn 56.07%**。

**当前代码状态**：correction_demo.enabled=true（生效）；record_selector.enabled 仍 false。产物 `data/evals/eval_dual_v12_1_correction_demo.jsonl`/`_summary.json`/`_errors.html`。（注：曾有"遮 record 字段"离线口径 score_no_record.py，用户判定口径不符已废弃删除，2026-08-21。）

## v12.2 prompt 画质映射纠错（打平，2026-08-21）

**动机**：发现 GT source_ref 自带 `resolution_quality_map:app:svc:词`，prompt 旧近似表与真实映射有实质出入（高清@网易云游戏=1080p、高清@优酷/视频=540p 等），prompt 在教错规则。

**改动**：`prompt_builder._SHARED_RULES` 画质段拆为 `_QUALITY_SECTION_APPROX`(旧)/`_QUALITY_SECTION_FULL`(全数据集 source_ref 还原的 73 条映射，0 冲突，表外组合用默认值，含"网络流畅非画质词"提示)；`set_full_quality_map` 切换，config `full_quality_map`（true/false 可回退）；pipeline 初始化时按 cfg 设置。

**实验结果（dataset_merged 352 全集 / 剔除 49 噪声 303，tag v12_2_full_qmap）**：

| 口径 | v12.1 | v12.2 | Δ |
|---|---|---|---|
| session（全集） | 55.97% (197/352) | 54.55% (192/352) | −1.42pp |
| turn（全集） | 56.07% (411/733) | 56.75% (416/733) | +0.68pp |
| session（剔除噪声） | 62.71% (190/303) | 61.39% (186/303) | −1.32pp |
| turn（剔除噪声） | 60.10% (378/629) | 61.05% (384/629) | +0.95pp |

- **有效证据**：其他字段值错误 39→27（−12，画质映射纠错生效）；意图漏识别 91→78（−13）。
- **代价**：时间推算错误 66→85（+19，prompt 变长挤压时间推算注意力）；T1-1 18→16、T2-4 25→23、T2-6 20→18 略降，T2-3 9→10、T3-2 9→11 略升。
- **判定：打平（session 微负 / turn 微正）**。规则纠错方向正确（值错误实降），但被时间推算回退抵消。开关保留 true，待观察或后续收紧时间规则段。

产物 `data/evals/eval_dual_v12_2_full_qmap.jsonl`/`_summary.json`/`_errors.html`。

## v13.0 意图状态机 Collecting/Ready/Confirmed/Cancelled（旁路上线，2026-08-24）

**动机**：在 JGA 之外新增意图状态跟踪口径（用户设计：Collecting 阻塞性缺失/模糊/错误、Ready 参数完整+校验过、Confirmed 用户明确确认、Cancelled 用户取消），用于区分"参数错"与"状态流转错"，并为下游 agent 动作决策打底。

**改动**（纯旁路，不改 JGA 主流程；四个独立消融开关默认全 false = v12.2 基线）：
- 新文件 `intent_status.py`：`is_affirm`（Affirm 规则识别，召回 117/117=100%、误报 0）、`has_pending_choice`（选择疑问检测，对 agent Request_Disambiguation 召回 48/78=62%、误报 0）、`derive_status`（Cancelled>Confirmed>Ready>Collecting，五槽齐=Ready 的操作定义，Ack 912 例全五槽齐实证）、`build_gt_status_trajectory`（GT 轨迹离线重建：Ready 1075/Collecting 224/Confirmed 119/Cancelled 86）、`judge_status`。
- `prompt_builder.build_prompt` 加两个可选段：AGENT LAST REPLY（`agent_context.enabled`，对症 T3-3 消歧信息不对称）、INTENT STATUS 快照（`status_prompt.enabled`）。
- `pipeline.run_multi` / `session_eval` 透传 agent_reply + prev_statuses；session_eval 新增状态口径输出（整体+分桶+混淆+开关记录）。
- `_sanity_status.py` 完美模型链路自洽冒烟（不调 LLM）：**上限 1448/1504=96.28%**（Ready/Cancelled 100%，Confirmed 98.3%，Collecting 75.9%——54 例损失全是 record 消歧，话语无信号）。

**冒烟**（dataset_merged，真实 LLM）：四开关全开 4 session 状态 8/9=88.9%（Confirmed 1/1、Cancelled 2/2、Collecting 1/1）；仅状态两开关 8 session 19/25=76%（Cancelled 5/5）。JGA 与基线一致（旁路无副作用）。全量消融待跑（顺序见 report_flow.md §4）。

**文档**：`report_flow.md`（全流程+S-000112 端到端实例 Collecting→Ready→Confirmed + S-000008 取消实例 + 已知边界清单）。

产物 `data/evals/eval_smoke_status.jsonl`、`data/evals/eval_smoke_status_full.jsonl`、`configs/config_smoke_status.json`、`data/split_mini_status.json`。

**v13.0 全量回归（status_obs，2026-08-24）**：status_tracking+confirm_rule 开（纯旁路，不改 prompt），dataset_merged 剔除 49 噪声 303 sessions：session 62.71% (190/303) / turn 61.84% (389/629)，与 v12.1 数字一致、与 v12.2 (61.39%/61.05%) 同水平（±1.5pp API 波动），**旁路无副作用全量实证**。首个全量状态口径基线：整体 489/621=78.74%（Cancelled 37/38=97.4%、Ready 373/438=85.2%、Confirmed 32/43=74.4%、Collecting 47/102=46.1%）。Collecting 最弱：主因 Collecting->Ready 混淆——record 消歧 30 例无话语信号（已知边界）+ 模型超前补齐槽位（pred 五槽齐判 Ready，GT 按剧本 agent 仍 Request_Slot 判 Collecting，属口径固有张力非纯错误）。产物 `data/evals/eval_dual_v13_status_obs.jsonl`/`_summary.json`。下一步全量消融：+agent_context（验证 T3-3 消歧）、+status_prompt。

## v13.1 agent_context 上轮 agent 回复进 prompt（显著正向，2026-08-24）

**动机**：T3-3 消歧 0/18 的根因是信息不对称——agent 话术含 record 候选详情但模型看不到 agent 轮。把上一轮 agent 回复作为上下文段（AGENT LAST REPLY）注入 prompt（输入侧信息，非状态 GT；与 MultiWOZ 提供 system utterance 同口径）。

**实验（dataset_merged 剔除 49 噪声 303，tag v13_agentctx，其余开关同 status_obs）**：

| 口径 | status_obs | +agent_context | Δ |
|---|---|---|---|
| session | 62.71% (190/303) | **66.01% (200/303)** | **+3.30pp** |
| turn | 61.84% (389/629) | **65.66% (413/629)** | **+3.82pp** |
| 状态机 | 78.74% (489/621) | 80.19% (498/621) | +1.45pp |

- **T3-3 破零**：session 0/18→3/18，turn 5/40(12.5%)→13/40(32.5%)——信息不对称根因判断实证。
- 广谱收益：T2-2 消歧 turn 71.4%→82.1%（session 7→9）、T2-6 session 17→20、T3-2 session 11→13、X-2 4→6；value_wrong 97→80、time_wrong 64→53。
- 代价：T2-5 session 11→8（−3）、T3-4 10→9、T1-4 6→5；missed_intent 78→82（+4）。
- **剔除噪声口径历史新高**（超 v12.1 的 62.71%/60.10%）。自 v12.1 以来最大单点提升。

产物 `data/evals/eval_dual_v13_agentctx.jsonl`/`_summary.json`，config `configs/config_abl_agentctx.json`。

**v13.1 补充：status_prompt 叠加消融（全开，2026-08-24）**：在 agent_context 基础上叠 status_prompt：session 65.35% (198/303) / turn 63.59% (400/629)，对比 agent_context 单开（66.01%/65.66%）**session −0.66pp、turn −2.07pp 打平微负**。但 T3-3 继续改善：session 3→6/18、turn 13→15/40（状态快照让模型感知 intent 处于 Collecting）。涨跌互现：T1-3 +3、T2-5 +2；T2-2 −2、T3-2 −2、T2-6 −2。**判定：status_prompt 默认 false（开关保留，待数据集扩充后再评估）；当前最佳配置 = agent_context on + 其余 off（session 66.01% / turn 65.66%）**。产物 `data/evals/eval_dual_v13_all.jsonl`/`_summary.json`，config `configs/config_abl_all.json`。

## v14.0 换模 qwen-plus（显著正向，历史新高，2026-08-25）

**动机**：qwen-plus 与 qwen3-32b 同走百炼 dashscope API，验证同方法栈下更大商用模型的收益（此前上限判断"当前方法栈严格 ~70%"隐含模型是瓶颈之一）。

**改动**：仅 `llm.model`: `qwen3-32b` → `qwen-plus`（temperature=0 等其余参数不变），配置 = v13.1 最佳（agent_context on + status_tracking/confirm_rule 观察口径），新配置 `configs/config_qwen_plus_agentctx.json`。零代码改动。

**实验结果（dataset_merged，tag v14_qwen_plus / 全集 v14_qwen_plus_full）**：

| 口径 | v13.1 (qwen3-32b) | v14.0 (qwen-plus) | Δ |
|---|---|---|---|
| session（剔除噪声 303） | 66.01% (200/303) | **73.93% (224/303)** | **+7.92pp** |
| turn（剔除噪声 629） | 65.66% (413/629) | **71.22% (448/629)** | **+5.56pp** |
| 状态机（剔除噪声 621） | 80.19% (498/621) | 81.00% (503/621) | +0.81pp |
| session（全集 352） | 55.97%（v12.1，v13.1 未跑全集） | **68.18% (240/352)** | +12.21pp vs v12.1 |
| turn（全集 733） | 56.07%（v12.1） | **66.71% (489/733)** | +10.64pp vs v12.1 |
| 状态机（全集 723） | — | 80.64% (583/723) | — |

- **逐样本翻转：获救 39 / 回退 15（净 +24）**。获救原错误集中：时间推算（跨日/半点算错）、record rtt 取值、漏意图（32b 输出空段，S-000066/S-000147）、duration 格式未归一化（"3h" vs "180min"，S-000121）。回退 15 例中 13 例 field_wrong：个别 record 取值带偏（S-000486 rtt 150ms/20ms）、补充轮丢 duration/end（S-000935）、record 时间污染覆盖用户明说时段（S-000075）。
- **分模板（session，剔除噪声）大涨**：T1-3 25→35/36（+10）、T2-5 8→13/13 满分（+5）、T1-1 17→21、T3-4 9→13、T2-4 24→26、T2-6 20→21。小跌：T3-2 13→11、T3-3 3→2、T2-2 9→8、T3-1 10→9。
- **T3-3 record 消歧仍硬瓶颈**（session 3→2/18，turn 13→9/40）：信息不对称在 32b 上是主因，换更强模型无收益反微跌，实证 T3-3 是评测设计/输入信息问题而非模型能力问题。
- **错误分布（err_report 口径，剔除噪声）**：漏意图 82→63（−19）、时间 64→51（−13）、其他字段值 31→11（−20，归一化能力收益）、memory 7→5；session 级集合不一致 50→35、字段错误 53→44。**唯一上升：record 提取错误 32→51（+19）**——qwen-plus 更倾向取画像 record 值，个别样本被带偏（与回退 15 例的 record 特征一致），T3-3 消歧仍集中于此。
- 状态机：Collecting 48→54/102 改善，Confirmed 32→29/43 微跌，Ready/Cancelled 持平。
- 速度：303 sessions 3350s（≈11s/session），与 32b 同量级。

**结论**：换模 qwen-plus 是自 v13.1 以来最大提升，**突破此前"方法栈上限 ~70%"判断——上限是模型的不是方法栈的**。当前最佳 = v14.0（v13.1 配置 + qwen-plus）：剔除噪声 session 73.93% / turn 71.22%，全集 68.18% / 66.71%。下一步方向不变：T3-3 需 agent 轮信息/数据集修订，非 prompt 或模型可解。

产物 `data/evals/eval_dual_v14_qwen_plus.jsonl`/`_summary.json`/`_errors.html`（剔除噪声）、`eval_dual_v14_qwen_plus_full.jsonl`/`_summary.json`（全集）、冒烟 `eval_dual_v14_qp_smoke.jsonl`（8/8），config `configs/config_qwen_plus_agentctx.json`。

## v15.0 数据集升级 dataset_merged_2.0 + 双模型重跑（2026-08-25）

**背景**：数据集升级到 dataset_merged_2.0（920 sessions = merged ∪ dataset_4 修复版；原 49 条 R5 record 噪声 33 条已修复、16 条残留见 `_noise_exclude.json`）。新 split（seed=42，test_ratio=0.4：train 552 / test 368；16 条噪声 11 条在 test → 有效 357）+ 新 case_bank + 新 embed_cache。在 2.0 上重跑双模型建立新基线（配置同 v14.0：agent_context on + status 观察口径，仅模型不同）。

**实验结果（dataset_merged_2.0，剔除 16 噪声后 357 test / 742 turn cases，tag v15_d2_*）**：

| 模型 | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| qwen3-32b | 56.86% (203/357) | 58.63% (435/742) | 78.10% (560/717) |
| **qwen-plus** | **68.91% (246/357)** | **67.65% (502/742)** | **82.15% (589/717)** |
| Δ | **+12.04pp** | **+9.02pp** | +4.05pp |

**全集 368 口径（并列，tag v15_d2_*_full）**：

| 模型 | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| qwen3-32b | 55.98% (206/368) | 56.75% (437/770) | 77.72% (579/745) |
| **qwen-plus** | **65.49% (241/368)** | **65.45% (504/770)** | **81.07% (604/745)** |
| Δ | +9.51pp | +8.70pp | +3.35pp |

（注：plus 全集重跑 241 < 剔除噪声跑的 246，超集数字反而更低——temperature=0 但 API 仍有运行间波动，幅度约 ±5 session ≈ ±1.5pp，与 v13.0 观察一致。）

- **逐样本翻转：获救 67 / 回退 24（净 +43）**。回退 24 例中 rtt 取值错占 10、resolution 5——record 取值被带偏仍是 plus 主要回退形态（与 v14.0 一致）。
- **分模板（session，plus vs 32b）**：大涨 T1-3 22→31/36、T2-6 22→28/35、T1-4 10→15/19、T2-4 19→24/36、T3-1 6→11/19、T3-4 9→14/20；**反跌 T3-3 4→1/19、T3-2 9→7/19**（record 消歧/改口多意图，与 v14.0 同向——强模型更倾向取画像值，消歧场景反受害）。
- **错误分布（err_report，plus vs 32b）**：漏意图 103→77、时间 86→61、memory 22→3、其他值 31→20、session 集合不一致 62→37、字段 92→74；**record 65→79 再次唯一反升**（+14，两版连续复现，实锤是模型行为差异非随机波动）。
- 状态机分桶（plus）：Cancelled 43/43=100%、Ready 87.5%、Confirmed 71.2%、Collecting 55.2%（仍最弱）。
- 与 1.0 数字不直接可比（test 构成不同：303→357，d4 修订 662 条话语改写）；趋势一致：plus 全面大幅领先 32b。

**结论**：2.0 新基线 = qwen-plus 68.91%/67.65%（剔除噪声）。双模型对比结论在 2.0 上复现并放大（+12pp session）；record 取值反升 + T3-3/T3-2 反跌是 plus 的两个稳定弱项，指向同一根因：强模型更积极引用画像 record。

产物 `data/evals/eval_dual_v15_d2_qwen_plus.jsonl`/`_summary`/`_errors.html`、`eval_dual_v15_d2_qwen3_32b.jsonl`/`_summary`/`_errors.html`、全集 `eval_dual_v15_d2_qwen_plus_full.jsonl` / `eval_dual_v15_d2_qwen3_32b_full.jsonl`、冒烟 `eval_dual_v15_d2_qp_smoke.jsonl`；配置 `configs/config_merged_2_qwen_plus_agentctx.json` / `config_merged_2_qwen3_32b_agentctx.json`（后者独立 embed cache 副本 `embed_cache_merged_2_32b.json` 防并行写竞争）。

## v16.0 数据集切 dataset_4 + status_prompt 状态辅助消融（微正，2026-08-26）

**背景**：① 用户决策：停用 dataset_merged_2.0，直接用 dataset_4（702 sessions，d4 修复版本身）评估。新配套：`split_d4.json`（seed42/0.4 分层 train 422/test 280）、`case_bank_train_d4.jsonl`（422 条，首次误用默认 dataset_2 构建仅 376 条已重建）、users_3000.jsonl 拷入 dataset_4（session_eval 从 DATASET_DIR 内加载画像）。16 条残留噪声全部落在 train 侧 → **test 280 无需剔除**（但噪声在检索库内，demo 教旧 record 规则，影响有限）。② 用户要求验证"意图状态辅助模型判断"——重检 `status_prompt`（v13.1 在 1.0+32b 上 −0.66pp 打平微负默认关）。

**实验（dataset_4 test 280 / 583 turn cases，qwen-plus，agent_context on，tag v16_d4_*）**：

| 配置 | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| baseline（status_prompt off） | 68.93% (193/280) | 67.58% (394/583) | 81.58% (465/570) |
| **+status_prompt** | **70.36% (197/280)** | **68.44% (399/583)** | 82.28% (469/570) |
| Δ | **+1.43pp** | +0.86pp | +0.70pp |

- **逐样本翻转：获救 16 / 回退 12（净 +4）**。获救含 3 例意图集合修复（幻觉多意图/漏意图减少）。
- **机制实证（status 快照如何起作用）**：S-000294 baseline 对"按上次记录"拒填 service（"utterance 未明说业务"→漏意图），status 版从 records[0] 补全 看直播/1080p/80ms；S-000215 baseline 丢 prev 时间槽（"本轮未提时间故不赋值"），status 版显式沿用 prev_state 时间。**状态快照让模型感知意图已有累积态 → 槽位沿用/补全更稳、幻觉与漏段减少**。
- **分模板（session，status vs baseline）**：定向收益 T2-3 6→8、T3-2 2→4、X-2 7→9、T1-4 11→12、T2-5 11→12（满分）；代价 T2-4 21→19、T2-1 9→8、T2-2 8→7；**T3-3 持平 1/12（状态信息不治 record 消歧，符合预期）**。
- **错误分布**：漏意图 59→56、record 55→53、其他值 21→16、集合不一致 37→34；时间 53→58（+5，prompt 变长挤压，与 v12.2 同形态）。
- 与 1.0+32b 消融（−0.66pp/−2.07pp 打平微负）对比：**方向翻转——更强模型更能利用状态快照信息**。

**判定：微正（+1.43pp 在 ±1.5pp 波动带边缘），建议默认开 status_prompt**（机制可解释、主指标同向为正、目标类定向收益；开关保留，复跑确认后可再收紧）。**当前最佳 = dataset_4 + qwen-plus + agent_context + status_prompt：session 70.36% / turn 68.44%**。T3-3（1/12）与 record 取值仍是首要瓶颈，不受状态辅助影响。

产物 `data/evals/eval_dual_v16_d4_qwen_plus.jsonl`/`_summary`/`_errors.html`、`eval_dual_v16_d4_qwen_plus_status.jsonl`/`_summary`/`_errors.html`、冒烟 `eval_dual_v16_d4_smoke.jsonl`；配置 `configs/config_d4_qwen_plus_agentctx.json` / `config_d4_qwen_plus_status.json`；数据配套 `data/split_d4.json`、`data/case_bank_train_d4.jsonl`、`data/embed_cache_d4.json`（32b 时需另建副本防并行写竞争）。

## v17.0 status_prompt 跨模型对照 + 72b 对标尝试（2026-08-26）

**动机**：① v16.0 在 qwen-plus 上判定 status_prompt 微正默认开，需验证是否跨模型成立（1.0 时代 32b 上曾 −0.66pp 打平微负）；② 用户想验证 qwen-plus 能力锚点 ≈ Qwen2.5-72B-Instruct。

**72b 对标（未完成，权限阻塞）**：`qwen2.5-72b-instruct` 在当前百炼 key 下 HTTP 403 access_denied（模型未开通）。配置已备 `configs/config_d4_qwen25_72b_agentctx.json`，开通后重跑即可。冒烟产物 `eval_dual_v17_d4_72b_smoke.jsonl`（0/6，llm_raw 全空即权限拒绝所致）。

**status_prompt 跨模型对照（dataset_4 test 280，tag v17_d4_qwen3_32b[_status]）**：

| 模型 | baseline session/turn | +status_prompt | Δ session | Δ turn |
|---|---|---|---|---|
| qwen-plus（v16.0） | 68.93% / 67.58% | 70.36% / 68.44% | **+1.43pp** | +0.86pp |
| qwen3-32b（v17.0） | 56.79% (159/280) / 56.95% (332/583) | 57.50% (161/280) / 58.66% (342/583) | +0.71pp | **+1.71pp** |

- **两模型同向为正**（32b：获救 13/回退 11，turn 获救 32/回退 22）——v16.0 "默认开"判定跨模型成立；1.0 时代的微负结论应视为数据集/时代局限（1.0 上 record 噪声占 13.9% 干扰更大）。
- **收益结构不同**：plus 收益在 session 级（+1.43pp，最终集合更完整）；32b 收益在 turn 级（+1.71pp，单轮槽位补全更稳）但 session 仅 +0.71pp——32b 累积误差大，单轮改善难以保全到最终态。
- 模型差距跨数据集稳定：plus−32b = +12.14pp（d4 baseline）/ +12.86pp（d4+status）/ +12.04pp（2.0）/ +7.92pp（1.0）。
- 32b 错误分布（baseline→status）：漏意图 84→82、record 59→57、memory 12→7、值 28→24、时间 68→71。

**结论**：status_prompt 在 d4 上对两模型均正向，维持默认开。**当前最佳不变 = v16.0 配置（d4 + qwen-plus + agent_context + status_prompt）70.36%/68.44%**。72b 对标待模型开通后补。

产物 `data/evals/eval_dual_v17_d4_qwen3_32b.jsonl`/`_summary`/`_errors.html`、`eval_dual_v17_d4_qwen3_32b_status.jsonl`/`_summary`/`_errors.html`；配置 `configs/config_d4_qwen3_32b_agentctx.json` / `config_d4_qwen3_32b_status.json` / `config_d4_qwen25_72b_agentctx.json`（待开通）。

## v18.0 qwen3-235b-a22b-instruct-2507 单跑（旗舰不如 plus，2026-08-26）

**动机**：验证 qwen-plus 能力锚点——与 Qwen3 旗舰开源 MoE（235B-A22B）在同一最新数据集+最新方案下对比（用户指示：只跑 d4 + 最佳配置，不消融）。

**实验（dataset_4 test 280，配置 = v16.0 最佳 agent_context+status_prompt，仅换模型，tag v18_d4_qwen3_235b）**：

| 模型 | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| qwen3-32b | 57.50% (161/280) | 58.66% (342/583) | 76.67% |
| **qwen-plus** | **70.36% (197/280)** | **68.44% (399/583)** | **82.28%** |
| qwen3-235b-a22b-instruct-2507 | 62.50% (175/280) | 62.95% (367/583) | 77.37% |

- **排序：qwen-plus > 235b > 32b**。旗舰 235b 比 plus 低 7.86pp/5.49pp——单任务证据，但差距远超波动带（±1.5pp）。
- 逐样本：plus 对/235b 错 38 例 vs plus 错/235b 对 16 例。235b 弱在多意图/取消场景的意图集合与字段完整性（漏意图 83 vs plus 56；S-000008 取消目标误判）；**235b 的 record 提取反而更好（42 vs plus 53）、T3-3 3/12 全场最佳（plus 1/12）**——235b 不像 plus 那样激进取画像值，消歧场景受益。
- **能力锚点结论**：本任务上 qwen-plus 明显强于 235B-A22B 旗舰开源模型，"plus ≈ 开源 70B 级"的估计偏低——plus 更像是针对指令遵循/企业任务特化调优的版本，不能简单用参数量对标（至少在本任务形态上）。若要低成本自托管，235b 不是平替（-7.86pp）。

**当前最佳不变 = v16.0（d4 + qwen-plus + agent_context + status_prompt）70.36%/68.44%**。

产物 `data/evals/eval_dual_v18_d4_qwen3_235b.jsonl`/`_summary`/`_errors.html`、冒烟 `eval_dual_v18_d4_235b_smoke.jsonl`；配置 `configs/config_d4_qwen3_235b_status.json`。

## v17.1 turn 级取消计分口径（方案 b，2026-08-27）

**背景**：错误归因发现 T2-6 missed_intent 30 例中 27 例是口径边界——slot_updates 只记加槽不记取消事件，取消轮的 turn 级 GT 保留历史态，模型正确执行取消（pred 空）反被判 missed（v4.0 已知的口径边界，d4 上占 turn 级总错误 ~15%）。用户拍板选方案 b：取消轮 GT 记为"应为空"，正确取消计 turn 命中。

**改动**：
- `session_eval.py`：新增 `cancel_turn_map()`（依据 event_sequence `Cancel_Request` action + related_intent_ids 定位取消轮；d4 全量核验 88/88 cancelled intent 标记齐全且即最后 related 轮，零缺失）；`turn_gt()` 加 `cancel_credit` 参数（默认开），取消轮 case GT 记 `CANCEL_GT` 标记；`judge_turn()` 对该 case 判"pred 无此 key = 命中"，detail 加 `cancel_case` 标志；新错误桶 `cancel_wrong`（未执行取消）；`--no_cancel_credit` 恢复旧口径；summary 记 `cancel_credit`。
- `error_report.py`：新分类 `cancel_wrong`（取消未执行）+ 归类/文案适配。

**验证（不重跑，用 v16.0 status 版 eval jsonl + 新 GT 复算）**：turn 级 68.44% (399/583) → **74.27% (433/583)，+5.83pp**；34 个取消 case 全部转正、零回退（v16 配置在 d4 test 上取消全部执行正确）；session 级 70.36% 不变。

**结论**：新口径自下次全量评估起默认生效。d4 上新旧 turn 级数字相差约 +5.8pp，跨口径比较时注意标注。待数据集更新后全量重跑确认。


## v19.0 数据集切 dataset_5 + 取消计分口径首次全量生效（2026-08-27）

**背景**：① dataset_5 发布（819 sessions：∩d4 641 条话语全改/intents 零变动 + 新增 178 − 删除 61；765 users 已由 users_3000.jsonl 全覆盖）。② v17.1 取消计分口径（方案 b）首次在全量评估默认生效。

**配套（全部新建）**：`split_d5.json`（seed42/0.4 分层 train 490/test 329）、`test_cases_d5.jsonl`（781 cases）、`case_bank_train_d5.jsonl`（490 条）、`embed_cache_d5.json`（首跑全新构建）、配置 `configs/config_d5_qwen_plus_status.json`（= v16.0 最佳配置仅换路径）。`users_3000.jsonl` 已拷入 dataset_5。

**噪声重扫（find_noise.py --all）**：d5 仍有 R5 系 record 噪声 **13 条**（新变体 R5-A 用户显式选择/R5-C 按 service 过滤取最近；旧 16 条清单为 merged_2.0 时代产物已失效，仅 S-000018/S-000760 复现）。test 中 7 条 → 有效 test 322；train 中 6 条留检索库。清单 `data/_noise_exclude_d5_scan.json`；session_eval 新增 `--noise_file` 参数（旧 `_noise_exclude.json` 保留不动）。

**实验（dataset_5，qwen-plus + agent_context + status_prompt，cancel_credit on，tag v19_d5_*）**：

| 口径 | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| 剔除 7 噪声（322） | **72.05% (232/322)** | **75.47% (517/685)** | 81.27% (538/662) |
| 全集（329） | 70.52% (232/329) | 73.32% (514/701) | 80.86% |

- **取消计分口径实证**：turn 级 missed_intent 从 d4 旧口径的 56 降至 36（取消 case 不再误罚）；新增 `cancel_wrong` 桶仅 2 例（模型几乎总能正确执行取消）；d5 cancelled intents 103 条。session 级不受口径影响。
- 与 d4 最佳（70.36%/68.44% 旧口径）比较：session +1.69pp（test 构成不同，仅参考）；turn 级跨口径不可直接比（d4 新口径复算为 74.27%，d5 75.47% 同口径 +1.20pp）。
- **错误分布（turn，剔除口径）**：value_wrong 93（rtt 57 仍最大单字段、resolution 36）、time_wrong 37、missed_intent 36、cancel_wrong 2。session 级：field_wrong 51 / intent_set_mismatch 39。
- **T3-3 全灭 0/14**（d4 为 1/12）：14 败中 10 例 intent_set_mismatch 且 9 例为 extra 幻觉意图（record 候选被建成新意图，与 d4 归因一致）——T3-3 消歧规则（修补方案②）仍是方法侧首要目标。
- 分模板（session，剔除口径）：T1-1 21/22、X-1 6/6 满分、T2-5 15/17；弱项 T3-3 0/14、T2-3 9/18、T2-2 10/19、T3-2 11/17。
- 状态机分桶：Cancelled 39/41=95.1%、Ready 87.1%、Confirmed 76.5%、Collecting 55.3%（仍最弱）。

**结论**：d5 新基线 = **72.05%/75.47%（剔除口径，当前最佳）**。下一步：错误修补计划不变（①画质表重建 → ②T3-3 消歧规则+③record 规则+④改口轮沿用 合并 prompt 修订），在 d5 上执行。

产物 `data/evals/eval_dual_v19_d5_qwen_plus_status.jsonl`/`_summary`/`_errors.html`（剔除口径）、`eval_dual_v19_d5_qwen_plus_status_full.jsonl`/`_summary`/`_errors.html`（全集）、冒烟 `eval_dual_v19_d5_smoke.jsonl`；噪声清单 `data/_noise_exclude_d5_scan.json`。

## v20.0 画质映射表重建（d5 规范表，正向，2026-08-27）

**背景**：错误归因①——prompt 画质映射表是 merged 时代（v12.2）推导，d5 再生成后过期：实证缺 `腾讯视频/视频 高清=540p`、`爱奇艺/视频 高清=540p`（GT source_ref 直接写明 `resolution_quality_map:腾讯视频:视频:高清`→540p，旧表缺该条目落默认 720p 恒错，S-000607 三轮全错）。

**改动**：
- 从 dataset_5 **train** split 的 `source_ref=resolution_quality_map:app:服务:画质词` 还原规范表：**60 条零冲突**；∪ 非冲突遗留条目（merged 时代存在但 d5 train 未采样到的组合，如 咪咕/视频 高清=540p）。11 条 test 独有组合全部与旧表/默认值一致，train-only 推导无损（无 test 泄漏）。
- `prompt_builder.py`：旧表改名 `_QUALITY_SECTION_FULL_LEGACY`（回退保留），新表 `_QUALITY_SECTION_FULL_D5`；`set_full_quality_map(flag, version)` 加版本参数，config 新键 `quality_map_version`（`"d5"` 默认 / `"legacy"` 回退）。pipeline 初始化传入。
- 与 legacy 表实际差异仅 4 条：+腾讯视频/视频 高清=540p、+爱奇艺/视频 高清=540p、+快手/开直播 标清=540p、+斗鱼直播/看直播 超清=1080p（后两条=默认值补显式）。
- 配置 `configs/config_d5_qwen_plus_qmap.json`（= v19.0 配置 + 显式 `quality_map_version:"d5"`）。

**实验（dataset_5 剔除 7 噪声 322，tag v20_d5_qmap，对照 v19.0=legacy 表）**：

| 配置 | session 级 | turn 级 | 意图状态机 |
|---|---|---|---|
| v19.0（legacy 表） | 72.05% (232/322) | 75.47% (517/685) | 81.27% |
| **v20.0（d5 规范表）** | **74.22% (239/322)** | **75.77% (519/685)** | **81.57%** |
| Δ | **+2.17pp** | +0.29pp | +0.30pp |

- **逐样本翻转：获救 14 / 回退 7（净 +7）**。目标修复实证：S-000607（腾讯视频 高清→540p）转正；间接受益 S-000392（微信/开直播 1080p 丢字段）、S-000519（淘宝主播 标清=720p）、S-000856（淘宝 高清=1080p）等——表补显式条目后模型查表遵从度提升，收益超出 2 条差异的直接覆盖。
- 回退 7 例核验：S-000675 芒果TV 超清=1080p 两表完全一致仍答 720p（模型遵从波动，非表差异）；其余为 rtt/时间/意图集合，与画质表无关——判定为 ±1.5pp 运行波动带内正常抖动。
- 错误分布：value_wrong 93→90、missed 36→34；resolution 错 36→27（−9）。
- 附带发现（非噪声，数据集设计）：d5 新增 `invalid_resolution_unit` 改口场景（用户说"1080m"后自我纠正"是1080p"），turn 级 GT 记字面 1080m、final 记 1080p，S-000656/S-000869 两例——模型直接归一化 1080p 会丢 turn 分但 session 不受影响，属设计口径不修补。

**结论**：d5 规范表正向采纳为默认（`quality_map_version` 缺省即 `"d5"`）。**当前最佳 = v20.0：session 74.22% / turn 75.77%（d5 剔除口径）**。下一步：修补②T3-3 消歧规则+③record 不超前取值+④改口轮沿用 合并 prompt 修订。

产物 `data/evals/eval_dual_v20_d5_qmap.jsonl`/`_summary`/`_errors.html`、冒烟 `eval_dual_v20_d5_qmap_smoke.jsonl`；配置 `configs/config_d5_qwen_plus_qmap.json`。

## v20.1 error_report 二级标签：错误原因维度（工具改动，2026-08-27）

**背景**：一级错误桶（record/时间/漏意图…）只回答"错在哪里"，不回答"为什么错"。按论文交叉审视（docs/report_papers_crosscheck.md §3.1）把 A1 ESF-Bench 分类法映射为二级标签，区分"我们特有的 bug"与"全行业能力短板"，指导修补优先级与预期管理。

**改动**（仅 `error_report.py`，不动评估主流程，对旧 eval 产物可直接重跑生效）：
- 新增 `scenario_tag()`：每个 turn 级错误样本在一级桶之外再打一个原因维度标签，单标签按优先级判定——
  **FMT** 形式差异（宽松归一化后 GT==pred，疑评估口径假错误，A2 思想）→ **SR** 取消/重置（A1）→ **CU** 消歧/会话理解（T3-3 或选择指代短句，A1）→ **COR** 改口覆盖（GT 同字段本轮+更早轮双更新，A1）→ **PER** 画像/记录引用（A1）→ **REA** 推理/时间（A1）→ **CTX** 状态沿用失败（本轮无新信息却丢/改 prev 值，E1）→ **XFER** 多意图串扰 → **INT** 意图识别 → **GEN** 本轮表述理解（兜底）。
- HTML 新增"二级标签：错误原因维度"面板（标签计数 + 标签×一级桶交叉 + 标签定义与修补指向）；样本卡片头部加标签徽章；控制台摘要同步打印。

**v20.0 @ d5 实测分布（166 错误 turn）**：PER 52 ＞ REA 39 ＞ INT 26 ＞ CU 25 ＞ CTX 8 ＞ COR 7 ＝ GEN 7 ＞ SR 2；**FMT 0**（精确匹配口径无形式差异假错误，A2 宽松匹配顾虑实证为空）；XFER 0（turn 级无应用/服务错配）。
- 印证：PER+REA=91/166（55%）恰是 A1 认定的全模型两大最弱类（个性化 55.9%/推理 59.4%）——过半错误是能力短板而非可修 bug，prompt 修订预期应设为"缓解"。
- CU 25 例远大于 T3-3 模板本身的 14 session：消歧/选择类表达散布在其他模板，修补②（消歧候选注入）受益面比单看 T3-3 更大。
- 产物：`data/evals/eval_dual_v20_d5_qmap_errors.html`（已用新版重生成）。


## v20.2 turn 级改口错误值轮豁免口径（correction_credit，2026-08-28）

**背景**：v20.0 错误归因发现 T3-2 turn 级仅 50.0%（25/50）是口径假错误——"用户说错"场景（T2-3 轮1 / T3-2 轮2）的逐轮 GT 按设计记字面错误值（v19 拍板"turn GT 记字面值不修补"），而方案的合法矩阵/validator 正确拒收非法组合（pred 空）反被判错。与 v17.1 取消轮口径同构，用户拍板采用同款处理：correction_credit，默认开，带入后续所有实验。

**改动**（`session_eval.py`，对旧 eval 产物可离线复算生效）：
- 新增 `correction_turn_map()`：用户 action 同时含 `Negate`+`Correct_Previous_Input` 的轮为更正轮（d5 全量核验仅 T2-3 轮2×49、T3-2 轮3×43 命中；T3-3 消歧的 Negate 单独出现、T2-5 改参无此组合，均不误命中）；在 slot_updates 中向前定位被覆盖的同名字段写入轮即错误值轮，被覆盖字段即豁免字段。T3-2 有 3 条 unsupported_qos_metric 型（错误值"丢包率10%"非合法槽位、GT 轮2 本就无更新）天然无口径冲突，无需豁免。
- `turn_gt()` 加 `correction_credit` 参数（默认开）：错误值轮 case GT 记 `{__correction__: 豁免字段, __gt__: 真实GT}`；`judge_turn()` 该 case 判定——pred 空（拒收）= 命中；pred 非空则豁免被更正字段后比对其余 7 字段（记字面值也算对，其余字段错仍判错）；detail 加 `correction_case`/`correction_exempt`；新错误桶 `correction_wrong`；`--no_correction_credit` 回退；summary 记 `correction_credit`。
- 离线复算工具 `_recompute_correction_credit.py`（从已有 eval jsonl 重算 turn 级，不重跑模型）。

**验证（复算 v20.0 @ d5 剔除口径，产物 `data/evals/eval_dual_v20_d5_qmap_corrcredit.jsonl`）**：

| 口径 | 修复前 | 修复后 |
|---|---|---|
| turn 级 | 75.77% (519/685) | **77.81% (533/685)，+2.04pp** |
| T3-2 turn | 50.0% (25/50) | 70.0% (35/50) |
| T2-3 turn | 61.1% (22/36) | 72.2% (26/36) |
| session 级 | 74.22% (239/322) | 不变（session 比最终态，错误轮在中间轮） |

33 个错误轮 case 中 21 个原判错：14 个转正（10 例模型拒收 pred 空 + 4 例记字面值后其余字段全对）、7 个仍错（S-000622/685/738/761 rtt 取值错、S-000639/995 时间推算错、S-000828 duration=-30min）——仍错全是真实错误，口径不误赦。S-000656/869（"1080m→自纠1080p"改口场景）随本口径转正。judge_turn 分支单测三分支（拒收/字面值/其余字段错）全过。

**结论**：新口径自下次全量评估起默认生效，后续实验统一带 correction_credit；跨口径比较历史 turn 级数字时 d5 口径差约 +2.0pp。err_report 已对新产物重跑（`eval_dual_v20_d5_qmap_corrcredit_errors.html`）。T3-2 剩余真实短板：轮1 超前补齐脑补业务致 key 错位（S-000158）、改应用名的改口致 key 割裂（S-000318，修补计划④覆盖）。


## v21.0/v21.1 T3-3 消歧规则注入（修补②落地，正向采纳，2026-08-28）

**背景**：T3-3 消歧在 d5 全灭（v19 起 0/14），9/10 失败为"agent 列候选记录→用户选择"轮把候选 app/service 误建成新意图（v20.1 二级标签 CU 25 例，受益面超出 T3-3 模板）。根因不是信息缺失（agent_context 已透候选列表），而是缺"消歧轮该怎么处理"的规则。

**改动**：
- `pipeline.py` 新增 `_is_disambiguation_turn()` 窄触发检测（三条件同时满足）：①上轮 agent 话术含"记录"+消歧问句（`哪一条|还是…呢` 二选一）；②当前 utterance 是选择回答（`第N条/就按/就选/X月X号那条/X号那条/最近一条`）；③prev_state 非空。配置开关 `disambiguation_rule.enabled`（默认关，消融用）。触发时把 prev_states 的现有 intent_key 列表传给 prompt_builder。
- `prompt_builder.build_prompt` 新增 `disamb_keys` 参数：触发时渲染 DISAMBIGUATION RULE 段——①候选 app/service 禁止用作输出（只是取值来源）；②必须沿用现有意图头 `# intent: <key>`；③序数/日期按 agent 话术列出顺序对齐，禁止用画像 records[N] 数序数；④只取被选择记录的所需字段，禁止覆盖 prev_state 已有值尤其时间；⑤summary 写明选了哪条候选。
- 触发精度全量扫描（d5 train+test）：test 命中 14 轮全在 T3-3（14/14 session 全覆盖）、其他模板零误伤；train 命中 T3-3×22 + T2-3×4（均为"值冲突二选一"轮，规则语义兼容）。

**实验（d5 剔除 322，qwen-plus，基线 = v20.0 同配置）**：

| 版本 | session | turn（v20.2 口径复算） | T3-3 session | T3-3 turn |
|---|---|---|---|---|
| v20.0 基线 | 74.22% (239/322) | 77.81% (533/685) | 0/14 | 10/31 |
| v21.0 初版规则 | 72.98% (235/322) | 75.62% (518/685，复算) | 4/14 | 8/31 |
| **v21.1 序数对齐修正** | **74.84% (241/322)** | **77.37% (530/685)** | **7/14** | **14/31** |

- v21.0 逐样本：获救 15/回退 19。回退根因查明——规则让模型"从画像 records 定位"，模型用 `records[N]` 数序数，但画像列表按 completed_at 降序、与 agent 话术列出顺序不一致（S-000239 "第三条"应指 agent 列表的 6/24 Telegram/720p，模型取 records[2]=微信/360p）；S-000906 还顺手把 prev 时间字段覆盖成 record 时间。
- v21.1 修正规则 3/4（序数按 agent 话术顺序、禁止覆盖 prev 已有字段）后：上轮 3 个回退 session（S-000239/762/906）t2/t3 全部修复，762/906 session 转正；T3-3 0→7/14 破半；session 获救 12/回退 10（非目标类翻转分散无聚集，±1.5pp 波动带内）；turn 级 −0.44pp（−3 turn，波动带内）。
- ⚠️ 口径说明：v21 两次全量跑于 v20.2 代码合入前，产物存储判定为旧口径；上表 turn 数为用 `correction_credit` 离线复算（复算 v20.0 产物得 533/685，与 v20.2 记录一致，复算方法已互验）。
- error_report v21.1：CU 25→21、PER 52→43、INT 26→32（意图错小幅反升为运行波动）；record 提取错误 58→42（−16，规则生效的直接证据）。

**结论**：session 主口径 +0.62pp 正向 + 目标类 T3-3 破零到 50% + turn 打平（波动带内），判定**采纳**，`disambiguation_rule.enabled=true` 已并入当前最佳配置 `configs/config_d5_qwen_plus_qmap.json`。**当前最佳 = v21.1：session 74.84% / turn 77.37%（v20.2 口径）/ 状态机 80.66%**。T3-3 剩余 7 败为 PER 能力短板（序数/日期→记录值对齐仍不稳），留给后续修补③/反思机制。修补计划剩余：③record 不超前取值 + ④改口轮沿用（合并 prompt 修订）。

产物 `data/evals/eval_dual_v21_disamb.jsonl`/`_errors.html`（v21.0）、`eval_dual_v21_1_disamb.jsonl`/`_summary`/`_errors.html`（v21.1）；冒烟 `eval_dual_v21_disamb_smoke.jsonl`；消融配置 `configs/config_d5_qwen_plus_disamb.json`。


## v22.0/v22.1 修补③+④ prompt 修订（拆开归因：④采纳③回退，2026-08-29）

**背景**：v20.0 遗留修补计划 ③record 不超前取值（PER 52 最大错误类，模型无指代时也从画像 records 补齐槽位）+ ④改口轮丢字段/key 割裂（resolution None ~15、S-000318 改应用名致 key 割裂）。按原计划合并为一次 prompt 修订。

**改动**：
- ③`record_restraint.enabled`：always-on system prompt 规则——本轮无"按上次/那条记录/按平时"等指代词时禁止从画像 records/profile 取任何字段值，没说的槽位留空由 agent 追问（`prompt_builder._RESTRAINT_RULE` + `set_record_restraint` 全局重组）。
- ④`correction_rule.enabled`：触发式注入（改口词 + prev_state 非空）→ CORRECTION RULE 段：intent 头沿用 prev 旧 key（coreference 可解析）、被更正 app/service 段内赋新值（最终 key 由 delta 值推导自动更新）、只改被更正字段、其余一律沿用不得丢字段。显式覆盖旧共享规则"改 service 必须用新头"（正是 key 割裂根源）。

**实验（d5 剔除 322，qwen-plus，原生 correction_credit 口径；基线 = v21.1：session 74.84%(241) / turn 77.37%(530，离线复算)）**：

| 版本 | session | turn | 状态机 | 备注 |
|---|---|---|---|---|
| v22.0 ③+④ 合并 | 74.22% (239/322) | 77.81% (533/685) | 80.36% | 打平（−0.62pp/+0.44pp），获救 12/回退 14 |
| **v22.1 ④-only** | **75.16% (242/322)** | **78.83% (540/685)** | **81.57%** | **+0.31pp/+1.46pp 双正，获救 12/回退 11（turn 27/17）** |

- **拆因结论：④ 是正收益来源，③ 净有害**。③的保守化副作用实证：S-000468 t3 确认轮整个意图丢输出、S-000029 t2 新增意图漏识别（"没说的槽位留空"被泛化成"本轮不输出"）、S-000357 t1 用户明说的 360P 被留空；v22.1 关掉③后 turn 反而 +7（533→540）。
- ④机制验证：动机案例 S-000318（改应用名 key 割裂）turn 2 获救；T2-3 turn 20/36→27/36（75.0%）、T3-2 turn 30/50→39/50（78.0%）；COR 二级标签 9→4；意图漏识别 46→34。T3-3 session 7/14 保持不跌。
- ③的回退不是方向否定：PER 超前补齐问题仍在（record 提取错误 41 持平），always-on 全局规则形态被证伪（同 v8.0-a demo 类教训：全局 prompt 变动扰动非目标类）；后续若重试应改触发式（prev 空+无指代词才注入）缩小作用域。

**结论**：采纳④（`correction_rule.enabled=true` 已并入最佳配置 `configs/config_d5_qwen_plus_qmap.json`），回退③。**当前最佳 = v22.1：session 75.16% / turn 78.83% / 状态机 81.57%（d5 剔除口径，correction_credit on）**。修补计划 ②③④ 全部收尾（②v21.1 采纳、③回退、④v22.1 采纳）；下一步：反思机制（report_reflection.md，CRITIC×Self-Debugging 校验反馈回路）。

产物 `data/evals/eval_dual_v22_fix34.jsonl`/`_errors.html`（③+④）、`eval_dual_v22_1_fix4.jsonl`/`_summary`/`_errors.html`（④-only）、冒烟 `eval_dual_v22_fix34_smoke.jsonl`；配置 `configs/config_d5_qwen_plus_fix34.json`（③+④）、`configs/config_d5_qwen_plus_fix4.json`（④-only）。


## v23.0/v23.1 time.py 时间规则库移植（净负回退，2026-08-29）

**背景**：时间类错误是当前已知短板（v22.1 turn 级 109 错中 27 个涉时间字段，其中 26 个纯时间错；X-2 占 11 个最多；典型模式：凌晨/下午 ±12h 混淆、明天/后天日期推算错、多时间候选选错、口语时长未换算、结束时间算错）。外部提供一份规则库 `ref_code/time.py`（875 行，口语时间/时长归一化后处理），要求评估能否融入。

**改动**：
- `time_rules.py`（新）：原样移植 time.py 时间相关 40 个函数（时长单位换算 `_normalize_unit`、口语时间解析 `_loose_time_parts`/`_loose_time_text_candidates`、上下文补全 `_complete_allowed_timestamp`、话术起止纠偏 `_prefer_query_*`、跨午夜滚动、起止倒置纠偏、畸形时间判定等）+ 胶水层 `normalize_timestamp_fields()`。未移植 app/service/resolution/rtt 归一化（time.py 依赖未提供的候选表，本项目有自己的 schema 链路）。
- 两处口径适配：①`_relative_day_offset` 覆盖——time.py 原生"明天"仅在 query 含"今天"时 +1，与 d5 数据集口径（明天=+1/后天=+2，质检报告全量机检通过）冲突，按数据集口径重写；②context 由 session `reference_time` 构造（base_year/base_month/default_start_timestamp）。
- `pipeline.py` 接入：`run_multi` 解析后、validate 前对 delta timestamp 应用规则；config `time_rules.enabled`（默认关）+ `disabled_rules` 细粒度门控；失败静默回退原 delta。
- `session_eval.py` stdout 加 `line_buffering=True`（修复后台重跑时日志 0 字节不可观测问题）。

**GT 不变性回归（本版本核心方法论，`tools/gt_invariance_timerules.py` + `tools/ablation_timerules.py`）**：把规则跑在 dataset_5 全部 slot_updates 的 GT 时间值上（每轮只喂当轮话语，贴近真实管线），凡会把 GT 改错的规则单独门控。逐轮归因与修复：
- 初版 343/3388 (10%) GT 改动 → 根因是多意图话语（T1-3/T2-6/X-2）中规则抓取别的意图的时间候选 → 强规则加"话术时间候选唯一"门控 → 归零。
- `end_with_start_ref` 残留 20 处：话术含"后天"时 start 参考日本身需偏移（跳过）+ end 与 start+时长一致时不介入 → 归零。
- 最终回归 **3388/3388 零改动**（wrong_month/triad_duration 默认关：前者 GT 回归 26 误改，后者与 T2-3/T3-2 更正场景语义冲突）。

**实验（d5 剔除 322，qwen-plus，correction_credit 口径；基线 = v22.1：session 75.16% / turn 78.83%）**：

| 版本 | session | turn | 状态机 | 备注 |
|---|---|---|---|---|
| v22.1 基线 | 75.16% (242/322) | 78.83% (540/685) | 81.57% | time_rules off |
| v23.0 全规则（fill on） | 73.60% (237/322) | 77.66% (532/685) | 81.27% | 获救 14 / 回退 19 |
| v23.1 fill-off | 72.67% (234/322) | 77.08% (528/685) | 80.82% | 获救 11 / 回退 19 |

**归因（关键）**：把 v22.1 的模型预测逐字段喂规则回放——不带 fill：**1 修 / 0 破**；带 fill：3 修 / 6 破（X-2/T3-3 共享时间话术 GT 常无 duration，填补反而多错）。即规则本身对真实预测 ≈ 零净作用。逐字段比对 v22/v23 产物：0 处预测差异可由规则解释、19 处为模型侧输出差异（temp=0 但 API 仍非确定性）——**两次全量的 −1.6pp/−2.5pp 波动主要由 API 非确定性贡献**，与规则无关，但无法证明为正收益。

**结论**：**回退**。`time_rules.enabled` 维持默认关，最佳配置 `config_d5_qwen_plus_qmap.json` 不含 time_rules，当前最佳仍为 **v22.1（session 75.16% / turn 78.83% / 状态机 81.57%）**。教训：①"GT 不变性回归"是后处理规则入库的硬门槛，本次靠它拦下 10% 的 GT 破坏；②时间错误的根因在模型对多意图话语的候选绑定与 API 非确定性，不在"口语→规范形"后处理——规则能修的（半小时→30min 等换算）模型基本已会，规则会错手的（候选歧义）正是模型也错的；③temp=0 下 DashScope 仍有 ±1.5pp 级非确定性，未来单次全量的 ±2pp 内差异不可作为采纳依据，重要决策需双跑取均值。产物 `data/evals/eval_dual_v23_timerules.jsonl`/`_errors.html`（v23.0）、`eval_dual_v23_1_timerules_nofill.jsonl`/`_errors.html`（v23.1）；配置 `configs/config_d5_qwen_plus_timerules.json`（v23.1 门控版）。

---

## v23.2 — 2026-08-31  时间规则精简入库（3 条重推导规则，离线回放验证，未全量重跑）

**背景**：v23 归因显示移植规则对 qwen-plus 已规范输出为恒等变换。用户提出只保留三条"从话术重推导"的规则（明天/后天日期推算、凌晨/下午 ±12h、显式起止/时长推 end），先用离线回放验证再决定是否入库。

**验证（tools/replay_three_rules.py，v22.1 存档预测 644 case，零 API）**：
- v23 移植版 complete_allowed / normalize_timestamp：**0修/0破**（恒等变换实证，删除依据）。
- 新写重推导规则：day_offset_rederive **2修/0破**（S-000862 日期 +1 天）、end_from_start_duration **1修/0破**（S-000829 end 21:00→21:30）、halfday_fix 0修/0破（唯一目标案例 S-000446 的错生于不计分轮 T1，回放弃权；保留因 GT 不变性零破坏且计分轮外/弱模型可能命中）。
- 组合 **3修/0破**；两个修复 case 唯一差异字段均为时间 → session 各翻正 1 个，上限 session +2/322 ≈ **+0.62pp**，低于 API 噪声带（±1.5~2.5pp）。
- **GT 不变性回归再次立功**：初版 day_offset_rederive 在全量 GT 回归中 4 处误改（S-000863/S-000301 "用N小时到明天/后天0点" 跨夜场景——相对日词绑 end 而非 start），加"起止跨夜弃权"门控后 **3388/3388 零改动**。

**改动**：
- `time_rules.py` 重写（1059→~330 行）：只留 3 条规则 + 必要解析助手；RULE_DEFAULTS = {day_offset_rederive, halfday_fix, end_from_start_duration} 全开。
- v23 全量移植版归档 `data/_archive/time_rules_v23_full.py`（弱模型实验可回取，见 report_method）。
- `tools/replay_three_rules.py` 改为直接调库回放（无脚本本地副本）；gt_invariance/ablation 工具接口不变直接可用。
- `configs/config_d5_qwen_plus_timerules.json` disabled_rules 清空（陈旧规则名移除）。
- pipeline 接入路径不变（`time_rules.enabled` 默认仍关，最佳配置不引用）。

**结论**：**入库但不单独全量重跑**——收益上限 +0.62pp 埋在 API 噪声下，随下次全量顺带验证（需双跑取均值）。回放盲区：计分轮才可见话术，线上每轮过规则可能略优于回放估计。

---

## v23.3 — 2026-08-31  qwen3-32b 弱模型评测 + 时间规则有效性证伪（零增量收益）

**背景**：v23.2 归档时假设"弱模型格式/换算类浅层错误占比高，全量规则可能重新有效"。用 qwen3-32b 实测验证。

**实验**（d5 剔除 322，qwen3-32b @ DashScope enable_thinking=False，其余同 v22.1 最佳配置；分离进程启动器 `run_qwen3_32b.py`）：

| 模型 | session | turn | time_wrong（turn 级首错字段口径） |
|---|---|---|---|
| qwen-plus（v22.1） | 75.16% (242/322) | 78.83% (540/685) | ~27 |
| **qwen3-32b** | **61.18% (197/322)** | **67.01% (459/685)** | **45** |

时间字段级错误 347 vs qwen-plus 241（+44%）——弱模型时间错误确实更多，假设前半成立。

**规则回放**（tools/replay_rules_on_eval.py，qwen3-32b 存档 644 case）：

| 规则集 | 字段修/破 | case 修/破 | session 影响 |
|---|---|---|---|
| new3（v23.2 三条重推导） | 1/2 | **1/0** | **+1（S-000829 唯一差 end，翻正）** |
| full（v23 移植全量默认门控） | 4/4 | **0/3** | **−1（S-000187 翻错；S-000203/267 破在中间轮，终局幸存）** |

字段级修/破会双向高估：new3 的 2 个字段破所在 case 本就有其他字段错（case 不变），full 的 4 个字段修所在 case 也都有其他字段错（修不活）。

**结论**：**假设证伪——弱模型的时间错误增量仍在推理/绑定层（候选选择、参照日、跨轮状态），不在格式层；prompt（python_par codegen + demo）下 qwen3-32b 输出同样是规范形**。全量规则净负、new3 微正（+0.31pp，噪声带内）。时间后处理路线在两个模型档上均已封顶，不再投入；方向仍是生成侧（候选绑定）与数据侧（X-2 模板缺陷）。产物 `data/evals/eval_dual_qwen3_32b.jsonl`/`_summary`/`_errors.html`；配置 `configs/config_d5_qwen3_32b.json`。


## v24.0/v24.1 无约束"上次记录"严格规则（records[0]，大幅正向，2026-08-31）

**背景**：用户确认数据集已修复——"上次"（无其他约束）严格指向 completed_at 最新一条 record（records[0]），与 profile 习惯值/语义过滤无关；之前的矛盾观感是数据集问题。v22.1 错误分析显示 PER 43 例中大量为"模型用语义过滤或 profile 习惯值替代 records[0]"（S-000029/170/218/365 原型）。

**改动**（`pipeline.py` + `prompt_builder.py`，config `record_rule.enabled`，触发式注入）：
- 触发器 `_needs_latest_record_rule`：记录指代词（记录/历史/上次/之前）+ 无筛选约束 + 画像有 records；排除"跟上次一样"型 prev 沿用。
- 约束识别为**紧邻记录词结构**（`上次X那条`/`按X的记录`/`X那条` 才算筛选）——v24.0 误用 `_extract_record_filters`，把话语中任何 app 名当筛选条件，导致"帮我保障微信视频通话，时延按上次记录"（app 是新意图目标）被全挡，9 个 PER 原型零覆盖。
- 排除 T3-3 预选/消歧轮：`查…记录`查询式、`选中/办过/参考`、`X月X号完成的那条记录`日期锚定——这些轮 GT 记录字段**悬空待消歧**（S-000351 实证：两条云游戏记录，agent 先问选哪条），注入 records[0] 反而污染（v24.0 T3-3 7/14→4/14 即此因）。
- 注入段：直接给出 records[0] 字段明细 + 三条纪律（被指代字段必须取 records[0] 值；禁止 profile 习惯值/其他记录替代；用户明说字段不覆盖，整单照抄才连 app/service 一起取）。
- 修复正则 bug：`记录?` 误作"记+可选录"（实需 `(记录)?`），导致"爱奇艺那条"漏判。

**数据实证（触发器精度）**：test 触发 70 轮/69 session；触发轮 GT 的 Record 来源字段**逐意图核验 100% 指向 records[0]**（含 8 例多意图核验脚本假象的逐意图澄清）；T3-3 零残留触发。

**实验（d5 剔除 322，qwen-plus，原生 correction_credit 口径，基线 = v22.1：75.16%/78.83%）**：

| 版本 | session | turn | 状态机 |
|---|---|---|---|
| v24.0（触发器过窄版） | 72.36% (233/322) ↩️ | 76.64% (525/685) | 80.97% |
| **v24.1（紧邻约束识别版）** | **80.12% (258/322)，+4.97pp** | **83.07% (569/685)，+4.24pp** | **81.27%** |

- 逐样本：获救 25/回退 9；**触发∩获救=18、触发∩回退=1（S-000369）**——收益强因果归于规则；9 个 PER 原型 7 个获救（S-000006/029/065/070/170/210/218）。turn 获救 45/回退 16。
- **项目历史最大单步收益**（此前最大为 v4.0 取消处理 +8.59pp @ dataset_2 小样本；d5 时代最大为 v20.0 +2.17pp）。
- 已知残余：S-000369（触发轮丢了 prev 已有的时间字段——RECORD RULE 段缺"prev 其他字段保持不变"一句，+1 session 量级，低于噪声带，留待下次顺带）。
- v24.0 教训：约束条件的识别粒度决定触发器成败——"话语提及 app" ≠ "记录筛选条件"，必须看是否紧邻记录指代词。

**结论**：采纳，`record_rule.enabled=true` 已并入最佳配置 `configs/config_d5_qwen_plus_qmap.json`。**当前最佳 = v24.1：session 80.12% / turn 83.07% / 状态机 81.27%**（d5 剔除口径）。PER 从"能力天花板"被规则化打掉一大块；剩余错误主线转为 REA（多意图绑定/时间推算）与 T3-3 残余。

产物 `data/evals/eval_dual_v24_1_recrule.jsonl`/`_summary`/`_errors.html`（v24.1）、`eval_dual_v24_recrule.jsonl`/`_errors.html`（v24.0 中间版）、冒烟 `eval_dual_v24_recrule_smoke.jsonl`；消融配置 `configs/config_d5_qwen_plus_recrule.json`。


## v24.2 error_report 增强：二级标签过滤 + 原始数据全量 + record 对照表（工具改动，2026-09-01）

**背景**：用户要求错误报告可按错误原因索引、展示数据集原始样本全貌、record 类错误需列出 records 列表对照。

**改动**（`error_report.py`，对旧 eval 产物可直接重跑）：
- **二级标签过滤**：标签面板下新增过滤按钮（全部 + PER/REA/CU/…×计数），点击后样本卡片按标签显隐，跨一级错误桶生效（全隐藏的桶整节隐藏）；卡片加 `data-tag` 属性，纯前端 JS（`filterTag`）无依赖。
- **数据集原始信息全量展示**：每个样本卡（turn 级 + session 级）新增可折叠区块——session meta 表 / 用户画像 profile 表 / 用户 records 全列表（含起止/时长/完成时间，completed_at 降序=模型所见顺序）/ **GT slot_updates 逐轮逐字段表（值+source_type+source_ref，来源类型着色）** / session 全量 JSON。新增 `load_user_profiles()` 与 `_walk_gt_leaves()`/`slot_updates_table_html()`/`raw_data_html()`。
- **record 对照表**：`record_analysis` 升级为完整 records 表（#、record_id、应用/业务/清晰度/时延/完成时间），**GT 选择行绿底、模型选择行红底**双列标记（模型选择按 pred 字段值在 records 中最多匹配≥2 字段判定，标注匹配字段数）。

**v24.1 @ d5 实测分布（119 错误 turn，重新生成）**：REA 42 ＞ CU 23 ＞ INT 18 ＞ **PER 10** ＞ COR 7 ＝ GEN 7+1 ＞ CTX 6 ＞ SR 2；一级桶 record 提取错误 **41→12**（v24.1 记录规则的直接效果量化）。剩余主线 = REA（时间/多意图绑定）。
- 产物：`data/evals/eval_dual_v24_1_recrule_errors.html`（已用新版生成）。

---

## v24.3 error_report 增强：噪声样本 record 对照 + 逐轮模型实时输出（工具改动，2026-09-01）

**背景**：用户要求噪声（已剔除）样本与其他错误样本同构展示（records 列表 + GT 错选定位 + 原始数据）；并要求对话区块在数据集剧本之外并列展示模型每轮真实输出，辅助判断"模型问题还是 prompt 问题"。

**改动**（`error_report.py` + `session_eval.py`）：
- **噪声卡片 record 选取对照**：列出用户全部 records（completed_at 降序=模型所见顺序），`✗ GT 错选`（红，取自 slot_updates 中 source_type=Record 的 source_ref）vs `✓ 应取`（绿，从噪声原因文本解析）双列高亮；并挂上与其他卡片一致的"数据集原始信息"折叠块（raw_data_html）。d5 全部 13 条噪声核验：错选/应取配对与 find_noise 原因文本一一对应。
- **逐轮模型实时输出**：`conversation_html()` 新增可选 `turn_detail` 参数，每轮 user/agent 剧本气泡下追加琥珀色折叠块"模型实时输出（第N轮）"（该轮 llm_raw：# summary 推理 + intent 程序段）。turn/session/噪声三类卡片均接入。
- **session_eval 结果新增 `turn_outputs` 字段**：保存全部轮次的 {turn, llm_raw, preds}。原 turn_detail 只含有 GT case 的轮——T3-3 第 1 轮（查询轮，GT 仅时间字段无 app）被 `turn_gt` 的 `if not app: continue` 跳过，导致该轮模型输出在 eval 产物中丢失、报告无法展示。**注意：旧 eval jsonl 无此字段，v24.1 报告仍看不到第 1 轮输出，下次全量评估起生效**（error_report 已做 turn_outputs→turn_detail 回退兼容）。

**实证案例（S-000303 T3-3）**：agent 话术第一条=8点/150ms，模型却选 9点/50ms。排查确认 agent 上轮回复经 agent_context 原文注入 prompt、DISAMBIGUATION RULE 也触发（prev 非空），但模型 summary 把 agent 话术顺序幻觉颠倒并映射到 records[0]（prompt 中 completed_at 降序=9点在前）——**规则在 prompt 里仍被违反，属模型遵从性失败**；结构性诱因是 prompt 内两套候选顺序冲突（结构化 records 列表 9点在前 vs agent 话术 8点在前）。错误实际在第 1 轮已锁定（模型在消歧未完成时就按 records[0] 超前填入 50ms）。

**产物**：`data/evals/eval_dual_v24_1_recrule_errors.html`（已用新版重新生成，统计口径不变 session 80.12%/turn 83.07%）。

---

## v24.4 消歧候选顺序对照注入（candidate_map，2026-09-01，已实现+离线验证，全量评估中）

**背景**：S-000303 实证 v21.1 纯文字消歧规则的残余失败——agent 话术"第一条=8点/150ms"，模型却选 9点/50ms。排查确认 agent 上轮回复经 agent_context 原文注入、DISAMBIGUATION RULE 已触发，但模型 summary 把话术顺序幻觉颠倒并映射到 records[0]（prompt 结构化 records 按 completed_at 降序=9点在前，与话术序冲突）。**规则在 prompt 里仍被违反 = 模型遵从性失败；结构性诱因 = prompt 内两套候选顺序冲突。**

**改动**：把"话术顺序 → records 对齐"从模型侧挪到 harness 侧确定性完成。
- `pipeline.py` `_parse_disamb_candidates(agent_reply, records)`：截问句（请问/您看/您想，防"按A这条还是B这条"复读）→ 切分候选段（分别是/、/；/还有/，另一条/，而/，(?=X月X号)）→ 段内前导剥离（"有两条…的记录"/"一条是"/"记录是"等枚举引导语，防抢占 records 匹配）→ 纯属性残片（rtt/清晰度被顿号劈开）不冲突时并回上一候选 → 属性打分（app3/svc2/rtt3/res2/日期2/时点2）唯一匹配画像 records（≥3 分）。安全降级：候选<2 / 匹配不到 / 话术声明条数（"两条/三条"）与解析条数不符 → 返回 None 回退纯文字规则。
- 触发点并入 v21 disambiguation_rule（`candidate_map` 子开关，消融可控）；`prompt_builder` 在规则段渲染"候选顺序对照"表（第一条 = app/svc/res/rtt/completed_at 字段明细，"禁止自行按 records 顺序重新排序"）。

**离线验证（d5 test 14 个 T3-3 消歧轮）**：13/14 解析成功且候选顺序与 GT 选择 100% 一致（序数答案逐位命中，日期/app 答案候选集合包含 GT），1 例安全回退（S-000487 "则是"句式候选未分开），**0 错位注入**。S-000303 修复：第一条=R-U0305-002(8点/150ms)=GT ✓。

**全量结果（d5 剔除 322，基线 v24.1 80.12%/83.07%）**：session 79.50%(256/322) / turn 82.63%(566/685) / 状态机 80.66%，−0.62pp/−0.44pp 在 ±2pp API 噪声带内（获救 10/回退 12）。**因果面干净：机制只触 14 个 T3-3 消歧轮——T3-3 turn 12→14/31、session 6→7/14，S-000303/S-000319 获救（目标样本 S-000303 turn2 起 rtt=150ms 全对）；唯一 T3-3 回退 S-000606 核验为 turn 1 时间推算波动（消歧轮输出本身正确不修改时间字段），非本机制致退；其余翻转全在非触发模板=运行波动。判定：方向正确（±2pp 内单次不作采纳依据）。~~暂不进最佳配置~~ → **2026-09-11 在 d6 转正采纳**（+2.99pp，见 v26.2c-d6 条；d5 上始终未采纳）。**

**产物**：eval_dual_v24_3_candmap(.jsonl/_summary/_errors.html)；消融配置 `configs/config_d5_qwen_plus_candmap.json`。另修：session_eval --err_report 自动报告此前用错噪声清单默认路径（报告口径 318≠322），已改为与评估一致的 --noise_file。


## v24.5 v24.1 双跑 + oracle 完美选择器模拟（测量，无代码改动，2026-09-02）

**背景**：report_reflection.md §7.5 P2 门控——v24.1 双跑测 oracle pass@2 上限，>3pp 才保留 BoN+verifier 采样路线。

**结果（d5 剔除 322，qwen-plus，同配置 temp=0 双跑）**：

| 跑次 | session | turn | 状态机 |
|---|---|---|---|
| run1（=v24.1 原跑） | 80.12% (258/322) | 83.07% (569/685) | 81.27% |
| run2 | 81.37% (262/322) | 83.94% (575/685) | 81.57% |

- 双跑差 session +1.25pp / turn +0.87pp，噪声带（±1.5~2pp）内，复现性正常。
- **翻转 28/673 turn（4.16%），A对B错 11 / A错B对 17**；pred 文本不同 54（8.02%）；session 翻转 12/322。
- **oracle pass@2 上限 = +1.63pp turn 级（17 turn，需完美选择器）→ ≤3pp 门控，采样路线整体关闭**（投票/verifier/TTRL 均不再投入）。
- 对比 v19 时代同法测量（翻转 7.43%、oracle +4.46pp）：v24.1 的 records[0] 规则把 PER 区采样抖动（画像习惯值 vs records[0] 多吸引子竞争）规则化消除，稳定性翻倍；剩余翻转集中 T3-1×8/T3-3×6/T2-2×4。

**产物**：`eval_dual_v24_1_run2.jsonl`/`_summary`/`_errors.html`；工具 `_oracle_pass2.py`（同配置双跑对比+门控判定，可复用）。


## v25.0 窄版冲突检查反思回路（conflict_check，净负回退，2026-09-02）

**背景**：report_reflection.md §7.5 P3+P4——validator 信号分层 + "话语明说值 vs 输出值"确定性冲突触发的定向重生成（用户早期单轮项目"对照原文查冲突"经验的移植；CRITIC 谱系有效区的窄版）。形态纪律：只报冲突不补全、bounded 1 轮、逐意图采纳（仅冲突消除才替换）、config 默认关。

**改动**：
- `validator.py`：①信号分层 `signal_level()`（hard=time_inconsistent/time_out_of_range/slot_value_invalid/matrix_out_*/unresolved_coreference；soft=missing_required_slot——"正常未收齐"不再是错误信号）；②`detect_utterance_conflicts(utterance, flats)` 确定性冲突检测：rtt/resolution/duration（含中文数词、"N个半小时"）/日期（只比 start 防跨日误报），**值-意图近邻绑定**（±40 字符最近前方 app，窗口内双 app 放弃），上下界前后缀表述跳过（"不超过50ms"/"20毫秒以内"），改口/否定轮整轮跳过（新旧值同在必误报，改口轮已由 v22 correction_rule 覆盖），日期记录锚定跳过。
- `pipeline.py`：`run_multi` 尾部 `_conflict_check_round`——冲突明细+上一次输出回灌 prompt 重生成 1 轮，逐意图采纳（conflicts2 为空才替换，非冲突意图保持第一次输出）。
- `session_eval.py`：turn_outputs 新增 `reflection` 字段（触发 turn 的冲突明细+采纳清单）。
- 配置：`conflict_check.enabled`（qmap 最佳配置默认 false）；消融配置 `configs/config_d5_qwen_plus_ccheck.json`。

**触发器精度（离线扫描 v24.1 run1，322 剔除口径）**：初版误报 88/106（多意图串扰/"一个半小时"误算 30min/改口轮新旧值），逐项收窄后 **触发 4 turn/4 session，全部命中真实错误，0 误触发**。

**实验（d5 剔除 322，qwen-plus，原生 correction_credit 口径，双跑纪律）**：

| 配置 | session | turn | 状态机 |
|---|---|---|---|
| v24.1 run1 / run2 | 80.12% / 81.37% | 83.07% / 83.94% | 81.27% / 81.57% |
| v25 ccheck run1 / run2 | 78.88% / 78.88% | 82.34% / 83.36% | 81.12% / 81.42% |

- 双跑均值：v25 78.88%/82.70% vs v24.1 80.75%/83.51% → **session −1.86pp / turn −0.80pp，双跑一致净负，触发预设判负条件，回退（enabled=false 保持默认关）**。
- **机制归因（run2 反思日志）**：触发 10 turn / 采纳仅 1（S-000328 rtt 100→20ms——但 GT 是画像记录值，话语"设20ms还是按上次记录"是选择疑问句，20ms 是被选项，错→错）；模型在 9/10 触发轮拒绝改动（2412.19513 置信侧占优），采纳门拦住了 S-000197（同 key 双意图）等回退风险。session 差 258→254 与反思无关（采纳仅 1 例），是纯运行噪声。
- **靶区上限**：离线真靶区 4 turn ≈ +0.6pp 即使全修也低于噪声带——与 §6（靶区<15 turn）、v23.2（+0.62pp<噪声带）第三次同构结论：**校验信号驱动类机制在本数据集的信号区结构性太小**。

**教训**：①离线触发扫描必须先于全量跑（初版 88 误报若直接跑全量就是 v22.0③ 重演）；②"话语明说值"在有画像记录兜底的场景里语义不可靠（选择疑问句/被选项/上限表述），字面比对天花板即此；③模型面对"校验器说你错"以拒绝为主——置信/批判冲突实证。

**产物**：`eval_dual_v25_ccheck(.jsonl/_summary/_errors.html)`（run1）、`eval_dual_v25_ccheck_run2(.jsonl/_summary/_errors.html)`（run2，含 reflection 日志）、冒烟 ×3；消融配置 `configs/config_d5_qwen_plus_ccheck.json`（enabled=true，已回退不入最佳配置）。


## v25.1 turn 级 GT 时间三字段知二推一口径（gt_derive，2026-09-03）

**背景**：v24.1 err_report 复核（S-000082 起）发现 REA·时间推算桶混入口径假错误——GT 标注只把字段登记在"字面明说"的轮次，X-2 模板轮2共享时长子句（"时间都是…一共X小时"）的 duration 只绑给将被取消的 I3、存活 I2 推迟到轮3确认句才登记（d5 全量 22 个 X-2 session 中招），另有 1 例（S-000981）用户本轮只明说 start+end、duration 下轮补说。而模型侧 validator `_complete_timestamp` 每轮强制知二推一，且 GT 最终态三字段必然齐全（d5 全量扫描 session 级 0 缺口）——**GT 最终态是"推算过的"、turn 级中间态是"未推算的"**，模型全程推算对齐了最终态、却在中间轮被未推算的 GT 罚。d5 turn 级 GT 共 29 处可推未推（全 duration，test 11 处）。

**改动**（`session_eval.py`，默认开，对旧 eval 产物可离线复算生效）：
- 新增 `_derive_time_flat()`：扁平版知二推一（start+end→duration / start+dur→end / end+dur→start），复用 validator 的 parse_dt/parse_duration_minutes/_fmt_dt，只补缺失不覆盖。
- `turn_gt()` 加 `gt_derive` 参数（默认开）：正常 case 与 correction case 的 real_gt 均先推导再比对；detail 记 `gt_derived` 字段清单；`--no_gt_derive` 回退；summary 记 `gt_derive`。
- 离线复算工具 `tools/recompute_gt_derive.py`（双向翻转都统计）。

**不改生成侧的论证**：当前判对 502 turn 中 83 个（16.5%）的 duration 靠 validator 推导补上（llm_raw 未写 duration、GT 有值），关推导 turn JGA 直接 −12pp——模型推导是对齐"GT 最终必全"的必要机制，不能关；只能让 GT 中间轮同步推算。

**验证（离线复算 v24.1 双跑产物，不重跑模型）**：

| 口径 | run1 | run2 |
|---|---|---|
| turn 级（修复前） | 83.07% (569/685) | 83.94% (575/685) |
| turn 级（gt_derive） | **84.67% (580/685)，+1.61pp** | **85.55% (586/685)，+1.61pp** |
| 转正/翻错 | +11 / −0 | +11 / −0 |
| X-2 turn | 48/63=76.2% → **59/63=93.7%** | 49/63=77.8% → **60/63=95.2%** |
| session 级 | 80.12% 不变 | 81.37% 不变 |

11 个转正全是 X-2 轮2 duration（S-000070/082/094/210/405/784/856/928/964/981/1000），双跑转正集合完全一致；穷举校验对→错方向 0 例（当前判对 turn 中不存在"GT 缺可推字段且 pred 该字段也空"的 case）。单元核验：S-000082 turn2 爱奇艺 GT duration None→180min（derived=['duration']），同轮抖音已登记值不被覆盖。

**结论**：新口径自下次全量评估起默认生效，跨口径比较历史 turn 级数字时 d5 口径差约 +1.6pp。REA·时间推算桶随之清净（11 例假错误移除，占 v24.1 错误 turn 的 10%）。session 级口径不变、不受影响。产物：`data/evals/eval_dual_v24_1_recrule_gtderive(.jsonl/_summary.json/_errors.html)`、`eval_dual_v24_1_run2_gtderive(.jsonl/_summary.json)`。


## v25.2 key 断裂修复（harness 侧，2026-09-03，冒烟版——全量版见下条）

**背景**：v25.1 报告 UI 复查发现 missed_intent 桶 21/34 是 key 错位（非漏识别），原型 S-000318（T3-2 改口轮）：轮3 模型 header 沿用 prev key、body 只赋新 app，harness 用 delta 的 app/svc 推导最终 key → svc 丢失 key 裂成"爱奇艺"，旧 key 残留为幻影意图。深挖冒烟又暴露第二层：模型 header 写完整 `爱奇艺/看直播` 而 prev 里是 `/看直播` 无头意图（轮1 用户没说 app）时，执行层 `_match_prev` 匹配不到 → 沙箱对空 prev 执行 `state.service_name` 自引用 → 段崩 → delta 整段丢弃。

**改动**（三处同规则统一）：
- `python_state.py`：`_match_prev_key()`（精确 > 同 app 前缀 > **唯一"无 app 同 svc"待补意图归并**）——执行层沙箱 state 用被归并意图的 prev 初始化，自引用 coreference 才能解析；`_match_prev` 改为委托。
- `pipeline.py` `run_multi`：key 改从**合并后 final_state** 的 app/svc 推导（旧逻辑只看 delta，改口单字段必裂）；记录 `_consumed_key`（被归并/改名的 prev key）；冲突检查二段同步。
- `session_eval.py` `run_session`：consumed key 不再沿用（从 session_out/new_prev 移除，防幻影残留）；`_run_multi_no_retrieve` 同步同规则。
- 安全边界：归并仅限"prev 侧 app 为空且 svc 唯一"（防误并新建意图）；无"删字段"语义，final 推导无反例风险。

**冒烟**（真实 API，S-000318 + S-000082，各 2 跑）：S-000318 两跑均修复转正（最终单意图 `爱奇艺/看直播` 七字段全对，幻影 `/看直播` 被 consumed 移除）；S-000082 一对一无回归（中间一次 resolution/rtt 取错画像值为已知 API 波动，与机制无关——机制不触字段值只触 key/归并）。selftest 全过。

**预期影响面**（v24.1 产物估算）：missed_intent 桶 21 例 key 错位（空 app 头 6+/同 app 异 svc 7/异 app 同 svc 14）+ 9 个带内容幻影意图的 session + session 级"空意图不算 pred"漏洞（若收紧口径 80.12%→77.33%，另行拍板）。→ **已全量验证采纳**（双跑 86.65%/87.89%，见下条 v25.2 全量版）。冒烟脚本 `tools/_smoke_v25_2_keyfix.py`。

**同批 error_report UI 升级**：turn/session 双视图切换（各自独立错误分布+原因分布+样本；session 级原因=失败 turn 众数标签）；session 卡新增"最终结果"块（GT∪pred 每意图完整 7 字段对照，空 app 残骸标注）+ 状态机对照块；**状态演进交互模块**（左侧点击对话轮 → 右侧实时显示截止该轮累积状态 GT vs 模型，红=不一致/蓝=本轮改动，含第1轮提取；数据源优先 turn_outputs，旧产物回退 turn_detail）；GT 推算字段"推算"徽章；missed_intent 的 key 错位徽章+分析说明。旧 `session_intent_diff_html`（曾过滤无 app pred 意图）已删。


## v25.2 harness 侧意图对象语义修复（key 断裂三层修复，2026-09-04，当前最佳）

**背景**：v24.1 err_report 深挖 missed_intent 桶发现 34 例中 21 例非漏识别而是 **intent 头 key 断裂**——模型识别了意图但 key 不一致被判集合不一致。根因在 harness：①intent key 每轮只从 delta 的 app/svc 推导（pipeline run_multi），改口轮只改 app 时 svc 在 prev 里 → key 丢 svc（S-000318 "爱奇艺/看直播"裂成 "爱奇艺"，旧 key 残留幻影）；②补槽轮 prev 挂在无 app 头（'/看直播' 或 'none'）下，轮2 用户补 app、模型 header 写全 app/svc 时按内容匹配不到 prev → 对空 prev 新建 stub，两份状态并存；③改口改名后旧 key 不退役 → 幻影意图（26 个 session 带内容空 app 幻影，其中 9 个被"空意图不算 pred"口径免罚）。

**改动（三层 + 补丁，全部 harness 侧，prompt/模型协议零改动）**：
- pipeline run_multi：key 从**合并后 final_state** 推导（`intent_key(flatten(final)))`）——对象状态=prev+delta 合并结果，规范名从当前状态内容导出；对新意图等价，系统无"删字段"语义无反例。
- pipeline `_match_prev_key`（python_state 同步）：精确 > 同 app 前缀 > 单 prev 无 app 必归并（S-000850 的 'none' 键情形）> 无 app 同 svc 唯一归并。
- 归并/改名时 `final["_consumed_key"]` 标记被吃掉的旧 key，session_eval 透传时与取消同路移除（session_out + prev_states），幻影不再残留。
- error_report session 卡新增"最终结果"块（GT∪pred 每意图 7 字段对照 + 空 app 残骸标注）与状态机对照块；状态演进模块逐轮全量累积继承（GT 按 intent_id 折叠）。

**实验（d5 剔除 322，qwen-plus，v25.1 口径，双跑）**：

| 版本 | session | turn | 状态机 |
|---|---|---|---|
| v24.1 基线（run1/run2） | 80.12% / 81.37% | 84.67% / 85.55% | 81.27% / 81.57% |
| v25.2 中间版（缺单prev归并） | 86.34% / 86.65% | 87.88% / 87.59% | — |
| **v25.2 最终（run1/run2）** | **86.65% (279/322) / 87.89% (283/322)** | **88.32% (605/685) / 88.61% (607/685)** | **84.44% / 84.44%** |

- 双跑均值 **87.27%/88.47% vs 基线 80.75%/85.11% → session +6.52pp / turn +3.35pp，项目最大单步收益（超过 v24.1 的 +4.97pp）**。
- 获救 30/32（双跑一致 24：T2-1×5、T2-3×5、T2-2×3、T3-2×3、T3-4/T3-3/T3-1×2、X-2/T1-1×1——原错 15 个 intent_set_mismatch + 9 field_wrong，全是 key 断裂/补槽丢 key 型）；回退 9/7，**双跑一致回退仅 1（S-000392，T1-3 同头多意图单轮省略 resolution/rtt 的模型波动——v24.1 那次 rtt 错这次 resolution 漏，同句"微信1080p"画质感知本在波动边缘，非机制致退）**，机制零误伤。
- 结构性改善：带内容空 app 幻影 26→**0/1**（run1 清零）；missed_intent 34→21/22；duration 错 13→0（run1）；T2-1 turn +19.4pp、T3-4 +16.7pp、T2-2 +12.1pp、T3-3 +12.9pp；T1-3 −6.0pp/T1-4 −6.2pp 为模型单轮多意图懒省略波动（S-000392 型）。
- "judge_session 空意图不算 pred"口径收紧问题（曾测 −2.8pp）随机制修复自然消解，评估口径不动。

**结论**：采纳，**当前最佳 = v25.2（代码即最佳，无 config 开关——harness 修复对旧配置同样生效）**。教训：①增量范式里"对象身份"必须有 harness 侧对象语义兜底（解析引用/合并状态/改名退役），模型自造的字符串 key 不可靠；②同类结构问题先看 harness 再怪模型——v24.1 时代 missed_intent 被归因为 PER/INT 能力短板，实为 key 断裂假象。下一步 = v26.0 intent-id 协议（身份与内容彻底解耦，设计已冻结）。

**产物**：`eval_dual_v25_2_keyfix2(.jsonl/_summary/_errors.html)`（run1）、`eval_dual_v25_2_keyfix2_run2(.jsonl/_summary/_errors.html)`（run2）、中间版 `eval_dual_v25_2_keyfix(.jsonl/_summary)`/`_run2`；冒烟 `tools/_smoke_v25_2_keyfix.py`（S-000318/082/850/906/187）。


## v26.0 intent-id 协议（身份与内容解耦，双跑净负回退，2026-09-07）

**背景**：v25.2 在 harness 侧模拟对象语义救回 +6.52pp 后，把引用解析彻底协议化——intent 身份用稳定 id（I1/I2…）与内容 key 解耦，模型段头写 `# intent: I1`（更新）/`new`（新建）/`# cancel: I1`（取消），消灭 harness 侧全部匹配试探。用户拍板：id 形式 I1/I2/I3；不做内容回退靠 prompt 硬约束保证遵从；旧 prompt 保留可选项（config `intent_id_protocol.enabled` 默认关）。

**改动**（新增 `intent_ids.py` ~130 行 + pipeline/prompt_builder/session_eval 双协议分支，协议关闭时零影响）：
- IntentIdBook：id 分配/id↔内容 key 映射/rename（改口时 key 变 id 不变）/describe_prev（`I1 = {...}` 渲染）。
- pipeline run_multi id 分支：`I1` 直接查表取 prev（绕过 `_match_prev_key` 全部试探）；`new` 分配新 id；cancel 直查 id；PAR 回路加"段头非 id 重采样一次"。
- prompt_builder：TASK 块 id 化 + 三条段头硬约束；消歧/改口规则段 id 版文案；demo 程序头 `new`/`I1` 二分（v12.0 教训：demo 与目标协议逐字节一致）。
- session_eval：每 session 一本 id 账，prev_states 内容键→id 键翻译；透传/JGA 逻辑不变。
- state 映射机制：id 在段头解析成内容键→取出该意图 prev→State(prev) 构造——state 仍是"当前段所属意图"的匿名投影，代码体（state.xxx/find_timestamp）零改动。

**遵从率（冒烟 10 session + 全量）**：段头遵从 **100%（冒烟 27/27、全量 729/729）**，cancel 2/2——qwen-plus 对 id 协议遵从无压力，prompt 硬约束+demo 一致化策略成功。

**实验（d5 剔除 322，qwen-plus，v25.1 口径，双跑）**：

| 版本 | session | turn | 状态机 |
|---|---|---|---|
| v25.2（基线，run1/run2） | 86.65% / 87.89% | 88.32% / 88.61% | 84.44% / 84.44% |
| **v26.0（run1/run2）** | **83.54% / 83.54%** | **85.69% / 85.55%** | 82.33% / 83.08% |

- 双跑完全一致净负：**session −3.11pp/−4.34pp、turn −2.63pp/−3.05pp**，判负回退（`intent_id_protocol.enabled` 保持默认关）。
- 获救 15/14（改口/多意图场景 id 优势兑现，S-000392 型懒省略转正），回退 25/28（双跑一致 13）。

**回退根因（13 个双跑一致回退，逐个核验）**：
- **new 滥用 ×5（最大根因）**：残缺意图补全场景（T2-1/T2-2"轮1 无 app 意图→轮2 补 app"），模型面对 `I1 = {service_name:视频, ...}` 不敢确定是更新还是新建，写 `new` 致 I1 残骸与新意图并存（S-000183/263/446/463/502 型）——**v25.2 的"单 prev 无 app 必归并"规则恰是治这个的，id 协议把引用解析交还模型，模型在补全型共指上恰恰不可靠**。
- cancel/update 同 id ×1：模型同轮 `# cancel: I1` + `# intent: I1`，对 id 的心智模型与 harness 分配序错位（S-000416，v25.2 用名字取消无歧义）。
- 字段值/继承波动 ×7：resolution 丢失等，prompt 变长的注意力成本。

**结论与教训（重要失败模式，勿忘）**：
1. **引用解析的责任分配有下限：补全型共指（残缺意图补 app/svc）模型承担不了**——v25.2 的启发式归并不是"补丁"而是"必要职能"，协议纯度不能替代它。若保留 id 协议须叠加"new 段 harness 归并校验"，但那等于 v25.2 规则换触发位置，协议无增益。
2. id 的隐式分配序（段序）与模型心智模型可能错位（cancel 对象错位）；名字引用在 cancel 场景反而无歧义。
3. 遵从率 100% ≠ 语义正确：形式遵从与语义正确是两回事，协议类实验必须看语义翻转而非只看格式。
4. **当前最佳维持 v25.2**（harness 对象语义模拟）。v26.0 代码保留（开关默认关），id 协议的 value 若干年后模型共指能力变强可再验。

**产物**：`eval_dual_v26_id(.jsonl/_summary/_errors.html)`、`eval_dual_v26_id_run2(.jsonl/_summary/_errors.html)`；配置 `configs/config_d5_qwen_plus_idproto.json`；冒烟 `tools/_smoke_v26_id.py`（10 session 遵从率 27/27）。


## v26.1/v26.2/v26.2b id 协议补丁链（迭代采纳，2026-09-08，当前最佳）

**背景**：v26.0 裸 id 协议双跑净负（83.54%×2，−3.11pp/−4.34pp）后，逐层补丁三轮迭代。每轮的回退根因分析直接驱动下一轮修复，最终形态验证了"引用解析责任分配"的完整答案。

**三轮迭代（每轮 = 单跑 + 翻转归因 → 针对性补丁）**：

| 版本 | session | 关键回退根因 → 补丁 |
|---|---|---|
| v26.0 裸协议 | 83.54% ×2 | new 滥用×5（残缺意图补全模型不敢写 I1）+ cancel id 错位×1 + 继承波动×7 |
| v26.1 = +①new 归并校验（无 app 同 svc 唯一 → prev 填空槽+残骸退役；弱信号级：唯一无 app 悬置意图）②cancel 名字化（prompt 强制写 app/svc 名） | **89.44%** | 8 个稳定回退中 6 个 = "有 app 意图改配置仍写 new"（S-000040 型，两级归并信号覆盖不到） |
| v26.2 = +③身份判定强制化（段头前必须写 `# 身份判定:` 行+demo 同步+PAR 校验 `_identity_ok`）④full-match new 重采样门 | 86.65% | S-000040 现场抓出段覆盖 bug：同轮 I1 段(完整)与冗余 new 段产出同 key，dict 后写覆盖先写 |
| **v26.2b = +⑤段去重（new 段产出 key 已在本轮 results → 丢弃冗余段）** | **88.51% / 88.82%** | — |

**定案（v26.2b 双跑 vs v25.2 双跑，qwen-plus，d5 剔除 322，v25.1 口径）**：

| 指标 | v25.2 | v26.2b | Δ |
|---|---|---|---|
| session | 87.27% (86.65/87.89) | **88.67% (88.51/88.82)** | **+1.39pp** |
| session 极差 | 1.24pp | 0.31pp | 方差更小 |
| turn | 88.47% (88.32/88.61) | 88.83% (88.03/89.64) | +0.37pp |
| 状态机 | 84.44% | 85.73% | +1.29pp |
| 稳对（双跑都对） | 267 | 275 | +8 |

- 稳对独占：仅 v26.2b 稳对 23 个（new 滥用修复面）vs 仅 v25.2 稳对 15 个（部分为 prompt 变长继承波动），净 +8。四条证据（三指标均值 + 方差 + 稳对数）同向，**采纳转正，v26.2b = 当前最佳**。
- ⚠️ 方法论修正记录：v26.2（含段覆盖 bug 的一次跑）曾混入均值与多数票统计，把 v26 系列算成"均值虚高/稳对率反输"——**含 bug 的采样必须从统计中剔除**，这是本次最重要的实验卫生教训。

**引用解析责任分配——最终结论（修正 v26.0 的"回退"定性）**：
1. 模型可靠承担**显式指派**（改口/多意图更新/消歧选择——id 或名字都行，id 更省 token）；
2. **推断指派**（补全型共指：残缺意图补 app、有 app 意图改配置但模型写 new）模型不可靠——必须 harness 兜底（归并校验两级信号 + full-match 重采样门）；
3. **身份判定的收益在"强制写出理由"而非"换引用介质"**——PAR 式生成前推理有效（v11 选 record +2.87pp 的同构复现），但每段 +1 行的 prompt 税存在（15 个 v25.2 稳对翻转成波动）；
4. cancel 场景**名字优于 id**（名字自校验；id 分配序与模型心智模型错位无法从格式识别）；
5. **harness 段间覆盖必须有去重审查**——dict 后写覆盖先写这类 bug 不在翻转归因里抓不到（S-000040 现场：I1 段输出完全正确被冗余 new 段盖掉）。

**产物**：`eval_dual_v26_1(.jsonl/_summary/_errors.html)`、`eval_dual_v26_2(.jsonl/_summary/_errors.html)`（含 bug，仅存档）、`eval_dual_v26_2b(.jsonl/_summary/_errors.html)`、`eval_dual_v26_2b_run2(.jsonl/_summary/_errors.html)`（采纳版双跑）；配置 `configs/config_d5_qwen_plus_idproto.json`；冒烟 `tools/_smoke_v26_id.py`。


## v26.2c 弱模型适配（沙箱逐行容错 + 只写变化字段 prompt）+ 四模型对照（2026-09-09）

**背景**：qwen3.8-27b 首测暴露"过度沿用"脆弱模式——弱模型习惯写全字段沿用（`state.rtt=state.rtt`、`find_timestamp(start=state.start_timestamp,...)`），引用 prev 里不存在的槽位时沙箱 coreference 异常 → **整段 delta 丢失**，连已写对的字段一起丢（q38 首测 resolution 缺失 31 例中 24 例此模式，session 77.95%）；plus 的最小 delta 习惯（只写变化字段）掩盖了该风险。同时暴露非标头（"I1："/"Intent 1"）致 results[None] 崩溃的防御缺口（plus 遵从 100% 掩盖）。

**改动（对 plus 路径无回归，已冒烟验证 3 靶向 session 与双跑一致）**：
- `python_state.execute_program(line_tolerant=True)` 默认开：**逐行容错执行**——单行 coreference 失败跳过该行，保留已成功赋值的行（放弃单次 exec 原子性；本任务程序为简单赋值序列，行间无依赖，容错收益远大于风险）。
- prompt（id 协议 TASK 块）：新增"只写【发生变化的字段】的赋值；未变化的字段不要写 state.xxx = state.xxx 沿用（未写的字段自动沿用；写了反而可能因引用不存在的槽位而出错）"。
- pipeline id 路径：非标头/未知 id 落"更新"分支且 delta 无 app/svc 时丢弃该段（防 results[None]）。

**四模型对照（同 v26.2b+容错配置，d5 剔除 322，qwen3.8/32b/235b 单跑）**：

| 模型 | session | turn | 状态机 | vs 各自旧配置 |
|---|---|---|---|---|
| qwen-plus | 88.67%（双跑均值） | 88.83% | 85.73% | — |
| **qwen3.8-27b** | **89.13%** | **89.93%** | 85.05% | **+11.18pp**（v1 77.95%） |
| qwen3-235b-a22b-instruct-2507 | 82.61% | 83.65% | 83.38% | **+20.11pp**（v18 62.50%） |
| qwen3-32b | 73.29% | 77.37% | 80.06% | **+12.11pp**（v23.3 61.18%） |

- **两项弱模型适配对所有代模型普遍兑现，且与模型强度反序**（最弱的 235b 涨最多 +20pp）——"过度沿用"问题最重的模型容错救得最多。qwen3.8 v1→v2 +11.18pp 中，"过度沿用"崩溃模式被消灭（REA 32 成最大桶，继承类 CTX 只剩 4）。
- **模型档差距被 harness 大幅压缩**：plus−32b 从旧配置时代的 ~27pp 收窄到 15.4pp；plus−235b 从 ~26pp 收窄到 6.1pp。
- **qwen3.8-27b 双跑坐实反超 plus**：run2 90.68%/90.95%，双跑均值 **session 89.91%（89.13/90.68）vs plus 88.67%、turn 90.29% vs 88.83%**，极差 1.55pp 正常；稳对（双跑都对）282 vs plus 275，**净 +7 个 session 稳定胜出**。"指令特化中型模型（27B）+ 好 harness ≈ 平替并反超 plus"，部署成本大降——**当前单模型最高分 = qwen3.8-27b v26.2c**。
- 模型锚点修正：qwen-plus 能力锚点 ≈"新一代 27B 指令特化档"（非旧 32B 档）；235b 旗舰在代码生成式 DST 上仍显著弱于 plus（−6.06pp）。

**产物**：`eval_dual_v262b_qwen38(.jsonl/_summary/_errors.html)`（v1 无容错）、`eval_dual_v262b_qwen38_v2(.jsonl/_summary/_errors.html)`、`eval_dual_v262b_qwen32b(...)`、`eval_dual_v262b_qwen235b(...)`；配置 `configs/config_d5_qwen38_27b_idproto.json`/`config_d5_qwen3_32b_idproto.json`/`config_d5_qwen3_235b_idproto.json`；冒烟 `tools/_smoke_qwen38.py`。


## v26.2c-d6 dataset_6 泛化性测试 + candidate_map 转正（2026-09-10/11）

**dataset_6 数据处理**：438 行中 201 个 session_id 各有两份**完全不同的渲染**（同 id 两份不同对话，非版本迭代而是 id 冲突）→ 去重策略=render_meta 优先（含 faithfulness_soft_passes 质检记录）→ 237 唯一 session（`data/dataset_6_clean/`）。无 users 文件（users_3000.jsonl 从 d5 拷贝，225 用户全覆盖）；render_model=qwen3.8-flash（生成器换模型）。find_noise R1–R5 扫描（画质表 26 条从 train 还原）→ **3 个 R5-A 问题样本剔除单列**（S-000106/210/228，record 选择 GT 错位）。

**泛化性结果（plus + v26.2c，237 全量剔 3 = 234 参评，单跑，检索库=d5 casebank）**：

| 口径 | d6 全量 | d5（对照） |
|---|---|---|
| session | **84.19%** (197/234) | 88.67% |
| turn | **87.27%** (432/495) | 88.83% |
| 状态机 | 85.16% | 85.73% |

泛化基本保持（−4.5pp 在跨数据集波动内，无崩溃性失败）。**过程事故**：run1 忘设 DST_DATASET → load 扫到 d5 文件、94 test 只匹配 73 id（静默错配数字作废）——**换数据集三件套（DST_DATASET/--split/--noise_file）必须一起设**；用户纠正"d6 不需要 train（检索库还原 d5）"且"用全量"（非 split 的 92）。

**candidate_map 在 d6 转正（消歧洼地实测）**：d6 全量 T3-3 消歧 3/10=0.30 是最大洼地。开启 `disambiguation_rule.candidate_map=true`（v24.4 机制：harness 解析 agent 话术候选顺序→注入"第N条=记录字段明细"对照表）重跑：

| 口径 | 基线 | candmap | Δ |
|---|---|---|---|
| session | 84.19% | **87.18%** (204/234) | **+2.99pp** |
| turn | 87.27% | 88.48% | +1.21pp |
| T3-3 session | 3/10=0.30 | **5/10=0.50** | +20pp |
| T3-3 turn | 7/23=0.30 | 10/23=0.43 | +13pp |

获救 12/回退 5；**5 个回退逐一核验全部无 candmap 消歧轮**（T2-4/X-1/T3-2/T3-1 的字段值波动，非机制致退）——**机制零误伤，因果面干净，d6 上转正**（`configs/config_d6_qwen_plus_candmap.json`）。S-000040 教学案例：模型把"第一条"对齐到 records 顺序而非 agent 话术顺序，candidate_map 的对照表直接消除该对齐负担。离线解析率 d6 T3-3 消歧轮 5/11（45%）——D6 的话术变体（"时延不唯一您选7月18日还是7月11日"日期式/非标准枚举）超出 d5 时代解析器覆盖，**未解析轮回退纯文字规则**（安全回退设计生效），后续可扩充解析器覆盖。

**产物**：`eval_dual_v262c_d6_full237(.jsonl/_summary/_errors.html)`（基线）、`eval_dual_v262c_d6_candmap(...)`（candmap 转正版）；配置 `config_d6_qwen_plus_idproto.json`（基线）/`config_d6_qwen_plus_candmap.json`（转正）；`data/dataset_6_clean/`、`split_d6_full.json`、`_noise_exclude_d6_scan.json`、`case_bank_train_d6.jsonl`（未用于检索）。

---

## v26.3-p1p2 错误分析修补：应用名待补规则 + eval 同 key 双意图徽章（2026-09-11，后续已验证 → v26.3-p1 采纳）

**背景**：d6 candmap 版 error html 错误分析（用户逐例核查）发现两类结构性问题：
1. **幻觉 app（extra 同 svc 异 app，8 session：S-000033/040/054/149/155/174/203/244）**：turn1 用户只说服务词不说 app 时，模型被"application_name 必须取 schema Literal"约束逼着从 records/矩阵猜 app（S-000149 猜哔哩哔哩=records[0] / S-000155 猜YY=矩阵首个 / S-000244 fallback 优酷=schema 首项），turn2 用户补真 app 后猜测头变幻影。d6 GT 中 app 非 turn1 首现的 session 有 76 个，暴露面大。
2. **GT 同 key 双意图塌缩（6 session：S-000035/046/113/129/200/225，全在 T1-3 同头多档位/T2-6）**：session 级 gt/pred 按 app/svc 建 dict，同 key 多意图互相覆盖——模型三段全对（S-000113 llm_raw 实证）也会因塌缩只剩一条，turn 级误判"resolution 错"；或塌缩后恰好相等造成 session 假阴性（S-000046/129/200 turn1 各错 1 条但 session 判对）。S-000225 加重版：turn1 塌缩→turn2"撤销第一个"无法从单 key 状态定位→全空。

**方法改动（生成侧 P1）**：
- `pipeline.py`：新增 `_utterance_mentions_app`（话语明说检测：矩阵内+域外 app 名匹配）+ `_pending_stubs`（id_states 中无 app 桩检测）；`run_multi` 触发式注入 PENDING-APP RULE（话语未明说任何 app 时：①禁止从 records/矩阵/schema 猜 app，已知字段照记 app 留空 ②列出当前待补桩 id，补充/确认类话语写该 id ③summary 写明"应用名留待补充"）。
- `prompt_builder.py`：`build_prompt` 新增 `pending_app_hint` 参数（与 record_rule 同构的触发式规则段，遵守"约束类规则必须触发式注入"纪律）。
- `pipeline.py _process_id_segment` 归并路径 id 连续性修复：v26.1 归并用 `ensure(k, alloc())` 会给补全后的 key 分配**新 id**（旧桩 id 残留、_iid 断裂）；改为 `rename(mk, k)`（与更新段同路径，id 不变）——离线验证 S-000149 场景 turn2 new 段补 app 后 `_iid=I1` 沿用、桩 key `/看直播` 经 `_consumed_key` 退役、prev 时间字段保留。
- harness 归并链（`_match_prev_key` 唯一无 app 同 svc 归并）本就绪，只差生成侧许可——机制零新增，纯 prompt 放开。

**方法改动（eval 侧 P2，诚实度）**：
- `error_report.py`：新增 `dup_key_sessions`（基于 slot_updates 检测同 app+svc 多意图 session，含后被取消的意图——S-000225 型取消前轮次同样受塌缩影响）；turn/session 卡片头部打"同key双意图·塌缩"紫徽章（title 说明失真机制），提示判读结合逐轮明细。
- d6 candmap errors.html 已重生成：S-000035/046/113/129/200/225 全部命中徽章（其余 229 session 零误报）。

**涉及文件**：pipeline.py / prompt_builder.py / error_report.py；配置新增 `pending_app_rule.enabled`（默认关 → 已双跑采纳转正进 d6 最佳配置，见 v26.3-p1 采纳条）。

**验证**：selftest 全过；离线 mock 测试 4 项（触发器精度 4/4、桩检测、turn1 注入/turn2 不注入、归并 id 连续性）全过；HTML 徽章重生成验证。

**P3 立项（未实施）**：同 key 双意图的多实例 key（`淘宝/看直播#2` 实例索引）+ cancel 时间锚定消歧（S-000225 型）——真正的架构升级，需 GT 侧同步改造 + 双跑消融，建议下轮评估后立项。

**结论**：P1/P2 均为低成本确定性修补（机制现成/纯标注），P1 预期收益=d6 8 幻觉 app session，P2 预期收益=错误分析诚实度（不计分变化）。**待 d6 双跑验证 pending_app_rule 后归档结论。**

---

## v26.3-p1 三轮单跑迭代（2026-09-11~14，v3 文案大幅转正，后经双跑采纳）

**背景**：v26.3-p1p2 的 pending_app_rule 首版（v1）单跑 85.47%（−1.71pp）。逐例归因发现两类问题：①S-000184 副作用（更新段 app 被留空丢失）；②S-000220 副作用（补参数轮"720P，时延按上次记录"模型误判新建而非更新 I1，意图分裂）。两轮文案修补 → v3。

**文案演进**：
- **v1**：禁止猜 app + 桩列表（无 app 桩意图 id 列表）提示补充时写 id。单跑 session 85.47%：目标 8 获救 4（149/155/244/54 机制因果明确），但 S-000184 丢 app。
- **v2**：+更新段 application_name 必须 coreference 沿用（state.application_name = state.application_name），不得因不赋值丢失。单跑 85.47%：S-000184 修复，但暴露 S-000220（I1 有 app 不在桩列表，"补参数"被判新建 → 意图分裂）。
- **v3**：文案重构为**【默认更新, 谨慎新建】**：话语无 app 时若内容对应已有意图的未收齐槽位/延续语境 → 必须写该意图 id（唯一时就是它），只赋新字段；app 用 coreference 沿用；仅当"新服务词+新时段且与所有已有意图都对不上"才 new；**拿不准一律按更新**（错误代价不对称：误更新丢 1 意图 vs 误新建分裂+传染后续轮）。

**结果（d6 234 参评，qwen-plus 单跑）**：

| 版本 | session | turn | 状态机 |
|---|---|---|---|
| 基线 candmap | 87.18% | 88.48% | 85.71% |
| v1 | 85.47% | 87.27% | 85.71% |
| v2 | 85.47% | 87.07% | 85.90% |
| **v3** | **90.60% (212/234)** | **90.51% (448/495)** | 87.01% |

**v3 翻转归因**：获救 11（S-000149/155/244/054/004/019/042/164/173/208/239，含 T2-1/T2-2/T3-1 补槽/歧义/待补多型，8 个有机制痕迹）；回退仅 3（056/156/246，全部零触发、消歧画质/时间裁决波动）。**v3 独有转错 = 0**（无任何基线/v1/v2 皆对而 v3 错的 session）。稳定性：三跑皆对 182/234。

**目标案例终态**：幻觉 app 类 S-000149/155 两跑稳定获救、S-000244 v3 转对（v2 丢于"100毫秒"未归一波动）；漏意图类 S-000046/129/225 属同 key 双意图塌缩（P2 徽章已标，判分修复归 P3），P1 治不了。

**产物**：eval_dual_v263p1_d6_pendapp{,_v2,_v3}_run1(.jsonl/_summary/_errors.html)；配置 config_d6_qwen_plus_pendapp.json（pending_app_rule.enabled=true）。

**结论**：v3 文案方向正确且大幅转正（+3.42pp session 远超噪声带）。~~按纪律待双跑确认~~ → **已双跑确认采纳**（run1 90.60%/run2 86.32%，均值 88.46%，+1.28pp，2026-09-15 转正进 d6 最佳配置，见下条）。v1/v2 两版副作用的教训：触发式规则的"更新 vs 新建"边界必须与模型可见信息（桩列表只含无 app 意图）对齐，否则规则本身成为新的误判源（v12.0 demo 格式教训同构）。

---

## v26.3-p1 采纳 + v26.3-p3 多实例 key 实施（2026-09-15，d6 新最佳配置）

**pending_app_rule 采纳（v3 文案，双跑确认）**：

| 口径 | v3 run1 | v3 run2 | **双跑均值** | 基线 candmap | Δ |
|---|---|---|---|---|---|
| session | 90.60% | 86.32% | **88.46%** | 87.18% | **+1.28pp** |
| turn | 90.51% | 88.48% | **89.49%** | 88.48% | +1.01pp |
| 状态机 | 87.01% | 86.64% | 86.83% | 85.71% | +1.11pp |

双跑稳对（vs 基线皆错获救）8 个：S-000004/019/042/054/155/208/239/244，覆盖幻觉 app 桩归并/T2-2 歧义留空/补槽挂靠三类；双跑稳错仅 3（056/156/246，消歧画质抖动）。run2 独有错 10 个逐个核验全为波动（resolution×6/time×2，9 个零规则触发）。run1 90.6% 有幸存者偏差，真实稳态收益 +1.3pp——**采纳进 d6 最佳配置**。S-000149 run2 暴露"留空不猜"残余风险（模型无锚点时时间漂移把 reference_time 当 start），单跑抖动级，记录在案。

**v26.3-p3 多实例 key 实施（multi_instance_key.enabled，与 idproto 联动）**：

- **eval 侧**：`session_gt`/`turn_gt` 按 intent_id 实例化（`app/svc#N`，编号基于全量 intents[] 含 cancelled 的 base 首现序，与 pred 侧 idbook.alloc 序对齐——S-000225 推演验证 I1 cancelled 占 #1/I2=#2）；状态机 GT 轨迹（`build_gt_status_trajectory`）与 pred 侧 `turn_status`（经 `_iid→key` 映射）同步实例化；`error_report.build_source_map` 按产物 summary 口径自动同步。
- **pipeline 侧**：`_instanced_key`/`_base_key`；三处塌缩点修复（`_process_id_segment` 归并/更新路径实例化写 results、段去重窄化为仅同实例 key 重复）；`_suspicious_new` mi_on 下放行合法第二实例（参数有非空差异才放行，纯复读保守判可疑）；`intent_ids.rename` 目标被占时自动实例化不再 pop victim。
- **cancel 消歧**：prompt 协议加"同类多份保障之一写 id"；`_resolve_cancel_key` 三级回退链（id 直达 > `_parse_time_hint` 时段匹配 > `_parse_ordinal` 序数，仅 mi_on 生效）。
- **顺带修存量 bug**：`run_multi` 段循环 `if idbook:` 受 `IntentIdBook.__len__` truthiness 陷阱影响（空账本 falsy → 首轮静默走旧协议段处理路径，v26 时代因空 prev 单段行为巧合一致未暴露）→ 改 `is not None`。
- **mi_off 回归验证**：原塌缩/去重语义零变化；离线 5 项测试（双实例并存/id-cancel 精确移除/时段消歧指向 #1/mi_off 回归/复读段保守判定）全过；selftest 全过。

**新最佳配置**：`configs/config_d6_qwen_plus_best.json`（candmap + pending_app_rule + multi_instance_key）。→ P3 已五跑迭代完成采纳（dup-key 6/6 判对，见下条 v26.3-p3 采纳定案）。

**产物**：v263p1_d6_pendapp_v3_run2(.jsonl/_summary/_errors.html)；v263p3_d6_best_run1（跑中）。

---

## v26.3-p3 多实例 key 五跑迭代收尾（2026-09-15，dup-key 6/6 全判对，采纳定案）

**P3 首次全量的 key 路径四轮 bug 修复迭代**（每轮全量 234）：

| 跑 | 修复内容 | session | turn | 暴露问题 |
|---|---|---|---|---|
| run1 | 跨轮实例延续（prior_keys 纳入 prev_states） | 88.03% | 90.51% | 更新段改名误编 #N（未爆发） |
| run2 | +更新段修复 | 75.64% | 81.82% | **修复引入自冲突**：改名目标被 `_instanced_key` 当占用避开→36 session 幻影 #2 |
| run3 | +更新段沿用 rename 后 idbook key | 86.32% | 89.29% | **归并路径同型 bug**（S-000149 补 app 段被编 #2） |
| run4 | +归并段沿用 idbook key | 88.03% | 90.51% | S-000225 空 delta 沿用段**自我吞并**（k 从合并 final 推导丢 #N 后缀→伪改名→_consumed_key=自己→透传 pop 掉自己） |
| **run5** | +base 相同即未改名（不 rename 不 consumed） | **88.03% (206/234)** | **89.90% (445/495)** | 无新问题，**dup-key 6/6 全判对** |

**核心成果**：**S-000035/046/113/129/200/225 六个同 key 双意图 session 全部干净判分**（v3 mi_off 口径下 5 个是塌缩假阴性、1 个全错）——P3 的设计目标（塌缩假阴性消除 + cancel 实例定位）完整兑现。S-000225 端到端：turn1 双实例并存 → turn2 `# cancel: I1` 精确移除 + I2 沿用段存活 → session 对。

**判分口径变化（诚实度收益）**：mi_on 暴露被塌缩掩盖的真实模型错误——S-000046 第二档"高清"映射错（720p，查表应 1080p）在 mi_off 塌缩口径下每个 run 都存在但从不判错。run5 翻转面：获救 11（含 225/129/149 直接 P3 收益）/ 回退 9（S-000042/064/099/104/146/164/244/248/249，与 v3 各跑互有胜负属波动带，无 #N 错位）。

**与 mi_off 口径关系**：session 88.03% vs 88.46%（−0.43pp）——mi_on 判分更严（暴露真错误）且修回 6 个 dup-key，两者相抵后持平；**后续 d6 数字以 mi_on 口径为准**（config_d6_qwen_plus_best.json）。

**迭代教训**：①实例化改造的 key 路径有四条（new/归并/更新/空 delta 沿用），**任何一条"从合并 final 推导 key"的逻辑都必须过"base 相同=未改名"检查**；②修复引入的次生 bug 比 bug 本身更猛（run2 −12pp），**每轮修复必须配全场景离线回归**（本轮补齐 6 场景 mock：改名/实例更新/cancel+update/mi_off/纯复读/补 app 归并）。

---

## v26.4 入库前重构：死代码清理 + Features 开关层 + gt_build 解耦 + 正式打包脚本（2026-09-15）

**背景**：v26.3 定案后入库前清理。目标：删"后续不可能启用"的回退机制代码、开关解析收敛为单一事实源、消除双向依赖、打包脚本正式化。

**删除（回退机制活代码）**：
- conflict_check 反思回路（v25.0 双跑一致净负，反思路线全线关闭）：pipeline `_conflict_check_round`（~90 行）+ run_multi 触发块 + `last_reflection`；session_eval reflection 记录组装（turn_outputs 不再含 reflection 键）；prompt_builder `conflict_hint` 参数与渲染。validator 的 `detect_utterance_conflicts` 段（~150 行）**原样移入 tools/_archive/conflict_detector.py** 留档（历史实验可复现，附复现依赖说明）。
- 一次性脚本归档：`_oracle_pass2.py`（BoN 门控判定，docs 有引用故归档不删）、`find_noise.py`（数据工具）→ tools/_archive/。
- 历史压缩包 docs.rar/docs.zip/eval zip → data/_archive/。
- 死配置键清理（9 个活跃 config）：`record_selector`（代码已删）、`conflict_check`、`output_mode`/`full_quality_map`/`quality_map_version`/`validator.strict`（全仓 .py 零引用）。

**新增 features.py（机制开关单一事实源）**：
- `Features` frozen dataclass：12 个机制开关从 config 一次性解析；**联动/派生规则唯一实现处**（candidate_map ⊂ disambiguation_rule、multi_instance_key ⊂ intent_id_protocol——此前联动判断写了 3 遍）；
- `switches()` 输出全量快照供 summary.json 落盘（取代旧 8 键硬编码列表）；
- 扩展指引：新机制 = Features 加字段 + from_config 一行 + 使用处读 `features.xxx` + config 加键；**禁止在 features.py 之外做机制开关解析**。

**新增 gt_build.py（turn 级 GT 构建层，解耦）**：
- `turn_gt`/`cancel_turn_map`/`correction_turn_map`/`_derive_time_flat`/`CANCEL_GT`/`CORRECT_MARK` 自 session_eval 原样拆出（161 行，零行为变更）；
- session_eval 与 error_report 均单向 import gt_build，**消除 session_eval↔error_report 双向惰性 import**；
- 顺手修一隐患：error_report `_cum_gt_by_turn`/`state_evolution_html` 的 turn_gt 调用补传 mi_on（读产物 summary 口径）——否则 mi_on 产物的报告 GT 累积态会退回塌缩口径；
- 删 session_eval `_resolve_cancel_key_local` 薄包装（直接 import pipeline 公共函数）。

**其他**：`load_config` 默认路径改 `config_d6_qwen_plus_best.json`（与文档一致）；main.py demo 断言更新（v0 时代"豆瓣应 valid"文案过期，改为当前 schema 口径的三断言全过）；`.gitignore` 加 `dist/`。

**新增 tools/package.py（正式打包脚本）**：白名单收集（代码+docs+configs+tools+数据）+ 排除（keys/缓存/产物/_archive）+ **打包后自检断言**（dataset_5/6 各 17 文件、case_bank、must-have 9 文件、无敏感泄漏——v26.2c 漏 .jsonl 教训写成断言）；版本号自动取 CHANGELOG 最新条目。**`--verify` 模式（2026-09-20 补）**：本地工作目录 vs dist 包逐文件字节 diff（包内有本地缺/本地有包缺/内容漂移三方向），改代码后跑一次即知是否需重打包。产物 `dist/qwen_intent_rag_v26.4.zip`（133 文件，1.7MB）。

**规模变化**：7110 → 6393 行（−717 行，−10%）；新增 features.py(83)/gt_build.py(178)/tools/package.py(128)。

**验证**：selftest 全过；八场景离线 mock 回归全过（改名/实例更新/cancel+沿用/mi_off/补app归并/真第二实例/features触发/config清理）；main.py 真实 API 三断言全过；grep 验证死机制 .py 零引用、机制开关解析只在 features.py；打包自检全过。

**涉及文件**：pipeline.py(−83行)/session_eval.py(−176)/prompt_builder.py(−6)/validator.py(−187)/main.py(断言)/features.py(新)/gt_build.py(新)/tools/package.py(新)/tools/_archive/{conflict_detector.py,_oracle_pass2.py,find_noise.py}(新归档)/configs 清理/.gitignore/README。

---

## v27.0 时间锚点 + 明说时间证据注入（2026-09-20，六开关实现 → run1 −6.8pp 大回退 → 三轮修复 → **采纳定案 d6 新最佳 92.74%/93.54%**）

**背景**：d6 最佳配置（v26.3-p3 run5）错误报告中时间错误居首——turn 级 time_wrong 19/50，session 级 28 败中 13 个涉时间字段。跨 5 跑稳定性核查：11 个 session 机制性错误（非波动）。六类形态：①相对日期 off-by-one（明天/后天算错一天、凌晨12点→12:00；prompt 已有文字规则仍算错）②明说时间被 record completed_at 覆盖/消歧轮丢明说时间 ③矛盾口径（GT 保字面，模型自行调和翻转）④画像"常用开始时段 vs 常用保障时长"冲突（GT 取时段，旧 prompt L129 诱导取时长）⑤候选时段未定场景的落槽边界（过度赋值/过度留空双向）⑥相对日期跨意图传播漏。方案全文 `docs/plan_time_anchor_20260920.md`。

**形态决策（否决项有实证）**：后处理纠错/覆盖 pred 否决（v23 时间规则移植恒等失效、v9.0 选择器二次干预净负；且本批 GT 口径本身反规范化——保字面矛盾/OCR 字面）；LLM 时间改写轮否决（改写器与 DST 共享同一日期算术弱点，误差上移+每轮多一次调用）。采纳 = **规则提取器 + 预解析 + 触发型 prompt 注入**（与 record_rule/candidate_map/pending_app_rule 全部成功改动同构：规则负责算，模型负责理解）。

**论文支持**（arXiv 检索 2026-09-20）：2505.16088 日期错误部分源于 BPE 分词割裂（文字规则救不了算术）；2601.07148 GPT-5 无工具 55.3%、**显式给日期显著提升**（支持预计算日期表）；2511.09993 工具增强代码生成 34.5%→95.31%（时间计算交确定性代码）；2507.06450 时间归一化=LLM 代码生成+可执行校验框架；2505.20243 (ACL2026 综述） 规则系统（SUTime/HeidelTime/Duckling）在窄域仍是基线强度。

**改动（六独立开关，config time_anchor/time_extractor/time_rule_contradiction/time_rule_profile_slot/time_rule_explicit_first/time_rule_candidate）**：
- 新增 `time_extractor.py`：明说时间正则提取+预解析（相对日期锚定/时段词/凌晨12点特例/起止角色标记/槽位命名直判/否定词防呆/句级日期锚继承/候选邻接标记），产出 TimeEvidence（mentions+durations+组装三元组+矛盾/候选信号）。宁缺毋滥：解析不确定即放弃该条。**d6 全量 463 话语扫描：组装值 vs GT 314/316 一致（99.4%），2 例不一致全是 GT 侧噪声字面（"3G分"/"19点70分"）**；日期表触发 196/463。
- pipeline `_build_time_hint`：组装 TIME ANCHOR（相对日期词触发的预计算日期表，查表代替算术）/ TIME EVIDENCE（明说时间预解析候选，只给证据不绑定）/ 四条 TIME RULE（矛盾保字面、画像时段优先、明说>record、候选不落槽）注入段。
- prompt_builder：`build_prompt` 加 `time_hint`/`time_anchor_on`；**时间散文规则段压缩版（_SHARED_RULES_TAIL_COMPACT，系统 prompt 9924→9084 字符）对冲变长风险**（v16.0/v26.2b 变长教训），并修两处口径：矛盾保字面、候选不落槽例外豁免"MUST 算 start"、画像时段优先（旧 L129 措辞是 D 类病根）。
- validator：`_complete_timestamp` 不再产生负 duration（矛盾场景 -30min 既不可能匹配 GT 也污染透传 state；双配置共用此 guard，A/B 比较不受影响）。
- selftest 加 test_time_extractor（日期表/锚定/凌晨12点/矛盾/候选/多意图不组装/否定防呆 8 组断言），全过。

**离线回放**（`tools/replay_time_cases.py`，11 目标 case + 12 对照，双配置同代码）：**修 5（S-000063/119/165/227/237）破 0**，S-000072/140 时间字段转正（session 仍败于 app 身份/record 字段等其他问题），S-000218 对照一度翻破但复跑两配置均 3/3——判波动（temp=0 单跑 ±2pp 噪声带内，双跑纪律适用）。S-000066 残余：矛盾口径已保字面（start/end 转正），duration=180min 是 GT 设计字段（ utterance 从未明说），不可达，记数据侧。

**遗留/边界**：S-000072 型跨轮明说时间回填（P1-b）未实施；S-000156 OCR 字面 GT 数据口径问题；画像时段/时长不一致（3 用户）已报数据侧。~~全量双跑进行中~~ → 采纳结果见下方三段迭代记录。经验沉淀：`docs/experience/11_time_rules.md`（计算给规则/理解给模型 + 三形态判决 + 8 条复刻 checklist）。

**涉及文件**：time_extractor.py（新）/pipeline.py（_build_time_hint+注入点）/prompt_builder.py（time_hint 渲染+压缩 tail 双版本）/features.py（+6 开关）/validator.py（负 duration guard）/session_eval.py（no-retrieve 路径同步）/selftest.py（+test_time_extractor）/configs/config_d6_qwen_plus_time.json（新）/tools/replay_time_cases.py（新）。

**v27.0 首次全量失败与修复迭代（2026-09-20，重要教训）**：
- run1（初版提取器）：session 81.20%/turn 84.24%（vs 基线 88.03%/89.90%，**−6.8pp 大幅回退**），time_wrong 19→36 反升。翻转面：获救 14（11 个目标 case 全中）/回退 30。状态机 79.30%（−7.33pp，同步回退）。
- 根因（破窗 30 个全部同型）：**区间终点省略时段词**——"明天晚上7点到8点半"的"8点半"被当裸时刻解析成 08:30（GT=20:30，时段词"晚上"覆盖整个区间）。更糟的是 start>end 误触发矛盾规则，prompt 指示"照字面录入"，模型忠实抄了错误预解析值。该形态在 d6 极高频（下午/晚上区间是主力句式），回放集覆盖不足+对照组 base 自身波动掩盖了 turn 级 time_wrong 增量信号（S-000005 回放中 time_wrong 0→1 但 session False→False 未计入破窗）——**回放纪律补丁：对照组必须盯 turn 级 time_wrong 增量，不能只看 session 翻转**。
- 修复（time_extractor 三处）：①子句级时段继承——裸时刻继承所在子句最近时段词的 PM 语义（混合时段句 S-000075 "晚上7点到8点…上午6点到9点"第二段跟随上午），子句无时段词回退句级主导；②跨夜滚动——end≤start 且 end 自带"凌晨"则滚 +1 天（"晚上11点到凌晨1点"），裸时刻 PM 继承后仍倒挂则重解读为次日凌晨（"晚上11点到1点"）；真矛盾（无时段词/各自有时段词的倒挂，如 S-000066/227/245）不误滚，保字面口径不变；③无锚兜底——全句无日期锚时锚定今天（d6 扫描 0 例，纯稳健性）。
- 修复后扫描：矛盾触发 27→3（剩余全是 T2-3/T3-2 设计内矛盾）；组装值 vs GT 一致 360/362=99.4%（2 例外仍是 GT 噪声字面）；30 个破窗 session 的提取值全部回对 GT 日期（29/30 全净，S-000075 混合时段经子句级继承修复）。selftest 加区间 PM 继承/跨夜滚动/混合时段/真矛盾不误滚断言，全过。
- 修复后回放（11 目标+12 对照）：**修 7（S-000063/119/165/227/237/249 + 对照 S-000218）破 0**，12 对照全净。

**v27.0 fix_run1（修复版首次全量，2026-09-20）**：session **91.88% (215/234)** / turn **93.13% (461/495)** / 状态机 85.90%——vs 基线 88.03%/89.90% = **+3.85pp/+3.23pp（单跑即超噪声带，用户拍板免双跑）**。time_wrong **36→3**（vs run1），获救 15/回退 6。回退逐案：S-000080/125 为机制 bug——**profile_slot 规则触发条件过宽**（"时长按平时"的 utterance 也触发"时段优先"，但 GT 口径：只说时长就取时长），已修为仅"时段"提及才触发；S-000075 同 key 实例分裂为波动（修复版回放 6/6 全过）；S-000114/246/247 为非时间字段波动（resolution/rtt/record 选择）。触发收窄后定向回放 S-000080/125/075/063/165/237 全过。

**v27.0 采纳定案（2026-09-20，终版 fix3 单跑 + 基线五跑稳定性核查，d6 新最佳）**：session **92.74% (217/234)** / turn **93.54% (463/495)** / 状态机 86.08%，vs v26.3-p3 基线 **+4.71pp/+3.64pp**（效应量为 ±2pp 噪声带的 2 倍+，用户指示单跑差距大不双跑）。time_wrong 19→5（−14 turn）。11 个机制性目标 case：8 个 session 转正（S-000063/119/146/154/165/185/227/237/249 等），S-000072/140 时间字段已修、session 败于 app 身份/record 选择等其他病灶，S-000066 duration=180min 为 GT 设计字段不可达（报数据侧）。fix2→fix3 迭代修复：profile_slot 触发条件收窄（限定"按平时+时段/时长"同现，S-000080/125 型误触发）+ 时长候选语境双收（S-000133 "选三个半小时还是三个小时"→dur_candidate 抑制时长断言不抑制 start/end）。回退 6 个经基线五跑稳定性核查确认无机制性回退：S-000246（基线 5 跑 4 败，run5 唯一侥幸对，T3-3 record 选择 t2 模型自发行为差异）、S-000114（基线 2/5 对，画质值波动）、S-000075/169/192/218（与基线各跑互有胜负，错误字段与时间机制无交叠）；T3-3 4→2 被 T3-2 6→9/T3-1 11→13/T1-4 10→13/X-1 9→11 覆盖。T1-1/T1-2 满分模板零变化。**采纳进 config_d6_qwen_plus_best.json（六时间开关全开）**。

**v27.0 fix2（2026-09-20）**：session **91.88% (215/234)** / turn **92.93% (460/495)** / 状态机 86.08%（与 fix_run1 91.88%/93.13% 几乎一致，两跑极差 0.2pp 内——v27 自身方差小）。获救 16/回退 7，time_wrong 6（vs fix_run1 的 3 微升，S-000133 型时长候选缝隙暴露）。回退再查：S-000133 为机制缝隙——**时长候选未检测**（"选三个半小时还是三个小时"只收到后一个，GT 口径 t1 时长悬空待定，提取器却断言了推导 end）；S-000075/246 两跑均翻（X-2 实例分裂/T3-3 record 错选，prompt 变动的边界 session 波动尾巴，与 v26.2b 同类）；S-000054/114/121/218 为单跑非时间波动。修复：时长候选语境（"还是/或者/选"邻接）双收→dur_candidate 信号（只抑制时长断言，不抑制 start/end 组装，不触发时段候选规则）；定向回放 S-000133/080/125 全过。→ fix3 结果见上段采纳定案（92.74%/93.54% 采纳）。


---

## v27.1 record 选择器：三轮实验否定回退 + 画质表补缺采纳（2026-09-23，v27.0 配置维持当前最佳）

**背景**：v27.0 定案后剩余错误分析（plan_record_selector_20260922.md）——record 相关 13 turn 分四病灶：A 画质表缺口/B T3-3 消歧错位/C 消歧轮抢跑（约束不唯一时 GT 悬空待消歧，模型提前取值；确定性可判）/D 补槽遗漏。方案 = record_selector 生成前证据注入（与 v27.0 时间机制同构），明确否决 v9.0 式后处理形态。

**实现**：新 `record_selector.py`（判定树：悬空 defer / 唯一定位 unique / 无约束最新 latest / 选择轮退出；d6 全量 81 例含记录指代话语 GT 行为核验 79/81 一致，2 例外为 T2-3 明说错误值轮设计口径）；features `record_selector` 开关；pipeline 触发域重构（selector 为 record_rule 上游，防双注入）；消歧会话信号 session 级传入（T3-3 模板或 agent Request_Disambiguation）；**画质表补缺**（全库 source_ref 还原 76 组合零冲突，prompt 表补 哔哩哔哩/视频 高清=720p、抖音/看直播 标清=540p 两条缺口，影响 8 test session）+ 消歧规则 4.5 条款（选定记录后必须补悬空槽位）+ 记录选择规则例外行。

**三轮全量否定记录**（每轮以错误归因驱动修改）：
- run1（EVIDENCE 全模式注入）：81.20%（−11.5pp），破 30——"命中N条"候选数暴露诱导模型自行按 service 过滤换记录；S-000005 型 summary 实证"取最近一条→但 service 不符→自行过滤"。
- run2（latest 措辞加固禁过滤）：88.46%（−4.3pp），救 3/破 13——定位类注入仍扰动 record_rule 已验证行为面（rtt=None/画质=None 型破窗）。
- run3（**defer-only**，定位类回归 record_rule 原文案）：89.32%（−3.4pp），救 5（S-000040/066/072/169/192，T3-3 家族机制性转正 ✓）/破 13（新增破窗多为 X-2 边界与非消歧会话，属已知波动带家族但合计面仍净负）。

**结论（按单跑确认原则否定）**：defer 机制在 T3-3 目标家族 5 例机制性转正（含消歧 4.5 补值链条端到端工作），但注入面持续扰动非目标类，三轮均在噪声带外净负——**record_selector 路线关闭（`record_selector.enabled` 默认关，代码留档）**；与 v25.0 反思路线同构结论：本数据集上"对抗模型取值直觉的注入"扰动面大于靶区。**画质表补缺独立采纳**（确定性数据修复，S-000114 型缺条目必救，无行为面扰动）——已在 prompt_builder 表中，v27.0 配置不变维持当前最佳。

**涉及文件**：record_selector.py（新，defer-only）/pipeline.py（触发域重构+session_disambig 参数）/session_eval.py（会话级消歧信号）/prompt_builder.py（画质表 +2、消歧 4.5 条款、记录规则例外行）/features.py（+record_selector）/selftest.py（+test_record_selector）/configs/config_d6_qwen_plus_rec.json（实验配置，不入最佳）/tools/replay_record_cases.py（新）/产物 eval_dual_v271_rec_run1/rec_run2/defer_run1。


---

## v27.2 候选模式采纳定案（2026-09-23~24，T3-3 查询-选择-落地链路，双跑均值 93.16%/94.85%，d6 新最佳）

**背景**：用户复盘 S-000040 确认三段失效链——t1 模型在两条候选(爱奇艺/拼多多)中自作主张"取最近"new 了拼多多 → 爱奇艺意图从未存在 → t2 选爱奇艺时只能在错误占位上补参数 → t3 被 pending-app 规则锁死输出 none。提出正确形态：**候选列举本身是模型输出的一部分**（后续"模型输出驱动客服回复"形态下，t1 不输出候选则下游无法生成消歧话术）。

**GT 分型核验**（13 个 T3-3 全量）：A 纯查询型 10 例（t1 只落时间字段，app 悬空）+ B 明说型 3 例（056/123/192，只悬空 rtt）。澄清用户疑点：客服话术确实只列部分候选（S-000192 列 3/画像 4），但 GT 选择基准=话术序（v21.1 实证 100%），列举缺口不直接致命。

**实现**：新 `candidate_book.py`（CandidateBook 旁路账本 + parse_candidates + select_phrase）；python_state candidate 段剥离（不入 JGA 主链）；pipeline `_session_is_t33` 信号域 + CANDIDATE MODE 双分支注入（列举轮/选择轮）；prompt_builder 消歧规则按"桩意图 app 是否为空"分支（空→记录 app/svc/res/rtt 全量落地；非空→只补悬空字段严禁改写身份）；session_eval 记账+豁免。config `candidate_mode` 开关。

**迭代过程**（T3-3 回放驱动，每轮按模型原话归因）：初版净负（修1破4）→ 信号域收窄至 T3-3（S-000116 T2-2 误伤确认）→ 1a/1b 分支（S-000192 B 型身份被改写确认）→ 1b 措辞加固（S-000123"身份不符必须改写"误读确认）→ 最终 T3-3 回放 修3破0。

**全量三跑定案（2026-09-24，run2 补跑 + run3 含 harness 分支修复）**：run1 **94.87/95.96/88.28**、run2 **91.45/93.74/87.36**、run3 **93.16/94.55/87.73**；**三跑均值 session 93.16% / turn 94.75% / 状态机 87.79%**，vs v27.0 单跑 92.74%/93.54% = **+0.42pp/+1.21pp**（turn 超带边缘、session 带内小正）。
**双跑差异大的归因（3.42pp）与修复**：run2 三个 T3-3（072/123/246）单向翻转——模型对消歧规则 1a/1b 分支判定二义（"桩 app 为空→落地"vs"已有明说 app→只补悬空"交模型自判，三例各走错一支）。修复 = **分支判定上移 harness 侧**：选择轮提示由 harness 检查桩意图 application_name 是否为空，直接注入确定性分支结论（空→"全量落地到桩 id"；非空→"严禁改写身份只补悬空"），模型只执行不再自判。修复后 T3-3 sweep 稳救家族扩至 040/056/246。
**稳对翻转分析**（三跑一致）：稳救 4（S-000040/056/174/192，T3-3 靶区稳定复现）；run1 独救 072/169/203/218/246、run2 独救 033/066/114、run3 独救 228；**稳破 3**（S-000151/164/211）。稳破归因：164/211 = "高清@淘宝/看直播=1080p"反常识映射表遵从不稳（v27.1 三跑也错，fix3 那次为孤例对，非 v27.2 引入，已列数据侧/表遵从问题）；151 = T2-3 改口轮 defer 悬空口径与"按之前那条记录"复合的边界 case（记录已唯一应取值）。**稳对净增 +1，维持采纳**。
**稳对翻转分析**（双跑一致）：稳救 5（S-000040/056/075/174/192——T3-3 靶区机制稳定复现，040/192 两轮均转正）；run1 独救 5（072/169/203/218/246）、run2 独救 3（033/066/114）；**稳破 4**（S-000149/151/164/211）。稳破归因：164/211 = "高清@淘宝/看直播=1080p"反常识映射表遵从不稳（v27.1 三跑也错，fix3 那次 1080p 是孤例对，非 v27.2 引入）；151 = T2-3 改口轮 defer 悬空口径与"按之前那条记录"复合的边界 case（记录已唯一应取值，模型留空）；149 = 补 app 轮时间字段错（独立波动）。**稳对净增 +1，靶区机制稳定复现，维持采纳**（config_d6_qwen_plus_best.json，candidate_mode+record_selector 开），151 型 defer/改口复合边界记为已知 case。

**附带修复（S-000012 型，双跑稳破残留归因时发现）**：无头段兜底条件 `len(prev_states)==1` 未考虑本轮 cancel——"cancel I2 + 无头补参段"场景剩余意图唯一但兜底失效，无头 delta 落入 `__nokey__` 丢弃（run2/run3 的 S-000012 即此，run1 模型恰好输出了 intent 头所以对）。修复 = 剩余数按"prev - 本轮 cancel"计算。非 candidate 相关，属 harness 存量边界 bug。**状态机侧同源症状自愈**：S-000012 t2 西瓜 GT=Ready PRED=Collecting 即丢 resolution 的状态机投影，修复后验证转 Ready ✓。另一处 t1 咪咕 GT=Collecting vs PRED=Ready 为 GT/pred 口径不对称（GT 侧"agent Request_Slot 整轮波及→Collecting"通道，pred 侧无 agent action 概念），属状态机已知口径弱项（Collecting 基线 46.1% 主因之一），是否对齐待拍板。

**画质表遵从修复（2026-09-24，v27.2.1 QUALITY HINT）**：用户指出标准画质表在 `resolution_quality_map.md`（数据侧单源）。核对：prompt 表与标准表 **diff=0**（75 条全一致），5 个失败 case 的错误不在表而在【查表遵从波动】——尤其"高清@淘宝/看直播=1080p"反常识条目（高清>标清720p），模型倾向落默认 720p。修复 = `schema.QUALITY_MAP`（79 条标准映射常量）+ QUALITY HINT 注入（话语提及 app/画质词时确定性查表高亮精确值；话语无 app 但 record_rule 定位了记录时用记录的 app/svc 查表，S-000114 型）+ features `quality_hint` 开关。**5 个失败 case 定点验证：S-000095/114/141/211/164 全部 PASS（5/5 修复）**。QUALITY HINT 与 v27.2 候选模式一并启用进 best config。

**涉及文件**：candidate_book.py（新）/python_state.py（candidate 剥离 + 无头段兜底剩余数修正）/pipeline.py（_session_is_t33+双分支注入）/prompt_builder.py（cand_mode_hint/cand_landing+消歧 1a/1b 分支）/session_eval.py（CandidateBook 记账）/features.py（+candidate_mode）/candidate_selftest.py（新）/tools/replay_candidate.py（新）/configs/config_d6_qwen_plus_cand.json。

---

## v27.3 候选组装修复 + 选择轮确定性锚点（2026-09-28，双跑 **95.09%/96.36% 采纳定案，d6 新最佳**）

**背景**：最新 errors html 分析发现 S-000089/140 型缺陷——用户指代的 key（应用名）与日期/"最近一条"约束**指向不同记录**时，数据口径要求两种约束命中的记录都列为候选（GT `evidence.candidate_record_ids` 恒为两条），而模型只落地一条，后续消歧无解。根因三层：①selector `_filter_records` 用 AND 串联约束，冲突时交集 0/1 条，candidate 注入门槛 `len(matched)>=2` 不满足 → CANDIDATE MODE 不触发；②"最近/上次"loose 信号从不参与候选组装；③t1 模型未列候选时 t2 无锚点，且 d6 新话术（"您选虎牙还是钉钉这条？"）不含"记录"字样 → `_DISAMB_AGENT_RE` 不触发 → 模型拿序数去数 records 列表（"第二条"误作 records[1]，v21.1 老病复发）。

**改动**（全部 harness 确定性侧，协议零改动）：
1. `record_selector.py` 消歧分支候选组装：AND 交集 ≥2 维持现状（S-000246 型一致多命中）；**交集 ≤1 时按"每约束各自命中的最近一条"取并集**，loose 信号并入 records[0]，按降序；defer reason 冲突优先（"约束指向不同记录"）。离线核验 13 个 T3-3：089/106/140/210 与 GT candidate_record_ids **全等（含顺序）**，其余 8 例逐条不变；228 为已知 R5-A 噪声（剔除名单，"取最近"口径下新输出才是对的）。
2. `render_hint` defer 文案候选兼容化（matched≥2 分支）："留空——不落地到保障意图（# candidate 候选段除外）"，消除与 CANDIDATE MODE"候选段赋 res/rtt"的表面冲突（曾致 089 只留空不列候选）。
3. 列举轮偏好条款：`PREFERENTIAL_RE` 提为模块常量共用，"最好是X那条"型话语追加"偏好≠最终选择，必须全部列出等确认"（S-000140 型直接落地违令）。
4. 选择轮确定性锚点链（pipeline）：
   - 候选明细注入："第N条=明细，禁止数 records 列表"。**顺序 = agent 话术序优先**（`_parse_disamb_candidates`，S-000157 实证 selector 降序与话术序可能相反，反序锚点会把模型带错），解析失败回退列举轮存档 `self._cand_listing`（session_eval 按 session 重置）。
   - `_parse_pick_ord` 选中序数确定性解析：动词紧邻优先（"选第二条"/"就第一条"）+ 前置否定排除（"不选第一条"）+ 唯一剩余兜底 + "最近一条"→completed_at 最新。**13/13 T3-3 选择话语离线全过**（含 106/228 否定在前、140 非序数型）。
   - 选中项明示注入（"系统解析： 用户选中 = 第N条 app/svc/res/rtt"）。
   - **占位意图改写许可**（1c 分支）：t1 违令把候选 app 抢跑落地时，选择轮不再锁死"严禁改写身份"（曾致 089 模型 cancel+new 钉钉并幻觉画像时段），而是明示"该意图是未选中候选的占位，允许改写为选中候选全量落地"。
   - `_time_keep` 明说时间锚点：从 `_id_states` 的 `timestamp` 子对象读桩/占位意图已落的用户明说时间，写入锚点"原样保留，禁止用记录时间覆盖，禁止从画像时段编造"。
   - 1b 悬空字段直给指令（S-000056 型回退修复）：B 型明说场景下选中记录全字段明细曾诱导模型"身份不匹配整条不可用"连悬空 rtt 拒补；现逐意图核验 rtt/resolution 悬空项直给 `state.rtt = "150ms"` 式补齐指令，并声明"选中记录只是字段来源，身份不一致属正常"。
5. `_parse_disamb_candidates` 两修复（d6 新话术变体）：段首纯属性残片切回上一段（"爱奇艺看直播、720p、50ms，抖音那条是…"的 50ms 是爱奇艺的 rtt，曾混入抖音段致虎牙同分抢占）；`^.*?条是` 剥离跨界吞掉本候选 app 时回补（"抖音那条是开直播"→"开直播"丢抖音，曾错配虎牙）；单 app 问句残段跳过（"您选虎牙还是钉钉这条？"）。

**验证**（qwen-plus，config_d6_qwen_plus_best）：
- 离线：selftest + candidate_selftest 全过；selector/序数/话术解析三组离线核验如上。
- 定点：S-000089 基线三跑全错 → 修复后累计 12 跑 10 对；S-000140 基线三跑全错 → 累计 11 跑 8 对。残余失败形态收敛为纯模型遵从波动（时间覆盖 ~25%、占位不改写偶发），确定性链路（候选组装/序数/选中解析/占位许可/时间锚点）已全部就绪。
- 23 session 回归（13 T3-3 + 10 对照，vs v27.2 run3）：净 +1（18/23 vs 17/23），对照组 10/10 零误伤；246 翻正为波动 case 巧合；056 曾 5/5 回退（1b 身份误读）经悬空直给指令修回 2/3+。
- **教训**：①序数语义 = GT/用户口径顺序，与 records 降序、agent 话术序三者可能两两不同（157 话术序对/106 话术自相矛盾 selector 序对）——锚点必须话术序优先 selector 兜底；②给模型的记录明细越全，越要防"身份比对"误读——B 型场景必须直给字段级补齐指令；③d6 话术变体持续超 d5 时代正则覆盖（"无记录字样消歧问句"、"X那条是"主体剥离），触发器/解析器要按新语料持续补。

**涉及文件**：record_selector.py（候选并集/defer 文案/reason 优先/PREFERENTIAL_RE 常量）、pipeline.py（_parse_pick_ord + 选择轮锚点链/占位 1c/时间锚点/1b 悬空直给 + _parse_disamb_candidates 两修复 + 列举轮偏好条款）、session_eval.py（_cand_listing 按 session 重置）、tools/_replay_v273.py / _replay_089_140.py / _replay_056.py（新，回放工具）。

**全量双跑定案（2026-09-28，d6 剔除 3 噪声 234 参评，含 v27.2.1 画质表）**：run1 **95.30%/96.97%/91.03%**、run2 **94.87%/95.76%/89.56%**，**均值 session 95.09% / turn 96.36% / 状态机 90.29%**，vs v27.2 三跑均值 93.16%/94.75%/87.79% = **+1.93pp / +1.61pp / +2.50pp**，双跑极差 0.43pp（高度稳定）。**稳救 4 / 稳破 0**：S-000089（目标 case，key vs 日期冲突候选）、S-000151（v27.2 稳破，T2-3 改口轮 defer 边界自愈）、S-000164/211（v27.2 稳破，画质表遵从波动转正）。T3-3 家族：089 双跑稳对（基线三跑全错）、246 双跑稳对（基线 T..）、140 T.（唯一残余=run2 start 时间覆盖波动）、072 TT；056/174/192 各单跑一次字段级波动（resolution 覆盖明说/start 覆盖），123 run2 多建咪咕快游意图（消歧选择波动）。错误结构：失败 11/12 个，field_wrong 6/7 个（时间覆盖与画质覆盖为主）、意图集合 5 个（波动型）。产物 eval_dual_v273_cand_run{1,2}(.jsonl/_summary/_errors.html)。**采纳进当前最佳（config_d6_qwen_plus_best.json 不变，代码即 v27.3）**。

### v27.3.1 候选列举轮时间桩兜底（2026-09-28，v27.3 追加）

**背景**：双跑后复查 S-140 run2 失败根因——非此前认为的"时间覆盖"，而是 t1 模型**只输出 `# candidate:` 段、漏掉承载明说时间的意图段**（summary 里正确算出 start=7/10 01:00 但没落意图段；candidate 段旁路剥离不入 JGA → 判分 None）。

**实现**（pipeline run_multi，execute 之后段处理之前）：列举轮 + `_time_ev` 明说时间完整（start/end 齐、无矛盾、无候选时段）+ 零意图段（all_empty，candidate 剥离后）+ **话语无新保障目标 app** → 用 TIME EVIDENCE 预解析值补建 `("new", {timestamp: {...}})` 段。四重收窄；B 型明说排除的判定用 `_extract_filters` 区分"新保障目标 app"与"记录筛选 app"（"最好是抖音那条"的抖音是筛选词不算，"用YY开直播"的 YY 才算——防选择轮 1a 全量落地劫持明说身份）。同轮抑制 record coreference 兜底（`_listed_this_turn` 标记，防 records[0] 抢跑落地——S-000140 run2 中该兜底曾把爱奇艺全字段落地，恰为 GT 目标才未炸）。`_build_time_hint` 返回值改 `(hint, ev)` 供兜底复用与模型所见同一预解析值。

**性质**：同 prev_state 兜底/validator 知二推一——harness 代执行模型已认可的机械落地（值在 TIME EVIDENCE 注入且模型 summary 已复述），时间提取规则化闭环；非 v9.0 封死的后处理选值（不覆盖任何模型已输出字段）。影响口径：明说时间机械落地功劳移至 harness，报告表述"时间提取完全规则化"。

**验证**：monkeypatch 模拟 candidate-only 输出——A 型（140）正确补桩（start/end/duration 齐）、B 型（056）正确排除；selftest + candidate_selftest 全过；23 session 回归两跑 22/23、20/23（差集 056/192 为已知画质遵从波动，B 型已排除与兜底无关；140/089/246 稳救保持）。

**涉及文件**：pipeline.py（时间桩兜底 + record 兜底列举轮抑制 + _build_time_hint 签名）、configs/config_d6_qwen38_27b_v273.json（新，qwen38 验证用）。

### v27.3.2 1b 悬空字段判定修复：嵌套槽位格式误读（2026-09-28，qwen38 全量暴露）

**背景**：v27.3 qwen38 双跑 S-000056 双跑全错（resolution 720p→360p）。t2 prompt dump 发现 1b 悬空字段补齐指令注入了**错误指令**——"resolution 悬空 → 补 360p"，但 t1 明明已落明说 720p；qwen38 指令遵从更强照抄错值双跑全错，plus 概率遵从（v27.3 plus run2 056/192 错同源）。

**根因**：`_fv` 只解 `{"value": ...}` 格式，而 resolution/rtt 是 `{"min_value"/"max_value": {"value": ...}}` 嵌套格式 → 已落值误判悬空 → 记录值覆盖明说值。**此前归因为"画质遵从波动"的 056/192 失败实为机制性致错**（归因纠正）。

**修复**：`_fv` 解析 `min_value`/`max_value` 子对象。定点 qwen38 056/192 修复前后 0/4 → 4/4。

**影响**：v27.3 plus 双跑 95.09%/96.36% 中 056(run2)/192(run2) 两 session 错误为机制性 → 修复后 plus 真实水平应更高；qwen38 同理。**v27.3 双跑数字含此 bug 不再作为定案，v27.3.2 重跑四跑（q38×2 + plus×2）定案。**

**涉及文件**：pipeline.py（_fv 嵌套解析）、tools/_replay_056_192.py（新）。

### v27.3.2 全量四跑定案（2026-09-28，plus×2 + qwen38×2，修复后代码，d6 新最佳）

**口径**：d6 剔 3 噪声 234 参评，mi_on，config_d6_qwen_plus_best.json（plus）/config_d6_qwen38_27b_v273.json（q38，同开关仅换 llm.model）。

| 跑 | session | turn | 状态机 |
|---|---|---|---|
| plus run1 | 97.44% (228/234) | 97.98% (485/495) | 91.58% |
| plus run2 | **98.29% (230/234)** | 97.78% (485/495) | 91.21% |
| **plus 均值** | **97.86%** | **97.88%** | **91.39%** |
| q38 run1 | 97.44% (228/234) | 97.98% (485/495) | 91.58% |
| q38 run2 | 96.15% (225/234) | 97.17% (481/495) | 90.84% |
| **q38 均值** | **96.79%** | **97.58%** | **91.21%** |

vs v27.2 三跑基线 93.16%/94.75%/87.79% = **plus +4.70pp/+3.13pp/+3.60pp；q38 +3.63pp/+2.83pp/+3.42pp**；vs v27.3（含 _fv bug）95.09%/96.36% = 修复收益 plus +2.77pp/+1.52pp。**当前最佳 = plus 均值 97.86%/97.88%（config_d6_qwen_plus_best.json）；qwen3.8-27b 二次坐实平替（差 1.07pp/0.30pp，均创各自历史新高）**。

**T3-3 家族（13 例参评 10）**：q38 双跑 20/20 全对；plus 19/20（run2 满分）。089/140/157/174/246 全稳——v27.3 主线目标（冲突约束候选列举+选择轮锚点）完全达成。

**稳错分析**：plus 双跑稳错 3（059=X-2 单轮双意图串扰恒选抖音、203=T2-2 澄清轮意图分裂两跑各错一形、218=T1-3 时间明说形态）；q38 稳错 6（059/066/130/151/203/227，同型字段级）。**plus 稳错 3 个全部非候选链路（X-2/T2-2/T1-3），候选模式管线自身零稳错**。

**qwen3.8-27b 全量验证结论（用户要求）**：①零补丁需求——v27.3.2 唯一代码修复（_fv 嵌套解析）为 plus/q38 共享 harness bug，非 q38 特有适配；弱模型配套（line_tolerant 默认开 + "只写变化字段"prompt 约束）在 v26.2c 已就位且本次沿用；②q38 与 plus 差距收窄到 1.07pp（session）/0.30pp（turn），"指令特化中型模型+好 harness"平替结论第三次复现（v26.2b 后）；③q38 稳错多 3 个（066/130/151/227 vs plus 仅 218 同型）且全为字段级遵从波动，无集合级/机制性差异。

**归因纠正（重要）**：v27.3 记录的 plus"画质遵从波动"（056/192）实为 _fv bug 机制性致错；q38 更强遵从使 bug 全量显形（056 双跑全错）——弱模型暴露 harness bug 是"harness/模型共同进化"的又一例（同 v25.2 教训：模型行为异常先查 harness 结构）。

**产物**：eval_dual_v2731_{plus,qwen38}_run{1,2}(.jsonl/_summary/_errors.html)；config_d6_qwen38_27b_v273.json（新）。

### v27.4 候选模块 LLM 兜底（2026-10-12，方案 B 用户定案——测试集永不进训练）

**背景**：dataset_930 泛化测试实证规则选择器的泛化脆弱——话术形态变体（"候选N"指称、"就按我这次说的"复合尾句、svc+日期逗号切开）持续穿透正则解析器，每换一代数据集就要补一轮。用户判定：选择环节泛化性差，candidate 模块改为非规则化。

**实现**（新 `candidate_llm.py`，规则优先 + LLM 兜底分层）：
- `pick_index_llm`：规则 `_parse_pick_ord` 失败时升级 LLM 结构化抽取——输入=用户话语+候选明细表（app/svc/res/rtt/completed_at），输出=选中序号（1-based)。LLM 只做信息抽取不做状态写，落地仍走确定性锚点链（字段值从 records 查表）。
- `parse_listing_llm`：话术解析 `_parse_disamb_candidates` 失败时升级 LLM 提取话术提及顺序（输出每候选 app/svc/completed_at，本模块匹配回 records)。专治"svc+日期逗号切开"等话术变体。
- 约束：失败安全回退（LLM 非法输出/超时 → None 退回现状）；规则快路径零成本；LLM 输入仅当前话语+records 明细，测试集永不进训练/检索。

**与封死路线的边界**：v9.0 封死"validate 后改写答案"、v25.0 封死"让模型重试自己输出"；本方案是**生成前信息抽取**（与 time_extractor 同构），LLM 不产字段值、不改状态，只把"选哪条"这个分类判断从正则手里拿回来。

**同期 d930 配套修复（v27.3.3-d930）**：①id 协议沙箱 prev 别名视图（段头 I1 vs 内容键失配致 state.xxx 自引用崩，T2-2 63.8%→72.7% 实证）；②Ellipsis 字面省略号崩 json（`_to_nested_param`/`_scalar` 双层清洗）；③`select_phrase` 补"候选N"指称（9/9 用例）+ 话术解析 svc+日期段合并（覆盖 110→140/258)。**教训：弱模型全量是 harness bug 的低成本扫描器——v26.2c"过度沿用"与本轮 id 沙箱断裂同为强模型掩盖的防御缺口**。

**验证中**：d930 444 分层子集 qwen-plus 双跑（v274_d930_s444_plus）。**待办**：跑完定案数字 + 与 q38 v2731/v2732 对比 LLM 兜底净收益。

### v27.4 d930 444 子集 qwen-plus 首跑 + 列举顺序缺陷待修（2026-10-12）

**结果**：session 91.67% / turn 93.96% / 状态机 89.86%（vs q38 v2731 92.34%/93.24%、v2732 91.67%/93.04%）。T3-3 13/26（候选N 选择语修复后仍有稳错）。

**新暴露的缺陷（gena-S-001130/1840 型）**：模型 t1 的 candidate 列举顺序（records 降序）与 agent 话术序（GT/用户口径序）不一致，模型拿自己的列举顺序理解"候选N"→ 落错记录。harness 的 disamb_cands（话术序）是对的，但**模型 t1 列举动作的自由顺序是污染源**。

**待修方向**：列举轮把 harness 的候选明细顺序（约束命中的话术序）直接写进 CANDIDATE MODE 指令的明细文本（"按以下顺序列出：第1条=X/第2条=Y"），模型的列举动作从"自由列举"变成"按给定顺序复述"——把顺序的控制权从模型收回 harness。或更彻底：列举轮不要求模型输出候选段，harness 直接用候选明细驱动下游（模型输出只留意图段）。待拍板。

### v27.6 选择轮判定 LLM 化（2026-10-13，用户定案——不依赖话术正则触发）

**背景**：v27.4 方案 B 后仍有两类话术正则断链——`select_phrase` 漏"候选N"指称（d930 主形态）、`_DISAMB_USER_RE` 漏"我选候选1这条记录"（1840 型）。用户判定：选择轮判定不应靠话术正则触发，未来必有没覆盖的话术，需要模型判断。

**实现**：
- `candidate_llm.judge_selection_turn(llm, utterance, listing)`：一次 LLM 调用同时判定"是否选择轮"+"选中第几条"（输出 JSON {"is_selection", "pick"})，语义覆盖序数/指代/否定枚举/"按最近那条"等全部形态，无需正则穷举。
- pipeline 选择轮触发改 harness 状态机：`_cand_listing` 非空（t1 列举发生过）→ 本轮无条件调 judge；非选择 → 清空 `_cand_listing` 退出候选模式；是选择 → 走选择轮锚点链。`_parse_pick_ord` 正则退化为兜底（judge pick=None 时），"最近一条"语义规则最后。
- 话术解析 `_parse_disamb_candidates` 的 LLM 兜底（v27.4 方案 B）保留：正则解析失败时 `parse_listing_llm` 按话术提及顺序提取候选，消除"svc+日期逗号切开"等变体的解析盲区。

**冒烟**：6 形态全对（含"就按最近一条"按 completed_at 最大定位——prompt 显式说明"最近=完成时间最晚"）。

**444 子集 qwen-plus 结果**：v276 91.22%/92.94%/89.58%（vs v274 91.67%/93.96%）。**T3-3 翻正 1130/2299（顺序污染源与话术穿透修复实证）、翻错 764/1789（LLM 判断引入新方差）**。剩余 T3-3 稳错归因（v276 13/26）：

| 形态 | 例 | 归因 |
|---|---|---|
| 话术解析 None + LLM 兜底未命中 | 1840 | "候选1是淘宝主播6月19日…候选2是钉钉7月7日"话术无 svc 名，正则与 LLM 均未映射回 records |
| 记录字段悬空残留 | 1840 | 模型落地 app/svc 后 rtt 仍 None（选中记录钉钉 150ms 未补）——1a 分支"全量落地"与"悬空字段补齐"指令在 B 型边界（已有明说意图）冲突 |
| 判断方差 | 764/1789 | LLM judge 把非选择轮误判为选择轮（v274 时这两个 session 是对的） |

**评估**：LLM 化方向对（覆盖长尾话术），但 plus 444 子集未现净收益——残留失败从"正则穿透"转为"LLM 判断方差"，两者量级相当。判断器本身还需收紧 prompt（明确"非选择"的判定标准）或在 prompt 里给反例。**建议：不急于采纳，先做判断器 prompt 迭代（冒烟 6 形态全对≠全量稳定），或把 judge 作为正则与 LLM 双通道投票（一致才采纳）。**

**待办**：判断器 prompt 加固（给"非选择"反例）→ 再跑 444 子集对照；d930 全量验证待口径稳定后。

### v27.6.1 待选择状态闭环（2026-10-13，v27.6 生命周期补丁）

**背景**：v276 归因时发现 764/1789 翻错真根因——**不是判断器误判，是 `_cand_listing` 生命周期没闭环**：t2 选择落地后待选择状态未清除，t3 确认轮"确认，就按这个来"再次触发 judge 被判为选择轮 → 模型把已正确落地的意图改写成另一条候选。旧版正则 `select_phrase` 不会把纯确认语当选择（"确认，就按这个"命中 _CAND_CONFIRM_RE 排除表），LLM judge 无此排除表 → 引入新方差。

**修复**：选择轮判定为选择后立即 `self._cand_listing = None`（待选择状态闭环）。t3 `_cand_listing` 空 → 不触发 judge → 正常 confirm。

**结果（444 子集 qwen-plus）**：91.22%/93.45%/89.77%（turn 较 v276 +0.51pp）。**T3-3 14/26（v276 13→14）**：764/1789 双双修复 ✓、1130/2299 翻正保留 ✓，翻正 524/764/1789 翻错 2197/943。**vs v274 规则版：翻正 8 / 翻错 10（X-2×4 波动为主，T3-3 净 −2）**。

**残余 T3-3 失败形态（12 个）**：字段错 10（时间污染 943 型 + rtt 补齐边界）+ 多出 2。最典型 gena-S-002197：**1b 分支"严禁改写身份"与"用户选择候选1=虎牙"冲突**——I1 已被 t1 抢跑落成咪咕快游（候选2），用户选候选1 时 1b 锁死身份致落地失败。这正是 1c 占位改写分支应覆盖的场景，但 2197 的 t1 落地发生在"明说意图"语境（非候选），harness 未识别为占位。

**评估**：LLM 判断路线（v27.6+6.1）与规则版（v27.4）在 444 子集基本打平（91.22 vs 91.67 session，T3-3 14 vs 13）。LLM 化已修复话术穿透（候选N 全形态）与确认轮误判（生命周期），剩余失败是**分支判定语义边界**（1b vs 1c 的占位识别）与字段级波动——这些不是选择判定问题，是落地分支逻辑问题。**方向维持，下一步攻 1b/1c 边界判定（harness 侧已有 _placeholders 检测，需扩展到"明说意图恰为候选之一"场景）。**

### v27.7/v27.7.1 路线乙:列举轮身份字段强制剥离（2026-10-13，1c 退役）

**设计**（用户定案"使用路线乙"）：抢跑不从"指望模型遵从"解决，而从"harness 层不可表达"解决——列举轮执行后剥离段内身份字段（app/svc/res/rtt），条件=该段 app∈本轮候选列表且**不是用户本轮明说的新保障目标**。占位意图从此无法产生，选择轮只剩 1a/1b，1c 分支退役。

**v27.7 首跑暴露剥离条件过宽（genb-S-001707 型）**：宽指代候选列表（"最近办过的记录"并集 9 条）会包含话语明说的目标 app（芒果TV∈候选且∈话语）→ 无差别剥离误杀真明说意图 → 意图从 t1 消失。**v27.7.1 修正**：剥离加明说豁免——app 出现在话语中且不是记录筛选词（与 pending_app_rule 同口径）则不剥。定点双验证：1707（明说芒果TV 保留 ✓）、2197（抢跑咪咕快游剥离 ✓）。

**444 子集 qwen-plus 结果**：
| 版本 | session | turn | T3-3 |
|---|---|---|---|
| v2761（1c 时代） | 91.22% | 93.45% | 14/26 |
| v277（剥离无豁免） | 90.99% | 92.94% | 13/26 |
| **v2771（剥离+明说豁免）** | 90.32% | 92.73% | 14/26 |

2197 型翻正实证（1b 死锁消除）；524 型翻错（scope_fields=['rtt.max_value'] 的"只补时延"场景，1a 全量落地指令与 GT 只取单字段口径冲突——GT 口径按 scope_fields 决定落地范围，1a 指令未区分）。

**评估与结论**：路线乙结构性目标达成（1c 退役、抢跑不可表达），但 444 子集总分未超 v2761（90.32 vs 91.22）。**失败结构已从"选择判定"完全转移到"落地口径"**：GT 的 scope_fields（候选记录供哪些字段）与 1a"全量落地"指令的粒度不匹配（1707 只取时延/524 只取时延/943 时间污染），这是下一个明确靶区——把 scope 语义引入选择轮分支（scope_fields 只有 rtt → 1a' 只补该字段;全字段 → 1a 全量）。

**判断（待用户拍板）**：①继续攻 scope 口径（v27.8）后 444 复验；②LLM 判断路线（v2761）与规则回归版（v2771）数字接近（91.22 vs 90.32），需定案采用哪个作为 d930 全量验证口径。

### d930 噪声扫描（2026-10-13，find_noise R1-R5 全量适配）

**背景**：444 子集与 d6 口径对齐问题——d6 97.86% 是剔 3 噪声后的 234 参评，d930 444 子集此前零剔除（默认加载的是 d5 清单，与 gena-/genb- 前缀零交集）。跑 d930 自己的 find_noise。

**适配**：find_noise.py（tools/_archive)适配 d930——split 只有 test_sessions（全测试集无 train），画质映射表改从 d930 全量 source_ref 还原。脚本 tools/_find_noise_d930.py。

**结果**：4455 全扫，噪声 **91 个（2.04%）**：R5-C（过滤后应取最近，GT 选了更早）60、R5-A（用户按指代选择与 GT 记录错位）18、R2（明说时长矛盾）9、R3（明说时段矛盾）3、R4（画质词矛盾）1。清单 `data/_noise_exclude_d930_scan.json`。

**对 444 子集影响**：命中 8 个（v2771 中 5 对 3 错），**剔除后口径 91.22% → 90.83%**（396/436）。d930 的噪声密度（2.04%）高于 d6（1.3%），且 R5 系占 86%——渲染器对"取最近"规则的偏离比 d6 更频繁。

**口径记录**：此后 d930 评估默认剔除该清单（session_eval --noise_file data/_noise_exclude_d930_scan.json）;此前各 444 子集数字（v2731~v2771）均为零剔除口径，跨版本可比但与 d6 剔噪口径不同权，对比时注明。

### v28 scope 语义判别（2026-10-13，实施定案：scope 字段 LLM 判断，444 plus 验证转正）

**背景**：v2771 的 T3-3 失败分类实证——"选对候选但落地范围错"占 7/12（58%),根因是 1a"桩空=全量落地"硬规则无法表达"引用语义"（用户只要借一个字段，如 524"时延就按候选2"GT 只记 rtt 一项）。**业界调研**（arXiv:2605.25204 分阶段澄清 + 受限枚举；Conformal Cascade 触发；Qwen json_object/schema 支持已核实）支持：判定+范围合一调用安全，条件是全部受限枚举。

**实现**：
- `candidate_llm.judge_selection_turn` 升级：一次调用三枚举 `{"action": select|confirm|new_request|chitchat, "pick": N|null, "scope": [app/svc/res/rtt]|null}`;prompt 含 scope 判断依据（动词/限定词规则）；校验失败安全回退。
- pipeline 选择轮分支由 scope 驱动：全四字段 → 原 1a 全量落地；子集（如 ["rtt"]）→ "只允许写该字段,严禁把选中记录当新保障办理"。明说时间锚点不变。
- 冒烟 10/10（含 524 原话语 scope=["rtt"] ✓、第三条记录 pick=3 ✓）;d6 回归 22/23 零回退。

**结果（444 子集 qwen-plus,剔噪口径 436 参评）**：**92.43% / 94.26% / 状态机 90.62%**——d930 444 系列最高（vs v2771 剔噪 90.83%/92.73% = **+1.60pp/+1.53pp**;vs 零剔除 v2761 91.22%）。T3-3 15/25（62%,v2771 14/25):**524 翻正（引用语义修复实证）,零翻错**。全模板 12 升 4 平（T2-6 +8pp、T2-2 +4pp、T3-4 +5pp),T1-3 −5pp 单跑波动。

**残余 T3-3 10 失败**：字段错 7（1840/1959/977/1351 rtt 未补——**RECORD RULE"悬空待消歧"与 scope 落地指令仍打架**：模型 t2 落了身份但 rtt 被 RECORD RULE 压住;943/1435 时间污染）+ 选择错 3（2129/1045/1300,判 pick 或顺序残余）。**1840 暴露的规则打架是下一个明确靶区**：scope=["rtt"] 时应显式压制 RECORD RULE 悬空条款（"选中记录的字段经用户确认,不再是悬空指代"）。

**涉及**：candidate_llm.py（judge 三枚举）、pipeline.py（scope 驱动分支）、llm_client.py（prompt 留存）。

### v28.1 悬空条款压制 + selector 双 app break 修正（2026-10-16）

**修复 1（scope 引用语义压制悬空条款）**：v28 残余的 1840 型——scope 判定后模型仍被 RECORD RULE"悬空待消歧"压住（rtt 不落地）。修复 = scope 子集分支追加三条指令：【记录指代已消解】用户明确选定候选 → 悬空条款失效，scope 字段必须照常落地；严禁新建意图承接选中记录。

**修复 2（selector 双 app break 位置 bug,顺带挖出）**：1840 深挖时发现 `_extract_filters` 的 app 循环 `break` 放在 `if name in utterance` 内——双 app 话语（"办芒果TV…查淘宝主播…的记录"）第一个 app（新保障目标,无紧邻记录词）就退出循环,真正的记录筛选 app（淘宝主播）永远测不到 → 候选并集缺约束。修正：break 移入紧邻命中分支 + app 紧邻窗 6→14（对齐 svc 窗,"淘宝主播7月7号完成的记录"间隔 7 字）。1840 listing 恢复 [钉钉, 淘宝主播] 双候选。

**结果（444 子集 plus,剔噪 436）**：**92.20% / 94.05% / 状态机 90.25%**（vs v28 92.43%/94.26% —— session −0.23pp turn −0.21pp,波动画内）。T3-3 **16/25**（v28 15→16,943 翻正零翻错）;1840 仍错（t2 模型遵从压制条款但仍走 new——R5-A 噪声候选错位叠加,单点不追）。全局翻正 7/翻错 8（T2-2×3/T2-6×2 波动）。

**判断**：scope 链路双修复后 T3-3 累计 12→16/25（v2771→v281）;session 级与 v28 持平（±0.2pp 波动画）。**候选链路迭代收敛**——残余失败为模型遵从波动与 X-2 串扰,无新的结构性缺陷。**进入 qwen3.8-27b 单跑验证**（用户流程:plus 好→q38）。

### v28.1 qwen3.8-27b 平替验证 + d930 泛化性定案（2026-10-16）

**结果（444 子集剔噪 436 参评，v28.1 同代码）**：**q38 92.66%/94.15%/状态机 90.81%** vs **plus 92.20%/94.05%/90.25%**——**q38 反超 plus +0.46pp/+0.10pp**（d6 时代 q38 落后 1.07pp,d930 上差距反转归零）。T3-3 q38 17/25 vs plus 16/25（候选链路在 q38 上更强）。

**分模板**：q38 强在 T2-6/T3-1/T3-3（100%/100%/68%）,plus 强在 T1-3/T3-4/T2-5（100%/100%/90%）;差异全在 ±1-2 session 波动画,无结构性鸿沟。

**d930 泛化性定案（444 分层子集剔噪口径）**：**session 92.66% / turn 94.15% / 状态机 90.81%（qwen3.8-27b,v28.1）**。从 d930 首跑有效段 92.89%（q38 旧代码）到 v28.1 的 92.66%（444 抽样剔噪）：候选链路六轮迭代（v27.3→v28.1)把 T3-3 从 55.6% 拉到 68%,同时修掉三个 harness 潜伏 bug（id 沙箱断裂/Ellipsis 崩溃/scope 硬编码）。**平替结论三次复现**（v26.2c d5、v28.1 d6、本次 d930）："中型指令特化模型 + 完整 harness = 闭源大模型平替"跨数据集成立。

**产物**：eval_dual_v281_d930_s444_{plus,q38}(.jsonl/_summary/_errors.html);prompt 审计 tools/_replay_prompts.py + v2771_prompt_audit.html。

### d930 第二独立子集 s444b 交叉验证（2026-10-16，泛化性定案收敛）

**抽样**：s444b = 与 s444 完全互斥的第二 444 分层子集（gena/genb×16 模板双维，seed7），固化 `split_d930_s444b.json`；剔噪后 435 参评。

**结果（q38 v28.1 同代码）**：**92.41% / 93.42% / 状态机 89.45%**——与 s444 的 92.66%/94.15%/90.81% 收敛（session 差 0.25pp,turn 差 0.73pp,均在子集方差内）。**T3-3 双子集一致：68% vs 67%**——候选链路修复效应跨子集稳定复现。

**模板级**：s444b 强在 T2-5/X-1/T2-2（95/100/72%）,s444 强在 T2-6/T2-4（100/98%）——互补分布，无系统性模板偏斜。

**合并 888 抽样总口径**：**session 92.54%（806/871）/ turn 93.79%（1768/1885）**——d930 泛化性最终定案数字（v28.1,q38,剔噪，~20% 分层抽样）。

**结论**：①92.5%±0.3pp 是 v28.1 在 d930 上的真实泛化水平（双子集交叉验证）；②d6（97.86%）→ d930（92.54%）差 5.3pp,为新一代数据集的话术漂移+噪声密度（2.04% vs 1.3%）+渲染器代差共同作用，泛化保持合格；③T3-3 68% 仍是最大模板洼地（残余=模型遵从波动+R5 系 GT 噪声），已无限接近无结构缺陷上限。

### d930 双子集全量错误逐例分析报告（2026-10-19，65 例归因）

**范围**：v28.1 双子集（s444 32 失败 + s444b 33 失败）全部失败 session 逐例核对对话/差异字段/模型原始输出，每例标注根因。产物 `docs/report_errors_d930_v281.md` + 取证底稿 `_d930_failures_v281.json`。

**分类（九类）**：T2 澄清轮明说字段未透传 16（25%,**最大单一机制类**——澄清/补参轮只补新字段，U1 明说的时间/画质跨轮丢失，普适缺口）、T1 时间解析 11（17%,长尾：凌晨12点歧义/跨午夜/相邻串扰）、T5 多意图串扰 9（14%,同 key 多实例漏建/cancel 误伤）、T8 scope 字段落地缺失 8（12%,**选对落错值——judge 与模型执行间缺"字段来源绑定"**，模型抄值不经 harness 查表）、T4 画质查表 7（11%,特殊条目落默认）、T6 候选选择错 5（8%,字段来源错位）、T3 改口丢字段 4、T7 scope 遵从 2、T9 GT 噪声 3（坐实数据侧）。

**结构结论**：①候选链路（T6+T7+T8=23%）已不是最大类；②**下一靶区 = T2 跨轮透传（25%）**——修复路径明确（澄清轮 prompt 显式列"已明说字段清单"，_time_keep 泛化）；③T8 的字段来源错位指向 scope 落地的终极形态：**字段值由 harness 从选中记录查表直填**（judge scope+pick 已知，字段值无需模型抄写），可同时消灭 T6/T8。

### v28.2 未定词槽位限定修正 + selector 双 app break/app 紧邻窗修正（2026-10-20，d930 双子集双升定案）

**修复 1（time_extractor `_generic_undecided` 槽位限定判定,用户定案方向）**：d930 65 例错误报告 T2 类 16 例（25%,最大类）的根因——"应用和时延还没定"/"时长我还没定"/"时延上限30ms，我还没定"等**未定词限定的是非时间槽位**，旧 `_UNDECIDED_SLOT_SCOPED_RE`（只认"(开始|结束|截止)时间"前缀）全部误判为全局未定 → 已明说完整的时间被整体抑制（组装清空+TIME RULE"一律留空"），t2 澄清后明说时间永久丢失。修正三层：①槽位关键词扩展（应用/时延/rtt/时长/画质/分辨率/清晰度/走/选）+ 紧邻(≤6字)与隔值(6~16字,关键词与未定词间无标点)双层判定；②后向判定（"…我还没定，是1080p配时延…"未定对象在未定词之后且带"是…配"选项列举形态,1684 型）；③app 名命中（"是云视讯…还是Telegram…还没定",1276 型）。**S-000185 真候选时段（"画质要流畅，时间还没定"——画质已定、时段两候选）经隔值标点校验保持抑制**。

**修复 2（record_selector 双 app break 修正,承接 v28.1）**：`_extract_filters` app 循环 break 移入紧邻命中分支（v28.1 已修）+ **app 紧邻窗 6→14**（"淘宝主播7月7号完成的记录"间隔 7 字,1840 型）+ **svc 命中前剥离已命中 app 名**（"网易云游戏7月4号完成的记录"的"云游戏"是 app 子串,误判 svc 约束致并集混入咪咕快游,524 型字段来源错位的根源）。524 listing 恢复与 GT 全等 [网易云100, 钉钉150]。

**验证**：12 形态离线用例 12/12（9 正例恢复组装+3 负例保持抑制含 S-000185）；selftest/candidate_selftest 全过；d6 回归 22/23 零回退。

**结果（444×2 双子集,q38,剔噪）**：
| 子集 | v28.1 | v28.2 | Δ |
|---|---|---|---|
| s444 | 92.66%/94.15% | **93.81%/94.78%** | +1.15pp/+0.63pp |
| s444b | 92.41%/93.42% | **92.87%/93.64%** | +0.46pp/+0.22pp |
| **合并 886 抽样** | 92.54%/93.79% | **93.34%/94.22%** | **+0.80pp/+0.43pp** |

T3-3 17/25 保持（524 翻正——scope 错位修复实证；1300 单点翻错波动）；全局翻正 7/翻错 2。**d930 泛化性定案更新：93.34%/94.22%（合并 886 抽样,q38,剔噪,v28.2）**——较 v28.1 再 +0.80pp,未定词槽位判定是透传缺口的正确修法（用户预判"透传问题明显"得到验证）。

**产物**：eval_dual_v282_d930_s444{,_b}_q38(.jsonl/_summary/_errors.html)。

### v28.3 TIME RULE 文案随组装结果分叉（2026-10-20，透传缺口收口）

**背景**：v28.2 修复 time_extractor 槽位限定误判后，TIME EVIDENCE 组装成功（start/end 有值），但 pipeline ③d 候选时段 TIME RULE 仍是旧文案"start/end 一律留空"——把已组装的明说时间又压掉了（533/1732 型：模型 summary 原话"受TIME RULE约束，start/end留空"）。**extractor 修了，prompt 文案没跟上**——T2 类残余 8 例的直接原因。

**修复**：TIME RULE 按组装结果分叉——`ev.start/end` 有值 → 新文案"时间本体已明说且无歧义（见组装结论），照常落地；未定的只是其他槽位（应用/时延），不要因其他槽位未定而把时间一并留空"；组装被抑制（时间本身二选一）→ 保留原"一律留空"文案（S-000185 真候选时段语义不变）。

**结果（s444 q38,剔噪 436）**：**94.72% / 95.30% / 状态机 91.29%**——vs v282 93.81%/94.78% = **+0.91pp/+0.52pp**。T2 类 16→13→**残余 3**（533/1732/1850/2224/1276 五例修复实证）；全局翻正 7（T2-2×5）翻错 3（T2-2×2/T1-3×1 波动）；T3-3 17/25 保持。

**透传缺口状态**：三层缺口（extractor 误判→TIME RULE 文案压制→跨轮唤醒）全部关闭，T2 类从 16 例压到 3 例（余为改参轮错值/黑话形态）。**d930 s444 定案数字更新：94.72%/95.30%（v28.3 q38 剔噪）**——s444b 复验与合并口径待跑。

### v28.3 s444b 交叉验证 + d930 泛化性定案更新（2026-10-20）

**s444b（剔噪 435 参评）**：**94.48% / 94.82% / 状态机 90.45%**——与 s444 的 94.72%/95.30% 收敛（差 0.24pp/0.48pp）,TIME RULE 组装分叉效应跨子集复现。

**d930 泛化性定案更新（合并 871 抽样,q38,v28.3,剔噪）**：**session 94.60% / turn 95.07%**（v28.2 93.34% → +1.26pp;较 v28.1 累计 +2.06pp）。透传三层缺口（extractor 未定词误判→TIME RULE 文案压制→跨轮唤醒缺失）全部关闭,T2 类 16→3。d6 97.86% → d930 94.60%,差距 3.26pp,泛化性进一步收敛。

**产物**：eval_dual_v283_d930_s444{,_b}_q38(.jsonl/_summary/_errors.html)。

### v28.4 候选列表属性匹配 resolve_listing（2026-10-21，用户定案实施）

**背景**：透传收口后残余 T8 值错位（524/1563/1959/977）的深挖发现根因不在"模型抄串行"，在**候选列表本身**——selector"约束+取最近"与渲染器"逐 session 按属性挑选"的口径分歧：1959 画像里有两条咪咕快游（6/12 的 100ms 与 7/4 的 80ms）,渲染器候选对挑的是 6/12 那条,selector"svc 命中取最近"给了 7/4 那条 → **GT 要的值根本不在模型看到的列表里**,直填也会精确填错。渲染器候选组成为逐 session 采样、无统一可推断规则（与 d6 时代"GT record 选择规则不可预测"结论一致）。

**实现**（用户定案"这个方案实施"）：
- `candidate_llm.resolve_listing_rule`（规则快路径）：A1 话术按"候选N/第N条"引导切段+剥回指尾巴（"您说的看直播和7月2日分别对应…"的'看直播'会被 svc 扫描误命中,540 型）,逐段抽属性（app/svc/rtt/res/完成日期）与 records 精确匹配——**全部候选唯一命中才返回,任一 0/多命中升级 LLM**。
- `candidate_llm.resolve_listing_llm`（LLM 兜底）：话术+records 全表→record_id 有序数组,属性匹配交 LLM（"咪咕快游+100ms+6月12号"→R-001,排除 80ms 的 R-002——渲染器按属性挑的,属性匹配可精确还原）。
- pipeline 接线：`disamb_cands`（话术序首选）与选择轮 `_listing` 均改走 `_resolve_listing`(规则→LLM)→回退 `_parse_disamb_candidates`(纯正则)→`_cand_listing` 存档。

**全量 T3-3 普查（258 例）**：rule 路径 **177 精确命中（68.6%）**、68 例升级 LLM、subset 12、superset 仅 1（001300,本就错）；对比旧 selector"equal 193 但含 superset 39/mixed 21"——**精确率大幅提升,歧义显式交 LLM**。

**结果（s444 q38,剔噪 436）**：**94.95% / 95.51% / 状态机 91.29%**——vs v28.3 94.72%/95.30% = +0.23pp/+0.21pp,翻正 1 翻错 0（纯增益）。T3-3 17/25 保持（值错位 4 例在 judge 之外,属性匹配已在列表层面修正,残余为模型执行层）。

**涉及**：candidate_llm.py（resolve_listing_rule/llm+_slim_record）、pipeline.py（_resolve_listing 接线×2 处）。

### v28.4 s444b 交叉验证 + d930 泛化性定案更新（2026-10-21）

**s444b（剔噪 435 参评）**：**94.25% / 94.71% / 状态机 90.25%**——与 s444 的 94.95%/95.51% 收敛（差 0.70pp/0.80pp）,属性匹配效应跨子集复现。

**d930 泛化性定案更新（合并 871 抽样,q38,v28.4,剔噪）**：**session 94.60% / turn 95.12%**（v28.3 94.60%/95.07% → turn +0.05pp,session 持平;较 v28.1 累计 +1.94pp）。turn 级提升被 session 级两例波动对冲（翻错 0,新增对侧翻正）；属性匹配的核心收益在**列表正确性与歧义显式化**（rule 177/258 精确命中+歧义升级 LLM,不再静默污染），为后续 scope 字段直填铺平列表基础。

**d930 泛化攻坚全程（v27.3 初版 91.43% 有效段 → v28.4 94.60%/95.12%）**,八轮迭代全部归档。

### 半结构化数据通路 semi_eval（2026-10-22，261022.jsonl 首验 100%）

**数据**：`data/semi_structured_dataset/261022.jsonl`——200 个 intent 级卡片（116 源 session,99 个含 2-3 intent），字段自述形态：输入=goal（自然语言，199/200 唯一）+constraints（业务约束文本，不映射槽位）；GT=context（身份）+sla.max_latency_ms（rtt）+lifetime（start/end/duration）。**无 resolution/bandwidth 维度（200/200 缺失）**；200/200 duration 与 end-start 一致、app/svc 全在 schema、无多候选/指代/矛盾场景。

**通路判定（tools/_semi_design_probe.py）**：全栈 DST 模块大面积可省略——检索/身份协议/time_extractor/TIME RULE×4/record_selector/候选模式/pending_app/quality_hint 全部无靶区（goal 无时刻表达 200/200,时间在 lifetime 直读）；保留=LLM 单次抽取+validator 矩阵校验（出口断言）+session 级聚合。**半结构化输入把"理解任务"压缩成"抽取+规范化"**。

**实现**（新 `semi_eval.py`）：LLM 单次调用 goal→四项抽取（app/svc/rtt/duration,JSON 受限），start/end/duration 由 lifetime 直读、rtt 由 sla 直读作 GT;validator 矩阵校验做出口断言；逐 intent 判分+失败明细。**首跑 10.5% 暴露 goal 字面短语→schema 词的规范化缺口**（"开直播推流"→开直播/"在线视频播放"→视频/"串流游玩"→云游戏）;修复=prompt 加 application_name 枚举+app-svc 矩阵 Literal 约束（与主栈 prompt 同构）。**二跑 100%（200/200）**。

**意义**：半结构化输入下 LLM 任务从"多轮理解+状态累积"退化为"单轮抽取+规范化"，schema 枚举约束是抽取出 100% 的关键——与主栈"受限枚举锁死输出空间"（v28）经验同构。**无透传问题**：无跨轮状态、无候选、无抑制类规则，三层透传缺口在此数据形态上天然不存在。

**产物**：semi_eval.py、data/evals/semi_v2841_261022_q38.jsonl/_summary.json。

### 入口路由 input_router（2026-10-22，双通路判别）

**背景**：半结构化（semi_eval,100%）与非结构化（session_eval 全栈,94.60%）两条推理通路并存后,需要"拿到一条数据先判定走哪条通路"的入口判别。

**实现**（新 `input_router.py`）：**纯结构层判别,无需 LLM**——
- `event_sequence` + `intents/slot_updates` → session 通路（全栈 DST:多轮/画像/候选/透传全需要）;
- `goal` + `sla/lifetime/constraints` 卡片 → semi 通路（LLM 单轮抽取+规范化,时间/身份结构化直读）;
- **安全降级**：semi 外壳但 goal 含记录指代词（上次/之前/最近那条）→ 强制走 session 通路（记录指代需要画像与选择链,semi 通路无此能力）;
- 其余 → unknown。

**验证**：200 semi 卡片 → 200/200 判 semi;4455 d930 sessions → 4455/4455 判 session;3 边界用例全过（含降级与 unknown）。

**意义**：回答"拿到一条数据能否辨别应如何推理"——**能,且是确定性的**:判别信号在数据结构层（键形态）,不是语义层。两条通路的本质差异=任务语义复杂度（多轮理解+状态累积 vs 单轮抽取+规范化）,由数据形态决定,路由只是把既有事实显式化。

### v28.5 schema 无关泛化：schema_discovery 值形态探针（2026-10-22，用户定案实施）【已封死：2026-10-23 半结构化通路整体删除，见下方封死条目】

**背景**：用户指出 semi 通路/input_router 硬编码 261022 的键名 (goal/sla/lifetime/constraints)——未见过的结构（键名不同但语义相同，如 description/latency_sla/schedule）不泛化。semi_eval 硬编码键引用 32 处。

**实现**（新 `schema_discovery.py`，键名无关）：
- `infer_semantic_roles(obj)`：**值形态扫描**推断每个顶层键的语义角色——时间区间（≥2 个规范时间戳）/latency（单数字 5~1000）/constraints（字符串数组）/大写枚举常量→other（**中文 upper() 恒等,必须 isascii() 门**——context 全中文被误判 other 的根因）/identity（短文本含已知 app 名）/goal（含"保障"且≥20 字）/键名低权重提示兜底。
- `project(obj)`：角色视图→semi 标准视图 {goal, identity_texts, latency_ms, time_range, constraints}。
- `resolve_listing_llm` 已有，新增 `resolve_listing_rule` 于 v28.4（见上条）。
- `input_router.py` 重写：改吃 role 视图——全结构化无 goal → session（校验/聚合）；goal+latency+time_range → semi；goal 含记录指代词 → 降级 session（安全保护保留）。
- `semi_eval.py`：**GT 重建双路径**——原生键齐全走原生 gt_of；GT 全 None（陌生结构）→ project 投影视图重建（rtt/时间从投影直读，身份走 identity_texts 松对照 skip 机制）；goal 文本取自投影；intent_id 加 intent_no 兜底。

**验证**：①原生 261022：200/200 不变（零回归）；②**改名模拟**（goal→description/sla→latency_sla/lifetime→schedule/context→service_info,20 例）：**100%**——键名不同语义相同的结构泛化成功；③input_router：200 semi + 4455 d930 全判对，改名 semi 卡片判 semi；④role 探针双结构语义覆盖（goal/latency/time_range/constraints/identity）均完整。

**过程中抓出的坑**：中文 str.upper() 恒等（'直播内容推流'==大写）——大写枚举常量检测必须加 isascii() 门；heredoc 内嵌测试函数的变量遮蔽导致调试假象（改用文件式脚本）。

**意义**：semi 通路从"识别一种特定格式"升级为"识别一类值形态"——只要新结构的【值】里能找到时间区间对/时延数字/已知 app 名/长保障描述，无需改代码即可接入。规则探针覆盖不了的形态由 LLM 键→角色映射兜底（discover_llm 已备）。

**涉及**：schema_discovery.py（新）、input_router.py（重写）、semi_eval.py（gt_of 双路径/judge skip/main 接线）、tools/_dbg_role.py（调试）。

### v28.6 LLM 格式学习（schema discovery 兜底，2026-10-22，用户定案"应该用 LLM 判断学习格式"）【已封死：2026-10-23 半结构化通路整体删除】

**背景**：v28.5 值形态探针实测有盲区（qos 多数字嵌套 `{"latency_cap_ms": 50, "jitter_ms": 10}` 判 other）——**值形态枚举永远追不完所有模式**（用户判断成立）。 

**实现**：`schema_discovery.py` 增 `coverage_ok`（goal+latency+time_range 最小覆盖集检查）+ `discover_llm`（LLM 按值内容判断每个键的语义角色,判断依据=内容形态非键名拼写）+ `resolve_schema` 统一入口（规则探针优先→覆盖不全时 LLM learn 兜底→角色混合[规则高置信形态保留,LLM 补判 goal/identity 歧义区]→重投影）。semi_eval 陌生结构分支改走 resolve_schema。

**验证**：①改名结构（5 例）经 resolve_schema 100%;②**混合盲区结构（qos 嵌套形态,探针 BLIND 形态）5 例也 100%**——LLM 格式学习补上了形态枚举的盲区;③原生 261022 200/200 零回归。

**架构定型**：三层识别体系——①规则形态探针（高频形态零成本快路径）②coverage 检查（goal+latency+time_range 最小集）③LLM 格式学习（长尾兜底,按值内容判角色）。与候选链路"正则快路径→LLM 兜底"同构,泛化问题收敛到同一架构模式。

### v28.6 终态归档补充（2026-10-22）：规则探针删除 + LLM 判定验证 + 双通路实例讲解【已封死：2026-10-23 半结构化通路整体删除】

**规则探针删除**（用户指令"前两层基于测试数据制作，删去"）：`_role_for` 值形态探针与 `coverage_ok` 全部删除——其关键词表/形态枚举均由测试数据值形态归纳，涉嫆作弊风险。`schema_discovery.py` 终态 = `learn_format_llm`（LLM 按值内容判每个键的语义角色）+ `resolve_schema`（重投影）+ `_walk_strs`/`_DT_PAIR` 通用工具。

**纯 LLM 判定验证**：①原生 261022 抽取 200/200 零回归；②改名结构（description/latency_sla/schedule 等）20/20 = 100%；③input_router LLM 路由：d930 4455/4455 判 session、semi 199/200（1 例 LLM 单次方差，可投票消除）。

**一条未知数据的双通路判别与推理实例**（讲解用，数据为真实样本）：
- 输入 A（键: session_id/user_id/event_sequence/intents）: 结构快判定命中 `event_sequence` → **session 通路**（这是数据形态定义：多轮话语必然需要透传/状态累积），后续走全栈八模块。
- 输入 B（键: intent_id/goal/constraints/sla/lifetime/context）: 无 `event_sequence` → 非多轮 → **LLM learn_format_llm** 按值内容判角色：goal="保障快手开直播推流60分钟…" → goal 角色；lifetime={"start_timestamp":"2026年07月27日23时00分","end_timestamp":…} → time_range 角色；sla={"max_latency_ms":30} → latency 角色；context={application_name:"快手",…} → identity 角色 → **semi 通路**（角色含 goal+latency+time_range,goal 无记录指代词），LLM 从 goal 抽取 app/svc/rtt/duration 四项（枚举规范化），start/end 由 time_range 直读。
- **判别信号全部在结构层与值内容层，判定本身不需要见过该数据的格式**——LLM 按语义角色归类的泛化能力替代了键名硬编码与形态枚举。

### 半结构化通路整体封死（2026-10-22，用户定案"全部删除不可用"）

**用户指令（原文）**："现在这个解法全部删除不可用。这个意图理解模块是每次收到一条信息进行判断，例如多轮对话会在第二轮附加上之前的信息，但是不会在第一轮就得知后面的对话内容，所以不能通过轮数判断结构非结构。还有不能通过特定关键词判断结构非结构，因为话术一直在变化。考虑加入分辨模块（决定是否走快路径）或者其他方法，可以查询相关论文再决定方案。之前的关于半结构化解法全部删除" + "原有的非结构化解法不要动"。

**封死根因**：input_router/schema_discovery 的前提是"输入形态可判别"——按结构字段（event_sequence 有无）或按键值角色投影分流。但生产侧**意图理解是逐消息进行**的：多轮对话在第 2 轮会附带此前信息，第 1 轮无法预知后续轮内容——"轮数/整体结构"在判定时刻不可得；话术持续漂移，任何关键词表都会失效。**按输入形态分流本身不可成立**，故整条半结构化旁路（semi_eval 抽取通路 + input_router 入口路由 + schema_discovery 格式学习）整体删除，不保留代码。session 全栈通路（非结构化解法）不受影响、维持 v28.4 定案。

**已删除**：`schema_discovery.py`、`input_router.py`、`semi_eval.py`、`tools/_semi_design_probe.py`、`tools/_dbg_role.py`、`data/evals/semi_*` 全部评测产物、`data/semi_structured_dataset/_test_renamed.jsonl`。selftest.py / candidate_selftest.py 验证主栈零回归。

**教训归档**：①凡"拿到一条数据先判类型再走路径"的入口分流，必须确认判定时刻所需信号在该时刻可得——逐消息场景下"整条数据的形态"不是合法判定信号；②前两版（键名硬编码→值形态探针）已被用户连续否决两次（"涉嫌作弊""覆盖不完"），第三版（LLM 判角色）虽通过验证，但**前置分流问题不成立使后级再准也无意义**——先证伪前提，再投入精度优化。

**后续方向**（用户指定）：不做"输入形态分流"，改做**逐消息快路径判别模块**（每条消息独立判断是否需要全栈处理，还是可直接快路径落地）——约束：逐消息判定、无轮数信号、无关键词表、抗话术漂移。方案待论文调研（cascade/routing 文献）后由用户定案，**未定案前不实施**。

### v28.7 判别器路线终止 + 格式注册表方案定案（2026-10-23，用户定案"半结构化数据会提前给格式"）

**经过**：逐消息判别模块完成论文调研（cascade/routing：FrugalGPT 2305.05176 事后打分、RouteLLM 2406.18665 事前分类器、MoT cascade 2310.03094 一致性升级、Conformal Cascade 2607.25018 唯一带错误率上界；对话侧：OrchestraLLM 2311.09758 DST 逐实例路由算力降>50%、INTENT-SIM 2311.09469 意图熵、Larson OOS 1909.02027 最佳系统 OOS recall 仅 66% 佐证话术漂移下枚举失效、Overconfidence 2405.02917 verbalized confidence 不可单用）。调研结论："逐消息+无关键词+无轮数"约束下优先 LLM 受限枚举判别器（与 v28 judge 同构）。已构建 63 条混合探针集（结构化卡片 20 + d930 消息 43 条 8 类，fast/full gold 设计定义并人工核读）准备测三模型（flash/27b/plus）时间精度差距。

**用户定案**："停止实验，现在确定了方案，之后半结构化数据会提前给格式，设置一个可以更新新格式的方案即可"——判别器/路由方向整体终止（探针集与已跑部分产物已删），既然格式由数据方提前给出，则无需任何运行时判别。

**落地**（新 `format_registry.py` + `data/formats/`）：可更新格式注册方案——**接入新格式 = 新增一个 spec JSON，零代码改动**。spec 声明 `key_roles`（点分键路径→语义角色 goal/constraints/latency_ms/time_start/time_end/duration_min/identity）+ `identity_map`（身份键改名映射）+ `required_roles`（必需角色校验）。API：`register`（校验+写盘+索引）/`load`/`project`（结构化字段直读投影为规范视图，无 LLM）/`validate_view`（缺角色拦截）/`match`（按键签名在已注册格式中分流，唯一命中才返回）。已注册 `semi_261022`（261022 五键卡片）。自检 `tools/_selftest_format_registry.py` 四项全过：①原生格式投影正确；②合成改名结构（description/latency_sla/schedule/service_info）注册即用零代码；③缺字段拦截；④match 双格式正确分流。主栈 selftest/candidate_selftest 零回归。

**与封死路线的边界**：本方案不做任何"按输入形态/内容实时判路由"——格式由外部提前声明，注册表只做声明→投影的机械映射；schema_discovery 的 LLM 运行时格式学习不复活。goal 文本的槽位抽取（自然语言部分）若日后需要，另行定案。
