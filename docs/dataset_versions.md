# 数据集版本统计

> 维护口径：每次数据集变更（新增版本/合并/修复）时更新本文档。统计脚本口径：sessions 数 = 各模板文件行数；turns = event_sequence 长度合计；intents = intents 长度合计；users = 去重 user_id 数。ID 重叠按 session_id 计算。
> 当前状态（2026-09-22）：**主战场 = dataset_6_clean 泛化测试集（v27.0 最佳 92.74%/93.54%）；dataset_5 = 训练/检索库数据集（历史最佳 v26.2b 88.67%/88.83%，v27 未在 d5 重跑）**。

## 版本总览

| 版本 | 位置 `data/` | sessions | turns | intents | users | 状态 |
|---|---|---|---|---|---|---|
| dataset (v1) | `dataset/` | 347 | 687 | 496 | 241 | 历史，停用 |
| dataset_2 (v2) | `dataset_2/` | 814 | 1561 | 1190 | 760 | 历史，2026-08-18 起停用 |
| dataset_3 | `dataset_3/` | 709 | 1364 | 1010 | 672 | 中间版本 |
| **dataset_4** | `dataset_4/` | **702** | **1345** | **1025** | **667** | 2026-08-27 起被 dataset_5 取代 |
| dataset_merged (v1.0) | `dataset_merged/` | 882 | 1710 | 1299 | 817 | 2026-08-25 起被 2.0 取代 |
| dataset_merged_2.0 | `dataset_merged_2.0/` | 920 | 1797 | 1358 | 847 | 2026-08-26 起被 dataset_4 取代 |
| **dataset_5** | `dataset_5/` | **819** | **1598** | **1184** | **765** | **训练/检索库数据集（2026-08-27 起）** |
| **dataset_6_clean** | `dataset_6_clean/` | **237** | **463** | **357** | **222**（画像文件 3000 条覆盖） | **当前泛化性测试集（2026-09-10 起，见下）** |

## dataset_5 作为评估数据集（2026-08-27 起，用户决策）

dataset_5 = 819 sessions（对比 d4：新增 178 / 删除 61 / 重叠 641 且**641 条话语全部改写**、intents 零变动；d5∩merged_2.0 = 794）。users_3000.jsonl 已验证覆盖全部 765 个 user_id 并拷入 `dataset_5/`。

- **配套文件（2026-08-27 已生成）**：`split_d5.json`（make_split.py，seed 42 / test_ratio 0.4 / 模板分层：train 490 / test 329）、`test_cases_d5.jsonl`（781 turn-intent cases，保障 747 / 非保障 34）、`case_bank_train_d5.jsonl`（490 条，已核验 train 数匹配）、`embed_cache_d5.json`（首跑构建）。评估配置 `configs/config_d5_qwen_plus_status.json`（= v16.0 最佳配置，仅替换 case_bank/embed_cache 路径）。
- **噪声（find_noise.py 全量重扫）**：d5 仍有 R5 系 record 噪声 **13 条**（新规则变体 R5-A/R5-C；旧 16 条清单是 merged_2.0 时代产物已失效，仅 S-000018/S-000760 复现）：S-000018, S-000115, S-000281, S-000377, S-000401, S-000414, S-000465, S-000583, S-000641, S-000760, S-000844, S-000852, S-000954。test 中 7 条（S-000018/115/465/583/760/844/954）→ **有效 test = 322**；train 中 6 条在检索库内。清单 `data/_noise_exclude_d5_scan.json`；session_eval 新增 `--noise_file` 参数（旧 `_noise_exclude.json` 保留不动）。取消计分口径（v17.1 方案 b）自 d5 起默认生效；d5 cancelled intents 共 103 条。
- 跑法：`export DST_DATASET=data/dataset_5 && python session_eval.py --config configs/config_d5_qwen_plus_status.json --split data/split_d5.json --noise_file data/_noise_exclude_d5_scan.json --err_report`。

## dataset_4 作为评估数据集（2026-08-26 起，用户决策）

用户决策：停用 dataset_merged_2.0，直接用 dataset_4 评估。d4 即修复版本身（702 sessions，664 条重叠修复 + 38 条新增，见下节"dataset_merged_2.0"的修订统计）。

- **配套文件（2026-08-26 已生成）**：`split_d4.json`（make_split.py，seed 42 / test_ratio 0.4 / 模板分层：train 422 / test 280）、`test_cases_d4.jsonl`（650 turn-intent cases，保障 620 / 非保障 30）、`case_bank_train_d4.jsonl`（422 条；⚠️ 首次误以默认 dataset_2 构建仅 376 条，`build_casebank.py` 必须显式 `DST_DATASET=data/dataset_4`）、`embed_cache_d4.json` / `_status.json`（以 merged_2 cache 为种子）。`users_3000.jsonl` 已拷入 `dataset_4/`（session_eval 从 DATASET_DIR 内 glob `users*.jsonl` 加载画像）。
- **噪声分布**：16 条残留 record 噪声全部落在 train 侧 → test 280 无需剔除（全集口径）；噪声在检索库内，demo 教旧 record 规则，影响有限。
- 评估配置 `configs/config_d4_qwen_plus_agentctx.json`（baseline）/ `config_d4_qwen_plus_status.json`（+status_prompt，当前最佳）。跑法：`export DST_DATASET=data/dataset_4 && python session_eval.py --config configs/config_d4_qwen_plus_status.json --split data/split_d4.json --err_report`。
- 首个基线（v16.0，2026-08-26）：qwen-plus baseline 68.93%/67.58%；+status_prompt **70.36%/68.44%**（当前最佳）。

## dataset_merged_2.0（2026-08-25）

由 dataset_merged + dataset_4 合并生成（合并脚本逻辑：按 session_id 去重，重叠样本取 dataset_4 修复版，dataset_4 新增样本按原模板文件追加；`users_3000.jsonl` 沿用 merged 版，已验证 847 个 user_id 全覆盖）。

- 构成 = 664 条重叠（取 d4 修复版） + 218 条 merged 独有（原样保留） + 38 条 d4 新增。
- 模板归属零变动（无 session 跨模板文件迁移）。
- **噪声修复**：原 `_noise_exclude.json` 49 条 record 噪声 session 中，33 条已被 dataset_4 修复并替换；**剩余 16 条未修复**（仍以旧版存在）：
  S-000018, S-000070, S-000266, S-000273, S-000279, S-000285, S-000480, S-000523, S-000618, S-000631, S-000676, S-000735, S-000748, S-000760, S-000962, S-000987。
- ✅ `_noise_exclude.json` 已更新（2026-08-25）：剔除集已缩为上述 16 条残留噪声（11 条在新 test、5 条在 train），reasons 保留原 R5 说明。
- dataset_4 本身是大范围修订：664 条重叠样本中 546 条 intents/slot_updates 有变更、662 条 event_sequence 话语改写、490 条 session_meta 变更，非仅噪声修复。
- **配套文件（2026-08-25 已全部生成）**：`split_merged_2.json`（make_split.py，seed 42 / test_ratio 0.4 / 模板分层：train 552 / test 368，无交集）、`test_cases_merged_2.jsonl`（875 turn-intent cases，保障 826 / 非保障 49）、`case_bank_train_merged_2.jsonl`（552 train sessions → 1336 turn cases）、`embed_cache_merged_2.json`（旧 cache 种子 2304 + 新增 863 = 3167 条，text-embedding-v3）。评估配置 `configs/config_merged_2_qwen_plus_agentctx.json`（= v14.0 当前最佳配置，仅替换 case_bank/embed_cache 路径）。跑法：`export DST_DATASET=data/dataset_merged_2.0 && python session_eval.py --config configs/config_merged_2_qwen_plus_agentctx.json --split data/split_merged_2.json --err_report`。

### 模板分布（sessions）

| 模板 | dataset | dataset_2 | dataset_3 | dataset_4 | merged | merged_2.0 | dataset_5 |
|---|---|---|---|---|---|---|---|
| T1-1 | 20 | 54 | 53 | 50 | 54 | 54 | 54 |
| T1-2 | 22 | 54 | 53 | 51 | 54 | 54 | 54 |
| T1-3 | 29 | 85 | 77 | 83 | 90 | 90 | 87 |
| T1-4 | 16 | 39 | 33 | 34 | 43 | 49 | 41 |
| T2-1 | 22 | 52 | 48 | 50 | 53 | 53 | 52 |
| T2-2 | 19 | 45 | 43 | 38 | 48 | 50 | 48 |
| T2-3 | 21 | 50 | 43 | 41 | 53 | 53 | 49 |
| T2-4 | 49 | 76 | 71 | 69 | 89 | 92 | 87 |
| T2-5 | 11 | 34 | 31 | 30 | 41 | 46 | 42 |
| T2-6 | 54 | 80 | 75 | 69 | 86 | 89 | 81 |
| T3-1 | 22 | 51 | 48 | 38 | 53 | 53 | 50 |
| T3-2 | 20 | 44 | 34 | 36 | 46 | 48 | 43 |
| T3-3 | 14 | 34 | 33 | 31 | 44 | 48 | 36 |
| T3-4 | 22 | 41 | 36 | 38 | 45 | 50 | 47 |
| X-1 | 2 | 40 | 10 | 14 | 42 | 43 | 14 |
| X-2 | 4 | 35 | 21 | 30 | 41 | 48 | 34 |
| **合计** | **347** | **814** | **709** | **702** | **882** | **920** | **819** |

## 版本间 ID 重叠（session_id 交集）

| | dataset | dataset_2 | dataset_3 | dataset_4 | merged | merged_2.0 | dataset_5 |
|---|---|---|---|---|---|---|---|
| dataset | — | 305 | 282 | 277 | 327 | 335 | 311 |
| dataset_2 | | — | 641 | 629 | 814 | 814 | 717 |
| dataset_3 | | | — | 562 | 709 | 709 | 656 |
| dataset_4 | | | | — | 664 | 702 | 641 |
| merged | | | | | — | 882 | 772 |
| merged_2.0 | | | | | | — | 794 |
| dataset_5 | | | | | | | — |

谱系：dataset_merged = dataset_2 ∪ dataset_3（814 + 709 − 641 = 882 ✓）；dataset_merged_2.0 = dataset_merged ∪ dataset_4（882 + 702 − 664 = 920 ✓）。dataset_5 为再生成版：∩d4 = 641（话语全改、intents 零变动）+ 新增 178（含旧 merged 系 16 噪声中的 14 条重新入集）− 删除 61。

## 用户画像

- `dataset/users.jsonl`：v1 配套画像。
- `dataset_merged/users_3000.jsonl`（3000 用户）：dataset_2 / dataset_3 / dataset_4 / dataset_merged / dataset_merged_2.0 共用，已验证 merged_2.0 全部 847 个 user_id 有画像。

## dataset_6_clean（2026-09-10/11 建，泛化性测试集）

- 原始 438 行中 201 个 session_id 各有两份**完全不同的渲染**（同 id 两份不同对话，非版本迭代而是 id 冲突）→ 去重策略 = render_meta 优先（含 faithfulness_soft_passes 质检软通过记录）→ **237 唯一 session**，全量 test（`data/split_d6_full.json`，无 train）。
- 无独立 users 文件（`users_3000.jsonl` 从 d5 拷贝入 dataset_6_clean/，全量 3000 条覆盖 session 涉及的 222 个 uid）；**render_model = qwen3.8-flash**（生成器换模型，d1~d5 时代为确定性模板/旧模型）。
- find_noise R1–R5 扫描 → 3 个 R5-A 问题样本剔除单列（S-000106/210/228，record 选择 GT 错位）：`data/_noise_exclude_d6_scan.json`；参评 234。
- 配套：`data/dataset_6_clean/`（17 文件）、`data/split_d6_full.json`、`data/case_bank_train_d6.jsonl`（建而未用于检索——检索库保持 d5 casebank，用户拍板 d6 纯测试集）、`data/embed_cache_d6/`（未用）。
- 评估配置演进：`configs/config_d6_qwen_plus_idproto.json`（基线）→ `config_d6_qwen_plus_candmap.json`（+candidate_map）→ `config_d6_qwen_plus_pendapp.json`（+pending_app_rule）→ `config_d6_qwen_plus_time.json`（+六时间开关，v27.0）→ **`config_d6_qwen_plus_best.json`（当前最佳 = v27.0 终版：candmap + pending_app_rule + multi_instance_key + 六时间开关全开）**。
- 评估里程碑（mi_on 口径，session/turn）：v26.2c 基线 84.19%/87.27% → candmap 87.18%/88.48% → v26.3-p1 88.46%/89.49%（双跑均值）→ v26.3-p3 88.03%/89.90% → **v27.0 92.74%/93.54%（状态机 86.08%）**。
- ⚠️ 换数据集三件套必须一起设：`DST_DATASET` + `--split` + `--noise_file`（run1 曾漏 DST_DATASET 致静默错配数字作废）。
- **数据侧已知问题（v27.0 错误分析上报，2026-09-20）**：①画像"常用开始时段"与"常用保障时长"数值不一致 3 用户（S-000063/165/237 的 U-0066/0166/0237，GT 一律跟随时段）；②S-000066 duration=180min 为 GT 骨架设计字段、话语从未明说（T2-3 矛盾轮连带），模型不可达；③S-000156 turn2 start GT 保 OCR 字面"14时3G分"（T3-2 错误值轮设计口径，模型规范化反被判错，由 correction_credit 豁免）；④S-000173 "19点70分"同类字面噪声。

## dataset_930（2026-10 建，新一代泛化性测试集）

- **4455 sessions**（16 模板 ×2 生成批次 gena-/genb- 前缀），**全测试集无 split**（`data/split_d930_full.json` 仅 test_sessions）；**2264 用户**，`users_3000.jsonl` 从 d6 拷入全覆盖；**render_model = qwen3.8-flash**（与 d6 同代生成器）。
- 话术风格与 d6 明显代差：候选指称从"第N条"变为"候选N"主形态、svc+日期逗号切开、复合尾句（"其他就按我这次说的"）等新变体——持续穿透 d6 时代正则（成为泛化性攻坚的主要素材）。
- find_noise R1–R5 全量扫描（适配脚本 `tools/_find_noise_d930.py`，画质映射从全量 source_ref 还原）：**噪声 91 个（2.04%,高于 d6 的 1.3%）**,R5-C×60/R5-A×18/R2×9/R3×3/R4×1 → `data/_noise_exclude_d930_scan.json`,此后评估默认剔除。
- 固定评估子集（分层 gena/genb×模板双维,可复现）：
  - **`data/split_d930_s444.json`**（seed42,444 sessions,剔噪 436 参评）
  - **`data/split_d930_s444b.json`**（seed7,与 s444 互斥,444 sessions,剔噪 435 参评）
- 评估里程碑（session/turn,qwen3.8-27b 剔噪口径）：首跑有效段 92.89%/93.51%（3065/4455,余 1390 API 欠费空分）→ 444 子集迭代（v2731 92.34% → v28 92.43%(92.66%@q38) → **v28.1 92.66%/94.15%**）→ s444b 交叉验证 92.41%/93.42% → **合并 888 抽样定案 92.54%/93.79%（状态机 90.81%@s444）**。
- 配置：`configs/config_d6_qwen38_27b_v273.json`（q38 全开关）；qwen-plus 用 `config_d6_qwen_plus_best.json`。
- **数据侧已知问题**：R5 系噪声占 86%（渲染器对"取最近"规则偏离频繁）；X-2 存在占位符泄漏变体（"互斥候选39"型,genb-S-002269）与 cancelled 无话语信号（gena-S-000124,GT I3 cancelled 但 U3 无取消语）。
- 评估产物谱系：eval_dual_v273_d930_q38_run1（首跑）→ eval_dual_v2731/2732/2733_d930_s444(_run2) → eval_dual_v274~v2771_d930_s444(_plus/_run2) → **eval_dual_v28(_v281)_d930_s444_{plus,q38}（定案）+ eval_dual_v281_d930_s444b_q38（交叉验证）**；错误逐例报告 `docs/report_errors_d930_v281.md`（65 例）。
