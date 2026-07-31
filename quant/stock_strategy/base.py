from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


@dataclass
class StrategyContext:
    asof_date: pd.Timestamp
    price_panel: pd.DataFrame
    market_features: pd.DataFrame
    factors: pd.DataFrame
    fundamentals: pd.DataFrame
    held_codes: list[str]


class Strategy(Protocol):
    name: str

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        ...

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        ...
