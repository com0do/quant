#!/usr/bin/env python3
from __future__ import annotations

import copy
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.backtest.engine import run_backtest
from quant.config import load_config


ETF_PROXY_POOL = ["000300.XSHG", "000905.XSHG", "000852.XSHG", "399303.XSHE"]
JQ_HORSE_ORDER = ["smallcap", "etf_rebound", "etf_rotation", "whitehorse_defense"]
JQ_HORSE_WEIGHTS = [0.5, 0.0, 0.5, 0.0]


def _calc_metrics(eq: pd.Series, bench: pd.Series | None = None) -> dict:
    ret = eq.pct_change(fill_method=None).fillna(0.0)
    n = max(len(eq) - 1, 1)
    ann = (float(eq.iloc[-1]) / float(eq.iloc[0])) ** (252 / n) - 1.0
    vol = float(ret.std(ddof=0)) * math.sqrt(252)
    sharpe = ann / vol if vol > 0 else 0.0
    mdd = float((eq / eq.cummax() - 1.0).min())
    out = {
        "ann_return": float(ann),
        "ann_volatility": float(vol),
        "sharpe": float(sharpe),
        "max_drawdown": mdd,
        "final_equity": float(eq.iloc[-1]),
    }
    if bench is not None:
        b_ret = bench.pct_change(fill_method=None).fillna(0.0)
        b_ann = (float(bench.iloc[-1]) / float(bench.iloc[0])) ** (252 / n) - 1.0
        te = float((ret - b_ret).std(ddof=0)) * math.sqrt(252)
        ir = (ann - b_ann) / te if te > 0 else 0.0
        out["bench_ann_return"] = float(b_ann)
        out["excess_ann_return"] = float(ann - b_ann)
        out["tracking_error"] = te
        out["information_ratio"] = float(ir)
    return out


def _weighted_momentum_score(prices: np.ndarray) -> float:
    if len(prices) < 5:
        return 0.0
    log_prices = np.log(prices)
    x = np.arange(len(log_prices))
    w = np.linspace(1.0, 2.0, len(log_prices))
    slope, intercept = np.polyfit(x, log_prices, 1, w=w)
    annualized_return = math.exp(slope * 250) - 1
    ss_res = np.sum(w * (log_prices - (slope * x + intercept)) ** 2)
    ss_tot = np.sum(w * (log_prices - np.mean(log_prices)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    score = annualized_return * r2
    # mimic jq_horse extra short-term fallback filter
    if min(prices[-1] / prices[-2], prices[-2] / prices[-3], prices[-3] / prices[-4]) < 0.97:
        return 0.0
    return float(score)


def _simulate_etf_rotation(
    db_path: str, start_date: str, end_date: str, initial_cash: float = 100000.0, lookback_days: int = 25
) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    sql = (
        "select date, code, close from index_prices_daily "
        "where date between ? and ? and code in ({}) order by date, code"
    ).format(",".join(["?"] * len(ETF_PROXY_POOL)))
    df = pd.read_sql_query(sql, conn, params=[start_date, end_date, *ETF_PROXY_POOL])
    conn.close()
    if df.empty:
        raise RuntimeError("no index_prices_daily data for ETF proxy pool")
    px = (
        df.pivot(index="date", columns="code", values="close")
        .sort_index()
        .ffill()
        .dropna(how="all")
    )
    dates = list(px.index)
    current = None
    eq = initial_cash
    rows = [{"date": dates[0], "equity": eq, "ret": 0.0, "selected_proxy": None}]

    for i in range(1, len(dates)):
        if i >= lookback_days:
            scores: dict[str, float] = {}
            for code in px.columns:
                window = px[code].iloc[i - lookback_days : i + 1].dropna().values
                if len(window) < lookback_days:
                    continue
                # mimic jq_horse score range filter: 0 < score < m_score(5)
                score = _weighted_momentum_score(window)
                if 0 < score < 5:
                    scores[str(code)] = score
            current = max(scores, key=scores.get) if scores else None

        day_ret = 0.0
        if current is not None:
            prev_close = float(px[current].iloc[i - 1])
            cur_close = float(px[current].iloc[i])
            day_ret = (cur_close / prev_close) - 1.0 if prev_close > 0 else 0.0
        eq *= 1.0 + day_ret
        rows.append({"date": dates[i], "equity": eq, "ret": day_ret, "selected_proxy": current})

    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"])
    return out


def main() -> None:
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)

    # Leg A: local small-cap strategy (proxy jq_horse strategy-1)
    cfg = copy.deepcopy(load_config("config/csi1000_refit_2025.toml"))
    cfg.data.benchmark_index = "000852.XSHG"
    cfg.data.cross_index_hold_enable = False
    cfg.data.cross_index_candidates = []
    small = run_backtest(cfg, output_prefix="jq_horse_smallcap_leg", write_outputs=True)
    small_eq = pd.read_csv(out_dir / "jq_horse_smallcap_leg_equity_curve.csv")
    small_eq["date"] = pd.to_datetime(small_eq["date"])

    # Leg B: ETF rotation momentum (proxy jq_horse strategy-3)
    etf_eq = _simulate_etf_rotation(
        db_path=cfg.data.runtime_csi300_500_db_path,
        start_date=cfg.data.start_date,
        end_date=cfg.data.end_date,
        initial_cash=100000.0,
        lookback_days=25,
    )

    # Combine in jq_horse order:
    # [小市值, ETF反弹, ETF轮动, 白马攻防] = [0.5, 0, 0.5, 0]
    mix = (
        small_eq[["date", "ret", "benchmark_equity"]]
        .rename(columns={"ret": "smallcap_ret"})
        .merge(etf_eq[["date", "ret", "selected_proxy"]].rename(columns={"ret": "etf_rot_ret"}), on="date", how="inner")
        .sort_values("date")
    )
    mix["combined_ret"] = (
        JQ_HORSE_WEIGHTS[0] * mix["smallcap_ret"]
        + JQ_HORSE_WEIGHTS[2] * mix["etf_rot_ret"]
    )
    initial_cash = float(cfg.execution.initial_cash)
    mix["combined_equity"] = initial_cash * (1.0 + mix["combined_ret"]).cumprod()

    bench = small_eq.set_index("date").reindex(mix["date"])["benchmark_equity"].ffill().bfill()
    m_mix = _calc_metrics(mix["combined_equity"], bench=bench.reset_index(drop=True))
    m_small = _calc_metrics(small_eq["equity"], bench=small_eq["benchmark_equity"])
    m_etf = _calc_metrics(etf_eq["equity"])
    summary = pd.DataFrame(
        [
            {"leg": "smallcap_leg", **m_small},
            {"leg": "etf_rotation_leg", **m_etf},
            {
                "leg": "combined_0.5_0_0.5_0",
                "weights_order": ",".join(JQ_HORSE_ORDER),
                "weights": ",".join(map(str, JQ_HORSE_WEIGHTS)),
                **m_mix,
            },
        ]
    )
    mix_out = mix[["date", "smallcap_ret", "etf_rot_ret", "combined_ret", "combined_equity", "selected_proxy"]]
    mix_out.to_csv(out_dir / "jq_horse_mimic_combo_equity_curve.csv", index=False)
    summary.to_csv(out_dir / "jq_horse_mimic_summary.csv", index=False)
    print(summary.to_string(index=False))
    print("[OK] wrote output/jq_horse_mimic_summary.csv and output/jq_horse_mimic_combo_equity_curve.csv")


if __name__ == "__main__":
    main()
