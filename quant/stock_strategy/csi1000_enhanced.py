from __future__ import annotations

import pandas as pd

from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.tech_features import add_price_features, zscore


class CSI1000EnhancedStrategy:
    name = "csi1000_enhanced"

    def __init__(
        self,
        value_w: float = 0.7716,
        quality_w: float = 0.8284,
        mom20_w: float = 0.55,
        mom60_w: float = 0.25,
        vol_w: float = 0.30,
    ) -> None:
        self.value_w = value_w
        self.quality_w = quality_w
        self.mom20_w = mom20_w
        self.mom60_w = mom60_w
        self.vol_w = vol_w

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        fac = ctx.factors.copy()
        if fac.empty:
            return pd.Series(dtype=float)
        if ctx.market_features is not None and not ctx.market_features.empty:
            latest_px = ctx.market_features[["code", "ret_20", "ret_60", "vol_20"]].copy()
        else:
            px = add_price_features(ctx.price_panel)
            latest_px = px.sort_values("date").groupby("code").tail(1)[["code", "ret_20", "ret_60", "vol_20"]]
        x = fac.merge(latest_px, on="code", how="left")
        value = -0.6 * zscore(x["pe_ratio"]) - 0.4 * zscore(x["pb_ratio"])
        quality = zscore(x["roe"])
        mom20 = zscore(x["ret_20"])
        mom60 = zscore(x["ret_60"])
        low_vol = -zscore(x["vol_20"])
        score = self.value_w * value + self.quality_w * quality + self.mom20_w * mom20 + self.mom60_w * mom60 + self.vol_w * low_vol
        out = pd.Series(score.values, index=x["code"].astype(str))
        return out.sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        # Convert buy score to sell urgency for held names.
        b = self.buy_scores(ctx)
        if b.empty or not ctx.held_codes:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        # lower buy score -> higher sell urgency
        norm = (held.max() - held) / (held.max() - held.min() + 1e-9)
        return norm.sort_values(ascending=False)
