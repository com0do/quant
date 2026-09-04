"""GBM multi-factor strategy.

- Feature columns are identical to simple_3factor, enabling head-to-head comparison
- Model missing / lightgbm not installed: degrades to a neutral score (all 0) and the main flow keeps running
- Training: `main.py --mode gbm-train`; the artifact defaults to `data/models/gbm_multifactor.txt`
"""

from __future__ import annotations

import pandas as pd

from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.tech_features import add_price_features


class GbmMultifactorStrategy:
    name = "gbm_multifactor"

    def __init__(self, model_path: str = "data/models/gbm_multifactor.txt") -> None:
        self.model_path = model_path
        self._model = None
        self._tried_load = False
        self._load_error: str | None = None

    def _ensure_model(self) -> None:
        if self._tried_load:
            return
        self._tried_load = True
        try:
            from quant.ml.gbm.factor_gbm import load_model  # lazy import
            self._model = load_model(self.model_path)
        except Exception as e:
            self._model = None
            self._load_error = str(e)

    def _build_features(self, ctx: StrategyContext) -> pd.DataFrame:
        fac = ctx.factors.copy() if ctx.factors is not None else pd.DataFrame()
        if fac.empty:
            return pd.DataFrame()

        if ctx.market_features is not None and not ctx.market_features.empty:
            latest_px = ctx.market_features[["code", "ret_20", "ret_60", "vol_20"]].copy()
        else:
            px = add_price_features(ctx.price_panel)
            if px.empty:
                return pd.DataFrame()
            latest_px = px.sort_values("date").groupby("code").tail(1)[["code", "ret_20", "ret_60", "vol_20"]]

        x = fac.merge(latest_px, on="code", how="left")
        required = ["pe_ratio", "pb_ratio", "roe", "ret_20", "ret_60", "vol_20"]
        for c in required:
            if c not in x.columns:
                x[c] = 0.0
        return x[["code", *required]].fillna(0.0)

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        self._ensure_model()
        feats = self._build_features(ctx)
        if feats.empty:
            return pd.Series(dtype=float)
        if self._model is None:
            return pd.Series(0.0, index=feats["code"].astype(str).values)
        from quant.ml.gbm.factor_gbm import predict_scores  # lazy
        return predict_scores(self._model, feats)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        b = self.buy_scores(ctx)
        if b.empty or not ctx.held_codes:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        if held.max() == held.min():
            return pd.Series(0.0, index=held.index)
        norm = (held.max() - held) / (held.max() - held.min() + 1e-9)
        return norm.sort_values(ascending=False)
