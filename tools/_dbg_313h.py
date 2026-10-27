# -*- coding: utf-8 -*-
"""逐层复现 execute_program_multi 内部: _split_intent_segments 切出什么? _match_prev 匹配到谁?"""
import sys, json
sys.path.insert(0, '.')
import python_state as ps
raw = "# intent: I1\nstate.timestamp = find_timestamp(start='2026年07月25日19时00分', end=state.end_timestamp)"
prev = {"微信/开直播": {"application_name": {"value": "微信"}, "service_name": {"value": "开直播"},
        "timestamp": {"end_timestamp": {"value": "2026年07月25日21时00分", "source_type": "Turn", "source_ref": "U1"}}}}
segs = ps._split_intent_segments(raw)
print('split:', [(k, repr(s)[:80]) for k, s in segs])
prev_m = ps._match_prev('I1', prev)
print('match I1 ->', prev_m if prev_m is None else list(prev_m.keys()))
# id 模式下 header 是 I1, prev_states 是内容键! execute_program_multi 里 _match_prev('I1', ...) 找不到
# 但 run_multi 传入 execute_program_multi 的 prev_states 是什么? 看 run_multi 调用处
