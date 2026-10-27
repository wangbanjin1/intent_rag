import io
p='pipeline.py'
s=io.open(p,encoding='utf-8').read()
old="            cand_mode_hint=cand_mode_hint,"
new="            cand_mode_hint=cand_mode_hint,\n            quality_hint=quality_hint,"
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('pass wired')
