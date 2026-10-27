import io
p='docs/CHANGELOG.md'
s=io.open(p,encoding='utf-8').read()
entry='''

---

## v27.2 候选模式采纳定案（2026-09-23，T3-3 查询-选择-落地链路，d6 新最佳 94.87%/95.96%）

**背景**：用户复盘 S-000040 确认三段失效链——t1 模型在两条候选(爱奇艺/拼多多)中自作主张"取最近"new 了拼多多 → 爱奇艺意图从未存在 → t2 选爱奇艺时只能在错误占位上补参数 → t3 被 pending-app 规则锁死输出 none。提出正确形态：**候选列举本身是模型输出的一部分**（后续"模型输出驱动客服回复"形态下，t1 不输出候选则下游无法生成消歧话术）。

**GT 分型核验**（13 个 T3-3 全量）：A 纯查询型 10 例（t1 只落时间字段，app 悬空）+ B 明说型 3 例（056/123/192，只悬空 rtt）。澄清用户疑点：客服话术确实只列部分候选（S-000192 列 3/画像 4），但 GT 选择基准=话术序（v21.1 实证 100%），列举缺口不直接致命。

**实现**：新 `candidate_book.py`（CandidateBook 旁路账本 + parse_candidates + select_phrase）；python_state candidate 段剥离（不入 JGA 主链）；pipeline `_session_is_t33` 信号域 + CANDIDATE MODE 双分支注入（列举轮/选择轮）；prompt_builder 消歧规则按"桩意图 app 是否为空"分支（空→记录 app/svc/res/rtt 全量落地；非空→只补悬空字段严禁改写身份）；session_eval 记账+豁免。config `candidate_mode` 开关。

**迭代过程**（T3-3 回放驱动，每轮按模型原话归因）：初版净负（修1破4）→ 信号域收窄至 T3-3（S-000116 T2-2 误伤确认）→ 1a/1b 分支（S-000192 B 型身份被改写确认）→ 1b 措辞加固（S-000123"身份不符必须改写"误读确认）→ 最终 T3-3 回放 修3破0。

**全量定案（run1 单跑 + 破窗逐一核查）**：session **94.87% (222/234)** / turn **95.96% (475/495)** / 状态机 88.28%，vs v27.0 **+2.13pp/+2.06pp**（超 ±2pp 噪声带）。救 10（含全部 T3-3 目标家族 040/056/072/140/174/192/246 等）破 5；破 5 逐一核查：042/164/211 = "高清@淘宝/看直播=1080p"反常识映射表遵从波动（基线复跑同样错，非 candidate 因果，两配置同错）；149/151 = cand 配置复跑转正（纯波动）。**实际机制性破窗 = 0，采纳**（config_d6_qwen_plus_best.json 更新，candidate_mode+record_selector 开）。

**涉及文件**：candidate_book.py（新）/python_state.py（candidate 剥离）/pipeline.py（_session_is_t33+双分支注入）/prompt_builder.py（cand_mode_hint/cand_landing+消歧 1a/1b 分支）/session_eval.py（CandidateBook 记账）/features.py（+candidate_mode）/candidate_selftest.py（新）/tools/replay_candidate.py（新）/configs/config_d6_qwen_plus_cand.json。
'''
s=io.open(p,encoding='utf-8').read()+entry
io.open(p,'w',encoding='utf-8').write(s)
print('changelog updated')

p='docs/report_experiments.md'
s=io.open(p,encoding='utf-8').read()
old='| **v27.0 fix3（终版，d6 当前最佳）**'
new='''| v27.1 record_selector（三轮否定关闭） | 81.20% / 88.46% / 89.32% | — | ↩️ 路线关闭（扰动面>靶区；详见 CHANGELOG v27.1） |
| **v27.2 候选模式（candidate/select/cancel，d6 当前最佳）** | **94.87% (222/234)** | **95.96% (475/495)** | ✅ **采纳**（状态口径 88.28%；**+2.13pp/+2.06pp**；救 10（T3-3 家族全转正）破 5 逐一核查 0 机制性；消歧规则 1a/1b 分支（空桩落地/非空补悬空）；config_d6_qwen_plus_best.json） |
| **v27.0 fix3（v27.2 前最佳）**'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('report_experiments updated')
