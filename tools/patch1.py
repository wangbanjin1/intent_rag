import io
p='pipeline.py'
s=io.open(p,encoding='utf-8').read()
old="""        agent_reply: str | None = None,
        prev_statuses: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        prev_states = prev_states or {}"""
new="""        agent_reply: str | None = None,
        prev_statuses: dict[str, str] | None = None,
        session_disambig: bool = False,
    ) -> dict[str, dict[str, Any]]:
        prev_states = prev_states or {}"""
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('signature ok')
