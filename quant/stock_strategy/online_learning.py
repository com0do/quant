"""
Online parameter learning via Exponentiated Gradient Descent (EGD).

Each trading day, after observing returns, update factor weights:
  w_i(t+1) = w_i(t) * exp(η * g_i) / Σ w_j(t) * exp(η * g_j)

where g_i is the gradient of factor i's contribution, estimated as the
cross-sectional rank IC between factor scores and next-day returns.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class FactorWeight:
    """A single factor's weight state."""
    name: str
    weight: float
    min_weight: float = 0.05   # floor to prevent factor from vanishing
    max_weight: float = 3.0    # ceiling to prevent single factor dominance


@dataclass
class OnlineWeightState:
    """Complete online weight state for a strategy."""
    strategy_name: str
    factors: dict[str, FactorWeight]
    learning_rate: float = 0.01
    last_update_date: str | None = None
    update_count: int = 0
    weight_history: list[dict] = field(default_factory=list)  # date → weights

    @property
    def weights_dict(self) -> dict[str, float]:
        return {k: v.weight for k, v in self.factors.items()}

    def normalize(self) -> None:
        """Ensure weights sum to a reasonable total (1.5 to 3.5)."""
        total = sum(v.weight for v in self.factors.values())
        if total < 0.5:
            # Reset to defaults
            defaults = {"value": 0.77, "quality": 0.83, "momentum_20": 0.55, "momentum_60": 0.25, "low_vol": 0.30}
            for name, fw in self.factors.items():
                fw.weight = defaults.get(name, 0.5)
        elif total > 0:
            target = max(1.5, min(3.5, total))
            scale = target / total
            for fw in self.factors.values():
                fw.weight *= scale

    def update(
        self,
        gradients: dict[str, float],
        date: str,
    ) -> dict[str, float]:
        """
        EGD update step.

        Parameters
        ----------
        gradients: factor_name → gradient (e.g., rank IC with next-day returns)
        date: current date string YYYY-MM-DD

        Returns
        -------
        Updated weights dict
        """
        eta = self.learning_rate

        # Compute new weights via EGD
        updated = {}
        for name, fw in self.factors.items():
            g = gradients.get(name, 0.0)
            # Clamp gradient to prevent extreme updates
            g = max(-2.0, min(2.0, g))
            new_w = fw.weight * math.exp(eta * g)
            # Apply floor/ceiling
            new_w = max(fw.min_weight, min(fw.max_weight, new_w))
            updated[name] = new_w

        # Normalize total
        total = sum(updated.values())
        if total > 0:
            scale = max(1.5, min(3.5, total)) / total
            for name in updated:
                updated[name] *= scale

        # Update state
        for name, fw in self.factors.items():
            fw.weight = updated[name]

        self.last_update_date = date
        self.update_count += 1
        self.weight_history.append({
            "date": date,
            "weights": dict(updated),
        })

        return updated


def create_initial_state(
    strategy_name: str = "csi1000_enhanced",
    learning_rate: float = 0.01,
) -> OnlineWeightState:
    """Create initial online weight state with default factor weights."""
    return OnlineWeightState(
        strategy_name=strategy_name,
        factors={
            "value": FactorWeight(name="value", weight=0.77),
            "quality": FactorWeight(name="quality", weight=0.83),
            "momentum_20": FactorWeight(name="momentum_20", weight=0.55),
            "momentum_60": FactorWeight(name="momentum_60", weight=0.25),
            "low_vol": FactorWeight(name="low_vol", weight=0.30),
        },
        learning_rate=learning_rate,
    )


def compute_factor_gradients(
    factor_scores: pd.DataFrame,   # columns: code, value, quality, momentum_20, momentum_60, low_vol
    next_day_returns: pd.Series,    # index: code, values: next-day return
) -> dict[str, float]:
    """
    Compute per-factor gradients as rank IC (Spearman correlation) between
    factor scores and next-day returns.

    Parameters
    ----------
    factor_scores: DataFrame with code column + factor score columns
    next_day_returns: Series indexed by code

    Returns
    -------
    dict of factor_name → rank IC
    """
    if factor_scores.empty or next_day_returns.empty:
        return {}

    # Merge on code
    df = factor_scores.merge(
        next_day_returns.rename("next_ret"),
        left_on="code",
        right_index=True,
        how="inner",
    )
    if df.empty:
        return {}

    factor_cols = [c for c in factor_scores.columns if c != "code"]
    gradients = {}

    for col in factor_cols:
        valid = df[[col, "next_ret"]].dropna()
        if len(valid) < 10:
            gradients[col] = 0.0
            continue

        # Spearman rank correlation (rank IC)
        from scipy.stats import spearmanr
        try:
            ic, _ = spearmanr(valid[col], valid["next_ret"])
            gradients[col] = float(ic) if not np.isnan(ic) else 0.0
        except Exception:
            gradients[col] = 0.0

    return gradients


def save_online_state(state: OnlineWeightState, path: str = "output/online_weights.json") -> None:
    """Persist online weight state to JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "strategy_name": state.strategy_name,
        "learning_rate": state.learning_rate,
        "last_update_date": state.last_update_date,
        "update_count": state.update_count,
        "current_weights": state.weights_dict,
        "history": state.weight_history[-100:],  # keep last 100 updates
    }
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_online_state(path: str = "output/online_weights.json") -> OnlineWeightState | None:
    """Load online weight state from JSON, or None if file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return None

    data = json.loads(p.read_text(encoding="utf-8"))
    state = create_initial_state(
        strategy_name=data.get("strategy_name", "csi1000_enhanced"),
        learning_rate=data.get("learning_rate", 0.01),
    )

    weights = data.get("current_weights", {})
    for name, fw in state.factors.items():
        if name in weights:
            fw.weight = weights[name]

    state.last_update_date = data.get("last_update_date")
    state.update_count = data.get("update_count", 0)
    state.weight_history = data.get("history", [])

    return state


def simulate_online_learning(
    price_panel: pd.DataFrame,
    factors_df: pd.DataFrame,
    initial_state: OnlineWeightState | None = None,
    learning_rate: float = 0.01,
    factor_cols: list[str] | None = None,
) -> OnlineWeightState:
    """
    Simulate online learning over a historical data period.

    For each date (except the last), compute factor scores, observe next-day returns,
    compute rank IC gradients, and update weights via EGD.

    Parameters
    ----------
    price_panel: DataFrame with columns [date, code, close, ...]
    factors_df: DataFrame with columns [date, code, pe_ratio, pb_ratio, roe, ...]
    initial_state: optional starting state; if None, uses defaults
    learning_rate: EGD learning rate
    factor_cols: list of factor names to track

    Returns
    -------
    OnlineWeightState with full update history
    """
    if factor_cols is None:
        factor_cols = ["value", "quality", "momentum_20", "momentum_60", "low_vol"]

    state = initial_state or create_initial_state(learning_rate=learning_rate)

    # Prepare factor scores per date
    dates = sorted(price_panel["date"].unique())
    if len(dates) < 10:
        return state

    # Compute returns
    px = price_panel.pivot(index="date", columns="code", values="close").sort_index()
    returns = px.pct_change().shift(-1)  # next-day returns

    # Compute simple factor scores per date
    # For simplicity, we compute: value = -pe_ratio, quality = roe, mom20 = ret_20, mom60 = ret_60, low_vol = -vol_20
    from quant.stock_strategy.tech_features import add_price_features, zscore

    px_features = add_price_features(price_panel)
    px_features = px_features.sort_values("date")

    for i, date in enumerate(dates[:-1]):  # exclude last date (no next-day return)
        date_str = str(date)[:10]
        next_date = dates[i + 1]

        # Get daily factor data
        fac_day = factors_df[factors_df["date"] == date].copy()
        px_day = px_features[px_features["date"] == date].copy()

        if fac_day.empty and px_day.empty:
            continue

        # Merge factor scores
        merged = fac_day[["code", "pe_ratio", "pb_ratio", "roe"]].merge(
            px_day[["code", "ret_20", "ret_60", "vol_20"]],
            on="code",
            how="inner",
        )
        if merged.empty:
            continue

        # Compute factor scores
        merged["value"] = -0.6 * zscore(merged["pe_ratio"]) - 0.4 * zscore(merged["pb_ratio"])
        merged["quality"] = zscore(merged["roe"])
        merged["momentum_20"] = zscore(merged["ret_20"])
        merged["momentum_60"] = zscore(merged["ret_60"])
        merged["low_vol"] = -zscore(merged["vol_20"])

        # Get next-day returns
        if date_str in returns.index and str(next_date)[:10] in returns.index:
            try:
                next_rets = returns.loc[date_str].dropna()
            except (KeyError, TypeError):
                continue

            # Compute gradients
            gradients = compute_factor_gradients(merged, next_rets)

            # Update weights
            state.update(gradients, date_str)

    return state
