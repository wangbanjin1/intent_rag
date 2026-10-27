import io
# report_method: v27.2 row
p='docs/report_method.md'
s=io.open(p,encoding='utf-8').read()
old='| **v27.0** | **09-20** |'
new='''| v27.1 | 09-23 | record 选错四病灶 | record_selector 生成前判定树注入 | 三轮 81.20/88.46/89.32% ↩️ 关闭 | 扰动面>靶区；教训=注入勿对抗已验证行为面 |
| **v27.2** | **09-23** | T3-3 查询-选择-落地链断裂（S-000040 型：t1 抢跑占位→t2 身份断裂→t3 摆烂） | **候选模式**：t1 全部命中记录列 `# candidate:` 段（旁路不入 JGA）→ t2 select 解引用落地/cancel → 消歧规则按"桩 app 空/非空"1a/1b 分支；CandidateBook 旁路账本 | ✅ **94.87%/95.96%（+2.13pp/+2.06pp）采纳，d6 新最佳**（状态口径 88.28%；救 10 破 5 全波动零机制性） | 候选列举是模型输出的必要部分（驱动下游客服回复）；信息流完整性与判分技巧统一 |
| **v27.0** | **09-20** |'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('report_method updated')

# report.md header + bullet
p='docs/report.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace(
 '｜ 当前方法栈 **v27.0**（+时间锚点/明说时间证据/四条时间口径规则，v26.3 基础上）',
 '｜ 当前方法栈 **v27.2**（+候选模式 candidate/select/cancel；v27.0 时间锚点/明说时间证据/四条时间口径规则，v26.3 基础上）')
s=s.replace(
 '**mi_on 口径** session **92.74%**/turn **93.54%**（v27 fix3，状态机 86.08%；vs v26.3-p3 +4.71pp/+3.64pp）',
 '**mi_on 口径** session **94.87%**/turn **95.96%**（v27.2 run1，状态机 88.28%；vs v27.0 +2.13pp/+2.06pp、累计 vs v26.3-p3 +6.84pp）')
io.open(p,'w',encoding='utf-8').write(s)
print('report.md updated')

# codeguide
p='docs/codeguide.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('> 更新：2026-09-20 ｜ 对应代码版本：v27.0（方法栈 v26.3 + 六时间开关；d6 最佳配置 config_d6_qwen_plus_best.json 已含六时间开关全开，mi_on 口径，session 92.74%/turn 93.54%）',
'> 更新：2026-09-23 ｜ 对应代码版本：v27.2（方法栈 v27.0 + 候选模式；d6 最佳配置 config_d6_qwen_plus_best.json 已含 candidate_mode，mi_on 口径，session 94.87%/turn 95.96%）')
old2='| `record_selector.py`'
new2='''| `candidate_book.py` | ~120 | v27.2 候选模式：CandidateBook 旁路账本（candidate 段解析/记账/select 落地/cancel 移除，不入 JGA） | `parse_candidates` `CandidateBook` |
| `record_selector.py`'''
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('codeguide updated')

# README
p='README.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('（代码 v27.0，方法栈 v26.3+六时间开关','（代码 v27.2，方法栈 v27.0+候选模式')
old3='| **v27.0（+ 六时间开关：日期表/证据/矛盾保字面/画像时段优先/明说>record/候选不落槽，当前最佳）** | **92.74% (217/234)** | **93.54% (463/495)** | **d6 最佳**（vs v26.3 +4.71pp/+3.64pp，time_wrong 19→5；迭代链 run1 81.20%→fix_run1 91.88%→fix2→fix3；回退 6 经基线五跑核查无机制性回退） |'
new3=old3+'\n| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支）** | **94.87% (222/234)** | **95.96% (475/495)** | **d6 最佳**（vs v27.0 +2.13pp/+2.06pp；救 10 破 5 逐一核查零机制性；T3-3 消歧家族全转正） |'
assert s.count(old3)==1
s=s.replace(old3,new3)
io.open(p,'w',encoding='utf-8').write(s)
print('README updated')
