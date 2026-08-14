"""Quant RPC endpoint：Hermes plugin 通过 subprocess 调这里，跑本仓库 Py 3.12 的代码。

Usage:
    uv run python -m quant.ml.hermes.rpc <cmd> <json_args>

- <cmd>：get_kbars / get_fundamentals / get_announcements / compute_factor / run_backtest_slice
- <json_args>：一个 JSON object 字符串

输出：一行 JSON 到 stdout。错误统一形如 {"error": ...}，返回码非 0。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _resolve_db_path() -> str:
    """按顺序：config → runtime db → market_cache_csi1000_1y → market_cache。"""
    try:
        from quant.config import load_config  # 允许失败：config 可能没准备好
        cfg = load_config()
        for candidate in (cfg.data.runtime_csi1000_db_path, cfg.data.sqlite_db_path):
            if candidate and Path(candidate).exists():
                return candidate
    except Exception:
        pass
    for candidate in (
        "data/runtime/market_csi1000_latest.db",
        "data/market_cache_csi1000_1y.db",
        "data/market_cache.db",
    ):
        if Path(candidate).exists():
            return candidate
    return "data/market_cache.db"


def _sqlite_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def get_kbars(code: str, start_date: str, end_date: str, limit: int = 500) -> dict[str, Any]:
    db_path = _resolve_db_path()
    if not Path(db_path).exists():
        return {"error": f"db not found: {db_path}"}
    with _sqlite_ro(db_path) as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, money "
            "FROM prices_daily WHERE code=? AND date>=? AND date<=? "
            "ORDER BY date ASC LIMIT ?",
            (code, start_date, end_date, int(limit)),
        ).fetchall()
    return {
        "code": code,
        "db": db_path,
        "rows": [
            {"date": r[0], "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "volume": r[5], "money": r[6]}
            for r in rows
        ],
    }


def get_fundamentals(code: str, date: str) -> dict[str, Any]:
    db_path = _resolve_db_path()
    if not Path(db_path).exists():
        return {"error": f"db not found: {db_path}"}
    with _sqlite_ro(db_path) as conn:
        row = conn.execute(
            "SELECT date, roe, market_cap, pe_ratio, pb_ratio, turnover_ratio "
            "FROM factors_snapshot WHERE code=? AND date<=? "
            "ORDER BY date DESC LIMIT 1",
            (code, date),
        ).fetchone()
    if not row:
        return {"code": code, "date": date, "found": False}
    return {
        "code": code,
        "asof_date": row[0],
        "roe": row[1],
        "market_cap": row[2],
        "pe_ratio": row[3],
        "pb_ratio": row[4],
        "turnover_ratio": row[5],
    }


def get_announcements(code: str, start_date: str, end_date: str, limit: int = 20) -> dict[str, Any]:
    nlp_db = "data/nlp/signals.db"
    if not Path(nlp_db).exists():
        return {
            "error": f"nlp signals db not found: {nlp_db}",
            "hint": "run `main.py --mode nlp-analyze` first to populate it",
        }
    with _sqlite_ro(nlp_db) as conn:
        rows = conn.execute(
            "SELECT date, title, category, sentiment, sentiment_score, "
            "revenue_yoy, net_profit_yoy, risk_flags, summary, confidence "
            "FROM nlp_signals WHERE code=? AND date>=? AND date<=? "
            "ORDER BY date DESC LIMIT ?",
            (code, start_date, end_date, int(limit)),
        ).fetchall()
    return {
        "code": code,
        "signals": [
            {
                "date": r[0], "title": r[1], "category": r[2],
                "sentiment": r[3], "sentiment_score": r[4],
                "revenue_yoy": r[5], "net_profit_yoy": r[6],
                "risk_flags": json.loads(r[7] or "[]"),
                "summary": r[8], "confidence": r[9],
            }
            for r in rows
        ],
    }


_SUPPORTED_FACTORS = {"roe", "market_cap", "pe_ratio", "pb_ratio", "turnover_ratio"}


def compute_factor(code: str, factor_name: str, date: str) -> dict[str, Any]:
    if factor_name not in _SUPPORTED_FACTORS:
        return {"error": f"unknown factor: {factor_name}", "supported": sorted(_SUPPORTED_FACTORS)}
    db_path = _resolve_db_path()
    if not Path(db_path).exists():
        return {"error": f"db not found: {db_path}"}
    with _sqlite_ro(db_path) as conn:
        row = conn.execute(
            f"SELECT date, {factor_name} FROM factors_snapshot "
            "WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
            (code, date),
        ).fetchone()
    if not row:
        return {"code": code, "factor": factor_name, "value": None}
    return {"code": code, "factor": factor_name, "asof_date": row[0], "value": row[1]}


def run_backtest_slice(strategy: str, start_date: str, end_date: str) -> dict[str, Any]:
    """跑一段小范围回测：单策略 + 短窗口。**慢**（可能到 1 分钟）——agent 请节制使用。"""
    try:
        from quant.backtest.engine import run_backtest
        from quant.config import load_config

        cfg = load_config()
        cfg.data.start_date = start_date
        cfg.data.end_date = end_date
        cfg.strategy.strategy_names = [strategy]
        cfg.strategy.strategy_weights = [1.0]
        result = run_backtest(cfg, output_prefix=f"hermes_slice_{strategy}")
        return {
            "strategy": strategy,
            "start_date": start_date,
            "end_date": end_date,
            "metrics": dict(result.metrics),
        }
    except Exception as e:  # noqa: BLE001 — return structured error, don't crash the RPC
        return {"error": f"backtest failed: {type(e).__name__}: {e}"}


HANDLERS = {
    "get_kbars": get_kbars,
    "get_fundamentals": get_fundamentals,
    "get_announcements": get_announcements,
    "compute_factor": compute_factor,
    "run_backtest_slice": run_backtest_slice,
}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def main() -> int:
    if len(sys.argv) < 2:
        _emit({"error": "usage: rpc <cmd> <json_args>", "handlers": sorted(HANDLERS.keys())})
        return 2
    cmd = sys.argv[1]
    raw_args = sys.argv[2] if len(sys.argv) > 2 else "{}"
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as e:
        _emit({"error": f"bad json args: {e}"})
        return 2

    if not isinstance(args, dict):
        _emit({"error": "args must be a JSON object"})
        return 2

    handler = HANDLERS.get(cmd)
    if not handler:
        _emit({"error": f"unknown command: {cmd}", "handlers": sorted(HANDLERS.keys())})
        return 2

    try:
        result = handler(**args)
    except TypeError as e:
        _emit({"error": f"bad args for {cmd}: {e}"})
        return 2
    except Exception as e:  # noqa: BLE001 — return error to caller
        _emit({"error": f"{type(e).__name__}: {e}"})
        return 1

    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
