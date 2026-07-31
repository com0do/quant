#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import os
import sqlite3
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Iterator
from urllib.parse import urlparse
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=Warning, module=r"jqdatasdk\.compat\.pickle_compat")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _setup_proxy_from_env() -> None:
    proxy = (
        os.getenv("JQ_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
    )
    if not proxy:
        return
    try:
        import socket
        import socks
    except Exception as exc:
        print(f"[WARN] proxy requested but socks unavailable: {exc}")
        return
    u = urlparse(proxy)
    if not u.hostname or not u.port:
        print(f"[WARN] invalid proxy url: {proxy}")
        return
    scheme = (u.scheme or "http").lower()
    if scheme in ("socks5", "socks5h"):
        ptype = socks.SOCKS5
    elif scheme in ("socks4", "socks4a"):
        ptype = socks.SOCKS4
    else:
        ptype = socks.HTTP
    socks.set_default_proxy(
        ptype,
        addr=u.hostname,
        port=u.port,
        username=u.username,
        password=u.password,
    )
    socket.socket = socks.socksocket
    print(f"[INFO] proxy enabled via {scheme}://{u.hostname}:{u.port}")


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prices_daily (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
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
            PRIMARY KEY (code, date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_progress (
            code TEXT PRIMARY KEY,
            next_date TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            last_error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS index_members (
            index_code TEXT NOT NULL,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            PRIMARY KEY (index_code, date, code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            pe_ratio REAL,
            pb_ratio REAL,
            turnover_ratio REAL,
            PRIMARY KEY (date, code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS factors_snapshot (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            roe REAL,
            market_cap REAL,
            pe_ratio REAL,
            pb_ratio REAL,
            turnover_ratio REAL,
            PRIMARY KEY (date, code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshot_progress (
            date TEXT PRIMARY KEY,
            done INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            last_error TEXT,
            rows_fundamentals INTEGER NOT NULL DEFAULT 0,
            rows_factors INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    # Backward-compatible schema migration for existing databases.
    col_map = {r[1] for r in conn.execute("PRAGMA table_info(sync_progress)").fetchall()}
    if "last_error" not in col_map:
        conn.execute("ALTER TABLE sync_progress ADD COLUMN last_error TEXT")
    price_col_map = {r[1] for r in conn.execute("PRAGMA table_info(prices_daily)").fetchall()}
    for col in ("high_limit", "low_limit", "avg", "pre_close"):
        if col not in price_col_map:
            conn.execute(f"ALTER TABLE prices_daily ADD COLUMN {col} REAL")
    conn.commit()


def _normalize_price_df(df: pd.DataFrame, code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    if "date" not in x.columns:
        x = x.reset_index()
    if "date" not in x.columns:
        if "time" in x.columns:
            x = x.rename(columns={"time": "date"})
        elif "index" in x.columns:
            x = x.rename(columns={"index": "date"})
    if "date" not in x.columns and len(x.columns) > 0:
        # Fallback: first column is often datetime index after reset.
        x = x.rename(columns={x.columns[0]: "date"})
    if "date" not in x.columns:
        return pd.DataFrame()
    x["date"] = pd.to_datetime(x["date"]).dt.strftime("%Y-%m-%d")
    x["code"] = code
    cols = [
        "code",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "money",
        "paused",
        "high_limit",
        "low_limit",
        "avg",
        "pre_close",
    ]
    for c in cols:
        if c not in x.columns:
            x[c] = None
    return x[cols]


def _upsert_prices(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    rows = [
        (
            r.code,
            r.date,
            r.open,
            r.high,
            r.low,
            r.close,
            r.volume,
            r.money,
            r.paused,
            r.high_limit,
            r.low_limit,
            r.avg,
            r.pre_close,
        )
        for r in df.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO prices_daily (
            code, date, open, high, low, close, volume, money, paused,
            high_limit, low_limit, avg, pre_close
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _upsert_index_members(conn: sqlite3.Connection, index_code: str, date: str, members: list[str]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO index_members(index_code, date, code)
        VALUES(?, ?, ?)
        """,
        [(index_code, date, c) for c in members],
    )


def _upsert_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(meta)").fetchall()}
    if "updated_at" in cols:
        conn.execute(
            """
            INSERT OR REPLACE INTO meta(key, value, updated_at)
            VALUES(?, ?, ?)
            """,
            (key, value, _today_str()),
        )
    else:
        conn.execute(
            """
            INSERT OR REPLACE INTO meta(key, value)
            VALUES(?, ?)
            """,
            (key, value),
        )


def _chunk(items: list[str], size: int) -> Iterator[list[str]]:
    if size <= 0:
        size = 300
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _as_float_or_none(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except Exception:
        return None


def _upsert_fundamental_rows(conn: sqlite3.Connection, date: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    rows: list[tuple[object, ...]] = []
    for r in df.itertuples(index=False):
        code = getattr(r, "code", None)
        if not code:
            continue
        rows.append(
            (
                date,
                str(code),
                _as_float_or_none(getattr(r, "pe_ratio", None)),
                _as_float_or_none(getattr(r, "pb_ratio", None)),
                _as_float_or_none(getattr(r, "turnover_ratio", None)),
            )
        )
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO fundamentals_snapshot(
            date, code, pe_ratio, pb_ratio, turnover_ratio
        ) VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _upsert_factor_rows(conn: sqlite3.Connection, date: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    rows: list[tuple[object, ...]] = []
    for r in df.itertuples(index=False):
        code = getattr(r, "code", None)
        if not code:
            continue
        rows.append(
            (
                date,
                str(code),
                _as_float_or_none(getattr(r, "roe", None)),
                _as_float_or_none(getattr(r, "market_cap", None)),
                _as_float_or_none(getattr(r, "pe_ratio", None)),
                _as_float_or_none(getattr(r, "pb_ratio", None)),
                _as_float_or_none(getattr(r, "turnover_ratio", None)),
            )
        )
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO factors_snapshot(
            date, code, roe, market_cap, pe_ratio, pb_ratio, turnover_ratio
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _ds(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


@dataclass
class Args:
    index_code: str
    start_date: str
    end_date: str
    db_path: str
    pause_sec: float
    max_codes: int
    sync_snapshots: bool
    only_snapshots: bool
    snapshot_chunk_size: int
    constituents_db: str


def _parse_args() -> Args:
    p = argparse.ArgumentParser(description="Sync JQ index members daily prices to SQLite")
    p.add_argument("--index-code", required=True, help="e.g. 000932.XSHG for CSI2000")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--db-path", required=True, help="sqlite db path")
    p.add_argument("--pause-sec", type=float, default=0.05, help="sleep between symbols")
    p.add_argument("--max-codes", type=int, default=0, help="0 means no limit")
    p.add_argument("--sync-snapshots", action="store_true", help="also sync fundamentals/factors snapshots")
    p.add_argument("--only-snapshots", action="store_true", help="skip price sync, only sync fundamentals/factors")
    p.add_argument("--snapshot-chunk-size", type=int, default=300, help="code chunk size for fundamentals query")
    p.add_argument(
        "--constituents-db",
        default="data/meta/index_constituents.db",
        help="Meta DB path for index constituent intervals.",
    )
    a = p.parse_args()
    return Args(
        index_code=a.index_code,
        start_date=a.start_date,
        end_date=a.end_date,
        db_path=a.db_path,
        pause_sec=max(0.0, float(a.pause_sec)),
        max_codes=max(0, int(a.max_codes)),
        sync_snapshots=bool(a.sync_snapshots),
        only_snapshots=bool(a.only_snapshots),
        snapshot_chunk_size=max(50, int(a.snapshot_chunk_size)),
        constituents_db=str(a.constituents_db),
    )


def main() -> int:
    args = _parse_args()
    _setup_proxy_from_env()

    name = os.getenv("CC_JQ_NAME")
    pw = os.getenv("CC_JQ_PW")
    if not name or not pw:
        print("[ERR] missing CC_JQ_NAME / CC_JQ_PW")
        return 2

    import jqdatasdk as jq

    logged_in = False

    def _safe_logout() -> None:
        nonlocal logged_in
        if not logged_in:
            return
        try:
            jq.logout()
            print("[INFO] jq logout ok")
        except Exception as exc:
            print(f"[WARN] jq logout failed: {exc}")
        logged_in = False

    atexit.register(_safe_logout)

    print(f"[INFO] jq auth start for index {args.index_code}")
    jq.auth(name, pw)
    logged_in = True
    print("[INFO] jq auth ok")

    os.makedirs(os.path.dirname(args.db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(args.db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _init_db(conn)

    _upsert_meta(conn, "index_code", args.index_code)
    _upsert_meta(conn, "start_date", args.start_date)
    _upsert_meta(conn, "end_date", args.end_date)
    conn.commit()

    members = jq.get_index_stocks(args.index_code, date=args.end_date) or []
    if not members:
        print(f"[ERR] no index members from {args.index_code}")
        return 3
    print(f"[INFO] members={len(members)}")
    try:
        from quant.stock_data.index_constituents import IndexConstituentsDB

        cdb = IndexConstituentsDB(args.constituents_db)
        n = cdb.upsert_snapshot(index_code=args.index_code, asof_date=args.end_date, codes=list(members))
        print(f"[INFO] constituents synced: db={args.constituents_db} rows={n}")
    except Exception as exc:
        print(f"[WARN] skip constituents sync: {exc}")

    now = _today_str()
    conn.executemany(
        """
        INSERT OR IGNORE INTO sync_progress(code, next_date, done, updated_at, last_error)
        VALUES(?, ?, 0, ?, NULL)
        """,
        [(c, args.start_date, now) for c in members],
    )
    conn.commit()
    _upsert_index_members(conn, args.index_code, args.end_date, members)
    conn.commit()

    if not args.only_snapshots:
        todo = conn.execute(
            """
            SELECT code, next_date, done
            FROM sync_progress
            ORDER BY code
            """
        ).fetchall()
        total = len(todo)
        done_count = 0
        row_count = 0
        calls = 0

        for i, (code, next_date, done) in enumerate(todo, start=1):
            if args.max_codes and i > args.max_codes:
                break
            if int(done) == 1:
                done_count += 1
                continue
            start = max(_d(next_date), _d(args.start_date))
            end = _d(args.end_date)
            if start > end:
                conn.execute(
                    "UPDATE sync_progress SET done=1, updated_at=?, last_error=NULL WHERE code=?",
                    (_today_str(), code),
                )
                conn.commit()
                done_count += 1
                continue
            try:
                df = jq.get_price(
                    security=code,
                    start_date=_ds(start),
                    end_date=args.end_date,
                    frequency="daily",
                    fields=[
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "money",
                        "paused",
                        "high_limit",
                        "low_limit",
                        "avg",
                        "pre_close",
                    ],
                    fq="post",
                    skip_paused=False,
                    fill_paused=False,
                    panel=False,
                )
                calls += 1
                ndf = _normalize_price_df(df, code)
                n = _upsert_prices(conn, ndf)
                row_count += n
                if n > 0:
                    max_dt = ndf["date"].max()
                    nxt = _ds(_d(max_dt) + timedelta(days=1))
                else:
                    nxt = _ds(end + timedelta(days=1))
                is_done = 1 if _d(nxt) > end else 0
                conn.execute(
                    """
                    UPDATE sync_progress
                    SET next_date=?, done=?, updated_at=?, last_error=NULL
                    WHERE code=?
                    """,
                    (nxt, is_done, _today_str(), code),
                )
                conn.commit()
                done_count += int(is_done)
                if i % 20 == 0 or n > 0:
                    print(
                        f"[SYNC:PRICE] {i}/{total} code={code} inserted={n} "
                        f"done={done_count} calls={calls} rows={row_count}"
                    )
                if args.pause_sec > 0:
                    time.sleep(args.pause_sec)
            except KeyboardInterrupt:
                print("[WARN] interrupted by user, checkpoint saved")
                conn.commit()
                return 130
            except Exception as exc:
                conn.execute(
                    "UPDATE sync_progress SET updated_at=?, last_error=? WHERE code=?",
                    (_today_str(), str(exc)[:1000], code),
                )
                conn.commit()
                print(f"[WARN] code={code} failed: {exc}")
                time.sleep(max(0.2, args.pause_sec))

    if args.sync_snapshots or args.only_snapshots:
        try:
            from jqdatasdk import indicator, query, valuation
        except Exception as exc:
            print(f"[ERR] import jqdatasdk query api failed: {exc}")
            return 4

        dates = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT date
                FROM prices_daily
                WHERE date >= ? AND date <= ?
                ORDER BY date
                """,
                (args.start_date, args.end_date),
            ).fetchall()
        ]
        if not dates:
            print("[WARN] no price dates found; skip snapshot sync")
        else:
            conn.executemany(
                """
                INSERT OR IGNORE INTO snapshot_progress(date, done, updated_at, last_error, rows_fundamentals, rows_factors)
                VALUES(?, 0, ?, NULL, 0, 0)
                """,
                [(d, _today_str()) for d in dates],
            )
            conn.commit()
            progress = {
                d: int(done)
                for d, done in conn.execute(
                    "SELECT date, done FROM snapshot_progress WHERE date >= ? AND date <= ?",
                    (args.start_date, args.end_date),
                ).fetchall()
            }
            total_dates = len(dates)
            done_dates = 0
            for idx, d in enumerate(dates, start=1):
                if progress.get(d, 0) == 1:
                    done_dates += 1
                    continue
                try:
                    frames: list[pd.DataFrame] = []
                    for codes in _chunk(members, args.snapshot_chunk_size):
                        q = query(
                            valuation.code,
                            valuation.pe_ratio,
                            valuation.pb_ratio,
                            valuation.turnover_ratio,
                            valuation.market_cap,
                            indicator.roe,
                        ).filter(valuation.code.in_(codes))
                        sdf = jq.get_fundamentals(q, date=d)
                        if sdf is not None and not sdf.empty:
                            frames.append(sdf)
                        if args.pause_sec > 0:
                            time.sleep(min(0.05, args.pause_sec))
                    if frames:
                        all_df = pd.concat(frames, ignore_index=True)
                        all_df = all_df.drop_duplicates(subset=["code"], keep="last")
                    else:
                        all_df = pd.DataFrame(columns=["code", "pe_ratio", "pb_ratio", "turnover_ratio", "market_cap", "roe"])
                    nfund = _upsert_fundamental_rows(conn, d, all_df)
                    nfac = _upsert_factor_rows(conn, d, all_df)
                    conn.execute(
                        """
                        UPDATE snapshot_progress
                        SET done=1, updated_at=?, last_error=NULL, rows_fundamentals=?, rows_factors=?
                        WHERE date=?
                        """,
                        (_today_str(), nfund, nfac, d),
                    )
                    conn.commit()
                    done_dates += 1
                    if idx % 5 == 0 or nfund > 0 or nfac > 0:
                        print(
                            f"[SYNC:SNAPSHOT] {idx}/{total_dates} date={d} "
                            f"fund={nfund} fac={nfac} done_dates={done_dates}"
                        )
                except KeyboardInterrupt:
                    print("[WARN] interrupted by user, snapshot checkpoint saved")
                    conn.commit()
                    return 130
                except Exception as exc:
                    conn.execute(
                        """
                        UPDATE snapshot_progress
                        SET updated_at=?, last_error=?
                        WHERE date=?
                        """,
                        (_today_str(), str(exc)[:1000], d),
                    )
                    conn.commit()
                    print(f"[WARN] snapshot date={d} failed: {exc}")
                    time.sleep(max(0.2, args.pause_sec))

    left = conn.execute("SELECT COUNT(*) FROM sync_progress WHERE done=0").fetchone()[0]
    total_rows = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
    left_snap = conn.execute("SELECT COUNT(*) FROM snapshot_progress WHERE done=0").fetchone()[0]
    nfund_total = conn.execute("SELECT COUNT(*) FROM fundamentals_snapshot").fetchone()[0]
    nfac_total = conn.execute("SELECT COUNT(*) FROM factors_snapshot").fetchone()[0]
    print(
        f"[DONE] left_price={left}, left_snapshot={left_snap}, prices_total={total_rows}, "
        f"fund_total={nfund_total}, fac_total={nfac_total}"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
