# Project Memory & Architecture Guide (`qwen_intent_rag`)

本文件为 `D:\coding\qwen_intent_rag`（远端仓库：`https://github.com/wangbanjin1/intent_rag.git`，主分支：`main`）的项目级记忆与开发规范。

---

## 1. 项目定位与环境配置

- **核心职责**：通信网络质量保障场景下的**多轮多意图对话状态跟踪（DST）与认知记忆联合评测系统**。
- **Python 解释器**：`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`
- **常用模型配置**：
  - 意图理解主力模型：`qwen3.7-plus` / `qwen-plus` / `qwen3.8-flash` / `qwen3.8-27b`
  - 检索向量模型：`text-embedding-v4` / `text-embedding-v3`
- **关联上下游仓库**：
  - 记忆系统引擎：`D:\coding\memory_system`（通过本仓 `memory_bridge.py` 集成）
  - 认知记忆评测数据集：`D:\coding\cross_memory_data\data\cognitive_samples`

---

## 2. 核心架构与模块职责

1. **`pipeline.py` (`MultiIntentPipeline`)**：
   - 核心编排流水线：动态 Few-shot 检索 (`retriever.py`) → 记忆桥接与规则触发 (`memory_bridge.py` / `time_extractor.py`) → Prompt 组装 (`prompt_builder.py`) → LLM 生成 Python 状态更新程序 → PAR (`# summary`) 校验回路 → 受限沙箱执行 (`python_state.py`) → 规则校验 (`validator.py`)。
2. **`memory_bridge.py` (`MemoryBridge`)**：
   - 连接 `D:\coding\memory_system` 与意图理解流水线的核心桥梁。
   - 负责在每轮对话前调用记忆系统检索**事实记忆**（正交拼装 `preferred_value` / `scene_preference` / `periodic_preference` / `expression`）、**工作记忆**（跨会话历史工单回溯与消歧）与**经验记忆**（快路径自动预热/参数补全 vs. 慢路径候选澄清），并转化为结构化提示段注入 Prompt。
3. **`time_extractor.py` & `validator.py`**：
   - `time_extractor.py`：预解析对话中的日期表、相对时间词、时段继承与跨夜滚动，生成 `TIME ANCHOR / EVIDENCE` 注入生成侧。
   - `validator.py`：校验 7 大核心槽位（`application_name, service_name, resolution, rtt, start_timestamp, end_timestamp, duration`），执行时间三字段“知二推一”自洽计算。
4. **`session_eval.py` & `error_report.py`**：
   - 逐轮真实状态透传评估入口（计算 Session JGA、Turn JGA 与意图状态机准确率），支持输出交互式 HTML 评测报告。

---

## 3. 项目工程红线与评测规范 (`Rule + Why + How to apply`)

1. **严禁 GT 泄露与样本硬编码（Zero Hardcoding）**：
   - `memory_bridge.py`、`time_extractor.py`、`python_state.py` 中严禁写死任何针对特定测试样本的时间段（如 `15:00-17:00`）、应用名、画质（如硬编码过滤 `1080p`）或固定 `duration`（如 `120min`）。
   - **Why:** 硬编码字面量属于测试集作弊，会掩盖记忆检索或桥接层的真实缺陷，导致跨数据集评测崩盘。
   - **How to apply:** 所有工单时长、预热偏移和候选参数必须从检索到的记忆卡片、工作记录（`working_records`）和用户当前话语中动态推导。

2. **严禁后处理粗暴覆盖模型预测（Context Over Post-hoc Patching）**：
   - 禁止在后处理脚本中用脆弱的正则或规则强行篡改模型的 `pred` 槽位输出。
   - **Why:** 后处理强行改值会割裂模型生成的 `# summary` 推理链与最终状态字典，且在复杂否定或改期语境下极易误伤正确预测。
   - **How to apply:** 坚持“计算给规则、理解给模型”，通过 `time_extractor.py` 与 `memory_bridge.py` 提供清晰的生成侧证据（Context / Hint）引导模型自主输出正确状态。

3. **如实汇报首轮评测基线与独立批次归档**：
   - 执行新批次评测时，必须先保存并汇报第一轮原始准确率与客观错误归因（区分数据集/GT 问题 vs. 记忆检索/桥接问题）。
   - **Why:** 私自改代码重跑会丢失基线对照，无法量化每次架构改动的真实收益与副作用。
   - **How to apply:** 每次评测结果存入独立的 `eval_results/run_XX_<tag>/` 子目录，生成支持按用户（Persona）与三层记忆类别下钻的交互式 HTML 报告。

---

## 4. 常用运行命令

```powershell
# 1. 离线自检（不调 LLM API）
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe selftest.py

# 2. 运行 dataset_6 基准评估
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe session_eval.py --config configs/config_d6_qwen_plus_best.json --split data/split_d6_full.json --noise_file data/_noise_exclude_d6_scan.json --tag d6_run --err_report
```
