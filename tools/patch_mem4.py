import io
p=r'C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\project_qwen_intent_rag_dataset.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('**d6 当前最佳 = v27.2（2026-09-23）：session 94.87% / turn 95.96% / 状态机 88.28%（config_d6_qwen_plus_best.json，candidate_mode 开，mi_on 口径）**。此前 v27.0 92.74%/93.54%、v26.3-p3 88.03%/89.90%。',
'**d6 当前最佳 = v27.2（2026-09-23~24 双跑）：session 93.16%（94.87/91.45）/ turn 94.85%（95.96/93.74）/ 状态机 87.82%（config_d6_qwen_plus_best.json，candidate_mode 开，mi_on 口径）**。此前 v27.0 92.74%/93.54%、v26.3-p3 88.03%/89.90%。')
old='**v27.2 run1：94.87%/95.96%（+2.13pp/+2.06pp）采纳，救 10 破 5 逐一核查零机制性（042/164/211=反常识画质映射波动基线复跑同错；149/151 复跑自愈）；T3-3 家族全转正**。'
new='''**双跑定案：均值 93.16%/94.85%（+0.42pp/+1.31pp，turn 超带边缘），稳救 5（040/056/075/174/192，T3-3 靶区稳定复现）稳破 4 净+1，维持采纳**。稳破 4 归因：164/211=反常识画质表"高清@淘宝/看直播=1080p"遵从不稳（v27.1 三跑也错，非 v27.2 引入）；151=T2-3 改口轮 defer 悬空与"按之前那条记录"复合的已知边界；149=补 app 轮独立波动。'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('memory updated')
