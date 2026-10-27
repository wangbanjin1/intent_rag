# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, '.')
import python_state as ps
prev = {'application_name': {'value': '微信'}, 'service_name': {'value': '开直播'},
        'timestamp': {'end_timestamp': {'value': '2026年07月25日21时00分', 'source_type': 'Turn', 'source_ref': 'U1'}}}
code = "state.timestamp = find_timestamp(start='2026年07月25日19时00分', end=state.end_timestamp)"
d = ps.execute_program(code, prev)
print('delta:', json.dumps(d, ensure_ascii=False)[:250])
