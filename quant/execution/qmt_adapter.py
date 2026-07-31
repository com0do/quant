from __future__ import annotations

from quant.execution.models import Order, OrderRequest


class QmtBrokerAdapter:
    """
    QMT adapter skeleton.

    Replace methods with real xtquant mapping in production.
    """

    def __init__(self, account_id: str = "", terminal_path: str = "") -> None:
        self.account_id = account_id
        self.terminal_path = terminal_path

    def get_account(self) -> dict:
        raise NotImplementedError("Wire to QMT account query API")

    def get_positions(self) -> dict[str, int]:
        raise NotImplementedError("Wire to QMT position query API")

    def submit_order(self, req: OrderRequest) -> Order:
        raise NotImplementedError("Wire to QMT order insert API")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("Wire to QMT cancel API")

    def get_order(self, order_id: str) -> Order | None:
        raise NotImplementedError("Wire to QMT order status query API")
