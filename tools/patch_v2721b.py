import io
# report.md: 逐段精准替换
p='docs/report.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('｜ 当前方法栈 **v27.2**（+候选模式 candidate/select/cancel；',
            '｜ 当前方法栈 **v27.2.1**（+QUALITY HINT 画质条目高亮；+候选模式 candidate/select/cancel；')
s=s.replace('**mi_on 口径** session **93.16%**（三跑均值 94.87/91.45/93.16）/turn **94.75%**（95.96/93.74/94.55）（v27.2，状态机 87.79%',
            '**mi_on 口径** session **93.16%**（三跑均值）/turn **94.75%**（v27.2.1 含 QUALITY HINT，状态机 87.79%')
io.open(p,'w',encoding='utf-8').write(s)
print('report.md header ok')
