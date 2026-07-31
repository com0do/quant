from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from quant.execution.models import OrderRequest


@dataclass
class ExecutionResult:
    submitted: int = 0
    rejected: int = 0


class ExecutionRunner:
    """
    Lightweight execution coordinator.

    This is a compatibility recovery implementation so existing callers
    can continue to invoke `run_orders`.
    """

    def __init__(self, broker, risk_guard=None) -> None:
        self.broker = broker
        self.risk_guard = risk_guard

    def run_orders(self, orders: Iterable[OrderRequest]) -> ExecutionResult:
        result = ExecutionResult()
        for od in orders:
            if self.risk_guard is not None and hasattr(self.risk_guard, "allow_order"):
                if not self.risk_guard.allow_order(
                    order=od,
                    account=self.broker.get_account(),
                    positions=self.broker.get_positions(),
                ):
                    result.rejected += 1
                    continue
            if hasattr(self.broker, "submit_order"):
                self.broker.submit_order(od)
                result.submitted += 1
            else:
                result.rejected += 1
        return result
