import io
p='docs/report_flow.md'
lines=io.open(p,encoding='utf-8').readlines()
for i,l in enumerate(lines):
    if 'v27.1' in l and 'record_selector.enabled' in l:
        indent=''
        new=indent+'| `candidate_mode.enabled` | T3-3 候选模式：t1 全部命中记录列 `# candidate:` 段（CandidateBook 旁路账本，不入 JGA）→ t2 用户 select 选中候选解引用落地（空桩全量落地/非空补悬空字段 1a/1b 分支）+ cancel 其余 → t3 confirm；触发域=T3-3 模板信号 | candidate_book.py + python_state 剥离 + pipeline `_session_is_t33` + prompt_builder 1a/1b 分支 | **true(best)** | v27.2 | ✅ 采纳（94.87%/95.96%；T3-3 家族救 10） |\n'
        lines.insert(i+1,new)
        break
io.open(p,'w',encoding='utf-8').writelines(lines)
s=''.join(lines)
s=s.replace('## 4. 独立消融开关（全量门控清单，v13.0 起 13 个 → v27.0 至 21 个）','## 4. 独立消融开关（全量门控清单，v13.0 起 13 个 → v27.2 至 23 个）')
io.open(p,'w',encoding='utf-8').write(s)
print('flow updated')
