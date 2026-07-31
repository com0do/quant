#!/usr/bin/env python3
from __future__ import annotations

import copy
import itertools
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.backtest.engine import run_backtest
from quant.config import load_config


def _run_one(payload: tuple) -> dict:
    top_k, max_pos, entry, sell, stop, trail, config_path = payload
    cfg = copy.deepcopy(load_config(config_path))
    cfg.strategy.top_k = int(top_k)
    cfg.strategy.max_positions = int(max_pos)
    cfg.strategy.entry_interval_days = int(entry)
    cfg.strategy.buy_score_threshold = 0.0
    cfg.strategy.sell_score_threshold = float(sell)
    cfg.strategy.stop_loss_pct = float(stop)
    cfg.strategy.trailing_stop_pct = float(trail)
    m = run_backtest(cfg, output_prefix="csi1000_refit48_tmp", write_outputs=False).metrics
    m.update(
        {
            "top_k": top_k,
            "max_positions": max_pos,
            "entry_interval_days": entry,
            "buy_score_threshold": 0.0,
            "sell_score_threshold": sell,
            "stop_loss_pct": stop,
            "trailing_stop_pct": trail,
        }
    )
    mdd_abs = abs(min(float(m.get("max_drawdown", 0.0)), 0.0))
    m["score_refit"] = (
        float(m.get("excess_ann_return", 0.0)) * 24
        + float(m.get("ann_return", 0.0)) * 6
        + float(m.get("sharpe", 0.0)) * 2
        - mdd_abs * 12
    )
    return m


def main() -> None:
    config_path = "config/final_freeze_2025.toml"
    top_k = [6, 8]
    max_pos = [4, 6]
    entry = [2, 3]
    sell = [0.90, 0.94, 0.98]
    stop = [0.04, 0.05]
    trail = [0.08, 0.10]
    combos = list(itertools.product(top_k, max_pos, entry, sell, stop, trail))
    payloads = [(*c, config_path) for c in combos]
    workers = 16
    print(f"[csi1000-refit48] workers={workers} combos={len(payloads)}")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_run_one, payloads, chunksize=1))
    df = pd.DataFrame(rows)
    df = df.sort_values(["score_refit", "ann_return", "excess_ann_return"], ascending=False).reset_index(drop=True)
    Path("output").mkdir(exist_ok=True)
    out_path = Path("output/csi1000_refit48_results.csv")
    df.to_csv(out_path, index=False)
    show_cols = [
        "top_k",
        "max_positions",
        "entry_interval_days",
        "sell_score_threshold",
        "stop_loss_pct",
        "trailing_stop_pct",
        "ann_return",
        "bench_ann_return",
        "excess_ann_return",
        "sharpe",
        "max_drawdown",
        "trade_count",
        "final_equity",
        "score_refit",
    ]
    print(df.head(20)[show_cols].to_string(index=False))
    print(f"[OK] wrote {out_path}")


if __name__ == "__main__":
    main()

