# -*- coding: utf-8 -*-
"""t2 引用场景验证: state.start_timestamp 读回 None (非 Ellipsis), find_timestamp(start=None) 不产槽."""
import sys
sys.path.insert(0, '.')
import python_state as ps
d2 = ps.execute_program("state.timestamp = find_timestamp(start=state.start_timestamp, end=state.end_timestamp, duration=state.duration)",
                        {'timestamp': {'start_timestamp': {'value': None}, 'end_timestamp': {'value': 'X'}, 'duration': {'value': '60min'}}})
print('t2 delta:', repr(d2))
