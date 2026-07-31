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


BENCHMARKS = ["000852.XSHG", "399303.XSHE", "000905.XSHG"]
BENCH_CROSS = {
    "000852.XSHG": ["000905.XSHG", "399303.XSHE"],
    "399303.XSHE": ["000852.XSHG", "000905.XSHG"],
    "000905.XSHG": ["000852.XSHG"],
}


def _run_one(payload: dict) -> dict:
    cfg = copy.deepcopy(load_config(payload["config_path"]))
    d = cfg.data
    s = cfg.strategy

    bench = str(payload["benchmark_index"])
    d.benchmark_index = bench
    d.cross_index_hold_enable = bool(payload["cross_index_hold_enable"])
    d.cross_index_candidates = BENCH_CROSS.get(bench, []) if d.cross_index_hold_enable else []

    s.top_k = int(payload["top_k"])
    s.max_positions = int(payload["max_positions"])
    s.entry_interval_days = int(payload["entry_interval_days"])
    s.buy_score_threshold = float(payload["buy_score_threshold"])
    s.sell_score_threshold = float(payload["sell_score_threshold"])
    s.stop_loss_pct = float(payload["stop_loss_pct"])
    s.trailing_stop_pct = float(payload["trailing_stop_pct"])
    s.sell_point_rule_enable = bool(payload["sell_point_rule_enable"])
    s.dynamic_trailing_stop_enable = bool(payload["dynamic_trailing_stop_enable"])

    m = run_backtest(cfg, output_prefix="global_max_return_tmp", write_outputs=False).metrics
    m.update(payload)
    return m


def _run_grid(name: str, payloads: list[dict], workers: int = 16) -> pd.DataFrame:
    print(f"[{name}] workers={workers} combos={len(payloads)}")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_run_one, payloads, chunksize=1))
    df = pd.DataFrame(rows)
    df = df.sort_values(["ann_return", "excess_ann_return", "sharpe"], ascending=False).reset_index(drop=True)
    return df


def _mk_payloads(base: dict, grid: dict) -> list[dict]:
    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    out = []
    for vals in combos:
        p = dict(base)
        for k, v in zip(keys, vals):
            p[k] = v
        out.append(p)
    return out


def main() -> None:
    Path("output").mkdir(exist_ok=True)
    base = {
        "config_path": "config/final_freeze_2025.toml",
        "benchmark_index": "000852.XSHG",
        "cross_index_hold_enable": True,
        "top_k": 8,
        "max_positions": 6,
        "entry_interval_days": 3,
        "buy_score_threshold": 0.0,
        "sell_score_threshold": 0.90,
        "stop_loss_pct": 0.05,
        "trailing_stop_pct": 0.10,
        "sell_point_rule_enable": False,
        "dynamic_trailing_stop_enable": False,
    }

    # Round 1: cross-index broad sweep.
    r1_grid = {
        "benchmark_index": BENCHMARKS,
        "cross_index_hold_enable": [False, True],
        "top_k": [6, 8, 10],
        "max_positions": [4, 6],
        "entry_interval_days": [2, 3],
        "sell_score_threshold": [0.90, 0.94],
    }
    r1 = _run_grid("global-max-r1", _mk_payloads(base, r1_grid), workers=16)
    r1.to_csv("output/global_max_return_r1.csv", index=False)
    best1 = r1.iloc[0].to_dict()

    # Round 2: focus on benchmark winner with broader risk controls.
    base.update(
        {
            "benchmark_index": str(best1["benchmark_index"]),
            "cross_index_hold_enable": bool(best1["cross_index_hold_enable"]),
            "top_k": int(best1["top_k"]),
            "max_positions": int(best1["max_positions"]),
            "entry_interval_days": int(best1["entry_interval_days"]),
            "sell_score_threshold": float(best1["sell_score_threshold"]),
        }
    )
    tk = int(base["top_k"])
    r2_grid = {
        "top_k": sorted(set([max(4, tk - 2), tk, tk + 2])),
        "max_positions": [4, 6],
        "entry_interval_days": [2, 3, 4],
        "sell_score_threshold": [0.88, 0.92, 0.96],
        "stop_loss_pct": [0.04, 0.05],
        "trailing_stop_pct": [0.09, 0.11],
        "cross_index_hold_enable": [False, True],
    }
    r2 = _run_grid("global-max-r2", _mk_payloads(base, r2_grid), workers=16)
    r2.to_csv("output/global_max_return_r2.csv", index=False)
    best2 = r2.iloc[0].to_dict()

    # Round 3: local refine around top return.
    base.update(
        {
            "top_k": int(best2["top_k"]),
            "max_positions": int(best2["max_positions"]),
            "entry_interval_days": int(best2["entry_interval_days"]),
            "sell_score_threshold": float(best2["sell_score_threshold"]),
            "stop_loss_pct": float(best2["stop_loss_pct"]),
            "trailing_stop_pct": float(best2["trailing_stop_pct"]),
            "cross_index_hold_enable": bool(best2["cross_index_hold_enable"]),
        }
    )
    r3_grid = {
        "top_k": sorted(set([max(4, int(base["top_k"]) - 2), int(base["top_k"]), int(base["top_k"]) + 2])),
        "max_positions": sorted(set([max(4, int(base["max_positions"]) - 2), int(base["max_positions"])])),
        "entry_interval_days": sorted(
            set([max(1, int(base["entry_interval_days"]) - 1), int(base["entry_interval_days"]), int(base["entry_interval_days"]) + 1])
        ),
        "sell_score_threshold": sorted(
            set(
                [
                    round(max(0.80, float(base["sell_score_threshold"]) - 0.02), 2),
                    round(float(base["sell_score_threshold"]), 2),
                    round(min(0.99, float(base["sell_score_threshold"]) + 0.02), 2),
                ]
            )
        ),
        "stop_loss_pct": sorted(set([round(max(0.02, float(base["stop_loss_pct"]) - 0.01), 2), round(float(base["stop_loss_pct"]), 2)])),
        "trailing_stop_pct": sorted(
            set([round(max(0.06, float(base["trailing_stop_pct"]) - 0.01), 2), round(float(base["trailing_stop_pct"]), 2), round(min(0.15, float(base["trailing_stop_pct"]) + 0.01), 2)])
        ),
        "sell_point_rule_enable": [False, True],
        "dynamic_trailing_stop_enable": [False, True],
    }
    r3 = _run_grid("global-max-r3", _mk_payloads(base, r3_grid), workers=16)
    r3.to_csv("output/global_max_return_r3.csv", index=False)

    final = r3.iloc[0]
    show_cols = [
        "benchmark_index",
        "cross_index_hold_enable",
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
    print("\n[FINAL GLOBAL BEST]\n" + final[show_cols].to_string())
    print("[OK] wrote output/global_max_return_r1.csv/r2.csv/r3.csv")


if __name__ == "__main__":
    main()
