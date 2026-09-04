"""DeepSeek Chat Completions client (thin wrapper).

Talks to the official DeepSeek API (https://api.deepseek.com) directly over the OpenAI-compatible protocol.
No streaming, no function-calling, no agent loop; those needs go through the hermes-agent layer.

Configuration (`.env`):
    DEEPSEEK_API_KEY=<required>
    DEEPSEEK_MODEL=deepseek-chat        # default deepseek-chat; deepseek-reasoner etc. also work
    DEEPSEEK_BASE_URL=https://api.deepseek.com   # official by default; can be overridden (SiliconFlow / self-hosted)
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
    """DeepSeek endpoint unreachable / authentication failed / unexpected response."""


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
        """Lightweight liveness probe: the /models endpoint should return 2xx / 4xx (the latter means the service is alive but auth may fail)."""
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
        """Call chat/completions and return the assistant text.

        json_mode=True -> request `response_format={"type":"json_object"}`;
        DeepSeek v3+ supports it natively, otherwise the system prompt "output JSON only" acts as the fallback constraint.
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
