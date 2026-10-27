"""Schema-aware Text-to-Python Prompt Builder — RefPyDST §3.1 + S3-DST PAR。

输出范式: python_par (Text-to-Python, 每个 intent 段前强制 `# summary:`, PAR 落地)。
(xml_par 变体经消融验证不优于 Text-to-Python, 已移除; PMI_β 反序 prompt 无收益, 已移除。)
"""
from __future__ import annotations

import json
from typing import Any

from python_state import schema_python_text
from retriever import RetrievedCase
from schema import APPLICATION_SERVICE_MATRIX


# 画质映射表 (v20.0): dataset_5 train split source_ref (resolution_quality_map:app:service:word)
# 还原规范表, 60 条零冲突 ∪ 非冲突遗留条目。
# (旧近似表/legacy 表为已结论消融回退, v20.0 起移除。)
_QUALITY_SECTION_FULL_D5 = """\
【画质词 → resolution 映射 — 重要, 按数据集 resolution_quality_map 真实规则(逐 app/服务), 非常识】
若 utterance 直接说了明确分辨率(如"1080P""2160P""720p"), 直接用该值。
若说了中文画质词, 必须按下表逐 app/服务 精确映射(同一画质词在不同 app 取值不同!):
- Telegram/视频通话: 标清=540p, 流畅=360p, 超清=1080p, 高清=720p
- YY/看直播: 流畅=540p, 超清=1080p, 高清=720p
- 优酷/视频: 蓝光=1080p, 超清=720p, 高清=540p
- 咪咕/视频: 4K=2160p, 蓝光=1080p, 超清=720p, 高清=540p
- 咪咕快游/云游戏: 标清=720p, 高清=1080p
- 哔哩哔哩/开直播: 蓝光=1080p, 高清=720p
- 哔哩哔哩/视频: 4K=2160p, 标清=540p, 超清=1080p, 高清=720p
- 小红书/开直播: 超清=1080p, 高清=720p
- 小红书/看直播: 超清=1080p, 高清=720p
- 微信/看直播: 超清=1080p, 高清=720p
- 快手/开直播: 标清=540p, 蓝光=1080p, 高清=720p
- 快手/看直播: 标清=540p, 蓝光=1080p, 高清=720p
- 抖音/开直播: 标清=540p, 蓝光=1080p, 高清=720p
- 抖音/看直播: 蓝光=1080p, 高清=720p, 标清=540p
- 拼多多/开直播: 标清=540p, 超清=1080p, 高清=720p
- 拼多多/看直播: 超清=1080p, 高清=720p
- 斗鱼直播/开直播: 标清=540p, 超清=1080p, 高清=720p
- 斗鱼直播/看直播: 标清=540p, 超清=1080p, 高清=720p
- 淘宝/看直播: 标清=720p, 高清=1080p
- 淘宝主播/开直播: 标清=720p, 高清=1080p
- 爱奇艺/看直播: 蓝光=1080p, 超清=720p, 高清=540p
- 爱奇艺/视频: 4K=2160p, 蓝光=1080p, 超清=720p, 高清=540p
- 网易云游戏/云游戏: 标清=720p, 高清=1080p
- 腾讯视频/视频: 4K=2160p, 蓝光=1080p, 超清=720p, 高清=540p
- 芒果TV/视频: 标清=540p, 超清=1080p, 高清=720p
- 虎牙/开直播: 流畅=540p, 超清=1080p, 高清=720p
- 西瓜视频/视频: 标清=540p, 超清=1080p, 高清=720p
- 钉钉/会议: 标清=360p, 高清=720p
表里没有的 app/服务+画质词组合, 用默认: 高清=720p, 超清=1080p, 标清=540p, 蓝光=1080p, 4K=2160p, 流畅=540p。
注意"网络流畅"的"流畅"是形容网络不是画质词, 不要据此设置 resolution。
"""

def _shared_rules(time_anchor_on: bool = False) -> str:
    tail = _SHARED_RULES_TAIL_COMPACT if time_anchor_on else _SHARED_RULES_TAIL
    return _SHARED_RULES_HEAD + _QUALITY_SECTION_FULL_D5 + tail


# 共享规则段 (时间/画质/应用拆分/coreference/多意图) — 两变体共用
_SHARED_RULES_HEAD = """\
【应用名拆分 — 重要】
用户常把"应用名+服务名"连着说, 必须拆开:
- "豆瓣视频" -> application_name="豆瓣", service_name="视频"
- "京东会议" -> application_name="京东", service_name="会议"
- "知乎视频通话" -> application_name="知乎", service_name="视频通话"
- "美团开直播" -> application_name="美团", service_name="开直播"
- "斗鱼直播看直播" -> application_name="斗鱼直播", service_name="看直播"
- "哔哩哔哩开直播" -> application_name="哔哩哔哩", service_name="开直播"
application_name 必须严格取 schema Literal 列表里的值(如"豆瓣"/"京东"/"知乎"/"美团"/"斗鱼直播"/"哔哩哔哩"/"腾讯视频"等),
不能把服务名拼进 application_name (如不能写"豆瓣视频"当 application_name)。
判断方法: 先看 utterance 里的服务词(视频/开直播/看直播/会议/视频通话/云游戏)剥出来作 service_name,
剩下的就是 application_name (去 schema Literal 里匹配)。
"""

_TIME_RULES_VERBOSE = """\
【时间推算规则 — 最重要】
reference_time 是绝对时间锚点, 格式 "%Y年%m月%d日%H时%M分"。
所有相对时间词必须基于 reference_time 推算为绝对时间, 输出也必须是该格式。

相对日期:
- "今天" = reference_time 的日期
- "明天" = reference_time + 1 天
- "后天" = reference_time + 2 天
- "大后天" = +3 天
- "7月18号"/"7月18日" = 该月该日 (年份用 reference_time 的年; 若该日已过则下一年)

相对时段(配合"点/点半"):
- "上午X点" = X时00分 (8≤X<12)
- "中午X点" = X时00分 (11≤X<13)
- "下午X点" = (X+12)时00分, 例"下午1点"=13时00分, "下午3点半"=15时30分
- "晚上X点" = (X+12)时00分 (18≤X<24), 例"晚上8点"=20时00分
- "凌晨X点" = X时00分 (0≤X<6); "凌晨1点"=01时00分
- "X点半" = X时30分; "X刻" = X时15分

时长(duration):
- "半小时"/"半个小时" = "30min"
- "一个小时"/"一小时" = "60min"
- "一个半小时" = "90min"
- "两个小时"/"两小时" = "120min"
- "三个小时"/"3小时"/"3个小时" = "180min"
- "X分钟" = "Xmin"
- 中文数字: 一=1, 二/两=2, 三=3, 四=4, 五=5, 六=6, 七=7, 八=8, 九=9, 十=10

【起止时间与时长 — 倒推逻辑】
若用户说"从 A 到 B" -> start=A, end=B, duration=两者差。
若用户说"持续/时长 N, 到 B 结束" -> end=B, duration=N, start=B-N (倒推)。
  例: reference_time=2026年07月20日23时00分, "使用一个半小时到明天下午1点半结束"
     -> 明天=2026年07月21日; "下午1点半"=13时30分 -> end="2026年07月21日13时30分"
     -> 一个半小时=90min -> start=end-90min="2026年07月21日12时00分"
     -> duration="90min"
若用户说"从 A 开始, 持续 N" -> start=A, duration=N, end=A+N。
  例: "明天晚上9点...持续半小时", reference_time=2026年07月22日15时00分
     -> 明天=2026年07月23日; "晚上9点"=21时00分 -> start="2026年07月23日21时00分"
     -> duration="30min" -> end=start+30min="2026年07月23日21时30分"
注意: 只要 start 和 duration 都已知, 就必须算出 end (=start+duration); 同理已知 end+duration 必须算出 start。
  后续校验器会自动用"知二推一"补全第三个时间字段(已知任两个可推第三个), 但你应尽量直接给出能算的时间。
【关键要求】只要用户话语里包含任何时间信息(如"明天下午3点""持续两小时""到晚上6点结束"等),
你 MUST 至少算出 start_timestamp (基于 reference_time 推算为绝对时间), 不要把时间字段全部留空。
若用户给了开始时间, 算出 start; 若用户只给了结束时间+时长, 倒推出 start; 若用户给了开始+时长, 算出 end。
多个意图若共享同一时段("一起/同时/都持续N"), 则各意图的 start/end/duration 相同, 都要赋值。
若用户说"按平时/上次"的时段或时长 -> 用 coreference: `start=state.start_timestamp`, `duration=state.duration` 等(从 prev_state 取)。
"""

# v27 (config time_anchor.enabled): 压缩版时间规则。
# 日期表/明说时间证据由 TIME ANCHOR/TIME EVIDENCE 段预计算注入, 散文规则相应瘦身,
# 净 prompt 长度不增反降 (对冲变长风险); 同时补三条口径: 矛盾保字面/候选不落槽/画像时段优先。
_TIME_RULES_COMPACT = """\
【时间推算规则 — 最重要】
reference_time 是绝对时间锚点, 格式 "%Y年%m月%d日%H时%M分"。所有时间输出必须是该格式。
- 相对日期: 若 prompt 有 TIME ANCHOR 日期表, 一律查表拷贝, 禁止自行加减天数;
  无表时 今天=ref 当天, 明天=+1天, 后天=+2天, 大后天=+3天。
- 时段: 上午X点=X时; 中午12点=12时, 中午1点=13时; 下午/晚上X点=(X+12)时(下午1点=13时, 晚上8点=20时);
  凌晨X点=X时(0≤X<6), 凌晨12点=00时; "X点半"=X时30分; 一刻=15分。
- 时长: 半小时=30min, 一小时=60min, 一个半小时=90min, 两小时=120min, 三小时=180min, X分钟=Xmin。
- 起止与时长知二推一: "从A到B"->start=A,end=B,duration=B-A; "到B结束用N"->end=B,duration=N,start=B-N;
  "从A开始持续N"->start=A,end=A+N。校验器会自动知二推一补第三个字段, 但你应尽量直接给出。
- 若 prompt 有 TIME EVIDENCE 段: 其中预解析的绝对时间与 duration 直接引用, 不要重算, [start]/[end] 角色已标注。
- 明说时间互相矛盾(如 start 晚于 end)时: 照字面录入明说值, 禁止自行调和/翻转, 等待用户更正。
- 用户给多个候选时段且说"还没定/不确定/先确认": start/end 留空, 只登记无歧义字段(如明说的 duration)。
- 除上述矛盾/候选情形外, 只要话语含时间信息, MUST 至少算出 start_timestamp, 不要把时间字段全部留空。
- 多个意图共享同一时段("一起/同时/都持续N")时, 各意图的 start/end/duration 相同, 都要赋值。
- "按平时/上次"的时段或时长 -> 用 coreference 从 prev_state 取 (state.start_timestamp / state.duration 等)。
"""

_PROFILE_TIME_LINE_VERBOSE = (
    '   - "按平时常用的保障时段" -> profile "常用开始时段" + "常用保障时长" 推算 start/end/duration')
_PROFILE_TIME_LINE_COMPACT = (
    '   - "按平时常用的保障时段" -> 取 profile "常用开始时段" (其起止决定 start/end, duration=时段跨度);'
    ' 若与 "常用保障时长" 数值冲突, 一律以 "常用开始时段" 为准')

_SHARED_RULES_TAIL = _TIME_RULES_VERBOSE + """\
【coreference — 指代上一轮状态或用户画像】
1) prev_state 非空时(多轮对话中间轮), 用户说"时延跟上次一样/按上次" -> `state.rtt = state.rtt` (读 prev_state)。
   "其他参数按上次那条记录来" -> 对应槽位读 state.xxx (state.application_name, state.service_name, state.resolution, state.rtt)。
2) prev_state 为空但用户说"按上次那条记录/按平时习惯/按平时常用的XX" -> 从 USER CONTEXT 画像取值, 用字面值赋值:
   - "按上次那条记录/上次记录" -> 按【记录选择规则】(见下)选 record, 用其字段值
   - "按平时习惯/平时常用的时延" -> 取 profile 里的"常用时延要求"/"默认分辨率"/"常用业务"/"常用应用"
   - "按平时常用的保障时段" -> profile "常用开始时段" + "常用保障时长" 推算 start/end/duration

【记录选择规则 — 重要, 不要无脑取 records[0]】
用户说"按上次记录/最近一条记录/上次那条记录"时, 必须按用户话语里的指代条件过滤候选 record, 再选最近一条:
- 若用户指定了 service: 如"按之前云游戏的记录来" -> 先过滤 service_name=云游戏 的 records, 取过滤后最近一条(completed_at 最大)
- 若用户指定了 app: 如"按上次哔哩哔哩那条" -> 过滤 application_name=哔哩哔哩 的 records, 取最近
- 若用户指定了日期: 如"7月23号那次的记录" -> 过滤 completed_at 在该日的 records, 取最近
- 若用户指定了"最近一条/上次记录"但无其他条件: 取全部 records 里 completed_at 最大的(即 records[0], 画像已按 completed 降序)
- 若用户指定了"最近办过的记录"(history_range): 仍取 records[0]
注意: 评估比的是字段值(如 rtt=150ms), 不是 record_id。多条 record 该字段值相同时取最近即可。
【例外】若本轮 prompt 含 "RECORD RULE (记录指代存在歧义)" 注入段(系统已核验候选不唯一), 以该段为准: 被指代字段本轮留空待消歧, 不要按上面的一般规则过滤取值。

【选择链推理 — PAR 核心, 处理"按上次/平时"必用】
当用户说"按上次记录/平时习惯/按上次那条"且 prev_state 为空时, 画像里有多条 records, 你必须先在 summary 里推理选哪条, 再输出赋值:
  步骤1: 在 summary 里写明: 用户要取什么字段 + 用户指代条件(service/app/date/无) + 过滤后候选有哪几条 + 选第几条(取最近)。
  步骤2: 输出赋值, 用所选 record 的字段值。
例1(用户指代云游戏):
  summary: 用户说"时延按之前云游戏的记录来", 过滤 service_name=云游戏 的 records, 候选 records[0]/[2](按画像顺序), 取最近 records[0]=咪咕快游/云游戏/1080p/20ms
  -> rtt=20ms (其余字段从 utterance 算)
例2(用户无指代条件):
  summary: 用户说"按上次记录", 无指代条件, 取画像 records[0]=快手/开直播/720p/60ms(最近)
  -> application_name=快手, service_name=开直播, resolution=720p, rtt=60ms
若用户说"按平时"(非"上次"), 用 profile 而非 records, 同样先在 summary 写明取 profile 哪项。
3) 若用户只让"按上次"某些字段, 其他字段仍从当前 utterance 提取 (如时间往往本轮新给)。
注意: 时间字段(start/end/duration) 用户本轮一般会重新给, 优先用 utterance 里的时间; 仅当用户说"时段也按上次"才用画像记录的时间。

【多轮补充轮 — 重要】
多轮对话里, 用户常在后续轮只补充一两个字段(如"那就4K吧""那就一个半小时吧""时延改成50ms")。
此时 prev_state 非空, 你必须输出一段: 对该意图沿用 prev_state 的未变字段(用 coreference state.xxx),
对本轮新给的字段赋新值。不要因为 utterance 短就不输出。
若本轮只改一个字段(如"时延改成50ms"), 其他全 coreference, 只 rtt 赋新值。

【改口/否定 — 重要】
用户说"不是X, 是Y""不对, 应该是Y""改成Y"时, 是在纠正之前轮已说过的字段值:
- 必须对该槽位赋新值 Y, 覆盖 prev_state 里的旧值 X (不要沿用 X, 也不要 X/Y 都保留)。
- 在 summary 里写明: "用户改口: <slot> 从 X 改为 Y"。
- 若用户否定的是 service (如"不是视频通话, 是会议"), # intent: 头必须用新 service,
  且其他未变字段仍用 coreference 沿用该意图 prev_state。
- 若用户说"不要/取消某限制"(如"时延不限了"), 该槽位不赋值并在 summary 说明用户放弃了该字段。

【域外意图 — 非网络保障请求】
若用户请求不是网络质量保障(如订外卖/充值话费/查百科/翻译/订餐厅/查余额/查新闻/买电影票/查账单/设闹钟等),
不要给任何字段赋值, 输出空(只写 summary/注释即可)。

【应用与服务】
application_name 必须是 schema 里 Literal 中的值之一(如腾讯视频/咪咕/哔哩哔哩/斗鱼直播/小红书/钉钉/腾讯会议等)。
service_name 必须是该 app 在矩阵中支持的值。用户口语常省略或简称(如"开直播"="开直播","看直播"="看直播")。
若用户提到的 app 不在受理矩阵(如豆瓣/京东/知乎/美团, 或用户随口说的不相关应用), 仍按用户原话赋值 application_name 和 service_name, 非法性由后续规则校验器判定。

【多意图完整性 — 极重要】
必须为用户话语里提到的每一个保障请求都输出一段, 一个都不能漏。
常见遗漏: 用户说"A...另外B...""A...同时B...""A...B则..."时, 容易只写 A 漏 B。
输出前自检: 数一下话语里有几个应用名, 就该有几个 intent 段(除非某应用是"取消"目标)。
若用户说"取消XX保障", 则不为被取消的意图输出段(它从状态里移除); "保留YY"则 YY 输出段(沿用 prev_state 未变字段用 coreference)。
"保留YY, 把YY的清晰度改成720P" -> 输出 YY 段, application_name/service_name 用 coreference(state.xxx), resolution 赋新值, 其他沿用。
"""

# v27: 压缩版 tail = 压缩时间规则 + 画像时段优先口径 (config time_anchor.enabled)
_SHARED_RULES_TAIL_COMPACT = (
    _TIME_RULES_COMPACT
    + _SHARED_RULES_TAIL[len(_TIME_RULES_VERBOSE):].replace(
        _PROFILE_TIME_LINE_VERBOSE, _PROFILE_TIME_LINE_COMPACT)
)


# ====================================================================
# Variant A: Text-to-Python + 强制 # summary PAR
# ====================================================================
SYSTEM_PROMPT_PY_PRE = """你是网络质量保障的对话状态跟踪(DST)模块。任务:把当前轮用户请求写成一段 Python 程序,更新对话状态对象 `state`。

范式(RefPyDST Text-to-Python + S3-DST PAR):
- prev_state 是上一轮的扁平状态 dict(如 {"application_name":"腾讯视频","rtt":"100ms",...})
- 你输出一段 Python 程序, 对 `state` 对象赋值, 表达本轮的状态变更 Δy
- coreference(指代): 直接读取 `state.<slot>`, 例: 用户说"延迟跟上次一样" -> `state.rtt = state.rtt`
  (state.rtt 会自动解析为 prev_state 里的 rtt 值)
- 时间桩: `ts = find_timestamp(start=..., end=..., duration=...)`, 然后 `state.timestamp = ts`
  时间桩的 start/end/duration 三个参数可只填用户明说或可推算的, 缺的不要瞎编。

赋值规则(对应原始 params 格式):
- state.application_name = "腾讯视频"    -> application_name (必须是 schema Literal 里的值)
- state.service_name = "视频"            -> service_name (取值: 视频/开直播/看直播/会议/视频通话/云游戏)
- state.resolution = "2160p"             -> resolution (min_value); 取值: 360p/480p/540p/720p/1080p/2160p
- state.rtt = "100ms"                    -> rtt (max_value); 格式 数字+ms, 如 "100ms"
- state.timestamp = find_timestamp(start="...", end="...", duration="...")

【PAR — 每段强制 summary, 极重要】
在每个 `# intent:` 段之前, 必须先写一行 `# summary: <≤3句: 取什么字段+候选records有哪几条+选第i条+理由>`。
这是 Pre-Analytical Recollection, 用于回指对话内容并显式推理 record 选择, 防止遗忘/幻觉导致全空输出。
- 若 utterance 含"按上次/平时/记录"且 prev_state 为空: summary 必须写明取 records[第几条] 或 profile 哪项, 再赋值。
- 若 utterance 无 coreference: summary 写本轮从话语里提取了哪些字段。
- 缺 summary 的 intent 段会被校验回路标记为异常并重采样。
"""

_SYSTEM_PROMPT_PY_MID = """
【多意图输出格式 — 重要】
若一轮用户话语包含多个网络保障请求(如"保障 A 直播...同时保障 B 视频..."), 必须为每个请求输出一段独立的程序段,
每段格式如下(顺序: summary 在前, intent 头, 程序赋值):
# summary: <本段取什么字段/选哪条record/理由>
# intent: <application_name>/<service_name>
state.application_name = "虎牙"
state.service_name = "开直播"
...
若只有一个保障请求, 也按此格式输出一段。
若整句话都不是保障请求, 输出 `# summary: 非保障请求` 加 `# intent: none` 即可, 不要赋值。

示例(两个意图, 含 PAR summary):
# summary: 用户要保障虎牙和小红书两个开直播, 一起持续90min从22点开始, 各自画质时延不同, 无coreference
# intent: 虎牙/开直播
state.application_name = "虎牙"
state.service_name = "开直播"
state.resolution = "540p"
state.rtt = "50ms"
state.timestamp = find_timestamp(start="2026年07月25日22时00分", duration="90min")

# summary: 小红书开直播, 与上一段同时段, 画质720p 时延150ms
# intent: 小红书/开直播
state.application_name = "小红书"
state.service_name = "开直播"
state.resolution = "720p"
state.rtt = "150ms"
state.timestamp = find_timestamp(start="2026年07月25日22时00分", duration="90min")

注意: 多个意图若用户说"一起/同时/都"持续某时长或某时段, 则各意图的 start/end/duration 相同。
若用户为不同意图说了不同时段/画质/时延, 必须分别正确对应, 不要张冠李戴。

【取消意图 — 重要】
若用户说"XX不用了/取消XX/把XX去掉/不要XX了"等取消某个意图, 用 # cancel 段表示, 不要再为该意图输出 # intent 段:
# summary: 用户取消FaceTime视频通话
# cancel: FaceTime/视频通话
被取消的意图会从对话状态里移除。用户"保留YY"则 YY 正常输出 # intent 段(沿用 prev_state 未变字段用 coreference)。
示例: prev 有 FaceTime/视频通话 和 Telegram/视频通话, 用户说"FaceTime不用了, Telegram保留"
# summary: 取消FaceTime, 保留Telegram
# cancel: FaceTime/视频通话
# summary: Telegram保留, 沿用上轮状态
# intent: Telegram/视频通话
state.application_name = state.application_name
state.service_name = state.service_name
state.resolution = state.resolution
state.rtt = state.rtt
state.timestamp = find_timestamp(start=state.start_timestamp, duration=state.duration)

输出规则:
1. 只输出 Python 程序(可含注释), 不要 markdown 代码块包裹, 不要解释文字
2. 程序只允许: 对 state.* 赋值、调用 find_* 桩函数、字符串/数字字面量、读取 state.* (coreference)
3. 缺信息时该槽位不要赋值(尤其时间: 用户没说又无法从 prev_state 取的, 不要瞎编)
"""


def _compose_system_py(time_anchor_on: bool = False) -> str:
    return SYSTEM_PROMPT_PY_PRE + _shared_rules(time_anchor_on) + _SYSTEM_PROMPT_PY_MID


SYSTEM_PROMPT_PY = _compose_system_py()
# v27: time_anchor 开时用的压缩版系统 prompt (净长度 < 原版)
SYSTEM_PROMPT_PY_TIME_ANCHOR = _compose_system_py(time_anchor_on=True)


def _schema_text() -> str:
    return schema_python_text()


def _format_prev(prev: dict[str, Any]) -> str:
    """prev_state 用扁平标量 dict 展示(便于 state.xxx 引用)。"""
    from case_format import flatten_params
    return json.dumps(flatten_params(prev), ensure_ascii=False)


def _case_to_program(c: RetrievedCase, id_mode: bool = False) -> str:
    """把 case 的 Δy(嵌套 params) 反编译为 Python 程序(作为 demonstration 的 output)。

    v12.1: 输出补 `# intent:` 头(与系统要求的目标格式严格一致, 修复 v12.0 因 demo 无头
    导致模型模仿省略 intent 头、delta 整段丢弃的问题)。intent key 从 prev+delta 最终态的
    app/svc 推导, 无 app(域外/非保障)时为 none。

    v26 id 协议(id_mode=True): 段头改 id 形式 ——
      prev 有同意图(prev_state 非空且 delta 含沿用) -> `# intent: I1`(更新已有);
      全新意图(prev_state 空) -> `# intent: new`。
      demo 是单意图 case, 统一 I1/new 二分即可与目标协议逐字节一致(v12.0 教训)。
    """
    from case_format import apply_nested, flatten_params
    flat = flatten_params(c.delta)
    lines = []
    ts_args = []
    for slot, val in flat.items():
        if slot in ("start_timestamp", "end_timestamp", "duration"):
            ts_args.append((slot, val))
            continue
        if isinstance(val, dict) and val.get("ref", "").startswith("state."):
            lines.append(f'state.{slot} = {val["ref"]}')
        else:
            lines.append(f'state.{slot} = {val!r}')
    if ts_args:
        parts = []
        for k, v in ts_args:
            if isinstance(v, dict) and v.get("ref", "").startswith("state."):
                parts.append(f'{k[0]}={v["ref"]}' if k != "duration" else f'duration={v["ref"]}')
            else:
                arg = "start" if k == "start_timestamp" else "end" if k == "end_timestamp" else "duration"
                parts.append(f'{arg}={v!r}')
        lines.append("state.timestamp = find_timestamp(" + ", ".join(parts) + ")")
    # intent 头: prev+delta 最终态的 app/svc
    final_flat = flatten_params(apply_nested(c.prev_state or {}, c.delta or {}))
    app, svc = final_flat.get("application_name"), final_flat.get("service_name")
    app = app if isinstance(app, str) else None
    svc = svc if isinstance(svc, str) else None
    if id_mode:
        # 更新已有意图(prev 非空) 用 I1; 全新意图用 new; 域外/无 app 用 none
        # v26.2: demo 同步加身份判定行(与目标协议逐字节一致, v12.0 教训)
        if not app:
            header = "# intent: none"
        elif c.prev_state:
            final_desc = "/".join(x for x in (app, svc) if x)
            header = f"# 身份判定: 更新已有意图 I1({final_desc})\n# intent: I1"
        else:
            header = "# 身份判定: 与已有意图无关, 新建\n# intent: new"
    else:
        header = f"# intent: {app}/{svc}" if app else "# intent: none"
    return header + "\n" + "\n".join(lines) if lines else header


def _record_sourced_slots(delta: dict[str, Any]) -> list[tuple[str, str, str]]:
    """从 delta(嵌套) 提取 record/Memory sourced 字段: [(slot, value, source_type)]。

    source_type 为 Record/Memory 的字段才是画像 coreference 来源, 需选择链。
    """
    out: list[tuple[str, str, str]] = []
    if not isinstance(delta, dict):
        return out
    for slot, spec in delta.items():
        if not isinstance(spec, dict):
            continue
        # 顶层 value
        if "value" in spec and isinstance(spec.get("value"), str):
            st = spec.get("source_type", "")
            if st in ("Record", "Memory"):
                out.append((slot, spec["value"], st))
        # min_value/max_value 内
        for k in ("min_value", "max_value"):
            inner = spec.get(k)
            if isinstance(inner, dict) and isinstance(inner.get("value"), str):
                st = inner.get("source_type", "")
                if st in ("Record", "Memory"):
                    out.append((slot, inner["value"], st))
    return out


_RECORD_COREF_WORDS = ("上次", "记录", "历史", "之前", "那条", "那次", "最近办过", "平时", "习惯")
_SERVICE_WORDS_DEMO = ("视频通话", "视频", "开直播", "看直播", "会议", "云游戏")


def _build_selection_chain(c: RetrievedCase) -> str:
    """为 record coref 类 case 生成选择链 summary (demonstration 用)。

    仅当 delta 含 Record/Memory sourced 字段时生成; 否则返回空串(调用方回退占位)。
    零作弊: 只用 utterance 指代条件 + delta 已有值回溯, 不读 GT evidence.query。
    """
    slots = _record_sourced_slots(c.delta or {})
    if not slots:
        return ""
    utt = c.utterance or ""
    parts: list[str] = []
    # 指代来源: Record(上次/记录) vs Memory(平时/习惯)
    is_memory = any(w in utt for w in ("平时", "习惯"))
    src_label = "profile(平时习惯)" if is_memory else "record(上次记录)"
    # 指代条件
    conds: list[str] = []
    for w in _SERVICE_WORDS_DEMO:
        if w in utt:
            conds.append(f"service={w}")
            break
    if "号" in utt or "日" in utt and any(m in utt for m in ("月",)):
        import re
        m = re.search(r"(\d{1,2}月\d{1,2}[号日])", utt)
        if m:
            conds.append(f"date={m.group(1)}")
    if "最近一条" in utt or "最近那次" in utt or "最近办过" in utt:
        conds.append("取最近")
    cond_str = ("按" + "+".join(conds) + "过滤") if conds else "无具体过滤条件取最近"
    # 字段取值
    field_parts = []
    for slot, val, st in slots:
        src = "profile" if st == "Memory" else "record"
        field_parts.append(f"{slot}={val}({src})")
    parts.append(f"用户话语含画像指代({src_label}), 取字段: {', '.join(field_parts)}")
    parts.append(f"选择链: {cond_str} → 取对应字段值")
    return "; ".join(parts) + " (PAR: 显式推理 record 选择, 防全空)"


def _build_correction_chain(c: RetrievedCase) -> str:
    """为改口/否定类 case 生成改口 summary (demonstration 用)。

    零作弊: 只对比 prev_state 与 delta 的同名字段值差异回溯, 不读 GT action_types。
    """
    from case_format import flatten_params
    prev = flatten_params(c.prev_state or {})
    delta = flatten_params(c.delta or {})
    changes: list[str] = []
    for k, v in delta.items():
        vv = v.get("ref") if isinstance(v, dict) else v
        pv = prev.get(k)
        if isinstance(vv, str) and isinstance(pv, str) and pv and pv != vv:
            changes.append(f"{k}: {pv} → {vv}")
    if not changes:
        return ""
    return ("用户改口/否定: " + ", ".join(changes)
            + " (纠正之前轮旧值, 必须覆盖 prev_state, 不要沿用旧值)")


def _format_case(c: RetrievedCase, idx: int, id_mode: bool = False) -> str:
    tag = "正例" if c.validity == "valid" else "负例"
    program = _case_to_program(c, id_mode=id_mode)
    reason = c.invalid_reason or "none"
    chain = _build_selection_chain(c) or _build_correction_chain(c)
    summary = chain if chain else "(示例, 省略)"
    if id_mode:
        # v26: demo 的 prev 也按 id 呈现(单意图 case 统一 I1; prev 空则无已有意图)
        if c.prev_state:
            prev_block = f"# 已有意图:\n#   I1 = {_format_prev(c.prev_state)}"
        else:
            prev_block = "# 已有意图: (无)"
        return (
            f"# === Demonstration {idx} ({tag}, score={c.score:.3f}, invalid_reason={reason}) ===\n"
            f"{prev_block}\n"
            f"# reference_time: {c.reference_time}\n"
            f"# user: {c.utterance}\n"
            f"# summary: {summary}\n"
            f"{program}\n"
        )
    return (
        f"# === Demonstration {idx} ({tag}, score={c.score:.3f}, invalid_reason={reason}) ===\n"
        f"prev_state = {_format_prev(c.prev_state)}\n"
        f"# reference_time: {c.reference_time}\n"
        f"# user: {c.utterance}\n"
        f"# summary: {summary}\n"
        f"{program}\n"
    )


def _dedupe_demos(demos: list[RetrievedCase]) -> list[RetrievedCase]:
    seen: set[frozenset] = set()
    out: list[RetrievedCase] = []
    for c in demos:
        s = frozenset((c.delta or {}).keys())
        if s in seen:
            continue
        seen.add(s)
        out.append(c)
    return out


def _active_app_hint(utterance: str) -> str:
    """B3 活跃槽掩码: 检测 utterance 提到的 app, 只展示这些 app 的合法 service。"""
    if not utterance:
        return "(无)"
    hits = []
    for app in sorted(APPLICATION_SERVICE_MATRIX.keys(), key=len, reverse=True):
        if app in utterance:
            svcs = sorted(APPLICATION_SERVICE_MATRIX[app])
            hits.append(f"#   {app} -> 合法service: {svcs}")
    oos = ["豆瓣", "京东", "知乎", "美团", "网易云音乐"]
    for app in oos:
        if app in utterance:
            hits.append(f"#   {app} -> 域外app(不受理)")
    return "\n".join(hits) if hits else "#   (话语未明确提及矩阵内app, 需从语义判断)"


def _format_user_context(uc: dict[str, Any]) -> str:
    """用户画像 (B2 DCA: 意图驱动对话信息增强)。"""
    if not uc:
        return "(无画像)"
    lines = ["# 用户画像 profile:"]
    prof = uc.get("profile", {})
    for k, v in prof.items():
        lines.append(f"#   {k} = {v}")
    recs = uc.get("recent_records", []) or []
    lines.append(f"# 近期保障记录 (共{len(recs)}条, 最近在前):")
    for i, r in enumerate(recs[:8]):
        lines.append(
            f"#   [{i}] app={r.get('application_name')} svc={r.get('service_name')} "
            f"res={r.get('resolution')} rtt={r.get('rtt')} "
            f"start={r.get('start_timestamp')} end={r.get('end_timestamp')} "
            f"dur={r.get('duration')} completed={r.get('completed_at')}"
        )
    return "\n".join(lines)


def _shared_task_sections(utterance: str, prev_state: dict[str, Any],
                          retrieved: list[RetrievedCase], user_context: dict[str, Any] | None,
                          id_mode: bool = False) -> str:
    pos = [c for c in retrieved if c.validity == "valid"]
    neg = [c for c in retrieved if c.validity == "invalid"]
    demos = _dedupe_demos(pos[:4] + neg[:2]) or retrieved[:4]
    demos_text = "\n".join(_format_case(c, i + 1, id_mode=id_mode) for i, c in enumerate(demos)) or "(无)"
    schema_text = _schema_text()
    uc_text = _format_user_context(user_context or {})
    active_hint = _active_app_hint(utterance)
    return (
        f"# === SCHEMA ===\n{schema_text}\n\n"
        f"# === APPLICATION_SERVICE_MATRIX ===\n"
        + "\n".join(f"# {app}: {sorted(svcs)}" for app, svcs in sorted(APPLICATION_SERVICE_MATRIX.items()))
        + f"\n\n# === ACTIVE APPS (话语提及的应用, B3 活跃槽掩码) ===\n{active_hint}\n"
        f"# 注: 豆瓣/京东/知乎/美团/网易云音乐 是域外app(不在矩阵), 若用户提了这些, 仍按原话赋值, 校验器会判 matrix_out_application\n\n"
        f"# === USER CONTEXT (用户画像与近期记录) ===\n{uc_text}\n\n"
        f"# === DEMONSTRATIONS (含正例与负例) ===\n{demos_text}\n\n"
    )


def build_prompt(
    utterance: str,
    reference_time: str,
    prev_state: dict[str, Any],
    retrieved: list[RetrievedCase],
    user_context: dict[str, Any] | None = None,
    agent_reply: str | None = None,
    prev_statuses: dict[str, str] | None = None,
    disamb_keys: list[str] | None = None,
    disamb_cands: list[dict] | None = None,
    corr_keys: list[str] | None = None,
    record_rule_hint: str | None = None,
    record_ev_hint: str | None = None,
    cand_mode_hint: str | None = None,
    quality_hint: str | None = None,
    cand_landing: bool = False,
    pending_app_hint: str | None = None,
    time_hint: str | None = None,
    time_anchor_on: bool = False,
    idbook: Any | None = None,
    id_states: dict[str, dict[str, Any]] | None = None,
    memory_hint: str | None = None,
) -> tuple[str, str]:
    """Variant A: 返回 (system_prompt, task_prompt)。Text-to-Python + 强制 # summary PAR。

    可选上下文 (默认 None 不渲染, 由 config 开关控制, 互不影响可独立消融):
      agent_reply   — 上一轮 agent 回复 (config agent_context.enabled)。
                      对症 T3-3 消歧信息不对称 (agent 话术含候选详情)。
      prev_statuses — 各意图状态机状态快照 (config status_prompt.enabled)。
      disamb_keys   — 消歧轮规则注入 (config disambiguation_rule.enabled, v21 修补②):
                      触发时传入 prev_states 的现有 intent_key 列表, 渲染消歧规则段。
      corr_keys     — 改口轮规则注入 (config correction_rule.enabled, v22 修补④):
                      触发时传入 prev_states 的现有 intent_key 列表, 渲染改口规则段。
      record_rule_hint — 无约束"按上次记录"严格规则 (config record_rule.enabled, v24):
                      触发时传入 records[0] (completed_at 最新一条) 的字段明细串。
      pending_app_hint — 应用名未明说规则 (config pending_app_rule.enabled, v26.3 P1):
                      触发时传入"禁止猜 app/待补桩 id 列表"规则段。
      time_hint     — 时间锚点/明说时间证据/时间口径规则段 (config time_anchor/
                      time_extractor/time_rule_*, v27): pipeline 预组装, 原样渲染。
      time_anchor_on — True 时系统 prompt 用压缩版时间规则段 (净长度下降)。
      idbook/id_states — v26 intent-id 协议 (config intent_id_protocol.enabled, 默认关):
                      传入时 TASK 块改 id 协议渲染(prev_state 按 id 列出, 段头写 id/new)。
    """
    extra = ""
    system = SYSTEM_PROMPT_PY_TIME_ANCHOR if time_anchor_on else SYSTEM_PROMPT_PY
    id_mode = idbook is not None and id_states is not None
    if prev_statuses:
        lines = "\n".join(f"#   {k} = {v}" for k, v in sorted(prev_statuses.items()))
        extra += f"# === INTENT STATUS (当前各意图状态机状态: Collecting/Ready/Confirmed) ===\n{lines}\n\n"
    if agent_reply:
        extra += (f"# === AGENT LAST REPLY (上一轮系统回复, 供理解用户当前话语的语境) ===\n"
                  f"# agent: {agent_reply}\n\n")
    if disamb_keys:
        if id_mode:
            # v26: 规则段引用 id 而非 key; 若提供 prev 映射, 优先显示 id 列表
            ids_text = "、".join(sorted(id_states.keys())) if id_states else ""
            head_rule = (f"# 2. 输出必须沿用现有意图 id 作段头: # intent: {ids_text} (禁止新建段);\n")
        else:
            keys_text = "、".join(disamb_keys)
            head_rule = f"# 2. 输出必须沿用现有意图头: # intent: {keys_text} (prev_state 中已存在);\n"
        cand_text = ""
        if disamb_cands:
            cn = "一二三四五六七八九"
            lines = "\n".join(
                f"#   第{cn[i]}条 = application_name={c.get('application_name')}, "
                f"service_name={c.get('service_name')}, resolution={c.get('resolution')}, "
                f"rtt={c.get('rtt')}, completed_at={c.get('completed_at')}"
                for i, c in enumerate(disamb_cands))
            cand_text = (
                "# 候选顺序对照 (系统已按 agent 话术列出顺序解析好, 直接使用, 禁止自行按 records 顺序重新排序):\n"
                f"{lines}\n"
                "# 用户说\"第N条\"即上表第N条; 说日期/app 指代即按上表定位。\n")
        rule1_landing = (
            "# 1. 选中的候选记录按如下规则落地:\n"
            "#    a. 当前意图的 application_name 为空(用户从未明说 app): 该记录的 application_name/"
            "service_name/resolution/rtt 全量写入本意图;\n"
            "#    b. 当前意图已有 application_name(用户此前明说): 当前意图的身份以用户明说为准——"
            "严禁改写 application_name/service_name, 即使用户选中的候选记录身份与之不同; 只把选中记录中此前悬空的"
            "字段(如 rtt)写入, 且不得覆盖用户明说的其他字段(如清晰度);\n"
        )
        rule1_block = (
            "# 1. 不得新建 intent 段: 候选记录中的 app/service 只是取值来源, 禁止用作 application_name/service_name 输出;\n"
        ) if not cand_landing else rule1_landing
        extra += (
            "# === DISAMBIGUATION RULE (消歧轮规则, 本轮生效, 优先级最高) ===\n"
            "# 上一轮 agent 列出了多条历史保障记录候选让用户挑选, 用户本轮是在【选择其中一条】, 不是提出新需求。必须遵守:\n"
            + rule1_block
            + f"{head_rule}"
            "# 3. 序数/日期指代【按 agent 话术里列出候选的顺序】对齐 (第一条=agent 说的第一条); "
            "画像 records 列表的顺序与 agent 话术无关, 禁止用 records[N] 数序数; "
            "画像 records 仅用于补齐 agent 话术里没写出的字段值, 按 app+完成日期 定位记录;\n"
            f"{cand_text}"
            + (
                "# 4. 用户此前明说的字段值(如时间、清晰度 540P)优先于记录值, 禁止被记录覆盖;\n"
                "# 5. # summary 写明: 用户选择了 agent 话术中的哪条候选(日期/app) + 落地了哪些字段。\n\n"
                if cand_landing else
                "# 4. 只取被选择记录中本轮需要的字段值 (通常是 rtt/resolution); 禁止覆盖 prev_state 已有的字段值, 尤其时间字段;\n"
                "# 4.5 若此前某轮的记录指代字段曾悬空留空(如 rtt), 本轮选定记录后必须立即用所选记录的字段值补上该空缺槽位, 不得继续留空;\n"
                "#     被选记录的 app/service 与当前意图不同不构成拒绝补值的理由——记录只是取值来源(时延/画质等),\n"
                "#     应用名/服务名本身仍保持当前意图不变;\n"
                "# 5. # summary 写明: 用户选择了 agent 话术中的哪条候选(日期/app) + 从中取了哪个字段值。\n\n"
            )
        )
    if corr_keys:
        if id_mode:
            ids_text = "、".join(sorted(id_states.keys())) if id_states else ""
            corr_head = (f"# 1. 更新已有意图时段头写该意图的 id (如 # intent: {ids_text}); "
                         "被更正的 app/service 在段内直接赋新值即可, id 不变, 这是同一意图的更正, 不是新意图;\n")
        else:
            keys_text = "、".join(corr_keys)
            corr_head = (f"# 1. # intent: 头沿用 prev_state 现有 key: {keys_text} (即使更正的是 app/service 也用旧 key 作段头, "
                         "被更正的 app/service 在段内直接赋新值即可, 这是同一意图的更正, 不是新意图);\n")
        extra += (
            "# === CORRECTION RULE (改口轮规则, 本轮生效, 优先级最高) ===\n"
            "# 用户本轮在改口/更正之前说过的内容。必须遵守:\n"
            f"{corr_head}"
            "# 2. 只把被更正的字段赋新值; 其余字段一律沿用 prev_state 的值, 不得丢弃/留空;\n"
            "# 3. 沿用字段用 coreference (state.xxx) 即可 (段头沿用旧 key, prev 可正常解析);\n"
            "# 4. # summary 写明: 用户把哪个字段从什么改成了什么。\n\n"
        )
    if quality_hint:
        extra += quality_hint + "\n"
    if cand_mode_hint:
        extra += cand_mode_hint + "\n"
    if record_ev_hint:
        extra += record_ev_hint + "\n"
    if record_rule_hint:
        extra += (
            "# === RECORD RULE (上次记录严格规则, 本轮生效, 优先级最高) ===\n"
            "# 用户本轮说\"按上次记录/按上次\"且未指定任何筛选条件(未提具体 app/service/日期/第N条)。\n"
            "# 严格规则: \"上次记录\" = 画像 records 中 completed_at 最新的一条(即列表 records[0]):\n"
            f"#   records[0] = {record_rule_hint}\n"
            "# 必须遵守:\n"
            "# 1. 被\"上次记录\"指代的字段(如\"时延按上次记录\" -> rtt)必须取 records[0] 的对应字段值;\n"
            "# 2. 禁止使用 profile 习惯值(如\"常用时延要求\")或其他记录的字段值代替;\n"
            "# 3. 用户本轮明确说出的字段(如 app/service/画质/时间)以话语为准, 不要用 records[0] 覆盖;\n"
            "#    仅当用户说\"按上次记录办/照上次那样办\"整单照抄时, 才连 app/service 一起取 records[0];\n"
            "# 4. # summary 写明: 取 records[0] 的哪个字段值。\n\n"
        )
    if pending_app_hint:
        extra += pending_app_hint + "\n"
    if time_hint:
        extra += time_hint + "\n"
    mem_h = memory_hint or ((user_context or {}).get("memory_hint") if isinstance(user_context, dict) else None)
    if mem_h:
        extra += str(mem_h) + "\n"
    if id_mode:
        # ---------- v26 intent-id 协议 TASK 块 ----------
        prev_block = idbook.describe_prev(id_states)
        task_prompt = (
            _shared_task_sections(utterance, prev_state, retrieved, user_context, id_mode=True)
            + extra
            + f"# === TASK (intent-id 协议) ===\n"
            f"# 已有意图 (id = 当前状态; 更新某意图时段头写它的 id, 新建意图段头写 # intent: new):\n"
            f"{prev_block}\n\n"
            f"# 段头协议(必须严格遵守):\n"
            f"#   # intent: I1   -> 更新 id 为 I1 的已有意图(即使改口改的是 app/service 也写原 id);\n"
            f"#   # intent: new  -> 新建一个意图(harness 自动分配新 id);\n"
            f"#   # cancel: <应用/服务名>   -> 取消该意图(取消段必须写意图的 application/service 名字, 不要写 id);\n"
            f"#   取消【同类多份保障之一】时(如两份同应用同服务的保障只撤销一份), 段头写那个意图的 id"
            f"(如 # cancel: I2), 并在 summary 写明撤销的是哪一份(按时段/序数);\n"
            f"# 禁止把 app/service 名写进段头(段头只允许 I1/I2/.../new), app/service 一律作为字段在段内赋值。\n"
            f"# 身份判定(强制): 每个段头前必须先写一行 '# 身份判定: ...'(≤20字), 写明本段对应哪个已有 id、"
            f"或为什么与所有已有意图无关(对照上面列出的每个 id 的 application/service 逐一核对)。\n"
            f"# 用户话语是在【修改/补充/确认某个已有保障】时, 必须写那个意图的 id, 禁止写 new——"
            f"只有确定是用户另外提出的新保障才写 new。\n"
            f"# 只写【发生变化的字段】的赋值; 未变化的字段不要写 state.xxx = state.xxx 沿用"
            f"(未写的字段自动沿用该意图当前状态; 写了反而可能因引用不存在的槽位而出错)。\n"
            f"# 每段的 state 用该意图当前状态初始化, coreference(state.xxx) 读的是本意图自己的字段。\n"
            f"reference_time = {reference_time!r}\n"
            f"user_utterance = {utterance!r}\n"
            f"# 写出更新 state 的 Python 程序 (每个 intent 段前必须有 # summary):\n"
        )
        return system, task_prompt
    task_prompt = (
        _shared_task_sections(utterance, prev_state, retrieved, user_context)
        + extra
        + f"# === TASK ===\n"
        f"prev_state = {_format_prev(prev_state)}\n"
        f"reference_time = {reference_time!r}\n"
        f"user_utterance = {utterance!r}\n"
        f"# 写出更新 state 的 Python 程序 (每个 intent 段前必须有 # summary):\n"
    )
    return system, task_prompt

