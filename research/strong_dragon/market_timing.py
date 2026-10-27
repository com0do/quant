"""Use the strong-dragon chart state as a market-wide sentiment/timing gauge.

The stock-level tests found no stable entry. This script aggregates the same chart state across a liquid universe:
the share of RED bars, GREEN bars, fresh RED signals, limit-ups and the share above MA20. It then asks whether those
breadth measures predict the next 5/10/20-day index return and whether a simple long/cash rule beats buy-and-hold.

Signals are computed at the close of day t from the chart data of day t; the position is applied to the next day's
index return (close-to-close). A 5 bp switching cost is charged on every change.

usage:
  uv run python research/strong_dragon/market_timing.py --start 2024-01-01 --end 2026-10-26
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output" / "strong_dragon" / "market_timing"
KLINE = ROOT / "data" / "market" / "kline"
if not KLINE.exists():
    KLINE = ROOT / "output" / "strong_dragon" / "kline"
INDEX = ROOT / "data" / "market" / "kline_index" / "000852.XSHG.csv"
if not INDEX.exists():
    INDEX = ROOT / "output" / "strong_dragon" / "kline_index" / "000852.XSHG.csv"
UNIVERSE = ROOT / "data" / "market" / "selector_universe_pit.csv"


def load_breadth(codes: list[str], start: str, end: str) -> pd.DataFrame:
    rows = []
    for i, code in enumerate(codes, 1):
        path = KLINE / f"{code}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, parse_dates=["date"])
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        if len(df) < 30:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        c = df["close"]
        ma5, ma10, ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
        ret3 = c / c.shift(3) - 1
        red = (ma5 > ma10) & (ma10 > ma20) & (ret3 >= 0.10)
        green = (red.astype(float).shift(1).rolling(15, min_periods=1).max().fillna(0) > 0) & ~red & (c < ma5)
        fresh = red & ~red.shift(1, fill_value=False).astype(bool)
        lim = np.where(df["isST"].fillna(0).astype(int) == 1, 0.05,
                       np.where(df["code"].astype(str).str[:3].isin(["300", "301", "688", "689"]), 0.20, 0.10))
        limit_up = (c / df["preclose"] - 1) >= (lim - 0.005)
        rows.append(pd.DataFrame({
            "date": df["date"], "red": red.astype(float), "green": green.astype(float),
            "fresh": fresh.astype(float), "limit_up": limit_up.astype(float),
            "above": (c > ma20).astype(float), "ret": (c / df["preclose"] - 1).clip(-0.5, 0.5),
        }))
        if i % 200 == 0:
            print(f"[breadth] {i}/{len(codes)}", flush=True)
    panel = pd.concat(rows, ignore_index=True)
    out = panel.groupby("date").agg(red_share=("red", "mean"), green_share=("green", "mean"),
                                    fresh_count=("fresh", "sum"), limit_up_count=("limit_up", "sum"),
                                    above_share=("above", "mean"), equal_ret=("ret", "mean")).reset_index()
    out["net_red"] = out["red_share"] - out["green_share"]
    return out.sort_values("date")


def perf(ret: pd.Series, exposure: pd.Series) -> dict:
    eq = (1 + ret).cumprod()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    return {"total_return": float(eq.iat[-1] - 1),
            "cagr": float(eq.iat[-1] ** (1 / years) - 1),
            "max_drawdown": float((eq / eq.cummax() - 1).min()),
            "sharpe": float(ret.mean() / ret.std() * math.sqrt(244)) if ret.std() > 0 else None,
            "exposure": float(exposure.mean()), "days": int(len(ret)),
            "win_rate": float((ret > 0).mean())}


def rule_ret(market: pd.DataFrame, signal: pd.Series, cost: float = 0.0005) -> tuple[pd.Series, pd.Series]:
    pos = signal.astype(float)
    switch = pos.diff().abs().fillna(0)
    ret = pos * market["ret_next"] - switch * cost
    return ret.dropna(), pos.loc[ret.dropna().index]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-10-26")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    codes = pd.read_csv(UNIVERSE, dtype={"code": str})["code"].tolist() if UNIVERSE.exists() else sorted(p.stem for p in KLINE.glob("*.csv"))
    print(f"[universe] {len(codes)}")
    breadth = load_breadth(codes, args.start, args.end)
    idx = pd.read_csv(INDEX, parse_dates=["date"]).set_index("date")["close"].sort_index()
    idx = idx[(idx.index >= args.start) & (idx.index <= args.end)]
    market = breadth.set_index("date").join(idx.rename("csi1000"), how="inner")
    market["ret_next"] = market["csi1000"].pct_change().shift(-1)
    market["eq_next"] = market["equal_ret"].shift(-1)
    market["fwd5"] = market["csi1000"].shift(-5) / market["csi1000"] - 1
    market["fwd20"] = market["csi1000"].shift(-20) / market["csi1000"] - 1
    market = market.dropna(subset=["ret_next"])
    print(f"[market] {len(market)} days, {market.index.min().date()}..{market.index.max().date()}")

    summary = {"data": {"days": int(len(market)), "start": str(market.index.min().date()),
                        "end": str(market.index.max().date())}, "quantiles": {}, "rules": {}, "by_year": {}}
    for col in ("red_share", "green_share", "net_red", "fresh_count", "above_share"):
        q = pd.qcut(market[col], 5, labels=False, duplicates="drop")
        summary["quantiles"][col] = {}
        for k, g in market.groupby(q):
            summary["quantiles"][col][str(int(k))] = {
                "n": int(len(g)), "fwd5": float(g["fwd5"].mean()), "fwd20": float(g["fwd20"].mean()),
            }

    rules = {
        "buy_hold": pd.Series(True, index=market.index),
        "red_share>0.05": market["red_share"] > 0.05,
        "red_share>0.10": market["red_share"] > 0.10,
        "red_share>0.15": market["red_share"] > 0.15,
        "red_share>0.20": market["red_share"] > 0.20,
        "red_share>0.25": market["red_share"] > 0.25,
        "red_share>0.30": market["red_share"] > 0.30,
        "net_red>0": market["net_red"] > 0,
        "above_share>0.40": market["above_share"] > 0.40,
        "above_share>0.45": market["above_share"] > 0.45,
        "above_share>0.50": market["above_share"] > 0.50,
        "above_share>0.55": market["above_share"] > 0.55,
        "above_share>0.60": market["above_share"] > 0.60,
        "red>0.05 & above>0.50": (market["red_share"] > 0.05) & (market["above_share"] > 0.50),
        "red>0.10 & above>0.50": (market["red_share"] > 0.10) & (market["above_share"] > 0.50),
        "net_red>0 & above>0.50": (market["net_red"] > 0) & (market["above_share"] > 0.50),
        "fresh>10 & above>0.50": (market["fresh_count"] > 10) & (market["above_share"] > 0.50),
        "red>0.20 & above>0.50": (market["red_share"] > 0.20) & (market["above_share"] > 0.50),
        "red>0.20 & net_red>0": (market["red_share"] > 0.20) & (market["net_red"] > 0),
    }
    for name, sig in rules.items():
        ret, pos = rule_ret(market, sig)
        summary["rules"][name] = perf(ret, pos)
        summary["by_year"][name] = {}
        for y in sorted(ret.index.year.unique()):
            mask = ret.index.year == y
            summary["by_year"][name][str(int(y))] = perf(ret[mask], pos[mask])
        print(f"{name:28s} ret={summary['rules'][name]['total_return']:+.3f} mdd={summary['rules'][name]['max_drawdown']:+.3f} "
              f"sharpe={summary['rules'][name]['sharpe'] or 0:+.2f} exposure={summary['rules'][name]['exposure']:.2f}")
    (OUT / "timing_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[done]", OUT / "timing_summary.json")


if __name__ == "__main__":
    main()
