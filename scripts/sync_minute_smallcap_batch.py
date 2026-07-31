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
    p = argparse.ArgumentParser(description="Sync minute bars for turnover-ranked smallcap symbols.")
    p.add_argument("--db-path", default="data/market_cache.db")
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=120)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    conn = sqlite3.connect(args.db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prices_minute (
            datetime TEXT NOT NULL,
            code TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            money REAL,
            PRIMARY KEY(datetime, code)
        )
        """
    )
    conn.commit()

    latest = cur.execute("select max(date) from factors_snapshot").fetchone()[0]
    q = """
    SELECT f.code
    FROM factors_snapshot f
    JOIN index_members m ON m.code=f.code
    WHERE f.date=? AND m.date=? AND m.index_code in ('000852.XSHG','399303.XSHE')
    GROUP BY f.code
    ORDER BY max(f.turnover_ratio) DESC
    LIMIT ? OFFSET ?
    """
    codes = [r[0] for r in cur.execute(q, (latest, latest, args.limit, args.offset)).fetchall()]
    print(f"[INFO] latest={latest} offset={args.offset} limit={args.limit} codes={len(codes)}")

    jq = JqClient()
    ok = 0
    rows = 0
    try:
        for i, code in enumerate(codes, start=1):
            try:
                df = jq.get_price_minute(code, args.start_date, args.end_date)
                if df is None or df.empty:
                    print(f"[SKIP] {i}/{len(codes)} {code} empty")
                    continue
                d = df.copy()
                if "time" in d.columns:
                    dt = pd.to_datetime(d["time"])
                elif "datetime" in d.columns:
                    dt = pd.to_datetime(d["datetime"])
                elif "date" in d.columns:
                    dt = pd.to_datetime(d["date"])
                else:
                    d = d.reset_index()
                    dt = pd.to_datetime(d.iloc[:, 0])
                d["datetime"] = dt.dt.strftime("%Y-%m-%d %H:%M:%S")
                d["code"] = code
                keep = ["datetime", "code", "open", "high", "low", "close", "volume", "money"]
                for c in keep:
                    if c not in d.columns:
                        d[c] = None
                recs = [tuple(x) for x in d[keep].itertuples(index=False, name=None)]
                cur.executemany(
                    "INSERT OR REPLACE INTO prices_minute(datetime,code,open,high,low,close,volume,money) VALUES (?,?,?,?,?,?,?,?)",
                    recs,
                )
                conn.commit()
                ok += 1
                rows += len(recs)
                if i % 10 == 0:
                    print(f"[PROGRESS] {i}/{len(codes)} ok={ok} rows={rows}")
            except Exception as e:
                msg = str(e)
                print(f"[WARN] {i}/{len(codes)} {code} failed: {msg}")
                if ("query limit" in msg.lower()) or ("最大查询限制" in msg):
                    print("[STOP] quota reached")
                    break
    finally:
        try:
            jq.logout()
        except Exception:
            pass
        total = cur.execute("select count(*) from prices_minute").fetchone()[0]
        conn.close()

    print(f"[DONE] ok={ok} rows={rows} total_rows={total}")


if __name__ == "__main__":
    main()

