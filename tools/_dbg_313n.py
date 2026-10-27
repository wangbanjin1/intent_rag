# -*- coding: utf-8 -*-
"""flatten app=None! 因为 'application_name': {'value': '微信'} — flatten_params 应解出 '微信'.
但这里返回 None?? _dbg_313m 的 v0 只有 timestamp. 真实 prev 有 app. 
上面 _dbg_313l 的 prev 也有完整结构吗? 没有! _dbg_313l prev 只有 timestamp!
所以走了"单 prev 且无 app"分支 — 测试输入失真.
真实路径: prev 有 app, 'I1' 应不命中... 但 eval 里 t2 delta={} 是事实.
eval prev 结构 (PREV-FULL dump): application_name 有 value: 微信. 
那 flatten 有 app, 单prev分支不命中, 'I1'无'/', svc分支不进 -> mkey=None -> prev={} -> 崩!
_dbg_313i 的"成功"是失真输入. 用真实 prev 重测:"""
import sys, json
sys.path.insert(0, '.')
import python_state as ps
prev = {"微信/开直播": {"application_name": {"value": "微信"}, "service_name": {"value": "开直播"},
        "resolution": {"min_value": {"value": "1080p"}}, "rtt": {"max_value": {"value": "20ms"}},
        "timestamp": {"end_timestamp": {"value": "2026年07月25日21时00分", "source_type": "Turn", "source_ref": "U1"}}}}
raw = "# intent: I1\nstate.timestamp = find_timestamp(start='2026年07月25日19时00分', end=state.end_timestamp)"
print('mkey:', ps._match_prev_key('I1', prev))
print('multi:', json.dumps(ps.execute_program_multi(raw, prev), ensure_ascii=False)[:200])
