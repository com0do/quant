#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

MOVE_TABLES = ("prices_daily", "fundamentals_snapshot", "factors_snapshot", "index_prices_daily")


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _copy_table_schema(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> None:
    row = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row and row[0]:
        dst.execute(row[0])


def _move_by_date(runtime_db: str, archive_db: str, table: str, start_date: str, end_date: str) -> int:
    with sqlite3.connect(runtime_db) as src, sqlite3.connect(archive_db) as dst:
        _copy_table_schema(src, dst, table)
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})").fetchall()]
        if not cols:
            return 0
        rows = src.execute(
            f"SELECT * FROM {table} WHERE date >= ? AND date <= ?",
            (start_date, end_date),
        ).fetchall()
        if not rows:
            return 0
        ph = ",".join(["?"] * len(cols))
        dst.executemany(
            f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES ({ph})",
            rows,
        )
        src.execute(
            f"DELETE FROM {table} WHERE date >= ? AND date <= ?",
            (start_date, end_date),
        )
        src.commit()
        dst.commit()
    return len(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Roll old runtime rows into archive DB with overflow threshold.")
    p.add_argument("--runtime-db", required=True)
    p.add_argument("--archive-db", required=True)
    p.add_argument("--window-days", type=int, default=548)
    p.add_argument("--overflow-days", type=int, default=61)
    p.add_argument("--move-chunk-days", type=int, default=61)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    Path(args.archive_db).parent.mkdir(parents=True, exist_ok=True)
    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)

    moved: dict[str, int] = {}
    with sqlite3.connect(args.runtime_db) as conn:
        tb = _tables(conn)
        if "prices_daily" not in tb:
            out = {"status": "skip", "reason": "no prices_daily", "runtime_db": args.runtime_db}
            Path("output/runtime_archive_roll_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[SKIP] runtime db has no prices_daily")
            return
        mm = conn.execute("SELECT MIN(date), MAX(date) FROM prices_daily").fetchone()
    min_dt, max_dt = mm[0], mm[1]
    if not min_dt or not max_dt:
        out = {"status": "skip", "reason": "empty prices_daily", "runtime_db": args.runtime_db}
        Path("output/runtime_archive_roll_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[SKIP] empty prices_daily")
        return

    max_d = _d(max_dt)
    keep_start = max_d - timedelta(days=max(1, int(args.window_days)))
    overflow_cutoff = keep_start - timedelta(days=max(1, int(args.overflow_days)))
    min_d = _d(min_dt)
    if min_d >= overflow_cutoff:
        out = {
            "status": "ok",
            "runtime_db": args.runtime_db,
            "archive_db": args.archive_db,
            "moved": {},
            "range": [min_dt, max_dt],
            "message": "no overflow",
        }
        Path("output/runtime_archive_roll_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[OK] no overflow")
        return

    move_start = min_d
    move_end = min(overflow_cutoff, move_start + timedelta(days=max(1, int(args.move_chunk_days)) - 1))
    s, e = move_start.strftime("%Y-%m-%d"), move_end.strftime("%Y-%m-%d")

    with sqlite3.connect(args.runtime_db) as conn:
        exists = _tables(conn)
    for table in MOVE_TABLES:
        if table not in exists:
            continue
        moved[table] = _move_by_date(args.runtime_db, args.archive_db, table, s, e)

    out = {
        "status": "ok",
        "runtime_db": args.runtime_db,
        "archive_db": args.archive_db,
        "moved_range": [s, e],
        "moved": moved,
        "window_days": int(args.window_days),
        "overflow_days": int(args.overflow_days),
        "move_chunk_days": int(args.move_chunk_days),
    }
    Path("output/runtime_archive_roll_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Runtime Archive Roll Report",
        "",
        f"- runtime_db: `{args.runtime_db}`",
        f"- archive_db: `{args.archive_db}`",
        f"- moved_range: `{s}` ~ `{e}`",
        f"- moved: {moved}",
    ]
    Path("reports/runtime_archive_roll_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("[OK] wrote output/runtime_archive_roll_report.json and reports/runtime_archive_roll_report.md")


if __name__ == "__main__":
    main()

