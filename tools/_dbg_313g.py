# -*- coding: utf-8 -*-
"""复现: execute_program_multi 直接喂这段 raw + 这个 prev."""
import sys, json
sys.path.insert(0, '.')
import python_state as ps
raw = "# intent: I1\nstate.timestamp = find_timestamp(start='2026年07月25日19时00分', end=state.end_timestamp)"
prev = {"微信/开直播": {"application_name": {"value": "微信", "source_type": "Turn", "source_ref": "U1"}, "service_name": {"value": "开直播", "source_type": "Turn", "source_ref": "U1"}, "resolution": {"min_value": {"value": "1080p", "source_type": "Turn", "source_ref": "U1"}}, "rtt": {"max_value": {"value": "20ms", "source_type": "Turn", "source_ref": "U1"}}, "timestamp": {"end_timestamp": {"value": "2026年07月25日21时00分", "source_type": "Turn", "source_ref": "U1"}}}}
segs = ps.execute_program_multi(raw, prev)
print('segs:', json.dumps(segs, ensure_ascii=False, default=str)[:400])
# 单段执行看差异
from python_state import execute_program
seg = "state.timestamp = find_timestamp(start='2026年07月25日19时00分', end=state.end_timestamp)"
try:
    d = execute_program(seg, prev["微信/开直播"])
    print('direct:', json.dumps(d, ensure_ascii=False)[:300])
except Exception as e:
    print('direct EXC:', type(e).__name__, e)
