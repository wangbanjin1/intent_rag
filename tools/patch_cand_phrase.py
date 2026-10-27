import io
p='candidate_book.py'
s=io.open(p,encoding='utf-8').read()
old='''_CAND_SELECT = re.compile(r"第\s*[一二三四五六七八九十\d]+\s*[条个]|就按|选第|按最近|按最后|第一条不要|第二条不要")'''
new='''# 选择语: 须含序数选择/指代选择, 排除纯确认语 ("就按这个/就这样/按这个处理" 是 confirm 非选择)
_CAND_SELECT = re.compile(
    r"第\s*[一二三四五六七八九十\d]+\s*[条个]|(就|我|先)?按最近(一条)?来|(第一|第二|第三)条(不要|吧)")
_CAND_CONFIRM_RE = re.compile(r"就按这个|就这样|按这个(处理|来|继续)|确认按?这个")'''
assert s.count(old)==1
s=s.replace(old,new)
old2='''def select_phrase(utterance: str) -> bool:
    """t2 选择语检测 (用户在选候选)。"""
    return bool(_CAND_SELECT.search(utterance or ""))'''
new2='''def select_phrase(utterance: str) -> bool:
    """t2 选择语检测 (用户在选候选)。纯确认语 (confirm) 不算选择。"""
    u = utterance or ""
    if _CAND_CONFIRM_RE.search(u):
        return False
    return bool(_CAND_SELECT.search(u))'''
assert s.count(old2)==1
s=s.replace(old2,new2)
io.open(p,'w',encoding='utf-8').write(s)
print('select_phrase refined')
