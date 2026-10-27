# -*- coding: utf-8 -*-
"""确认根因: id 模式下 execute_program_multi 收到的 prev_states 是【内容键】dict,
段头 'I1' 在 _match_prev 找不到 -> prev={} -> state.end_timestamp 对空 prev 解析失败 ->
line_tolerant 跳行 -> delta={} -> 时间丢失.
d6 为什么没炸? d6 时间 update 轮模型写的是 find_timestamp(start=..., end=state.end_timestamp)?
或 d6 模型直接写 state.start_timestamp=...? 对比两侧 t2 形态."""
import sys, json
sys.path.insert(0, '.')
import python_state as ps
raw = "# intent: I1\nstate.timestamp = find_timestamp(start='2026年07月25日19时00分', end=state.end_timestamp)"
prev_content = {"微信/开直播": {"timestamp": {"end_timestamp": {"value": "2026年07月25日21时00分"}}}}
print('content-key prev:', json.dumps(ps.execute_program_multi(raw, prev_content), ensure_ascii=False)[:200])
# 单段 _match_prev('I1', content_prev) 找不到 I1 -> prev={}
prev_m = ps._match_prev('I1', prev_content)
print('match:', prev_m)
# 直接 execute with empty prev
try:
    d = ps.execute_program("state.timestamp = find_timestamp(start='X', end=state.end_timestamp)", {})
    print('empty-prev delta:', json.dumps(d, ensure_ascii=False)[:150])
except Exception as e:
    print('empty-prev EXC:', type(e).__name__, str(e)[:80])
