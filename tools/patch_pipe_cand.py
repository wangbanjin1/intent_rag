import io
p='pipeline.py'
s=io.open(p,encoding='utf-8').read()

# insert candidate-mode block right after the v27.1 selector/record_rule block (after "completed_at=..." + blank)
anchor = '''        # v26: intent-id 协议 (config intent_id_protocol.enabled, 默认关)'''
insert = '''        # v27.2: 候选模式 (config candidate_mode.enabled, 消歧会话信号域内)
        # 触发: session_disambig + record_selector 判定树 defer(多候选/预选查询式)
        # → t1 注入 CANDIDATE MODE 列举指令(候选明细来自判定树); t2 选择语 → 落地/移除指令
        cand_mode_hint: str | None = None
        if getattr(self.features, "candidate_mode", False) and session_disambig:
            import candidate_book as _cb
            from record_selector import record_evidence as _rev
            if _cb.select_phrase(utterance):
                cand_mode_hint = (
                    "# === CANDIDATE MODE (选择轮, 本轮生效, 优先级最高) ===\n"
                    "# 用户在选定此前列出的候选记录。必须遵守:\n"
                    "# 1. 选中的候选记录: 其 application_name/service_name/resolution/rtt 全量落地——\n"
                    "#    已有桩意图(空 app 或对应)则写该意图 id 并全量赋值; 无桩则 # intent: new 全量赋值;\n"
                    "#    若此前误建的意图与选中记录身份不符, 必须在本段内将 app/service 改写为选中记录的身份;\n"
                    "# 2. 未被选中的候选: 不再保留, 也不为其输出任何段;\n"
                    "# 3. 用户在前几轮明说的字段值(如时间、清晰度)优先于记录值, 禁止覆盖明说值;\n"
                    "# 4. 记录的 completed_at 是记录完成时间, 不是保障时间, 禁止当作 start/end。\n")
            else:
                _ev = _rev(utterance, user_context, agent_reply, disambiguation_active=True)
                if _ev is not None and _ev.mode == "defer" and len(_ev.matched) >= 2:
                    lines = []
                    for i, rr in enumerate(_ev.matched, 1):
                        rtt = (rr.get('rtt') or {}).get('max_value') if isinstance(rr.get('rtt'), dict) else rr.get('rtt')
                        res = (rr.get('resolution') or {}).get('min_value') if isinstance(rr.get('resolution'), dict) else rr.get('resolution')
                        lines.append(f"#   第{i}条: application_name={rr.get('application_name')}, "
                                     f"service_name={rr.get('service_name')}, resolution={res}, rtt={rtt}, "
                                     f"completed_at={rr.get('completed_at')}")
                    cand_mode_hint = (
                        "# === CANDIDATE MODE (候选列举轮, 本轮生效, 优先级最高) ===\n"
                        "# 用户要求按记录办理但系统核验候选不唯一。必须遵守:\n"
                        "# 1. 按下方候选列表逐条输出 # candidate: <app>/<svc> 段(每条一段, 全部列出):\n"
                        + "\n".join(lines) + "\n"
                        "#    每段内赋值该记录的 resolution/rtt;\n"
                        "# 2. 用户本轮明说的字段(如时间/画质)正常落地到意图段(记录字段悬空时意图段只含明说字段);\n"
                        "# 3. candidate 段不算保障意图, 是等待用户选择的候选; 禁止自行挑选某条直接落地;\n"
                        "# 4. # summary 写明: 约束命中 N 条, 全部列为候选等待选择。\n")

        # v26: intent-id 协议 (config intent_id_protocol.enabled, 默认关)'''
assert s.count(anchor)==1
s=s.replace(anchor, insert)

old2="""            record_rule_hint=record_rule_hint,
            record_ev_hint=record_ev_hint,"""
new2="""            record_rule_hint=record_rule_hint,
            record_ev_hint=record_ev_hint,
            cand_mode_hint=cand_mode_hint,"""
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('pipeline patched')
