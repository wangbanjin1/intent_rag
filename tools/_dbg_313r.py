# -*- coding: utf-8 -*-
"""自引用率相近, 那 d930 空 delta 的根因不是它. 回到 313: t2 delta={} 且 prev 匹配失败.
—— 等等, _dbg_313e 显示 t2 段 [I1, {}] 而 t1 [new, {完整}]. t2 失败, t1 成功.
t1 是 'new' 头 (无匹配需求), t2 是 'I1' 头 (匹配失败). 
而重放 313d 时 t2 又成功了?! 重放用了同一 config. 差异: 重放时 idbook 是新的, t1 后 
idbook.ensure('微信/开直播') 注册了 I1 -> 但 _match_prev 用的是 prev_states (内容键) 
而非 idbook! 重放 d 里 t2 也成功 → execute_program_multi 相同输入应当相同输出.
除非 raw 不同! 重放时模型 t2 输出的可能是 find_timestamp(start=...) 无 end 引用.
即: 这是模型输出的【非确定性】— 同 temp=0 但 API 波动. eval 时 t2 带了 end=state.end_timestamp,
重放时没有. 验证: 打重放的 t2 raw."""
import sys, json
sys.path.insert(0, '.')
from session_eval import load_test_sessions, run_session
from pipeline import MultiIntentPipeline, load_config
ss = load_test_sessions('data/split_d930_full.json')
s = [x for x in ss if x['session_id'] == 'gena-S-000313'][0]
cfg = load_config('configs/config_d6_qwen38_27b_v273.json')
pipe = MultiIntentPipeline(cfg)
pred, per_turn = run_session(pipe, s, retrieve_on=True)
print('t2 raw:', (per_turn[1].get('llm_raw') or '')[:400])
