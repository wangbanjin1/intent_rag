# -*- coding: utf-8 -*-
"""奇怪: 单测 _match_prev('I1', content_prev) 返回了内容?! 看 _match_prev 实现"""
import sys, json, inspect
sys.path.insert(0, '.')
import python_state as ps
print(inspect.getsource(ps._match_prev))
