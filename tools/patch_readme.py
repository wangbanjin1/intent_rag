import io
p='README.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('（代码 v27.0，方法栈 v26.3+六时间开关；入库包由','（代码 v27.2，方法栈 v27.0+候选模式；入库包由')
s=s.replace('**dataset_6_clean（当前主战场）最佳 = v27.0：session 92.74% (217/234) / turn 93.54% (463/495) / 状态机 86.08%（ 六时间开关全开，mi_on 口径，vs v26.3-p3 +4.71pp/+3.64pp）**。',
'**dataset_6_clean（当前主战场）最佳 = v27.2：session 94.87% (222/234) / turn 95.96% (475/495) / 状态机 88.28%（candidate_mode 开，mi_on 口径，vs v27.0 +2.13pp/+2.06pp、累计 vs v26.3-p3 +6.84pp）**。')
old='| **v27.0 fix3（+ 六时间开关：日期表/证据/矛盾保字面/画像时段优先/明说>record/候选不落槽，当前最佳）** | **92.74% (217/234)** | **93.54% (463/495)** | **d6 最佳**（vs v26.3 +4.71pp/+3.64pp，time_wrong 19→5；迭代链 run1 81.20%→fix_run1 91.88%→fix2→fix3；回退 6 经基线五跑核查无机制性回退） |'
new=old+'\n| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支）** | **94.87% (222/234)** | **95.96% (475/495)** | **d6 最佳**（vs v27.0 +2.13pp/+2.06pp；救 10 破 5 逐一核查零机制性；T3-3 消歧家族全转正） |'
assert s.count(old)==1
s=s.replace(old,new)
s=s.replace('- **时间锚点+明说时间证据注入（v27.0）**','- **时间锚点+明说时间证据注入（v27.0）**')
io.open(p,'w',encoding='utf-8').write(s)
print('README updated')
