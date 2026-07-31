from __future__ import annotations

import pandas as pd


def exclude_paused_and_no_liquidity(price_panel: pd.DataFrame, min_amount: float = 1_000_000.0) -> list[str]:
    if price_panel is None or price_panel.empty:
        return []
    p = price_panel.copy()
    p["paused"] = pd.to_numeric(p.get("paused"), errors="coerce").fillna(0.0)
    p["money"] = pd.to_numeric(p.get("money"), errors="coerce").fillna(0.0)
    latest = p.sort_values("date").groupby("code").tail(1)
    out = latest[(latest["paused"] < 1.0) & (latest["money"] >= min_amount)]
    return out["code"].astype(str).tolist()
