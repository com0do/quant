from __future__ import annotations

import json
import subprocess
from pathlib import Path

from quant.config import load_config


def _sync_progress_snapshot(db_path: str) -> dict:
    import sqlite3

    out = {"db_path": db_path}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "prices_daily" in tables:
            out["prices_daily_rows"] = int(cur.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0])
            out["prices_date_span"] = cur.execute("SELECT MIN(date), MAX(date) FROM prices_daily").fetchone()
            out["price_code_count"] = int(cur.execute("SELECT COUNT(DISTINCT code) FROM prices_daily").fetchone()[0])
        if "sync_progress" in tables:
            total, done, min_next, max_next = cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(done),0), MIN(next_date), MAX(next_date) FROM sync_progress"
            ).fetchone()
            out["sync_progress_total"] = int(total)
            out["sync_progress_done"] = int(done)
            out["sync_next_min"] = min_next
            out["sync_next_max"] = max_next
        if "snapshot_progress" in tables:
            total, done = cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(done),0) FROM snapshot_progress"
            ).fetchone()
            out["snapshot_progress_total"] = int(total)
            out["snapshot_progress_done"] = int(done)
        conn.close()
    except Exception as exc:
        out["snapshot_error"] = str(exc)
    return out


def run_jq_bulk_sync(config_path: str | None = None) -> dict:
    cfg = load_config(config_path=config_path)
    target_db = cfg.data.sqlite_db_path
    if bool(getattr(cfg.data, "enable_index_aware_db", False)):
        idx = str(cfg.data.benchmark_index)
        if idx in {"000300.XSHG", "000905.XSHG"}:
            target_db = str(getattr(cfg.data, "runtime_csi300_500_db_path", target_db))
        elif idx == "000852.XSHG":
            target_db = str(getattr(cfg.data, "runtime_csi1000_db_path", target_db))
        elif idx == "399303.XSHE":
            target_db = str(getattr(cfg.data, "runtime_csi2000_db_path", target_db))
    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    before = _sync_progress_snapshot(target_db)
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/jq_bulk_sync_smallcap.py",
        "--index-code",
        cfg.data.benchmark_index,
        "--start-date",
        cfg.data.start_date,
        "--end-date",
        cfg.data.end_date,
        "--db-path",
        target_db,
        "--constituents-db",
        str(getattr(cfg.data, "meta_constituents_db_path", "data/meta/index_constituents.db")),
        "--sync-snapshots",
    ]
    q_before = {}
    q_after = {}
    try:
        from quant.stock_data.jq_client import JqClient

        jq = JqClient()
        q_before = jq.query_count()
    except Exception:
        pass

    quota_exhausted = False
    error_message = ""
    stdout_tail = ""
    stderr_tail = ""
    try:
        cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
        stdout_tail = (cp.stdout or "")[-2000:]
        stderr_tail = (cp.stderr or "")[-2000:]
    except subprocess.CalledProcessError as exc:
        error_message = str(exc)
        stdout_tail = (exc.stdout or "")[-2000:]
        stderr_tail = (exc.stderr or "")[-2000:]
        text = "\n".join([error_message, stdout_tail, stderr_tail]).lower()
        quota_exhausted = ("最大查询限制" in text) or ("query limit" in text) or ("超过了每日最大查询限制" in text)
    after = _sync_progress_snapshot(target_db)
    try:
        from quant.stock_data.jq_client import JqClient

        jq = JqClient()
        q_after = jq.query_count()
        jq.logout()
    except Exception:
        pass

    report = {
        "config_path": config_path,
        "command": cmd,
        "quota_before": q_before,
        "quota_after": q_after,
        "before": before,
        "after": after,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "quota_exhausted": quota_exhausted,
        "error_message": error_message,
        "status": "ok" if not error_message else ("quota_blocked" if quota_exhausted else "failed"),
    }
    Path("output/jq_bulk_sync_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [
        "# JQ Bulk Sync Report",
        "",
        f"- status: {report['status']}",
        f"- db: `{target_db}`",
        f"- benchmark_index: `{cfg.data.benchmark_index}`",
        f"- quota_exhausted: {quota_exhausted}",
        f"- quota_before: {q_before}",
        f"- quota_after: {q_after}",
        "",
        "## Progress Before",
        f"- {before}",
        "",
        "## Progress After",
        f"- {after}",
    ]
    if error_message:
        md += ["", "## Error", f"- {error_message}"]
    Path("reports/jq_bulk_sync_report.md").write_text("\n".join(md), encoding="utf-8")
    return report
