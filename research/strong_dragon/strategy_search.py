"""Search for a tradable use of the strong-dragon chart state, with point-in-time entries and yearly checks.

The earlier work showed that buying the first RED bar at the next open is negative. This script treats RED as a
**state** (main up-wave in progress) and tests entries that are structurally different:

  fresh        first RED bar (the baseline)
  pull5        first pullback to MA5 inside a RED run, close still above MA10/MA20
  pull10       first pullback to MA10 inside a RED run, close still above MA20
  breakout     new 60-day high after a tight 60-day range, volume expansion, MA alignment
  first_limit  first limit-up with a 3-day gain >= 10% and MA alignment
  red_break    RED state + new 20-day high + volume expansion

For every entry, forward returns are measured from the next open to the close 5/10/20 trading days later, with a
limit-up open skipped. Results are split by year (2024/2025/2026) and by the CSI 1000 regime (index above MA20).
The universe is every Shanghai/Shenzhen stock in the local cache; the only entry filter is that day's 20-day
turnover, so there is no "today's liquidity" universe look-ahead.

usage:
  uv run python research/strong_dragon/strategy_search.py --start 2024-01-01 --end 2026-10-26
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "strong_dragon"))

OUT = ROOT / "output" / "strong_dragon" / "strategy_search"
KLINE = ROOT / "data" / "market" / "kline"
if not KLINE.exists():
    KLINE = ROOT / "output" / "strong_dragon" / "kline"
INDEX = ROOT / "data" / "market" / "kline_index" / "000852.XSHG.csv"
if not INDEX.exists():
    INDEX = ROOT / "output" / "strong_dragon" / "kline_index" / "000852.XSHG.csv"
ENTRIES = ("fresh", "pull5", "pull10", "breakout", "first_limit", "red_break")
HORIZONS = ("fwd5", "fwd10", "fwd20")


def limit_series(g: pd.DataFrame, code: str) -> pd.Series:
    num = code.partition(".")[0]
    if num.startswith(("300", "301")):
        base = pd.Series(0.20, index=g.index).where(g["date"] >= pd.Timestamp("2020-08-24"), 0.10)
    elif num.startswith(("688", "689")):
        base = pd.Series(0.20, index=g.index)
    elif num.startswith(("8", "4", "920")):
        base = pd.Series(0.30, index=g.index)
    else:
        base = pd.Series(0.10, index=g.index)
    return base.where(g["isST"].fillna(0).astype(int) == 0, 0.05)


def features(g: pd.DataFrame, code: str) -> pd.DataFrame:
    g = g.sort_values("date").reset_index(drop=True).copy()
    c, h, l = g["close"], g["high"], g["low"]
    for n in (5, 10, 20, 60):
        g[f"ma{n}"] = c.rolling(n).mean()
    g["ret3"] = c / c.shift(3) - 1
    g["ret5"] = c / c.shift(5) - 1
    g["ret20"] = c / c.shift(20) - 1
    g["vol_ratio20"] = g["volume"] / g["volume"].rolling(20).mean()
    g["amount20"] = g["amount"].rolling(20).mean()
    g["lim"] = limit_series(g, code)
    g["limit_up"] = (c / g["preclose"] - 1) >= (g["lim"] - 0.005)
    g["red"] = (g["ma5"] > g["ma10"] * (1 + 1e-9)) & (g["ma10"] > g["ma20"] * (1 + 1e-9)) & (g["ret3"] >= 0.10 - 1e-9)
    g["green"] = (g["red"].astype(float).shift(1).rolling(15, min_periods=1).max().fillna(0) > 0) & ~g["red"] & (c < g["ma5"])
    g["fresh_red"] = g["red"] & ~g["red"].shift(1, fill_value=False).astype(bool)
    g["fresh"] = g["fresh_red"]
    run_id = (~g["red"]).cumsum()
    g["red_run"] = g["red"].astype(int).groupby(run_id).cumsum()
    g["high20"] = h.rolling(20).max()
    g["high60"] = h.rolling(60).max()
    g["low60"] = l.rolling(60).min()
    g["range60"] = g["high60"] / g["low60"] - 1
    pull5 = g["red"] & (l <= g["ma5"]) & (c >= g["ma10"]) & (c > g["ma20"]) & (g["red_run"] >= 3)
    pull10 = g["red"] & (l <= g["ma10"]) & (c > g["ma20"]) & (g["red_run"] >= 3)
    g["pull5"] = pull5 & (pull5.groupby(run_id).cumsum() == 1)
    g["pull10"] = pull10 & (pull10.groupby(run_id).cumsum() == 1)
    g["breakout"] = ((c >= g["high60"].shift(1)) & (g["range60"] <= 0.35)
                     & (g["vol_ratio20"] >= 1.5) & (g["ma5"] > g["ma10"]) & (g["ma10"] > g["ma20"]))
    g["first_limit"] = (g["limit_up"] & ~g["limit_up"].shift(1, fill_value=False).astype(bool)
                        & (g["ret3"] >= 0.10 - 1e-9) & (g["ma5"] > g["ma10"]) & (g["ma10"] > g["ma20"]))
    g["red_break"] = g["red"] & (c >= g["high20"].shift(1)) & (g["vol_ratio20"] >= 1.5)
    entry_open = g["open"].shift(-1)
    lim_next = g["lim"].shift(-1)
    pre_next = g["preclose"].shift(-1)
    g["entry_open"] = entry_open
    g["entry_ok"] = entry_open.notna() & (entry_open < pre_next * (1 + lim_next) * 0.995)
    for n in (5, 10, 20):
        g[f"fwd{n}"] = c.shift(-n) / entry_open - 1
    return g


def collect(start: str, end: str, codes: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    dates = []
    if codes:
        files = [KLINE / f"{c}.csv" for c in codes if (KLINE / f"{c}.csv").exists()]
    else:
        files = sorted(KLINE.glob("*.csv"))
    for i, path in enumerate(files, 1):
        code = path.stem
        try:
            df = pd.read_csv(path, parse_dates=["date"])
        except Exception:
            continue
        if len(df) < 80:
            continue
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        if len(df) < 80:
            continue
        g = features(df, code)
        dates.append(pd.DataFrame({"date": g["date"], "above": (g["close"] > g["ma20"]).astype("int8")}))
        for entry in ENTRIES:
            s = g[g[entry] & g["entry_ok"] & (g["amount20"] >= 5e7)].copy()
            if s.empty:
                continue
            s["entry"] = entry
            s["code"] = code
            rows.append(s[["date", "code", "entry", "fwd5", "fwd10", "fwd20"]])
        if i % 500 == 0:
            print(f"[scan] {i}/{len(files)}", flush=True)
    events = pd.concat(rows, ignore_index=True).rename(columns={"date": "signal_date"})
    events["year"] = events["signal_date"].dt.year
    breadth = pd.concat(dates, ignore_index=True).groupby("date")["above"].mean().rename("breadth").reset_index()
    idx = pd.read_csv(INDEX, parse_dates=["date"]).set_index("date")["close"].sort_index()
    regime = pd.DataFrame({"date": idx.index, "mkt": idx.values})
    regime["mkt_ma20"] = regime["mkt"].rolling(20).mean()
    regime["mkt_ok"] = regime["mkt"] > regime["mkt_ma20"]
    regime = regime.merge(breadth, on="date", how="left")
    regime["breadth_ok"] = regime["breadth"] > 0.5
    events = events.merge(regime[["date", "mkt_ok", "breadth_ok"]], left_on="signal_date", right_on="date", how="left")
    print(f"[events] {len(events):,} signals, {events['code'].nunique():,} codes")
    return events, regime


def stats(df: pd.DataFrame, h: str) -> dict:
    x = df.dropna(subset=[h])
    if len(x) == 0:
        return {"n": 0}
    daily = x.groupby("signal_date")[h].mean()
    t = float(daily.mean() / (daily.std(ddof=1) / math.sqrt(len(daily)))) if len(daily) > 1 and daily.std() > 0 else None
    return {"n": int(len(x)), "mean": float(x[h].mean()), "median": float(x[h].median()),
            "win_rate": float((x[h] > 0).mean()), "daily_mean": float(daily.mean()), "t_stat": t}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-10-26")
    ap.add_argument("--universe", default=str(ROOT / "data" / "market" / "selector_universe_pit.csv"))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    codes = None
    if args.universe and Path(args.universe).exists():
        codes = pd.read_csv(args.universe, dtype={"code": str})["code"].tolist()
        print(f"[universe] {len(codes)} codes from {args.universe}")
    events, regime = collect(args.start, args.end, codes)
    summary = {"data": {"signals": int(len(events)), "codes": int(events["code"].nunique()),
                        "start": args.start, "end": args.end}, "entries": {}, "by_year": {}, "by_regime": {}}
    rows = []
    for entry in ENTRIES:
        e = events[events["entry"] == entry]
        summary["entries"][entry] = {h: stats(e, h) for h in HORIZONS}
        summary["by_year"][entry] = {str(int(y)): stats(g, "fwd5") for y, g in e.groupby("year")}
        for label, mask in (("mkt_ok", e["mkt_ok"]), ("breadth_ok", e["breadth_ok"])):
            summary["by_regime"].setdefault(entry, {})[label] = stats(e[mask.fillna(False)], "fwd5")
        for h in HORIZONS:
            rows.append({"entry": entry, "horizon": h, **summary["entries"][entry][h]})
    pd.DataFrame(rows).to_csv(OUT / "entry_event_groups.csv", index=False)
    (OUT / "entry_event_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n[event study] fwd5 by entry / year")
    for entry in ENTRIES:
        base = summary["entries"][entry]["fwd5"]
        years = summary["by_year"][entry]
        print(f"{entry:12s} all n={base.get('n',0):6d} mean={base.get('mean',0):+.4f} t={base.get('t_stat') or 0:+.2f} | "
              + " ".join(f"{y}:{v.get('mean',0):+.4f}(n{v.get('n',0)})" for y, v in years.items()))
    print("[done]", OUT / "entry_event_summary.json")


if __name__ == "__main__":
    main()
