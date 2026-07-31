from __future__ import annotations

import pandas as pd

from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.tech_features import add_price_features, zscore


class ValueActivityDowStrategy:
    name = "value_activity_dow"

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        fac = ctx.fundamentals.copy()
        if fac.empty:
            return pd.Series(dtype=float)
        if ctx.market_features is not None and not ctx.market_features.empty:
            latest = ctx.market_features[["code", "ret_20", "ma_20", "ma_60", "money"]].copy()
        else:
            px = add_price_features(ctx.price_panel)
            latest = px.sort_values("date").groupby("code").tail(1)[["code", "ret_20", "ma_20", "ma_60", "money"]]
        x = fac.merge(latest, on="code", how="left")
        value = -0.6 * zscore(x["pe_ratio"]) - 0.4 * zscore(x["pb_ratio"])
        activity = zscore(x.get("money"))
        trend = zscore(x["ret_20"]) + ((x["ma_20"] > x["ma_60"]).astype(float) * 0.5)
        score = value + 0.25 * activity + 0.35 * trend
        out = pd.Series(score.values, index=x["code"].astype(str))
        return out.sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        b = self.buy_scores(ctx)
        if b.empty or not ctx.held_codes:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        norm = (held.max() - held) / (held.max() - held.min() + 1e-9)
        return norm.sort_values(ascending=False)
