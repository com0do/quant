#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    p = argparse.ArgumentParser(description="Mark incomplete snapshot dates for retry without repeating completed dates")
    p.add_argument("--db-path", required=True)
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    args = p.parse_args()

    conn = sqlite3.connect(args.db_path, timeout=60.0)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshot_progress (
            date TEXT PRIMARY KEY,
            done INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            last_error TEXT,
            rows_fundamentals INTEGER NOT NULL DEFAULT 0,
            rows_factors INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # Ensure every price date in range has a snapshot_progress row.
    price_dates = [
        r[0]
        for r in cur.execute(
            """
            SELECT DISTINCT date
            FROM prices_daily
            WHERE date >= ? AND date <= ?
            ORDER BY date
            """,
            (args.start_date, args.end_date),
        ).fetchall()
    ]
    cur.executemany(
        """
        INSERT OR IGNORE INTO snapshot_progress(date, done, updated_at, last_error, rows_fundamentals, rows_factors)
        VALUES(?, 0, ?, NULL, 0, 0)
        """,
        [(d, _now()) for d in price_dates],
    )

    # Mark as pending if either snapshot table has fewer codes than prices table on that date.
    rows = cur.execute(
        """
        WITH p AS (
          SELECT date, COUNT(DISTINCT code) AS n_price
          FROM prices_daily
          WHERE date >= ? AND date <= ?
          GROUP BY date
        ),
        f AS (
          SELECT date, COUNT(DISTINCT code) AS n_fund
          FROM fundamentals_snapshot
          WHERE date >= ? AND date <= ?
          GROUP BY date
        ),
        fc AS (
          SELECT date, COUNT(DISTINCT code) AS n_fac
          FROM factors_snapshot
          WHERE date >= ? AND date <= ?
          GROUP BY date
        )
        SELECT p.date, p.n_price, COALESCE(f.n_fund, 0) AS n_fund, COALESCE(fc.n_fac, 0) AS n_fac
        FROM p
        LEFT JOIN f USING(date)
        LEFT JOIN fc USING(date)
        WHERE COALESCE(f.n_fund, 0) < p.n_price
           OR COALESCE(fc.n_fac, 0) < p.n_price
        ORDER BY p.date
        """,
        (
            args.start_date,
            args.end_date,
            args.start_date,
            args.end_date,
            args.start_date,
            args.end_date,
        ),
    ).fetchall()

    cur.executemany(
        """
        UPDATE snapshot_progress
        SET done=0, updated_at=?, last_error=NULL
        WHERE date=?
        """,
        [(_now(), d) for d, _, _, _ in rows],
    )
    conn.commit()

    left = cur.execute("SELECT COUNT(*) FROM snapshot_progress WHERE done=0").fetchone()[0]
    print(
        f"[prepare_snapshot_retry] db={args.db_path} price_dates={len(price_dates)} "
        f"forced_pending={len(rows)} left_pending={left}"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
