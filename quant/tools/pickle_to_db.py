from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd


def run_pickle_to_db(input_dir: str = "data", output_db: str = "output/market_cache.db") -> str:
    Path(output_db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output_db)
    mapping = {
        "prefetch_price_panel.pkl": "prefetch_price_panel",
        "prefetch_fund_snapshot.pkl": "prefetch_fund_snapshot",
        "prefetch_factor_snapshot.pkl": "prefetch_factor_snapshot",
        "prefetch_benchmark.pkl": "prefetch_benchmark",
    }
    for name, table in mapping.items():
        p = Path(input_dir) / name
        if not p.exists():
            continue
        df = pd.read_pickle(p)
        if isinstance(df, pd.Series):
            df = df.to_frame().reset_index()
        df.to_sql(table, conn, if_exists="replace", index=False)
    conn.close()
    return output_db
