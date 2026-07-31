from __future__ import annotations

import pandas as pd


def select_value_candidates(
    fundamentals: pd.DataFrame,
    pe_quantile: float = 0.4,
    pb_quantile: float = 0.4,
) -> list[str]:
    if fundamentals is None or fundamentals.empty:
        return []
    f = fundamentals.copy()
    f["pe_ratio"] = pd.to_numeric(f.get("pe_ratio"), errors="coerce")
    f["pb_ratio"] = pd.to_numeric(f.get("pb_ratio"), errors="coerce")
    pe_th = f["pe_ratio"].quantile(pe_quantile)
    pb_th = f["pb_ratio"].quantile(pb_quantile)
    out = f[(f["pe_ratio"] <= pe_th) & (f["pb_ratio"] <= pb_th)]
    return out["code"].astype(str).drop_duplicates().tolist()
