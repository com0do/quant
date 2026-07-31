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
    cfg.data.benchmark_index = "399303.XSHE"
    cfg.data.cross_index_hold_enable = False
    cfg.data.cross_index_candidates = []

    s = cfg.strategy
    s.top_k = int(top_k)
    s.max_positions = int(max_pos)
    s.entry_interval_days = int(entry)
    s.buy_score_threshold = 0.0
    s.sell_score_threshold = float(sell)
    s.stop_loss_pct = float(stop)
    s.trailing_stop_pct = float(trail)
    s.sell_point_rule_enable = False
    s.dynamic_trailing_stop_enable = False

    m = run_backtest(cfg, output_prefix="global_max_refine_tmp", write_outputs=False).metrics
    m.update(
        {
            "benchmark_index": "399303.XSHE",
            "cross_index_hold_enable": False,
            "top_k": top_k,
            "max_positions": max_pos,
            "entry_interval_days": entry,
            "buy_score_threshold": 0.0,
            "sell_score_threshold": sell,
            "stop_loss_pct": stop,
            "trailing_stop_pct": trail,
            "sell_point_rule_enable": False,
            "dynamic_trailing_stop_enable": False,
        }
    )
    return m


def main() -> None:
    config_path = "config/final_freeze_2025.toml"
    top_k = [4, 6, 8]
    max_pos = [3, 4, 5]
    entry = [2, 3]
    sell = [0.86, 0.88, 0.90]
    stop = [0.04, 0.05]
    trail = [0.10, 0.11]

    combos = list(itertools.product(top_k, max_pos, entry, sell, stop, trail))
    payloads = [(*c, config_path) for c in combos]
    print(f"[global-max-refine] workers=16 combos={len(payloads)}")
    with ProcessPoolExecutor(max_workers=16) as ex:
        rows = list(ex.map(_run_one, payloads, chunksize=1))
    df = pd.DataFrame(rows)
    df = df.sort_values(["ann_return", "excess_ann_return", "sharpe"], ascending=False).reset_index(drop=True)
    Path("output").mkdir(exist_ok=True)
    out = Path("output/global_max_return_refine.csv")
    df.to_csv(out, index=False)
    print(
        df.head(20)[
            [
                "benchmark_index",
                "cross_index_hold_enable",
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
            ]
        ].to_string(index=False)
    )
    print(f"[OK] wrote {out}")


if __name__ == "__main__":
    main()
