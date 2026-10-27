import io
# prompt_builder: 加 cand_mode_hint 参数与渲染 (渲染顺序: 最前, 优先级最高)
p='prompt_builder.py'
s=io.open(p,encoding='utf-8').read()
old="    record_rule_hint: str | None = None,\n    record_ev_hint: str | None = None,"
new="    record_rule_hint: str | None = None,\n    record_ev_hint: str | None = None,\n    cand_mode_hint: str | None = None,"
assert s.count(old)==1
s=s.replace(old,new)
old2="    if record_ev_hint:"
new2="    if cand_mode_hint:\n        extra += cand_mode_hint + \"\n\"\n    if record_ev_hint:"
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('prompt_builder patched')
