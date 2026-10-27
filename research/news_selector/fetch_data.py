"""Fetch recent news, announcements and limit-up themes into data/news/news.db.

Sources (all free, no token):
  announcements  akshare stock_notice_report(symbol="全部", date=...) — one request per trading day, all A shares.
  cctv           akshare news_cctv(date=...) — CCTV news transcripts, one request per calendar day (policy/macro).
  limit_up       the JSONL files written by scripts/news_archive.py --sources ths_limit_up (one year backfill).
  market         akshare stock_info_global_em / _cls / _ths — recent news flashes only; run daily to build history.

The store is `data/news/news.db` (gitignored). Everything is idempotent; rerunning a range only replaces rows.

usage:
  uv run --with akshare python research/news_selector/fetch_data.py --start 2026-05-01 --end 2026-10-26
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.stock_data.news_store import NewsStore  # noqa: E402

DATA = ROOT / "data"


def trading_days(start: str, end: str):
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def calendar_days(start: str, end: str):
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        yield d
        d += timedelta(days=1)


def _num(value):
    try:
        x = float(value)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _iso_date(value, fallback: date) -> str:
    s = str(value or "")
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10] if len(s) >= 10 else fallback.isoformat()


def fetch_announcements(store: NewsStore, start: str, end: str) -> None:
    import akshare as ak

    total = 0
    for i, day in enumerate(trading_days(start, end), 1):
        try:
            df = ak.stock_notice_report(symbol="全部", date=day.strftime("%Y%m%d"))
        except Exception as exc:
            print(f"[ann] {day} failed: {exc}")
            time.sleep(1.0)
            continue
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
        total += store.upsert_announcements(rows)
        if i % 10 == 0 or i == 1:
            print(f"[ann] {day} {len(rows)} rows, total {total}", flush=True)
        time.sleep(0.2)
    print(f"[ann] done: {total} rows")


def fetch_cctv(store: NewsStore, start: str, end: str) -> None:
    import akshare as ak

    total = 0
    for i, day in enumerate(calendar_days(start, end), 1):
        try:
            df = ak.news_cctv(date=day.strftime("%Y%m%d"))
        except Exception:
            continue
        rows = [{
            "date": _iso_date(r.get("date"), day),
            "title": str(r.get("title") or ""),
            "content": str(r.get("content") or ""),
            "source": "cctv",
        } for r in df.to_dict("records")]
        total += store.upsert_policy_news(rows)
        if i % 20 == 0:
            print(f"[cctv] {day}, total {total}", flush=True)
        time.sleep(0.15)
    print(f"[cctv] done: {total} rows")


def fetch_limit_up(store: NewsStore, start: str, end: str) -> None:
    folder = DATA / "news" / "ths_limit_up"
    files = sorted(folder.glob("*.jsonl"))
    rows = []
    for path in files:
        day = path.stem
        if not (start <= day <= end):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                x = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = str(x.get("code") or "").zfill(6)
            if not re.fullmatch(r"\d{6}", code):
                continue
            rows.append({
                "date": day,
                "code": code,
                "name": x.get("name") or "",
                "reason": x.get("reason_type") or "",
                "limit_up_type": x.get("limit_up_type") or "",
                "high_days": _num(x.get("high_days")),
                "turnover_rate": _num(x.get("turnover_rate")),
                "source": "ths",
            })
    print(f"[limit_up] {len(rows)} rows from {len(files)} files")
    print(f"[limit_up] wrote {store.upsert_limit_up(rows)} rows")


def fetch_market(store: NewsStore) -> None:
    import akshare as ak

    rows = []
    try:
        df = ak.stock_info_global_em()
        for r in df.to_dict("records"):
            rows.append({"ts": str(r.get("发布时间") or ""), "title": str(r.get("标题") or ""),
                         "content": str(r.get("摘要") or ""), "source": "eastmoney",
                         "url": str(r.get("链接") or ""), "stocks": []})
    except Exception as exc:
        print("[market] eastmoney failed:", exc)
    for source, fn in (("cls", ak.stock_info_global_cls), ("ths", ak.stock_info_global_ths)):
        try:
            df = fn()
            for r in df.to_dict("records"):
                ts = str(r.get("发布时间") or r.get("发布日期") or "")
                rows.append({"ts": ts, "title": str(r.get("标题") or ""),
                             "content": str(r.get("内容") or ""), "source": source,
                             "url": str(r.get("链接") or ""), "stocks": []})
        except Exception as exc:
            print(f"[market] {source} failed:", exc)
    print(f"[market] wrote {store.upsert_market_news(rows)} rows")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--db", default=str(DATA / "news" / "news.db"))
    ap.add_argument("--sources", default="ann,cctv,limit_up,market")
    args = ap.parse_args()
    store = NewsStore(args.db)
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}
    if "ann" in sources:
        fetch_announcements(store, args.start, args.end)
    if "cctv" in sources:
        fetch_cctv(store, args.start, args.end)
    if "limit_up" in sources:
        fetch_limit_up(store, args.start, args.end)
    if "market" in sources:
        fetch_market(store)
    print("[done]", store.counts())


if __name__ == "__main__":
    main()
