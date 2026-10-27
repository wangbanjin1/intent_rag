# -*- coding: utf-8 -*-
"""313 深挖: t2 更新段 delta 只有 start, prev timestamp 有 end.
validate 是否因 start 变化重推导致 end 丢失? 直接跑 validate."""
import sys, json
sys.path.insert(0, '.')
from validator import validate
from case_format import flatten_params
prev = {'application_name': {'value': '微信', 'source_type': 'Turn', 'source_ref': 'U1'},
        'service_name': {'value': '开直播', 'source_type': 'Turn', 'source_ref': 'U1'},
        'resolution': {'min_value': {'value': '1080p', 'source_type': 'Turn', 'source_ref': 'U1'}},
        'rtt': {'max_value': {'value': '20ms', 'source_type': 'Turn', 'source_ref': 'U1'}},
        'timestamp': {'end_timestamp': {'value': '2026年07月25日21时00分', 'source_type': 'Turn', 'source_ref': 'U1'}}}
delta = {'timestamp': {'start_timestamp': {'value': '2026年07月25日19时00分', 'source_type': 'Turn', 'source_ref': 'U1'}}}
final = validate({'intent': 'unknown', 'params': delta}, '2026年07月24日12时00分', prev)
print('final:', json.dumps(final.get('final_state', {}), ensure_ascii=False)[:400])
print('flat:', flatten_params(final.get('final_state') or {}))
