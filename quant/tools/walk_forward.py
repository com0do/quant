"""
Walk-forward backtest framework.

Rolling-window train/validation split to combat overfitting.
Each window: train on N months → validate on M months → slide forward.
Aggregate all validation periods for the out-of-sample performance record.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant.config import AppConfig, load_config
from quant.backtest.engine import BacktestResult, run_backtest


@dataclasses.dataclass
class WalkForwardWindow:
    """A single train/validation window."""
    train_start: str   # YYYY-MM-DD
    train_end: str
    valid_start: str
    valid_end: str
    # Optimization results
    best_params: dict[str, float] | None = None
    # Validation results
    valid_metrics: dict[str, float] | None = None
    valid_excess: float | None = None


@dataclasses.dataclass
class WalkForwardResult:
    windows: list[WalkForwardWindow]
    # Aggregated OOS (out-of-sample) metrics
    oos_total_return: float
    oos_ann_return: float
    oos_ann_volatility: float
    oos_sharpe: float
    oos_max_drawdown: float
    oos_win_rate: float
    # Stability metrics
    param_stability: dict[str, float]  # factor -> std of weights across windows
    excess_persistence: float  # fraction of windows with positive excess
    # All trades from validation periods
    all_trades: pd.DataFrame


def _make_config_for_window(
    base_cfg: AppConfig,
    train_start: str,
    train_end: str,
) -> AppConfig:
    """Create a config scoped to a training window."""
    cfg = dataclasses.replace(base_cfg)
    cfg.data.start_date = train_start
    cfg.data.end_date = train_end
    return cfg


def _optimize_params(
    cfg: AppConfig,
    strategy_name: str = "csi1000_enhanced",
    n_trials: int = 50,
) -> dict[str, float]:
    """
    Lightweight parameter optimization via random search on the training window.

    Optimizes 4 key hyperparameters that most impact performance:
      - top_k: number of positions held
      - entry_interval_days: cooldown between new entries
      - stop_loss_pct: hard stop-loss level
      - trailing_stop_pct: trailing stop level

    Returns the best param dict (plus sharpe and excess for reference).
    """
    import random
    random.seed(42)
    np.random.seed(42)

    best_sharpe = -999.0
    best_params: dict[str, float] = {}

    for _ in range(n_trials):
        params = {
            "top_k": random.randint(2, 12),
            "entry_interval_days": random.randint(1, 6),
            "stop_loss_pct": round(random.uniform(0.02, 0.08), 3),
            "trailing_stop_pct": round(random.uniform(0.04, 0.15), 3),
        }

        # Apply to config
        orig = {
            "top_k": cfg.strategy.top_k,
            "entry_interval_days": cfg.strategy.entry_interval_days,
            "stop_loss_pct": cfg.strategy.stop_loss_pct,
            "trailing_stop_pct": cfg.strategy.trailing_stop_pct,
        }

        try:
            cfg.strategy.top_k = params["top_k"]
            cfg.strategy.max_positions = params["top_k"]
            cfg.strategy.entry_interval_days = params["entry_interval_days"]
            cfg.strategy.stop_loss_pct = params["stop_loss_pct"]
            cfg.strategy.trailing_stop_pct = params["trailing_stop_pct"]

            result = run_backtest(cfg, output_prefix="wf_opt", write_outputs=False)
            sharpe = result.metrics.get("sharpe", -999)

            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_params = dict(params)
                best_params["sharpe"] = sharpe
                best_params["excess"] = result.metrics.get("excess_ann_return", 0)

        finally:
            cfg.strategy.top_k = orig["top_k"]
            cfg.strategy.max_positions = orig["max_positions"]
            cfg.strategy.entry_interval_days = orig["entry_interval_days"]
            cfg.strategy.stop_loss_pct = orig["stop_loss_pct"]
            cfg.strategy.trailing_stop_pct = orig["trailing_stop_pct"]

    return best_params


def run_walk_forward(
    config_path: str = "config/final_freeze_2025.toml",
    train_months: int = 12,
    valid_months: int = 3,
    step_months: int = 3,
    n_trials: int = 50,
    output_dir: str = "output",
) -> WalkForwardResult:
    """
    Run walk-forward backtest.

    Parameters
    ----------
    config_path: path to base config TOML
    train_months: training window size in months
    valid_months: validation window size in months
    step_months: how many months to slide forward each step
    n_trials: random search iterations per window for weight optimization
    output_dir: directory for saving results

    Returns
    -------
    WalkForwardResult with aggregated OOS metrics
    """
    base_cfg = load_config(config_path=config_path)

    # Determine window boundaries from data date range
    start = datetime.strptime(base_cfg.data.start_date, "%Y-%m-%d")
    end = datetime.strptime(base_cfg.data.end_date, "%Y-%m-%d")

    windows: list[WalkForwardWindow] = []
    all_valid_trades: list[pd.DataFrame] = []

    current = start
    train_delta = timedelta(days=train_months * 30)
    valid_delta = timedelta(days=valid_months * 30)
    step_delta = timedelta(days=step_months * 30)

    window_idx = 0
    while current + train_delta + valid_delta <= end:
        train_start = current.strftime("%Y-%m-%d")
        train_end = (current + train_delta).strftime("%Y-%m-%d")
        valid_start = train_end
        valid_end = (current + train_delta + valid_delta).strftime("%Y-%m-%d")

        wf = WalkForwardWindow(
            train_start=train_start,
            train_end=train_end,
            valid_start=valid_start,
            valid_end=valid_end,
        )

        print(f"\n{'='*60}")
        print(f"Window {window_idx}: train=[{train_start}, {train_end}] valid=[{valid_start}, {valid_end}]")

        # Step 1: Optimize hyperparameters on training data
        print(f"  Optimizing params ({n_trials} trials)...")
        train_cfg = _make_config_for_window(base_cfg, train_start, train_end)
        try:
            best = _optimize_params(train_cfg, n_trials=n_trials)
            wf.best_params = best
            print(f"  Best params: {json.dumps({k: round(v, 4) for k, v in best.items()})}")
        except Exception as e:
            print(f"  WARNING: optimization failed: {e}")
            wf.best_params = {
                "top_k": 4, "entry_interval_days": 2,
                "stop_loss_pct": 0.05, "trailing_stop_pct": 0.10,
            }

        # Step 2: Validate with optimized params on validation data
        print(f"  Running validation backtest...")
        valid_cfg = _make_config_for_window(base_cfg, valid_start, valid_end)

        # Apply optimized parameters
        orig_params = {
            "top_k": valid_cfg.strategy.top_k,
            "max_positions": valid_cfg.strategy.max_positions,
            "entry_interval_days": valid_cfg.strategy.entry_interval_days,
            "stop_loss_pct": valid_cfg.strategy.stop_loss_pct,
            "trailing_stop_pct": valid_cfg.strategy.trailing_stop_pct,
        }

        try:
            p = wf.best_params
            valid_cfg.strategy.top_k = int(p["top_k"])
            valid_cfg.strategy.max_positions = int(p["top_k"])
            valid_cfg.strategy.entry_interval_days = int(p["entry_interval_days"])
            valid_cfg.strategy.stop_loss_pct = float(p["stop_loss_pct"])
            valid_cfg.strategy.trailing_stop_pct = float(p["trailing_stop_pct"])

            valid_result = run_backtest(valid_cfg, output_prefix=f"wf_valid_{window_idx}", write_outputs=True)
            wf.valid_metrics = dict(valid_result.metrics)

            if valid_result.trades is not None and not valid_result.trades.empty:
                trades_df = valid_result.trades.copy()
                trades_df["window"] = window_idx
                all_valid_trades.append(trades_df)

            excess = valid_result.metrics.get("excess_ann_return", 0)
            wf.valid_excess = excess
            sharpe = valid_result.metrics.get("sharpe", 0)
            dd = valid_result.metrics.get("max_drawdown", 0)
            print(f"  Validation: excess={excess:.4f}, sharpe={sharpe:.2f}, max_dd={dd:.4f}")

        except Exception as e:
            print(f"  WARNING: validation failed: {e}")
            wf.valid_metrics = {}
            wf.valid_excess = None
        finally:
            valid_cfg.strategy.top_k = orig_params["top_k"]
            valid_cfg.strategy.max_positions = orig_params["max_positions"]
            valid_cfg.strategy.entry_interval_days = orig_params["entry_interval_days"]
            valid_cfg.strategy.stop_loss_pct = orig_params["stop_loss_pct"]
            valid_cfg.strategy.trailing_stop_pct = orig_params["trailing_stop_pct"]

        windows.append(wf)
        current += step_delta
        window_idx += 1

    # Aggregate OOS metrics
    all_trades_df = pd.concat(all_valid_trades, ignore_index=True) if all_valid_trades else pd.DataFrame()

    oos_metrics = _aggregate_oos(windows, all_trades_df)

    # Parameter stability: std of each hyperparameter across windows
    param_stability: dict[str, float] = {}
    param_names = ["top_k", "entry_interval_days", "stop_loss_pct", "trailing_stop_pct"]
    all_params = {k: [] for k in param_names}
    for w in windows:
        if w.best_params:
            for k in param_names:
                all_params[k].append(w.best_params.get(k, float("nan")))
    for k in param_names:
        vals = [v for v in all_params[k] if not np.isnan(v)]
        param_stability[k] = float(np.std(vals)) if len(vals) > 1 else 0.0

    # Excess persistence
    valid_excesses = [w.valid_excess for w in windows if w.valid_excess is not None]
    excess_persistence = sum(1 for e in valid_excesses if e > 0) / len(valid_excesses) if valid_excesses else 0.0

    result = WalkForwardResult(
        windows=windows,
        oos_total_return=oos_metrics.get("total_return", 0),
        oos_ann_return=oos_metrics.get("ann_return", 0),
        oos_ann_volatility=oos_metrics.get("ann_volatility", 0),
        oos_sharpe=oos_metrics.get("sharpe", 0),
        oos_max_drawdown=oos_metrics.get("max_drawdown", 0),
        oos_win_rate=oos_metrics.get("win_rate", 0),
        param_stability=param_stability,
        excess_persistence=excess_persistence,
        all_trades=all_trades_df,
    )

    # Save
    out_path = Path(output_dir) / "walk_forward_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "config": {"train_months": train_months, "valid_months": valid_months, "step_months": step_months, "n_trials": n_trials},
        "oos_metrics": {
            "total_return": result.oos_total_return,
            "ann_return": result.oos_ann_return,
            "ann_volatility": result.oos_ann_volatility,
            "sharpe": result.oos_sharpe,
            "max_drawdown": result.oos_max_drawdown,
            "win_rate": result.oos_win_rate,
        },
        "windows": [
            {
                "train": f"{w.train_start}→{w.train_end}",
                "valid": f"{w.valid_start}→{w.valid_end}",
                "excess": w.valid_excess,
                "sharpe": w.valid_metrics.get("sharpe") if w.valid_metrics else None,
            }
            for w in windows
        ],
        "param_stability": param_stability,
        "excess_persistence": excess_persistence,
        "overfit_warning": (
            "LOW" if excess_persistence > 0.6 and all(v < 0.3 for v in param_stability.values())
            else "MEDIUM" if excess_persistence > 0.4
            else "HIGH"
        ),
    }
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWalk-forward result saved to {out_path}")
    print(f"  OOS Sharpe: {result.oos_sharpe:.2f}")
    print(f"  Excess persistence: {result.excess_persistence:.1%}")
    print(f"  Overfit warning: {summary['overfit_warning']}")
    print(f"  Param stability (std): {json.dumps({k: round(v, 4) for k, v in param_stability.items()})}")

    return result


def _aggregate_oos(
    windows: list[WalkForwardWindow],
    trades: pd.DataFrame,
) -> dict[str, float]:
    """Aggregate OOS metrics from validation windows."""
    if trades.empty:
        return {}

    try:
        import numpy as np
        import vectorbt as vbt
    except ImportError:
        return _aggregate_oos_simple(trades)

    if "date" not in trades.columns or "return" not in trades.columns:
        return _aggregate_oos_simple(trades)

    # Build daily equity curve from trade returns
    daily = trades.groupby("date")["return"].sum().reset_index()
    daily = daily.sort_values("date")
    daily["equity"] = (1 + daily["return"]).cumprod()

    returns = daily["return"].values
    if len(returns) < 5:
        return _aggregate_oos_simple(trades)

    ann_return = float(np.mean(returns) * 252)
    ann_vol = float(np.std(returns) * np.sqrt(252))
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0

    # Max drawdown from equity curve
    equity = daily["equity"].values
    peak = np.maximum.accumulate(equity)
    dd = (equity / peak) - 1
    max_dd = float(np.min(dd))

    total_return = float(equity[-1] / equity[0] - 1) if len(equity) > 0 else 0.0
    win_rate = float(np.mean(returns > 0))

    return {
        "total_return": total_return,
        "ann_return": ann_return,
        "ann_volatility": ann_vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
    }


def _aggregate_oos_simple(trades: pd.DataFrame) -> dict[str, float]:
    """Fallback OOS aggregation."""
    if trades.empty:
        return {}
    if "return" not in trades.columns:
        return {"total_return": 0, "ann_return": 0, "sharpe": 0, "max_drawdown": 0, "win_rate": 0}
    returns = trades["return"].values
    n = len(returns)
    if n < 2:
        return {"total_return": 0, "ann_return": 0, "sharpe": 0, "max_drawdown": 0, "win_rate": 0}
    total_return = float(np.prod(1 + returns) - 1)
    ann_return = float(np.mean(returns) * 252)
    ann_vol = float(np.std(returns) * np.sqrt(252))
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0
    max_dd = float(np.min(np.minimum.accumulate(np.cumprod(1 + returns)) / np.maximum.accumulate(np.cumprod(1 + returns)) - 1))
    win_rate = float(np.mean(returns > 0))
    return {
        "total_return": total_return,
        "ann_return": ann_return,
        "ann_volatility": ann_vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
    }
