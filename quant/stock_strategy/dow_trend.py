from __future__ import annotations

import pandas as pd

from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.tech_features import add_price_features


class DowTrendStrategy:
    name = "dow_trend"

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        if ctx.market_features is not None and not ctx.market_features.empty:
            last = ctx.market_features.copy()
        else:
            px = add_price_features(ctx.price_panel)
            if px.empty:
                return pd.Series(dtype=float)
            last = px.sort_values("date").groupby("code").tail(1).copy()
        if last.empty:
            return pd.Series(dtype=float)
        score = (
            (last["ma_5"] > last["ma_20"]).astype(float)
            + (last["ma_20"] > last["ma_60"]).astype(float)
            + (last["ret_20"] > 0).astype(float)
        )
        return pd.Series(score.values, index=last["code"].astype(str)).sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        b = self.buy_scores(ctx)
        if b.empty or not ctx.held_codes:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(0.0)
        return (1.0 - held / (held.max() + 1e-9)).sort_values(ascending=False)
