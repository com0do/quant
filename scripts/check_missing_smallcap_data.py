#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


def _inspect_db(db_path: str, index_code: str) -> dict:
    out: dict = {"db_path": db_path, "index_code": index_code, "exists": False}
    p = Path(db_path)
    if not p.exists():
        out["missing_reason"] = "db_not_found"
        return out
    out["exists"] = True
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    out["tables"] = sorted(tables)

    def _one(sql: str, args: tuple = ()) -> tuple:
        return cur.execute(sql, args).fetchone()

    out["prices_rows"] = int(_one("SELECT COUNT(*) FROM prices_daily")[0]) if "prices_daily" in tables else 0
    out["prices_span"] = _one("SELECT MIN(date), MAX(date) FROM prices_daily") if "prices_daily" in tables else (None, None)
    out["prices_codes"] = int(_one("SELECT COUNT(DISTINCT code) FROM prices_daily")[0]) if "prices_daily" in tables else 0

    out["index_members_rows"] = int(_one("SELECT COUNT(*) FROM index_members")[0]) if "index_members" in tables else 0
    out["index_members_latest"] = (
        int(_one("SELECT COUNT(DISTINCT code) FROM index_members WHERE index_code=?", (index_code,))[0])
        if "index_members" in tables
        else 0
    )

    out["sync_left_price"] = int(_one("SELECT COUNT(*) FROM sync_progress WHERE done=0")[0]) if "sync_progress" in tables else 0
    out["snapshot_left"] = int(_one("SELECT COUNT(*) FROM snapshot_progress WHERE done=0")[0]) if "snapshot_progress" in tables else 0
    out["fund_rows"] = int(_one("SELECT COUNT(*) FROM fundamentals_snapshot")[0]) if "fundamentals_snapshot" in tables else 0
    out["factor_rows"] = int(_one("SELECT COUNT(*) FROM factors_snapshot")[0]) if "factors_snapshot" in tables else 0

    # Missing snapshot coverage based on date-level price code coverage.
    if {"prices_daily", "fundamentals_snapshot", "factors_snapshot"} <= tables:
        incomplete = cur.execute(
            """
            WITH p AS (
              SELECT date, COUNT(DISTINCT code) AS n_price
              FROM prices_daily
              GROUP BY date
            ),
            f AS (
              SELECT date, COUNT(DISTINCT code) AS n_fund
              FROM fundamentals_snapshot
              GROUP BY date
            ),
            fc AS (
              SELECT date, COUNT(DISTINCT code) AS n_fac
              FROM factors_snapshot
              GROUP BY date
            )
            SELECT p.date, p.n_price, COALESCE(f.n_fund, 0) AS n_fund, COALESCE(fc.n_fac, 0) AS n_fac
            FROM p
            LEFT JOIN f USING(date)
            LEFT JOIN fc USING(date)
            WHERE COALESCE(f.n_fund, 0) < p.n_price
               OR COALESCE(fc.n_fac, 0) < p.n_price
            ORDER BY p.date
            """
        ).fetchall()
        out["snapshot_incomplete_dates"] = len(incomplete)
        out["snapshot_incomplete_samples"] = [
            {"date": d, "price_codes": int(np), "fund_codes": int(nf), "factor_codes": int(nc)}
            for d, np, nf, nc in incomplete[-10:]
        ]
    else:
        out["snapshot_incomplete_dates"] = 0
        out["snapshot_incomplete_samples"] = []

    conn.close()
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Check missing data status for CSI1000/CSI2000 and generate tomorrow sync script")
    p.add_argument("--start-date", default="2024-12-23")
    p.add_argument("--end-date", default="2025-12-23")
    p.add_argument("--db-csi1000", default="data/market_cache_csi1000_1y.db")
    p.add_argument("--db-csi2000", default="data/market_cache_csi2000_1y.db")
    p.add_argument("--out-json", default="output/missing_data_check.json")
    p.add_argument("--out-md", default="reports/missing_data_check.md")
    p.add_argument("--emit-script", default="scripts/run_tomorrow_smallcap_sync.sh")
    args = p.parse_args()

    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    Path("scripts").mkdir(exist_ok=True)

    rows = [
        _inspect_db(args.db_csi1000, "000852.XSHG"),
        _inspect_db(args.db_csi2000, "399303.XSHE"),
    ]
    report = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "databases": rows,
    }

    Path(args.out_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# Missing Data Check",
        "",
        f"- start_date: `{args.start_date}`",
        f"- end_date: `{args.end_date}`",
        "",
    ]
    for r in rows:
        md += [
            f"## {r['db_path']}",
            f"- exists: {r.get('exists')}",
            f"- index_code: `{r.get('index_code')}`",
            f"- prices_rows: {r.get('prices_rows', 0)}",
            f"- prices_span: {r.get('prices_span')}",
            f"- prices_codes: {r.get('prices_codes', 0)}",
            f"- index_members_rows: {r.get('index_members_rows', 0)}",
            f"- index_members_latest: {r.get('index_members_latest', 0)}",
            f"- sync_left_price: {r.get('sync_left_price', 0)}",
            f"- snapshot_left: {r.get('snapshot_left', 0)}",
            f"- snapshot_incomplete_dates: {r.get('snapshot_incomplete_dates', 0)}",
            f"- fund_rows: {r.get('fund_rows', 0)}",
            f"- factor_rows: {r.get('factor_rows', 0)}",
            "",
        ]
    Path(args.out_md).write_text("\n".join(md), encoding="utf-8")

    sh = f"""#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
cd "${{ROOT_DIR}}"

START_DATE="${{START_DATE:-{args.start_date}}}"
END_DATE="${{END_DATE:-{args.end_date}}}"
SNAPSHOT_CHUNK_SIZE="${{SNAPSHOT_CHUNK_SIZE:-300}}"

echo "[0/3] Re-check missing status"
uv run python scripts/check_missing_smallcap_data.py --start-date "${{START_DATE}}" --end-date "${{END_DATE}}"

echo "[1/3] Prepare snapshot retry set (only missing/incomplete dates)"
uv run python scripts/prepare_snapshot_retry.py --db-path {args.db_csi1000} --start-date "${{START_DATE}}" --end-date "${{END_DATE}}"
uv run python scripts/prepare_snapshot_retry.py --db-path {args.db_csi2000} --start-date "${{START_DATE}}" --end-date "${{END_DATE}}"

echo "[2/3] Sync missing snapshots for CSI1000 (no repeat for done=1 dates)"
uv run python scripts/jq_bulk_sync_smallcap.py \\
  --index-code 000852.XSHG \\
  --start-date "${{START_DATE}}" \\
  --end-date "${{END_DATE}}" \\
  --db-path {args.db_csi1000} \\
  --only-snapshots \\
  --snapshot-chunk-size "${{SNAPSHOT_CHUNK_SIZE}}"

echo "[3/3] Sync missing snapshots for CSI2000 (no repeat for done=1 dates)"
uv run python scripts/jq_bulk_sync_smallcap.py \\
  --index-code 399303.XSHE \\
  --start-date "${{START_DATE}}" \\
  --end-date "${{END_DATE}}" \\
  --db-path {args.db_csi2000} \\
  --only-snapshots \\
  --snapshot-chunk-size "${{SNAPSHOT_CHUNK_SIZE}}"

echo "[DONE] Re-check result"
uv run python scripts/check_missing_smallcap_data.py --start-date "${{START_DATE}}" --end-date "${{END_DATE}}"
"""
    script_path = Path(args.emit_script)
    script_path.write_text(sh, encoding="utf-8")
    script_path.chmod(0o755)
    print(f"[OK] wrote {args.out_json}, {args.out_md}, {script_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
