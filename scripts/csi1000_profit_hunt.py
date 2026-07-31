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


def _run_one(payload: dict) -> dict:
    cfg = copy.deepcopy(load_config(payload["config_path"]))
    s = cfg.strategy
    s.top_k = int(payload["top_k"])
    s.max_positions = int(payload["max_positions"])
    s.entry_interval_days = int(payload["entry_interval_days"])
    s.buy_score_threshold = float(payload["buy_score_threshold"])
    s.sell_score_threshold = float(payload["sell_score_threshold"])
    s.stop_loss_pct = float(payload["stop_loss_pct"])
    s.trailing_stop_pct = float(payload["trailing_stop_pct"])
    s.sell_point_rule_enable = bool(payload["sell_point_rule_enable"])
    s.dynamic_trailing_stop_enable = bool(payload["dynamic_trailing_stop_enable"])

    m = run_backtest(cfg, output_prefix="csi1000_profit_hunt_tmp", write_outputs=False).metrics
    m.update(payload)
    return m


def run_grid(name: str, base: dict, grid: dict, workers: int = 16) -> pd.DataFrame:
    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    payloads = []
    for vals in combos:
        p = dict(base)
        for k, v in zip(keys, vals):
            p[k] = v
        payloads.append(p)
    print(f"[{name}] workers={workers} combos={len(payloads)}")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_run_one, payloads, chunksize=1))
    df = pd.DataFrame(rows)
    df = df.sort_values(["ann_return", "excess_ann_return", "sharpe"], ascending=False).reset_index(drop=True)
    return df


def main() -> None:
    Path("output").mkdir(exist_ok=True)
    base = {
        "config_path": "config/final_freeze_2025.toml",
        "top_k": 6,
        "max_positions": 6,
        "entry_interval_days": 3,
        "buy_score_threshold": 0.0,
        "sell_score_threshold": 0.94,
        "stop_loss_pct": 0.05,
        "trailing_stop_pct": 0.10,
        "sell_point_rule_enable": True,
        "dynamic_trailing_stop_enable": False,
    }

    # Round 1: toggle exits around current strong region.
    r1_grid = {
        "sell_score_threshold": [0.90, 0.94],
        "sell_point_rule_enable": [False, True],
        "dynamic_trailing_stop_enable": [False, True],
    }
    r1 = run_grid("profit-hunt-r1", base, r1_grid, workers=16)
    r1.to_csv("output/csi1000_profit_hunt_r1.csv", index=False)

    best = r1.iloc[0].to_dict()
    base.update(
        {
            "sell_score_threshold": float(best["sell_score_threshold"]),
            "sell_point_rule_enable": bool(best["sell_point_rule_enable"]),
            "dynamic_trailing_stop_enable": bool(best["dynamic_trailing_stop_enable"]),
        }
    )

    # Round 2: profit-first wider search with best exit toggles fixed.
    r2_grid = {
        "top_k": [6, 8, 10],
        "max_positions": [6, 8],
        "entry_interval_days": [2, 3, 4],
        "sell_score_threshold": [0.88, 0.92, 0.96],
        "stop_loss_pct": [0.04, 0.05],
        "trailing_stop_pct": [0.08, 0.10],
    }
    r2 = run_grid("profit-hunt-r2", base, r2_grid, workers=16)
    r2.to_csv("output/csi1000_profit_hunt_r2.csv", index=False)

    best2 = r2.iloc[0].to_dict()
    base.update(
        {
            "top_k": int(best2["top_k"]),
            "max_positions": int(best2["max_positions"]),
            "entry_interval_days": int(best2["entry_interval_days"]),
            "sell_score_threshold": float(best2["sell_score_threshold"]),
            "stop_loss_pct": float(best2["stop_loss_pct"]),
            "trailing_stop_pct": float(best2["trailing_stop_pct"]),
        }
    )

    # Round 3: local refine around round-2 best.
    r3_grid = {
        "top_k": sorted(set([base["top_k"], base["top_k"] + 2])),
        "max_positions": sorted(set([max(4, base["max_positions"] - 2), base["max_positions"]])),
        "entry_interval_days": sorted(set([base["entry_interval_days"], base["entry_interval_days"] + 1])),
        "sell_score_threshold": sorted(
            set(
                [
                    round(max(0.8, base["sell_score_threshold"] - 0.02), 2),
                    round(base["sell_score_threshold"], 2),
                    round(min(0.99, base["sell_score_threshold"] + 0.02), 2),
                ]
            )
        ),
        "stop_loss_pct": sorted(set([round(max(0.02, base["stop_loss_pct"] - 0.01), 2), round(base["stop_loss_pct"], 2)])),
        "trailing_stop_pct": sorted(set([round(base["trailing_stop_pct"], 2), round(min(0.15, base["trailing_stop_pct"] + 0.02), 2)])),
    }
    r3 = run_grid("profit-hunt-r3", base, r3_grid, workers=16)
    r3.to_csv("output/csi1000_profit_hunt_r3.csv", index=False)

    final = r3.iloc[0]
    show_cols = [
        "top_k",
        "max_positions",
        "entry_interval_days",
        "sell_score_threshold",
        "stop_loss_pct",
        "trailing_stop_pct",
        "sell_point_rule_enable",
        "dynamic_trailing_stop_enable",
        "ann_return",
        "bench_ann_return",
        "excess_ann_return",
        "sharpe",
        "max_drawdown",
        "trade_count",
        "final_equity",
    ]
    print("\n[FINAL BEST]\n" + final[show_cols].to_string())
    print("[OK] wrote output/csi1000_profit_hunt_r1.csv/r2.csv/r3.csv")


if __name__ == "__main__":
    main()
