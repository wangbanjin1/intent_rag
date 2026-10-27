"""半结构化数据格式注册表（2026-10-23 用户定案方向）。

前提：半结构化数据的格式由数据提供方**提前给出**，不做运行时格式判别/路由。
本模块 = 可更新的格式注册方案：接入新格式 = 新增一个 spec JSON，无需改代码。

spec JSON 字段（data/formats/<format_id>.json）：
  format_id     唯一标识
  version       int，同 id 升级递增
  description   数据形态说明
  key_roles     {数据内点分键路径: 角色}，角色 ∈
                goal / constraints / latency_ms / time_start / time_end / duration_min / identity
  identity_map  {identity 对象内键: 标准身份键(application_name/service_name/...)}
  required_roles [投影后必须非空的角色]，缺失即拒绝并报错

用法：
  from format_registry import load, project, validate_view, match
  spec = load("semi_261022")
  view = project(card, spec)          # 结构化字段直读投影
  errs = validate_view(view, spec)    # 必需角色校验
  spec = match(card)                  # 按签名键自动匹配已注册格式（可选便捷）
"""
from __future__ import annotations

import json
import os
from typing import Any

REGISTRY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "formats")
ROLES = {"goal", "constraints", "latency_ms", "time_start", "time_end", "duration_min", "identity"}


def _spec_path(format_id: str, registry_dir: str = REGISTRY_DIR) -> str:
    return os.path.join(registry_dir, f"{format_id}.json")


def register(spec: dict[str, Any], registry_dir: str = REGISTRY_DIR) -> str:
    """注册/更新一个格式 spec：校验字段合法性后写盘，并更新 registry.json 索引。"""
    fid = spec.get("format_id")
    if not fid:
        raise ValueError("spec 缺 format_id")
    bad = set(spec.get("key_roles", {}).values()) - ROLES
    if bad:
        raise ValueError(f"未知角色: {bad}")
    for r in spec.get("required_roles", []):
        if r not in ROLES:
            raise ValueError(f"required_roles 未知角色: {r}")
    os.makedirs(registry_dir, exist_ok=True)
    path = _spec_path(fid, registry_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    idx_path = os.path.join(registry_dir, "registry.json")
    idx = {}
    if os.path.exists(idx_path):
        idx = json.load(open(idx_path, encoding="utf-8"))
    idx[fid] = {"version": spec.get("version", 1), "file": f"{fid}.json",
                "description": spec.get("description", "")}
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
    return path


def load(format_id: str, registry_dir: str = REGISTRY_DIR) -> dict[str, Any]:
    with open(_spec_path(format_id, registry_dir), encoding="utf-8") as f:
        return json.load(f)


def list_formats(registry_dir: str = REGISTRY_DIR) -> dict[str, Any]:
    idx_path = os.path.join(registry_dir, "registry.json")
    return json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else {}


def match(obj: dict[str, Any], registry_dir: str = REGISTRY_DIR) -> dict[str, Any] | None:
    """按 spec 声明的 key_roles 顶层键集合匹配已注册格式（全包含即命中，唯一命中才返回）。"""
    hits = []
    for fid in list_formats(registry_dir):
        spec = load(fid, registry_dir)
        top_keys = {p.split(".")[0] for p in spec.get("key_roles", {})}
        if top_keys and top_keys <= set(obj.keys()):
            hits.append(spec)
    return hits[0] if len(hits) == 1 else None


def _dig(obj: Any, path: str) -> Any:
    for k in path.split("."):
        if not isinstance(obj, dict) or k not in obj:
            return None
        obj = obj[k]
    return obj


def project(obj: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """按 spec 把一条数据投影为规范视图（结构化字段直读，无 LLM）。"""
    view: dict[str, Any] = {"goal": None, "constraints": None, "latency_ms": None,
                            "time_start": None, "time_end": None, "duration_min": None,
                            "identity": {}}
    for path, role in spec.get("key_roles", {}).items():
        v = _dig(obj, path)
        if role == "identity" and isinstance(v, dict):
            imap = spec.get("identity_map", {})
            view["identity"] = {imap.get(k, k): val for k, val in v.items()}
        elif role in view:
            view[role] = v
    return view


def validate_view(view: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """必需角色非空校验，返回错误列表（空=通过）。"""
    errs = []
    for r in spec.get("required_roles", []):
        v = view.get("identity", {}).get(r) if r.startswith("identity.") else view.get(r)
        if v is None or v == "" or v == []:
            errs.append(f"必需角色缺失: {r}")
    return errs
