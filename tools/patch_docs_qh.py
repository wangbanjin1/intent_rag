import io
p='docs/CHANGELOG.md'
s=io.open(p,encoding='utf-8').read()
marker='**涉及文件**：candidate_book.py（新）/python_state.py（candidate 剥离 + 无头段兜底剩余数修正）'
add='''**画质表遵从修复（2026-09-24，v27.2.1 QUALITY HINT）**：用户指出标准画质表在 `resolution_quality_map.md`（数据侧单源）。核对：prompt 表与标准表 **diff=0**（75 条全一致），5 个失败 case 的错误不在表而在【查表遵从波动】——尤其"高清@淘宝/看直播=1080p"反常识条目（高清>标清720p），模型倾向落默认 720p。修复 = `schema.QUALITY_MAP`（79 条标准映射常量）+ QUALITY HINT 注入（话语提及 app/画质词时确定性查表高亮精确值；话语无 app 但 record_rule 定位了记录时用记录的 app/svc 查表，S-000114 型）+ features `quality_hint` 开关。**5 个失败 case 定点验证：S-000095/114/141/211/164 全部 PASS（5/5 修复）**。QUALITY HINT 与 v27.2 候选模式一并启用进 best config。

**涉及文件**：candidate_book.py（新）/python_state.py（candidate 剥离 + 无头段兜底剩余数修正）'''
assert s.count(marker)==1
s=s.replace(marker,add)
io.open(p,'w',encoding='utf-8').write(s)
print('changelog updated')

# report_method 追加
p='docs/report_method.md'
s=io.open(p,encoding='utf-8').read()
old='| **v27.2** | **09-23** |'
new='''| v27.2.1 | 09-24 | 画质表遵从波动（高清@淘宝=1080p 反常识条目模型落默认 720p） | QUALITY HINT：schema.QUALITY_MAP 标准表（79 条）+ 话语 app/画质词确定性查表高亮注入（全 case 平等，不挑反常识） | 5 失败 case 定点验证 5/5 修复 ✅（并入 v27.2 采纳） | 表遵从波动用"条目高亮"治——不是改表（表本来就对）而是把模型该看的行放大 |
| **v27.2** | **09-23** |'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('report_method updated')

# memory
p=r'C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\project_qwen_intent_rag_dataset.md'
s=io.open(p,encoding='utf-8').read()
old='核心教训：候选列举是模型输出的必要部分（驱动下游客服回复），顺信息流方向的注入有效、逆行为面方向无效。'
new='''核心教训：候选列举是模型输出的必要部分（驱动下游客服回复），顺信息流方向的注入有效、逆行为面方向无效。
**画质表遵从修复（v27.2.1，2026-09-24）**：5 失败 case（095/114/141/211/164）根因=表遵从波动（prompt 表与标准表 diff=0，模型对"高清@淘宝/看直播=1080p"反常识条目倾向落默认 720p）。修复=schema.QUALITY_MAP（79 条标准表，源 resolution_quality_map.md）+ QUALITY HINT 注入（话语 app/画质词查表高亮；话语无 app 时用 record_rule 定位的记录 app/svc 查表）。**5/5 定点验证修复**，quality_hint 开关已入 best config。'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('memory updated')
