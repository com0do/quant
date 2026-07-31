#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.stock_data.index_constituents import IndexConstituentsDB
from quant.stock_data.market_db import MarketDB


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> int:
    row = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row and row[0]:
        try:
            dst.execute(row[0])
        except Exception:
            pass
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0
    cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})").fetchall()]
    ph = ",".join(["?"] * len(cols))
    dst.executemany(f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES ({ph})", rows)
    return len(rows)


def _ensure_index_prices_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
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


def _migrate_one(src_db: str, dst_db: str) -> dict:
    Path(dst_db).parent.mkdir(parents=True, exist_ok=True)
    MarketDB(dst_db)  # ensure base tables
    out = {"source": src_db, "target": dst_db, "copied": {}}
    with sqlite3.connect(src_db) as src, sqlite3.connect(dst_db) as dst:
        _ensure_index_prices_schema(dst)
        st = _tables(src)
        for tb in (
            "prices_daily",
            "prices_minute",
            "fundamentals_snapshot",
            "factors_snapshot",
            "sync_progress",
            "snapshot_progress",
            "index_members",
            "meta",
            "index_prices_daily",
            "prefetch_progress",
        ):
            if tb not in st:
                continue
            try:
                n = _copy_table(src, dst, tb)
                out["copied"][tb] = int(n)
            except Exception:
                out["copied"][tb] = -1
        dst.commit()
    return out


def _rebuild_constituents_from_index_members(meta_db: str, src_dbs: list[str]) -> dict:
    cdb = IndexConstituentsDB(meta_db)
    stats: dict[str, int] = {}
    for p in src_dbs:
        if not Path(p).exists():
            continue
        with sqlite3.connect(p) as conn:
            if "index_members" not in _tables(conn):
                continue
            rows = conn.execute(
                """
                SELECT index_code, date, GROUP_CONCAT(code)
                FROM index_members
                GROUP BY index_code, date
                ORDER BY index_code, date
                """
            ).fetchall()
        for index_code, dt, csv_codes in rows:
            codes = [x for x in str(csv_codes or "").split(",") if x]
            n = cdb.upsert_snapshot(str(index_code), str(dt), codes)
            stats[str(index_code)] = int(stats.get(str(index_code), 0) + n)
    return stats


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Full migrate legacy DBs to index-aware runtime/meta layout.")
    p.add_argument("--src-main", default="data/market_cache.db")
    p.add_argument("--src-csi1000", default="data/market_cache_csi1000_1y.db")
    p.add_argument("--src-csi2000", default="data/market_cache_csi2000_1y.db")
    p.add_argument("--dst-runtime-300-500", default="data/runtime/market_csi300_500_latest.db")
    p.add_argument("--dst-runtime-1000", default="data/runtime/market_csi1000_latest.db")
    p.add_argument("--dst-runtime-2000", default="data/runtime/market_csi2000_latest.db")
    p.add_argument("--meta-db", default="data/meta/index_constituents.db")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    Path("data/archive").mkdir(parents=True, exist_ok=True)
    Path("data/runtime").mkdir(parents=True, exist_ok=True)
    Path("data/meta").mkdir(parents=True, exist_ok=True)
    Path("output").mkdir(parents=True, exist_ok=True)
    Path("reports").mkdir(parents=True, exist_ok=True)

    migrations = []
    if Path(args.src_main).exists():
        migrations.append(_migrate_one(args.src_main, args.dst_runtime_300_500))
    if Path(args.src_csi1000).exists():
        migrations.append(_migrate_one(args.src_csi1000, args.dst_runtime_1000))
    if Path(args.src_csi2000).exists():
        migrations.append(_migrate_one(args.src_csi2000, args.dst_runtime_2000))

    constituents_stats = _rebuild_constituents_from_index_members(
        meta_db=args.meta_db,
        src_dbs=[args.src_main, args.src_csi1000, args.src_csi2000],
    )
    report = {
        "status": "ok",
        "migrations": migrations,
        "constituents_stats": constituents_stats,
        "runtime_dbs": {
            "csi300_500": args.dst_runtime_300_500,
            "csi1000": args.dst_runtime_1000,
            "csi2000": args.dst_runtime_2000,
        },
        "meta_db": args.meta_db,
    }
    Path("output/index_aware_migration_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Index-aware Full Migration Report",
        "",
        f"- status: {report['status']}",
        f"- meta_db: `{args.meta_db}`",
        f"- runtime csi300_500: `{args.dst_runtime_300_500}`",
        f"- runtime csi1000: `{args.dst_runtime_1000}`",
        f"- runtime csi2000: `{args.dst_runtime_2000}`",
        "",
        "## Constituents Rebuild",
        f"- {constituents_stats}",
        "",
        "## Table Copy",
    ]
    for m in migrations:
        lines.append(f"- `{m['source']}` -> `{m['target']}`: {m['copied']}")
    Path("reports/index_aware_migration_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("[OK] wrote output/index_aware_migration_report.json and reports/index_aware_migration_report.md")


if __name__ == "__main__":
    main()

