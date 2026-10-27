"""Qwen3-32B 客户端 — Text-to-Python 输出 (论文 §3.3)。

LLM 输出 Python 程序串(对 state 赋值 / 调用 find_*)。

PMI_β 重排已移除: 实验验证无收益 (数据池小 + canonicalize 只解单 intent + 32b 先验噪声)。
当前为贪心单次采样, pmi 相关代码与配置已删除。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


class LLMClient:
    def __init__(self, cfg: dict[str, Any]):
        self.model = cfg.get("model", "qwen3.8-flash")
        self.api_base = cfg.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        self.api_key = (
            cfg.get("api_key")
            or os.environ.get(cfg.get("api_key_env", ""), "")
            or os.environ.get("DASHSCOPE_API_KEY", "")
            or "sk-5201f73df9c54f6894cb26d64d6eddba"
        )
        self.temperature = cfg.get("temperature", 0.0)
        self.max_tokens = cfg.get("max_tokens", 2048)
        self.timeout = cfg.get("timeout", 60)
        self.max_retries: int = cfg.get("max_retries", 4)
        self._client: Any = None
        try:
            import httpx  # type: ignore

            self._client = httpx.Client(
                trust_env=False,
                transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                timeout=httpx.Timeout(connect=10.0, read=float(self.timeout), write=30.0, pool=10.0),
            )
        except Exception:
            self._client = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _chat(self, system: str, prompt: str) -> dict:
        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "enable_thinking": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                if self._client is not None:
                    resp = self._client.post(url, headers=headers, json=payload)
                    if resp.status_code < 400:
                        return resp.json()
                    if resp.status_code in (429, 500, 502, 503, 504):
                        last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                        time.sleep(min(2 ** attempt, 16))
                        continue
                    resp.raise_for_status()
                else:
                    body = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                    with self._opener.open(req, timeout=self.timeout) as r:
                        return json.loads(r.read().decode("utf-8"))
            except Exception as e:
                last_err = e
                time.sleep(min(2 ** attempt, 16))
        raise RuntimeError(f"LLM 请求失败({self.max_retries}次重试): {last_err}")

    def gen_raw_code(self, system_prompt: str, task_prompt: str) -> str:
        """贪心采样一次, 返回原始输出(程序或 XML, 由 caller 解析)。"""
        self.last_system_prompt = system_prompt   # v27.7.2: 供 error_report 展示
        self.last_task_prompt = task_prompt
        data = self._chat(system_prompt, task_prompt)
        return data["choices"][0]["message"]["content"]
