from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path
import itertools
import json
import os

import pandas as pd

from quant.config import load_config
from scripts.backtest_csi1000_baseline import Params, run_backtest


@dataclass(frozen=True)
class IterativeCandidate:
    stage: str
    strategy_name: str
    top_k: int
    entry_interval_days: int
    stop_loss_pct: float
    trailing_stop_pct: float
    strategy_names: tuple[str, ...]
    strategy_weights: tuple[float, ...]


def _score(metrics: dict) -> float:
    # Hard constraints (requested in previous iterations):
    # 1) excess_ann_return > 0
    # 2) max_drawdown >= -0.20
    if float(metrics.get("excess_ann_return", -1.0)) <= 0.0:
        return -1e9
    if float(metrics.get("max_drawdown", -1.0)) < -0.20:
        return -1e8
    ann = float(metrics.get("ann_return", 0.0))
    sharpe = float(metrics.get("sharpe", 0.0))
    info = float(metrics.get("information_ratio", 0.0))
    return ann * 8.0 + sharpe * 1.5 + info


def _run_one(payload: dict) -> dict:
    c = IterativeCandidate(**payload)
    p = Params(
        start_date="2025-01-01",
        end_date="2025-12-23",
        initial_cash=1_000_000.0,
        top_k=c.top_k,
        max_positions=min(c.top_k, 20),
        entry_interval_days=c.entry_interval_days,
        stop_loss_pct=c.stop_loss_pct,
        trailing_stop_pct=c.trailing_stop_pct,
        buy_fee=0.0003,
        sell_fee=0.0013,
        benchmark_code="000852.XSHG",
    )
    metrics = run_backtest(
        db_path="data/market_cache_csi1000_1y.db",
        params=p,
        write_outputs=False,
    )
    row = asdict(c)
    row.update(metrics)
    row["score"] = _score(metrics)
    return row


def _vectorbt_prefilter(candidates: list[IterativeCandidate], stage: str, keep_ratio: float = 0.35) -> list[IterativeCandidate]:
    """
    Prefilter candidates using vectorbt optimization file.

    stage1 + stage2 both go through this function (user-requested behavior).
    """
    path = Path("output/vectorbt_optimization.csv")
    if not path.exists() or not candidates:
        return candidates
    try:
        df = pd.read_csv(path)
    except Exception:
        return candidates
    if df.empty or "top_k" not in df.columns or "rebalance_days" not in df.columns:
        return candidates

    # Keep high-score vectorbt rows as hints.
    df = df.sort_values("score", ascending=False).head(max(20, int(len(df) * 0.15)))
    hint_pairs = {(int(r.top_k), int(r.rebalance_days)) for r in df.itertuples(index=False)}
    hint_topk = {x[0] for x in hint_pairs}
    hint_days = {x[1] for x in hint_pairs}

    scored: list[tuple[float, IterativeCandidate]] = []
    for c in candidates:
        s = 0.0
        if (c.top_k, c.entry_interval_days) in hint_pairs:
            s += 2.0
        if c.top_k in hint_topk:
            s += 1.0
        if c.entry_interval_days in hint_days:
            s += 1.0
        # Mild regularization toward conservative risk knobs.
        if c.stop_loss_pct <= 0.03:
            s += 0.2
        if c.trailing_stop_pct <= 0.08:
            s += 0.2
        scored.append((s, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    k = max(8, int(len(scored) * keep_ratio))
    keep = [c for _, c in scored[:k]]
    print(f"[iterative] {stage} vectorbt prefilter: {len(candidates)} -> {len(keep)}")
    return keep


def _build_stage1_candidates() -> list[IterativeCandidate]:
    items: list[IterativeCandidate] = []
    strategy_defs = [
        ("price_only_cross_section", ("price_only_cross_section",), (1.0,)),
        ("csi1000_enhanced", ("csi1000_enhanced",), (1.0,)),
        ("value_activity_dow", ("value_activity_dow",), (1.0,)),
    ]
    # Coarse search in stage1.
    quick = os.getenv("ITERATIVE_QUICK", "0") == "1"
    grid = list(
        itertools.product(
            [12, 15] if quick else [10, 12, 15, 20],   # top_k
            [5, 6] if quick else [5, 6, 8, 10],        # entry days
            [0.02, 0.03] if quick else [0.02, 0.03, 0.04],  # stop loss
            [0.06, 0.08] if quick else [0.06, 0.08, 0.10],  # trailing stop
        )
    )
    for sname, snames, sweights in strategy_defs:
        for top_k, entry_days, stop, trail in grid:
            items.append(
                IterativeCandidate(
                    stage="stage1",
                    strategy_name=sname,
                    top_k=top_k,
                    entry_interval_days=entry_days,
                    stop_loss_pct=stop,
                    trailing_stop_pct=trail,
                    strategy_names=snames,
                    strategy_weights=sweights,
                )
            )
    return items


def _build_stage2_candidates(stage1_df: pd.DataFrame) -> list[IterativeCandidate]:
    if stage1_df.empty:
        return []
    # Survivors by strategy.
    survivors: list[IterativeCandidate] = []
    for sname, g in stage1_df.groupby("strategy_name"):
        g = g.sort_values("score", ascending=False).head(3)
        for r in g.itertuples(index=False):
            survivors.append(
                IterativeCandidate(
                    stage="stage2_seed",
                    strategy_name=sname,
                    top_k=int(r.top_k),
                    entry_interval_days=int(r.entry_interval_days),
                    stop_loss_pct=float(r.stop_loss_pct),
                    trailing_stop_pct=float(r.trailing_stop_pct),
                    strategy_names=(sname,),
                    strategy_weights=(1.0,),
                )
            )
    if not survivors:
        return []

    # Build pairwise combinations from strategy survivors.
    by_name: dict[str, list[IterativeCandidate]] = {}
    for c in survivors:
        by_name.setdefault(c.strategy_name, []).append(c)
    names = sorted(by_name.keys())
    if len(names) < 2:
        return survivors

    out: list[IterativeCandidate] = []
    for a_name, b_name in itertools.combinations(names, 2):
        for a in by_name[a_name]:
            for b in by_name[b_name]:
                # Blend knobs from pair endpoints.
                top_k = int(round((a.top_k + b.top_k) / 2))
                entry = int(round((a.entry_interval_days + b.entry_interval_days) / 2))
                stop = round((a.stop_loss_pct + b.stop_loss_pct) / 2, 4)
                trail = round((a.trailing_stop_pct + b.trailing_stop_pct) / 2, 4)
                for w1, w2 in [(0.7, 0.3), (0.6, 0.4), (0.5, 0.5)]:
                    out.append(
                        IterativeCandidate(
                            stage="stage2",
                            strategy_name=f"{a_name}+{b_name}",
                            top_k=top_k,
                            entry_interval_days=entry,
                            stop_loss_pct=stop,
                            trailing_stop_pct=trail,
                            strategy_names=(a_name, b_name),
                            strategy_weights=(w1, w2),
                        )
                    )
    return out


def _run_candidates(cands: list[IterativeCandidate], workers: int) -> pd.DataFrame:
    if not cands:
        return pd.DataFrame()
    payloads = [asdict(c) for c in cands]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_run_one, payloads, chunksize=1))
    return pd.DataFrame(rows)


def run_iterative_optimize(config_path: str | None = None) -> dict:
    cfg = load_config(config_path=config_path)
    workers = max(1, int(getattr(cfg.daemon, "preopen_scan_threads", os.cpu_count() or 4)))

    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    print(f"[iterative] workers={workers}")

    stage1 = _build_stage1_candidates()
    stage1_pref = _vectorbt_prefilter(stage1, stage="stage1", keep_ratio=0.30)
    stage1_df = _run_candidates(stage1_pref, workers=workers)
    if not stage1_df.empty:
        stage1_df = stage1_df.sort_values("score", ascending=False).reset_index(drop=True)
    stage1_df.to_csv("output/iterative_stage1_strategy_screen.csv", index=False)

    stage2 = _build_stage2_candidates(stage1_df)
    stage2_pref = _vectorbt_prefilter(stage2, stage="stage2", keep_ratio=0.35)
    stage2_df = _run_candidates(stage2_pref, workers=workers)
    if not stage2_df.empty:
        stage2_df = stage2_df.sort_values("score", ascending=False).reset_index(drop=True)
    stage2_df.to_csv("output/iterative_stage2_combo_search.csv", index=False)

    if stage2_df.empty and stage1_df.empty:
        result = {"status": "no_results", "workers": workers}
        Path("output/iterative_optimize_recovery_note.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result

    final_df = stage2_df if not stage2_df.empty else stage1_df
    best = final_df.iloc[0].to_dict()
    report = [
        "# Iterative Optimization Report",
        "",
        f"- Workers: {workers}",
        f"- Stage1 candidates: {len(stage1)}",
        f"- Stage1 after vectorbt prefilter: {len(stage1_pref)}",
        f"- Stage2 candidates: {len(stage2)}",
        f"- Stage2 after vectorbt prefilter: {len(stage2_pref)}",
        "",
        "## Best Result",
        f"- strategy_name: {best.get('strategy_name')}",
        f"- strategy_names: {best.get('strategy_names')}",
        f"- strategy_weights: {best.get('strategy_weights')}",
        f"- top_k: {best.get('top_k')}",
        f"- entry_interval_days: {best.get('entry_interval_days')}",
        f"- stop_loss_pct: {best.get('stop_loss_pct')}",
        f"- trailing_stop_pct: {best.get('trailing_stop_pct')}",
        f"- ann_return: {best.get('ann_return'):.4f}",
        f"- bench_ann_return: {best.get('bench_ann_return'):.4f}",
        f"- excess_ann_return: {best.get('excess_ann_return'):.4f}",
        f"- sharpe: {best.get('sharpe'):.4f}",
        f"- information_ratio: {best.get('information_ratio'):.4f}",
        f"- max_drawdown: {best.get('max_drawdown'):.4f}",
        f"- score: {best.get('score'):.4f}",
    ]
    Path("output/iterative_optimization_report.md").write_text("\n".join(report), encoding="utf-8")

    result = {
        "status": "ok",
        "workers": workers,
        "stage1_total": len(stage1),
        "stage1_prefiltered": len(stage1_pref),
        "stage2_total": len(stage2),
        "stage2_prefiltered": len(stage2_pref),
        "best": best,
    }
    Path("output/iterative_optimize_recovery_note.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
