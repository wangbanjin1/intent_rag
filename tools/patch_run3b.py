import io
# report.md / README / story / flow / method: 均值改三跑 + 翻转说明
p='docs/report.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**mi_on 口径** session **93.16%**（双跑均值 94.87/91.45）/turn **94.85%**（95.96/93.74）（v27.2，状态机 87.82%；vs v27.0 +0.42pp/+1.31pp、累计 vs v26.3-p3 +5.13pp）',
'**mi_on 口径** session **93.16%**（三跑均值 94.87/91.45/93.16）/turn **94.75%**（95.96/93.74/94.55）（v27.2，状态机 87.79%；vs v27.0 +0.42pp/+1.21pp、累计 vs v26.3-p3 +5.13pp）')
s=s.replace('| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，双跑均值）** | **93.16%**（94.87/91.45） | **94.85%**（95.96/93.74） | **d6 最佳**（vs v27.0 +0.42pp/+1.31pp；稳救 5（T3-3 靶区稳定复现）稳破 4 净+1；稳破=画质表遵从不稳×2/改口 defer 复合边界/波动） |',
'| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，三跑均值）** | **93.16%**（94.87/91.45/93.16） | **94.75%**（95.96/93.74/94.55） | **d6 最佳**（vs v27.0 +0.42pp/+1.21pp；稳救 4（T3-3 靶区）稳破 3 净+1；run2 暴露分支二义→harness 侧判定修复；稳破=画质表遵从不稳×2/改口 defer 复合边界） |')
io.open(p,'w',encoding='utf-8').write(s)
print('report.md updated')

p='README.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**dataset_6_clean（当前主战场）最佳 = v27.2：session 93.16%（双跑均值 94.87/91.45）/ turn 94.85%（95.96/93.74）/ 状态机 87.82%（candidate_mode 开，mi_on 口径，vs v27.0 +0.42pp/+1.31pp）**。',
'**dataset_6_clean（当前主战场）最佳 = v27.2：session 93.16%（三跑均值）/ turn 94.75% / 状态机 87.79%（candidate_mode 开，mi_on 口径，vs v27.0 +0.42pp/+1.21pp）**。')
s=s.replace('| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，双跑均值）** | **93.16%**（94.87/91.45） | **94.85%**（95.96/93.74） | **d6 最佳**（vs v27.0 +0.42pp/+1.31pp；稳救 5（T3-3 靶区稳定复现）稳破 4 净+1） |',
'| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，三跑均值）** | **93.16%**（94.87/91.45/93.16） | **94.75%**（95.96/93.74/94.55） | **d6 最佳**（vs v27.0 +0.42pp/+1.21pp；稳救 4（T3-3 靶区）稳破 3 净+1） |')
io.open(p,'w',encoding='utf-8').write(s)
print('README updated')

p='docs/report_method.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('✅ **双跑均值 93.16%/94.85%（+0.42pp/+1.31pp）采纳，d6 新最佳**（状态口径 87.82%；run1 单跑 94.87%；稳救 5 稳破 4 净+1，靶区 T3-3 稳定复现）',
'✅ **三跑均值 93.16%/94.75%（+0.42pp/+1.21pp）采纳，d6 新最佳**（状态口径 87.79%；run2 暴露 1a/1b 分支二义→判定上移 harness 侧；稳救 4 稳破 3 净+1，靶区 T3-3 稳定复现）')
io.open(p,'w',encoding='utf-8').write(s)
print('report_method updated')

p='docs/report_flow.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('✅ 采纳（双跑均值 93.16%/94.85%；T3-3 靶区稳救 5）','✅ 采纳（三跑均值 93.16%/94.75%；T3-3 靶区稳救 4；1a/1b 分支判定上移 harness）')
io.open(p,'w',encoding='utf-8').write(s)
print('report_flow updated')

p='docs/story.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**结果（双跑均值）：session 93.16% / turn 94.85%（vs v27.0 +0.42pp/+1.31pp），T3-3 消歧家族双跑一致转正（稳救 5 稳破 4 净+1）**；run1 单跑曾达 94.87%。',
'**结果（三跑均值）：session 93.16% / turn 94.75%（vs v27.0 +0.42pp/+1.21pp），T3-3 消歧家族稳救 4（040/056/174/192 三跑全对）**。双跑曾差 3.42pp，归因 = 消歧规则 1a/1b 分支交模型自判是二义的——修复 = 分支判定上移 harness 侧（桩意图 app 为空与否由系统核验后直接注入分支结论）。')
s=s.replace('93.16% ─ v27.2   候选模式（候选列举/select 落地，双跑均值；run1 单跑 94.87%）','93.16% ─ v27.2   候选模式（候选列举/select 落地，三跑均值）')
io.open(p,'w',encoding='utf-8').write(s)
print('story updated')

p=r'C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\project_qwen_intent_rag_dataset.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**d6 当前最佳 = v27.2（2026-09-23~24 双跑）：session 93.16%（94.87/91.45）/ turn 94.85%（95.96/93.74）/ 状态机 87.82%（config_d6_qwen_plus_best.json，candidate_mode 开，mi_on 口径）**。',
'**d6 当前最佳 = v27.2（2026-09-24 三跑）：session 93.16%（94.87/91.45/93.16）/ turn 94.75%（95.96/93.74/94.55）/ 状态机 87.79%（config_d6_qwen_plus_best.json，candidate_mode 开，mi_on 口径）**。')
s=s.replace('**双跑定案：均值 93.16%/94.85%（+0.42pp/+1.31pp，turn 超带边缘），稳救 5（040/056/075/174/192，T3-3 靶区稳定复现）稳破 4 净+1，维持采纳**。',
'**三跑定案：均值 93.16%/94.75%（+0.42pp/+1.21pp，turn 超带边缘），稳救 4（040/056/174/192，T3-3 靶区三跑全对）稳破 3 净+1，维持采纳**。run1/run2 差 3.42pp 的归因 = 消歧规则 1a/1b 分支交模型自判二义（072/123/246 各走错一支）→ **修复 = 分支判定上移 harness 侧**（选择轮提示注入确定性 a/b 结论），run3 验证稳救家族扩至 040/056/246。')
io.open(p,'w',encoding='utf-8').write(s)
print('memory updated')
