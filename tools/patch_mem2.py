import io
p=r'C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\project_qwen_intent_rag_dataset.md'
s=io.open(p,encoding='utf-8').read()
old='**T3-3 落地链路方案（2026-09-23 立项，未实施）**'
i=s.find(old)
j=s.find('\n\n',i)
new='''**T3-3 候选模式方案（2026-09-23 用户定案设计，未实施）**：S-000040 复盘确认三段失效链（t1 抢跑 new 拼多多→爱奇艺意图从未存在→t2 只能在错误占位上补参数→t3 被 pending-app 规则锁死输出 none）。**用户提出正确形态 = 候选列举本身是模型输出的一部分**（后续"模型输出驱动客服回复"形态下，t1 不输出候选则下游无法生成消歧话术）：t1 把约束命中全部记录列为 `# candidate:` 段（旁路，不入 JGA），t2 select（选中候选解引用落地到桩意图 I1）/cancel 其余，t3 confirm。GT 分型核验 13 例：A 纯查询型 10 例（t1 只落时间，app 悬空）+ B 明说型 3 例（056/123/192，只悬空 rtt）——候选旁路化后判分与 GT 天然对齐。方案 plan_t33_candidate_mode_20260923.md：CandidateBook 旁路账本+评估豁免+record_selector 判定树供候选明细+defer 保留；风险=新段类型五处改动（parse/沙箱/账本/评估/报告）；可选缩小扰动=B 型不进候选模式。验证：协议单测→13 T3-3 回放+T2-x 对照→单跑超带双跑定案。

'''
s=s[:i]+new+s[j:]
io.open(p,'w',encoding='utf-8').write(s)
print('memory updated')
