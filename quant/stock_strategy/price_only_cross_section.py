from __future__ import annotations

import pandas as pd

from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.tech_features import add_price_features, zscore


class PriceOnlyCrossSectionStrategy:
    name = "price_only_cross_section"

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        if ctx.market_features is not None and not ctx.market_features.empty:
            latest = ctx.market_features.copy()
        else:
            px = add_price_features(ctx.price_panel)
            if px.empty:
                return pd.Series(dtype=float)
            latest = px.sort_values("date").groupby("code").tail(1)
        if latest.empty:
            return pd.Series(dtype=float)
        score = 0.7 * zscore(latest["ret_20"]) + 0.3 * zscore(latest["ret_60"]) - 0.2 * zscore(latest["vol_20"])
        out = pd.Series(score.values, index=latest["code"].astype(str))
        return out.sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        b = self.buy_scores(ctx)
        if b.empty or not ctx.held_codes:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        norm = (held.max() - held) / (held.max() - held.min() + 1e-9)
        return norm.sort_values(ascending=False)
