# -*- coding: utf-8 -*-
"""真相: t1 selector matched 有 9 条 (recent_multiple 宽指代), 芒果TV 在其中!
v27.7 剥离把 t1 明说的芒果TV 当'候选之一'剥掉了 → 意图从 t1 消失.
教训: 剥离条件过宽 — 'app∈候选' 不够, 用户真明说的 app 也会出现在宽指代候选里.
修法: 剥离仅当 (a) app∈候选 且 (b) 该 app ≠ 话语明说的新保障目标
(_utterance_mentions_app 同款逻辑: 记录筛选词不算, 新目标算).
1707 U1='帮我办芒果TV视频保障' — 芒果TV 是明说目标 → 不剥."""
print('方案确认')
