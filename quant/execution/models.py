from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass
class OrderRequest:
    code: str
    side: OrderSide
    quantity: int
    price: float | None = None
    algo: str = "twap"


@dataclass
class Order:
    order_id: str
    req: OrderRequest
    status: OrderStatus = OrderStatus.NEW
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
