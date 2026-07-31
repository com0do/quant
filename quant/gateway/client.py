from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


def normalize_code(code: str) -> str:
    c = str(code or "").strip()
    if "." in c:
        return c
    if c.startswith("6"):
        return f"{c}.SH"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    if c.startswith(("8", "4")):
        return f"{c}.BJ"
    return c


@dataclass
class GatewayHttpClient:
    base_url: str
    token: str = ""
    timeout_sec: int = 8

    def __post_init__(self) -> None:
        self.base_url = str(self.base_url or "").rstrip("/")
        self.timeout_sec = max(1, int(self.timeout_sec))
        if not self.base_url:
            raise ValueError("gateway base_url is required")
        self._session = requests.Session()
        # Do not inherit system proxy for local gateway calls.
        self._session.trust_env = False

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def post_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.post(url, json=payload or {}, headers=self._headers(), timeout=self.timeout_sec)
            resp.raise_for_status()
            if not resp.text:
                return {}
            obj = resp.json()
            return obj if isinstance(obj, dict) else {"data": obj}
        except requests.HTTPError as exc:
            msg = exc.response.text[:200] if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else "?"
            raise RuntimeError(f"gateway POST {path} failed: {code} {msg}") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"gateway POST {path} connect error: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"gateway POST {path} invalid JSON response") from exc

    def get_json(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.get(url, headers=self._headers(), timeout=self.timeout_sec)
            resp.raise_for_status()
            if not resp.text:
                return {}
            obj = resp.json()
            return obj if isinstance(obj, dict) else {"data": obj}
        except requests.HTTPError as exc:
            msg = exc.response.text[:200] if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else "?"
            raise RuntimeError(f"gateway GET {path} failed: {code} {msg}") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"gateway GET {path} connect error: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"gateway GET {path} invalid JSON response") from exc


@dataclass
class GatewayClient:
    base_url: str
    token: str = ""
    timeout_sec: int = 8
    account_id: str = ""

    def __post_init__(self) -> None:
        self.http = GatewayHttpClient(base_url=self.base_url, token=self.token, timeout_sec=self.timeout_sec)

    def _with_account(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        out = dict(payload or {})
        if self.account_id and "account_id" not in out:
            out["account_id"] = self.account_id
        return out

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, with_account: bool = True) -> Any:
        m = str(method or "GET").strip().upper()
        p = payload or {}
        if with_account:
            p = self._with_account(p)
        if m == "GET":
            if p:
                raise ValueError("GET request does not support payload in GatewayClient")
            res = self.http.get_json(path)
        elif m == "POST":
            res = self.http.post_json(path, payload=p)
        else:
            raise ValueError(f"unsupported method: {method}")
        return res.get("data", res) if isinstance(res, dict) else res

    def health_live(self) -> dict[str, Any]:
        data = self.request("GET", "/health/live", with_account=False)
        return data if isinstance(data, dict) else {}

    def health_trader(self) -> dict[str, Any]:
        data = self.request("GET", "/health/trader", with_account=False)
        return data if isinstance(data, dict) else {}

    def health_quote(self) -> dict[str, Any]:
        data = self.request("GET", "/health/quote", with_account=False)
        return data if isinstance(data, dict) else {}

    def metrics_summary(self) -> dict[str, Any]:
        data = self.request("GET", "/metrics/summary", with_account=False)
        return data if isinstance(data, dict) else {}

    def get_quote(self, code: str) -> dict[str, Any]:
        data = self.request("POST", "/quote", {"code": normalize_code(code)})
        return data if isinstance(data, dict) else {}

    def get_account(self) -> dict[str, Any]:
        data = self.request("POST", "/account")
        return data if isinstance(data, dict) else {}

    def get_positions_raw(self) -> Any:
        return self.request("POST", "/positions")

    def submit_order(
        self,
        *,
        code: str,
        side: str,
        quantity: int,
        price: float | None,
        price_type: str = "limit",
        strategy_name: str = "",
        order_remark: str = "",
        client_order_id: str = "",
    ) -> dict[str, Any]:
        payload = self._with_account(
            {
                "code": normalize_code(code),
                "side": str(side).lower(),
                "quantity": int(quantity),
                "price": price,
                "price_type": price_type,
                "strategy_name": strategy_name,
                "order_remark": order_remark,
                "client_order_id": client_order_id,
            }
        )
        data = self.request("POST", "/order", payload, with_account=False)
        return data if isinstance(data, dict) else {}

    def cancel_order(self, order_id: str) -> bool:
        data = self.request("POST", "/cancel", {"order_id": str(order_id)})
        if isinstance(data, dict):
            return bool(data.get("ok", False))
        return bool(data)

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        data = self.request("POST", "/order/status", {"order_id": str(order_id)})
        return data if isinstance(data, dict) else {}

    def get_fills(self, since_ts: str = "") -> list[dict[str, Any]]:
        data = self.request("POST", "/fills", {"since_ts": since_ts} if since_ts else {})
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []
