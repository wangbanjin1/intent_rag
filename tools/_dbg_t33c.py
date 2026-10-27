# -*- coding: utf-8 -*-
"""365: t2 模型只补 res/rtt 没落 app/svc (预期 CANDIDATE MODE 1a 分支会下指令全量落地,
含 app=云视讯) — d930 话术'候选2'但选择轮提示的候选明细顺序? _cand_listing 存档是否命中?
关键: t1 时 selector matched=[云视讯, 钉钉]? t2 '候选2' -> _parse_pick_ord('那就选候选2吧')?
'候选2' 不匹配 _ORD_SEL_RE (要求'第N条/个')! d930 话术变体='候选N'!
验证 _parse_pick_ord 对'候选2'返回 None -> 无选中明示 -> 模型不知道选了谁 -> 只补悬空."""
import sys
sys.path.insert(0, '.')
from pipeline import _parse_pick_ord
for u in ['那就选候选2吧。', '就选候选1吧。', '这次就选候选2吧。', '候选2不要，我选候选1这条记录。', '选第二条记录。']:
    print(repr(u), '->', _parse_pick_ord(u))
