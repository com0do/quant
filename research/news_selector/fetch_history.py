"""Backfill announcements and CCTV policy news over a long window with a thread pool.

The recent-data fetcher (`fetch_data.py`) is sequential, which is fine for a few months. A 2019-2026 event study
needs ~1,700 announcement days and ~2,800 CCTV days; this script fetches days concurrently and writes through
the same `NewsStore` (writes happen in the main thread, so SQLite is not shared across threads).

usage:
  uv run --with akshare python research/news_selector/fetch_history.py --start 2019-01-01 --end 2026-10-26 --workers 8
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "news_selector"))

from fetch_data import _iso_date, calendar_days, trading_days  # noqa: E402
from quant.stock_data.news_store import NewsStore  # noqa: E402


def ann_day(day) -> list[dict]:
    import akshare as ak

    try:
        df = ak.stock_notice_report(symbol="全部", date=day.strftime("%Y%m%d"))
    except Exception:
        return []
    rows = []
    for r in df.to_dict("records"):
        code = str(r.get("代码", "")).strip().zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        rows.append({
            "date": str(r.get("公告日期") or day.isoformat())[:10],
            "code": code,
            "name": str(r.get("名称") or ""),
            "title": str(r.get("公告标题") or ""),
            "type": str(r.get("公告类型") or ""),
            "url": str(r.get("网址") or ""),
            "source": "eastmoney",
        })
    return rows


def cctv_day(day) -> list[dict]:
    import akshare as ak

    try:
        df = ak.news_cctv(date=day.strftime("%Y%m%d"))
    except Exception:
        return []
    return [{
        "date": _iso_date(r.get("date"), day),
        "title": str(r.get("title") or ""),
        "content": str(r.get("content") or ""),
        "source": "cctv",
    } for r in df.to_dict("records")]


def run(days, fn, store: NewsStore, kind: str, workers: int) -> None:
    total = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn, d): d for d in days}
        for i, fut in enumerate(as_completed(futures), 1):
            rows = fut.result()
            total += (store.upsert_announcements(rows) if kind == "ann" else store.upsert_policy_news(rows))
            if i % 100 == 0 or i == len(days):
                print(f"[{kind}] {i}/{len(days)} days, {total} rows", flush=True)
    print(f"[{kind}] done {total} rows", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2026-10-26")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sources", default="ann,cctv")
    ap.add_argument("--db", default=str(ROOT / "data" / "news" / "news.db"))
    args = ap.parse_args()
    store = NewsStore(args.db)
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}
    if "ann" in sources:
        run(list(trading_days(args.start, args.end)), ann_day, store, "ann", args.workers)
    if "cctv" in sources:
        run(list(calendar_days(args.start, args.end)), cctv_day, store, "cctv", args.workers)
    print("[done]", store.counts())


if __name__ == "__main__":
    main()
