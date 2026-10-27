# -*- coding: utf-8 -*-
"""核心问题: 话术解析失败时回退 _cand_listing (降序), 与用户口径序(话术序)可能相反.
修法探索: 用 U2 话语中的日期/时段词与候选明细的 completed_at 匹配来确定性对齐顺序.
960 例: U2='就选第一条记录' 无日期 — 无法从 U2 对齐. 但 A1 话术里有日期!
('第一条是云游戏, 6月25号凌晨2点完成; 第二条是虎牙开直播, 7月16号...')
→ 修法 = 增强 _parse_disamb_candidates: '第一条是云游戏, 6月25号...' 型
候选无 app 名 (svc+日期+时段), 匹配分算法已支持 svc+md+hour 打分, 为何 None?
回看 960 flat records: 咪咕快游/云游戏 6/25, 虎牙/开直播 7/16.
A1 段: '第一条是云游戏，6月25号凌晨2点完成' → attrs={svc:云游戏, md:(6,25), hour:2}
  _rec_match_score: svc+2, md+2, hour+2 = 6 ≥3 应成功! 为何 None?
可能: _DISAMB_SPLIT_RE 切分问题. 试."""
import sys, json
sys.path.insert(0, '.')
import pipeline as pl
a1 = '我查到两条：第一条是云游戏，6月25号凌晨2点完成；第二条是虎牙开直播，7月16号凌晨2点完成，业务和日期正好对应不同记录，您选第一条还是第二条？'
text = pl._DISAMB_QCUT_RE.split(a1)[0]
segs = [x.strip('，。；、 ') for x in pl._DISAMB_SPLIT_RE.split(text)]
print('segs:', segs)
merged = []
for seg in segs:
    for pre in pl._DISAMB_PREFIX_RES:
        new = pre.sub('', seg, count=1)
        if new != seg:
            _ba, _aa = pl._cand_attrs(seg), pl._cand_attrs(new)
            if _ba.get('app') and not _aa.get('app'):
                new = _ba['app'] + ' ' + new
            seg = new.strip('，。；、 ')
            break
    if not seg: continue
    a = pl._cand_attrs(seg)
    if set(a) == {'app'} and re.search(r'还是|哪条|哪一|对不上|吗|？|\?', seg) if False else False: pass
    if set(a) == {'app'} and pl.re.search(r'还是|哪条|哪一|对不上|吗|？|\?', seg):
        continue
    merged.append(seg)
print('merged:', merged)
for seg in merged:
    print(repr(seg), 'attrs:', pl._cand_attrs(seg))
