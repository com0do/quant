from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=5.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS idempotency(client_order_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS fills(fill_key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS api_metrics(id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL, latency_ms REAL NOT NULL, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_order(self, order_id: str, payload: dict[str, Any]) -> None:
        if not order_id:
            return
        raw = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """
                    INSERT INTO orders(order_id, payload, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(order_id) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP
                    """,
                    (order_id, raw),
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_idempotency(self, client_order_id: str, payload: dict[str, Any]) -> None:
        if not client_order_id:
            return
        raw = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """
                    INSERT INTO idempotency(client_order_id, payload, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(client_order_id) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP
                    """,
                    (client_order_id, raw),
                )
                conn.commit()
            finally:
                conn.close()

    def get_idempotency(self, client_order_id: str) -> dict[str, Any] | None:
        if not client_order_id:
            return None
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT payload FROM idempotency WHERE client_order_id = ?",
                    (client_order_id,),
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        try:
            payload = json.loads(str(row[0] or "{}"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def load_recent_idempotency(self, limit: int = 5000) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT client_order_id, payload FROM idempotency ORDER BY updated_at DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            finally:
                conn.close()
        for cid, raw in rows:
            try:
                payload = json.loads(str(raw or "{}"))
            except Exception:
                continue
            if isinstance(payload, dict):
                out[str(cid)] = payload
        return out

    def insert_fill(self, fill_key: str, payload: dict[str, Any]) -> None:
        if not fill_key:
            return
        raw = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("INSERT OR IGNORE INTO fills(fill_key, payload) VALUES (?, ?)", (fill_key, raw))
                conn.commit()
            finally:
                conn.close()

    def insert_metric(self, endpoint: str, latency_ms: float, status: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO api_metrics(endpoint, latency_ms, status) VALUES (?, ?, ?)",
                    (endpoint, float(latency_ms), status),
                )
                conn.commit()
            finally:
                conn.close()

