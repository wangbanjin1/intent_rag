# -*- coding: utf-8 -*-
"""t1 keys=[] — 芒果TV 意图从 t1 就没了. v27.7 strip 在 _listed_this_turn 时执行,
但 t1 的 listing 是在 run_multi 里 _cand_listing=matched 设置的, strip 检查
app ∈ _cand_apps. 芒果TV 不在 → 不剥. 但 t1 段丢了 → 谁干的?
嫌疑: 时间桩兜底! _listed_this_turn + all_empty? 不, 段非空.
嫌疑2: segs 处理后段处理 _process_id_segment 里 new 段 app=芒果TV... 
—— 等等, t1 EXEC 显示段在. strip 后呢? 打 strip 前后."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config
import pipeline as pl
ss = {s['session_id']: s for s in load_test_sessions('data/split_d930_full.json')}
s = ss['genb-S-001707']
cfg = load_config('configs/config_d6_qwen_plus_best.json')
pipe = MultiIntentPipeline(cfg)
# monkeypatch run_multi 内部不行; 直接检查 strip 逻辑触发条件:
# _listed_this_turn 在 run_multi 局部, strip 在其后. listing 在 t1 被 set.
# strip 条件: _listed_this_turn and _cand_listing. t1 满足.
# _cand_apps = {斗鱼直播, 咪咕, 优酷...}. 段 app=芒果TV 不在 → 不剥. 应该没影响.
# 但 t1 keys=[]! 试跑一遍打 strip 后 segs (patch execute_program_multi 后的下一个环节难).
# 直接怀疑: strip 循环 bug — _k == "__cancel__" 分支外, 对 (key, delta) 重建时
# _d.items() 若 delta 是 str (cancel) 会崩? t1 无 cancel.
# 另一嫌疑: strip 后 segs 变了, 但 t1 的段 key='new' delta 里 app 被剥? 芒果TV 不在候选.
# 重新精确看 _cand_listing 在 t1 的内容:
import record_selector as rs
from session_eval import _load_user_context
uc = _load_user_context(s['user_id'])
t1 = s['event_sequence'][0]['user']['utterance']
ev = rs.record_evidence(t1, uc, None, disambiguation_active=True)
print('t1 selector matched:', [(r.get('application_name')) for r in ev.matched] if ev else None)
