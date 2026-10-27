import io

# 1) report.md: 头部方法栈 + 成绩表行 + bullet
p='docs/report.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('｜ 当前方法栈 **v27.2**（+候选模式 candidate/select/cancel；',
            '｜ 当前方法栈 **v27.2.1**（+QUALITY HINT 画质条目高亮；+候选模式 candidate/select/cancel；')
old='| **v27.2（+ 候选模式：t1 列 candidate 旁路候选→t2 select 落地/cancel→消歧 1a/1b 分支，三跑均值）** | **93.16%**（94.87/91.45/93.16） | **94.75%**（95.96/93.74/94.55） | **d6 最佳**（vs v27.0 +0.42pp/+1.21pp；稳救 4（T3-3 靶区）稳破 3 净+1；run2 暴露分支二义→harness 侧判定修复；稳破=画质表遵从不稳×2/改口 defer 复合边界） |'
new=old+'\n| **v27.2.1（+ QUALITY HINT：话语 app/画质词查标准表高亮注入）** | 定点验证 5/5 修复 | — | ✅ 并入 v27.2 采纳（表遵从波动用条目高亮治，schema.QUALITY_MAP 79 条；全 case 平等不挑反常识） |'
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('report.md ok')

# 2) report_flow.md: 开关表行
p='docs/report_flow.md'
s=io.open(p,encoding='utf-8').read()
old='| `record_selector.enabled` | 记录指代预判'
new='''| `quality_hint.enabled` | 画质条目高亮：话语 app/画质词查 schema.QUALITY_MAP（79 条标准表，源 resolution_quality_map.md）注入精确值；话语无 app 时用 record_rule 定位记录的 app/svc 查表 | pipeline QUALITY HINT 块 + schema.QUALITY_MAP | **true(best)** | v27.2.1 | ✅ 采纳（5 失败 case 定点验证 5/5；治表遵从波动，全 case 平等不挑反常识） |
| `record_selector.enabled` | 记录指代预判'''
assert s.count(old)==1
s=s.replace(old,new)
s=s.replace('## 4. 独立消融开关（全量门控清单，v13.0 起 13 个 → v27.2 至 23 个）',
            '## 4. 独立消融开关（全量门控清单，v13.0 起 13 个 → v27.2.1 至 24 个）')
io.open(p,'w',encoding='utf-8').write(s)
print('report_flow ok')

# 3) codeguide.md: schema 行 + pipeline 行 + 版本头
p='docs/codeguide.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('> 更新：2026-09-23 ｜ 对应代码版本：v27.2（方法栈 v27.0 + 候选模式；',
            '> 更新：2026-09-24 ｜ 对应代码版本：v27.2.1（方法栈 v27.2 + QUALITY HINT；')
old2='| `candidate_book.py`'
new2='''| `schema.py` QUALITY_MAP | 79 | v27.2.1 标准画质映射常量（源 resolution_quality_map.md，数据侧单源）；QUALITY HINT 查表用 | `QUALITY_MAP` |
| `candidate_book.py`'''
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('codeguide ok')

# 4) README.md: 头部 + 特性 bullet
p='README.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('（代码 v27.2，方法栈 v27.0+候选模式；入库包由','（代码 v27.2.1，方法栈 v27.2+QUALITY HINT；入库包由')
old3='- **时间锚点+明说时间证据注入（v27.0）**'
new3='''- **QUALITY HINT 画质条目高亮（v27.2.1）**：`schema.QUALITY_MAP`（79 条标准映射，源 `resolution_quality_map.md`）+ 话语 app/画质词确定性查表注入——治反常识条目（高清@淘宝=1080p）的表遵从波动，全 case 平等
- **时间锚点+明说时间证据注入（v27.0）**'''
assert s.count(old3)==1
s=s.replace(old3,new3)
io.open(p,'w',encoding='utf-8').write(s)
print('README ok')
