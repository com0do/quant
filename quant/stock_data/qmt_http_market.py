from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quant.gateway.client import GatewayClient


@dataclass
class QmtHttpMarketData:
    base_url: str
    token: str = ""
    timeout_sec: int = 8
    account_id: str = ""

    def __post_init__(self) -> None:
        self.client = GatewayClient(
            base_url=self.base_url,
            token=self.token,
            timeout_sec=self.timeout_sec,
            account_id=self.account_id,
        )

    def get_quote(self, code: str) -> dict[str, Any]:
        return self.client.get_quote(code)

    def get_last_price(self, code: str) -> float | None:
        q = self.get_quote(code)
        for k in ("lastPrice", "last", "last_price", "price", "close"):
            if k in q and q.get(k) not in (None, ""):
                try:
                    return float(q[k])
                except Exception:
                    continue
        return None
