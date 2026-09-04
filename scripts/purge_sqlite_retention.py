#!/usr/bin/env python3
"""Delete SQLite rows older than a retention window (default 3 years).

Used by home data node daily job. Purges date-keyed tables in runtime/archive DBs,
then runs VACUUM to reclaim disk space.

Usage:
  python scripts/purge_sqlite_retention.py --db-path data/runtime/market_csi1000_latest.db
  python scripts/purge_sqlite_retention.py --glob "data/runtime/*.db" --years 3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


DATE_TABLES = (
    "prices_daily",
    "prices_minute",
    "fundamentals_snapshot",
    "factors_snapshot",
    "index_prices_daily",
    "snapshot_progress",
    "index_members",
)


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def purge_db(db_path: str, cutoff: str, vacuum: bool = True) -> dict:
    path = Path(db_path)
    out: dict = {"db": str(path), "cutoff": cutoff, "deleted": {}, "error": None}
    if not path.is_file():
        out["error"] = "missing"
        return out

    before_mb = round(path.stat().st_size / 1_048_576, 2)
    total_deleted = 0

    try:
        conn = sqlite3.connect(str(path), timeout=120.0)
        conn.execute("PRAGMA journal_mode=WAL")
        existing = _tables(conn)

        for table in DATE_TABLES:
            if table not in existing:
                continue
            cur = conn.execute(
                f"DELETE FROM {table} WHERE date < ?",
                (cutoff,),
            )
            n = int(cur.rowcount)
            if n > 0:
                out["deleted"][table] = n
                total_deleted += n

        # sync_progress: drop stale per-code checkpoints far behind cutoff
        if "sync_progress" in existing:
            cur = conn.execute(
                "DELETE FROM sync_progress WHERE next_date < ?",
                (cutoff,),
            )
            n = int(cur.rowcount)
            if n > 0:
                out["deleted"]["sync_progress"] = n
                total_deleted += n

        conn.commit()

        if vacuum and total_deleted > 0:
            conn.execute("VACUUM")
        conn.close()
    except Exception as exc:
        out["error"] = str(exc)
        return out

    after_mb = round(path.stat().st_size / 1_048_576, 2)
    out["total_deleted"] = total_deleted
    out["size_mb_before"] = before_mb
    out["size_mb_after"] = after_mb
    out["freed_mb"] = round(before_mb - after_mb, 2)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Purge SQLite market data older than N years")
    p.add_argument("--db-path", action="append", default=[], help="single db path (repeatable)")
    p.add_argument("--glob", default="", help='e.g. "data/runtime/*.db"')
    p.add_argument("--years", type=float, default=3.0, help="retention window in years")
    p.add_argument("--no-vacuum", action="store_true")
    p.add_argument(
        "--report",
        default="output/home_data_node_retention_report.json",
        help="write JSON report here",
    )
    args = p.parse_args()

    paths: list[str] = list(args.db_path)
    if args.glob:
        paths.extend(str(p) for p in sorted(Path().glob(args.glob)))

    if not paths:
        print("[ERR] no --db-path or --glob")
        return 2

    cutoff_dt = datetime.utcnow().date() - timedelta(days=int(args.years * 365.25))
    cutoff = cutoff_dt.isoformat()
    print(f"[retention] cutoff={cutoff}  years={args.years}  dbs={len(paths)}")

    reports = []
    for db in paths:
        rep = purge_db(db, cutoff, vacuum=not args.no_vacuum)
        reports.append(rep)
        if rep.get("error"):
            print(f"  [err] {db}: {rep['error']}")
        elif rep.get("total_deleted", 0) > 0:
            print(
                f"  [ok] {db}: deleted={rep['total_deleted']} "
                f"freed={rep.get('freed_mb', 0)}MB"
            )
        else:
            print(f"  [ok] {db}: nothing older than cutoff")

    summary = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "cutoff": cutoff,
        "years": args.years,
        "dbs": reports,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[retention] report -> {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
