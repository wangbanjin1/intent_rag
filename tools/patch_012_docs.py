import io
p='docs/CHANGELOG.md'
s=io.open(p,encoding='utf-8').read()
old='''**附带修复（S-000012 型，双跑稳破残留归因时发现）**：无头段兜底条件 `len(prev_states)==1` 未考虑本轮 cancel——"cancel I2 + 无头补参段"场景剩余意图唯一但兜底失效，无头 delta 落入 `__nokey__` 丢弃（run2/run3 的 S-000012 即此，run1 模型恰好输出了 intent 头所以对）。修复 = 剩余数按"prev - 本轮 cancel"计算。非 candidate 相关，属 harness 存量边界 bug。'''
new='''**附带修复（S-000012 型，双跑稳破残留归因时发现）**：无头段兜底条件 `len(prev_states)==1` 未考虑本轮 cancel——"cancel I2 + 无头补参段"场景剩余意图唯一但兜底失效，无头 delta 落入 `__nokey__` 丢弃（run2/run3 的 S-000012 即此，run1 模型恰好输出了 intent 头所以对）。修复 = 剩余数按"prev - 本轮 cancel"计算。非 candidate 相关，属 harness 存量边界 bug。**状态机侧同源症状自愈**：S-000012 t2 西瓜 GT=Ready PRED=Collecting 即丢 resolution 的状态机投影，修复后验证转 Ready ✓。另一处 t1 咪咕 GT=Collecting vs PRED=Ready 为 GT/pred 口径不对称（GT 侧"agent Request_Slot 整轮波及→Collecting"通道，pred 侧无 agent action 概念），属状态机已知口径弱项（Collecting 基线 46.1% 主因之一），是否对齐待拍板。'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('changelog updated')
