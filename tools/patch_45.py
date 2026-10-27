import io
p='prompt_builder.py'
lines=io.open(p,encoding='utf-8').readlines()
for i,l in enumerate(lines):
    if '4.5 若此前某轮的记录指代字段曾悬空留空' in l:
        indent='            '
        new=indent+'"#     但只补【悬空字段】: 用户此前明说的字段值(如清晰度 540P)严禁被记录值覆盖;\n"\n'
        lines.insert(i+1,new)
        break
io.open(p,'w',encoding='utf-8').writelines(lines)
print('inserted no-override line')
