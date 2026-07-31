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


def _db_stats(p: str) -> dict:
    out = {"db_path": p, "exists": Path(p).exists()}
    if not out["exists"]:
        return out
    with sqlite3.connect(p) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        out["tables"] = sorted(tables)
        if "prices_daily" in tables:
            out["prices_rows"] = int(conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0])
            out["prices_span"] = conn.execute("SELECT MIN(date), MAX(date) FROM prices_daily").fetchone()
            out["prices_codes"] = int(conn.execute("SELECT COUNT(DISTINCT code) FROM prices_daily").fetchone()[0])
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate index-aware DB migration outputs.")
    p.add_argument("--db-csi300-500", default="data/runtime/market_csi300_500_latest.db")
    p.add_argument("--db-csi1000", default="data/runtime/market_csi1000_latest.db")
    p.add_argument("--db-csi2000", default="data/runtime/market_csi2000_latest.db")
    p.add_argument("--meta-db", default="data/meta/index_constituents.db")
    p.add_argument("--probe-date", default="2025-12-31")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    cdb = IndexConstituentsDB(args.meta_db)
    out = {
        "status": "ok",
        "runtime": [
            _db_stats(args.db_csi300_500),
            _db_stats(args.db_csi1000),
            _db_stats(args.db_csi2000),
        ],
        "meta_db": args.meta_db,
        "probe_date": args.probe_date,
        "constituent_probe": {
            "000300.XSHG": len(cdb.get_codes("000300.XSHG", args.probe_date)),
            "000905.XSHG": len(cdb.get_codes("000905.XSHG", args.probe_date)),
            "000852.XSHG": len(cdb.get_codes("000852.XSHG", args.probe_date)),
            "399303.XSHE": len(cdb.get_codes("399303.XSHE", args.probe_date)),
        },
    }
    Path("output/index_aware_validation_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Index-aware Validation Report",
        "",
        f"- probe_date: `{args.probe_date}`",
        f"- constituent_probe: {out['constituent_probe']}",
        "",
        "## Runtime DB Stats",
    ]
    for x in out["runtime"]:
        lines.append(f"- `{x['db_path']}` exists={x.get('exists')} rows={x.get('prices_rows', 0)} span={x.get('prices_span')}")
    Path("reports/index_aware_validation_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("[OK] wrote output/index_aware_validation_report.json and reports/index_aware_validation_report.md")


if __name__ == "__main__":
    main()

