from __future__ import annotations

import pandas as pd

from quant.config import AppConfig
from quant.stock_data.data_service import DataService


def intraday_sell_scores(
    data: DataService,
    cfg: AppConfig,
    asof: pd.Timestamp,
    held_codes: list[str],
) -> pd.Series:
    if not held_codes:
        return pd.Series(dtype=float)
    now = pd.Timestamp(asof)
    start = (now - pd.Timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    out: dict[str, float] = {}
    for code in held_codes:
        try:
            m1 = data.get_price_single_minute(code, start_date=start, end_date=end)
        except Exception:
            continue
        if m1 is None or m1.empty or "close" not in m1.columns:
            continue
        s = m1.sort_values("date").tail(60).copy()
        close = pd.to_numeric(s["close"], errors="coerce").dropna()
        if len(close) < 8:
            continue
        day_open = float(close.iloc[0])
        last = float(close.iloc[-1])
        ret_open = last / max(day_open, 1e-9) - 1.0
        ret5 = last / max(float(close.iloc[-6]), 1e-9) - 1.0 if len(close) >= 6 else 0.0
        vol = float(close.pct_change().tail(20).std(ddof=0) or 0.0)
        score = 0.0
        if ret_open <= -cfg.strategy.stop_loss_pct * 0.6:
            score = max(score, 0.95)
        if ret5 <= -max(0.004, 3.0 * vol):
            score = max(score, 0.80)
        peak = float(close.cummax().iloc[-1])
        dd_peak = last / max(peak, 1e-9) - 1.0
        if dd_peak <= -cfg.strategy.trailing_stop_pct * 0.5:
            score = max(score, 0.88)
        if score > 0:
            out[code] = min(score, 1.0)
    return pd.Series(out, dtype=float).sort_values(ascending=False) if out else pd.Series(dtype=float)
