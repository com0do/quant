from __future__ import annotations

import os

def layered_score(metrics: dict) -> float:
    excess = float(metrics.get("excess_ann_return", -1.0))
    mdd = float(metrics.get("max_drawdown", -1.0))
    ann = float(metrics.get("ann_return", 0.0))
    sharpe = float(metrics.get("sharpe", 0.0))
    sharpe_floor = float(os.getenv("OPT_SHARPE_FLOOR", "2.0"))
    mdd_abs = abs(min(mdd, 0.0))

    if excess <= 0:
        return -1e9
    # Gate unstable parameter sets first, then rank by return.
    if sharpe < sharpe_floor:
        return -5e8 + sharpe
    if mdd_abs > 0.20:
        return -1e8
    # Penalize larger drawdown magnitude explicitly (mdd is negative).
    return excess * 24.0 + ann * 6.0 + sharpe * 2.0 - mdd_abs * 12.0
