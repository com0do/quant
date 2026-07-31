from __future__ import annotations

import pandas as pd


def check_exposure_limits(
    code: str,
    add_notional: float,
    portfolio_notional: float,
    exposure_df: pd.DataFrame | None,
    max_industry_exposure_pct: float = 0.35,
    max_style_exposure_pct: float = 0.40,
) -> bool:
    if exposure_df is None or exposure_df.empty or portfolio_notional <= 0:
        return True
    row = exposure_df[exposure_df["code"].astype(str) == str(code)]
    if row.empty:
        return True
    r = row.iloc[0]
    ind = float(r.get("industry_weight", 0.0))
    sty = float(r.get("style_weight", 0.0))
    new_ratio = add_notional / portfolio_notional
    return (ind + new_ratio) <= max_industry_exposure_pct and (sty + new_ratio) <= max_style_exposure_pct
