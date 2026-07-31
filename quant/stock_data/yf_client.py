from __future__ import annotations

import pandas as pd


class YfClient:
    def get_price_daily(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        import yfinance as yf

        df = yf.download(ticker, start=start_date, end=end_date, auto_adjust=False, progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        out = df.reset_index().rename(columns={"Date": "date", "adj close": "adj_close"})
        out.columns = [str(c).lower() for c in out.columns]
        for c in ["date", "open", "high", "low", "close", "volume"]:
            if c not in out.columns:
                out[c] = None
        return out[["date", "open", "high", "low", "close", "volume"]]
