# -*- coding: utf-8 -*-
"""_match_prev_key('I1', {content_key...}) 居然命中? 看 fallback 分支"""
import sys, json, inspect
sys.path.insert(0, '.')
import python_state as ps
print(inspect.getsource(ps._match_prev_key))
