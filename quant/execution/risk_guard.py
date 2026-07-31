from __future__ import annotations

from dataclasses import dataclass

from quant.execution.models import OrderRequest, OrderSide


@dataclass
class RiskPolicy:
    max_single_position_pct: float = 0.2
    reserve_cash_pct: float = 0.05
    max_daily_orders: int = 100
    lot_size: int = 100
    max_daily_notional_pct: float = 1.2


class RiskGuard:
    def __init__(self, policy: RiskPolicy) -> None:
        self.policy = policy
        self.daily_orders = 0
        self.daily_notional = 0.0

    def reset_daily(self) -> None:
        self.daily_orders = 0
        self.daily_notional = 0.0

    def allow_order(self, order: OrderRequest, account: dict, positions: dict[str, int]) -> bool:
        if self.daily_orders >= self.policy.max_daily_orders:
            return False
        price = float(order.price or 0.0)
        if price <= 0 or order.quantity <= 0:
            return False
        lot = max(1, int(self.policy.lot_size))
        if order.quantity % lot != 0:
            return False
        cash = float(account.get("cash", 0.0))
        equity = max(float(account.get("equity", cash)), 1.0)
        frozen = float(account.get("frozen_cash", 0.0))
        available_cash = max(0.0, float(account.get("available_cash", cash - frozen)))
        notional = price * order.quantity
        if self.daily_notional + notional > equity * max(0.0, self.policy.max_daily_notional_pct):
            return False
        if order.side == OrderSide.BUY:
            if notional > equity * self.policy.max_single_position_pct:
                return False
            if available_cash - notional < equity * self.policy.reserve_cash_pct:
                return False
        else:
            hold = int(positions.get(order.code, 0))
            if order.quantity > hold:
                return False
        self.daily_orders += 1
        self.daily_notional += notional
        return True
