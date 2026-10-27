# -*- coding: utf-8 -*-
"""intent_ids.py: v26.0 意图 id 协议 —— 身份与内容解耦的薄映射层。

设计:
  - id 形如 I1/I2/I3, 由 harness 在 session 开始时分配, 永不改变(改口/补槽都指向同一 id)。
  - id -> 内容 key(app/svc) 的映射由 harness 维护; 模型只见 id, 不见 key。
  - 模型段头协议: `# intent: I1` 更新已有意图 / `# intent: new` 新建(harness 分配下一 id) /
    `# cancel: I1` 取消。
  - 本模块只管 id 命名空间与映射; 沙箱执行/validator/JGA 全部复用旧路径。

IntentIdBook 职责:
  1. ensure(key, iid=None): 登记/查询 key 对应的 id (评估侧读 GT 时用)。
  2. alloc(): 分配下一个 id (模型 `# intent: new` 时)。
  3. key_of(iid) / iid_of(key): 双向映射。
  4. describe(): 渲染 prompt 用的 id 化 prev_state 块。
"""
from __future__ import annotations

import json
import re
from typing import Any

from case_format import flatten_params

ID_RE = re.compile(r"^I(\d+)$")
NEW_TOKENS = {"new", "new_intent", ""}


class IntentIdBook:
    """一个 session 一本账: id <-> 内容 key 双向映射 + id 分配器。"""

    def __init__(self) -> None:
        self._by_id: dict[str, str] = {}      # iid -> content key
        self._by_key: dict[str, str] = {}     # content key -> iid
        self._next = 1

    # ---------- 分配与登记 ----------
    def alloc(self) -> str:
        iid = f"I{self._next}"
        self._next += 1
        return iid

    def ensure(self, key: str, iid: str | None = None) -> str:
        """登记内容 key; 已有则返回现有 id, 否则分配(或用调用方指定的 id)。"""
        key = (key or "").strip()
        if not key:
            return ""
        if key in self._by_key:
            return self._by_key[key]
        iid = iid if iid and ID_RE.match(iid) and iid not in self._by_id else self.alloc()
        self._by_id[iid] = key
        self._by_key[key] = iid
        return iid

    # ---------- 查询 ----------
    def key_of(self, iid: str) -> str | None:
        return self._by_id.get((iid or "").strip())

    def iid_of(self, key: str) -> str | None:
        return self._by_key.get((key or "").strip())

    def rename(self, old_key: str, new_key: str) -> None:
        """内容 key 变更(改口改 app/svc)时同步映射, id 不变。

        v26.3 P3: new_key 已被其他 id 占用时不再 pop victim(那会断裂第一个意图的映射),
        而是给本 id 自动实例化(new_key -> new_key#N), 保持全部映射存活。
        """
        old_key, new_key = (old_key or "").strip(), (new_key or "").strip()
        if not old_key or not new_key or old_key == new_key:
            return
        iid = self._by_key.pop(old_key, None)
        if iid is None:
            return
        victim = self._by_key.get(new_key)
        if victim is not None and victim != iid:
            # 自动实例化: 找到该 base 下未占用的 #N
            n = 2
            while f"{new_key}#{n}" in self._by_key:
                n += 1
            new_key = f"{new_key}#{n}"
        self._by_key[new_key] = iid
        self._by_id[iid] = new_key

    def keys_by_base(self, base: str) -> list[str]:
        """v26.3 P3: 该 app/svc base 下已注册的实例 key 列表 (cancel 时段匹配用)。"""
        base = (base or "").split("#", 1)[0]
        return [k for k, iid in self._by_key.items() if k.split("#", 1)[0] == base]

    def known_ids(self) -> list[str]:
        return sorted(self._by_id, key=lambda x: int(x[1:]))

    def __len__(self) -> int:
        return len(self._by_id)

    # ---------- prompt 渲染 ----------
    def describe_prev(self, id_states: dict[str, dict[str, Any]]) -> str:
        """id 化 prev_state 块: 每 id 一行 JSON(扁平标量, 与旧协议同构)。"""
        if not id_states:
            return "{}  # (无已有意图)"
        lines = []
        for iid in sorted(id_states, key=lambda x: int(x[1:]) if ID_RE.match(x) else 999):
            flat = flatten_params(id_states[iid] or {})
            lines.append(f"#   {iid} = {json.dumps(flat, ensure_ascii=False)}")
        return "\n".join(lines)


def parse_id_header(key: str) -> tuple[str | None, bool]:
    """解析模型段头: 返回 (iid, is_new)。
    - "I3"        -> ("I3", False)
    - "new"/""    -> (None, True)
    - 其他(app/svc 等) -> (None, False)  [不遵从协议, 由调用方决定处置]
    """
    k = (key or "").strip()
    if ID_RE.match(k):
        return k, False
    if k in NEW_TOKENS or k == "none":
        return None, True
    return None, False
