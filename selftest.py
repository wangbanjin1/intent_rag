"""离线自检:Text-to-Python 全链路(不调 LLM/API)。

用法: python selftest.py
"""
from __future__ import annotations

import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("DASHSCOPE_API_KEY", "dummy")

import json
import numpy as np

from case_format import flatten_params, nested_from_flat
from python_state import execute_program, schema_python_text
from prompt_builder import build_prompt, _case_to_program
from retrieval_bank import CaseBank
from retriever import DiverseRetriever
from validator import validate


def _nested_douban():
    # 注意: 新 schema 下豆瓣不在受理矩阵 -> matrix_out_application
    # 此 fixture 仅用于 coreference 沙箱测试(state.rtt 读 prev_state),不走 validator valid 断言
    return {
        "application_name": {"value": "豆瓣", "source_type": "Turn", "source_ref": "U1"},
        "service_name": {"value": "视频", "source_type": "Turn", "source_ref": "U1"},
        "resolution": {"min_value": {"value": "2160p", "source_type": "Turn", "source_ref": "U1"}},
        "rtt": {"max_value": {"value": "100ms", "source_type": "Turn", "source_ref": "U1"}},
        "timestamp": {
            "start_timestamp": {"value": "2026年07月21日12时00分", "source_type": "Context", "source_ref": "U1"},
            "end_timestamp": {"value": "2026年07月21日13时30分", "source_type": "Turn", "source_ref": "U1"},
            "duration": {"value": "90min", "source_type": "Turn", "source_ref": "U1"},
        },
    }


def _nested_tencent():
    # 合法 app (腾讯视频/视频) 用于 validator valid 断言
    return {
        "application_name": {"value": "腾讯视频", "source_type": "Turn", "source_ref": "U1"},
        "service_name": {"value": "视频", "source_type": "Turn", "source_ref": "U1"},
        "resolution": {"min_value": {"value": "2160p", "source_type": "Turn", "source_ref": "U1"}},
        "rtt": {"max_value": {"value": "100ms", "source_type": "Turn", "source_ref": "U1"}},
        "timestamp": {
            "start_timestamp": {"value": "2026年07月21日12时00分", "source_type": "Context", "source_ref": "U1"},
            "end_timestamp": {"value": "2026年07月21日13时30分", "source_type": "Turn", "source_ref": "U1"},
            "duration": {"value": "90min", "source_type": "Turn", "source_ref": "U1"},
        },
    }


def test_sandbox_execute():
    """LLM 风格的 Python 程序 -> 沙箱执行 -> 嵌套 params Δy。"""
    code = (
        'state.application_name = "腾讯视频"\n'
        'state.service_name = "视频"\n'
        'state.resolution = "2160p"\n'
        'state.rtt = "100ms"\n'
        'ts = find_timestamp(start="2026年07月21日12时00分", end="2026年07月21日13时30分", duration="90min")\n'
        'state.timestamp = ts\n'
    )
    delta = execute_program(code, {})
    assert delta["application_name"]["value"] == "腾讯视频"
    assert delta["resolution"]["min_value"]["value"] == "2160p"
    assert delta["timestamp"]["start_timestamp"]["value"] == "2026年07月21日12时00分"
    assert delta["timestamp"]["duration"]["value"] == "90min"
    print("[OK] 沙箱执行 LLM 程序 -> 嵌套 params Δy 格式正确")


def test_sandbox_coreference():
    """coreference: state.rtt / state.start_timestamp 读 prev_state。"""
    prev = _nested_douban()
    code = (
        'state.application_name = "豆瓣"\n'
        'state.service_name = "视频"\n'
        'state.rtt = state.rtt\n'
        'ts = find_timestamp(start=state.start_timestamp, end=state.end_timestamp, duration="90min")\n'
        'state.timestamp = ts\n'
    )
    delta = execute_program(code, prev)
    assert delta["rtt"]["max_value"]["value"] == "100ms", delta["rtt"]
    assert delta["timestamp"]["start_timestamp"]["value"] == "2026年07月21日12时00分"
    assert delta["timestamp"]["end_timestamp"]["value"] == "2026年07月21日13时30分"
    print("[OK] coreference: state.xxx 直接解析 prev_state 值 (Python 变量引用)")


def test_sandbox_security():
    """沙箱应拒绝 import / 危险 builtins。"""
    for bad in ['import os', 'open("x")', '__import__("os")', 'exec("print(1)")']:
        try:
            execute_program(bad, {})
            print(f"[X] 安全失败: {bad!r} 未被拦截")
            raise AssertionError(bad)
        except Exception:
            pass
    print("[OK] 沙箱安全: import/open/exec/__import__ 均被拦截")


def test_case_to_program_roundtrip():
    """case 的 Δy(嵌套) -> 反编译成程序 -> 再执行 -> 应得到等价 Δy。"""
    from case_format import extract_turn_cases
    session = json.loads(open("data/sample_session.json", "r", encoding="utf-8").read())
    cases = extract_turn_cases(session)
    c = cases[0]
    # 构造 RetrievedCase 风格对象
    class RC:
        pass
    rc = RC()
    rc.delta = c["delta"]; rc.prev_state = c["prev_state"]; rc.reference_time = c["reference_time"]
    rc.utterance = c["utterance"]; rc.validity = c["validity"]; rc.invalid_reason = c["invalid_reason"]
    rc.intent = c["intent"]; rc.score = 0.9
    program = _case_to_program(rc)
    delta2 = execute_program(program, c["prev_state"])
    # 关键槽位应一致
    assert flatten_params(delta2)["application_name"] == "豆瓣"
    assert flatten_params(delta2)["resolution"] == "2160p"
    assert flatten_params(delta2)["start_timestamp"] == "2026年07月21日12时00分"
    print("[OK] Δy -> 程序 -> 再执行 -> 等价(论文 §3.3.2 canonicalize 可行)")


def test_validator_nested():
    ref = "2026年07月20日23时00分"
    nested = _nested_tencent()
    out = validate({"intent": "腾讯视频视频保障", "params": nested}, ref, prev_state={})
    assert out["validity"] == "valid", out
    assert out["final_state"]["application_name"]["value"] == "腾讯视频"
    print("[OK] validator: 嵌套 params Δy(腾讯视频) -> final_state -> valid")

    # 豆瓣不在受理矩阵 -> matrix_out_application
    douban = _nested_douban()
    out = validate({"intent": "豆瓣视频保障", "params": douban}, ref, prev_state={})
    assert out["invalid_reason"] == "matrix_out_application", out
    print("[OK] validator: 豆瓣视频 -> matrix_out_application (豆瓣不在受理矩阵)")


class _FakeEmbedder:
    dim = 64
    def embed(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**31))
        v = rng.random(self.dim).astype("float32")
        return (v / (np.linalg.norm(v) + 1e-8)).tolist()
    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def test_prompt_and_mmr():
    bank = CaseBank.load("data/case_bank_train_d5.jsonl", _FakeEmbedder())
    assert len(bank) > 0
    ret = DiverseRetriever(bank, top_k=5, pool_n=50, alpha=0.3)
    hits = ret.retrieve("腾讯视频保障,4K,明天12点到下午1点半。", {})
    assert hits
    sys_, task = build_prompt("腾讯视频,4K,明天12点。", "2026年07月20日23时00分", {}, hits)
    # task prompt 应含 Python 类定义和 state 赋值
    assert "class State" in task
    assert "find_timestamp" in task
    assert "state.application_name" in task or "state." in task
    print(f"[OK] Text-to-Python prompt: class State + find_* + demonstrations是Python程序")
    print(f"[OK] MMR: {len(hits)} 条, 多样性 {len({frozenset((h.delta or {}).keys()) for h in hits})} 种组合")


def test_record_selector():
    """v27.1 record_selector: 记录指代预判判定树 (悬空/定位/退出)。"""
    import record_selector as rs

    recs = [
        {"application_name": "咪咕快游", "service_name": "云游戏", "rtt": {"max_value": "100ms"},
         "completed_at": "2026年07月01日12时00分", "record_id": "R-005"},   # 画像按 completed_at 降序, [0]=最新
        {"application_name": "咪咕快游", "service_name": "云游戏", "rtt": {"max_value": "150ms"},
         "completed_at": "2026年07月01日11时00分", "record_id": "R-001"},
        {"application_name": "快手", "service_name": "看直播", "rtt": {"max_value": "30ms"},
         "completed_at": "2026年06月23日17时00分", "record_id": "R-004"},
    ]

    # 预选查询式 + 多命中 → 悬空 (S-000123 型: "查7月1号完成的云游戏记录" 2 条候选)
    ev = rs.record_evidence("时延帮我查一下7月1号完成的云游戏记录，最后选中哪条就用哪条的时延",
                            {"recent_records": recs}, None, disambiguation_active=True)
    assert ev and ev.mode == "defer" and len(ev.matched) == 2, ev
    hint = rs.render_hint(ev)
    assert "留空" in hint and "待消歧" in hint

    # 无约束多候选 + T3-3 → 悬空 (S-000192 型)
    ev = rs.record_evidence("时延按我最近办过的记录里最终选中的那条来",
                            {"recent_records": recs}, None, disambiguation_active=True)
    assert ev and ev.mode == "defer"

    # 无约束 + 非消歧会话 → latest records[0] (58 例 GT 口径); 渲染回归 record_rule (defer-only)
    ev = rs.record_evidence("时延按上次记录", {"recent_records": recs}, None,
                            disambiguation_active=False)
    assert ev and ev.mode == "latest" and ev.target_record["record_id"] == "R-005"
    assert rs.render_hint(ev) is None

    # 约束唯一命中 → unique; 渲染同样回归 record_rule
    ev = rs.record_evidence("时延按之前快手看直播的记录来",
                            {"recent_records": recs}, None, disambiguation_active=False)
    assert ev and ev.mode == "unique" and ev.target_record["record_id"] == "R-004"
    assert rs.render_hint(ev) is None

    # 消歧选择轮 (序数) → None, disambiguation_rule 接管
    ev = rs.record_evidence("就按第二条记录来", {"recent_records": recs}, None,
                            disambiguation_active=True)
    assert ev is None

    # 选择轮指代resolve ("就按第二条记录来"句首变体) → None (v27.3-d930 收窄后仍覆盖)
    ev = rs.record_evidence("就按第二条记录来", {"recent_records": recs}, None,
                            disambiguation_active=True)
    assert ev is None
    # v27.3-d930: t1 复合句尾部"就按我这次说的"不是选择轮 (S-000844 型, 应 defer)
    ev = rs.record_evidence("时延帮我查6月11号完成的斗鱼直播记录并按最后选中的记录来，其他就按我这次说的。",
                            {"recent_records": recs}, None, disambiguation_active=True)
    assert ev is not None and ev.mode == "defer", ev

    # 沿用式/无指代/无记录 → None
    assert rs.record_evidence("时延跟上次一样", {"recent_records": recs}, None,
                              disambiguation_active=False) is None
    assert rs.record_evidence("时延按上次记录", {"recent_records": []}, None,
                              disambiguation_active=True) is None
    assert rs.record_evidence("时延不超过50ms", {"recent_records": recs}, None,
                              disambiguation_active=False) is None

    # 筛选词未命中 (svc 误过滤) 非消歧 → latest 全量回退, 不悬空 (S-000006 型防误伤)
    ev = rs.record_evidence("拼多多开直播的时延按上次记录", {"recent_records": recs}, None,
                            disambiguation_active=False)
    assert ev and ev.mode == "latest"
    print("[OK] record_selector: 悬空/定位/选择轮退出/零命中回退/沿用式排除 全过")


def test_time_extractor():
    import time_extractor as tex

    # 日期表: 相对词触发, 预计算正确
    rd = tex.parse_reference_time("2026年07月22日23时00分")
    tbl = tex.build_date_table(rd, "后天凌晨4点到上午7点")
    assert tbl and dict(tbl)["后天"].startswith("7月24日"), tbl
    assert tex.build_date_table(rd, "7月25号上午10点") is None  # 无相对词不触表

    # 相对日期锚定: 明天/后天不 off-by-one (S-000119/S-000249)
    ev = tex.extract("结束时间改到明天晚上9点半，时长改成一个半小时",
                     tex.parse_reference_time("2026年07月24日13时00分"))
    assert tex._fmt(ev.end) == "2026年07月25日21时30分" and ev.duration_min == 90
    ev = tex.extract("后天凌晨4点到上午7点，三个小时", rd)
    assert tex._fmt(ev.start) == "2026年07月24日04时00分"
    assert tex._fmt(ev.end) == "2026年07月24日07时00分" and not ev.has_contradiction

    # 凌晨12点 = 00:00 (S-000104)
    ev = tex.extract("今天晚上11点到明天凌晨12点",
                     tex.parse_reference_time("2026年07月20日06时00分"))
    assert tex._fmt(ev.end) == "2026年07月21日00时00分"

    # 矛盾检测: start>end (S-000066/S-000227 口径 = 保字面)
    ev = tex.extract("今天9点开始、8点半结束",
                     tex.parse_reference_time("2026年07月14日07时00分"))
    assert ev.has_contradiction and ev.start and ev.end and ev.start > ev.end
    ev = tex.extract("从7月23号下午3点半开始，到下午3点结束，办三个小时吧",
                     tex.parse_reference_time("2026年07月22日06时00分"))
    assert ev.has_contradiction and ev.duration_min == 180

    # 否定词防呆 + 槽位命名角色 (改口轮): "不是8点半" 剔除, "改成" 归 end
    ev = tex.extract("结束时间不是8点半，改成今天中午12点",
                     tex.parse_reference_time("2026年07月14日07时00分"))
    assert len(ev.mentions) == 1 and tex._fmt(ev.mentions[0].dt) == "2026年07月14日12时00分"
    assert ev.mentions[0].role == "end"

    # 候选时段信号 (S-000185): 或者 + 还没定 -> 不组装三元组
    ev = tex.extract("我想申请虎牙开直播保障，时延最高80毫秒，直播一个半小时，画质要流畅，"
                     "时间还没定，8月2号凌晨2点到3点半，或者凌晨2点半到4点。",
                     tex.parse_reference_time("2026年08月01日09时00分"))
    assert ev.has_candidate and ev.start is None and ev.end is None
    assert ev.duration_min == 90 and len(ev.mentions) == 4

    # 多意图句不组装 (S-000154/S-000146): 绑定留给模型, 证据全列出
    ev = tex.extract("明天上午11点结束、用一个小时；爱奇艺到下午1点半结束、用三个小时",
                     tex.parse_reference_time("2026年07月05日10时00分"))
    assert ev.start is None and ev.end is None and len(ev.mentions) == 2
    assert tex._fmt(ev.mentions[1].dt) == "2026年07月06日13时30分"  # 跨子句继承明天
    assert sorted(d for _, d in ev.durations) == [60, 180]

    # 明说 end 唯一直接组装 (S-000099 turn1)
    ev = tex.extract("7月22号晚上9点半结束，开始时间我还不确定是19点半还是20点整",
                     tex.parse_reference_time("2026年07月22日19时00分"))
    assert ev.has_candidate and tex._fmt(ev.end) == "2026年07月22日21时30分"

    # 无时间信息 -> None (不注入)
    assert tex.extract("清晰度至少720P，时延不超过80毫秒", rd) is None
    # 画像句无明说时间 -> None (S-000063)
    assert tex.extract("帮我按平时常用的保障时段和时长来", rd) is None

    # 区间终点省略时段词 -> 继承句级 PM (v27 run1 破窗根因: S-000005/073/079)
    rd5 = tex.parse_reference_time("2026年07月22日07时00分")
    ev = tex.extract("帮我安排明天下午2点到4点的保障", rd5)
    assert tex._fmt(ev.end) == "2026年07月23日16时00分", ev.end  # 4点 -> 16:00 非 04:00
    assert not ev.has_contradiction
    # 句级 PM 覆盖后续裸区间 (S-000002 第二段 "7点半到9点半" 也是晚上)
    ev = tex.extract("明天晚上7点到8点半的A保障，7点半到9点半的B保障", rd5)
    clocks = sorted(m.clock for m in ev.mentions)
    assert clocks == ["19:00", "19:30", "20:30", "21:30"], clocks
    # 上午主导句不位移 (S-000146 回归)
    ev = tex.extract("7月15号上午10点到10点半", rd5)
    assert ev.mentions[0].clock == "10:00" and ev.mentions[1].clock == "10:30"

    # 跨夜滚动: "晚上11点到凌晨1点" -> end 次日01:00, 不判矛盾
    ev = tex.extract("从晚上11点到凌晨1点", rd5)
    assert tex._fmt(ev.start) == "2026年07月22日23时00分"
    assert tex._fmt(ev.end) == "2026年07月23日01时00分" and not ev.has_contradiction
    # 裸时刻 PM 继承后仍倒挂 -> 重解读为次日凌晨 ("晚上11点到1点")
    ev = tex.extract("从晚上11点到1点", rd5)
    assert tex._fmt(ev.end) == "2026年07月23日01时00分" and not ev.has_contradiction
    # 真矛盾不误滚 (S-000066: 无 PM 词, 保字面)
    ev = tex.extract("今天9点开始、8点半结束",
                     tex.parse_reference_time("2026年07月14日07时00分"))
    assert ev.has_contradiction and tex._fmt(ev.end) == "2026年07月14日08时30分"

    # 时长候选 (S-000133): 两个时长都收, 不断言 duration/不推导 end, start 照常组装,
    # 且不是时段候选 (has_candidate=False, 不触发"候选不落槽")
    ev = tex.extract("今天8点开始用网易云游戏，时长我还没拿准，不知道选三个半小时还是三个小时",
                     tex.parse_reference_time("2026年07月28日06时00分"))
    assert sorted(d for _, d in ev.durations) == [180, 210], ev.durations
    assert ev.dur_candidate and not ev.has_candidate
    assert ev.duration_min is None
    assert tex._fmt(ev.start) == "2026年07月28日08时00分" and ev.end is None
    print("[OK] time_extractor: 日期表/预解析/矛盾/候选/多意图/否定防呆/区间PM继承/跨夜滚动/时长候选 全过")


if __name__ == "__main__":
    test_sandbox_execute()
    test_sandbox_coreference()
    test_sandbox_security()
    test_case_to_program_roundtrip()
    test_validator_nested()
    test_prompt_and_mmr()
    test_time_extractor()
    test_record_selector()
    print("\n所有离线自检通过 ✅")
