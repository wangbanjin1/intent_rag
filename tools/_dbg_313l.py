# -*- coding: utf-8 -*-
"""真相逼近: 'I1'.split('/')[0]='I1', prev 键 '微信/开直播' 不以 'I1' 开头 -> app 匹配失败;
单 prev 且 app 非空 -> 失败; '/' not in 'I1' -> svc 失败. 应返回 None!
但单测返回了 prev?! 上面 _dbg_313i 打出 match 有值 — 因为 prev_content 里 key='微信/开直播',
'I1' 无 '/'. 重新精确测."""
import sys, json
sys.path.insert(0, '.')
import python_state as ps
prev_content = {"微信/开直播": {"timestamp": {"end_timestamp": {"value": "X"}}}}
print('mkey:', ps._match_prev_key('I1', prev_content))
print('match:', ps._match_prev('I1', prev_content))
