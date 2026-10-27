import io
p='docs/report_experiments.md'
s=io.open(p,encoding='utf-8').read()
old='| **v27.2 三跑均值（d6 当前最佳）**'
new='''| v27.2.1 QUALITY HINT（画质条目高亮） | 定点验证 5/5 修复 ✅（并入 v27.2） | — | 表遵从波动用条目高亮治（schema.QUALITY_MAP 79 条标准表；话语 app/画质词查表注入；全 case 平等不挑反常识） |
| **v27.2 三跑均值（d6 当前最佳）**'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('report_experiments updated')
