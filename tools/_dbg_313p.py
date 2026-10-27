# -*- coding: utf-8 -*-
"""执行前的 raw 预处理: id->content 替换? 搜 'rewrite'/'_sub_id'/'seg 头替换'."""
import sys
sys.path.insert(0, '.')
src = open('pipeline.py', encoding='utf-8').read()
lines = src.split('\n')
# 找 1051 (execute_program_multi) 之前 30 行内对 raw 的改写
for i in range(980, 1052):
    l = lines[i-1]
    if any(w in l for w in ('raw', 'sub(', 'replace', 'rewrite')):
        print(i, l.rstrip()[:110])
