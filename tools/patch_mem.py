import io
p=r'C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\project_qwen_intent_rag_dataset.md'
s=io.open(p,encoding='utf-8').read()
marker='**dataset_6 泛化性测试（2026-09-10/11'
add='''**T3-3 落地链路方案（2026-09-23 立项，未实施）**：v27.0 fix3 的 T3-3 2/10 洼地逐例核验——用户怀疑的"模型无法列举全部记录"不成立（客服话术只列部分候选如 S-000192 列 3/画像 4，但 GT 基准=话术序 v21.1 已证）。**真根因 = 消歧规则第 1 条语义反了**：GT 口径为三段式（t1 明说字段落地+记录字段悬空桩 → t2 选中记录的 app/svc/res/rtt 全量落地 source_ref=Record → t3 确认），而规则教"候选 app/service 禁止用作输出"（为 T2-x 设计），6/10 失败 case t2/t3 身份断裂全挂于此。方案 plan_t33_record_landing_20260923.md：①消歧规则按桩意图状态分支（空桩→选定记录全量落地；无桩→new 落地；非空桩→原规则）②明说优先显式化（治 720p 覆盖 540P、completed_at 当保障时间）③v27.1 defer 保留。理论上限 T3-3 8/10（+2.6pp）。验证：分支单测→10 T3-3 全量回放+T2-x 对照→单跑超带采纳。风险=T2-3/T2-6 共用消歧规则，分支必须绑空桩信号。

'''
i=s.find(marker)
s=s[:i]+add+s[i:]
io.open(p,'w',encoding='utf-8').write(s)
print('memory updated')
