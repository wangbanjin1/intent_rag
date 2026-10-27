import io
# session_eval: 传 session_disambig (template T3-3 或事件序列含 Request_Disambiguation) 进 run_multi
p='session_eval.py'
s=io.open(p,encoding='utf-8').read()
old="""            if retrieve_on:
                finals = pipe.run_multi(utt, ref, prev_states if prev_states else None, user_context,
                                        agent_reply=t.get("prev_agent_reply"),
                                        prev_statuses=prev_statuses if prev_statuses else None)"""
new="""            if retrieve_on:
                finals = pipe.run_multi(utt, ref, prev_states if prev_states else None, user_context,
                                        agent_reply=t.get("prev_agent_reply"),
                                        prev_statuses=prev_statuses if prev_statuses else None,
                                        session_disambig=session_disambig)"""
assert s.count(old)==1
s=s.replace(old,new)
# compute session_disambig before loop
old2="""    # 意图状态机 (独立消融开关, 纯旁路不改 JGA 主流程):"""
new2="""    # v27.1: 消歧类会话信号 (T3-3 模板或事件序列含 Request_Disambiguation) → record_selector 悬空口径生效域
    session_disambig = s.get("session_meta", {}).get("template_id") == "T3-3" or any(
        "Request_Disambiguation" in json.dumps(ev.get("agent", {}).get("action_types") or [])
        for ev in s.get("event_sequence", []))

    # 意图状态机 (独立消融开关, 纯旁路不改 JGA 主流程):"""
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('session_eval patched')
