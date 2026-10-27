# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
import python_state as ps
code = "state.timestamp = find_timestamp(start=..., end='2026年07月25日21时00分')"
try:
    d = ps.execute_program(code, {})
    ts = d.get('timestamp', {})
    print('start value repr:', repr(ts.get('start_timestamp')))
except Exception as e:
    print('EXC:', type(e).__name__, str(e)[:100])
