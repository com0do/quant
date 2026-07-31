from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _today_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class IndexConstituentsDB:
    db_path: str

    def __post_init__(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=60.0)

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS constituent_snapshot_compact(
                    index_code TEXT NOT NULL,
                    in_date TEXT NOT NULL,
                    codes_json TEXT NOT NULL,
                    member_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(index_code, in_date)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_constituent_compact_lookup ON constituent_snapshot_compact(index_code, in_date)")

    @staticmethod
    def _normalize_codes(raw: object) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            arr = raw
        else:
            try:
                arr = json.loads(str(raw))
            except Exception:
                arr = []
        if not isinstance(arr, list):
            return []
        return sorted({str(x).strip() for x in arr if str(x).strip()})

    def upsert_snapshot(self, index_code: str, asof_date: str, codes: list[str]) -> int:
        idx = str(index_code).strip()
        dt = str(asof_date).strip()
        clean_codes = sorted({str(x).strip() for x in codes if str(x).strip()})
        if not idx or not dt or not clean_codes:
            return 0
        with self._conn() as conn:
            # Compare against nearest historical snapshot at/before asof_date.
            prev_row = conn.execute(
                """
                SELECT in_date, codes_json
                FROM constituent_snapshot_compact
                WHERE index_code = ? AND in_date <= ?
                ORDER BY in_date DESC
                LIMIT 1
                """,
                (idx, dt),
            ).fetchone()
            if prev_row:
                prev_codes = self._normalize_codes(prev_row[1])
                if prev_codes == clean_codes:
                    return 0
            else:
                # If inserting before earliest snapshot and composition equals that earliest one, skip.
                next_row = conn.execute(
                    """
                    SELECT in_date, codes_json
                    FROM constituent_snapshot_compact
                    WHERE index_code = ? AND in_date > ?
                    ORDER BY in_date ASC
                    LIMIT 1
                    """,
                    (idx, dt),
                ).fetchone()
                if next_row:
                    next_codes = self._normalize_codes(next_row[1])
                    if next_codes == clean_codes:
                        return 0
            conn.execute(
                """
                INSERT OR REPLACE INTO constituent_snapshot_compact(index_code, in_date, codes_json, member_count, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (idx, dt, json.dumps(clean_codes, ensure_ascii=False), len(clean_codes), _today_ts()),
            )
        return len(clean_codes)

    def compact_snapshots(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        with self._conn() as conn:
            indexes = [str(r[0]) for r in conn.execute("SELECT DISTINCT index_code FROM constituent_snapshot_compact ORDER BY index_code").fetchall()]
            for idx in indexes:
                rows = conn.execute(
                    """
                    SELECT in_date, codes_json
                    FROM constituent_snapshot_compact
                    WHERE index_code = ?
                    ORDER BY in_date
                    """,
                    (idx,),
                ).fetchall()
                keep_dates: list[str] = []
                prev_codes: list[str] | None = None
                for dt, raw in rows:
                    cur_codes = self._normalize_codes(raw)
                    if prev_codes is None or cur_codes != prev_codes:
                        keep_dates.append(str(dt))
                        prev_codes = cur_codes
                drop_dates = [str(dt) for dt, _ in rows if str(dt) not in set(keep_dates)]
                if drop_dates:
                    conn.executemany(
                        "DELETE FROM constituent_snapshot_compact WHERE index_code=? AND in_date=?",
                        [(idx, d) for d in drop_dates],
                    )
                out[idx] = {
                    "before_dates": len(rows),
                    "after_dates": len(keep_dates),
                    "dropped_dates": len(drop_dates),
                }
            conn.commit()
        return out

    def get_codes(self, index_code: str, asof_date: str) -> list[str]:
        idx = str(index_code).strip()
        dt = str(asof_date).strip()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT codes_json
                FROM constituent_snapshot_compact
                WHERE index_code = ? AND in_date <= ?
                ORDER BY in_date DESC
                LIMIT 1
                """,
                (idx, dt),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT codes_json
                    FROM constituent_snapshot_compact
                    WHERE index_code = ?
                    ORDER BY in_date ASC
                    LIMIT 1
                    """,
                    (idx,),
                ).fetchone()
        return self._normalize_codes(row[0]) if row else []

    def get_codes_union(self, index_codes: list[str], start_date: str, end_date: str) -> set[str]:
        out: set[str] = set()
        s = str(start_date)
        e = str(end_date)
        with self._conn() as conn:
            for idx in [str(x).strip() for x in index_codes if str(x).strip()]:
                rows = conn.execute(
                    """
                    SELECT in_date, codes_json
                    FROM constituent_snapshot_compact
                    WHERE index_code = ? AND in_date <= ?
                    ORDER BY in_date
                    """,
                    (idx, e),
                ).fetchall()
                if not rows:
                    # If no snapshot exists before end_date, fall back to earliest available snapshot.
                    row0 = conn.execute(
                        """
                        SELECT in_date, codes_json
                        FROM constituent_snapshot_compact
                        WHERE index_code = ?
                        ORDER BY in_date ASC
                        LIMIT 1
                        """,
                        (idx,),
                    ).fetchone()
                    if row0:
                        rows = [(str(row0[0]), str(row0[1]))]
                    else:
                        continue
                # include nearest snapshot before start_date as baseline + all snapshots in range
                chosen: list[tuple[str, str]] = []
                baseline = None
                for dt, raw in rows:
                    if str(dt) <= s:
                        baseline = (str(dt), str(raw))
                    if s <= str(dt) <= e:
                        chosen.append((str(dt), str(raw)))
                if baseline is not None:
                    chosen.insert(0, baseline)
                elif not chosen and rows:
                    # rows may come from earliest fallback with in_date > end_date;
                    # still use it as an approximation to avoid empty union.
                    dt0, raw0 = rows[0]
                    chosen.append((str(dt0), str(raw0)))
                for _, raw in chosen:
                    out.update(self._normalize_codes(raw))
        return out

    def get_indexes_for_code(self, code: str, asof_date: str) -> list[str]:
        target = str(code).strip()
        dt = str(asof_date).strip()
        out: list[str] = []
        with self._conn() as conn:
            indexes = [str(r[0]) for r in conn.execute("SELECT DISTINCT index_code FROM constituent_snapshot_compact ORDER BY index_code").fetchall()]
            for idx in indexes:
                row = conn.execute(
                    """
                    SELECT codes_json
                    FROM constituent_snapshot_compact
                    WHERE index_code = ? AND in_date <= ?
                    ORDER BY in_date DESC
                    LIMIT 1
                    """,
                    (idx, dt),
                ).fetchone()
                if not row:
                    row = conn.execute(
                        """
                        SELECT codes_json
                        FROM constituent_snapshot_compact
                        WHERE index_code = ?
                        ORDER BY in_date ASC
                        LIMIT 1
                        """,
                        (idx,),
                    ).fetchone()
                if row and target in set(self._normalize_codes(row[0])):
                    out.append(idx)
        return sorted(out)

