#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _exists(p: str) -> bool:
    return Path(p).exists()


def _db_basic(p: str) -> dict:
    out = {"path": p, "exists": _exists(p)}
    if not out["exists"]:
        return out
    out["size_mb"] = round(Path(p).stat().st_size / 1024 / 1024, 3)
    conn = sqlite3.connect(p)
    cur = conn.cursor()
    tabs = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1").fetchall()]
    out["tables"] = tabs
    rc = {}
    for t in ("prices_daily", "prices_minute", "fundamentals_snapshot", "factors_snapshot", "index_prices_daily", "sync_progress"):
        if t not in tabs:
            continue
        rc[t] = int(cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    out["rows"] = rc
    conn.close()
    return out


def _script_contains(path: str, needle: str) -> bool:
    p = ROOT / path
    if not p.exists():
        return False
    return needle in p.read_text(encoding="utf-8", errors="ignore")


def main() -> None:
    out_dir = ROOT / "output"
    rep_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    rep_dir.mkdir(exist_ok=True)

    runtime_3500 = "data/runtime/market_csi300_500_latest.db"
    runtime_1000 = "data/runtime/market_csi1000_latest.db"
    runtime_2000 = "data/runtime/market_csi2000_latest.db"
    meta_db = "data/meta/index_constituents.db"

    checks = {
        "runtime_dbs": [
            _db_basic(str(ROOT / runtime_3500)),
            _db_basic(str(ROOT / runtime_1000)),
            _db_basic(str(ROOT / runtime_2000)),
        ],
        "meta_db": _db_basic(str(ROOT / meta_db)),
        "daily_pipeline_has_auto_archive": _script_contains("scripts/run_daily_live_pipeline.sh", "roll_archive_runtime_db.py"),
        "xtquant_3m_backfill_hook_exists": _script_contains("scripts/run_daily_live_pipeline.sh", "sync_minute_csi1000_runtime.py")
        or _script_contains("README.md", "XTDATA_MINIMAL_SYNC_POLICY"),
        "notes": {
            "archive_expected": "runtime更新应自动触发溢出归档",
            "xtquant_expected": "后续需通过xtquant补前3个月数据（JQ窗口前15月~前3月）",
        },
    }

    # Minute coverage for CSI1000 runtime.
    try:
        conn = sqlite3.connect(str(ROOT / runtime_1000))
        cur = conn.cursor()
        tabs = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "prices_minute" in tabs:
            rows = cur.execute("SELECT COUNT(*) FROM prices_minute").fetchone()[0]
            codes = cur.execute("SELECT COUNT(DISTINCT code) FROM prices_minute").fetchone()[0]
            span = cur.execute("SELECT MIN(datetime), MAX(datetime) FROM prices_minute").fetchone()
            checks["csi1000_minute_coverage"] = {"rows": int(rows), "codes": int(codes), "span": span}
        conn.close()
    except Exception as exc:
        checks["csi1000_minute_coverage_error"] = str(exc)

    (out_dir / "runtime_consistency_check.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Runtime Consistency Check",
        "",
        f"- auto_archive_in_daily_pipeline: {checks['daily_pipeline_has_auto_archive']}",
        f"- xtquant_3m_backfill_hook_exists: {checks['xtquant_3m_backfill_hook_exists']}",
        "",
        "## Runtime DBs",
    ]
    for x in checks["runtime_dbs"]:
        lines.append(f"- `{x['path']}` exists={x['exists']} size_mb={x.get('size_mb','-')} rows={x.get('rows',{})}")
    lines += ["", "## Meta DB", f"- `{checks['meta_db']['path']}` exists={checks['meta_db']['exists']} rows={checks['meta_db'].get('rows',{})}"]
    if "csi1000_minute_coverage" in checks:
        lines += ["", "## CSI1000 Minute Coverage", f"- {checks['csi1000_minute_coverage']}"]
    (rep_dir / "runtime_consistency_check.md").write_text("\n".join(lines), encoding="utf-8")
    print("[OK] wrote output/runtime_consistency_check.json and reports/runtime_consistency_check.md")


if __name__ == "__main__":
    main()

