from __future__ import annotations

from typing import Protocol

from quant.execution.models import Order, OrderRequest


class BrokerAdapter(Protocol):
    def get_account(self) -> dict:
        ...

    def get_positions(self) -> dict[str, int]:
        ...

    def submit_order(self, req: OrderRequest) -> Order:
        ...

    def cancel_order(self, order_id: str) -> bool:
        ...

    def get_order(self, order_id: str) -> Order | None:
        ...
