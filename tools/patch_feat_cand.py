import io
p='features.py'
s=io.open(p,encoding='utf-8').read()
old="    # ---- 记录指代预判 (v27.1, docs/plan_record_selector_20260922.md) ----\n    record_selector: bool        # 记录指代预判 (RECORD EVIDENCE/悬空 RULE 生成前注入)"
new="""    # ---- 记录指代预判 (v27.1, docs/plan_record_selector_20260922.md) ----
    record_selector: bool        # 记录指代预判 (RECORD EVIDENCE/悬空 RULE 生成前注入)
    # ---- 候选模式 (v27.2, docs/plan_t33_candidate_mode_20260923.md) ----
    candidate_mode: bool         # T3-3 候选列举/选择/落地 (candidate 段旁路 + select/cancel)"""
assert s.count(old)==1
s=s.replace(old,new)
old2="            record_selector=_on(cfg, \"record_selector\"),"
new2="            record_selector=_on(cfg, \"record_selector\"),\n            candidate_mode=_on(cfg, \"candidate_mode\"),"
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('features patched')
