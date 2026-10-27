import io
# session_eval: run_session 维护 CandidateBook; candidate 段的解析在 llm_raw 层完成(经 pipeline 透传);
# 评估豁免: run_session 中从 finals 剔除 candidate 段产生的 key (模型若误输出 # candidate 段,
# python_state 已在 _split_intent_segments 剥除 → finals 不会有 candidate key; 双保险: 判分侧过滤)
p='session_eval.py'
s=io.open(p,encoding='utf-8').read()
# 在 run_session 里加 CandidateBook
old="""    # v27.1: 消歧类会话信号 (T3-3 模板或事件序列含 Request_Disambiguation) → record_selector 悬空口径生效域
    session_disambig = session.get("session_meta", {}).get("template_id") == "T3-3" or any(
        "Request_Disambiguation" in json.dumps(ev.get("agent", {}).get("action_types") or [])
        for ev in session.get("event_sequence", []))"""
new="""    # v27.1: 消歧类会话信号 (T3-3 模板或事件序列含 Request_Disambiguation) → record_selector 悬空口径生效域
    session_disambig = session.get("session_meta", {}).get("template_id") == "T3-3" or any(
        "Request_Disambiguation" in json.dumps(ev.get("agent", {}).get("action_types") or [])
        for ev in session.get("event_sequence", []))
    # v27.2: 候选账本 (candidate 模式旁路, 不入 JGA; 供 select 落地与报告可视化)
    from candidate_book import CandidateBook, parse_candidates
    cand_book = CandidateBook()"""
assert s.count(old)==1
s=s.replace(old,new)
# 在 llm_raw 处解析 candidate 段记账
old2="""        llm_raw = getattr(pipe, "last_raw", "")"""
new2="""        llm_raw = getattr(pipe, "last_raw", "")
        # v27.2: 记账本轮 candidate 段 (旁路)
        for ck, cseg in parse_candidates(llm_raw):
            from case_format import flatten_params as _fp2
            cand_flat = {}
            try:
                from python_state import execute_program as _ep2
                cand_delta = _ep2(cseg, {}, timeout=1.5)
                cand_flat = _fp2(cand_delta)
            except Exception:
                pass
            from candidate_book import Candidate
            cand_book.add(Candidate(key=ck, fields=cand_flat,
                                    record_ref=str(cand_flat.get('completed_at') or '')))"""
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('session_eval patched')
