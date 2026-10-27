"""SQLite store for point-in-time news, announcements, limit-up themes and fundamentals.

The store is a data layer only: it knows nothing about strategies. The default path is `data/news/news.db`
(gitignored); fetchers under `research/news_selector/` write it, and selectors read it through this class.

Tables:
  announcements  exchange/Eastmoney announcement titles by publication date
  policy_news    CCTV news transcripts by date (policy / macro themes)
  limit_up       Tonghuashun limit-up pool with the editors' reason tags
  market_news    Eastmoney / CLS / THS news flashes (recent-only sources)
  stock_news     per-stock news items returned by Eastmoney
  fundamentals   quarterly financial indicators, keyed by report date
  news_signals   optional classifier output (keyword or LLM)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DEFAULT_PATH = Path("data/news/news.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, title TEXT NOT NULL, type TEXT,
    url TEXT, source TEXT, fetched_at TEXT,
    PRIMARY KEY (date, code, title)
);
CREATE INDEX IF NOT EXISTS idx_ann_code_date ON announcements(code, date);

CREATE TABLE IF NOT EXISTS policy_news (
    date TEXT NOT NULL, title TEXT NOT NULL, content TEXT, source TEXT, url TEXT, fetched_at TEXT,
    PRIMARY KEY (date, title)
);
CREATE INDEX IF NOT EXISTS idx_policy_date ON policy_news(date);

CREATE TABLE IF NOT EXISTS limit_up (
    date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, reason TEXT, limit_up_type TEXT,
    high_days INTEGER, turnover_rate REAL, source TEXT, fetched_at TEXT,
    PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_limit_up_date ON limit_up(date);

CREATE TABLE IF NOT EXISTS market_news (
    ts TEXT NOT NULL, title TEXT NOT NULL, content TEXT, source TEXT, url TEXT, stocks TEXT, fetched_at TEXT,
    PRIMARY KEY (ts, title)
);

CREATE TABLE IF NOT EXISTS stock_news (
    ts TEXT NOT NULL, code TEXT NOT NULL, title TEXT NOT NULL, content TEXT, source TEXT, url TEXT, fetched_at TEXT,
    PRIMARY KEY (ts, code, title)
);

CREATE TABLE IF NOT EXISTS fundamentals (
    code TEXT NOT NULL, report_date TEXT NOT NULL, pub_date TEXT NOT NULL,
    roe REAL, revenue_yoy REAL, profit_yoy REAL, gross_margin REAL, debt_ratio REAL,
    eps REAL, bvps REAL, source TEXT, fetched_at TEXT,
    PRIMARY KEY (code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_fund_code_pub ON fundamentals(code, pub_date);

CREATE TABLE IF NOT EXISTS news_signals (
    id TEXT PRIMARY KEY, date TEXT NOT NULL, code TEXT, source TEXT, title TEXT,
    category TEXT, sentiment TEXT, score REAL, themes TEXT, confidence REAL, method TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_signal_code_date ON news_signals(code, date);
"""


class NewsStore:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA)

    def _upsert(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        if not rows:
            return 0
        marks = ",".join("?" for _ in columns)
        sql = f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({marks})"
        with sqlite3.connect(self.path) as conn:
            conn.executemany(sql, rows)
            conn.commit()
        return len(rows)

    def upsert_announcements(self, rows: list[dict]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        return self._upsert("announcements",
                            ["date", "code", "name", "title", "type", "url", "source", "fetched_at"],
                            [(r["date"], r["code"], r.get("name", ""), r["title"], r.get("type", ""),
                              r.get("url", ""), r.get("source", "eastmoney"), r.get("fetched_at", now)) for r in rows])

    def upsert_policy_news(self, rows: list[dict]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        return self._upsert("policy_news", ["date", "title", "content", "source", "url", "fetched_at"],
                            [(r["date"], r["title"], r.get("content", ""), r.get("source", "cctv"),
                              r.get("url", ""), r.get("fetched_at", now)) for r in rows])

    def upsert_limit_up(self, rows: list[dict]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        return self._upsert("limit_up",
                            ["date", "code", "name", "reason", "limit_up_type", "high_days", "turnover_rate",
                             "source", "fetched_at"],
                            [(r["date"], r["code"], r.get("name", ""), r.get("reason", ""),
                              r.get("limit_up_type", ""), r.get("high_days"), r.get("turnover_rate"),
                              r.get("source", "ths"), r.get("fetched_at", now)) for r in rows])

    def upsert_market_news(self, rows: list[dict]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        return self._upsert("market_news", ["ts", "title", "content", "source", "url", "stocks", "fetched_at"],
                            [(r["ts"], r["title"], r.get("content", ""), r.get("source", ""), r.get("url", ""),
                              json.dumps(r.get("stocks", []), ensure_ascii=False), r.get("fetched_at", now))
                             for r in rows])

    def upsert_stock_news(self, rows: list[dict]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        return self._upsert("stock_news", ["ts", "code", "title", "content", "source", "url", "fetched_at"],
                            [(r["ts"], r["code"], r["title"], r.get("content", ""), r.get("source", ""),
                              r.get("url", ""), r.get("fetched_at", now)) for r in rows])

    def upsert_fundamentals(self, rows: list[dict]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        return self._upsert("fundamentals",
                            ["code", "report_date", "pub_date", "roe", "revenue_yoy", "profit_yoy",
                             "gross_margin", "debt_ratio", "eps", "bvps", "source", "fetched_at"],
                            [(r["code"], r["report_date"], r["pub_date"], r.get("roe"), r.get("revenue_yoy"),
                              r.get("profit_yoy"), r.get("gross_margin"), r.get("debt_ratio"),
                              r.get("eps"), r.get("bvps"), r.get("source", ""), r.get("fetched_at", now))
                             for r in rows])

    def upsert_signals(self, rows: list[dict]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        return self._upsert("news_signals",
                            ["id", "date", "code", "source", "title", "category", "sentiment", "score",
                             "themes", "confidence", "method", "created_at"],
                            [(r["id"], r["date"], r.get("code"), r.get("source", ""), r.get("title", ""),
                              r.get("category", "other"), r.get("sentiment", "neutral"), r.get("score", 0.0),
                              json.dumps(r.get("themes", []), ensure_ascii=False), r.get("confidence", 0.0),
                              r.get("method", "rule"), r.get("created_at", now)) for r in rows])

    def _read(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        with sqlite3.connect(self.path) as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def announcements(self, start: str, end: str) -> pd.DataFrame:
        return self._read("SELECT * FROM announcements WHERE date BETWEEN ? AND ? ORDER BY date, code",
                          (start, end))

    def policy_news(self, start: str, end: str) -> pd.DataFrame:
        return self._read("SELECT * FROM policy_news WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))

    def limit_up(self, start: str, end: str) -> pd.DataFrame:
        return self._read("SELECT * FROM limit_up WHERE date BETWEEN ? AND ? ORDER BY date, code", (start, end))

    def market_news(self, start: str = "", end: str = "") -> pd.DataFrame:
        if start and end:
            return self._read("SELECT * FROM market_news WHERE substr(ts,1,10) BETWEEN ? AND ? ORDER BY ts",
                              (start, end))
        return self._read("SELECT * FROM market_news ORDER BY ts")

    def stock_news(self, code: str) -> pd.DataFrame:
        return self._read("SELECT * FROM stock_news WHERE code=? ORDER BY ts", (code,))

    def fundamentals(self, code: str | None = None) -> pd.DataFrame:
        if code:
            return self._read("SELECT * FROM fundamentals WHERE code=? ORDER BY report_date", (code,))
        return self._read("SELECT * FROM fundamentals ORDER BY code, report_date")

    def signals(self, start: str = "", end: str = "") -> pd.DataFrame:
        if start and end:
            return self._read("SELECT * FROM news_signals WHERE date BETWEEN ? AND ? ORDER BY date, code",
                              (start, end))
        return self._read("SELECT * FROM news_signals ORDER BY date, code")

    def counts(self) -> dict[str, int]:
        tables = ("announcements", "policy_news", "limit_up", "market_news", "stock_news",
                  "fundamentals", "news_signals")
        with sqlite3.connect(self.path) as conn:
            return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}
