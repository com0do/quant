"""DeepSeek Chat Completions 客户端（薄壳）。

用 OpenAI 兼容协议直连 DeepSeek 官方 API（https://api.deepseek.com）。
不做 stream、不做 function-calling、不做 agent 循环——那些需求走 hermes-agent 层。

配置（`.env`）：
    DEEPSEEK_API_KEY=<必填>
    DEEPSEEK_MODEL=deepseek-chat        # 默认 deepseek-chat；也可 deepseek-reasoner 等
    DEEPSEEK_BASE_URL=https://api.deepseek.com   # 默认官方；可覆盖成硅基流动/自建
    DEEPSEEK_TIMEOUT_SEC=60
    DEEPSEEK_TEMPERATURE=0.1
    DEEPSEEK_MAX_TOKENS=1024
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class DeepSeekSettings:
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key: str = ""
    timeout_sec: int = 60
    temperature: float = 0.1
    max_tokens: int = 1024
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "DeepSeekSettings":
        return cls(
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            timeout_sec=int(os.getenv("DEEPSEEK_TIMEOUT_SEC", "60") or 60),
            temperature=float(os.getenv("DEEPSEEK_TEMPERATURE", "0.1") or 0.1),
            max_tokens=int(os.getenv("DEEPSEEK_MAX_TOKENS", "1024") or 1024),
        )


class DeepSeekUnavailableError(RuntimeError):
    """DeepSeek 端点不可达 / 认证失败 / 非预期响应。"""


class DeepSeekClient:
    def __init__(self, settings: DeepSeekSettings | None = None) -> None:
        self.settings = settings or DeepSeekSettings.from_env()
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.settings.api_key:
            h["Authorization"] = f"Bearer {self.settings.api_key}"
        h.update(self.settings.extra_headers)
        return h

    def ping(self) -> bool:
        """轻量探活：/models 端点应返回 2xx / 4xx（后者说明服务活着但可能鉴权不通）。"""
        if not self.settings.api_key:
            return False
        try:
            r = self._session.get(
                f"{self.settings.base_url}/models",
                headers=self._headers(),
                timeout=min(5, self.settings.timeout_sec),
            )
            return r.status_code < 500
        except requests.RequestException:
            return False

    def chat(
        self,
        messages: list[dict[str, str]],
        json_mode: bool = True,
        temperature: float | None = None,
    ) -> str:
        """调用 chat/completions，返回 assistant 文本。

        json_mode=True → 请求 `response_format={"type":"json_object"}`；
        DeepSeek v3+ 已原生支持，其余靠 system prompt "只输出 JSON" 约束兜底。
        """
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.settings.base_url}/chat/completions"
        try:
            r = self._session.post(
                url,
                headers=self._headers(),
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=self.settings.timeout_sec,
            )
        except requests.RequestException as e:
            raise DeepSeekUnavailableError(f"connect fail: {e}") from e

        if r.status_code >= 500:
            raise DeepSeekUnavailableError(f"server {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise DeepSeekUnavailableError(f"client {r.status_code}: {r.text[:200]}")

        try:
            data = r.json()
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, ValueError) as e:
            raise DeepSeekUnavailableError(f"bad response shape: {e}: {r.text[:200]}") from e
