from __future__ import annotations

from dataclasses import dataclass
import sqlite3

import pandas as pd

from quant.stock_data.index_constituents import IndexConstituentsDB
from quant.stock_data.market_db import MarketDB


@dataclass
class SqliteClient:
    db_path: str

    def __post_init__(self) -> None:
        self.db = MarketDB(self.db_path)
        self._db_map: dict[str, str] = {}
        self._all_dbs: list[MarketDB] = [self.db]
        self._constituents: IndexConstituentsDB | None = None

    def configure_index_aware(
        self,
        *,
        csi300_500_db_path: str,
        csi1000_db_path: str,
        csi2000_db_path: str,
        constituents_db_path: str,
    ) -> None:
        self._db_map = {
            "000300.XSHG": csi300_500_db_path,
            "000905.XSHG": csi300_500_db_path,
            "000852.XSHG": csi1000_db_path,
            "399303.XSHE": csi2000_db_path,
        }
        uniq = sorted({str(x).strip() for x in self._db_map.values() if str(x).strip()})
        self._all_dbs = [MarketDB(p) for p in uniq] if uniq else [self.db]
        self._constituents = IndexConstituentsDB(constituents_db_path)

    def _pick_market_db(self, index_code: str) -> MarketDB:
        p = self._db_map.get(str(index_code), "")
        return MarketDB(p) if p else self.db

    def get_codes_active_in_index(self, index_code: str, date: str) -> set[str]:
        return set(self.get_index_stocks(index_code=index_code, date=date))

    def get_indexes_for_code(self, code: str, date: str) -> list[str]:
        if self._constituents is None:
            return []
        return self._constituents.get_indexes_for_code(code=code, asof_date=date)

    def get_index_stocks(self, index_code: str, date: str) -> list[str]:
        if self._constituents is not None:
            codes = self._constituents.get_codes(index_code=index_code, asof_date=date)
            if codes:
                return codes
        # Primary: index_members table.
        sql = """
            SELECT DISTINCT code
            FROM index_members
            WHERE index_code = ? AND date <= ?
            ORDER BY code
        """
        try:
            with self.db._conn() as conn:
                df = pd.read_sql_query(sql, conn, params=(index_code, date))
            if not df.empty:
                return df["code"].astype(str).tolist()
            # Fallback to nearest available snapshot date for this index.
            with self.db._conn() as conn:
                df2 = pd.read_sql_query(
                    """
                    SELECT DISTINCT code
                    FROM index_members
                    WHERE index_code = ?
                      AND date = (
                          SELECT MAX(date) FROM index_members WHERE index_code = ?
                      )
                    ORDER BY code
                    """,
                    conn,
                    params=(index_code, index_code),
                )
            if not df2.empty:
                return df2["code"].astype(str).tolist()
        except Exception:
            pass
        # Fallback: all price codes in range.
        with self.db._conn() as conn:
            df = pd.read_sql_query(
                "SELECT DISTINCT code FROM prices_daily ORDER BY code",
                conn,
            )
        return df["code"].astype(str).tolist()

    def get_index_stocks_union(self, index_codes: list[str], start_date: str, end_date: str) -> set[str]:
        if self._constituents is None:
            out: set[str] = set()
            for idx in index_codes:
                out.update(self.get_index_stocks(index_code=idx, date=end_date))
            return out
        return self._constituents.get_codes_union(index_codes=index_codes, start_date=start_date, end_date=end_date)

    def get_price_panel(self, codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        if len(self._all_dbs) <= 1:
            return self.db.read_price_panel(codes=codes, start_date=start_date, end_date=end_date)
        frames: list[pd.DataFrame] = []
        for mdb in self._all_dbs:
            df = mdb.read_price_panel(codes=codes, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        out = out.sort_values(["date", "code"]).drop_duplicates(subset=["date", "code"], keep="last")
        return out.reset_index(drop=True)

    def get_price_panel_for_index(self, index_code: str, date: str, start_date: str, end_date: str) -> pd.DataFrame:
        codes = self.get_index_stocks(index_code=index_code, date=date)
        mdb = self._pick_market_db(index_code=index_code)
        return mdb.read_price_panel(codes=codes, start_date=start_date, end_date=end_date)

    def get_latest_fundamentals(self, codes: list[str], asof_date: str) -> pd.DataFrame:
        if len(self._all_dbs) <= 1:
            return self.db.read_latest_fundamentals(codes=codes, asof_date=asof_date)
        frames: list[pd.DataFrame] = []
        for mdb in self._all_dbs:
            df = mdb.read_latest_fundamentals(codes=codes, asof_date=asof_date)
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(["date", "code"]).drop_duplicates(subset=["code"], keep="last").reset_index(drop=True)

    def get_latest_factors(self, codes: list[str], asof_date: str) -> pd.DataFrame:
        if len(self._all_dbs) <= 1:
            return self.db.read_latest_factors(codes=codes, asof_date=asof_date)
        frames: list[pd.DataFrame] = []
        for mdb in self._all_dbs:
            df = mdb.read_latest_factors(codes=codes, asof_date=asof_date)
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(["date", "code"]).drop_duplicates(subset=["code"], keep="last").reset_index(drop=True)

    def get_index_benchmark_close(self, index_code: str, start_date: str, end_date: str) -> pd.Series:
        candidates = [self._pick_market_db(index_code=index_code), *self._all_dbs]
        seen: set[str] = set()
        for mdb in candidates:
            if mdb.db_path in seen:
                continue
            seen.add(mdb.db_path)
            with sqlite3.connect(mdb.db_path) as conn:
                try:
                    df = pd.read_sql_query(
                        """
                        SELECT date, close
                        FROM index_prices_daily
                        WHERE code = ? AND date >= ? AND date <= ?
                        ORDER BY date
                        """,
                        conn,
                        params=(index_code, start_date, end_date),
                    )
                except Exception:
                    df = pd.DataFrame()
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            s = pd.to_numeric(df["close"], errors="coerce")
            s.index = df["date"]
            s = s.dropna().sort_index()
            if not s.empty:
                return s
        return pd.Series(dtype=float)
