# -*- coding: utf-8 -*-
"""修法①回归: 模拟'白名单扩展+结构化判定'对全部 d930 T2 类 + d6 正向 case 的影响.
最终设计: _generic_undecided 改为【未定词逐个判定其限定对象】——
未定词前 8 字上下文含 (开始|结束|截止)时间 或 应用|时延|rtt|时长|画质|分辨率|走|选 → 该未定词非时间未定;
只有【时间词本身在未定对象里】(如'时间还没定'/'什么时候还没定'或未定词紧邻 或/还是 连接两个时间点) 才判时间未定.
更简单且更稳的口径: 全局未定判定 = 话语含未定词 且 未定词前文【无任何新关键词】且 未定对象含时间词."""
import sys, re
sys.path.insert(0, '.')
import time_extractor as tex

NEW_KW = ('应用', '时延', 'rtt', '时长', '画质', '分辨率', '清晰度', '走', '选')
def new_generic(u):
    """新口径: 全部未定词都被槽位/选择动词限定 → 非全局; 任一未定词悬空(前文无关键词) → 全局."""
    found_any = False
    for w in ('还没定', '没定', '不确定', '先确认', '待定', '还没想好'):
        i = u.find(w)
        while i >= 0:
            found_any = True
            ctx = u[max(0, i - 8):i]
            if not (tex._UNDECIDED_SLOT_SCOPED_RE.search(ctx) or any(k in ctx for k in NEW_KW)):
                return True  # 悬空未定词 → 全局
            i = u.find(w, i + 1)
    return False

# d930 T2 类 10 例 (应从抑制变组装)
t2_cases = [
 '我想申请云游戏保障，至少720p，明天晚上11点到后天凌晨1点总共两个小时，但应用和时延还没定，是网易云游戏20ms还是咪咕快游30ms，你看哪个合适？',
 '帮我安排腾讯会议的会议保障，清晰度至少720P，7月14号上午10点结束，时长我还没定，是150分钟还是120分钟，时延按之前那条记录。',
 '麻烦申请2026年07月31日21时00分到21时30分的视频通话保障，分辨率360p、时延150ms以内；应用还没定，QQ和Telegram两种安排分别帮我看看。',
 '明天下午1点到2点，一个小时，我要看直播，用1080P，是走斗鱼直播、时延上限20ms，还是走拼多多、时延上限30ms，我还没定。',
 '麻烦申请2026年07月31日07时00分到08时00分的视频通话保障，分辨率360p、时延150ms以内；应用还没定，QQ和Telegram两种安排分别帮我看看。',
 '我明天晚上8点到晚上11点需要三个小时保障，画质360p、时延30ms以内，是用云视讯开会议还是用Telegram做视频通话我还没定。',
 '帮我保障咪咕快游的云游戏，高清，时延不能超过80ms，明天凌晨4点结束，用三个半小时还是三个小时我还没定。',
 '给咪咕视频做保障，720P，明天凌晨12点到12点半，时延上限我还没定，100毫秒和50毫秒哪个更合适？',
 '请帮我保障淘宝看直播，明天下午1点到1点半半小时，我还没定，是1080p配时延上限100ms，还是720p配时延上限20ms？',
 '我想申请微信看直播保障，分辨率1080P，时间定在7月30号凌晨5点到上午6点，但时延上限我还没定，在30ms和150ms之间犹豫。',
]
ref = '2026年07月12日12时00分'
print('== d930 T2 类 (期望: 时间组装恢复) ==')
n_ok = 0
for u in t2_cases:
    old_g = tex._generic_undecided(u)
    new_g = new_generic(u)
    ev = tex.extract(u, tex.parse_reference_time(ref))
    assembled = ev.start is not None or ev.end is not None
    mark = 'OK' if (not new_g and assembled) or (new_g and not assembled) else 'CHECK'
    if not new_g: n_ok += 1
    print(f'{mark} new_global={new_g} (old={old_g}) assembled={assembled} | {u[:30]}')
print(f'恢复组装: {n_ok}/10')
# d6 正向 case (应保持抑制)
print()
print('== d6 抑制正向 case (期望: 保持抑制) ==')
keep = [
 ('开始时间我还没定，是11点半还是12点', '时间未定'),
 ('时间还没定，是8点还是9点', '时间未定'),
]
for u, note in keep:
    old_g = tex._generic_undecided(u)
    new_g = new_generic(u)
    print(f'{"OK" if new_g else "FAIL"} {note} | new_global={new_g} (old={old_g})')
