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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compact duplicate constituent snapshots and rebuild intervals.")
    p.add_argument("--meta-db", default="data/meta/index_constituents.db")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    db = IndexConstituentsDB(args.meta_db)
    out = db.compact_snapshots()

    # Remove legacy tables that are no longer needed.
    with sqlite3.connect(args.meta_db) as conn:
        conn.execute("DROP TABLE IF EXISTS index_dim")
        conn.execute("DROP TABLE IF EXISTS constituent_snapshots")
        conn.execute("DROP TABLE IF EXISTS index_constituents")
        conn.execute("DROP TABLE IF EXISTS constituent_snapshot_compact_legacy")
        conn.execute("DROP TABLE IF EXISTS constituent_snapshots_v2")
        conn.execute("DROP TABLE IF EXISTS index_constituents_v2")
        conn.commit()

    result = {"status": "ok", "meta_db": args.meta_db, "indexes": out}
    Path("output/compact_constituents_meta_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["# Compact Constituents Meta Report", "", f"- meta_db: `{args.meta_db}`", ""]
    for idx, stat in out.items():
        lines.append(f"- `{idx}`: before={stat['before_dates']} after={stat['after_dates']} dropped={stat['dropped_dates']}")
    Path("reports/compact_constituents_meta_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("[OK] wrote output/compact_constituents_meta_report.json and reports/compact_constituents_meta_report.md")


if __name__ == "__main__":
    main()

