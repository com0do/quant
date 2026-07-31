"""
Simplified 3-factor strategy — a drop-in replacement for csi1000_enhanced.

Collapses the original 5 factors into 3 composite factors:
  1. fundamentals (价值+质量): PE + PB + ROE
  2. momentum (动量): ret_20 + ret_60
  3. stability (低波动): vol_20

Keeps the logic simple, transparent, and ≤3 core factors per ADR-3.
"""

from __future__ import annotations

import pandas as pd

from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.tech_features import add_price_features, zscore


class Simple3FactorStrategy:
    """3-factor strategy: fundamentals + momentum + stability."""

    name = "simple_3factor"

    def __init__(
        self,
        fundamental_w: float = 1.0,
        momentum_w: float = 0.6,
        stability_w: float = 0.4,
    ) -> None:
        """
        Parameters
        ----------
        fundamental_w: weight for value+quality composite
        momentum_w: weight for momentum composite
        stability_w: weight for low-volatility signal
        """
        self.fundamental_w = fundamental_w
        self.momentum_w = momentum_w
        self.stability_w = stability_w

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

        # Factor 1: Fundamentals (Value + Quality)
        # Low PE/PB = good value; high ROE = good quality
        value = -0.6 * zscore(x["pe_ratio"]) - 0.4 * zscore(x["pb_ratio"])
        quality = zscore(x["roe"])
        fundamentals = 0.55 * value + 0.45 * quality  # composite

        # Factor 2: Momentum
        # Recent returns signal continuation
        mom20 = zscore(x["ret_20"])
        mom60 = zscore(x["ret_60"])
        momentum = 0.65 * mom20 + 0.35 * mom60  # composite (short-term weighted more)

        # Factor 3: Stability
        # Low volatility = more stable, less crash-prone
        stability = -zscore(x["vol_20"])

        # Weighted combination
        score = (
            self.fundamental_w * fundamentals
            + self.momentum_w * momentum
            + self.stability_w * stability
        )

        out = pd.Series(score.values, index=x["code"].astype(str))
        return out.sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        b = self.buy_scores(ctx)
        if b.empty or not ctx.held_codes:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        norm = (held.max() - held) / (held.max() - held.min() + 1e-9)
        return norm.sort_values(ascending=False)


# Also create a more aggressive 2-factor variant for comparison
class MomentumQualityStrategy:
    """2-factor strategy: momentum + quality only. Simpler baseline."""

    name = "mom_quality"

    def __init__(
        self,
        momentum_w: float = 1.0,
        quality_w: float = 1.0,
    ) -> None:
        self.momentum_w = momentum_w
        self.quality_w = quality_w

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        fac = ctx.factors.copy()
        if fac.empty:
            return pd.Series(dtype=float)

        if ctx.market_features is not None and not ctx.market_features.empty:
            latest_px = ctx.market_features[["code", "ret_20", "ret_60"]].copy()
        else:
            px = add_price_features(ctx.price_panel)
            latest_px = px.sort_values("date").groupby("code").tail(1)[["code", "ret_20", "ret_60"]]

        x = fac.merge(latest_px, on="code", how="left")

        quality = zscore(x["roe"])
        mom20 = zscore(x["ret_20"])
        mom60 = zscore(x["ret_60"])
        momentum = 0.65 * mom20 + 0.35 * mom60

        score = self.quality_w * quality + self.momentum_w * momentum
        out = pd.Series(score.values, index=x["code"].astype(str))
        return out.sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        b = self.buy_scores(ctx)
        if b.empty or not ctx.held_codes:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        norm = (held.max() - held) / (held.max() - held.min() + 1e-9)
        return norm.sort_values(ascending=False)
