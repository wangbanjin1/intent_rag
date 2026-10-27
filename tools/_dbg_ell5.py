# -*- coding: utf-8 -*-
"""state.start_timestamp 在 prev 无该槽时 raise AttributeError -> line_tolerant 跳行.
但有该槽且值本身是 ... 呢? 不. 真凶: prev 里值存在时返回值; 
但 crash 时 t3 的 I1 prev (咪咕/视频) 的 timestamp 槽值可能已被 t2 写成 Ellipsis!
即 t1/t2 某轮模型写 find_timestamp(start=...) 字面省略号 -> _to_nested_param 把 Ellipsis 
包进 {'value': Ellipsis} -> 透传 -> t3 读回 Ellipsis -> json 崩. 
(单测已证 find_timestamp(start=...) 产 {'value': Ellipsis}).
修复: 沙箱层 find_* 把 Ellipsis 参数当 None (省略号是模型抄注释的噪声); 
+ _scalar/序列化前防御. 先确认 _dbg_ell 的产物路径."""
import sys
sys.path.insert(0, '.')
import python_state as ps
d = ps.execute_program("state.timestamp = find_timestamp(start=..., end='2026年07月25日21时00分')", {})
print('t=1 delta:', repr(d))
# 透传到下一轮: state.start_timestamp 读回 Ellipsis
d2 = ps.execute_program("state.timestamp = find_timestamp(start=state.start_timestamp, end=state.end_timestamp)", 
                        {'timestamp': {'start_timestamp': {'value': Ellipsis}, 'end_timestamp': {'value': 'X'}}})
print('t=2 delta:', repr(d2))
