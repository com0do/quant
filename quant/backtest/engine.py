from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from quant.config import AppConfig
from quant.stock_data.data_service import DataService
from quant.stock_data.sqlite_client import SqliteClient
from quant.stock_exclusion.rules import exclude_paused_and_no_liquidity
from quant.stock_filter.basic_selector import select_value_candidates
from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.registry import build_strategy


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict


@lru_cache(maxsize=8)
def _load_sqlite_market_data(db_path: str, benchmark_index: str, start_date: str, end_date: str) -> dict:
    """
    Cache market data per process to avoid repeatedly opening/closing DB
    across parameter sweeps.
    """
    data = SqliteClient(db_path)
    universe = data.get_index_stocks(benchmark_index, end_date)
    price = data.get_price_panel(universe, start_date, end_date)
    if price.empty:
        raise RuntimeError("empty price panel")
    price = price.copy()
    price["date"] = pd.to_datetime(price["date"])
    dates = tuple(sorted(price["date"].dropna().unique()))
    price_wide = price.pivot(index="date", columns="code", values="close").sort_index()
    volume_wide = price.pivot(index="date", columns="code", values="volume").sort_index()
    open_wide = price.pivot(index="date", columns="code", values="open").sort_index()
    high_wide = price.pivot(index="date", columns="code", values="high").sort_index()
    low_wide = price.pivot(index="date", columns="code", values="low").sort_index()
    codes = tuple(sorted(price["code"].astype(str).unique()))
    ret_5 = price_wide.pct_change(5, fill_method=None)
    ret_20 = price_wide.pct_change(20, fill_method=None)
    ret_60 = price_wide.pct_change(60, fill_method=None)
    vol_20 = price_wide.pct_change(fill_method=None).rolling(20).std(ddof=0)
    ma_5 = price_wide.rolling(5).mean()
    ma_10 = price_wide.rolling(10).mean()
    ma_20 = price_wide.rolling(20).mean()
    ma_60 = price_wide.rolling(60).mean()
    ma_50 = price_wide.rolling(50).mean()
    ma_120 = price_wide.rolling(120, min_periods=80).mean()
    ma_200 = price_wide.rolling(200, min_periods=120).mean()
    ma50_slope20 = ma_50 / ma_50.shift(20) - 1.0
    support_20 = price_wide.rolling(20, min_periods=10).min()
    resistance_20 = price_wide.rolling(20, min_periods=10).max()
    vol_ma20 = volume_wide.rolling(20, min_periods=5).mean()
    vol_ratio20 = volume_wide / vol_ma20.replace(0, np.nan)
    ema12 = price_wide.ewm(span=12, adjust=False).mean()
    ema26 = price_wide.ewm(span=26, adjust=False).mean()
    macd_dif = ema12 - ema26
    macd_dea = macd_dif.ewm(span=9, adjust=False).mean()
    macd_hist = macd_dif - macd_dea
    roll_max_60 = price_wide.rolling(60, min_periods=20).max()
    roll_min_60 = price_wide.rolling(60, min_periods=20).min()
    near_top = (price_wide >= roll_max_60 * 0.98).astype(float)
    near_low = (price_wide <= roll_min_60 * 1.02).astype(float)
    top_touches_40 = near_top.rolling(40, min_periods=10).sum()
    low_touches_40 = near_low.rolling(40, min_periods=10).sum()
    support_break_down = (price_wide < support_20 * 0.995).astype(float)
    resistance_break_up = (price_wide > resistance_20 * 1.005).astype(float)
    top_break_risk = (
        (top_touches_40 >= 2.0)
        & (price_wide < ma_50)
        & (price_wide.pct_change(5, fill_method=None) < 0)
        & (vol_ratio20 > 1.3)
    ).astype(float)
    uptrend_break_down = (
        (ma50_slope20 > 0)
        & (price_wide < ma_50)
        & (support_break_down > 0)
    ).astype(float)
    downtrend_break_up = (
        (ma50_slope20 < 0)
        & (resistance_break_up > 0)
        & (vol_ratio20 > 1.2)
        & (macd_dif > macd_dea)
    ).astype(float)
    inverse_top_break_signal = ((low_touches_40 >= 2.0) & (downtrend_break_up > 0)).astype(float)
    from_recent_high_20 = price_wide / resistance_20.replace(0, np.nan) - 1.0
    up_leg_strength = (
        (ret_20 > 0.10)
        & (ma_20 > ma_60)
        & (price_wide > ma_20)
    )
    up_vol_5 = volume_wide.where(price_wide > open_wide).rolling(5, min_periods=2).mean()
    down_vol_5 = volume_wide.where(price_wide < open_wide).rolling(5, min_periods=2).mean()
    normal_pullback_signal = (
        up_leg_strength
        & (from_recent_high_20 <= -0.03)
        & (from_recent_high_20 >= -0.12)
        & (vol_ratio20 < 1.0)
        & (down_vol_5 < up_vol_5)
    ).astype(float)
    trend_resume_signal = (
        (up_leg_strength | up_leg_strength.shift(1, fill_value=False))
        & (resistance_break_up > 0)
        & (vol_ratio20 >= 0.9)
        & (vol_ratio20 <= 1.8)
        & (macd_dif > macd_dea)
    ).astype(float)
    candle_range = (high_wide - low_wide).replace(0, np.nan)
    upper_shadow_ratio = (high_wide - price_wide) / candle_range
    climax_spike = (price_wide.pct_change(2, fill_method=None) > 0.12) & (vol_ratio20 > 1.8)
    climax_reversal = climax_spike & (upper_shadow_ratio > 0.45) & (price_wide < high_wide * 0.97)
    abnormal_climax_reversal = (
        (climax_reversal | climax_reversal.shift(1, fill_value=False))
        & (price_wide.pct_change(1, fill_method=None) < -0.03)
    ).astype(float)
    fomo_trap_risk = (
        (ret_5 > 0.08)
        & (vol_ratio20 > 1.8)
        & (upper_shadow_ratio > 0.35)
        & (macd_hist < macd_hist.shift(1))
    ).astype(float)
    roll_max_120 = price_wide.rolling(120, min_periods=20).max()
    dd_120 = price_wide / roll_max_120 - 1.0

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    fundamentals = pd.read_sql_query(
        """
        SELECT date, code, pe_ratio, pb_ratio, turnover_ratio
        FROM fundamentals_snapshot
        WHERE date >= ? AND date <= ?
        """,
        conn,
        params=(start_date, end_date),
    )
    factors = pd.read_sql_query(
        """
        SELECT date, code, roe, market_cap, pe_ratio, pb_ratio, turnover_ratio
        FROM factors_snapshot
        WHERE date >= ? AND date <= ?
        """,
        conn,
        params=(start_date, end_date),
    )
    benchmark_close = pd.Series(dtype=float)
    try:
        benchmark_df = pd.read_sql_query(
            """
            SELECT date, close
            FROM index_prices_daily
            WHERE code = ? AND date >= ? AND date <= ?
            ORDER BY date
            """,
            conn,
            params=(benchmark_index, start_date, end_date),
        )
        if not benchmark_df.empty:
            benchmark_df["date"] = pd.to_datetime(benchmark_df["date"])
            benchmark_close = pd.to_numeric(benchmark_df["close"], errors="coerce")
            benchmark_close.index = benchmark_df["date"]
            benchmark_close = benchmark_close.dropna()
    except Exception:
        # Backward compatibility: table may not exist.
        benchmark_close = pd.Series(dtype=float)
    conn.close()
    if not fundamentals.empty:
        fundamentals["date"] = pd.to_datetime(fundamentals["date"])
    if not factors.empty:
        factors["date"] = pd.to_datetime(factors["date"])

    price_by_date = {d: g for d, g in price.groupby("date", sort=True)}

    def _expand_latest(snapshot: pd.DataFrame, fields: list[str]) -> dict:
        if snapshot.empty:
            return {}
        pieces = []
        for f in fields:
            w = snapshot.pivot(index="date", columns="code", values=f).sort_index()
            w = w.reindex(index=pd.DatetimeIndex(dates), columns=list(codes)).ffill()
            pieces.append(w.stack().rename(f))
        long = pd.concat(pieces, axis=1).reset_index().rename(columns={"level_0": "date", "level_1": "code"})
        long = long.dropna(how="all", subset=fields)
        return {d: g for d, g in long.groupby("date", sort=True)}

    fund_by_date = _expand_latest(fundamentals, ["pe_ratio", "pb_ratio", "turnover_ratio"])
    factor_by_date = _expand_latest(factors, ["roe", "market_cap", "pe_ratio", "pb_ratio", "turnover_ratio"])
    return {
        "universe": universe,
        "codes": codes,
        "dates": dates,
        "price": price,
        "price_wide": price_wide,
        "price_by_date": price_by_date,
        "fund_by_date": fund_by_date,
        "factor_by_date": factor_by_date,
        "benchmark_close": benchmark_close,
        "matrices": {
            "close": price_wide,
            "ret_5": ret_5,
            "ret_20": ret_20,
            "ret_60": ret_60,
            "vol_20": vol_20,
            "ma_5": ma_5,
            "ma_10": ma_10,
            "ma_20": ma_20,
            "ma_60": ma_60,
            "ma_50": ma_50,
            "ma_120": ma_120,
            "ma_200": ma_200,
            "ma50_slope20": ma50_slope20,
            "support_20": support_20,
            "resistance_20": resistance_20,
            "vol_ratio20": vol_ratio20,
            "near_top": near_top,
            "upper_shadow_ratio": upper_shadow_ratio,
            "macd_dif": macd_dif,
            "macd_dea": macd_dea,
            "macd_hist": macd_hist,
            "support_break_down": support_break_down,
            "resistance_break_up": resistance_break_up,
            "top_break_risk": top_break_risk,
            "uptrend_break_down": uptrend_break_down,
            "downtrend_break_up": downtrend_break_up,
            "inverse_top_break_signal": inverse_top_break_signal,
            "normal_pullback_signal": normal_pullback_signal,
            "trend_resume_signal": trend_resume_signal,
            "abnormal_climax_reversal": abnormal_climax_reversal,
            "fomo_trap_risk": fomo_trap_risk,
            "dd_120": dd_120,
        },
    }


def _combine_weighted(scores: list[pd.Series], weights: list[float]) -> pd.Series:
    if not scores:
        return pd.Series(dtype=float)
    out = None
    for s, w in zip(scores, weights):
        s = s.astype(float) * float(w)
        out = s if out is None else out.add(s, fill_value=0.0)
    return out.sort_values(ascending=False)


def _build_benchmark_equity(
    px: pd.DataFrame, benchmark_code: str, start_equity: float, benchmark_close: pd.Series | None = None
) -> pd.Series:
    if benchmark_close is not None and not benchmark_close.empty:
        s = pd.to_numeric(benchmark_close, errors="coerce").dropna().sort_index()
        if not s.empty and float(s.iloc[0]) > 0:
            return (s / float(s.iloc[0]) * start_equity).rename("benchmark_equity")
    if benchmark_code in px.columns:
        s = px[benchmark_code].dropna()
        if not s.empty and float(s.iloc[0]) > 0:
            return (s / float(s.iloc[0]) * start_equity).rename("benchmark_equity")
    b_ret = px.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).mean(axis=1).fillna(0.0)
    return ((1.0 + b_ret).cumprod() * start_equity).rename("benchmark_equity")


def _is_weekly_auto_weight_enabled(cfg: AppConfig) -> bool:
    names = [str(x) for x in cfg.strategy.strategy_names]
    return (
        bool(getattr(cfg.strategy, "weekly_auto_weight", False))
        and len(names) == 2
        and "adaptive_regime_midterm" in names
        and "csi1000_enhanced" in names
    )


def _calc_weekly_adaptive_weight(asof: pd.Timestamp, matrices: dict, cfg: AppConfig) -> float:
    close = matrices["close"]
    ma20 = matrices["ma_20"]
    ret20 = matrices["ret_20"]
    if asof not in close.index or asof not in ma20.index or asof not in ret20.index:
        return float(getattr(cfg.strategy, "weekly_adaptive_neutral", 0.5))
    breadth = float((close.loc[asof] > ma20.loc[asof]).mean())
    med20 = float(pd.to_numeric(ret20.loc[asof], errors="coerce").median())
    min_w = float(getattr(cfg.strategy, "weekly_adaptive_min", 0.2))
    max_w = float(getattr(cfg.strategy, "weekly_adaptive_max", 0.8))
    neu_w = float(getattr(cfg.strategy, "weekly_adaptive_neutral", 0.5))
    # 热门趋势越强，适度降低逆向中线仓位；震荡回撤时提升中线仓位
    if breadth >= 0.68 and med20 >= 0.05:
        w = min_w
    elif breadth <= 0.50 or med20 <= 0.0:
        w = max_w
    else:
        # 在 [0.5, 0.68] 区间线性插值
        t = (breadth - 0.50) / (0.68 - 0.50)
        w = max_w + (min_w - max_w) * max(0.0, min(1.0, t))
        w = 0.7 * w + 0.3 * neu_w
    return float(max(min_w, min(max_w, w)))


def _calc_dual_layer_weights(
    asof: pd.Timestamp, matrices: dict, cfg: AppConfig, rel_lookback_ext: float | None = None
) -> tuple[float, float, dict]:
    """
    Return (defense_weight, offense_weight, diagnostics) for
    adaptive_regime_midterm (defense) and csi1000_enhanced (offense).
    """
    close = matrices["close"]
    ma20 = matrices["ma_20"]
    if asof not in close.index or asof not in ma20.index:
        w_def = float(getattr(cfg.strategy, "weekly_adaptive_neutral", 0.5))
        w_off = 1.0 - w_def
        return w_def, w_off, {"breadth": np.nan, "rel_lookback": 0.0}

    # Breadth: cross-sectional trend participation.
    breadth = float((close.loc[asof] > ma20.loc[asof]).mean())

    # Relative strength: by default use equal-weight universe proxy. Caller can
    # pass rel_lookback_ext (e.g. strategy-vs-benchmark trailing relative return).
    lb = max(5, int(getattr(cfg.strategy, "dual_layer_rel_lookback_days", 20)))
    i = close.index.get_loc(asof)
    j = max(0, int(i) - lb)
    c_now = close.loc[asof]
    c_prev = close.iloc[j]
    ew_ret = float((c_now / c_prev - 1.0).replace([np.inf, -np.inf], np.nan).mean())
    bench_code = str(getattr(cfg.data, "benchmark_index", "000852.XSHG"))
    if bench_code in close.columns and pd.notna(close.loc[asof, bench_code]) and pd.notna(close.iloc[j][bench_code]):
        bench_ret = float(close.loc[asof, bench_code] / close.iloc[j][bench_code] - 1.0)
    else:
        bench_ret = float(ew_ret)
    rel_lookback = float(ew_ret - bench_ret)
    if rel_lookback_ext is not None and np.isfinite(rel_lookback_ext):
        rel_lookback = float(rel_lookback_ext)

    # Convert signals into offensive tilt in [0,1].
    breadth_center = float(getattr(cfg.strategy, "dual_layer_breadth_center", 0.56))
    breadth_width = max(0.05, float(getattr(cfg.strategy, "dual_layer_breadth_width", 0.20)))
    breadth_score = (breadth - breadth_center) / breadth_width
    rel_sens = max(0.1, float(getattr(cfg.strategy, "dual_layer_rel_sensitivity", 1.0)))
    rel_score = (rel_lookback / 0.05) * rel_sens
    offense_raw = 0.5 + 0.35 * breadth_score + 0.15 * rel_score
    offense_raw = float(max(0.0, min(1.0, offense_raw)))

    off_floor = float(getattr(cfg.strategy, "dual_layer_offense_floor", 0.35))
    off_ceil = float(getattr(cfg.strategy, "dual_layer_offense_ceiling", 0.75))
    def_floor = float(getattr(cfg.strategy, "dual_layer_defense_floor", 0.25))
    def_ceil = float(getattr(cfg.strategy, "dual_layer_defense_ceiling", 0.65))

    # Clamp by offense bounds first, then derive defense and clamp again.
    w_off = off_floor + offense_raw * (off_ceil - off_floor)
    w_off = float(max(off_floor, min(off_ceil, w_off)))
    w_def = float(1.0 - w_off)
    w_def = float(max(def_floor, min(def_ceil, w_def)))
    w_off = float(1.0 - w_def)

    return w_def, w_off, {"breadth": breadth, "rel_lookback": rel_lookback}


def _calc_market_gate_state(
    asof: pd.Timestamp, matrices: dict, _benchmark_curve_unit: pd.Series, cfg: AppConfig
) -> tuple[bool, float, dict]:
    close = matrices["close"]
    ma20 = matrices["ma_20"]
    ma60 = matrices["ma_60"]
    ret20 = matrices["ret_20"]
    if asof not in close.index or asof not in ma20.index or asof not in ma60.index or asof not in ret20.index:
        return True, 0.5, {"breadth": np.nan, "bench_ret20": np.nan, "bench_above_ma": np.nan}

    breadth = float((close.loc[asof] > ma20.loc[asof]).mean())
    bench_code = str(getattr(cfg.data, "benchmark_index", "000852.XSHG"))
    bench_ret20 = np.nan
    bench_above_ma = np.nan
    if bench_code in close.columns:
        bench_close = float(close.loc[asof, bench_code]) if pd.notna(close.loc[asof, bench_code]) else np.nan
        bench_ma20 = float(ma20.loc[asof, bench_code]) if pd.notna(ma20.loc[asof, bench_code]) else np.nan
        bench_ma60 = float(ma60.loc[asof, bench_code]) if pd.notna(ma60.loc[asof, bench_code]) else np.nan
        bench_ret20 = float(ret20.loc[asof, bench_code]) if pd.notna(ret20.loc[asof, bench_code]) else np.nan
        if np.isfinite(bench_close) and np.isfinite(bench_ma20) and np.isfinite(bench_ma60):
            bench_above_ma = float((bench_close > bench_ma20) and (bench_ma20 > bench_ma60))

    # Win probability proxy: market participation + benchmark trend + benchmark momentum.
    p = 0.35 + 0.40 * max(0.0, min(1.0, breadth))
    if np.isfinite(bench_above_ma):
        p += 0.15 * float(bench_above_ma)
    if np.isfinite(bench_ret20):
        p += 0.10 * max(0.0, min(1.0, bench_ret20 / 0.08))
    p = float(max(0.0, min(1.0, p)))

    # Hard gate: broad market weakness or benchmark downtrend blocks new entries.
    min_breadth = float(getattr(cfg.strategy, "market_regime_min_breadth", 0.50))
    weak_breadth = bool(breadth < min_breadth)
    weak_mom = bool(np.isfinite(bench_ret20) and bench_ret20 < 0)
    weak_trend = bool(np.isfinite(bench_above_ma) and bench_above_ma <= 0.0)
    # Require multi-condition confirmation to avoid over-filtering healthy pullbacks.
    market_down = bool((weak_breadth and weak_mom) or (weak_trend and weak_mom))
    ok = not market_down
    diag = {
        "breadth": breadth,
        "bench_ret20": float(bench_ret20) if np.isfinite(bench_ret20) else np.nan,
        "bench_above_ma": float(bench_above_ma) if np.isfinite(bench_above_ma) else np.nan,
    }
    return ok, p, diag


def _estimate_stock_win_prob(asof: pd.Timestamp, code: str, matrices: dict) -> float:
    def _v(name: str) -> float:
        m = matrices.get(name)
        if m is None or asof not in m.index or code not in m.columns:
            return np.nan
        x = m.loc[asof, code]
        return float(x) if pd.notna(x) else np.nan

    ret20 = _v("ret_20")
    close = _v("close")
    ma20 = _v("ma_20")
    ma60 = _v("ma_60")
    vr20 = _v("vol_ratio20")
    md = _v("macd_dif")
    ms = _v("macd_dea")
    abnormal_rev = _v("abnormal_climax_reversal")

    p = 0.35
    if np.isfinite(ret20):
        p += 0.25 * max(0.0, min(1.0, ret20 / 0.10))
    if np.isfinite(close) and np.isfinite(ma20) and np.isfinite(ma60):
        p += 0.20 * float((close > ma20) and (ma20 > ma60))
    if np.isfinite(vr20):
        p += 0.10 * float(0.8 <= vr20 <= 1.8)
    if np.isfinite(md) and np.isfinite(ms):
        p += 0.20 * float(md > ms)
    if np.isfinite(abnormal_rev) and abnormal_rev > 0:
        p -= 0.25
    return float(max(0.0, min(1.0, p)))


def _calc_monthly_exposure_cap(asof: pd.Timestamp, matrices: dict, cfg: AppConfig, base_cap: float) -> float:
    close = matrices["close"]
    ma20 = matrices["ma_20"]
    ma60 = matrices["ma_60"]
    ret20 = matrices["ret_20"]
    if asof not in close.index or asof not in ma20.index or asof not in ma60.index or asof not in ret20.index:
        return float(base_cap)
    breadth = float((close.loc[asof] > ma20.loc[asof]).mean())
    bench_code = str(getattr(cfg.data, "benchmark_index", "000852.XSHG"))
    bench_ret20 = 0.0
    trend_flag = 0.0
    if bench_code in close.columns:
        c = float(close.loc[asof, bench_code]) if pd.notna(close.loc[asof, bench_code]) else np.nan
        m20 = float(ma20.loc[asof, bench_code]) if pd.notna(ma20.loc[asof, bench_code]) else np.nan
        m60 = float(ma60.loc[asof, bench_code]) if pd.notna(ma60.loc[asof, bench_code]) else np.nan
        r20 = float(ret20.loc[asof, bench_code]) if pd.notna(ret20.loc[asof, bench_code]) else np.nan
        if np.isfinite(r20):
            bench_ret20 = r20
        if np.isfinite(c) and np.isfinite(m20) and np.isfinite(m60):
            trend_flag = 1.0 if (c > m20 and m20 > m60) else -1.0
    score = 0.50 + 0.35 * ((breadth - 0.50) / 0.25) + 0.25 * (bench_ret20 / 0.10) + 0.15 * trend_flag
    score = float(max(0.0, min(1.0, score)))
    e_min = float(getattr(cfg.strategy, "monthly_exposure_min_pct", 0.40))
    e_max = float(getattr(cfg.strategy, "monthly_exposure_max_pct", 1.00))
    cap = e_min + (e_max - e_min) * score
    cap = float(max(e_min, min(e_max, cap)))
    return float(max(0.0, min(float(base_cap), cap)))


def _is_market_crash(asof: pd.Timestamp, matrices: dict, cfg: AppConfig) -> bool:
    close = matrices["close"]
    ma20 = matrices["ma_20"]
    ret5 = matrices["ret_5"]
    ret20 = matrices["ret_20"]
    if asof not in close.index or asof not in ma20.index or asof not in ret5.index or asof not in ret20.index:
        return False
    breadth = float((close.loc[asof] > ma20.loc[asof]).mean())
    bench_code = str(getattr(cfg.data, "benchmark_index", "000852.XSHG"))
    if bench_code not in close.columns:
        return False
    b_ret5 = float(ret5.loc[asof, bench_code]) if pd.notna(ret5.loc[asof, bench_code]) else np.nan
    b_ret20 = float(ret20.loc[asof, bench_code]) if pd.notna(ret20.loc[asof, bench_code]) else np.nan
    if not np.isfinite(b_ret5) or not np.isfinite(b_ret20):
        return False
    thr5 = float(getattr(cfg.strategy, "crash_ret5_threshold", -0.06))
    thr20 = float(getattr(cfg.strategy, "crash_ret20_threshold", -0.12))
    thrb = float(getattr(cfg.strategy, "crash_breadth_threshold", 0.35))
    return bool((b_ret5 <= thr5) and (b_ret20 <= thr20) and (breadth <= thrb))


def _calc_regime_switch_params(
    asof: pd.Timestamp,
    matrices: dict,
    cfg: AppConfig,
    base_top_k: int,
    base_sell_thr: float,
    base_entry_interval: int,
    base_exposure_cap: float,
) -> dict:
    if not bool(getattr(cfg.strategy, "regime_param_switch_enable", False)):
        return {
            "regime_state": "neutral",
            "top_k": int(base_top_k),
            "sell_threshold": float(base_sell_thr),
            "entry_interval_days": int(base_entry_interval),
            "exposure_cap": float(base_exposure_cap),
        }
    close = matrices["close"]
    ma20 = matrices["ma_20"]
    ret20 = matrices["ret_20"]
    if asof not in close.index or asof not in ma20.index or asof not in ret20.index:
        return {
            "regime_state": "neutral",
            "top_k": int(base_top_k),
            "sell_threshold": float(base_sell_thr),
            "entry_interval_days": int(base_entry_interval),
            "exposure_cap": float(base_exposure_cap),
        }
    breadth = float((close.loc[asof] > ma20.loc[asof]).mean())
    bench_code = str(getattr(cfg.data, "benchmark_index", "000852.XSHG"))
    bench_ret20 = float(ret20.loc[asof, bench_code]) if (bench_code in ret20.columns and pd.notna(ret20.loc[asof, bench_code])) else 0.0
    b_on = float(getattr(cfg.strategy, "regime_switch_breadth_risk_on", 0.62))
    b_off = float(getattr(cfg.strategy, "regime_switch_breadth_risk_off", 0.45))
    if breadth >= b_on and bench_ret20 > 0:
        state = "risk_on"
    elif breadth <= b_off or bench_ret20 < 0:
        state = "risk_off"
    else:
        state = "neutral"

    if state == "risk_on":
        top_k = int(base_top_k + int(getattr(cfg.strategy, "regime_switch_top_k_delta_on", 0)))
        sell_thr = float(base_sell_thr + float(getattr(cfg.strategy, "regime_switch_sell_thr_delta_on", 0.0)))
        entry_mult = float(getattr(cfg.strategy, "regime_switch_entry_interval_mult_on", 1.0))
        exp_mult = float(getattr(cfg.strategy, "regime_switch_exposure_mult_on", 1.0))
    elif state == "risk_off":
        top_k = int(base_top_k + int(getattr(cfg.strategy, "regime_switch_top_k_delta_off", 0)))
        sell_thr = float(base_sell_thr + float(getattr(cfg.strategy, "regime_switch_sell_thr_delta_off", 0.0)))
        entry_mult = float(getattr(cfg.strategy, "regime_switch_entry_interval_mult_off", 1.0))
        exp_mult = float(getattr(cfg.strategy, "regime_switch_exposure_mult_off", 1.0))
    else:
        top_k = int(base_top_k)
        sell_thr = float(base_sell_thr)
        entry_mult = 1.0
        exp_mult = 1.0

    top_k = max(1, top_k)
    entry_days = max(1, int(round(float(base_entry_interval) * max(0.25, entry_mult))))
    sell_thr = float(max(0.0, min(10.0, sell_thr)))
    exposure_cap = float(max(0.0, min(float(base_exposure_cap), float(base_exposure_cap) * max(0.1, exp_mult))))
    return {
        "regime_state": state,
        "top_k": top_k,
        "sell_threshold": sell_thr,
        "entry_interval_days": entry_days,
        "exposure_cap": exposure_cap,
    }


def _build_weighted_alloc(
    buy_list: list[str],
    buy_scores: pd.Series,
    deploy_budget: float,
    enable: bool,
    power: float,
    max_ratio: float,
) -> dict[str, float]:
    if not buy_list:
        return {}
    n = len(buy_list)
    if deploy_budget <= 0:
        return {c: 0.0 for c in buy_list}
    if not enable or n == 1:
        a = float(deploy_budget / n)
        return {c: a for c in buy_list}
    s = pd.to_numeric(buy_scores.reindex(buy_list), errors="coerce").fillna(0.0)
    s = (s - float(s.min())) + 1e-6
    p = max(1.0, float(power))
    w = s.pow(p)
    if float(w.sum()) <= 0:
        a = float(deploy_budget / n)
        return {c: a for c in buy_list}
    w = w / float(w.sum())
    cap = max(1.0 / n, min(1.0, float(max_ratio)))
    # Clip single-name overweight and re-normalize.
    w = w.clip(upper=cap)
    if float(w.sum()) <= 0:
        a = float(deploy_budget / n)
        return {c: a for c in buy_list}
    w = w / float(w.sum())
    return {c: float(deploy_budget) * float(w.loc[c]) for c in buy_list}


def _passes_ma_trend_filter(
    *,
    asof: pd.Timestamp,
    code: str,
    c: float,
    matrices: dict,
    require_above_ma120: bool,
    require_above_ma200: bool,
    confirm_days: int,
    buffer_bps: float,
) -> bool:
    if not require_above_ma120 and not require_above_ma200:
        return True
    confirm_days = max(1, int(confirm_days))
    i = matrices["close"].index.get_loc(asof)
    j0 = max(0, i - confirm_days + 1)
    b = max(0.0, float(buffer_bps)) / 10000.0
    close_m = matrices["close"]
    ma120_m = matrices.get("ma_120")
    ma200_m = matrices.get("ma_200")
    for j in range(j0, i + 1):
        dt = close_m.index[j]
        cc = c if dt == asof else (float(close_m.loc[dt, code]) if code in close_m.columns and pd.notna(close_m.loc[dt, code]) else np.nan)
        if not np.isfinite(cc) or cc <= 0:
            return False
        if require_above_ma120:
            if ma120_m is None or dt not in ma120_m.index or code not in ma120_m.columns:
                return False
            m120 = float(ma120_m.loc[dt, code]) if pd.notna(ma120_m.loc[dt, code]) else np.nan
            if not np.isfinite(m120) or cc < m120 * (1.0 + b):
                return False
        if require_above_ma200:
            if ma200_m is None or dt not in ma200_m.index or code not in ma200_m.columns:
                return False
            m200 = float(ma200_m.loc[dt, code]) if pd.notna(ma200_m.loc[dt, code]) else np.nan
            if not np.isfinite(m200) or cc < m200 * (1.0 + b):
                return False
    return True


def _confirm_ma_breakdown(asof: pd.Timestamp, code: str, matrices: dict, confirm_days: int) -> bool:
    close_m = matrices["close"]
    ma20_m = matrices["ma_20"]
    ma60_m = matrices["ma_60"]
    if asof not in close_m.index or code not in close_m.columns:
        return False
    n = max(1, int(confirm_days))
    i = close_m.index.get_loc(asof)
    j0 = max(0, int(i) - n + 1)
    for dt in close_m.index[j0 : i + 1]:
        if dt not in ma20_m.index or dt not in ma60_m.index:
            return False
        if code not in ma20_m.columns or code not in ma60_m.columns:
            return False
        cc = float(close_m.loc[dt, code]) if pd.notna(close_m.loc[dt, code]) else np.nan
        m20 = float(ma20_m.loc[dt, code]) if pd.notna(ma20_m.loc[dt, code]) else np.nan
        m60 = float(ma60_m.loc[dt, code]) if pd.notna(ma60_m.loc[dt, code]) else np.nan
        if not (np.isfinite(cc) and np.isfinite(m20) and np.isfinite(m60)):
            return False
        if not (cc < m20 and m20 < m60):
            return False
    return True


def _effective_trailing_stop_pct(ret: float, cfg: AppConfig) -> float:
    base = max(0.005, float(getattr(cfg.strategy, "trailing_stop_pct", 0.10)))
    if not bool(getattr(cfg.strategy, "dynamic_trailing_stop_enable", False)):
        return base
    profits = [float(x) for x in getattr(cfg.strategy, "dynamic_trailing_profit_levels", [])]
    trails = [float(x) for x in getattr(cfg.strategy, "dynamic_trailing_stop_levels", [])]
    if not profits or len(profits) != len(trails):
        return base
    eff = base
    pairs = sorted(zip(profits, trails), key=lambda x: x[0])
    for p, t in pairs:
        if ret >= p:
            # only tighten, never loosen versus current effective value
            eff = min(eff, max(0.005, t))
    return max(0.005, eff)


def run_backtest(cfg: AppConfig, output_prefix: str = "backtest", write_outputs: bool = True) -> BacktestResult:
    index_aware_mode = bool(getattr(cfg.data, "enable_index_aware_db", False))
    cross_hold_on = bool(getattr(cfg.data, "cross_index_hold_enable", True))
    if cfg.data.source == "sqlite" and not index_aware_mode:
        md = _load_sqlite_market_data(
            cfg.data.sqlite_db_path,
            cfg.data.benchmark_index,
            cfg.data.start_date,
            cfg.data.end_date,
        )
        universe = md["universe"]
        dates = list(md["dates"])
        price = md["price"]
        price_wide = md["price_wide"]
        price_by_date = md["price_by_date"]
        fund_by_date = md["fund_by_date"]
        factor_by_date = md["factor_by_date"]
        benchmark_close = md.get("benchmark_close", pd.Series(dtype=float))
        matrices = md["matrices"]
        data = None
    else:
        data = DataService(cfg)
        universe = data.get_index_stocks(cfg.data.benchmark_index, cfg.data.end_date)
        price_codes = set(universe)
        if index_aware_mode:
            cross_indexes = [str(x) for x in getattr(cfg.data, "cross_index_candidates", [])]
            idx_union = [str(cfg.data.benchmark_index), *cross_indexes]
            price_codes = data.get_index_stocks_union(
                index_codes=idx_union,
                start_date=cfg.data.start_date,
                end_date=cfg.data.end_date,
            )
        price = data.get_price_panel(sorted(price_codes), cfg.data.start_date, cfg.data.end_date)
        if price.empty:
            raise RuntimeError("empty price panel")
        price["date"] = pd.to_datetime(price["date"])
        dates = sorted(price["date"].dropna().unique())
        price_wide = price.pivot(index="date", columns="code", values="close").sort_index()
        price_by_date = {d: g for d, g in price.groupby("date", sort=True)}
        fund_by_date = {}
        factor_by_date = {}
        ret_5 = price_wide.pct_change(5, fill_method=None)
        ret_20 = price_wide.pct_change(20, fill_method=None)
        ret_60 = price_wide.pct_change(60, fill_method=None)
        vol_20 = price_wide.pct_change(fill_method=None).rolling(20).std(ddof=0)
        ma_5 = price_wide.rolling(5).mean()
        ma_10 = price_wide.rolling(10).mean()
        ma_20 = price_wide.rolling(20).mean()
        ma_60 = price_wide.rolling(60).mean()
        ma_50 = price_wide.rolling(50).mean()
        ma_120 = price_wide.rolling(120, min_periods=80).mean()
        ma_200 = price_wide.rolling(200, min_periods=120).mean()
        ma50_slope20 = ma_50 / ma_50.shift(20) - 1.0
        support_20 = price_wide.rolling(20, min_periods=10).min()
        resistance_20 = price_wide.rolling(20, min_periods=10).max()
        volume_wide = price.pivot(index="date", columns="code", values="volume").sort_index()
        open_wide = price.pivot(index="date", columns="code", values="open").sort_index()
        high_wide = price.pivot(index="date", columns="code", values="high").sort_index()
        low_wide = price.pivot(index="date", columns="code", values="low").sort_index()
        vol_ma20 = volume_wide.rolling(20, min_periods=5).mean()
        vol_ratio20 = volume_wide / vol_ma20.replace(0, np.nan)
        ema12 = price_wide.ewm(span=12, adjust=False).mean()
        ema26 = price_wide.ewm(span=26, adjust=False).mean()
        macd_dif = ema12 - ema26
        macd_dea = macd_dif.ewm(span=9, adjust=False).mean()
        macd_hist = macd_dif - macd_dea
        roll_max_60 = price_wide.rolling(60, min_periods=20).max()
        roll_min_60 = price_wide.rolling(60, min_periods=20).min()
        near_top = (price_wide >= roll_max_60 * 0.98).astype(float)
        near_low = (price_wide <= roll_min_60 * 1.02).astype(float)
        top_touches_40 = near_top.rolling(40, min_periods=10).sum()
        low_touches_40 = near_low.rolling(40, min_periods=10).sum()
        support_break_down = (price_wide < support_20 * 0.995).astype(float)
        resistance_break_up = (price_wide > resistance_20 * 1.005).astype(float)
        top_break_risk = (
            (top_touches_40 >= 2.0)
            & (price_wide < ma_50)
            & (price_wide.pct_change(5, fill_method=None) < 0)
            & (vol_ratio20 > 1.3)
        ).astype(float)
        uptrend_break_down = (
            (ma50_slope20 > 0)
            & (price_wide < ma_50)
            & (support_break_down > 0)
        ).astype(float)
        downtrend_break_up = (
            (ma50_slope20 < 0)
            & (resistance_break_up > 0)
            & (vol_ratio20 > 1.2)
            & (macd_dif > macd_dea)
        ).astype(float)
        inverse_top_break_signal = ((low_touches_40 >= 2.0) & (downtrend_break_up > 0)).astype(float)
        from_recent_high_20 = price_wide / resistance_20.replace(0, np.nan) - 1.0
        up_leg_strength = (
            (ret_20 > 0.10)
            & (ma_20 > ma_60)
            & (price_wide > ma_20)
        )
        up_vol_5 = volume_wide.where(price_wide > open_wide).rolling(5, min_periods=2).mean()
        down_vol_5 = volume_wide.where(price_wide < open_wide).rolling(5, min_periods=2).mean()
        normal_pullback_signal = (
            up_leg_strength
            & (from_recent_high_20 <= -0.03)
            & (from_recent_high_20 >= -0.12)
            & (vol_ratio20 < 1.0)
            & (down_vol_5 < up_vol_5)
        ).astype(float)
        trend_resume_signal = (
            (up_leg_strength | up_leg_strength.shift(1, fill_value=False))
            & (resistance_break_up > 0)
            & (vol_ratio20 >= 0.9)
            & (vol_ratio20 <= 1.8)
            & (macd_dif > macd_dea)
        ).astype(float)
        candle_range = (high_wide - low_wide).replace(0, np.nan)
        upper_shadow_ratio = (high_wide - price_wide) / candle_range
        climax_spike = (price_wide.pct_change(2, fill_method=None) > 0.12) & (vol_ratio20 > 1.8)
        climax_reversal = climax_spike & (upper_shadow_ratio > 0.45) & (price_wide < high_wide * 0.97)
        abnormal_climax_reversal = (
            (climax_reversal | climax_reversal.shift(1, fill_value=False))
            & (price_wide.pct_change(1, fill_method=None) < -0.03)
        ).astype(float)
        fomo_trap_risk = (
            (ret_5 > 0.08)
            & (vol_ratio20 > 1.8)
            & (upper_shadow_ratio > 0.35)
            & (macd_hist < macd_hist.shift(1))
        ).astype(float)
        roll_max_120 = price_wide.rolling(120, min_periods=20).max()
        dd_120 = price_wide / roll_max_120 - 1.0
        matrices = {
            "close": price_wide,
            "ret_5": ret_5,
            "ret_20": ret_20,
            "ret_60": ret_60,
            "vol_20": vol_20,
            "ma_5": ma_5,
            "ma_10": ma_10,
            "ma_20": ma_20,
            "ma_60": ma_60,
            "ma_50": ma_50,
            "ma_120": ma_120,
            "ma_200": ma_200,
            "ma50_slope20": ma50_slope20,
            "support_20": support_20,
            "resistance_20": resistance_20,
            "vol_ratio20": vol_ratio20,
            "near_top": near_top,
            "upper_shadow_ratio": upper_shadow_ratio,
            "macd_dif": macd_dif,
            "macd_dea": macd_dea,
            "macd_hist": macd_hist,
            "support_break_down": support_break_down,
            "resistance_break_up": resistance_break_up,
            "top_break_risk": top_break_risk,
            "uptrend_break_down": uptrend_break_down,
            "downtrend_break_up": downtrend_break_up,
            "inverse_top_break_signal": inverse_top_break_signal,
            "normal_pullback_signal": normal_pullback_signal,
            "trend_resume_signal": trend_resume_signal,
            "abnormal_climax_reversal": abnormal_climax_reversal,
            "fomo_trap_risk": fomo_trap_risk,
            "dd_120": dd_120,
        }
        benchmark_close = data.get_index_benchmark_close(
            index_code=cfg.data.benchmark_index,
            start_date=cfg.data.start_date,
            end_date=cfg.data.end_date,
        )

    cash = float(getattr(cfg.execution, "initial_cash", 100000.0))
    positions: dict[str, int] = {}
    entry_price: dict[str, float] = {}
    entry_cost: dict[str, float] = {}
    peak_price: dict[str, float] = {}
    entry_i: dict[str, int] = {}
    trades: list[dict] = []
    equity_rows: list[dict] = []
    weekly_weight_rows: list[dict] = []

    strategies = [build_strategy(x) for x in cfg.strategy.strategy_names]
    weights = [float(x) for x in cfg.strategy.strategy_weights]
    dynamic_weights = list(weights)
    auto_weekly = _is_weekly_auto_weight_enabled(cfg)
    names = [str(x) for x in cfg.strategy.strategy_names]
    adaptive_idx = names.index("adaptive_regime_midterm") if "adaptive_regime_midterm" in names else -1
    base_idx = names.index("csi1000_enhanced") if "csi1000_enhanced" in names else -1
    last_week_key: tuple[int, int] | None = None
    min_hold_days = max(0, int(getattr(cfg.strategy, "min_hold_days", 2)))
    max_single_loss_pct = float(getattr(cfg.strategy, "max_single_position_loss_pct", 0.05))
    max_total_exposure_pct = float(getattr(cfg.strategy, "max_total_exposure_pct", 0.50))
    base_top_k = int(cfg.strategy.top_k)
    base_sell_threshold = float(cfg.strategy.sell_score_threshold)
    base_entry_interval_days = max(1, int(cfg.strategy.entry_interval_days))
    max_trade_risk_pct = float(getattr(cfg.strategy, "max_single_trade_risk_pct", 0.01))
    open_vol_min = float(getattr(cfg.strategy, "open_vol_min", 0.008))
    open_vol_max = float(getattr(cfg.strategy, "open_vol_max", 0.045))
    require_above_ma120 = bool(getattr(cfg.strategy, "require_above_ma120", False))
    require_above_ma200 = bool(getattr(cfg.strategy, "require_above_ma200", True))
    ma_trend_confirm_days = int(getattr(cfg.strategy, "ma_trend_confirm_days", 2))
    ma_trend_filter_buffer_bps = float(getattr(cfg.strategy, "ma_trend_filter_buffer_bps", 20.0))
    anti_fomo_filter_enable = bool(getattr(cfg.strategy, "anti_fomo_filter_enable", True))
    anti_fomo_ret5_threshold = float(getattr(cfg.strategy, "anti_fomo_ret5_threshold", 0.08))
    anti_fomo_vol_ratio_threshold = float(getattr(cfg.strategy, "anti_fomo_vol_ratio_threshold", 1.8))
    anti_fomo_upper_shadow_threshold = float(getattr(cfg.strategy, "anti_fomo_upper_shadow_threshold", 0.35))
    allow_short_below_ma200_exception = bool(getattr(cfg.strategy, "allow_short_below_ma200_exception", True))
    ma200_exc_min_ret5 = float(getattr(cfg.strategy, "ma200_exception_min_ret5", 0.04))
    ma200_exc_vr_min = float(getattr(cfg.strategy, "ma200_exception_vol_ratio_min", 1.2))
    ma200_exc_vr_max = float(getattr(cfg.strategy, "ma200_exception_vol_ratio_max", 2.4))
    trend_extend_hold = bool(getattr(cfg.strategy, "trend_extend_hold", True))
    sell_point_rule_on = bool(getattr(cfg.strategy, "sell_point_rule_enable", True))
    sell_top_break_on = bool(getattr(cfg.strategy, "sell_point_top_break_enable", True))
    sell_box_break_on = bool(getattr(cfg.strategy, "sell_point_box_break_enable", True))
    sell_ma_break_on = bool(getattr(cfg.strategy, "sell_point_ma_break_enable", True))
    sell_ma_break_confirm_days = max(1, int(getattr(cfg.strategy, "sell_point_ma_break_confirm_days", 2)))
    sell_take_profit_on = bool(getattr(cfg.strategy, "sell_point_take_profit_enable", True))
    sell_take_profit_pct = float(getattr(cfg.strategy, "sell_point_take_profit_pct", 0.35))
    pause_after_loss_n = max(0, int(getattr(cfg.strategy, "pause_after_consecutive_loss_days", 2)))
    pause_days = max(0, int(getattr(cfg.strategy, "pause_after_loss_pause_days", 1)))
    losing_streak_days = 0
    pause_until_idx = -1
    monthly_dynamic_exposure_on = bool(getattr(cfg.strategy, "monthly_dynamic_exposure_enable", False))
    crash_cooldown_days = max(0, int(getattr(cfg.strategy, "crash_cooldown_days", 3)))
    crash_until_idx = -1
    current_month_key: tuple[int, int] | None = None
    dynamic_exposure_cap = max_total_exposure_pct
    signal_weighted_alloc_on = bool(getattr(cfg.strategy, "signal_weighted_allocation_enable", False))
    signal_weighted_power = float(getattr(cfg.strategy, "signal_weighted_power", 1.5))
    signal_weighted_max_ratio = float(getattr(cfg.strategy, "signal_weighted_max_ratio", 0.55))
    buy_fee = float(getattr(cfg.execution, "buy_fee", 0.0003))
    sell_fee = float(getattr(cfg.execution, "sell_fee", 0.0013))
    slippage = float(getattr(cfg.execution, "slippage_bps", 0.0)) / 10000.0

    has_real_benchmark_daily = benchmark_close is not None and not benchmark_close.empty
    bench_curve_unit = _build_benchmark_equity(
        price_wide, cfg.data.benchmark_index, 1.0, benchmark_close=benchmark_close
    )
    market_gate_enable = bool(getattr(cfg.strategy, "market_regime_gate_enable", True))
    market_min_win_prob = float(getattr(cfg.strategy, "market_regime_min_win_prob", 0.60))
    for i, d in enumerate(dates):
        asof = pd.Timestamp(d)
        day_realized_pnl = 0.0
        week_key = (int(asof.isocalendar().year), int(asof.isocalendar().week))
        if auto_weekly and week_key != last_week_key and adaptive_idx >= 0 and base_idx >= 0:
            rel_lookback_ext = None
            lb = max(5, int(getattr(cfg.strategy, "dual_layer_rel_lookback_days", 20)))
            if i > lb and len(equity_rows) > lb:
                eq_now = float(equity_rows[-1]["equity"])
                eq_prev = float(equity_rows[-1 - lb]["equity"])
                if eq_prev > 0:
                    p_ret = eq_now / eq_prev - 1.0
                    d_now = pd.Timestamp(dates[i - 1])
                    d_prev = pd.Timestamp(dates[i - 1 - lb])
                    if d_now in bench_curve_unit.index and d_prev in bench_curve_unit.index:
                        b_now = float(bench_curve_unit.loc[d_now])
                        b_prev = float(bench_curve_unit.loc[d_prev])
                        if b_prev > 0:
                            b_ret = b_now / b_prev - 1.0
                            rel_lookback_ext = float(p_ret - b_ret)
            dual_layer_dynamic_on = bool(getattr(cfg.strategy, "dual_layer_enable", True)) and has_real_benchmark_daily
            if dual_layer_dynamic_on:
                w_ad, w_base, diag = _calc_dual_layer_weights(
                    asof, matrices, cfg, rel_lookback_ext=rel_lookback_ext
                )
            else:
                w_ad = _calc_weekly_adaptive_weight(asof, matrices, cfg)
                w_base = 1.0 - w_ad
                diag = {"breadth": np.nan, "rel_lookback": 0.0}
            dynamic_weights[adaptive_idx] = w_ad
            dynamic_weights[base_idx] = w_base
            weekly_weight_rows.append(
                {
                    "date": asof,
                    "w_ad": w_ad,
                    "w_base": w_base,
                    "breadth": float(diag.get("breadth", np.nan)),
                    "rel_lookback": float(diag.get("rel_lookback", 0.0)),
                }
            )
            last_week_key = week_key
        month_key = (asof.year, asof.month)
        if monthly_dynamic_exposure_on and month_key != current_month_key:
            dynamic_exposure_cap = _calc_monthly_exposure_cap(asof, matrices, cfg, max_total_exposure_pct)
            current_month_key = month_key
        regime_params = _calc_regime_switch_params(
            asof=asof,
            matrices=matrices,
            cfg=cfg,
            base_top_k=base_top_k,
            base_sell_thr=base_sell_threshold,
            base_entry_interval=base_entry_interval_days,
            base_exposure_cap=dynamic_exposure_cap,
        )
        eff_top_k = int(regime_params["top_k"])
        eff_sell_threshold = float(regime_params["sell_threshold"])
        eff_entry_interval_days = int(regime_params["entry_interval_days"])
        eff_exposure_cap = float(regime_params["exposure_cap"])
        row = price_by_date.get(asof, pd.DataFrame(columns=price.columns))
        codes = row["code"].astype(str).tolist()
        asof_s = asof.strftime("%Y-%m-%d")
        selection_set = set(codes)
        if data is not None and index_aware_mode:
            selection_set = set(data.get_index_stocks(cfg.data.benchmark_index, asof_s))
        if data is None:
            fundamentals = fund_by_date.get(asof, pd.DataFrame(columns=["date", "code", "pe_ratio", "pb_ratio", "turnover_ratio"]))
            factors = factor_by_date.get(asof, pd.DataFrame(columns=["date", "code", "roe", "market_cap", "pe_ratio", "pb_ratio", "turnover_ratio"]))
        else:
            fundamentals = data.get_latest_fundamentals(codes, asof.strftime("%Y-%m-%d"))
            factors = data.get_latest_factors(codes, asof.strftime("%Y-%m-%d"))

        selected = set(select_value_candidates(fundamentals))
        tradable = set(exclude_paused_and_no_liquidity(row))
        if bool(getattr(cfg.strategy, "value_filter_strict", True)):
            base_allow = list((selected & tradable) if selected else tradable)
        else:
            # Offensive sleeve needs broader opportunity set; keep liquidity/paused filter.
            base_allow = list(tradable)
        # Selection pool is restricted to benchmark constituents of current day.
        allow = [c for c in base_allow if c in selection_set]
        # Keep held names in feature context even after moving out of selection pool.
        allow_or_held = set(allow) | set(positions.keys())
        by_code = row.set_index("code") if ("code" in row.columns and not row.empty) else pd.DataFrame()
        mf = pd.DataFrame({"code": pd.Index(codes, dtype="object").astype(str)})
        for fn, mat in matrices.items():
            if asof in mat.index:
                mf[fn] = mat.loc[asof].reindex(codes).values
            else:
                mf[fn] = np.nan
        if not by_code.empty:
            for c in ("money", "volume", "paused"):
                if c in by_code.columns:
                    mf[c] = by_code.reindex(codes)[c].values
        mf = mf[mf["code"].astype(str).isin(allow_or_held)] if allow_or_held else mf

        ctx = StrategyContext(
            asof_date=asof,
            price_panel=row,
            market_features=mf,
            factors=factors[factors["code"].astype(str).isin(allow_or_held)] if not factors.empty else factors,
            fundamentals=fundamentals[fundamentals["code"].astype(str).isin(allow_or_held)] if not fundamentals.empty else fundamentals,
            held_codes=list(positions.keys()),
        )
        buy_scores = _combine_weighted([s.buy_scores(ctx) for s in strategies], dynamic_weights)
        sell_scores = _combine_weighted([s.sell_scores(ctx) for s in strategies], dynamic_weights)

        if _is_market_crash(asof, matrices, cfg):
            crash_until_idx = max(crash_until_idx, i + crash_cooldown_days)
            # Crash regime: flatten positions immediately (exposure -> 0%).
            for code in list(positions.keys()):
                c = float(price_wide.loc[asof, code]) if code in price_wide.columns else None
                if c is None or pd.isna(c) or c <= 0:
                    continue
                shares = positions.pop(code)
                epx = entry_price.pop(code, None)
                ecost = entry_cost.pop(code, None)
                peak_price.pop(code, None)
                entry_i.pop(code, None)
                exec_sell = c * (1.0 - slippage)
                proceeds = shares * exec_sell * (1.0 - sell_fee)
                cash += proceeds
                if epx is not None:
                    base_cost = float(ecost) if ecost is not None else float(epx) * (1.0 + buy_fee + slippage)
                    pnl = shares * (float(exec_sell) * (1.0 - sell_fee) - base_cost)
                    day_realized_pnl += float(pnl)
                trades.append({"date": asof, "code": code, "side": "SELL", "price": c, "shares": shares})

        # risk exits daily
        for code in list(positions.keys()):
            c = float(price_wide.loc[asof, code]) if code in price_wide.columns else None
            if c is None or pd.isna(c) or c <= 0:
                continue
            peak_price[code] = max(peak_price.get(code, c), c)
            ret = c / max(entry_price.get(code, c), 1e-9) - 1.0
            dd = c / max(peak_price.get(code, c), 1e-9) - 1.0
            sell_urgency = float(sell_scores.get(code, 0.0))
            held_days = i - int(entry_i.get(code, i))
            signal_sell = sell_urgency >= eff_sell_threshold and held_days >= min_hold_days
            structural_sell = False
            if sell_point_rule_on:
                top_break = 0.0
                box_break = 0.0
                near_top = 0.0
                if asof in matrices["top_break_risk"].index and code in matrices["top_break_risk"].columns:
                    top_break = float(matrices["top_break_risk"].loc[asof, code])
                if asof in matrices["uptrend_break_down"].index and code in matrices["uptrend_break_down"].columns:
                    box_break = float(matrices["uptrend_break_down"].loc[asof, code])
                if asof in matrices["near_top"].index and code in matrices["near_top"].columns:
                    near_top = float(matrices["near_top"].loc[asof, code])
                ma_break = _confirm_ma_breakdown(asof, code, matrices, sell_ma_break_confirm_days) if sell_ma_break_on else False
                tp_reached = bool(sell_take_profit_on and np.isfinite(ret) and ret >= sell_take_profit_pct and near_top > 0)
                structural_sell = bool(
                    (sell_top_break_on and top_break > 0)
                    or (sell_box_break_on and box_break > 0)
                    or ma_break
                    or tp_reached
                )
                if structural_sell and held_days >= min_hold_days:
                    signal_sell = True
            if asof in matrices["normal_pullback_signal"].index and code in matrices["normal_pullback_signal"].columns:
                normal_pullback = float(matrices["normal_pullback_signal"].loc[asof, code])
                abnormal_rev = float(matrices["abnormal_climax_reversal"].loc[asof, code])
                # Avoid premature exit on healthy pullback, but force defense on abnormal reversal.
                if signal_sell and (not structural_sell) and normal_pullback > 0 and abnormal_rev <= 0:
                    signal_sell = False
                if abnormal_rev > 0:
                    signal_sell = True
            effective_stop = min(float(cfg.strategy.stop_loss_pct), max_single_loss_pct)
            effective_trailing_stop = _effective_trailing_stop_pct(ret, cfg)
            if ret <= -effective_stop or dd <= -effective_trailing_stop or signal_sell:
                shares = positions.pop(code)
                epx = entry_price.pop(code, None)
                ecost = entry_cost.pop(code, None)
                peak_price.pop(code, None)
                entry_i.pop(code, None)
                exec_sell = c * (1.0 - slippage)
                proceeds = shares * exec_sell * (1.0 - sell_fee)
                cash += proceeds
                if epx is not None:
                    base_cost = float(ecost) if ecost is not None else float(epx) * (1.0 + buy_fee + slippage)
                    pnl = shares * (float(exec_sell) * (1.0 - sell_fee) - base_cost)
                    day_realized_pnl += float(pnl)
                trades.append({"date": asof, "code": code, "side": "SELL", "price": c, "shares": shares})

        if i % eff_entry_interval_days == 0:
            market_ok, market_win_prob, _ = _calc_market_gate_state(asof, matrices, bench_curve_unit, cfg)
            targets = [c for c in buy_scores.index if c in allow][:eff_top_k]
            # sell non-targets
            for code in list(positions.keys()):
                if code in targets:
                    continue
                if cross_hold_on and code not in selection_set:
                    if asof in price_wide.index and code in price_wide.columns:
                        ccur = float(price_wide.loc[asof, code])
                        ma20 = float(matrices["ma_20"].loc[asof, code]) if asof in matrices["ma_20"].index else np.nan
                        ma60 = float(matrices["ma_60"].loc[asof, code]) if asof in matrices["ma_60"].index else np.nan
                        r20 = float(matrices["ret_20"].loc[asof, code]) if asof in matrices["ret_20"].index else np.nan
                        if np.isfinite(ccur) and np.isfinite(ma20) and np.isfinite(ma60) and np.isfinite(r20):
                            if ccur > ma20 and ma20 > ma60 and r20 > 0.0:
                                continue
                held_days = i - int(entry_i.get(code, i))
                if held_days < min_hold_days:
                    continue
                if trend_extend_hold and asof in price_wide.index and code in price_wide.columns:
                    ccur = float(price_wide.loc[asof, code])
                    ma20 = float(matrices["ma_20"].loc[asof, code]) if asof in matrices["ma_20"].index else np.nan
                    ma60 = float(matrices["ma_60"].loc[asof, code]) if asof in matrices["ma_60"].index else np.nan
                    r20 = float(matrices["ret_20"].loc[asof, code]) if asof in matrices["ret_20"].index else np.nan
                    normal_pullback = float(matrices["normal_pullback_signal"].loc[asof, code]) if asof in matrices["normal_pullback_signal"].index else 0.0
                    abnormal_rev = float(matrices["abnormal_climax_reversal"].loc[asof, code]) if asof in matrices["abnormal_climax_reversal"].index else 0.0
                    if np.isfinite(ccur) and np.isfinite(ma20) and np.isfinite(ma60) and np.isfinite(r20):
                        if ccur > ma20 and ma20 > ma60 and r20 > 0.03:
                            continue
                    if normal_pullback > 0 and abnormal_rev <= 0:
                        continue
                c = float(price_wide.loc[asof, code]) if code in price_wide.columns else None
                if c is None or pd.isna(c) or c <= 0:
                    continue
                shares = positions.pop(code)
                epx = entry_price.pop(code, None)
                ecost = entry_cost.pop(code, None)
                peak_price.pop(code, None)
                entry_i.pop(code, None)
                exec_sell = c * (1.0 - slippage)
                cash += shares * exec_sell * (1.0 - sell_fee)
                if epx is not None:
                    base_cost = float(ecost) if ecost is not None else float(epx) * (1.0 + buy_fee + slippage)
                    pnl = shares * (float(exec_sell) * (1.0 - sell_fee) - base_cost)
                    day_realized_pnl += float(pnl)
                trades.append({"date": asof, "code": code, "side": "SELL", "price": c, "shares": shares})

            slots = max(0, cfg.strategy.max_positions - len(positions))
            buy_list = [x for x in targets if x not in positions][:slots]
            if buy_list and i > pause_until_idx:
                current_exposure = 0.0
                for pcode, pshares in positions.items():
                    if asof in price_wide.index and pcode in price_wide.columns:
                        pc = float(price_wide.loc[asof, pcode])
                        if np.isfinite(pc) and pc > 0:
                            current_exposure += pshares * pc
                equity_now = cash + current_exposure
                effective_exposure_cap = 0.0 if i <= crash_until_idx else eff_exposure_cap
                max_exposure_value = effective_exposure_cap * equity_now
                can_add = max(0.0, max_exposure_value - current_exposure)
                deploy_budget = min(cash, can_add)
                if deploy_budget <= 0:
                    buy_list = []
            if buy_list and i > pause_until_idx:
                if market_gate_enable:
                    if (not market_ok) or (market_win_prob < market_min_win_prob):
                        buy_list = []
            if buy_list and i > pause_until_idx:
                alloc_map = _build_weighted_alloc(
                    buy_list=buy_list,
                    buy_scores=buy_scores,
                    deploy_budget=float(deploy_budget),
                    enable=signal_weighted_alloc_on,
                    power=signal_weighted_power,
                    max_ratio=signal_weighted_max_ratio,
                )
                for code in buy_list:
                    alloc = float(alloc_map.get(code, 0.0))
                    if alloc <= 0:
                        continue
                    c = float(price_wide.loc[asof, code]) if code in price_wide.columns else None
                    if c is None or pd.isna(c) or c <= 0:
                        continue
                    if anti_fomo_filter_enable:
                        abnormal_rev = (
                            float(matrices["abnormal_climax_reversal"].loc[asof, code])
                            if (asof in matrices["abnormal_climax_reversal"].index and code in matrices["abnormal_climax_reversal"].columns)
                            else 0.0
                        )
                        top_break_risk = (
                            float(matrices["top_break_risk"].loc[asof, code])
                            if (asof in matrices["top_break_risk"].index and code in matrices["top_break_risk"].columns)
                            else 0.0
                        )
                        ret5 = (
                            float(matrices["ret_5"].loc[asof, code])
                            if (asof in matrices["ret_5"].index and code in matrices["ret_5"].columns and pd.notna(matrices["ret_5"].loc[asof, code]))
                            else np.nan
                        )
                        vr20 = (
                            float(matrices["vol_ratio20"].loc[asof, code])
                            if (asof in matrices["vol_ratio20"].index and code in matrices["vol_ratio20"].columns and pd.notna(matrices["vol_ratio20"].loc[asof, code]))
                            else np.nan
                        )
                        usr = (
                            float(matrices["upper_shadow_ratio"].loc[asof, code])
                            if (asof in matrices["upper_shadow_ratio"].index and code in matrices["upper_shadow_ratio"].columns and pd.notna(matrices["upper_shadow_ratio"].loc[asof, code]))
                            else np.nan
                        )
                        fomo_combo = bool(
                            np.isfinite(ret5)
                            and np.isfinite(vr20)
                            and np.isfinite(usr)
                            and ret5 >= anti_fomo_ret5_threshold
                            and vr20 >= anti_fomo_vol_ratio_threshold
                            and usr >= anti_fomo_upper_shadow_threshold
                        )
                        if abnormal_rev > 0 or top_break_risk > 0 or fomo_combo:
                            continue
                    volv = float(matrices["vol_20"].loc[asof, code]) if (asof in matrices["vol_20"].index and code in matrices["vol_20"].columns) else np.nan
                    if np.isfinite(volv) and not (open_vol_min <= volv <= open_vol_max):
                        continue
                    if require_above_ma120 or require_above_ma200:
                        trend_ok = _passes_ma_trend_filter(
                            asof=asof,
                            code=code,
                            c=float(c),
                            matrices=matrices,
                            require_above_ma120=require_above_ma120,
                            require_above_ma200=require_above_ma200,
                            confirm_days=ma_trend_confirm_days,
                            buffer_bps=ma_trend_filter_buffer_bps,
                        )
                        if not trend_ok:
                            exception_ok = False
                            if allow_short_below_ma200_exception:
                                r5 = float(matrices["ret_5"].loc[asof, code]) if (asof in matrices["ret_5"].index and code in matrices["ret_5"].columns) else np.nan
                                vr = float(matrices["vol_ratio20"].loc[asof, code]) if (asof in matrices["vol_ratio20"].index and code in matrices["vol_ratio20"].columns) else np.nan
                                md = float(matrices["macd_dif"].loc[asof, code]) if (asof in matrices["macd_dif"].index and code in matrices["macd_dif"].columns) else np.nan
                                ms = float(matrices["macd_dea"].loc[asof, code]) if (asof in matrices["macd_dea"].index and code in matrices["macd_dea"].columns) else np.nan
                                exception_ok = (
                                    np.isfinite(r5)
                                    and r5 >= ma200_exc_min_ret5
                                    and np.isfinite(vr)
                                    and ma200_exc_vr_min <= vr <= ma200_exc_vr_max
                                    and np.isfinite(md)
                                    and np.isfinite(ms)
                                    and md > ms
                                )
                            if not exception_ok:
                                continue
                    if market_gate_enable:
                        stock_win_prob = _estimate_stock_win_prob(asof, code, matrices)
                        total_win_prob = 0.65 * market_win_prob + 0.35 * stock_win_prob
                        if total_win_prob < market_min_win_prob:
                            continue
                    effective_stop = min(float(cfg.strategy.stop_loss_pct), max_single_loss_pct)
                    risk_per_share = max(1e-9, c * effective_stop)
                    shares_by_risk = int((equity_now * max_trade_risk_pct) // risk_per_share)
                    exec_buy = c * (1.0 + slippage)
                    shares_by_alloc = int((alloc * (1.0 - buy_fee)) // exec_buy)
                    shares = min(shares_by_alloc, shares_by_risk)
                    if shares <= 0:
                        continue
                    cost = shares * exec_buy * (1.0 + buy_fee)
                    if cost > cash:
                        continue
                    cash -= cost
                    positions[code] = shares
                    entry_price[code] = c
                    entry_cost[code] = exec_buy * (1.0 + buy_fee)
                    peak_price[code] = c
                    entry_i[code] = i
                    trades.append({"date": asof, "code": code, "side": "BUY", "price": c, "shares": shares})

        eq = cash
        for code, shares in positions.items():
            if code in price_wide.columns and asof in price_wide.index:
                c = float(price_wide.loc[asof, code])
                if not pd.isna(c):
                    eq += shares * c
        equity_rows.append({"date": asof, "equity": eq})
        if day_realized_pnl < 0:
            losing_streak_days += 1
        elif day_realized_pnl > 0:
            losing_streak_days = 0
        if pause_after_loss_n > 0 and losing_streak_days >= pause_after_loss_n:
            pause_until_idx = max(pause_until_idx, i + pause_days)
            losing_streak_days = 0

    equity = pd.DataFrame(equity_rows).sort_values("date")
    equity["ret"] = equity["equity"].pct_change(fill_method=None).fillna(0.0)
    bench = _build_benchmark_equity(
        price_wide, cfg.data.benchmark_index, float(equity["equity"].iloc[0]), benchmark_close=benchmark_close
    )
    eq2 = equity.set_index("date").join(bench, how="left")
    eq2["benchmark_equity"] = eq2["benchmark_equity"].ffill().bfill()
    eq2["bench_ret"] = eq2["benchmark_equity"].pct_change(fill_method=None).fillna(0.0)
    ann = (eq2["equity"].iloc[-1] / eq2["equity"].iloc[0]) ** (252 / max(len(eq2) - 1, 1)) - 1.0
    bench_ann = (eq2["benchmark_equity"].iloc[-1] / eq2["benchmark_equity"].iloc[0]) ** (252 / max(len(eq2) - 1, 1)) - 1.0
    vol = eq2["ret"].std(ddof=0) * (252**0.5)
    tracking = (eq2["ret"] - eq2["bench_ret"]).std(ddof=0) * (252**0.5)
    dd = equity["equity"] / equity["equity"].cummax() - 1.0
    metrics = {
        "ann_return": float(ann),
        "bench_ann_return": float(bench_ann),
        "excess_ann_return": float(ann - bench_ann),
        "ann_volatility": float(vol),
        "tracking_error": float(tracking),
        "information_ratio": float((ann - bench_ann) / tracking) if tracking > 1e-12 else 0.0,
        "sharpe": float(ann / vol) if vol > 1e-12 else 0.0,
        "max_drawdown": float(dd.min()),
        "trade_count": int(len(trades)),
        "final_equity": float(equity["equity"].iloc[-1]),
    }

    if write_outputs:
        Path("output").mkdir(exist_ok=True)
        eq2.reset_index().to_csv(f"output/{output_prefix}_equity_curve.csv", index=False)
        pd.DataFrame(trades).to_csv(f"output/{output_prefix}_trades.csv", index=False)
        if weekly_weight_rows:
            pd.DataFrame(weekly_weight_rows).to_csv(f"output/{output_prefix}_weekly_weights.csv", index=False)
    return BacktestResult(equity_curve=equity, trades=pd.DataFrame(trades), metrics=metrics)
