import io
# CHANGELOG v27.2 条目补充此修复
p='docs/CHANGELOG.md'
s=io.open(p,encoding='utf-8').read()
old='**涉及文件**：candidate_book.py（新）/python_state.py（candidate 剥离）'
new='''**附带修复（S-000012 型，双跑稳破残留归因时发现）**：无头段兜底条件 `len(prev_states)==1` 未考虑本轮 cancel——"cancel I2 + 无头补参段"场景剩余意图唯一但兜底失效，无头 delta 落入 `__nokey__` 丢弃（run2/run3 的 S-000012 即此，run1 模型恰好输出了 intent 头所以对）。修复 = 剩余数按"prev - 本轮 cancel"计算。非 candidate 相关，属 harness 存量边界 bug。

**涉及文件**：candidate_book.py（新）/python_state.py（candidate 剥离 + 无头段兜底剩余数修正）'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('changelog updated')
