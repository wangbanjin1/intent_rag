import io
p='pipeline.py'
s=io.open(p,encoding='utf-8').read()
old="""            ev = _rse.record_evidence(
                utterance, user_context, agent_reply,
                disambiguation_active=bool(disamb_keys is not None) or bool(
                    re.search(r"查.{0,18}(?:记录|历史)|选中|办过|参考|最终选中|最近.{0,4}(?:办过|一条)", utterance)
                    and (user_context or {}).get("recent_records")))"""
new="""            ev = _rse.record_evidence(
                utterance, user_context, agent_reply,
                disambiguation_active=bool(disamb_keys is not None) or session_disambig)"""
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('trigger simplified to session-level signal')
