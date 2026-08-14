"""OpenAI-style JSON schemas for the 5 quant tools exposed to Hermes."""

GET_KBARS = {
    "name": "get_kbars",
    "description": (
        "Fetch daily OHLCV bars for an A-share stock from the local SQLite cache. "
        "Returns date-sorted rows. Cheap. Prefer this over shell/curl for price data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码，如 000001.XSHE / 600519.XSHG"},
            "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            "limit": {"type": "integer", "description": "最多返回多少行（默认 500）", "default": 500},
        },
        "required": ["code", "start_date", "end_date"],
    },
}

GET_FUNDAMENTALS = {
    "name": "get_fundamentals",
    "description": (
        "Look up point-in-time fundamentals (pe / pb / roe / market_cap / turnover) "
        "for a stock as of a date. Returns the closest available snapshot <= that date."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
        },
        "required": ["code", "date"],
    },
}

GET_ANNOUNCEMENTS = {
    "name": "get_announcements",
    "description": (
        "Retrieve structured NLP signals for a stock over a date range. "
        "Source: data/nlp/signals.db, populated by `main.py --mode nlp-analyze`. "
        "Each row includes sentiment, sentiment_score, revenue_yoy, net_profit_yoy, "
        "risk_flags and summary produced by DeepSeek. "
        "If db missing, an error+hint is returned — surface that to the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["code", "start_date", "end_date"],
    },
}

COMPUTE_FACTOR = {
    "name": "compute_factor",
    "description": (
        "Look up a factor value for a stock as of a date. "
        "Supported factors: roe, market_cap, pe_ratio, pb_ratio, turnover_ratio. "
        "For any factor outside this set, use get_kbars + your own reasoning instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "factor_name": {
                "type": "string",
                "enum": ["roe", "market_cap", "pe_ratio", "pb_ratio", "turnover_ratio"],
            },
            "date": {"type": "string", "description": "YYYY-MM-DD"},
        },
        "required": ["code", "factor_name", "date"],
    },
}

RUN_BACKTEST_SLICE = {
    "name": "run_backtest_slice",
    "description": (
        "Run a small-scope backtest: single strategy, short date window. "
        "SLOW — may take up to a minute per call. Use sparingly. "
        "Available strategies live in quant/stock_strategy/registry.py "
        "(e.g. simple_3factor, csi1000_enhanced, gbm_multifactor)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "strategy": {"type": "string"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
        },
        "required": ["strategy", "start_date", "end_date"],
    },
}
