# -*- coding: utf-8 -*-
"""candidate 模式单测 (独立文件, 与 selftest.py 解耦)。用法: python candidate_selftest.py"""
from __future__ import annotations

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

NL = "\n"


def _mk_code():
    return NL.join([
        "# summary: 候选1",
        "# candidate: 爱奇艺/看直播",
        'state.resolution = "1080p"',
        'state.rtt = "150ms"',
        "",
        "# summary: 候选2",
        "# candidate: 拼多多/看直播",
        'state.resolution = "1080p"',
        'state.rtt = "20ms"',
        "",
        "# summary: 时间落地",
        "# intent: new",
        'state.timestamp = find_timestamp(start="2026年07月26日04时00分", end="2026年07月26日07时00分")',
    ])


def test_parse_and_strip():
    import candidate_book as cb
    from python_state import _split_intent_segments
    code = _mk_code()
    cands = cb.parse_candidates(code)
    assert len(cands) == 2 and cands[0][0] == "爱奇艺/看直播" and cands[1][0] == "拼多多/看直播"
    segs = _split_intent_segments(code)
    assert [k for k, _ in segs] == ["new"], [(k, len(s)) for k, s in segs]
    print("[OK] candidate 段解析 + 主链剥离")


def test_book():
    import candidate_book as cb
    book = cb.CandidateBook()
    for k, _ in cb.parse_candidates(_mk_code()):
        book.add(cb.Candidate(key=k, fields={}))
    assert len(book.pending()) == 2
    sel = book.select_by_index(1)
    assert sel.key == "爱奇艺/看直播"
    removed = book.discard_except(sel.key)
    assert removed == ["拼多多/看直播"] and len(book.pending()) == 1
    book.resolve(sel.key, "I1")
    assert book.resolved["爱奇艺/看直播"] == "I1"
    print("[OK] 账本 select/discard/resolve")


def test_select_phrase():
    import candidate_book as cb
    assert cb.select_phrase("按第一条记录来配。")
    assert cb.select_phrase("选第二条记录。")
    assert cb.select_phrase("第二条不要，就第一条吧。")
    assert not cb.select_phrase("爱奇艺，明天上午6点开始，持续三个小时。")
    assert not cb.select_phrase("对的，就按这个来。")
    print("[OK] 选择语检测 (含负例)")


def test_candidate_exec():
    """candidate 段程序体可独立执行出字段 (供账本记账)。"""
    from python_state import execute_program
    from candidate_book import parse_candidates
    cands = parse_candidates(_mk_code())
    key, seg = cands[0]
    delta = execute_program(seg, {}, timeout=1.5)
    flat_res = delta.get("resolution", {}).get("min_value", {}).get("value")
    flat_rtt = delta.get("rtt", {}).get("max_value", {}).get("value")
    assert flat_res == "1080p" and flat_rtt == "150ms", delta
    print("[OK] candidate 段沙箱执行 -> 字段提取")


def test_no_retrieve_exempt():
    """评估豁免: candidate 段不被当 intent 段判分 (execute_program_multi 不产出其 key)。"""
    from python_state import execute_program_multi
    segs = execute_program_multi(_mk_code(), {})
    keys = [k for k, _ in segs]
    assert "爱奇艺/看直播" not in keys and "拼多多/看直播" not in keys, keys
    assert "new" in keys
    print("[OK] execute_program_multi 候选豁免")


if __name__ == "__main__":
    test_parse_and_strip()
    test_book()
    test_select_phrase()
    test_candidate_exec()
    test_no_retrieve_exempt()
    print("\ncandidate 模式全部单测通过 ✅")
