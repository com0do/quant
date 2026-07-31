from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from quant.config import load_config


def _perf(ret: pd.Series) -> tuple[float, float, float]:
    r = pd.to_numeric(ret, errors="coerce").fillna(0.0)
    if r.empty:
        return 0.0, 0.0, 0.0
    eq = (1.0 + r).cumprod()
    ann = float(eq.iloc[-1] ** (252.0 / max(1, len(eq))) - 1.0)
    vol = float(r.std(ddof=0) * np.sqrt(252.0))
    sharpe = float(ann / vol) if vol > 1e-12 else 0.0
    dd = eq / eq.cummax() - 1.0
    mdd = float(dd.min()) if not dd.empty else 0.0
    return ann, sharpe, mdd


def run_vectorbt_optimize(config_path: str | None = None) -> pd.DataFrame:
    """
    Fast vectorbt pre-screen to generate artifact used by
    optimize / scan-optimize / iterative-optimize prefilter stages.
    """
    cfg = load_config(config_path=config_path)
    db_path = cfg.data.sqlite_db_path
    start = cfg.data.start_date
    end = cfg.data.end_date
    bench = cfg.data.benchmark_index

    with sqlite3.connect(db_path) as conn:
        universe = pd.read_sql_query(
            """
            SELECT DISTINCT code
            FROM index_members
            WHERE index_code = ? AND date <= ?
            ORDER BY code
            """,
            conn,
            params=(bench, end),
        )
        if universe.empty:
            universe = pd.read_sql_query("SELECT DISTINCT code FROM prices_daily ORDER BY code", conn)
        codes = universe["code"].astype(str).tolist()
        # Keep universe moderate for ultra-fast vectorized pre-screen.
        codes = codes[: min(600, len(codes))]
        if not codes:
            raise RuntimeError("vectorbt optimize: empty universe")
        placeholders = ",".join(["?"] * len(codes))
        price = pd.read_sql_query(
            f"""
            SELECT date, code, close
            FROM prices_daily
            WHERE date >= ? AND date <= ? AND code IN ({placeholders})
            """,
            conn,
            params=[start, end, *codes],
        )
    if price.empty:
        raise RuntimeError("vectorbt optimize: empty price data")
    price["date"] = pd.to_datetime(price["date"])
    close = price.pivot(index="date", columns="code", values="close").sort_index().ffill()
    close = close.dropna(axis=1, how="all")
    close = close.loc[:, close.notna().sum() >= max(80, int(len(close) * 0.6))]
    if close.empty:
        raise RuntimeError("vectorbt optimize: insufficient close panel")

    # Use vectorbt indicator engine to build fast cross-sectional momentum proxy.
    ma_obj = vbt.MA.run(close, window=[20]).ma
    if isinstance(ma_obj.columns, pd.MultiIndex):
        ma20 = ma_obj.xs(20, level=0, axis=1)
    else:
        ma20 = ma_obj
    mom20 = close / ma20 - 1.0
    daily_ret = close.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)

    fee_bps = 5.0
    rows = []
    for top_k in [10, 12, 15, 20]:
        for rebalance_days in [5, 6, 8, 10]:
            w = pd.DataFrame(0.0, index=close.index, columns=close.columns)
            prev = np.zeros(len(close.columns), dtype=float)
            costs = np.zeros(len(close.index), dtype=float)
            for i, dt in enumerate(close.index):
                if i < 25:
                    continue
                if i % rebalance_days != 0:
                    continue
                s = mom20.iloc[i].replace([np.inf, -np.inf], np.nan).dropna()
                if s.empty:
                    continue
                pick = s.nlargest(min(top_k, len(s))).index
                cur = np.zeros(len(close.columns), dtype=float)
                sel_idx = [close.columns.get_loc(c) for c in pick]
                if sel_idx:
                    cur[sel_idx] = 1.0 / len(sel_idx)
                turnover = np.abs(cur - prev).sum() / 2.0
                costs[i] = turnover * (fee_bps / 10000.0)
                prev = cur
                w.iloc[i] = cur
            w = w.replace(0.0, np.nan).ffill().fillna(0.0)
            port_ret = (w.shift(1).fillna(0.0) * daily_ret).sum(axis=1) - pd.Series(costs, index=close.index)
            ann, sharpe, mdd = _perf(port_ret)
            score = ann * 10000.0 + sharpe * 10.0 + mdd * 30.0
            rows.append(
                {
                    "top_k": top_k,
                    "rebalance_days": rebalance_days,
                    "fee_bps": fee_bps,
                    "w_value": 0.6,
                    "w_quality": 0.4,
                    "w_mom20": 0.2,
                    "w_vol_penalty": 0.4,
                    "ann_return": ann,
                    "sharpe": sharpe,
                    "max_drawdown": mdd,
                    "score": score,
                }
            )
    df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    Path("output").mkdir(exist_ok=True)
    df.to_csv("output/vectorbt_optimization.csv", index=False)
    return df
