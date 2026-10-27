"""Fetch quarterly fundamentals for the liquid part of the A-share universe.

Source: akshare `stock_financial_analysis_indicator` (Sina), one request per stock, all report dates.
Point-in-time rule: a report is visible only after `report_date + 120 days` for annual reports and +90 days for
quarterly reports. That is conservative; the store keeps both dates so a later run can tighten it with the
exchange publication date.

The universe is built from the local daily bars: 20-day average turnover >= 50M CNY, close > 1, latest row fresh.
It is cached in `data/market/selector_universe.csv`.

usage:
  uv run --with akshare python research/news_selector/fetch_fundamentals.py --limit 1000
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.stock_data.news_store import NewsStore  # noqa: E402

DATA = ROOT / "data"
KLINE = ROOT / "data" / "market" / "kline"
if not KLINE.exists():
    KLINE = ROOT / "output" / "strong_dragon" / "kline"
UNIVERSE = DATA / "market" / "selector_universe_pit.csv"


def build_universe(limit: int, path: Path = UNIVERSE) -> list[str]:
    if not path.exists():
        raise SystemExit(f"universe not found: {path}; run research/news_selector/build_universe.py --asof 2026-03-01")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return [r["code"] for r in rows[:limit]]
    out = []
    for path in sorted(KLINE.glob("*.csv")):
        try:
            df = pd.read_csv(path, usecols=["date", "close", "amount", "isST"]).tail(40)
        except Exception:
            continue
        if len(df) < 25:
            continue
        close = pd.to_numeric(df["close"], errors="coerce").iloc[-1]
        amount20 = pd.to_numeric(df["amount"], errors="coerce").tail(20).mean()
        is_st = int(pd.to_numeric(df["isST"], errors="coerce").fillna(0).iloc[-1])
        if close > 1 and amount20 >= 5e7 and not is_st:
            out.append({"code": path.stem, "close": round(float(close), 3), "amount20": round(float(amount20), 0)})
    out.sort(key=lambda x: -x["amount20"])
    UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
    with UNIVERSE.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "close", "amount20"])
        w.writeheader()
        w.writerows(out)
    print(f"[universe] {len(out)} liquid stocks -> {UNIVERSE}")
    return [r["code"] for r in out[:limit]]


def _num(value):
    try:
        x = float(value)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def one(code: str, start_year: str) -> list[dict]:
    import akshare as ak

    num = code.partition(".")[0]
    try:
        df = ak.stock_financial_analysis_indicator(symbol=num, start_year=start_year)
    except Exception:
        return []
    rows = []
    for r in df.to_dict("records"):
        report = str(r.get("日期") or "")[:10]
        if len(report) < 10:
            continue
        try:
            d = date.fromisoformat(report)
        except ValueError:
            continue
        lag = 120 if d.month == 12 else 90
        rows.append({
            "code": code,
            "report_date": report,
            "pub_date": (d + timedelta(days=lag)).isoformat(),
            "roe": _num(r.get("净资产收益率(%)")),
            "revenue_yoy": _num(r.get("主营业务收入增长率(%)")),
            "profit_yoy": _num(r.get("净利润增长率(%)")),
            "gross_margin": _num(r.get("销售毛利率(%)")),
            "debt_ratio": _num(r.get("资产负债率(%)")),
            "eps": _num(r.get("摊薄每股收益(元)")),
            "bvps": _num(r.get("每股净资产_调整后(元)")),
            "source": "akshare/sina",
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--start-year", default="2024")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--db", default=str(DATA / "news" / "news.db"))
    ap.add_argument("--universe", default=str(UNIVERSE))
    args = ap.parse_args()
    store = NewsStore(args.db)
    codes = build_universe(args.limit, Path(args.universe))
    print(f"[fund] {len(codes)} stocks, workers={args.workers}")
    done = written = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, c, args.start_year): c for c in codes}
        for fut in as_completed(futs):
            rows = fut.result()
            written += store.upsert_fundamentals(rows)
            done += 1
            if done % 50 == 0:
                print(f"[fund] {done}/{len(codes)} stocks, {written} rows", flush=True)
            time.sleep(0.02)
    print("[done]", store.counts())


if __name__ == "__main__":
    main()
