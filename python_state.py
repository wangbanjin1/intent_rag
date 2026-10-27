"""Text-to-Python 状态对象与受限沙箱执行器 — 论文 §3.1。

LLM 输出一段 Python 程序, 对 `state` 对象赋值 / 调用 find_* 桩函数, 以表达 Δy。
coreference 直接用变量引用: `state.rtt`(读 prev_state 的值)。

安全: 沙箱禁 import、禁 builtins(除白名单)、禁 __dunder__ 属性访问、设超时。
只暴露: state(prev_state 构造), 一组 find_* 桩函数(按 schema 生成), str/int/float。
"""
from __future__ import annotations

import re
import signal
import threading
from typing import Any, Callable

from schema import SLOT_SCHEMA, KNOWN_APPLICATIONS


# --------------------------------------------------------------------
# State 对象: 支持属性读写, 序列化为嵌套 params
# --------------------------------------------------------------------
class State:
    """对话状态对象。赋值即记录 Δy, 支持读取已有值(coreference 解析)。"""

    def __init__(self, prev: dict[str, Any] | None = None):
        # _data: 嵌套 params(原始格式) 累积(本轮 Δy)
        object.__setattr__(self, "_data", {})
        # _prev: prev_state 扁平标量 dict, 供 state.xxx 读取(coreference 解析)
        object.__setattr__(self, "_prev", _flatten(prev) if prev else {})

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        # 记录到 _data(嵌套 params 格式)
        self._data[name] = _to_nested_param(name, value)

    def __getattr__(self, name: str) -> Any:
        # 读 prev_state 的值(coreference): state.xxx
        if name.startswith("_"):
            raise AttributeError(name)
        prev = object.__getattribute__(self, "_prev")
        if name in prev:
            return _scalar(prev[name])
        raise AttributeError(f"state.{name} 未定义(无法解析 coreference)")

    def delta(self) -> dict[str, Any]:
        """返回 Δy(嵌套 params)。"""
        return dict(self._data)


def _flatten(nested: dict[str, Any]) -> dict[str, Any]:
    """prev_state(嵌套 params) -> 扁平标量 dict, 供 state.xxx 读取。"""
    from case_format import flatten_params
    return flatten_params(nested)


def _scalar(v: Any) -> Any:
    # v27.3-d930: 模型字面抄注释 "find_timestamp(start=..., end=...)" 时 ... 是 Ellipsis,
    # 会经 _to_nested_param 包成 {'value': Ellipsis} 透传并崩 json 序列化 (genb-S-001419
    # T3-3 t3 实证)。取值层一律视为缺省 None。
    if v is Ellipsis:
        return None
    if isinstance(v, dict):
        if "value" in v:
            return None if v["value"] is Ellipsis else v["value"]
        for k in ("min_value", "max_value"):
            if k in v and isinstance(v[k], dict) and "value" in v[k]:
                return None if v[k]["value"] is Ellipsis else v[k]["value"]
    return v


def _to_nested_param(slot: str, value: Any) -> dict[str, Any]:
    """把 LLM 赋的标量值包装为原始嵌套 params 格式。"""
    if value is Ellipsis:
        value = None  # 省略号参数不入 Δy
    src = {"source_type": "Turn", "source_ref": "U1"}
    if isinstance(value, State):  # find_* 返回的 State 对象
        return value._data  # 子对象
    if slot == "resolution":
        return {"min_value": {"value": value, **src}}
    if slot in ("rtt", "bandwidth"):
        return {"max_value": {"value": value, **src}}
    if slot in ("start_timestamp", "end_timestamp", "duration"):
        # 会归到 timestamp 子对象, 由 caller 合并; 这里单独返回带 source
        return {"value": value, **src}
    return {"value": value, **src}


# --------------------------------------------------------------------
# find_* 桩函数: 按应用生成, 接收 kwargs 返回 State(子对象)
# --------------------------------------------------------------------
def make_find_functions() -> dict[str, Callable]:
    """为每个已知应用生成 find_<app>_<service> 桩函数。
    返回 {name: fn}, fn(**kwargs) -> State(子对象, 存 kwargs 作为槽位)。
    """
    funcs: dict[str, Callable] = {}
    for app in KNOWN_APPLICATIONS:
        # 通用桩: find_<app>(application_name=..., service_name=..., ...)
        fname = f"find_{app}"

        def _make(app_name: str):
            def _fn(**kwargs):
                s = State()
                s.application_name = app_name
                for k, v in kwargs.items():
                    setattr(s, k, v)
                return s
            return _fn
        funcs[fname] = _make(app)
    # 额外: find_timestamp(start=..., end=..., duration=...)
    def _ts(start=None, end=None, duration=None):
        s = State()
        if start is not None:
            s.start_timestamp = start
        if end is not None:
            s.end_timestamp = end
        if duration is not None:
            s.duration = duration
        return s
    funcs["find_timestamp"] = _ts
    return funcs


# --------------------------------------------------------------------
# timestamp 合并: 多次赋值 start/end/duration 应合并进一个 timestamp 子对象
# --------------------------------------------------------------------
def _merge_timestamp(delta: dict[str, Any]) -> dict[str, Any]:
    out = dict(delta)
    ts_keys = {"start_timestamp", "end_timestamp", "duration"}
    ts_parts = {k: out[k] for k in ts_keys if k in out}
    if ts_parts:
        for k in ts_parts:
            out.pop(k)
        out["timestamp"] = ts_parts
    return out


# --------------------------------------------------------------------
# 受限沙箱执行
# --------------------------------------------------------------------
class _TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _TimeoutError("程序执行超时")


def execute_program(
    code: str,
    prev_state: dict[str, Any],
    timeout: float = 2.0,
    line_tolerant: bool = True,
) -> dict[str, Any]:
    """在受限环境里执行 LLM 生成的 Python 程序, 返回 Δy(嵌套 params)。

    安全措施:
      - 只暴露白名单 builtins (无 eval/exec/import/open/... 等)
      - 禁止 __dunder__ 访问(通过 State.__setattr__ 已限制属性名)
      - 信号超时(Unix); Windows 用线程+join 超时(无法强杀, 但限制常见死循环)
      - find_* 桩函数只接收 kwargs 标量
    line_tolerant=True (默认): 单条赋值行 coreference 失败(引用 prev 不存在的槽位,
      如 state.start_timestamp 未登记)时**跳过该行**继续执行, 而非整段异常丢弃 delta。
      动机: 弱模型习惯写全字段沿用 state.x=state.x, 部分填充状态下整段崩 = 连已写对的
      字段一起丢(qwen3.8-27b 实测 resolution 缺失 31 例中 24 例此模式)。
    """
    state = State(prev_state)
    find_funcs = make_find_functions()
    # 白名单 builtins
    safe_builtins = {
        "True": True, "False": False, "None": None,
        "str": str, "int": int, "float": float, "len": len,
        "min": min, "max": max, "abs": abs,
    }
    glob = {"__builtins__": safe_builtins, "state": state, **find_funcs}
    loc: dict[str, Any] = {}

    # 预处理: 去掉 markdown 代码块包裹
    code = _strip_codeblock(code)

    # 超时(Unix 用 signal; Windows 用线程降级)
    import os as _os
    if line_tolerant:
        # 逐行容错执行: 单行失败(尤其 coreference AttributeError)跳过该行, 保留已成功赋值
        for ln in code.splitlines():
            if not ln.strip():
                continue
            try:
                exec(ln, glob, loc)
            except Exception:
                continue
    elif _os.name == "posix":
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            exec(code, glob, loc)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # Windows: 线程执行, 主线程 join 超时即放弃(无法强杀子线程, 但避免阻塞)
        result: dict[str, Any] = {"err": None}
        def _run():
            try:
                exec(code, glob, loc)
            except BaseException as e:
                result["err"] = e
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise _TimeoutError("程序执行超时")
        if result["err"]:
            raise result["err"]

    delta = state.delta()
    delta = _merge_timestamp(delta)
    return delta


def _strip_codeblock(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code


# --------------------------------------------------------------------
# 多意图执行: 按 "# intent: <app>/<service>" 注释切分, 每段一个 State
# --------------------------------------------------------------------
_INTENT_HDR = re.compile(r"^#\s*intent\s*:\s*(.+?)\s*$", re.MULTILINE)
_CANCEL_HDR = re.compile(r"^#\s*cancel\s*:\s*(.+?)\s*$", re.MULTILINE)
_SUMMARY_HDR = re.compile(r"^#\s*summary\s*:\s*(.*?)\s*$", re.MULTILINE)


def parse_summaries(code: str) -> dict[str, str]:
    """Variant A PAR: 提取每个 intent 段前的 # summary 文本。
    返回 {intent_key: summary_text}。无 intent 段时返回 {}。
    summary 在 intent 头之前的最近一行 # summary。
    """
    code = _strip_codeblock(code)
    matches = list(_INTENT_HDR.finditer(code))
    if not matches:
        return {}
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        key = m.group(1).strip()
        seg_start = matches[i - 1].end() if i > 0 else 0
        seg_region = code[seg_start:m.start()]
        sm = list(_SUMMARY_HDR.finditer(seg_region))
        out[key] = sm[-1].group(1).strip() if sm else ""
    return out


def parse_cancels(code: str) -> list[str]:
    """提取所有 # cancel: <app>/<service> 段，返回被取消的 intent_key 列表。"""
    code = _strip_codeblock(code)
    return [m.group(1).strip() for m in _CANCEL_HDR.finditer(code)]


def _split_intent_segments(code: str) -> list[tuple[str, str]]:
    """切分多意图程序。返回 [(intent_key, segment_code), ...]。
    intent_key 形如 "虎牙/开直播" 或 "none"。无 intent 头的整段归为 ("", code)。
    # cancel 段被跳过（取消信号由 parse_cancels 单独处理）。
    # v27.2: # candidate 段同样跳过（候选记账由 candidate_book.parse_candidates 单独处理, 旁路不入 JGA）。
    """
    code = _strip_codeblock(code)
    # 剔除 candidate 段 (避免其程序体被当作意图段执行)
    code = re.sub(r"^#\s*candidate\s*:.*?(?=^#\s*(?:candidate|intent|cancel)\s*:|\Z)",
                  "", code, flags=re.MULTILINE | re.DOTALL)
    matches = list(_INTENT_HDR.finditer(code))
    if not matches:
        return [("", code)]
    segs = []
    for i, m in enumerate(matches):
        key = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        seg = code[start:end].strip()
        segs.append((key, seg))
    return segs


def execute_program_multi(
    code: str,
    prev_states: dict[str, dict[str, Any]] | None = None,
    timeout: float = 2.0,
) -> list[tuple[str, dict[str, Any]]]:
    """多意图执行。prev_states: {intent_key: prev_nested_state}。
    返回 [(intent_key, delta_nested), ...]。intent_key 用 app/service 拼接。
    取消段: 返回 ("__cancel__", <被取消的 intent_key>) —— 由 caller 从 prev_states 移除。
    """
    prev_states = prev_states or {}
    cancels = parse_cancels(code)
    out: list[tuple[str, dict[str, Any]]] = []
    # 取消信号优先输出
    for ck in cancels:
        out.append(("__cancel__", ck))
    segs = _split_intent_segments(code)
    for key, seg in segs:
        if not seg.strip():
            out.append((key, {}))
            continue
        # v12.1 无头段兜底: 省略 # intent 头(key="")且 prev 只有一个意图时归并过去,
        # 须在执行前归并, 否则 coreference(state.xxx) 对着空 prev 解析失败、delta 整段丢弃
        # v27.2 补: 本轮有 cancel 段时, 被取消意图不计入剩余数——"cancel I2 + 无头补参段"
        # 场景 (S-000012 型) 剩余意图唯一, 无头段应归并到未被取消的那个
        remaining = {k: v for k, v in prev_states.items() if k not in cancels}
        if (not key or key == "none") and len(remaining) == 1:
            key = next(iter(remaining))
        # 匹配 prev_state: 优先精确 key, 否则按 app/service 提示匹配
        prev = _match_prev(key, prev_states)
        try:
            delta = execute_program(seg, prev, timeout=timeout)
        except Exception:
            delta = {}
        out.append((key, delta))
    return out


def _match_prev_key(key: str, prev_states: dict[str, dict[str, Any]]) -> str | None:
    """返回 prev_states 中被该 header 命中的 key (精确 > 同app前缀 > 唯一"无app同svc"待补意图)。

    v25.2: 补 app/改口轮的常见形态——prev 里是 '/svc' 无头意图 (用户此前没说 app),
    模型 header 写完整 'app/svc'。此时应归并到该无 app 意图 (沙箱 state 用其 prev 初始化,
    state.xxx 自引用 coreference 才能解析), 而不是当新意图对空 prev 执行 (属性读空 -> 段崩 -> delta 丢失)。
    限定 prev 侧 app 为空且 svc 唯一匹配, 防误并新建意图。
    """
    if not key or key == "none":
        return None
    if key in prev_states:
        return key
    # 按 app 匹配
    app = key.split("/")[0]
    for k in prev_states:
        if k == app or k.startswith(app + "/"):
            return k
    from case_format import flatten_params
    # 单 prev 且该意图无 app: 带 app 的 header 必然是在补全它
    # (S-000850: prev 挂在 'none' 键下且无 app 无 svc, svc 规则也救不到)
    if len(prev_states) == 1:
        k0, v0 = next(iter(prev_states.items()))
        if not flatten_params(v0).get("application_name"):
            return k0
    # 无 app 同 svc 唯一归并
    if "/" in key:
        svc = key.split("/", 1)[1]
        cands = []
        for k, v in prev_states.items():
            f = flatten_params(v)
            if not f.get("application_name") and f.get("service_name") == svc:
                cands.append(k)
        if len(cands) == 1:
            return cands[0]
    return None


def _match_prev(key: str, prev_states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """根据 intent_key (如 "虎牙/开直播") 找 prev_state。"""
    mkey = _match_prev_key(key, prev_states)
    return prev_states[mkey] if mkey else {}


# --------------------------------------------------------------------
# schema 文本(供 prompt 用)
# --------------------------------------------------------------------
def schema_python_text() -> str:
    """生成 Python 类定义形式的 schema(论文 Figure 1 风格)。"""
    lines = ["class State:"]
    for slot, spec in SLOT_SCHEMA.items():
        req = "" if spec.get("required") else "  # 可选"
        t = spec.get("type", "str")
        if "enum" in spec:
            t = "Literal[" + ", ".join(repr(v) for v in spec["enum"]) + "]"
        elif t in ("datetime", "string"):
            t = "str"
        elif t == "datetime":
            t = "str  # 格式 %Y年%m月%d日%H时%M分"
        lines.append(f"    {slot}: {t}{req}")
    lines.append("")
    lines.append("# 桩函数(按应用名): find_<app>(application_name=..., service_name=..., **slots)")
    lines.append("# 时间桩: find_timestamp(start=..., end=..., duration=...)")
    return "\n".join(lines)
