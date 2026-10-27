# -*- coding: utf-8 -*-
"""'I1' 命中了 '微信/开直播'! 哪个分支? len(prev_states)==1 且 flatten 无 app? 
不, 有 app. 唯一可能: k == app 分支 — '微信/开直播'.startswith('I1/')? 否.
k == app? '微信/开直播' == 'I1'? 否. 怪. 逐步 debug."""
import sys
sys.path.insert(0, '.')
import python_state as ps
prev_content = {"微信/开直播": {"timestamp": {"end_timestamp": {"value": "X"}}}}
key = 'I1'
print('key in prev:', key in prev_content)
app = key.split('/')[0]
print('app:', app)
for k in prev_content:
    print('compare', k, '==', app, '?', k == app, '| startswith?', k.startswith(app + '/'))
from case_format import flatten_params
print('len==1:', len(prev_content) == 1)
k0, v0 = next(iter(prev_content.items()))
print('flatten app:', flatten_params(v0).get('application_name'))
