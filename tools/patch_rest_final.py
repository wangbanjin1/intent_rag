import io

# README: 成绩表行 + 当前最佳声明
p='README.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**dataset_6_clean（当前主战场）最佳 = v27.2：session 93.16%（三跑均值 94.87/91.45）/ turn 94.85%（95.96/93.74）/ 状态机 87.79%（candidate_mode 开，mi_on 口径，vs v27.0 +0.42pp/+1.31pp）**。',
'**dataset_6_clean（当前主战场）最佳 = v27.2.1：session 93.16%（三跑均值 94.87/91.45/93.16）/ turn 94.75% / 状态机 87.79%（candidate_mode + quality_hint 开，mi_on 口径，vs v27.0 +0.42pp/+1.21pp）**。')
old2='| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，三跑均值）** | **93.16%**（94.87/91.45/93.16） | **94.75%**（95.96/93.74/94.55） | **d6 最佳**（vs v27.0 +0.42pp/+1.21pp；稳救 4（T3-3 靶区）稳破 3 净+1） |'
new2=old2+'\n| **v27.2.1（+ QUALITY HINT 画质条目高亮）** | 定点验证 5/5 修复 | — | 并入 v27.2 采纳（治反常识条目表遵从波动，全 case 平等） |'
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('README ok')

# codeguide: schema QUALITY_MAP 行
p='docs/codeguide.md'
s=io.open(p,encoding='utf-8').read()
old3='| `candidate_book.py`'
new3='''| `schema.py` QUALITY_MAP | 79 | v27.2.1 标准画质映射常量（源 resolution_quality_map.md，数据侧单源）；QUALITY HINT 查表用 | `QUALITY_MAP` |
| `candidate_book.py`'''
assert s.count(old3)==1
s=s.replace(old3,new3)
s=s.replace('> 更新：2026-09-23 ｜ 对应代码版本：v27.2（方法栈 v27.0 + 候选模式；','> 更新：2026-09-24 ｜ 对应代码版本：v27.2.1（方法栈 v27.2 + QUALITY HINT；')
io.open(p,'w',encoding='utf-8').write(s)
print('codeguide ok')

# report_flow: 开关行
p='docs/report_flow.md'
s=io.open(p,encoding='utf-8').read()
old4='| `record_selector.enabled` | 记录指代预判'
new4='''| `quality_hint.enabled` | 画质条目高亮：话语 app/画质词查 schema.QUALITY_MAP（79 条标准表）注入精确值；话语无 app 时用 record_rule 定位记录的 app/svc 查表 | pipeline QUALITY HINT 块 + schema.QUALITY_MAP | **true(best)** | v27.2.1 | ✅ 采纳（5 失败 case 定点验证 5/5；治表遵从波动，全 case 平等） |
| `record_selector.enabled` | 记录指代预判'''
assert s.count(old4)==1
s=s.replace(old4,new4)
s=s.replace('## 4. 独立消融开关（全量门控清单，v13.0 起 13 个 → v27.2 至 23 个）','## 4. 独立消融开关（全量门控清单，v13.0 起 13 个 → v27.2.1 至 24 个）')
io.open(p,'w',encoding='utf-8').write(s)
print('report_flow ok')

# story.md: 10.5 追加画质段落 + 记分牌
p='docs/story.md'
s=io.open(p,encoding='utf-8').read()
old5='## 11. 明确失败并封死的路线（负结果同样是资产）'
new5='''另一个遗留问题也在 v27.2.1 收尾：三个 session（淘宝看直播"高清"）稳定错在同一处——画质表里"高清=1080p"是反常识条目（高于标清的 720p），模型总倾向落默认 720p。核对发现 prompt 表与数据侧标准表完全一致，错不在表而在**查表遵从**。修复用"条目高亮"：话语提到哪个 app 的哪个画质词，系统就把标准表对应行放大注入。5 个失败 case 定点验证全部转正。方法上与候选模式同族——都是"把模型该看到的正确信息放大"，而不是教它新规则。

## 11. 明确失败并封死的路线（负结果同样是资产）'''
assert s.count(old5)==1
s=s.replace(old5,new5,1)
s=s.replace('93.16% ─ v27.2   候选模式（候选列举/select 落地，三跑均值）','93.16% ─ v27.2.1 候选模式+画质高亮（候选列举/select 落地，三跑均值）')
io.open(p,'w',encoding='utf-8').write(s)
print('story ok')

# experience/11: 追加画质教训
p='docs/experience/11_time_rules.md'
s=io.open(p,encoding='utf-8').read()
s=s.rstrip()+'''

## 7. 增补（v27.2.1，2026-09-24）：表遵从波动的治法——条目高亮而非改表

"高清@淘宝/看直播=1080p"是反常识条目（高清>标清720p），模型倾向落全局默认 720p——三个 session 稳定错在同处，且基线复跑同样错。核对后 prompt 表与标准表 diff=0，**错不在表而在遵从**。修复 = QUALITY HINT：话语提及 app/画质词时，把标准表对应条目以独立段落放大注入（schema.QUALITY_MAP 79 条常量，全 case 平等不挑反常识），5/5 定点验证转正。

两条补充教训：
1. **先核对表，再修遵从**：出错时第一个直觉是"表错了"，实际 diff=0——如果直接改表会引入数据泄漏且破坏数据侧单源。
2. **"放大要看的行"属于顺行为面的注入**：与 v27.1 失败的定位类注入不同，QUALITY HINT 不与任何已验证机制竞争——它只是把系统 prompt 里已有表格的对应行在 task prompt 里再强调一次，模型查表行为本来就存在（时对时错），高亮是提高信噪比而非改变行为模式。
'''
io.open(p,'w',encoding='utf-8').write(s)
print('experience ok')
