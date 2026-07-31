from __future__ import annotations

import pandas as pd

from quant.config import AppConfig
from quant.stock_data.data_service import DataService
from quant.stock_exclusion.rules import exclude_paused_and_no_liquidity
from quant.stock_filter.basic_selector import select_value_candidates
from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.registry import build_strategy


def generate_live_scores(data: DataService, cfg: AppConfig, asof: pd.Timestamp, universe: list[str], held_codes: list[str]) -> tuple[pd.Series, pd.Series]:
    panel = data.get_price_panel(universe, asof.strftime("%Y-%m-%d"), asof.strftime("%Y-%m-%d"))
    fundamentals = data.get_latest_fundamentals(universe, asof.strftime("%Y-%m-%d"))
    factors = data.get_latest_factors(universe, asof.strftime("%Y-%m-%d"))
    selected = set(select_value_candidates(fundamentals))
    tradable = set(exclude_paused_and_no_liquidity(panel))
    allow = list((selected & tradable) if selected else tradable)
    filtered_panel = panel[panel["code"].astype(str).isin(allow)] if not panel.empty else panel
    if filtered_panel is None or filtered_panel.empty:
        market_features = pd.DataFrame(columns=["code", "close", "money", "volume", "ret_5", "ret_20", "ret_60", "vol_20", "ma_5", "ma_10", "ma_20", "ma_60", "dd_120"])
    else:
        market_features = filtered_panel.copy()
        for c in ("ret_5", "ret_20", "ret_60", "vol_20", "ma_5", "ma_10", "ma_20", "ma_60", "dd_120"):
            if c not in market_features.columns:
                market_features[c] = pd.NA
        keep_cols = ["code", "close", "money", "volume", "ret_5", "ret_20", "ret_60", "vol_20", "ma_5", "ma_10", "ma_20", "ma_60", "dd_120"]
        for c in keep_cols:
            if c not in market_features.columns:
                market_features[c] = pd.NA
        market_features = market_features[keep_cols]
    ctx = StrategyContext(
        asof_date=asof,
        price_panel=filtered_panel,
        market_features=market_features,
        factors=factors[factors["code"].astype(str).isin(allow)] if not factors.empty else factors,
        fundamentals=fundamentals[fundamentals["code"].astype(str).isin(allow)] if not fundamentals.empty else fundamentals,
        held_codes=held_codes,
    )
    strategies = [build_strategy(x) for x in cfg.strategy.strategy_names]
    w = cfg.strategy.strategy_weights
    buy = None
    sell = None
    for st, ww in zip(strategies, w):
        b = st.buy_scores(ctx).astype(float) * float(ww)
        s = st.sell_scores(ctx).astype(float) * float(ww)
        buy = b if buy is None else buy.add(b, fill_value=0.0)
        sell = s if sell is None else sell.add(s, fill_value=0.0)
    return (
        buy.sort_values(ascending=False) if buy is not None else pd.Series(dtype=float),
        sell.sort_values(ascending=False) if sell is not None else pd.Series(dtype=float),
    )
