from __future__ import annotations

import pandas as pd

from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.tech_features import zscore


class JqMultifactorCloneStrategy:
    """
    Local clone of the JoinQuant multifactor sample:
    rank factors cross-sectionally, weighted aggregation.
    """

    name = "jq_multifactor_clone"

    def __init__(self, factor_names: tuple[str, ...] = ("market_cap", "roe"), weights: tuple[float, ...] = (-1.0, 1.0)) -> None:
        self.factor_names = factor_names
        self.weights = weights

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        x = ctx.factors.copy()
        if x.empty:
            return pd.Series(dtype=float)
        score = 0.0
        for f, w in zip(self.factor_names, self.weights):
            if f not in x.columns:
                continue
            score = score + float(w) * zscore(x[f])
        return pd.Series(score.values, index=x["code"].astype(str)).sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        b = self.buy_scores(ctx)
        if b.empty or not ctx.held_codes:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        return ((held.max() - held) / (held.max() - held.min() + 1e-9)).sort_values(ascending=False)
