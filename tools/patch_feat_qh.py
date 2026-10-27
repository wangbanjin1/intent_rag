import io
p='features.py'
s=io.open(p,encoding='utf-8').read()
old="    candidate_mode: bool         # T3-3 候选列举/选择/落地 (candidate 段旁路 + select/cancel)"
new="""    candidate_mode: bool         # T3-3 候选列举/选择/落地 (candidate 段旁路 + select/cancel)
    quality_hint: bool           # 画质条目高亮 (话语 app/画质词查标准表注入)"""
assert s.count(old)==1
s=s.replace(old,new)
old2='            candidate_mode=_on(cfg, "candidate_mode"),'
new2='            candidate_mode=_on(cfg, "candidate_mode"),\n            quality_hint=_on(cfg, "quality_hint"),'
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('features patched')
