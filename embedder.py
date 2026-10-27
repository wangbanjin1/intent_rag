"""Embedding 封装。

支持两种后端:
  - "qwen":  调用 DashScope OpenAI 兼容接口 text-embedding-v3
  - "bge":   本地 sentence-transformers BGE-M3 (需 pip install sentence-transformers)

带磁盘缓存(JSON),避免重复 embedding 案例/历史 query。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.request
from typing import Any


class Embedder:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.backend: str = cfg.get("backend", "qwen")
        self.model: str = cfg.get("model", "text-embedding-v3")
        self.dim: int = cfg.get("dim", 1024)
        self.api_base: str = cfg.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.api_key: str = (
            cfg.get("api_key")
            or os.environ.get(cfg.get("api_key_env", ""), "")
            or os.environ.get("DASHSCOPE_API_KEY", "")
            or "sk-5201f73df9c54f6894cb26d64d6eddba"
        )
        self.cache_path: str = cfg.get("cache_path", "data/embed_cache.json")
        self._cache: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._load_cache()
        self._backend = self._make_backend()

    # ---------------- cache ----------------
    def _load_cache(self) -> None:
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False)
        os.replace(tmp, self.cache_path)

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    # ---------------- backend ----------------
    def _make_backend(self):
        if self.backend == "qwen":
            return _QwenBackend(self.api_base, self.api_key, self.model)
        elif self.backend == "bge":
            return _BGEBackend(self.model or "BAAI/bge-m3", self.dim)
        raise ValueError(f"unknown embedder backend: {self.backend}")

    # ---------------- public ----------------
    def embed(self, text: str) -> list[float]:
        if not text:
            return [0.0] * self.dim
        k = self._key(text)
        with self._lock:
            if k in self._cache:
                return self._cache[k]
        vec = self._backend.embed([text])[0]
        with self._lock:
            self._cache[k] = vec
            if len(self._cache) % 32 == 0:
                self._save_cache()
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        miss_idx, miss_txt = [], []
        out: list[list[float]] = []

        with self._lock:
            for t in texts:
                k = self._key(t)
                if k in self._cache:
                    out.append(self._cache[k])
                else:
                    out.append(None)  # type: ignore[arg-type]
                    miss_idx.append(len(out) - 1)
                    miss_txt.append(t)

        if miss_txt:
            # 百炼 text-embedding-v3 单次最多 10 条
            for start in range(0, len(miss_txt), 10):
                batch_txt = miss_txt[start:start + 10]
                batch_idx = miss_idx[start:start + 10]

                new_vecs = self._backend.embed(batch_txt)

                with self._lock:
                    for i, v, text in zip(batch_idx, new_vecs, batch_txt):
                        out[i] = v
                        self._cache[self._key(text)] = v

                # 每批保存一次，避免中途程序挂掉导致全部重新请求
                self._save_cache()

        return out

    def dim_size(self) -> int:
        return self.dim


class _QwenBackend:
    def __init__(self, api_base: str, api_key: str, model: str):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client: Any = None
        try:
            import httpx  # type: ignore

            self._client = httpx.Client(
                trust_env=False,
                transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
            )
        except Exception:
            self._client = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def embed(self, texts: list[str]) -> list[list[float]]:
        import time as _time
        url = f"{self.api_base}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "input": texts}
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                if self._client is not None:
                    resp = self._client.post(url, headers=headers, json=payload)
                    if resp.status_code in (429, 500, 502, 503, 504):
                        last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                        _time.sleep(min(2 ** attempt, 8))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                else:
                    body = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                    with self._opener.open(req, timeout=60) as r:
                        data = json.loads(r.read().decode("utf-8"))
                items = sorted(data["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in items]
            except Exception as e:
                last_err = e
                _time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"Embedding failed after 4 retries: {last_err}")


class _BGEBackend:
    """本地 BGE-M3 后端。首次使用会下载模型权重。"""

    def __init__(self, model_name: str, dim: int):
        self.model_name = model_name
        self.dim = dim
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError(
                    "使用 BGE 后端需安装: pip install sentence-transformers"
                ) from e
            self._model = SentenceTransformer(self.model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]