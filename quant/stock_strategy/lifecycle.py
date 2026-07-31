"""
Strategy lifecycle management.

Each strategy has a lifecycle state: active → watch → retired.
- active: in production use
- watch: performance degraded, under observation
- retired: no longer used, kept for reference

Retirement conditions are checked periodically based on OOS performance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class StrategyLifecycle:
    name: str
    status: str = "active"  # active | watch | retired
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    retired_at: str | None = None

    # Performance tracking (rolling window)
    rolling_sharpe: float | None = None
    rolling_excess: float | None = None
    rolling_max_dd: float | None = None
    rolling_win_rate: float | None = None
    last_evaluated: str | None = None

    # Retirement conditions
    retire_sharpe_threshold: float = 0.3
    retire_excess_threshold: float = -0.05
    retire_dd_threshold: float = -0.25
    retire_win_rate_threshold: float = 0.35

    # Consecutive failures
    consecutive_below_threshold: int = 0
    max_consecutive_failures: int = 3

    # Notes
    retirement_reason: str | None = None
    notes: str = ""

    def evaluate(self, metrics: dict[str, float], date: str) -> str:
        """
        Evaluate strategy performance and update lifecycle status.

        Parameters
        ----------
        metrics: dict with keys sharpe, excess_ann_return, max_drawdown, win_rate
        date: evaluation date

        Returns
        -------
        New status string
        """
        if self.status == "retired":
            return "retired"

        self.last_evaluated = date
        self.rolling_sharpe = metrics.get("sharpe", 0)
        self.rolling_excess = metrics.get("excess_ann_return", 0)
        self.rolling_max_dd = metrics.get("max_drawdown", 0)
        self.rolling_win_rate = metrics.get("win_rate", 0)

        # Check conditions
        below = (
            (self.rolling_sharpe is not None and self.rolling_sharpe < self.retire_sharpe_threshold)
            or (self.rolling_excess is not None and self.rolling_excess < self.retire_excess_threshold)
            or (self.rolling_max_dd is not None and self.rolling_max_dd < self.retire_dd_threshold)
            or (self.rolling_win_rate is not None and self.rolling_win_rate < self.retire_win_rate_threshold)
        )

        if below:
            self.consecutive_below_threshold += 1
        else:
            self.consecutive_below_threshold = 0

        # Status transitions
        if self.status == "active":
            if self.consecutive_below_threshold >= 2:
                self.status = "watch"
                self.notes = f"Moved to watch on {date}: consecutive below threshold ({self.consecutive_below_threshold})"
        elif self.status == "watch":
            if not below:
                self.status = "active"
                self.consecutive_below_threshold = 0
                self.notes = f"Recovered to active on {date}"
            elif self.consecutive_below_threshold >= self.max_consecutive_failures:
                self.status = "retired"
                self.retired_at = date
                self.retirement_reason = (
                    f"Sharpe={self.rolling_sharpe:.2f}, Excess={self.rolling_excess:.4f}, "
                    f"MaxDD={self.rolling_max_dd:.4f}, WinRate={self.rolling_win_rate:.2%}. "
                    f"Consecutive below: {self.consecutive_below_threshold}"
                )
                self.notes = f"Retired on {date}: {self.retirement_reason}"

        return self.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "retired_at": self.retired_at,
            "rolling_sharpe": self.rolling_sharpe,
            "rolling_excess": self.rolling_excess,
            "rolling_max_dd": self.rolling_max_dd,
            "rolling_win_rate": self.rolling_win_rate,
            "last_evaluated": self.last_evaluated,
            "consecutive_below_threshold": self.consecutive_below_threshold,
            "retirement_reason": self.retirement_reason,
            "notes": self.notes,
        }


@dataclass
class LifecycleManager:
    """Manages lifecycle state for all registered strategies."""
    strategies: dict[str, StrategyLifecycle] = field(default_factory=dict)
    state_path: str = "output/strategy_lifecycle.json"

    def register(self, name: str) -> StrategyLifecycle:
        if name not in self.strategies:
            self.strategies[name] = StrategyLifecycle(name=name)
        return self.strategies[name]

    def evaluate_all(
        self,
        metrics_by_strategy: dict[str, dict[str, float]],
        date: str,
    ) -> dict[str, str]:
        """
        Evaluate all strategies and return status map.

        Parameters
        ----------
        metrics_by_strategy: strategy_name → metrics dict
        date: evaluation date YYYY-MM-DD
        """
        results = {}
        for name, metrics in metrics_by_strategy.items():
            lifecycle = self.register(name)
            status = lifecycle.evaluate(metrics, date)
            results[name] = status
        self.save()
        return results

    def get_active_strategies(self) -> list[str]:
        return [name for name, sl in self.strategies.items() if sl.status == "active"]

    def get_watch_strategies(self) -> list[str]:
        return [name for name, sl in self.strategies.items() if sl.status == "watch"]

    def get_retired_strategies(self) -> list[str]:
        return [name for name, sl in self.strategies.items() if sl.status == "retired"]

    def save(self) -> None:
        p = Path(self.state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "strategies": {name: sl.to_dict() for name, sl in self.strategies.items()},
        }
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self) -> LifecycleManager:
        p = Path(self.state_path)
        if not p.exists():
            return self
        data = json.loads(p.read_text(encoding="utf-8"))
        for name, d in data.get("strategies", {}).items():
            sl = StrategyLifecycle(
                name=d["name"],
                status=d.get("status", "active"),
                created_at=d.get("created_at", ""),
                retired_at=d.get("retired_at"),
                rolling_sharpe=d.get("rolling_sharpe"),
                rolling_excess=d.get("rolling_excess"),
                rolling_max_dd=d.get("rolling_max_dd"),
                rolling_win_rate=d.get("rolling_win_rate"),
                last_evaluated=d.get("last_evaluated"),
                consecutive_below_threshold=d.get("consecutive_below_threshold", 0),
                retirement_reason=d.get("retirement_reason"),
                notes=d.get("notes", ""),
            )
            self.strategies[name] = sl
        return self


def create_default_manager() -> LifecycleManager:
    """Create a lifecycle manager with default strategies registered."""
    mgr = LifecycleManager()
    for name in [
        "csi1000_enhanced",
        "price_only_cross_section",
        "adaptive_regime_midterm",
        "value_activity_dow",
        "dow_trend",
        "jq_multifactor_clone",
        "low_heat_quality_rebound",
    ]:
        mgr.register(name)
    return mgr
