from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import copy
import itertools
import os
from pathlib import Path

import pandas as pd

from quant.backtest.engine import run_backtest
from quant.config import load_config
from quant.tools.optimizer import layered_score
from quant.tools.vectorbt_optimize import run_vectorbt_optimize


def _resolve_workers(requested: int | None, fallback: int) -> int:
    # Safety valve: default cap avoids accidental CPU saturation.
    safe_cap = max(1, int(os.getenv("OPT_SAFE_MAX_WORKERS", "4")))
    raw = int(requested or fallback)
    wk = max(1, min(raw, safe_cap))
    if wk < raw:
        print(f"[scan-opt] worker cap applied: requested={raw}, cap={safe_cap}, using={wk}")
    return wk


def _vectorbt_prefilter(combos: list[tuple], keep_ratio: float = 0.35) -> list[tuple]:
    path = Path("output/vectorbt_optimization.csv")
    if not path.exists() or not combos:
        return combos
    try:
        df = pd.read_csv(path)
    except Exception:
        return combos
    if df.empty or "top_k" not in df.columns or "rebalance_days" not in df.columns:
        return combos
    df = df.sort_values("score", ascending=False).head(max(20, int(len(df) * 0.15)))
    hint_pairs = {(int(r.top_k), int(r.rebalance_days)) for r in df.itertuples(index=False)}
    hint_topk = {x[0] for x in hint_pairs}
    hint_days = {x[1] for x in hint_pairs}
    scored: list[tuple[float, tuple]] = []
    for c in combos:
        top_k, entry, _, _, stop, trail, _ = c
        s = 0.0
        if (int(top_k), int(entry)) in hint_pairs:
            s += 2.0
        if int(top_k) in hint_topk:
            s += 1.0
        if int(entry) in hint_days:
            s += 1.0
        if float(stop) <= 0.03:
            s += 0.2
        if float(trail) <= 0.08:
            s += 0.2
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    k = max(12, int(len(scored) * keep_ratio))
    kept = [x[1] for x in scored[:k]]
    print(f"[scan-opt] vectorbt prefilter: {len(combos)} -> {len(kept)}")
    return kept


def _run_one(args: tuple, base_cfg) -> dict:
    top_k, entry, buy_thr, sell_thr, stop, trail, wpair = args
    cfg = copy.deepcopy(base_cfg)
    cfg.strategy.top_k = int(top_k)
    cfg.strategy.max_positions = min(int(top_k), cfg.strategy.max_positions)
    cfg.strategy.entry_interval_days = int(entry)
    cfg.strategy.buy_score_threshold = float(buy_thr)
    cfg.strategy.sell_score_threshold = float(sell_thr)
    cfg.strategy.stop_loss_pct = float(stop)
    cfg.strategy.trailing_stop_pct = float(trail)
    if len(cfg.strategy.strategy_names) >= 2:
        cfg.strategy.strategy_weights = [float(wpair[0]), float(wpair[1])]
    m = run_backtest(cfg, output_prefix="scan_tmp", write_outputs=False).metrics
    row = {
        "top_k": top_k,
        "entry_interval_days": entry,
        "buy_score_threshold": buy_thr,
        "sell_score_threshold": sell_thr,
        "stop_loss_pct": stop,
        "trailing_stop_pct": trail,
        "strategy_weights": list(cfg.strategy.strategy_weights),
        **m,
    }
    row["score"] = layered_score(m)
    return row


def run_scan_optimize(workers: int = 16, config_path: str | None = None) -> pd.DataFrame:
    base_cfg = load_config(config_path=config_path)
    quick = os.getenv("SCAN_OPT_QUICK", "0") == "1"
    # Generate fresh vectorbt hints before process-based fine evaluation.
    try:
        run_vectorbt_optimize(config_path=config_path)
    except Exception as e:
        print(f"[scan-opt] vectorbt pre-run skipped: {e}")
    combos = list(
        itertools.product(
            [12] if quick else [10, 12, 15, 20],
            [6] if quick else [5, 6, 8, 10],
            [0.55] if quick else [0.50, 0.55, 0.60],
            [0.62] if quick else [0.58, 0.62, 0.66],
            [0.03] if quick else [0.02, 0.03, 0.04],
            [0.08] if quick else [0.06, 0.08, 0.10],
            [(0.6, 0.4)] if quick else [(0.8, 0.2), (0.7, 0.3), (0.6, 0.4), (0.5, 0.5)],
        )
    )
    filtered = _vectorbt_prefilter(combos, keep_ratio=0.35 if not quick else 1.0)
    wk = _resolve_workers(workers, int(getattr(base_cfg.daemon, "preopen_scan_threads", 4)))
    payloads = [(x, base_cfg) for x in filtered]
    chunksize = max(1, len(payloads) // max(1, wk * 8))
    print(f"[scan-opt] workers={wk}, combos={len(combos)}, after_prefilter={len(filtered)}")
    if len(payloads) <= 2:
        # Avoid process-pool startup overhead for tiny test grids.
        rows = [_run_one_star(p) for p in payloads]
    else:
        with ProcessPoolExecutor(max_workers=wk) as ex:
            rows = list(ex.map(_run_one_star, payloads, chunksize=chunksize))
    df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    Path("output").mkdir(exist_ok=True)
    df.to_csv("output/scan_optimization_results.csv", index=False)
    return df


def _run_one_star(payload: tuple) -> dict:
    args, base_cfg = payload
    return _run_one(args, base_cfg)
