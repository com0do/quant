from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant.config import AppConfig, resolve_gateway_settings
from quant.stock_data.jq_client import JqClient
from quant.stock_data.qmt_http_market import QmtHttpMarketData
from quant.stock_data.sqlite_client import SqliteClient


@dataclass
class DataService:
    cfg: AppConfig

    def __post_init__(self) -> None:
        self.sqlite = SqliteClient(self.cfg.data.sqlite_db_path)
        if bool(getattr(self.cfg.data, "enable_index_aware_db", False)):
            self.sqlite.configure_index_aware(
                csi300_500_db_path=str(getattr(self.cfg.data, "runtime_csi300_500_db_path", self.cfg.data.sqlite_db_path)),
                csi1000_db_path=str(getattr(self.cfg.data, "runtime_csi1000_db_path", self.cfg.data.sqlite_db_path)),
                csi2000_db_path=str(getattr(self.cfg.data, "runtime_csi2000_db_path", self.cfg.data.sqlite_db_path)),
                constituents_db_path=str(getattr(self.cfg.data, "meta_constituents_db_path", "data/meta/index_constituents.db")),
            )
        self.jq = JqClient()
        self.qmt_market: QmtHttpMarketData | None = None
        quote_source = str(getattr(self.cfg.data, "realtime_quote_source", "daily_close")).lower()
        if quote_source == "qmt_http":
            gs = resolve_gateway_settings(self.cfg)
            if gs.base_url:
                self.qmt_market = QmtHttpMarketData(
                    base_url=gs.base_url,
                    token=gs.token,
                    timeout_sec=gs.timeout_sec,
                    account_id=gs.account_id,
                )

    @property
    def source(self) -> str:
        return self.cfg.data.source

    def get_index_stocks(self, index_code: str, date: str) -> list[str]:
        if self.source == "jq":
            return self.jq.get_index_stocks(index_code, date=date)
        return self.sqlite.get_index_stocks(index_code, date=date)

    def get_index_stocks_union(self, index_codes: list[str], start_date: str, end_date: str) -> set[str]:
        if self.source == "jq":
            out: set[str] = set()
            for idx in index_codes:
                out.update(self.jq.get_index_stocks(idx, date=end_date))
            return out
        return self.sqlite.get_index_stocks_union(index_codes=index_codes, start_date=start_date, end_date=end_date)

    def get_indexes_for_code(self, code: str, date: str) -> list[str]:
        if self.source == "jq":
            return []
        return self.sqlite.get_indexes_for_code(code=code, date=date)

    def get_price_panel_for_index(self, index_code: str, date: str, start_date: str, end_date: str) -> pd.DataFrame:
        if self.source == "jq":
            codes = self.jq.get_index_stocks(index_code, date=date)
            return self.get_price_panel(codes=codes, start_date=start_date, end_date=end_date)
        return self.sqlite.get_price_panel_for_index(index_code=index_code, date=date, start_date=start_date, end_date=end_date)

    def get_index_benchmark_close(self, index_code: str, start_date: str, end_date: str) -> pd.Series:
        if self.source == "jq":
            df = self.jq.get_price_daily(index_code, start_date, end_date)
            if df is None or df.empty:
                return pd.Series(dtype=float)
            x = df.copy()
            if "date" not in x.columns:
                x = x.reset_index().rename(columns={"index": "date"})
            x["date"] = pd.to_datetime(x["date"])
            s = pd.to_numeric(x["close"], errors="coerce")
            s.index = x["date"]
            return s.dropna().sort_index()
        return self.sqlite.get_index_benchmark_close(index_code=index_code, start_date=start_date, end_date=end_date)

    def get_price_panel(self, codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame()
        if self.source == "jq":
            frames = []
            for code in codes:
                df = self.jq.get_price_daily(code, start_date, end_date)
                if df is None or df.empty:
                    continue
                g = df.copy()
                if "date" not in g.columns:
                    g = g.reset_index().rename(columns={"index": "date"})
                g["code"] = code
                frames.append(g[["date", "code", "open", "high", "low", "close", "volume", "money", "paused"]])
            return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return self.sqlite.get_price_panel(codes=codes, start_date=start_date, end_date=end_date)

    def get_latest_fundamentals(self, codes: list[str], asof_date: str) -> pd.DataFrame:
        if self.source == "jq":
            df = self.jq.get_fundamentals_bundle(codes, asof_date)
            if df is None:
                return pd.DataFrame()
            out = df.copy()
            if "code" not in out.columns and len(out.columns) > 0:
                out = out.rename(columns={out.columns[0]: "code"})
            out["date"] = asof_date
            cols = ["date", "code", "pe_ratio", "pb_ratio", "turnover_ratio"]
            for c in cols:
                if c not in out.columns:
                    out[c] = None
            return out[cols]
        return self.sqlite.get_latest_fundamentals(codes, asof_date)

    def get_latest_factors(self, codes: list[str], asof_date: str) -> pd.DataFrame:
        if self.source == "jq":
            df = self.jq.get_fundamentals_bundle(codes, asof_date)
            if df is None:
                return pd.DataFrame()
            out = df.copy()
            out["date"] = asof_date
            cols = ["date", "code", "roe", "market_cap", "pe_ratio", "pb_ratio", "turnover_ratio"]
            for c in cols:
                if c not in out.columns:
                    out[c] = None
            return out[cols]
        return self.sqlite.get_latest_factors(codes, asof_date)

    def get_price_single_minute(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        df = self.jq.get_price_minute(code, start_date, end_date)
        return df if df is not None else pd.DataFrame()

    def get_realtime_last_price(self, code: str) -> float | None:
        if self.qmt_market is None:
            return None
        try:
            return self.qmt_market.get_last_price(code)
        except Exception:
            return None

    def get_realtime_quote(self, code: str) -> dict:
        if self.qmt_market is None:
            return {}
        try:
            q = self.qmt_market.get_quote(code)
            return q if isinstance(q, dict) else {}
        except Exception:
            return {}
