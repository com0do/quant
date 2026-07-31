from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

SQLITE_IN_LIMIT = 800


class MarketDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=60.0)

    @staticmethod
    def _chunk(items: Iterable[str], size: int = SQLITE_IN_LIMIT) -> list[list[str]]:
        arr = list(items)
        if not arr:
            return []
        return [arr[i : i + size] for i in range(0, len(arr), size)]

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prices_daily(
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    money REAL,
                    paused REAL,
                    high_limit REAL,
                    low_limit REAL,
                    avg REAL,
                    pre_close REAL,
                    PRIMARY KEY(date, code)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fundamentals_snapshot(
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    pe_ratio REAL,
                    pb_ratio REAL,
                    turnover_ratio REAL,
                    PRIMARY KEY(date, code)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS factors_snapshot(
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    roe REAL,
                    market_cap REAL,
                    pe_ratio REAL,
                    pb_ratio REAL,
                    turnover_ratio REAL,
                    PRIMARY KEY(date, code)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_progress(
                    code TEXT PRIMARY KEY,
                    next_date TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                )
                """
            )

    def read_price_panel(self, codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame(columns=["date", "code", "open", "high", "low", "close", "volume", "money", "paused"])
        out: list[pd.DataFrame] = []
        with self._conn() as conn:
            for chunk in self._chunk(codes):
                q = ",".join("?" for _ in chunk)
                sql = f"""
                    SELECT date, code, open, high, low, close, volume, money, paused
                    FROM prices_daily
                    WHERE code IN ({q}) AND date >= ? AND date <= ?
                    ORDER BY date, code
                """
                out.append(pd.read_sql_query(sql, conn, params=[*chunk, start_date, end_date]))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def read_latest_fundamentals(self, codes: list[str], asof_date: str) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame(columns=["date", "code", "pe_ratio", "pb_ratio", "turnover_ratio"])
        out: list[pd.DataFrame] = []
        with self._conn() as conn:
            for chunk in self._chunk(codes):
                q = ",".join("?" for _ in chunk)
                sql = f"""
                    SELECT f.*
                    FROM fundamentals_snapshot f
                    JOIN (
                        SELECT code, MAX(date) AS date
                        FROM fundamentals_snapshot
                        WHERE code IN ({q}) AND date <= ?
                        GROUP BY code
                    ) t ON f.code=t.code AND f.date=t.date
                """
                out.append(pd.read_sql_query(sql, conn, params=[*chunk, asof_date]))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def read_latest_factors(self, codes: list[str], asof_date: str) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame(columns=["date", "code", "roe", "market_cap", "pe_ratio", "pb_ratio", "turnover_ratio"])
        out: list[pd.DataFrame] = []
        with self._conn() as conn:
            for chunk in self._chunk(codes):
                q = ",".join("?" for _ in chunk)
                sql = f"""
                    SELECT f.*
                    FROM factors_snapshot f
                    JOIN (
                        SELECT code, MAX(date) AS date
                        FROM factors_snapshot
                        WHERE code IN ({q}) AND date <= ?
                        GROUP BY code
                    ) t ON f.code=t.code AND f.date=t.date
                """
                out.append(pd.read_sql_query(sql, conn, params=[*chunk, asof_date]))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def read_sync_progress(self, codes: list[str]) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame(columns=["code", "next_date", "done", "updated_at", "last_error"])
        out: list[pd.DataFrame] = []
        with self._conn() as conn:
            for chunk in self._chunk(codes):
                q = ",".join("?" for _ in chunk)
                sql = f"SELECT code, next_date, done, updated_at, last_error FROM sync_progress WHERE code IN ({q})"
                out.append(pd.read_sql_query(sql, conn, params=chunk))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
