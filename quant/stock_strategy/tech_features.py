from __future__ import annotations

import numpy as np
import pandas as pd


def zscore(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    m = x.mean()
    sd = x.std(ddof=0)
    if not np.isfinite(sd) or sd <= 1e-12:
        return pd.Series(0.0, index=x.index)
    return (x - m) / sd


def add_price_features(panel: pd.DataFrame) -> pd.DataFrame:
    if panel is None or panel.empty:
        return pd.DataFrame()
    p = panel.copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p.sort_values(["code", "date"])
    grp = p.groupby("code")
    p["ret_1"] = grp["close"].pct_change(1)
    p["ret_5"] = grp["close"].pct_change(5)
    p["ret_20"] = grp["close"].pct_change(20)
    p["ret_60"] = grp["close"].pct_change(60)
    p["vol_20"] = grp["close"].pct_change().rolling(20).std(ddof=0).reset_index(level=0, drop=True)
    p["ma_5"] = grp["close"].rolling(5).mean().reset_index(level=0, drop=True)
    p["ma_20"] = grp["close"].rolling(20).mean().reset_index(level=0, drop=True)
    p["ma_60"] = grp["close"].rolling(60).mean().reset_index(level=0, drop=True)
    return p
