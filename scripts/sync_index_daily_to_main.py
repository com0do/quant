from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.stock_data.jq_client import JqClient


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync index daily prices into main sqlite DB.")
    p.add_argument("--db-path", default="data/market_cache.db")
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument(
        "--index-codes",
        default="000852.XSHG,399303.XSHE,000905.XSHG,000300.XSHG",
        help="Comma-separated index codes.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    codes = [x.strip() for x in args.index_codes.split(",") if x.strip()]
    conn = sqlite3.connect(args.db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS index_prices_daily (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            money REAL,
            PRIMARY KEY(date, code)
        )
        """
    )
    conn.commit()

    jq = JqClient()
    quota_blocked = False
    try:
        for i, code in enumerate(codes, start=1):
            try:
                df = jq.get_price_daily(code, args.start_date, args.end_date)
            except Exception as e:
                msg = str(e)
                print(f"[WARN] {i}/{len(codes)} {code} failed: {msg}")
                if ("query limit" in msg.lower()) or ("最大查询限制" in msg):
                    print("[STOP] quota reached")
                    quota_blocked = True
                    break
                continue
            if df is None or df.empty:
                print(f"[SKIP] {i}/{len(codes)} {code} empty")
                continue
            d = df.copy()
            if "date" in d.columns:
                dt = pd.to_datetime(d["date"])
            else:
                d = d.reset_index()
                dt = pd.to_datetime(d.iloc[:, 0])
            d["date"] = dt.dt.strftime("%Y-%m-%d")
            d["code"] = code
            keep = ["date", "code", "open", "high", "low", "close", "volume", "money"]
            for c in keep:
                if c not in d.columns:
                    d[c] = None
            recs = [tuple(x) for x in d[keep].itertuples(index=False, name=None)]
            cur.executemany(
                "INSERT OR REPLACE INTO index_prices_daily(date,code,open,high,low,close,volume,money) VALUES (?,?,?,?,?,?,?,?)",
                recs,
            )
            conn.commit()
            print(f"[OK] {i}/{len(codes)} {code} rows={len(recs)}")
    finally:
        try:
            jq.logout()
        except Exception:
            pass
        conn.close()

    if quota_blocked:
        print("[DONE] status=quota_blocked")
    else:
        print("[DONE] status=ok")


if __name__ == "__main__":
    main()

