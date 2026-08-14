"""
JoinQuant script: c1 + 小市值 + ETF轮动 组合策略

目标:
1) 将本地高收益思路抽象为 c1 子策略(中证2000偏进攻股池);
2) 融合 jq_horse 的小市值与 ETF 轮动逻辑;
3) 支持三个子策略资金比例可配置。
"""

import math
import datetime
from dataclasses import dataclass
from enum import Enum, unique
import numpy as np
import pandas as pd

from jqdata import *
try:
    from jq_research_redis_common import build_target_value_signal, RedisSignalQueueClient
    _REDIS_HELPER_IMPORT_ERROR = None
except Exception as e:
    build_target_value_signal = None
    RedisSignalQueueClient = None
    _REDIS_HELPER_IMPORT_ERROR = e


# ===== 可调参数 =====
# 通过 ACTIVE_PRESET 一键切换
ACTIVE_PRESET = "balanced"  # conservative / balanced / aggressive
EXEC_MODE = "direct_jq_order"  # direct_jq_order / redis_signal

SIGNAL_QUEUE_CONFIG = {
    # Redis list queue, intended for cloud JoinQuant -> public Redis service.
    "host": "",
    "port": 6379,
    "password": "",
    "db": 0,
    "queue_key": "jq:signals",
    "use_tls": True,
    "timeout_sec": 5,
    "stale_after_sec": 900,
    "price_mode": "latest",  # latest / limit_last
    "hmac_secret": "6wotuan6",
    "strategy_name": "c1_smallcap_etf_combo",
}


@unique
class Leg(str, Enum):
    C1 = "c1"
    SMALLCAP = "smallcap"
    ETF_ROTATION = "etf_rotation"


LEGS = tuple(Leg)


@dataclass(frozen=True)
class StrategyLegConfig:
    leg: Leg
    weight: float
    rebalance_every_n_days: int
    stop_loss_pct: float
    priority: int

    @property
    def record_weight(self):
        return "%s_weight" % self.leg.value

    @property
    def record_return(self):
        return "%s_return" % self.leg.value


PRESET_CONFIGS = {
    "conservative": {
        "leg_weights": (0.35, 0.20, 0.45),
        "leg_rebalance_every_n_days": (5, 7, 0),
        "leg_stop_loss_pct": (0.08, 0.09, 0.05),
        "c1_top_k": 8,
        "c1_max_positions": 4,
        "smallcap_pick_n": 6,
        "smallcap_audit_lookback_years": 3,
        "smallcap_avoid_trade_months": (1, 4),
        "smallcap_enable_dynamic_stock_num": True,
        "smallcap_dynamic_stock_num_ma_days": 10,
        "smallcap_dynamic_stock_num_gap_high": 0.04,
        "smallcap_dynamic_stock_num_gap_mid": -0.04,
        "smallcap_dynamic_stock_num_gap_low": -0.10,
        "smallcap_enable_dynamic_position": False,
        "smallcap_base_position_ratio": 1.0,
        "smallcap_volatility_lookback": 20,
        "smallcap_volatility_threshold_low": 0.015,
        "smallcap_volatility_threshold_high": 0.035,
        "smallcap_position_ratio_min": 0.5,
        "smallcap_position_ratio_max": 1.0,
        "smallcap_enable_top_divergence_pause": True,
        "smallcap_top_divergence_check_days": 10,
        "smallcap_enable_defense_pause": True,
        "smallcap_defense_high_lookback": 60,
        "smallcap_defense_high_proximity": 0.95,
        "smallcap_defense_ma_days": 20,
        "smallcap_defense_sample_size": 120,
        "smallcap_price_cap": 50.0,
        "smallcap_momentum_lookback": 25,
        "smallcap_momentum_score_min": -1.0,
        "smallcap_momentum_score_max": 10.5,
        "smallcap_min_range_position": 0.35,
        "smallcap_min_return_20d": 0.0,
        "etf_lookback_days": 30,
        "etf_momentum_min": -0.2,
        "etf_momentum_max": 4.0,
    },
    "balanced": {
        # 默认平衡档
        "leg_weights": (0.50, 0.25, 0.25),
        "leg_rebalance_every_n_days": (3, 5, 0),
        "leg_stop_loss_pct": (0.07, 0.09, 0.05),
        "c1_top_k": 6,
        "c1_max_positions": 3,
        "smallcap_pick_n": 5,
        "smallcap_audit_lookback_years": 3,
        "smallcap_avoid_trade_months": (1, 4),
        "smallcap_enable_dynamic_stock_num": True,
        "smallcap_dynamic_stock_num_ma_days": 10,
        "smallcap_dynamic_stock_num_gap_high": 0.04,
        "smallcap_dynamic_stock_num_gap_mid": -0.04,
        "smallcap_dynamic_stock_num_gap_low": -0.10,
        "smallcap_enable_dynamic_position": False,
        "smallcap_base_position_ratio": 1.0,
        "smallcap_volatility_lookback": 20,
        "smallcap_volatility_threshold_low": 0.015,
        "smallcap_volatility_threshold_high": 0.035,
        "smallcap_position_ratio_min": 0.5,
        "smallcap_position_ratio_max": 1.0,
        "smallcap_enable_top_divergence_pause": True,
        "smallcap_top_divergence_check_days": 10,
        "smallcap_enable_defense_pause": True,
        "smallcap_defense_high_lookback": 60,
        "smallcap_defense_high_proximity": 0.95,
        "smallcap_defense_ma_days": 20,
        "smallcap_defense_sample_size": 120,
        "smallcap_price_cap": 50.0,
        "smallcap_momentum_lookback": 25,
        "smallcap_momentum_score_min": -1.0,
        "smallcap_momentum_score_max": 10.5,
        "smallcap_min_range_position": 0.35,
        "smallcap_min_return_20d": 0.0,
        "etf_lookback_days": 25,
        "etf_momentum_min": 0.0,
        "etf_momentum_max": 5.0,
    },
    "aggressive": {
        "leg_weights": (0.70, 0.20, 0.10),
        "leg_rebalance_every_n_days": (2, 4, 0),
        "leg_stop_loss_pct": (0.06, 0.08, 0.04),
        "c1_top_k": 6,
        "c1_max_positions": 3,
        "smallcap_pick_n": 5,
        "smallcap_audit_lookback_years": 3,
        "smallcap_avoid_trade_months": (1, 4),
        "smallcap_enable_dynamic_stock_num": True,
        "smallcap_dynamic_stock_num_ma_days": 10,
        "smallcap_dynamic_stock_num_gap_high": 0.04,
        "smallcap_dynamic_stock_num_gap_mid": -0.04,
        "smallcap_dynamic_stock_num_gap_low": -0.10,
        "smallcap_enable_dynamic_position": False,
        "smallcap_base_position_ratio": 1.0,
        "smallcap_volatility_lookback": 20,
        "smallcap_volatility_threshold_low": 0.015,
        "smallcap_volatility_threshold_high": 0.035,
        "smallcap_position_ratio_min": 0.5,
        "smallcap_position_ratio_max": 1.0,
        "smallcap_enable_top_divergence_pause": True,
        "smallcap_top_divergence_check_days": 10,
        "smallcap_enable_defense_pause": True,
        "smallcap_defense_high_lookback": 60,
        "smallcap_defense_high_proximity": 0.95,
        "smallcap_defense_ma_days": 20,
        "smallcap_defense_sample_size": 120,
        "smallcap_price_cap": 50.0,
        "smallcap_momentum_lookback": 25,
        "smallcap_momentum_score_min": -1.0,
        "smallcap_momentum_score_max": 10.5,
        "smallcap_min_range_position": 0.35,
        "smallcap_min_return_20d": 0.0,
        "etf_lookback_days": 20,
        "etf_momentum_min": 0.0,
        "etf_momentum_max": 6.0,
    },
}
CONFIG = PRESET_CONFIGS[ACTIVE_PRESET]


def _build_leg_configs(config):
    weights = config["leg_weights"]
    rebalance_days = config["leg_rebalance_every_n_days"]
    stop_losses = config["leg_stop_loss_pct"]
    if not (len(weights) == len(rebalance_days) == len(stop_losses) == len(LEGS)):
        raise ValueError("leg config arrays must match LEGS length")
    return {
        leg: StrategyLegConfig(
            leg=leg,
            weight=float(weights[i]),
            rebalance_every_n_days=int(rebalance_days[i]),
            stop_loss_pct=float(stop_losses[i]),
            priority=i,
        )
        for i, leg in enumerate(LEGS)
    }


LEG_CONFIGS = _build_leg_configs(CONFIG)
LEG_SELECTORS = {
    Leg.C1: lambda context: _select_c1_targets(context),
    Leg.SMALLCAP: lambda context: _select_smallcap_targets(context),
    Leg.ETF_ROTATION: lambda context: _select_etf_rotation_targets(context),
}


def _fixed_point_anchor_date(context):
    """
    历史价格序列统一使用“当前决策日”为锚点做定点复权。

    这样回测走到某一天时，只使用当日可确定的复权基准，
    不会像普通前复权那样被更后面的分红送转再次改写。
    """
    return context.current_dt.date()


def _get_fixed_point_close_wide(securities, end_dt, count, context):
    """
    获取多标的定点复权日线 close 宽表。

    用法:
    - 历史因子、动量、波动率等“连续价格序列”计算
    - 不用于真实成交价格判断
    """
    data = get_bars(
        securities,
        end_dt=end_dt,
        count=count,
        unit="1d",
        fields=["date", "close"],
        include_now=False,
        fq_ref_date=_fixed_point_anchor_date(context),
        df=True,
    )
    if isinstance(securities, str):
        securities = [securities]
    if data is None or len(data) == 0:
        raise ValueError("get_bars returned empty data in _get_fixed_point_close_wide")

    # 多标的优先走 unstack，避免 reset_index + pivot 的额外开销
    if isinstance(data.index, pd.MultiIndex):
        if not {"date", "close"}.issubset(data.columns):
            raise ValueError("missing required columns {'date','close'} in multi-security get_bars result")
        multi = data.rename_axis(index=["code", "bar_idx"])
        date_stats = multi["date"].groupby(level="bar_idx").agg(["nunique", "first"])
        if (date_stats["nunique"] != 1).any():
            raise ValueError("bar_idx maps to multiple dates; cannot safely align wide table")
        out = multi["close"].unstack(0).reindex(columns=securities)
        date_index = pd.to_datetime(date_stats["first"])
        out.index = date_index
        out.index.name = "date"
        return out.sort_index()

    # 单标的兜底：构造 date 为索引、code 为列名的宽表
    if "close" not in data.columns:
        raise ValueError("missing required column 'close' in single-security get_bars result")
    if "date" in data.columns:
        index = pd.to_datetime(data["date"])
    else:
        index = pd.to_datetime(data.index)
    out = pd.DataFrame({securities[0]: data["close"].to_numpy()}, index=index)
    out.index.name = "date"
    return out.sort_index()


def _get_fixed_point_close_series(security, end_dt, count, context):
    """
    获取单标的定点复权日线 close 序列。

    历史走势计算用定点复权，避免后续 corporate action 反向污染回测。
    """
    data = get_bars(
        security,
        end_dt=end_dt,
        count=count,
        unit="1d",
        fields=["close"],
        include_now=False,
        fq_ref_date=_fixed_point_anchor_date(context),
        df=True,
    )
    if data is None or len(data) == 0:
        return pd.Series(dtype=float)
    if isinstance(data, pd.DataFrame) and "close" in data.columns:
        return data["close"].dropna().astype(float)
    reset_df = data.reset_index()
    if "close" in reset_df.columns:
        return reset_df["close"].dropna().astype(float)
    return pd.Series(dtype=float)


def _push_signal_to_redis(message):
    if RedisSignalQueueClient is None:
        raise RuntimeError(
            "redis helper import failed. Put jq_research_redis_common.py in JoinQuant research path "
            "and ensure redis-py is available. error=%s" % _REDIS_HELPER_IMPORT_ERROR
        )
    cfg = SIGNAL_QUEUE_CONFIG
    if not str(cfg.get("host") or "").strip():
        raise ValueError("redis_signal mode requires SIGNAL_QUEUE_CONFIG['host']")
    client = RedisSignalQueueClient.from_config(cfg)
    client.push_message(message)


def emit_signal(context, leg_name, code, target_value, note=""):
    if build_target_value_signal is None:
        raise RuntimeError(
            "redis helper import failed. Put jq_research_redis_common.py in JoinQuant research path "
            "and ensure redis-py is available. error=%s" % _REDIS_HELPER_IMPORT_ERROR
        )
    bar_time = context.current_dt.strftime("%Y-%m-%dT%H:%M:%S")
    message = build_target_value_signal(
        strategy_name=SIGNAL_QUEUE_CONFIG["strategy_name"],
        leg_name=leg_name,
        code=code,
        target_value=target_value,
        bar_time=bar_time,
        price_mode=SIGNAL_QUEUE_CONFIG.get("price_mode", "latest"),
        note=note,
        hmac_secret=SIGNAL_QUEUE_CONFIG.get("hmac_secret", ""),
    )
    _push_signal_to_redis(message)
    log.info("emit signal %s %s target=%.2f", leg_name, code, float(target_value))


def place_or_emit_order(context, leg_name, security, target_value, note=""):
    current_value = float(getattr(context.portfolio.positions.get(security), "value", 0.0) or 0.0)
    if EXEC_MODE == "redis_signal":
        emit_signal(context, leg_name, security, target_value, note=note)
        g.leg_cash[leg_name] -= float(target_value) - current_value
    else:
        order = order_target_value(security, target_value)
        if order is not None:
            g.leg_cash[leg_name] -= float(target_value) - current_value


def initialize(context):
    _set_backtest_options()
    _init_state(context)
    run_daily(_prepare_smallcap_session, time="9:31")
    run_daily(_stage_targets, time="10:35")
    run_daily(_rebalance_buy_sell_phase, time="10:35:05")
    run_daily(_check_smallcap_limit_up_break, time="14:00")
    run_daily(_run_intraday_risk_checks, time="10:01")
    run_daily(_run_intraday_risk_checks, time="14:30")
    run_daily(_smallcap_close_account, time="14:50")
    run_daily(_record_strategy_state, time="14:55")
    run_daily(_log_strategy_summary, time="14:56")


def _set_backtest_options():
    set_option("avoid_future_data", True)
    set_option("use_real_price", True)
    set_benchmark("000852.XSHG")
    set_slippage(FixedSlippage(0.001), type="stock")
    set_slippage(FixedSlippage(0.0005), type="fund")
    set_order_cost(
        OrderCost(
            open_tax=0,
            close_tax=0.001,
            open_commission=0.0003,
            close_commission=0.0003,
            close_today_commission=0,
            min_commission=5,
        ),
        type="stock",
    )
    set_order_cost(
        OrderCost(
            open_tax=0,
            close_tax=0,
            open_commission=0.0001,
            close_commission=0.0001,
            close_today_commission=0,
            min_commission=1,
        ),
        type="fund",
    )


def _init_state(context):
    weights = {leg.value: LEG_CONFIGS[leg].weight for leg in LEGS}
    for leg in LEGS:
        if LEG_CONFIGS[leg].weight < 0:
            raise ValueError("%s weight must be >= 0" % leg.value)
    weight_sum = sum(LEG_CONFIGS[leg].weight for leg in LEGS)
    if weight_sum > 1.0 + 1e-8:
        raise ValueError("weights sum must be <= 1.0")

    g.weights = weights
    g.day_count = 0
    g.leg_holdings = {leg.value: [] for leg in LEGS}
    g.pending_targets = {leg.value: [] for leg in LEGS}
    g.leg_cooldown = {leg.value: {} for leg in LEGS}
    g.leg_starting_cash = {
        leg.value: context.portfolio.total_value * _leg_weight(leg) for leg in LEGS
    }
    g.leg_cash = dict(g.leg_starting_cash)
    g.smallcap_yesterday_limit_up = []
    g.smallcap_trading_allowed = True
    g.smallcap_pause_reason = ""
    g.smallcap_dbl_history = []
    g.smallcap_defense_signal = False
    g.smallcap_target_count = int(CONFIG["smallcap_pick_n"])
    g.smallcap_position_ratio = float(CONFIG.get("smallcap_base_position_ratio", 1.0))
    g.smallcap_signal_day = None
    g.last_trade_day = None
    g.exec_mode = EXEC_MODE
    g.etf_pool = [
        "510180.XSHG",  # 上证180
        "513100.XSHG",  # 纳指ETF
        "518880.XSHG",  # 黄金ETF
        "159915.XSHE",  # 创业板ETF
        "588120.XSHG",  # 科创100ETF
        "512480.XSHG",  # 半导体ETF
        "513690.XSHG",  # 港股红利ETF
        "510050.XSHG",  # 50ETF
    ]


def _is_tradable(stock):
    try:
        cd = get_current_data()[stock]
    except Exception:
        return False
    if cd.last_price >= cd.high_limit:
        return False
    if cd.paused or cd.is_st or "ST" in cd.name or "*" in cd.name or "退" in cd.name:
        return False
    return True


def _filter_tradable(stocks):
    return [s for s in stocks if _is_tradable(s)]


def _history_last_value(history_result, security):
    values = history_result.get(security) if hasattr(history_result, "get") else history_result[security]
    if isinstance(values, (pd.Series, np.ndarray, list, tuple)):
        if len(values) == 0:
            return None
        return float(values[-1])
    if values is None:
        return None
    return float(values)


def _smallcap_filter_stocks(context, stock_list):
    if not stock_list:
        return []
    current_data = get_current_data()
    last_prices = history(1, unit="1m", field="close", security_list=stock_list, df=False)
    filtered = []
    for stock in stock_list:
        cd = current_data[stock]
        if cd.paused or cd.is_st or "ST" in cd.name or "*" in cd.name or "退" in cd.name:
            continue
        if stock.startswith(("30", "68", "8", "4")):
            continue
        start_date = get_security_info(stock).start_date
        if context.previous_date - start_date < datetime.timedelta(days=375):
            continue
        if stock not in context.portfolio.positions:
            last_price = _history_last_value(last_prices, stock)
            if last_price is None:
                continue
            if last_price >= cd.high_limit or last_price <= cd.low_limit:
                continue
        filtered.append(stock)
    return filtered


def _smallcap_recent_limit_up_codes(context):
    holdings = _active_leg_positions(context, Leg.SMALLCAP)
    if not holdings:
        return []
    df = get_price(
        holdings,
        end_date=context.previous_date,
        fields=["close", "high_limit"],
        frequency="daily",
        count=1,
        panel=False,
        fill_paused=False,
    )
    if df is None or df.empty:
        return []
    return list(df[df["close"] == df["high_limit"]]["code"])


def _smallcap_market_volatility(context):
    lookback = int(CONFIG.get("smallcap_volatility_lookback", 20))
    df = get_price(
        "000300.XSHG",
        end_date=context.previous_date,
        count=lookback + 1,
        frequency="daily",
        fields=["close"],
    )
    if df is None or len(df) < lookback:
        return None
    returns = df["close"].pct_change().dropna()
    return float(returns.std()) if len(returns) > 0 else None


def _smallcap_position_ratio(context):
    base_ratio = float(CONFIG.get("smallcap_base_position_ratio", 1.0))
    if not CONFIG.get("smallcap_enable_dynamic_position", False):
        return base_ratio
    volatility = _smallcap_market_volatility(context)
    if volatility is None:
        return base_ratio
    low = float(CONFIG.get("smallcap_volatility_threshold_low", 0.015))
    high = float(CONFIG.get("smallcap_volatility_threshold_high", 0.035))
    ratio_min = float(CONFIG.get("smallcap_position_ratio_min", 0.5))
    ratio_max = float(CONFIG.get("smallcap_position_ratio_max", 1.0))
    if volatility < low:
        return ratio_max
    if volatility > high:
        return ratio_min
    ratio_range = ratio_max - ratio_min
    volatility_range = max(high - low, 1e-8)
    return ratio_max - (volatility - low) / volatility_range * ratio_range


def _smallcap_target_count(context):
    default_n = int(CONFIG["smallcap_pick_n"])
    if not CONFIG.get("smallcap_enable_dynamic_stock_num", True):
        return default_n
    ma_days = int(CONFIG.get("smallcap_dynamic_stock_num_ma_days", 10))
    today = context.previous_date
    start_date = today - datetime.timedelta(days=ma_days * 2)
    index_df = get_price("399101.XSHE", start_date=start_date, end_date=today, frequency="daily", fields=["close"])
    if index_df is None or len(index_df) < ma_days:
        return default_n
    index_df["ma"] = index_df["close"].ewm(span=ma_days, adjust=False).mean()
    last_row = index_df.iloc[-1]
    ma_value = float(last_row["ma"] or 0.0)
    if ma_value <= 0:
        return default_n
    gap = float(last_row["close"] / ma_value - 1.0)
    if gap >= float(CONFIG.get("smallcap_dynamic_stock_num_gap_high", 0.04)):
        return 3
    if gap >= float(CONFIG.get("smallcap_dynamic_stock_num_gap_mid", -0.04)):
        return 4
    if gap >= float(CONFIG.get("smallcap_dynamic_stock_num_gap_low", -0.10)):
        return 5
    return 6


def _macd(close, short=12, long=26, signal=9):
    dif = close.ewm(span=short, min_periods=short - 1, adjust=False).mean() - close.ewm(
        span=long, min_periods=long - 1, adjust=False
    ).mean()
    dea = dif.ewm(span=signal, min_periods=signal - 1, adjust=False).mean()
    return dif, dea, (dif - dea) * 2


def _smallcap_detect_top_divergence(context):
    rows = (12 + 26 + 9) * 5
    grid = get_price(
        "399101.XSHE",
        end_date=context.previous_date,
        count=rows + 10,
        frequency="daily",
        fields=["close"],
    )
    if grid is None or len(grid) < rows:
        return False
    grid = grid.dropna().copy()
    if len(grid) < rows:
        return False
    grid["dif"], grid["dea"], grid["macd"] = _macd(grid["close"])
    mask = (grid["macd"] < 0) & (grid["macd"].shift(1) >= 0)
    if mask.sum() < 2:
        return False
    key2, key1 = mask[mask].index[-2], mask[mask].index[-1]
    price_cond = grid.loc[key2, "close"] < grid.loc[key1, "close"]
    dif_cond = grid.loc[key2, "dif"] > grid.loc[key1, "dif"] > 0
    macd_cond = grid["macd"].iloc[-2] > 0 > grid["macd"].iloc[-1]
    if len(grid["dif"]) <= 20:
        return False
    recent_avg = grid["dif"].iloc[-10:].mean()
    prev_avg = grid["dif"].iloc[-20:-10].mean()
    return bool(price_cond and dif_cond and macd_cond and recent_avg < prev_avg)


def _smallcap_detect_defense_signal(context):
    if not CONFIG.get("smallcap_enable_defense_pause", True):
        return False
    high_lookback = int(CONFIG.get("smallcap_defense_high_lookback", 60))
    index_df = get_price(
        "399101.XSHE",
        end_date=context.previous_date,
        count=high_lookback,
        frequency="daily",
        fields=["close", "high"],
    )
    if index_df is None or len(index_df) < high_lookback:
        return False
    high_proximity = float(CONFIG.get("smallcap_defense_high_proximity", 0.95))
    current_close = float(index_df["close"].iloc[-1])
    max_high = float(index_df["high"].max())
    is_high = current_close >= max_high * high_proximity
    if not is_high:
        return False

    universe = _smallcap_filter_stocks(context, get_index_stocks("399101.XSHE"))
    sample_size = int(CONFIG.get("smallcap_defense_sample_size", 120))
    universe = _short_by_market_cap(context, universe)[:sample_size]
    if len(universe) < 20:
        return False

    ma_days = int(CONFIG.get("smallcap_defense_ma_days", 20))
    wide = _get_fixed_point_close_wide(universe, end_dt=context.previous_date, count=ma_days + 1, context=context)
    if wide is None or wide.empty or len(wide) < ma_days + 1:
        return False
    valid_cols = [col for col in wide.columns if wide[col].notna().sum() >= ma_days + 1]
    if not valid_cols:
        return False
    recent = wide[valid_cols].tail(ma_days + 1)
    above_ma_ratio = float((recent.iloc[-1] > recent.tail(ma_days).mean()).mean())
    daily_ret = recent.iloc[-1] / recent.iloc[-2] - 1
    up_ratio = float((daily_ret > 0).mean())
    return bool(above_ma_ratio < 0.6 and up_ratio < 0.5)


def _prepare_smallcap_session(context):
    today = context.current_dt.strftime("%Y-%m-%d")
    if g.smallcap_signal_day == today:
        return
    g.smallcap_signal_day = today
    g.smallcap_yesterday_limit_up = _smallcap_recent_limit_up_codes(context)
    g.smallcap_target_count = _smallcap_target_count(context)
    g.smallcap_position_ratio = _smallcap_position_ratio(context)

    month_blocked = context.current_dt.month in set(CONFIG.get("smallcap_avoid_trade_months", (1, 4)))
    if CONFIG.get("smallcap_enable_top_divergence_pause", True):
        g.smallcap_dbl_history.append(1 if _smallcap_detect_top_divergence(context) else 0)
        keep = max(int(CONFIG.get("smallcap_top_divergence_check_days", 10)), 20)
        g.smallcap_dbl_history = g.smallcap_dbl_history[-keep:]
    check_days = int(CONFIG.get("smallcap_top_divergence_check_days", 10))
    top_divergence_blocked = any(g.smallcap_dbl_history[-check_days:]) if g.smallcap_dbl_history else False
    g.smallcap_defense_signal = _smallcap_detect_defense_signal(context)

    reasons = []
    if month_blocked:
        reasons.append("avoid_month")
    if top_divergence_blocked:
        reasons.append("top_divergence")
    if g.smallcap_defense_signal:
        reasons.append("defense")
    g.smallcap_pause_reason = ",".join(reasons)
    g.smallcap_trading_allowed = not reasons


def _ensure_smallcap_session(context):
    if g.smallcap_signal_day != context.current_dt.strftime("%Y-%m-%d"):
        _prepare_smallcap_session(context)


def _check_smallcap_limit_up_break(context):
    holdings = _active_leg_positions(context, Leg.SMALLCAP)
    if not holdings or not g.smallcap_yesterday_limit_up:
        return
    now_time = context.current_dt
    for stock in list(g.smallcap_yesterday_limit_up):
        if stock not in holdings:
            continue
        current_df = get_price(
            stock,
            end_date=now_time,
            frequency="1m",
            fields=["close", "high_limit"],
            skip_paused=False,
            fq="pre",
            count=1,
            panel=False,
            fill_paused=True,
        )
        if current_df is None or current_df.empty:
            continue
        if float(current_df.iloc[0]["close"]) < float(current_df.iloc[0]["high_limit"]):
            place_or_emit_order(context, Leg.SMALLCAP.value, stock, 0, note="yesterday limit up opened")
            if stock in g.leg_holdings[Leg.SMALLCAP.value]:
                g.leg_holdings[Leg.SMALLCAP.value].remove(stock)


def _smallcap_close_account(context):
    _ensure_smallcap_session(context)
    if g.smallcap_trading_allowed:
        return
    for stock in list(_active_leg_positions(context, Leg.SMALLCAP)):
        place_or_emit_order(context, Leg.SMALLCAP.value, stock, 0, note="smallcap close account")


def _maybe_reset_day_state(context):
    today = context.current_dt.strftime("%Y-%m-%d")
    if g.last_trade_day == today:
        return
    g.last_trade_day = today
    for leg in LEGS:
        # 仅清理“严格早于今天”的冷却记录；today 仍在冷却期内。
        expired = [code for code, until_day in g.leg_cooldown[leg.value].items() if until_day < today]
        for code in expired:
            g.leg_cooldown[leg.value].pop(code, None)


def _active_leg_positions(context, leg):
    return [s for s in g.leg_holdings[leg.value] if s in context.portfolio.positions]


def _leg_stop_loss_pct(leg):
    return LEG_CONFIGS[leg].stop_loss_pct


def _leg_codes_after_cooldown(leg, targets, today):
    """
    冷却依据是 (code, until_day) 二元组，而不只是 code 名字：
    - only when until_day >= today 才视为仍在冷却期
    - until_day < today 则不再拦截（即便清理尚未执行）
    """
    blocked = {
        code
        for code, until_day in g.leg_cooldown[leg.value].items()
        if until_day >= today
    }
    return [code for code in targets if code not in blocked]


def _leg_enabled(leg):
    return _leg_weight(leg) > 0


def _leg_weight(leg):
    return LEG_CONFIGS[leg].weight


def _leg_rebalance_due(leg):
    interval = LEG_CONFIGS[leg].rebalance_every_n_days
    return interval <= 0 or g.day_count % interval == 0


def _filter_target_tradable(leg, targets):
    """新目标必须可交易；已有持仓允许保留，避免因临时状态误删持仓记录。"""
    current_holdings = set(g.leg_holdings[leg.value])
    return [code for code in targets if code in current_holdings or _is_tradable(code)]


def _select_c1_targets(context):
    """
    c1: 中证2000池内，偏进攻的“质量 + 动量”集中策略。
    """
    universe = _filter_tradable(get_index_stocks("399303.XSHE"))
    if not universe:
        return []

    q = query(
        valuation.code,
        valuation.market_cap,
        valuation.pe_ratio,
        indicator.roe,
        indicator.roa,
    ).filter(
        valuation.code.in_(universe),
        valuation.market_cap > 0,
        indicator.roe > 0,
        indicator.roa > 0,
    )
    df = get_fundamentals(q)
    if df is None or df.empty:
        return []

    codes = list(df["code"].values)
    # 历史排序特征使用定点复权日线，避免普通前复权在长回测里引入未来函数。
    px = _get_fixed_point_close_wide(codes, end_dt=context.previous_date, count=60, context=context)
    if px is None or px.empty:
        return []

    score_rows = []
    for code in codes:
        s = px[code].dropna().values if code in px.columns else np.array([])
        if len(s) < 21:
            continue
        ret20 = s[-1] / s[-21] - 1
        vol20 = np.std(np.diff(np.log(s[-21:]))) * math.sqrt(252)
        score_rows.append((code, ret20 - 0.5 * vol20, ret20, vol20))

    if not score_rows:
        return []

    score_df = pd.DataFrame(score_rows, columns=["code", "score", "ret20", "vol20"])
    merged = df.merge(score_df, on="code", how="inner")
    # 偏小市值 + 质量 + 动量
    merged["rank_mc"] = merged["market_cap"].rank(ascending=True)
    merged["rank_roe"] = merged["roe"].rank(ascending=False)
    merged["rank_roa"] = merged["roa"].rank(ascending=False)
    merged["rank_mom"] = merged["score"].rank(ascending=False)
    merged["final_score"] = (
        0.35 * merged["rank_mom"]
        + 0.30 * merged["rank_mc"]
        + 0.20 * merged["rank_roe"]
        + 0.15 * merged["rank_roa"]
    )
    merged = merged.sort_values("final_score", ascending=True)
    ranked_codes = list(merged["code"])
    targets = ranked_codes[: int(CONFIG["c1_top_k"])]
    return targets[: CONFIG["c1_max_positions"]]


def _smallcap_refine_candidates_by_price_action(context, candidates):
    if not candidates:
        return []
    lookback = int(CONFIG.get("smallcap_momentum_lookback", 25))
    count = max(60, lookback + 5)
    wide = _get_fixed_point_close_wide(candidates, end_dt=context.previous_date, count=count, context=context)
    if wide is None or wide.empty:
        return list(candidates)
    current_data = get_current_data()
    holdings = set(g.leg_holdings[Leg.SMALLCAP.value])
    primary = []
    relaxed = []
    price_cap = float(CONFIG.get("smallcap_price_cap", 50.0))
    min_range_position = float(CONFIG.get("smallcap_min_range_position", 0.35))
    min_return_20d = float(CONFIG.get("smallcap_min_return_20d", 0.0))
    score_min = float(CONFIG.get("smallcap_momentum_score_min", -1.0))
    score_max = float(CONFIG.get("smallcap_momentum_score_max", 10.5))
    for code in candidates:
        hist = wide[code].dropna().values if code in wide.columns else np.array([])
        if len(hist) < max(60, lookback):
            if code in holdings:
                primary.append(code)
            continue
        current_price = float(getattr(current_data[code], "last_price", 0.0) or 0.0)
        prices = np.append(hist, current_price)
        if len(prices) < max(60, lookback):
            if code in holdings:
                primary.append(code)
            continue
        if code in holdings:
            primary.append(code)
            continue
        ma20 = float(np.mean(prices[-20:]))
        ret20 = float(prices[-1] / prices[-21] - 1)
        window60 = prices[-60:]
        price_span = float(window60.max() - window60.min())
        range_position = 0.5 if price_span <= 1e-8 else float((prices[-1] - window60.min()) / price_span)
        y = np.log(prices[-lookback:])
        x = np.arange(len(y))
        weights = np.linspace(1.0, 2.0, len(y))
        slope, intercept = np.polyfit(x, y, 1, w=weights)
        annualized_return = math.exp(slope * 250) - 1
        residuals = y - (slope * x + intercept)
        weighted_residuals = weights * residuals ** 2
        r_squared = 1 - (np.sum(weighted_residuals) / np.sum(weights * (y - np.mean(y)) ** 2))
        momentum_score = float(annualized_return * r_squared)
        if (
            current_price <= price_cap
            and prices[-1] > ma20
            and ret20 > min_return_20d
            and range_position >= min_range_position
            and score_min < momentum_score < score_max
        ):
            primary.append(code)
        elif current_price <= price_cap and prices[-1] > ma20 and momentum_score > 0:
            relaxed.append(code)
    ranked = primary + [code for code in relaxed if code not in primary]
    return ranked


def _select_smallcap_targets(context):
    """
    小市值腿：基本面 -> 审计 -> 分红 -> 价格位置/动量 的有序候选列表。
    """
    _ensure_smallcap_session(context)
    universe = _smallcap_filter_stocks(context, get_index_stocks("399101.XSHE"))
    if not universe:
        return []

    candidate_limit = max(int(g.smallcap_target_count) * 5, 30)
    q = query(
        valuation.code,
        valuation.market_cap,  # 总市值
        income.net_profit,  # 净利润
        income.operating_revenue,  # 营业收入
        cash_flow.subtotal_operate_cash_inflow,  # 经营活动现金流入
    ).filter(
        valuation.code.in_(universe),
        valuation.market_cap.between(10, 100),
        income.operating_revenue > 1e8,
        indicator.roe > 0,
        indicator.roa > 0,
        income.net_profit > 2_000_000,
        cash_flow.subtotal_operate_cash_inflow > -10_000_000,
    ).order_by(valuation.market_cap.asc()).limit(candidate_limit)

    df = get_fundamentals(q)
    if df is None or df.empty:
        return []

    # 现金流质量使用“经营现金流入/净利润”做软排序，不做硬阈值一刀切。
    # 这样既能偏好现金流更健康的公司，也能保留行业付款周期差异带来的弹性。
    df = df.copy()
    df["cash_profit_ratio"] = (
        df["subtotal_operate_cash_inflow"] / df["net_profit"].replace(0, np.nan)
    )
    # 对极端值做截断，避免个别异常值主导排序。
    df["cash_profit_ratio"] = df["cash_profit_ratio"].clip(lower=-1.0, upper=5.0).fillna(-1.0)
    df["rank_mc"] = df["market_cap"].rank(ascending=True)
    df["rank_cash"] = df["cash_profit_ratio"].rank(ascending=False)
    df["final_score"] = 0.7 * df["rank_mc"] + 0.3 * df["rank_cash"]
    df = df.sort_values("final_score", ascending=True)

    candidates = list(df["code"])
    if not candidates:
        return []

    # jq_horse v3：审计过滤 + 红利过滤
    audited = _filter_audit_v3(context, candidates)
    if audited:
        candidates = audited
    bonus_filtered = _bonus_filter_v3(context, candidates)
    if bonus_filtered:
        candidates = bonus_filtered

    return _smallcap_refine_candidates_by_price_action(context, candidates)


def _short_by_market_cap(context, stock_list):
    if not stock_list:
        return []
    q = query(
        valuation.code,  # 股票代码
        valuation.market_cap,  # 总市值，用于按小市值优先排序
    ).filter(
        valuation.code.in_(stock_list),  # 仅在传入候选池内排序
        valuation.day == context.previous_date,  # 使用上一交易日可见的估值数据，避免引入当日未来信息
    ).order_by(valuation.market_cap.asc())
    df = get_fundamentals(q)
    return df["code"].unique().tolist() if df is not None and not df.empty else []


def _filter_audit_v3(context, code_list):
    """过滤近几年出现过非标准/负面审计意见的股票。"""
    if not code_list:
        return []
    try:
        previous_date = context.previous_date
        lookback_years = int(CONFIG.get("smallcap_audit_lookback_years", 3))
        start_date = previous_date.replace(year=previous_date.year - lookback_years, month=1, day=1).strftime("%Y-%m-%d")
        # 聚宽审计意见编码：3=保留意见，4=无法表示意见，5=否定意见，7=保留带解释性说明。
        bad_opinion_ids = [3, 4, 5, 7]

        q = query(
            finance.STK_AUDIT_OPINION.code,  # 股票代码
            finance.STK_AUDIT_OPINION.pub_date,  # 审计意见公告日期
            finance.STK_AUDIT_OPINION.opinion_type_id,  # 审计意见类型编码，用于识别保留/否定/无法表示等风险意见
        ).filter(
            finance.STK_AUDIT_OPINION.code.in_(code_list),  # 批量检查候选池，避免逐只股票循环查询
            finance.STK_AUDIT_OPINION.pub_date >= start_date,  # 只看最近 N 年公告
        )
        df = finance.run_query(q)
        if df is None or df.empty:
            return list(code_list)

        bad_codes = set(df[df["opinion_type_id"].isin(bad_opinion_ids)]["code"].unique().tolist())
        return [stock for stock in code_list if stock not in bad_codes]
    except Exception:
        return list(code_list)


def _bonus_filter_v3(context, stock_list):
    if not stock_list:
        return []
    year = context.previous_date.year
    start_date = datetime.datetime(year=year, month=1, day=1)
    end_date = context.previous_date
    target_n = int(max(getattr(g, "smallcap_target_count", 0), CONFIG["smallcap_pick_n"]))
    try:
        # 不再固定“仅5月”使用分红实施数据，全年都优先使用“已实施分红”信息。
        q = query(
            finance.STK_XR_XD.code,
            finance.STK_XR_XD.company_name,
            finance.STK_XR_XD.board_plan_pub_date,
            finance.STK_XR_XD.bonus_amount_rmb,
            finance.STK_XR_XD.bonus_ratio_rmb,
        ).filter(
            finance.STK_XR_XD.code.in_(stock_list),
            finance.STK_XR_XD.board_plan_pub_date > start_date,
            finance.STK_XR_XD.implementation_pub_date <= end_date,
            finance.STK_XR_XD.bonus_ratio_rmb > 0,
        )
        expected_bonus_df = finance.run_query(q)

        if expected_bonus_df is not None and len(expected_bonus_df) > 0:
            bonus_list = expected_bonus_df["code"].unique().tolist()
            price_df = history(
                1, unit="1d", field="close", security_list=bonus_list, df=True, skip_paused=False, fq="pre"
            )
            price_df = price_df.T
            price_df.rename(columns={price_df.columns[0]: "Close_now"}, inplace=True)
            price_df["code"] = price_df.index
            expected_bonus_df = pd.merge(expected_bonus_df, price_df, on=("code",), how="left")
            expected_bonus_df["bonus_ratio"] = expected_bonus_df["bonus_ratio_rmb"] / expected_bonus_df["Close_now"]
            # 偏好更高分红收益率，按估算股息率降序
            expected_bonus_df = expected_bonus_df.sort_values(by="bonus_ratio", ascending=False)
            bonus_list = expected_bonus_df["code"].unique().tolist()
        else:
            report_date = datetime.datetime(year=year - 1, month=12, day=31)
            # 若当年已实施分红信息不足，则回退到年报口径，剔除“年度不分配不转增”公司
            q = query(
                finance.STK_XR_XD.code,
                finance.STK_XR_XD.company_name,
                finance.STK_XR_XD.a_registration_date,
                finance.STK_XR_XD.bonus_amount_rmb,
                finance.STK_XR_XD.bonus_ratio_rmb,
            ).filter(
                finance.STK_XR_XD.report_date == report_date,
                finance.STK_XR_XD.bonus_type == "年度分红",
                finance.STK_XR_XD.implementation_pub_date <= end_date,
                finance.STK_XR_XD.board_plan_bonusnote == "不分配不转增",
                finance.STK_XR_XD.code.in_(stock_list),
            )
            no_year_bonus = finance.run_query(q)
            no_year_bonus_list = no_year_bonus["code"].unique().tolist() if no_year_bonus is not None else []
            bonus_list = [code for code in stock_list if code not in no_year_bonus_list]
            bonus_list = _short_by_market_cap(context, bonus_list)

        if len(bonus_list) < target_n:
            tail = [x for x in _short_by_market_cap(context, stock_list) if x not in bonus_list]
            bonus_list.extend(tail[: target_n - len(bonus_list)])
        return bonus_list
    except Exception:
        return list(stock_list)


def _etf_momentum_score(etf, context, lookback_days):
    try:
        # 动量序列用定点复权，当前可交易价仍使用实时真实价格。
        # 这样既保持历史序列连续，又不把执行层价格“复权化”。
        hist = _get_fixed_point_close_series(etf, end_dt=context.previous_date, count=lookback_days, context=context)
        if hist is None or hist.empty or len(hist) < lookback_days:
            return None
        current_price = get_current_data()[etf].last_price
        prices = np.append(hist.values, current_price)
        if len(prices) < 5:
            return None
        log_prices = np.log(prices)
        x = np.arange(len(log_prices))
        w = np.linspace(1.0, 2.0, len(log_prices))
        slope, intercept = np.polyfit(x, log_prices, 1, w=w)
        annualized_return = math.exp(slope * 250) - 1
        ss_res = np.sum(w * (log_prices - (slope * x + intercept)) ** 2)
        ss_tot = np.sum(w * (log_prices - np.mean(log_prices)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        score = annualized_return * r2
        if min(prices[-1] / prices[-2], prices[-2] / prices[-3], prices[-3] / prices[-4]) < 0.97:
            return 0.0
        return float(score)
    except Exception:
        return None


def _select_etf_rotation_targets(context):
    ranked = []
    for etf in g.etf_pool:
        if not _is_tradable(etf):
            continue
        score = _etf_momentum_score(etf, context, CONFIG["etf_lookback_days"])
        if score is None:
            continue
        if CONFIG["etf_momentum_min"] < score < CONFIG["etf_momentum_max"]:
            ranked.append((etf, score))
    ranked.sort(key=lambda x: x[1], reverse=True)
    ranked_codes = [item[0] for item in ranked]
    return ranked_codes[:1]


def _leg_targets(leg, context):
    if leg == Leg.SMALLCAP:
        _ensure_smallcap_session(context)
    targets = list(g.leg_holdings[leg.value])
    if _leg_enabled(leg) and _leg_rebalance_due(leg):
        targets = LEG_SELECTORS[leg](context)
    if not _leg_enabled(leg):
        return []
    if leg == Leg.SMALLCAP and not g.smallcap_trading_allowed:
        return []
    today = context.current_dt.strftime("%Y-%m-%d")
    targets = _leg_codes_after_cooldown(leg, targets, today)
    # 二次防线：即便 _select 内部已过滤，这里仍兜底一次。
    targets = _filter_target_tradable(leg, targets)
    if leg == Leg.SMALLCAP and _leg_rebalance_due(leg):
        return targets[: int(g.smallcap_target_count)]
    return targets


def _stage_targets(context):
    _maybe_reset_day_state(context)
    g.day_count += 1

    staged = {leg: _leg_targets(leg, context) for leg in LEGS}

    # 无跨策略腿冲突时，直接落库，避免不必要的优先级裁剪。
    all_codes = [code for leg in LEGS for code in staged[leg]]
    if len(all_codes) == len(set(all_codes)):
        for leg in LEGS:
            g.pending_targets[leg.value] = list(staged[leg])
        return

    # 冲突消解：按 LEG_CONFIGS priority 优先级分配标的，避免同一标的被多条腿同时持有。
    used = set()
    for leg in sorted(LEGS, key=lambda item: LEG_CONFIGS[item].priority):
        targets = []
        for code in staged[leg]:
            if code not in used:
                targets.append(code)
                used.add(code)
        g.pending_targets[leg.value] = targets


def _rebalance_leg_sell(context, leg, targets):
    weight = _leg_weight(leg)
    current = _active_leg_positions(context, leg)

    for s in current:
        if s not in targets:
            if leg == Leg.SMALLCAP and s in g.smallcap_yesterday_limit_up:
                continue
            place_or_emit_order(context, leg.value, s, 0, note="remove from targets")

    # 卖出阶段只负责发卖单，不再依赖对 g.leg_holdings 的中间修改来驱动买入阶段。
    if weight <= 0 or not targets:
        g.leg_holdings[leg.value] = []
        return


def _position_needs_rebalance(pos, target_value):
    if pos is None:
        return True
    current_value = float(getattr(pos, "value", 0.0) or 0.0)
    gap = abs(current_value - float(target_value))
    return gap > max(100.0, abs(float(target_value)) * 0.01)


def _rebalance_leg_buy(context, leg, targets):
    weight = _leg_weight(leg)
    if weight <= 0 or not targets:
        g.leg_holdings[leg.value] = []
        return

    # 买入阶段基于当前真实持仓处理，不依赖卖出阶段对 g.leg_holdings 的即时修改。
    leg_capital = context.portfolio.total_value * weight
    if leg == Leg.SMALLCAP:
        leg_capital *= g.smallcap_position_ratio
    per_value = leg_capital / max(len(targets), 1)
    next_holdings = []
    for s in targets:
        pos = context.portfolio.positions.get(s)
        if pos is None and not _is_tradable(s):
            continue
        if _position_needs_rebalance(pos, per_value):
            place_or_emit_order(context, leg.value, s, per_value, note="rebalance target value")
        next_holdings.append(s)
    g.leg_holdings[leg.value] = list(next_holdings)


def _rebalance_buy_sell_phase(context):
    for leg in LEGS:
        _rebalance_leg_sell(context, leg, g.pending_targets[leg.value])
    for leg in LEGS:
        if _leg_enabled(leg):
            _rebalance_leg_buy(context, leg, g.pending_targets[leg.value])


def _run_intraday_risk_checks(context):
    _maybe_reset_day_state(context)
    current_data = get_current_data()
    today = context.current_dt.strftime("%Y-%m-%d")
    for leg in LEGS:
        if not _leg_enabled(leg):
            continue
        stop_loss_pct = _leg_stop_loss_pct(leg)
        for code in _active_leg_positions(context, leg):
            pos = context.portfolio.positions.get(code)
            if pos is None:
                continue
            avg_cost = float(getattr(pos, "avg_cost", 0.0) or 0.0)
            last_price = float(getattr(current_data[code], "last_price", 0.0) or 0.0)
            if avg_cost <= 0 or last_price <= 0:
                continue
            if last_price < avg_cost * (1.0 - stop_loss_pct):
                place_or_emit_order(context, leg.value, code, 0, note="stop loss trigger")
                if code in g.leg_holdings[leg.value]:
                    g.leg_holdings[leg.value].remove(code)
                g.leg_cooldown[leg.value][code] = today


def _record_strategy_state(context):
    record_kwargs = {}
    for leg in LEGS:
        leg_market_value = 0.0
        for code in _active_leg_positions(context, leg):
            pos = context.portfolio.positions.get(code)
            if pos is None:
                continue
            leg_market_value += float(getattr(pos, "value", 0.0) or 0.0)
        leg_total_asset = g.leg_cash[leg.value] + leg_market_value
        record_kwargs[LEG_CONFIGS[leg].record_weight] = _leg_weight(leg) * 100
        record_kwargs[LEG_CONFIGS[leg].record_return] = round(
            leg_total_asset / max(g.leg_starting_cash[leg.value], 1.0) * 100 - 100, 2
        )
    record_kwargs["exec_mode"] = 1 if g.exec_mode == "redis_signal" else 0
    record(**record_kwargs)


def _log_strategy_summary(context):
    log.info(
        "preset=%s mode=%s c1=%s smallcap=%s etf=%s",
        ACTIVE_PRESET,
        g.exec_mode,
        g.pending_targets[Leg.C1.value],
        g.pending_targets[Leg.SMALLCAP.value],
        g.pending_targets[Leg.ETF_ROTATION.value],
    )
