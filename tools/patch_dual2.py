import io
# report.md 头部+成绩表改双跑
p='docs/report.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**mi_on 口径** session **94.87%**/turn **95.96%**（v27.2 run1，状态机 88.28%；vs v27.0 +2.13pp/+2.06pp、累计 vs v26.3-p3 +6.84pp）',
'**mi_on 口径** session **93.16%**（双跑均值 94.87/91.45）/turn **94.85%**（95.96/93.74）（v27.2，状态机 87.82%；vs v27.0 +0.42pp/+1.31pp、累计 vs v26.3-p3 +5.13pp）')
s=s.replace('| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支）** | **94.87% (222/234)** | **95.96% (475/495)** | **d6 最佳**（vs v27.0 +2.13pp/+2.06pp；救 10 破 5 逐一核查零机制性；T3-3 消歧家族全转正） |',
'| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，双跑均值）** | **93.16%**（94.87/91.45） | **94.85%**（95.96/93.74） | **d6 最佳**（vs v27.0 +0.42pp/+1.31pp；稳救 5（T3-3 靶区稳定复现）稳破 4 净+1；稳破=画质表遵从不稳×2/改口 defer 复合边界/波动） |')
io.open(p,'w',encoding='utf-8').write(s)
print('report.md updated')

p='README.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**dataset_6_clean（当前主战场）最佳 = v27.2：session 94.87% (222/234) / turn 95.96% (475/495) / 状态机 88.28%（candidate_mode 开，mi_on 口径，vs v27.0 +2.13pp/+2.06pp、累计 vs v26.3-p3 +6.84pp）**。',
'**dataset_6_clean（当前主战场）最佳 = v27.2：session 93.16%（双跑均值 94.87/91.45）/ turn 94.85%（95.96/93.74）/ 状态机 87.82%（candidate_mode 开，mi_on 口径，vs v27.0 +0.42pp/+1.31pp）**。')
old2='| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支）** | **94.87% (222/234)** | **95.96% (475/495)** | **d6 最佳**（vs v27.0 +2.13pp/+2.06pp；救 10 破 5 逐一核查零机制性；T3-3 消歧家族全转正） |'
new2='| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，双跑均值）** | **93.16%**（94.87/91.45） | **94.85%**（95.96/93.74） | **d6 最佳**（vs v27.0 +0.42pp/+1.31pp；稳救 5（T3-3 靶区稳定复现）稳破 4 净+1） |'
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('README updated')

# report_method 演进表 v27.2 行改双跑
p='docs/report_method.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('✅ **94.87%/95.96%（+2.13pp/+2.06pp）采纳，d6 新最佳**（状态口径 88.28%；救 10 破 5 全波动零机制性）',
'✅ **双跑均值 93.16%/94.85%（+0.42pp/+1.31pp）采纳，d6 新最佳**（状态口径 87.82%；run1 单跑 94.87%；稳救 5 稳破 4 净+1，靶区 T3-3 稳定复现）')
io.open(p,'w',encoding='utf-8').write(s)
print('report_method updated')

# story.md 记分牌与 10.5 数字
p='docs/story.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**结果：session 94.87% / turn 95.96%（+2.13pp/+2.06pp），T3-3 消歧家族全部转正，救 10 破 5 且破窗逐一核查零机制性**（3 例是反常识画质映射的遵从波动、2 例复跑自愈）。',
'**结果（双跑均值）：session 93.16% / turn 94.85%（vs v27.0 +0.42pp/+1.31pp），T3-3 消歧家族双跑一致转正（稳救 5 稳破 4 净+1）**；run1 单跑曾达 94.87%。')
s=s.replace('94.87% ─ v27.2   候选模式（候选列举/select 落地，+2.13pp）','93.16% ─ v27.2   候选模式（候选列举/select 落地，双跑均值；run1 单跑 94.87%）')
io.open(p,'w',encoding='utf-8').write(s)
print('story updated')

# report_flow 开关表行改双跑
p='docs/report_flow.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('✅ 采纳（94.87%/95.96%；T3-3 家族救 10）','✅ 采纳（双跑均值 93.16%/94.85%；T3-3 靶区稳救 5）')
io.open(p,'w',encoding='utf-8').write(s)
print('report_flow updated')
