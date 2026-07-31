from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from quant.execution.models import Order, OrderRequest, OrderSide, OrderStatus


@dataclass
class PaperBroker:
    initial_cash: float = 1_000_000.0
    cash: float = 1_000_000.0
    positions: dict[str, int] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cash = float(self.initial_cash)

    def get_account(self) -> dict:
        return {"cash": self.cash, "equity": self.cash}

    def get_positions(self) -> dict[str, int]:
        return dict(self.positions)

    def submit_order(self, req: OrderRequest) -> Order:
        oid = uuid4().hex[:16]
        order = Order(order_id=oid, req=req)
        price = float(req.price or 0.0)
        if req.quantity <= 0 or price <= 0:
            order.status = OrderStatus.REJECTED
        else:
            if req.side == OrderSide.BUY:
                cost = req.quantity * price
                if cost > self.cash:
                    order.status = OrderStatus.REJECTED
                else:
                    self.cash -= cost
                    self.positions[req.code] = self.positions.get(req.code, 0) + req.quantity
                    order.status = OrderStatus.FILLED
                    order.filled_qty = req.quantity
                    order.avg_fill_price = price
            else:
                hold = self.positions.get(req.code, 0)
                qty = min(hold, req.quantity)
                if qty <= 0:
                    order.status = OrderStatus.REJECTED
                else:
                    self.positions[req.code] = hold - qty
                    self.cash += qty * price
                    order.status = OrderStatus.FILLED
                    order.filled_qty = qty
                    order.avg_fill_price = price
        self.orders[oid] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        od = self.orders.get(order_id)
        if od is None:
            return False
        if od.status == OrderStatus.NEW:
            od.status = OrderStatus.CANCELED
            return True
        return False

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)
