from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import itertools
import copy
import os
from pathlib import Path
import json

import pandas as pd

from quant.backtest.engine import run_backtest
from quant.config import AppConfig, load_config
from quant.tools.optimizer import layered_score
from quant.tools.vectorbt_optimize import run_vectorbt_optimize


def _resolve_workers(requested: int | None, fallback: int) -> int:
    # Safety valve: default cap avoids accidental CPU saturation.
    safe_cap = max(1, int(os.getenv("OPT_SAFE_MAX_WORKERS", "4")))
    raw = int(requested or fallback)
    wk = max(1, min(raw, safe_cap))
    if wk < raw:
        print(f"[optimize] worker cap applied: requested={raw}, cap={safe_cap}, using={wk}")
    return wk


def _run_one(payload: tuple) -> dict:
    args, base = payload
    top_k, entry, buy_thr, sell_thr, stop, trail, wpair = args
    cfg = copy.deepcopy(base)
    cfg.strategy.top_k = int(top_k)
    cfg.strategy.max_positions = min(int(top_k), cfg.strategy.max_positions)
    cfg.strategy.entry_interval_days = int(entry)
    cfg.strategy.buy_score_threshold = float(buy_thr)
    cfg.strategy.sell_score_threshold = float(sell_thr)
    cfg.strategy.stop_loss_pct = float(stop)
    cfg.strategy.trailing_stop_pct = float(trail)
    if len(cfg.strategy.strategy_names) >= 2:
        cfg.strategy.strategy_weights = [float(wpair[0]), float(wpair[1])]
    m = run_backtest(cfg, output_prefix="opt_tmp", write_outputs=False).metrics
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
    print(f"[optimize] vectorbt prefilter: {len(combos)} -> {len(kept)}")
    return kept


_WF_WINDOWS: list[tuple[str, str]] = [
    ("2025-01-01", "2025-01-31"),
    ("2025-02-01", "2025-02-28"),
    ("2025-03-01", "2025-03-31"),
    ("2025-04-01", "2025-04-30"),
    ("2025-05-01", "2025-05-31"),
    ("2025-06-01", "2025-06-30"),
    ("2025-07-01", "2025-07-31"),
    ("2025-08-01", "2025-08-31"),
    ("2025-09-01", "2025-09-30"),
    ("2025-10-01", "2025-10-31"),
    ("2025-11-01", "2025-11-30"),
    ("2025-12-01", "2025-12-23"),
]


def _parse_weights(v) -> list[float] | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return [float(x) for x in v]
    if isinstance(v, str):
        try:
            obj = json.loads(v)
            if isinstance(obj, list):
                return [float(x) for x in obj]
        except Exception:
            return None
    return None


def _apply_row_to_cfg(base_cfg: AppConfig, row: pd.Series) -> AppConfig:
    cfg = copy.deepcopy(base_cfg)
    cfg.strategy.top_k = int(row["top_k"])
    cfg.strategy.max_positions = min(int(row["top_k"]), cfg.strategy.max_positions)
    cfg.strategy.entry_interval_days = int(row["entry_interval_days"])
    cfg.strategy.buy_score_threshold = float(row["buy_score_threshold"])
    cfg.strategy.sell_score_threshold = float(row["sell_score_threshold"])
    cfg.strategy.stop_loss_pct = float(row["stop_loss_pct"])
    cfg.strategy.trailing_stop_pct = float(row["trailing_stop_pct"])
    w = _parse_weights(row.get("strategy_weights"))
    if w and len(w) == len(cfg.strategy.strategy_names):
        cfg.strategy.strategy_weights = [float(x) for x in w]
    return cfg


def _ann_to_period(ann: float, steps: int) -> float:
    if ann <= -0.999999:
        return -1.0
    return float((1.0 + float(ann)) ** (max(1, int(steps)) / 252.0) - 1.0)


def run_walkforward_gate(df: pd.DataFrame, base_cfg: AppConfig, top_n: int = 6) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    sharpe_floor = float(os.getenv("OPT_SHARPE_FLOOR", "2.0"))
    gated = df[
        (df["excess_ann_return"] > 0)
        & (df["sharpe"] >= sharpe_floor)
        & (df["max_drawdown"] > -0.20)
    ].copy()
    if gated.empty:
        gated = df.copy()
    top = gated.sort_values(["score", "excess_ann_return"], ascending=False).head(max(1, int(top_n))).reset_index(drop=True)
    rows: list[dict] = []
    for _, r in top.iterrows():
        cfg = _apply_row_to_cfg(base_cfg, r)
        wf_excess: list[float] = []
        wf_sharpe: list[float] = []
        for s, e in _WF_WINDOWS:
            c = copy.deepcopy(cfg)
            c.data.start_date = s
            c.data.end_date = e
            res = run_backtest(c, output_prefix="wf_opt_tmp", write_outputs=False)
            m = res.metrics
            steps = max(1, len(res.equity_curve) - 1)
            wf_excess.append(_ann_to_period(float(m.get("ann_return", 0.0)), steps) - _ann_to_period(float(m.get("bench_ann_return", 0.0)), steps))
            wf_sharpe.append(float(m.get("sharpe", 0.0)))
        beat = float(pd.Series(wf_excess).gt(0).mean()) if wf_excess else 0.0
        avg_ex = float(pd.Series(wf_excess).mean()) if wf_excess else -1.0
        min_ex = float(pd.Series(wf_excess).min()) if wf_excess else -1.0
        avg_sh = float(pd.Series(wf_sharpe).mean()) if wf_sharpe else 0.0
        out = r.to_dict()
        out.update(
            {
                "wf_beat_rate": beat,
                "wf_avg_excess": avg_ex,
                "wf_min_excess": min_ex,
                "wf_avg_sharpe": avg_sh,
                "wf_score": float(r["score"]) + 10.0 * beat + 90.0 * avg_ex + 55.0 * min_ex + avg_sh,
            }
        )
        rows.append(out)
    return pd.DataFrame(rows).sort_values(["wf_score", "score"], ascending=False).reset_index(drop=True)


def run_optimize(
    config_path: str | None = None,
    workers: int | None = None,
    run_vectorbt_prefetch: bool = True,
) -> pd.DataFrame:
    base = load_config(config_path=config_path)
    quick = os.getenv("OPT_QUICK", "0") == "1"
    # Generate fresh vectorbt hints before process-based fine evaluation.
    if run_vectorbt_prefetch:
        try:
            run_vectorbt_optimize(config_path=config_path)
        except Exception as e:
            print(f"[optimize] vectorbt pre-run skipped: {e}")
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
    wk = _resolve_workers(workers, int(getattr(base.daemon, "preopen_scan_threads", 4)))
    payloads = [(c, base) for c in filtered]
    chunksize = max(1, len(payloads) // max(1, wk * 8))
    print(f"[optimize] workers={wk}, combos={len(combos)}, after_prefilter={len(filtered)}")
    if len(payloads) <= 2:
        # Avoid process-pool startup overhead for tiny test grids.
        rows = [_run_one(p) for p in payloads]
    else:
        with ProcessPoolExecutor(max_workers=wk) as ex:
            rows = list(ex.map(_run_one, payloads, chunksize=chunksize))
    df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    Path("output").mkdir(exist_ok=True)
    df.to_csv("output/optimization_results.csv", index=False)
    return df
