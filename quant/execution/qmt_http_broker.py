from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quant.execution.models import Order, OrderRequest, OrderSide, OrderStatus
from quant.gateway.client import GatewayClient, normalize_code


def _to_order_status(value: Any) -> OrderStatus:
    s = str(value or "").strip().lower()
    if s in {"new", "submitted", "pending"}:
        return OrderStatus.NEW
    if s in {"partially_filled", "partial_filled"}:
        return OrderStatus.PARTIALLY_FILLED
    if s in {"filled", "done"}:
        return OrderStatus.FILLED
    if s in {"canceled", "cancelled"}:
        return OrderStatus.CANCELED
    if s in {"rejected", "reject", "error"}:
        return OrderStatus.REJECTED
    return OrderStatus.NEW


@dataclass
class QmtHttpBroker:
    base_url: str
    token: str = ""
    timeout_sec: int = 8
    account_id: str = ""

    def __post_init__(self) -> None:
        self.base_url = str(self.base_url or "").rstrip("/")
        self.timeout_sec = max(1, int(self.timeout_sec))
        self.client = GatewayClient(
            base_url=self.base_url,
            token=self.token,
            timeout_sec=self.timeout_sec,
            account_id=self.account_id,
        )
        if not self.base_url:
            raise ValueError("qmt_http base_url is required")

    def get_account(self) -> dict:
        return self.client.get_account()

    def get_quote(self, code: str) -> dict:
        return self.client.get_quote(code)

    def get_positions(self) -> dict[str, int]:
        rows = self.client.get_positions_raw()
        out: dict[str, int] = {}
        if isinstance(rows, dict):
            for k, v in rows.items():
                out[normalize_code(k)] = int(v or 0)
            return out
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = normalize_code(str(row.get("code") or row.get("symbol") or ""))
                qty = int(row.get("quantity") or row.get("volume") or row.get("avail_qty") or 0)
                if code:
                    out[code] = qty
        return out

    def submit_order(self, req: OrderRequest) -> Order:
        data = self.client.submit_order(
            code=req.code,
            side=str(req.side.value).lower(),
            quantity=int(req.quantity),
            price=None if req.price is None else float(req.price),
        )
        oid = str(data.get("order_id") or data.get("id") or "")
        status = _to_order_status(data.get("status"))
        filled_qty = int(data.get("filled_qty") or data.get("filled") or 0)
        avg_fill_price = float(data.get("avg_fill_price") or data.get("avg_price") or 0.0)
        return Order(
            order_id=oid or "unknown",
            req=req,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
        )

    def cancel_order(self, order_id: str) -> bool:
        return self.client.cancel_order(order_id)

    def get_order(self, order_id: str) -> Order | None:
        data = self.client.get_order_status(order_id)
        if not isinstance(data, dict):
            return None
        req = OrderRequest(
            code=normalize_code(str(data.get("code") or data.get("symbol") or "")),
            side=OrderSide.BUY if str(data.get("side", "")).lower() == "buy" else OrderSide.SELL,
            quantity=int(data.get("quantity") or data.get("qty") or 0),
            price=(None if data.get("price") in (None, "") else float(data.get("price"))),
        )
        return Order(
            order_id=str(data.get("order_id") or data.get("id") or order_id),
            req=req,
            status=_to_order_status(data.get("status")),
            filled_qty=int(data.get("filled_qty") or data.get("filled") or 0),
            avg_fill_price=float(data.get("avg_fill_price") or data.get("avg_price") or 0.0),
        )

    def get_fills(self, since_ts: str = "") -> list[dict[str, Any]]:
        return self.client.get_fills(since_ts=since_ts)
