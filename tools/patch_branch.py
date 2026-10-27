import io
p='pipeline.py'
s=io.open(p,encoding='utf-8').read()
# 选择轮 hint: harness 侧确定 a/b 分支 —— 检查 prev_states/id_states 中桩意图的 app 是否为空
old='''            if _cb.select_phrase(utterance):
                cand_mode_hint = (
                    "# === CANDIDATE MODE (选择轮, 本轮生效, 优先级最高) ===\n"
                    "# 用户在选定此前列出的候选记录。必须遵守:\n"
                    "# 1. 选中的候选记录 = 本保障的身份来源: application_name/service_name/resolution/rtt 四字段必须在选中轮【同时】落地, 不得只补部分——\n"
                    "#    已有桩意图(空 app 或对应)则写该意图 id 并全量赋值; 无桩则 # intent: new 全量赋值;\n"
                    "#    若此前误建的意图与选中记录身份不符, 必须在本段内将 app/service 改写为选中记录的身份;\n"
                    "# 2. 未被选中的候选: 不再保留, 也不为其输出任何段;\n"
                    "# 3. 用户在前几轮明说的字段值(如时间、清晰度)优先于记录值, 禁止覆盖明说值;\n"
                    "# 4. 记录的 completed_at 是记录完成时间, 不是保障时间, 禁止当作 start/end。\n")'''
new='''            if _cb.select_phrase(utterance):
                # v27.2 双跑教训: a/b 分支交模型自判是二义的 (run2 三例 T3-3 各走错一支)。
                # harness 确定性判定: 桩意图 application_name 为空 (t1 悬空桩) → 1a 落地候选身份;
                # 已有明说 app → 1b 只补悬空字段。分支结论直接写入提示。
                stubs = [(k, v) for k, v in (id_states or {}).items()
                         if not (v.get("application_name", {}).get("value")
                                 if isinstance(v.get("application_name"), dict)
                                 else v.get("application_name"))]
                if stubs:
                    sid_list = "、".join(k for k, _ in stubs)
                    branch = (
                        f"# 系统核验: 当前桩意图 {sid_list} 的 application_name 为空(用户从未明说 app)。\n"
                        "# 本轮选中候选后, 必须把选中记录的 application_name/service_name/resolution/rtt\n"
                        "# 【全量落地】到该桩意图(段头写其 id); 时间等用户明说字段保持不变。\n")
                else:
                    branch = (
                        "# 系统核验: 当前各意图的 application_name 均为用户此前明说。\n"
                        "# 本轮选中候选后, 【严禁改写任何意图的身份】, 只把选中记录中此前悬空的字段\n"
                        "# (如 rtt)写入对应意图; 用户明说的其他字段严禁被记录值覆盖。\n")
                cand_mode_hint = (
                    "# === CANDIDATE MODE (选择轮, 本轮生效, 优先级最高) ===\n"
                    "# 用户在选定此前列出的候选记录。必须遵守:\n"
                    + branch +
                    "# 2. 未被选中的候选: 不再保留, 也不为其输出任何段;\n"
                    "# 3. 记录的 completed_at 是记录完成时间, 不是保障时间, 禁止当作 start/end。\n")'''
assert s.count(old)==1
s=s.replace(old,new)
io.open(p,'w',encoding='utf-8').write(s)
print('harness-side branch decision wired')
