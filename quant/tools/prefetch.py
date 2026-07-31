from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pandas as pd

from quant.config import load_config
from quant.stock_data.data_service import DataService
from quant.stock_data.market_db import MarketDB


def run_prefetch(config_path: str | None = None) -> dict:
    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    cfg = load_config(config_path=config_path)
    cfg.data.source = "jq"
    out = {
        "status": "failed",
        "config_path": config_path,
        "benchmark_index": cfg.data.benchmark_index,
        "universe_total": 0,
        "prefetch_done": 0,
        "prefetch_total": 0,
        "calls": 0,
        "inserted_rows": 0,
        "prices_daily_rows": 0,
        "errors": 0,
        "quota_exhausted": False,
        "quota_before": {},
        "quota_after": {},
        "db_path": cfg.data.sqlite_db_path,
        "error_message": "",
    }

    try:
        data = DataService(cfg)
        db = MarketDB(cfg.data.sqlite_db_path)
        with db._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prefetch_progress(
                    code TEXT PRIMARY KEY,
                    done INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                )
                """
            )
        universe = data.get_index_stocks(cfg.data.benchmark_index, cfg.data.end_date)
        if cfg.data.prefetch_universe_limit and cfg.data.prefetch_universe_limit > 0:
            universe = universe[: cfg.data.prefetch_universe_limit]
        out["universe_total"] = len(universe)

        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with db._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO prefetch_progress(code, done, updated_at, last_error) VALUES(?,0,?,NULL)",
                [(c, now_s) for c in universe],
            )
        try:
            out["quota_before"] = data.jq.query_count()
        except Exception:
            pass

        max_calls = int(cfg.data.jq_sync_max_calls or 0)
        row_budget = int(cfg.data.jq_sync_daily_row_budget or 0)
        for code in universe:
            with db._conn() as conn:
                row = conn.execute("SELECT done FROM prefetch_progress WHERE code=?", (code,)).fetchone()
            if row and int(row[0]) == 1:
                continue
            if max_calls > 0 and out["calls"] >= max_calls:
                break
            if row_budget > 0 and out["inserted_rows"] >= row_budget:
                break

            try:
                df = data.jq.get_price_daily(code, cfg.data.start_date, cfg.data.end_date)
                out["calls"] += 1
            except Exception as exc:
                msg = str(exc)
                out["errors"] += 1
                if "最大查询限制" in msg or "query" in msg.lower():
                    out["quota_exhausted"] = True
                    out["status"] = "quota_blocked"
                    break
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE prefetch_progress SET updated_at=?, last_error=? WHERE code=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg[:1000], code),
                    )
                continue

            if df is None or df.empty:
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE prefetch_progress SET done=1, updated_at=?, last_error=NULL WHERE code=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), code),
                    )
                continue

            g = df.copy()
            if "date" not in g.columns:
                g = g.reset_index().rename(columns={"index": "date"})
            g["date"] = pd.to_datetime(g["date"]).dt.strftime("%Y-%m-%d")
            g["code"] = code
            cols = ["date", "code", "open", "high", "low", "close", "volume", "money", "paused"]
            for c in cols:
                if c not in g.columns:
                    g[c] = None
            try:
                with db._conn() as conn:
                    g[cols].to_sql("prices_daily", conn, if_exists="append", index=False)
                    conn.execute(
                        "UPDATE prefetch_progress SET done=1, updated_at=?, last_error=NULL WHERE code=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), code),
                    )
                out["inserted_rows"] += len(g)
            except Exception as exc:
                out["errors"] += 1
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE prefetch_progress SET updated_at=?, last_error=? WHERE code=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(exc)[:1000], code),
                    )

        with db._conn() as conn:
            total, done = conn.execute("SELECT COUNT(*), COALESCE(SUM(done),0) FROM prefetch_progress").fetchone()
            rows = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
        out["prefetch_total"] = int(total)
        out["prefetch_done"] = int(done)
        out["prices_daily_rows"] = int(rows)
        if out["status"] != "quota_blocked":
            out["status"] = "ok"
        try:
            out["quota_after"] = data.jq.query_count()
            data.jq.logout()
        except Exception:
            pass
    except Exception as exc:
        msg = str(exc)
        out["error_message"] = msg
        if "最大查询限制" in msg or "query" in msg.lower():
            out["status"] = "quota_blocked"
            out["quota_exhausted"] = True
        else:
            out["status"] = "failed"

    Path("output/prefetch_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("reports/prefetch_report.md").write_text(
        "# Prefetch Report\n\n"
        f"- status: {out['status']}\n"
        f"- db: `{out['db_path']}`\n"
        f"- benchmark_index: `{out['benchmark_index']}`\n"
        f"- prefetch_progress: {out['prefetch_done']}/{out['prefetch_total']}\n"
        f"- calls: {out['calls']}\n"
        f"- inserted_rows: {out['inserted_rows']}\n"
        f"- prices_daily_rows: {out['prices_daily_rows']}\n"
        f"- quota_before: {out['quota_before']}\n"
        f"- quota_after: {out['quota_after']}\n"
        f"- quota_exhausted: {out['quota_exhausted']}\n",
        encoding="utf-8",
    )
    return out
