import io
p=r'C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\project_qwen_intent_rag_dataset.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**d6 当前最佳 = v27.0（2026-09-20）：session 92.74% / turn 93.54% / 状态机 86.08%（config_d6_qwen_plus_best.json 六时间开关全开，mi_on 口径）**。此前最佳 v26.3-p3 88.03%/89.90%。',
'**d6 当前最佳 = v27.2（2026-09-23）：session 94.87% / turn 95.96% / 状态机 88.28%（config_d6_qwen_plus_best.json，candidate_mode 开，mi_on 口径）**。此前 v27.0 92.74%/93.54%、v26.3-p3 88.03%/89.90%。汇报版讲解文档 `docs/story.md`。')
old3='**record 选择器（2026-09-23 三轮否定关闭）**'
i=s.find(old3); j=s.find('\n\n',i)
new3='''**record 选择器（v27.1 三轮否定关闭）+ 候选模式（v27.2 采纳定案）**：record 相关错误两波攻击——v27.1 定位类注入三轮净负关闭（扰动 record_rule 已验证行为面）；随后用户定案**候选模式**：T3-3 的 t1 全部命中记录列 `# candidate:` 段（CandidateBook 旁路账本不入 JGA）→ t2 select 解引用落地/cancel 其余 → 消歧规则按"桩意图 app 空/非空"1a/1b 分支（空=记录身份全量落地；非空=只补悬空字段严禁改写身份）。**v27.2 run1：94.87%/95.96%（+2.13pp/+2.06pp）采纳，救 10 破 5 逐一核查零机制性（042/164/211=反常识画质映射波动基线复跑同错；149/151 复跑自愈）；T3-3 家族全转正**。核心教训：候选列举是模型输出的必要部分（驱动下游客服回复），顺信息流方向的注入有效、逆行为面方向无效。'''
s=s[:i]+new3+s[j:]
io.open(p,'w',encoding='utf-8').write(s)
print('memory updated')
