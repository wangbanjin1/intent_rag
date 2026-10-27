import io
p='python_state.py'
s=io.open(p,encoding='utf-8').read()
# 1) execute_program_multi: candidate 段跳过(候选段由 caller 经 CandidateBook 处理, 不入主链)
old='''def _split_intent_segments(code: str) -> list[tuple[str, str]]:
    """切分多意图程序。返回 [(intent_key, segment_code), ...]。
    intent_key 形如 "虎牙/开直播" 或 "none"。无 intent 头的整段归为 ("", code)。
    # cancel 段被跳过（取消信号由 parse_cancels 单独处理）。
    """
    code = _strip_codeblock(code)
    matches = list(_INTENT_HDR.finditer(code))'''
new='''def _split_intent_segments(code: str) -> list[tuple[str, str]]:
    """切分多意图程序。返回 [(intent_key, segment_code), ...]。
    intent_key 形如 "虎牙/开直播" 或 "none"。无 intent 头的整段归为 ("", code)。
    # cancel 段被跳过（取消信号由 parse_cancels 单独处理）。
    # v27.2: # candidate 段同样跳过（候选记账由 candidate_book.parse_candidates 单独处理, 旁路不入 JGA）。
    """
    code = _strip_codeblock(code)
    # 剔除 candidate 段 (避免其程序体被当作意图段执行)
    code = re.sub(r"^#\s*candidate\s*:.*?(?=^#\s*(?:candidate|intent|cancel)\s*:|\Z)",
                  "", code, flags=re.MULTILINE | re.DOTALL)
    matches = list(_INTENT_HDR.finditer(code))'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('python_state patched')
