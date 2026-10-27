# -*- coding: utf-8 -*-
"""根因坐实链:
1. eval run t2: 模型写 find_timestamp(start=..., end=state.end_timestamp) — end 引用 prev.
2. execute_program_multi(raw, prev_states=内容键): _match_prev('I1', 内容键) 失败 (id头 vs 内容键)
   -> prev={} -> state.end_timestamp 解析失败 -> line_tolerant 跳行 -> delta={}
3. 但这只在该模型输出带 end=state.end_timestamp 时发生 (API 非确定性, 该形态 ~2%).
4. 结构性 bug: id 协议下 execute_program_multi 的 prev 映射从不认识 'I1' 头 —
   一直是靠"模型输出恰好不含 state.* 自引用"才没炸. d6 同样存在, 触发率低.
修法 (harness 侧): execute_program_multi 增加 id_states 感知 — 或 run_multi 在 exec 前把
raw 段头 I1 -> idbook.key_of(iid) 内容键替换, exec 后再按 id 处理 (process 层已按 id).
最小改法: run_multi exec 前, 若 id_on 且 idbook, 将 '# intent: <iid>' 段头替换为对应内容键.
但 process_id_segment 依赖段头是 id! 所以不能替换 raw.
正确修法: 给 execute_program_multi 传 {iid: prev} 视图 — run_multi 构造 exec_prevs =
{**prev_states, **{iid: prev_states[key_of(iid)] for iid in idbook._by_iid}}? 
但 cancel 段/去重/parse_cancels 都在 exec 内部按 key 匹配...
最小无副作用方案: exec 前构造 alias prev_states2 = {**prev_states}, 再对每个 iid 
把 idbook.key_of(iid) 的 prev 也挂到 iid 键下 (仅 exec 用). _match_prev('I1') 精确命中.
_cancel/去重逻辑不受影响 (cancel 头在 _handle_cancel_segment 另行处理, exec 里 parse_cancels
返回名字/id — id 不在 prev 也不会崩). 试证."""
import sys, json
sys.path.insert(0, '.')
import python_state as ps
prev = {"微信/开直播": {"application_name": {"value": "微信"}, "service_name": {"value": "开直播"},
        "timestamp": {"end_timestamp": {"value": "2026年07月25日21时00分"}}}}
exec_prevs = dict(prev)
exec_prevs['I1'] = prev["微信/开直播"]  # alias
raw = "# intent: I1\nstate.timestamp = find_timestamp(start='2026年07月25日19时00分', end=state.end_timestamp)"
print(json.dumps(ps.execute_program_multi(raw, exec_prevs), ensure_ascii=False)[:250])
