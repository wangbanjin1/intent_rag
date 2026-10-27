import io
p='prompt_builder.py'
lines=io.open(p,encoding='utf-8').readlines()
for i,l in enumerate(lines):
    if l.strip()=='extra += quality_hint + "':
        lines[i]='        extra += quality_hint + "\n"\n'
        # 下一行若是孤立引号行则删除
        if i+1<len(lines) and lines[i+1].strip()=='"':
            del lines[i+1]
        break
io.open(p,'w',encoding='utf-8').writelines(lines)
print('fixed')
