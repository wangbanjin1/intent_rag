import io
p='session_eval.py'
s=io.open(p,encoding='utf-8').read()
s=s.replace('''    session_disambig = s.get("session_meta", {}).get("template_id") == "T3-3" or any(
        "Request_Disambiguation" in json.dumps(ev.get("agent", {}).get("action_types") or [])
        for ev in s.get("event_sequence", []))''','''    session_disambig = session.get("session_meta", {}).get("template_id") == "T3-3" or any(
        "Request_Disambiguation" in json.dumps(ev.get("agent", {}).get("action_types") or [])
        for ev in session.get("event_sequence", []))''')
io.open(p,'w',encoding='utf-8').write(s)
print('fixed var name')
