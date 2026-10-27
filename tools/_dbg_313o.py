# -*- coding: utf-8 -*-
"""run_multi 收到的 prev_states 是内容键 (session_eval L173 传 prev_states=内容键).
execute_program_multi(raw, prev_states) 内部 _match_prev('I1', 内容键prev) -> None -> prev={}.
但 d6 上为何几乎不炸? d6 模型 t2 更新时间写的多为基础赋值? 或 d6 的 id 模式下
execute_program_multi 之前有 id->content 重映射? 检查 run_multi 是否在 exec 前做 id 替换.
搜 _id_states 使用点:"""
import sys
sys.path.insert(0, '.')
import re
src = open('pipeline.py', encoding='utf-8').read()
for i, line in enumerate(src.split('\n'), 1):
    if '_id_states' in line:
        print(i, line.rstrip()[:120])
