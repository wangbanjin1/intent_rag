import io
p='selftest.py'
s=io.open(p,encoding='utf-8').read()
old='''def test_record_selector():'''
new='''def test_candidate_mode():
    """v27.2 候选模式: 段解析/旁路剥离/账本/select落地质语。"""
    import candidate_book as cb
    from python_state import _split_intent_segments

    code = (
        "# summary: 候选1\n"
        "# candidate: 爱奇艺/看直播\n"
        'state.resolution = "1080p"\n'
        'state.rtt = "150ms"\n'
        "\n"
        "# summary: 候选2\n"
        "# candidate: 拼多多/看直播\n"
        'state.resolution = "1080p"\n'
        'state.rtt = "20ms"\n'
        "\n"
        "# summary: 时间落地\n"
        "# intent: new\n"
        'state.timestamp = find_timestamp(start="2026年07月26日04时00分", end="2026年07月26日07时00分")\n'
    )
    # 1) candidate 段解析
    cands = cb.parse_candidates(code)
    assert len(cands) == 2 and cands[0][0] == "爱奇艺/看直播" and cands[1][0] == "拼多多/看直播"
    # 2) 主链剥离: intent 切分只剩 new 段
    segs = _split_intent_segments(code)
    assert [k for k, _ in segs] == ["new"], segs
    # 3) 账本
    book = cb.CandidateBook()
    for key, seg in cands:
        book.add(cb.Candidate(key=key, fields={"raw": seg[:20]}))
    assert len(book.pending()) == 2
    sel = book.select_by_index(1)
    assert sel.key == "爱奇艺/看直播"
    removed = book.discard_except(sel.key)
    assert removed == ["拼多多/看直播"] and len(book.pending()) == 1
    book.resolve(sel.key, "I1")
    assert book.resolved["爱奇艺/看直播"] == "I1"
    # 4) 选择语检测
    assert cb.select_phrase("按第一条记录来配。") and cb.select_phrase("选第二条记录。")
    assert not cb.select_phrase("爱奇艺，明天上午6点开始，持续三个小时。")
    print("[OK] candidate_mode: 段解析/主链剥离/账本 select/cancel/选择语 全过")


def test_record_selector():'''
assert s.count(old)==1
s=s.replace(old,new)
old2="    test_time_extractor()\n    test_record_selector()"
new2="    test_time_extractor()\n    test_record_selector()\n    test_candidate_mode()"
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('selftest patched')
