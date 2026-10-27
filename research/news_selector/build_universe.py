"""Build a point-in-time liquid universe for the news selector.

The first version selected the 800 most liquid stocks using the last rows of the data (October 2026) and then
backtested from May 2026. That is look-ahead: a stock that became liquid only later was already in the universe.
This script uses only rows up to `--asof` (default 2026-03-01), so the universe is knowable before the test.

usage:
  python research/news_selector/build_universe.py --asof 2026-03-01 --limit 800
Writes data/market/selector_universe_pit.csv (gitignored).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
KLINE = ROOT / "data" / "market" / "kline"
if not KLINE.exists():
    KLINE = ROOT / "output" / "strong_dragon" / "kline"
OUT = ROOT / "data" / "market" / "selector_universe_pit.csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2026-03-01")
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    rows = []
    for path in sorted(KLINE.glob("*.csv")):
        try:
            df = pd.read_csv(path, usecols=["date", "close", "amount", "isST"])
        except Exception:
            continue
        df = df[df["date"] <= args.asof]
        if len(df) < 25:
            continue
        tail = df.tail(40)
        close = pd.to_numeric(tail["close"], errors="coerce").iloc[-1]
        amount20 = pd.to_numeric(tail["amount"], errors="coerce").tail(20).mean()
        is_st = int(pd.to_numeric(tail["isST"], errors="coerce").fillna(0).iloc[-1])
        if close > 1 and amount20 >= 5e7 and not is_st:
            rows.append({"code": path.stem, "close": round(float(close), 3),
                         "amount20": round(float(amount20), 0)})
    rows.sort(key=lambda x: -x["amount20"])
    rows = rows[:args.limit]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "close", "amount20"])
        w.writeheader()
        w.writerows(rows)
    print(f"[universe] {len(rows)} stocks as of {args.asof} -> {out}")


if __name__ == "__main__":
    main()
