# 代码结构和使用文档（codeguide.md）

> 更新：2026-10-24 ｜ 新增：半结构化格式注册表（format_registry，§5/§6/§8 已收录）。注：v27.3~v28.4 候选链路/scope/透传修复详见 docs/CHANGELOG.md，本册模块地图部分行尚未同步该链。
> 本册 = 新人上手指南 + 模块地图 + 常用命令 + 扩展指引。

## 1. 系统一句话

多轮多意图"网络质量保障客服"的对话状态跟踪（DST）：每轮把用户话语转成 Text-to-Python 程序 → 沙箱执行得增量 Δy → 合并进意图状态 → 逐轮透传直到 session 结束，与 GT 严格全等比对（JGA）。

## 2. 数据流总览

```
session(jsonl)
  └─ session_eval.run_session          逐轮驱动 + prev_states 真实透传 + id 账本(v26)
       └─ pipeline.run_multi           单轮入口（规则触发检测/检索/prompt组装/段处理）
            ├─ retriever.retrieve      MMR 检索 demo（query = prev_state||utterance）
            ├─ prompt_builder.build_prompt  system+task（demo/schema/画像/规则段/id 呈现; v27 time_hint/压缩时间散文段）
            ├─ llm_client.gen_raw_code qwen-plus 生成 Python 程序（PAR 校验+重采样回路）
            ├─ python_state.execute_program_multi  沙箱按 # intent 段执行得 Δy
            └─ validator.validate      合并 prev→final_state + 知二推一 + 合法性校验
       └─ judge_session / turn_gt / judge_turn   session 级 + turn 级 JGA（含三口径修正）
  └─ error_report.generate             错误归类 + HTML 可视化（--err_report 自动触发）
```

## 3. 模块地图（按职责）

| 文件 | 行数 | 职责 | 关键符号 |
|---|---|---|---|
| `session_eval.py` | 772 | 评估器：逐轮驱动、透传、双口径 JGA、状态机口径、CLI | `run_session` `session_gt` `turn_gt` `judge_turn` `_derive_time_flat` `main` |
| `pipeline.py` | 797 | 单轮管线：规则触发（消歧/改口/记录/correction_demo）、prompt 组装、**段处理三方法**（`_process_id_segment` id 协议 / `_process_key_segment` 旧协议 + `_handle_cancel_segment`）、conflict_check 回路 | `MultiIntentPipeline.run_multi` `_match_prev_key` `_process_id_segment` `_suspicious_new` `_identity_ok` |
| `prompt_builder.py` | 666 | system/task prompt；demo 反编译（`_case_to_program` 双协议）；规则段（消歧/改口/记录/冲突/id 协议 TASK 块） | `build_prompt` `_format_case` `_build_selection_chain` |
| `python_state.py` | 361 | 沙箱：State 对象（coreference `state.xxx` 读 prev）、find_* 桩、段切分 | `execute_program_multi` `_split_intent_segments` |
| `validator.py` | 385 | Δy 合并 prev → final_state；时间知二推一（v27 起不产负 duration）；合法性校验（矩阵/范围/时间一致性）；冲突检测（v25 信号分层） | `validate` `_complete_timestamp` `detect_utterance_conflicts` |
| `schema.py` QUALITY_MAP | 79 | v27.2.1 标准画质映射常量（源 resolution_quality_map.md，数据侧单源）；QUALITY HINT 查表用 | `QUALITY_MAP` |
| `candidate_book.py` | ~120 | v27.2 候选模式：CandidateBook 旁路账本（candidate 段解析/记账/select 落地/cancel 移除，不入 JGA） | `parse_candidates` `CandidateBook` |
| `record_selector.py` | ~200 | v27.1 记录指代预判器（悬空 defer/定位判定树；已三轮否定关闭，enabled 默认关，代码留档） | `record_evidence` `render_hint` |
| `time_extractor.py` | ~350 | v27 明说时间规则提取器：日期表预计算 + utterance 时间表达式提取/预解析（TIME ANCHOR/EVIDENCE 证据，生成侧注入，严禁后处理覆盖 pred） | `build_date_table` `extract` |
| `intent_ids.py` | 106 | v26 id 协议账本：id 分配、id↔内容 key 双向映射、rename、prev 渲染、段头解析 | `IntentIdBook` `parse_id_header` |
| `case_format.py` | 248 | 嵌套 params 的扁平/应用/累积；从 session 抽 turn case | `extract_turn_cases` `apply_nested` `flatten_params` `EVAL_SLOTS` |
| `retrieval_bank.py` / `retriever.py` / `embedder.py` | 133/105/165 | 检索库（case 索引+向量）、MMR 多样性检索、DashScope embedding | `CaseBank.load` `DiverseRetriever.retrieve` |
| `llm_client.py` | 80 | DashScope chat 调用（重试+退避）；**请求无 seed**（temp=0 仍非确定） | `LLMClient.gen_raw_code` |
| `intent_status.py` | 252 | 状态机：Affirm/待消歧识别、状态推导、GT 轨迹、判定 | `derive_status` `build_gt_status_trajectory` `judge_status` |
| `error_report.py` | 1559 | HTML 报告：turn/session 双视图、错误/原因分布、状态演进、噪声区块 | `generate` `scenario_tag` `state_evolution_html` |
| `format_registry.py` | ~120 | **半结构化格式注册表**（2026-10-23 定案：格式由数据方提前给，不做运行时判别）：spec 注册/投影/校验/签名分流 | `register` `load` `project` `validate_view` `match` |
| `schema.py` | 127 | 受理矩阵（app×service）、槽位 schema、SLOT_SCHEMA | `APPLICATION_SERVICE_MATRIX` |
| `build_casebank.py` / `make_split.py` | 77/172 | 检索库构建（必须显式 DST_DATASET）、train/test 切分 | — |
| `features.py` | ~100 | **机制开关单一事实源**（v26.4）：18 开关一次性解析为 Features dataclass（v27 +6 时间类开关），联动规则唯一实现处；新机制必接此层 | 必读 |
| `gt_build.py` | 178 | turn 级 GT 构建层（v26.4 自 session_eval 拆出）：turn_gt + cancel/correction/gt_derive 三口径；session_eval 与 error_report 共同消费 | — |
| `tools/package.py` | 130 | 入库打包（白名单+自检断言）；`find_noise.py`/`conflict_detector.py`/`_oracle_pass2.py` 已归档 tools/_archive | — |
| `selftest.py` | 169 | 离线自检（沙箱/校验器/prompt/MMR），改代码后必跑 | `python selftest.py` |
| `tools/` | — | 活跃工具：离线复算（correction_credit/gt_derive）+ 多模型冒烟 | `recompute_gt_derive.py` `_smoke_qwen38.py` |
| `tools/_archive/` | — | 一次性脚本归档（规则回放/触发扫描/噪声报告/历史冒烟，均有 CHANGELOG 结论背书） | — |
| `configs/_archive/` | — | 历史消融配置归档（abl/d4/merged/ccheck/candmap 等，数字见 CHANGELOG） | — |

## 4. 环境与配置

```bash
# 依赖
pip install -r requirements.txt

# API key（keys.txt 或环境变量）
export BAILIAN_API_KEY=sk-xxx        # llm 与 embedding 共用

# 必须绕开系统代理（会劫持 DashScope 请求致假死）
unset all_proxy ALL_PROXY http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

# 数据集根（默认 data/dataset_5）
export DST_DATASET=data/dataset_5
```

配置文件（configs/）：
- `config_d6_qwen_plus_best.json` — **d6 当前最佳（v27.0）**：candmap + `pending_app_rule` v3 + `multi_instance_key` + **六时间开关全开**（`time_anchor`/`time_extractor`/`time_rule_contradiction`/`time_rule_profile_slot`/`time_rule_explicit_first`/`time_rule_candidate`，session 92.74%/turn 93.54%）；跑法三件套 `DST_DATASET=data/dataset_6_clean` + `--split data/split_d6_full.json` + `--noise_file data/_noise_exclude_d6_scan.json`
- `config_d5_qwen_plus_idproto.json` — **d5 当前最佳（v26.2b）**：qmap 全量开关 + `intent_id_protocol.enabled=true`
- `config_d5_qwen38_27b_idproto.json` / `config_d5_qwen3_235b_idproto.json` / `config_d5_qwen3_32b_idproto.json` — 其他模型档（四模型对照，v26.2c）
- `config_d5_qwen_plus_qmap.json` — v25.2 最佳（旧协议路径，门控回退用）
- 其余历史消融配置已归档至 `configs/_archive/`（ccheck/candmap/fix4/disamb/recrule/status 等）

## 5. 常用命令

```bash
# 自检（改代码后必跑，离线零 API）
python selftest.py

# 全量评估（322 test，双口径 + 状态机 + 自动 err_report）
PYTHONIOENCODING=utf-8 python session_eval.py \
  --config configs/config_d5_qwen_plus_idproto.json \
  --tag v27_x --err_report
# 常用旗标：--limit N 冒烟 | --include_noise 不剔噪 | --no_gt_derive/--no_cancel_credit/
#           --no_correction_credit 关口径 | --retrieve off 无检索消融

# 错误报告（单独重生成）
python error_report.py --eval data/evals/eval_dual_v27_x.jsonl \
  --dataset data/dataset_5 --noise data/_noise_exclude_d5_scan.json

# 离线复算（口径变更后对旧 eval 重算，不重跑模型）
python tools/recompute_gt_derive.py data/evals/eval_dual_v27_x.jsonl
python tools/recompute_correction_credit.py data/evals/eval_dual_v27_x.jsonl

# 冒烟（靶向 session 真实 API）
python tools/_smoke_qwen38.py

# 半结构化格式注册表自检（离线零 API：原生投影/改名注册/缺字段拦截/签名分流）
python tools/_selftest_format_registry.py

# 检索库重建（换数据集/协议后必须）
DST_DATASET=data/dataset_5 python build_casebank.py   # 显式指定，勿依赖默认
python make_split.py                                  # seed42/0.4
```

## 6. 数据集

```
data/dataset_5/            819 sessions（sessions.T1-1.jsonl ... sessions.X-2.jsonl + users_3000.jsonl）
  session 结构             event_sequence(对话) / intents(最终态+status) / slot_updates(逐轮逐字段 GT+来源) / relations(意图协作关系)
  users_3000.jsonl         用户画像 profile + records（completed_at 降序 = 模型所见顺序）
data/split_d5.json         seed42/0.4 → train 490 / test 329（剔 7 噪声 = 322）
data/case_bank_train_d5.jsonl / embed_cache_d5.json   检索库 + 向量缓存

data/formats/              半结构化格式注册表（registry.json 索引 + 每格式一个 spec JSON）
  spec 字段                format_id / version / description
                           key_roles      {点分键路径: 角色}（goal/constraints/latency_ms/time_start/time_end/duration_min/identity）
                           identity_map   {identity 对象内键: 标准身份键}（键名不同语义相同时使用）
                           required_roles [投影后必须非空的角色]，缺失即拦截
  已注册                   semi_261022（261022 五键卡片：goal/sla/lifetime/context/constraints）
data/semi_structured_dataset/261022.jsonl   200 张 intent 卡片
```

## 7. 意图身份机制（v26.2b，最重要的运行时逻辑）

```
session 开始: IntentIdBook() 一本账
每轮开始:    prev_states(内容键) --ensure--> _id_states(id 键) 传给 pipeline
prompt:      "# 已有意图:  I1 = {...}  I2 = {...}"  + 段头协议(身份判定行强制)
模型输出段:  # intent: I1  → key_of(I1) 查表取 prev → State(prev) 沙箱执行
             # intent: new → 对空 prev 执行 → 归并校验(无app同svc互补/唯一悬置)
             │              → 命中则 prev 填空槽+残骸 _consumed_key 退役
             │              → 产出 key 已在 results 则丢弃冗余段(段去重)
             # cancel: 名字 → _resolve_cancel_key 归一化(id 也可)
轮末透传:    results(内容键) → session_out / new_prev；cancelled∪consumed 移除；其余沿用
评估:        id→内容键映射后与 GT 比对（GT 侧从不感知 id）
```

## 8. 扩展指引（按改动类型）

| 想做什么 | 改哪里 | 注意 |
|---|---|---|
| 新增触发式规则 | pipeline 触发检测函数 + prompt_builder 规则段 + **features.py 加字段** + config 开关 | 触发式勿 always-on（v22.0③ 教训）；开关解析只在 features.py，禁止散布 cfg.get |
| 时间类注入 | pipeline `_build_time_hint`（日期表/EVIDENCE/四条 TIME RULE 组装）→ build_prompt `time_hint` 参数；提取器改 `time_extractor.py` + selftest 断言 | 严禁后处理覆盖 pred（v23 封死）；回放须盯 turn 级 time_wrong 增量；见 experience/11_time_rules.md |
| 改输出协议/段头格式 | prompt_builder（TASK 块+demo `_case_to_program`）+ python_state 段解析 + pipeline 段处理 | **demo 与目标格式逐字节一致**（v12.0 教训）；embed cache 重建 |
| 调整评估口径 | session_eval（turn_gt/judge_turn）+ 回退开关 + summary 落盘 + tools/recompute_* | 先离线复算验证双向翻转（转正/翻错都统计） |
| 换数据集 | DST_DATASET + split + casebank + embed cache + 画质映射表（从 train source_ref 还原）+ 重扫噪声 | dataset_versions.md 更新 |
| 换模型 | config llm.model | 注意 qwen2.5-72b 无权限（403 静默全空）；跑消融时 embed cache 按模型分文件 |
| 加错误可视化 | error_report（categorize/scenario_tag/卡片） | 新错误桶要加 CAT_META+CAT_ORDER+judge_turn 桶映射 |
| 检测 GT 同 key 双意图 | error_report `dup_key_sessions`（徽章）| session 级 gt/pred dict 按 app/svc 塌缩，6/234 d6 session 受损；多实例 key 属 P3 架构升级 |
| **接入新半结构化格式** | **不改代码**：只写一份 spec JSON 注册进 `data/formats/` | 格式由数据方**提前给定**（定案前提，运行时判别/LLM 格式学习路线已封死勿复活）；步骤=①按数据方字段说明写 key_roles（点分路径→角色）+identity_map+required_roles ②`python -c "from format_registry import register; register(<spec dict>)"` ③`project()` 直读投影 + `validate_view()` 拦截缺角色数据 ④同库多格式时 `match(obj)` 按键签名分流；注册表改动后跑 `python tools/_selftest_format_registry.py` |

## 9. 质量纪律清单（每次改动）

1. `python selftest.py` 过；
2. 冒烟（靶向 session 真实 API）行为符合预期；
3. 全量跑 + `--err_report`；
4. 翻转归因（获救/回退逐个核验 llm_raw，区分机制因果 vs 运行波动）；
5. 双跑（±2pp 内不作采纳依据）；
6. CHANGELOG + 三册 + 评估口径册（若动口径）+ 记忆更新；
7. **含 bug 的采样从一切统计中剔除**。

## 10. 相关文档索引

| 文档 | 内容 |
|---|---|
| docs/report.md | 项目总览/当前最佳/水平表 |
| docs/report_method.md | 方法演进全表（v1→v26.2b）+ 失败教训 |
| docs/report_experiments.md | 实验设置/全版本结果/错误分析 |
| docs/report_eval.md | **评估口径专册**（三口径修正/换算表/FAQ） |
| docs/report_flow.md | 状态机全流程 + **全量门控清单（12 开关）** |
| docs/report_reflection.md | 反思机制调研 + **v25.0 实验执行日志（§8）** |
| docs/dataset_versions.md | 数据集谱系 |
| docs/experience/ | **经验总结文件夹（9 篇，按主题，给同类项目参考）** |
