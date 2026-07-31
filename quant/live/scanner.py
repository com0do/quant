from __future__ import annotations

import pandas as pd

from quant.config import AppConfig
from quant.live.signal_engine import generate_live_scores
from quant.stock_data.data_service import DataService


def run_preopen_scan(data: DataService, cfg: AppConfig, asof: pd.Timestamp) -> list[str]:
    universe = data.get_index_stocks(cfg.data.benchmark_index, asof.strftime("%Y-%m-%d"))
    buy, _ = generate_live_scores(data, cfg, asof=asof, universe=universe, held_codes=[])
    return buy.head(cfg.strategy.top_k).index.astype(str).tolist() if not buy.empty else []
