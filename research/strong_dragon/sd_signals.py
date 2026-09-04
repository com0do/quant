"""Reverse-engineered approximation of the "强龙出现" (Strong Dragon Appears) indicator and a single-stock trade simulator.

RED   (main up-wave in progress): MA5 > MA10 > MA20 and close / close[3 bars ago] - 1 >= RED_RET3.
GREEN (main up-wave over):        a RED within the previous GREEN_LOOKBACK bars, no RED today, close < MA5.

The thresholds were chosen on the 22 screenshots in cases.csv (see README, "Indicator agreement");
they are an approximation of a closed-source formula, not the formula itself.

Execution model used everywhere in this study (and mirrored in jq_strong_dragon.py):
signals are evaluated on the close of day t, orders execute at the open of day t+1;
a buy is skipped when t+1 opens at the limit-up price, a sell is postponed while the open is at limit-down.
"""
from __future__ import annotations

import pandas as pd

RED_RET3 = 0.10
GREEN_LOOKBACK = 15
EPS = 1e-9  # ties (e.g. MA10 == MA20 on flat prices) must not depend on float noise across pandas versions
COMMISSION = 0.0003
STAMP_DUTY = 0.0005
SLIPPAGE = 0.001


def add_indicator(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for n in (5, 10, 20):
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    df["ret3"] = df["close"] / df["close"].shift(3) - 1
    df["red"] = ((df["ma5"] > df["ma10"] * (1 + EPS)) & (df["ma10"] > df["ma20"] * (1 + EPS))
                 & (df["ret3"] >= RED_RET3 - EPS))
    recent_red = df["red"].astype(float).shift(1).rolling(GREEN_LOOKBACK, min_periods=1).max().fillna(0) > 0
    df["green"] = recent_red & ~df["red"] & (df["close"] < df["ma5"] * (1 - EPS))
    prev_red = df["red"].shift(1, fill_value=False).astype(bool)
    df["fresh_red"] = df["red"] & ~prev_red
    return df


def simulate(df: pd.DataFrame, limit: float, stop_loss: float | None = 0.08, max_hold: int | None = None,
             entry: str = "fresh_red") -> list[dict]:
    """Trades of one stock, one position at a time. df must already carry add_indicator() columns.

    `limit` is the board's daily limit ratio; a per-row "lim" column, when present, takes precedence
    (it carries the exchange's actual limit, e.g. 5% on ST days).
    """
    df = df.reset_index(drop=True)
    per_row = "lim" in df.columns
    trades, pos = [], None
    for t in range(len(df) - 1):
        today, nxt = df.iloc[t], df.iloc[t + 1]
        lim = nxt["lim"] if per_row and pd.notna(nxt["lim"]) else limit
        up_px = today["close"] * (1 + lim)
        dn_px = today["close"] * (1 - lim)
        if pos is None:
            if bool(today[entry]):
                if nxt["open"] >= up_px * 0.995:
                    trades.append({"signal_date": today["date"], "status": "unfilled_limit_up_open"})
                    continue
                pos = {"signal_date": today["date"], "entry_date": nxt["date"],
                       "entry_px": nxt["open"] * (1 + SLIPPAGE) * (1 + COMMISSION), "bars": 0, "peak": nxt["high"]}
            continue
        pos["bars"] += 1
        pos["peak"] = max(pos["peak"], today["high"])
        reason = None
        if bool(today["green"]):
            reason = "green"
        elif stop_loss is not None and today["close"] <= pos["entry_px"] * (1 - stop_loss):
            reason = "stop_loss"
        elif max_hold is not None and pos["bars"] >= max_hold:
            reason = "max_hold"
        if reason and nxt["open"] > dn_px * 1.005:
            exit_px = nxt["open"] * (1 - SLIPPAGE) * (1 - COMMISSION - STAMP_DUTY)
            trades.append({**pos, "status": "closed", "exit_date": nxt["date"], "exit_px": exit_px, "reason": reason,
                           "ret": exit_px / pos["entry_px"] - 1, "peak_ret": pos["peak"] / pos["entry_px"] - 1})
            pos = None
    if pos is not None:
        last = df.iloc[-1]
        trades.append({**pos, "status": "open_at_end", "exit_date": last["date"], "exit_px": last["close"],
                       "reason": "end_of_data", "ret": last["close"] / pos["entry_px"] - 1,
                       "peak_ret": pos["peak"] / pos["entry_px"] - 1})
    return trades
