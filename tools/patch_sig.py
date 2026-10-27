import io
p='prompt_builder.py'
s=io.open(p,encoding='utf-8').read()
old="    cand_mode_hint: str | None = None,\n    pending_app_hint: str | None = None,"
new="    cand_mode_hint: str | None = None,\n    cand_landing: bool = False,\n    pending_app_hint: str | None = None,"
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('signature fixed')
