# -*- coding: utf-8 -*-
"""模型写 find_timestamp(start=state.start_timestamp, ...) — 对空/缺槽 prev,
State 属性缺失时返回 Ellipsis? 查 State.__getattr__."""
import sys, inspect
sys.path.insert(0, '.')
import python_state as ps
print(inspect.getsource(ps.State))
