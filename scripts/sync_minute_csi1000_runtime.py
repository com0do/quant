#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.stock_data.index_constituents import IndexConstituentsDB
from quant.stock_data.jq_client import JqClient


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync missing minute bars for CSI1000 runtime DB.")
    p.add_argument("--db-path", default="data/runtime/market_csi1000_latest.db")
    p.add_argument("--meta-db", default="data/meta/index_constituents.db")
    p.add_argument("--index-code", default="000852.XSHG")
    p.add_argument("--asof-date", default="2025-12-23")
    p.add_argument("--start-date", default="2025-01-01")
    p.add_argument("--end-date", default="2025-12-31")
    p.add_argument("--max-codes", type=int, default=0, help="0 means no manual limit.")
    return p.parse_args()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prices_minute(
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


def _existing_codes(conn: sqlite3.Connection, start_date: str, end_date: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT code
        FROM prices_minute
        WHERE datetime >= ? AND datetime < ?
        """,
        (f"{start_date} 00:00:00", f"{end_date} 23:59:59"),
    ).fetchall()
    return {str(r[0]) for r in rows}


def main() -> None:
    args = parse_args()
    cdb = IndexConstituentsDB(args.meta_db)
    members = cdb.get_codes(args.index_code, args.asof_date)
    if not members:
        print(f"[ERR] no members found in meta db for {args.index_code} @{args.asof_date}")
        return
    conn = sqlite3.connect(args.db_path)
    _ensure_schema(conn)
    existing = _existing_codes(conn, args.start_date, args.end_date)
    missing = [c for c in members if c not in existing]
    if args.max_codes > 0:
        missing = missing[: int(args.max_codes)]
    print(f"[INFO] members={len(members)} existing={len(existing)} missing={len(missing)}")
    if not missing:
        conn.close()
        print("[DONE] no missing minute codes")
        return

    jq = JqClient()
    ok = 0
    rows_total = 0
    quota_stop = False
    try:
        for i, code in enumerate(missing, start=1):
            try:
                df = jq.get_price_minute(code, args.start_date, args.end_date)
            except Exception as exc:
                msg = str(exc)
                print(f"[WARN] {i}/{len(missing)} {code} failed: {msg}")
                if ("query limit" in msg.lower()) or ("最大查询限制" in msg):
                    quota_stop = True
                    break
                continue
            if df is None or df.empty:
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
            conn.executemany(
                """
                INSERT OR REPLACE INTO prices_minute(datetime,code,open,high,low,close,volume,money)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                recs,
            )
            conn.commit()
            ok += 1
            rows_total += len(recs)
            if i % 10 == 0:
                print(f"[PROGRESS] {i}/{len(missing)} ok={ok} rows={rows_total}")
    finally:
        try:
            jq.logout()
        except Exception:
            pass
        total_rows = conn.execute("SELECT COUNT(*) FROM prices_minute").fetchone()[0]
        conn.close()
    print(f"[DONE] quota_stop={quota_stop} synced_codes={ok} rows={rows_total} total_rows={total_rows}")


if __name__ == "__main__":
    main()

