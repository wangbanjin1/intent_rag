import io
p='prompt_builder.py'
lines=io.open(p,encoding='utf-8').readlines()
for i,l in enumerate(lines):
    if '# 1. 不得新建 intent 段' in l and 'DISAMBIG' not in l:
        indent='            '
        cond=indent+"(f\"# 1. 选中的候选记录即本保障的身份来源: 用户选定哪条, 就把该记录的 application_name/\"\n"
        cond+=indent+"     \"service_name/resolution/rtt 全量写入意图段;\n\" if cand_landing else\n"
        new_l=cond+l
        lines[i]=new_l
        break
io.open(p,'w',encoding='utf-8').writelines(lines)
print('rule1 branch written')
