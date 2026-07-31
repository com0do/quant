"""
JoinQuant script: c1 + 小市值 + ETF轮动 组合策略

目标:
1) 将本地高收益思路抽象为 c1 子策略(中证2000偏进攻股池);
2) 融合 jq_horse 的小市值与 ETF 轮动逻辑;
3) 支持三个子策略资金比例可配置。
"""

import math
import datetime
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

PRESET_CONFIGS = {
    "conservative": {
        "weights": {"c1": 0.35, "smallcap": 0.20, "etf_rotation": 0.45},
        "c1_top_k": 8,
        "c1_max_positions": 4,
        "c1_rebalance_every_n_days": 5,
        "smallcap_pick_n": 6,
        "smallcap_rebalance_every_n_days": 7,
        "etf_lookback_days": 30,
        "etf_momentum_min": -0.2,
        "etf_momentum_max": 4.0,
        "c1_stop_loss_pct": 0.08,
        "smallcap_stop_loss_pct": 0.09,
        "etf_rotation_stop_loss_pct": 0.05,
    },
    "balanced": {
        # 默认平衡档
        "weights": {"c1": 0.5, "smallcap": 0.25, "etf_rotation": 0.25},
        "c1_top_k": 6,
        "c1_max_positions": 3,
        "c1_rebalance_every_n_days": 3,
        "smallcap_pick_n": 5,
        "smallcap_rebalance_every_n_days": 5,
        "etf_lookback_days": 25,
        "etf_momentum_min": 0.0,
        "etf_momentum_max": 5.0,
        "c1_stop_loss_pct": 0.07,
        "smallcap_stop_loss_pct": 0.09,
        "etf_rotation_stop_loss_pct": 0.05,
    },
    "aggressive": {
        "weights": {"c1": 0.7, "smallcap": 0.2, "etf_rotation": 0.1},
        "c1_top_k": 6,
        "c1_max_positions": 3,
        "c1_rebalance_every_n_days": 2,
        "smallcap_pick_n": 5,
        "smallcap_rebalance_every_n_days": 4,
        "etf_lookback_days": 20,
        "etf_momentum_min": 0.0,
        "etf_momentum_max": 6.0,
        "c1_stop_loss_pct": 0.06,
        "smallcap_stop_loss_pct": 0.08,
        "etf_rotation_stop_loss_pct": 0.04,
    },
}
CONFIG = PRESET_CONFIGS[ACTIVE_PRESET]
LEG_NAMES = ("c1", "smallcap", "etf_rotation")


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
    if EXEC_MODE == "redis_signal":
        emit_signal(context, leg_name, security, target_value, note=note)
    else:
        order_target_value(security, target_value)


def initialize(context):
    _set_backtest_options()
    _init_state(context)
    run_daily(_rebalance_sell_phase, time="10:35")
    run_daily(_rebalance_buy_phase, time="10:35:05")
    run_daily(_run_intraday_risk_checks, time="10:01")
    run_daily(_run_intraday_risk_checks, time="14:30")
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
    weights = CONFIG["weights"]
    weight_sum = float(weights["c1"] + weights["smallcap"] + weights["etf_rotation"])
    if weight_sum > 1.0 + 1e-8:
        raise ValueError("weights sum must be <= 1.0")

    g.weights = weights
    g.day_count = 0
    g.leg_holdings = {"c1": [], "smallcap": [], "etf_rotation": []}
    g.pending_targets = {"c1": [], "smallcap": [], "etf_rotation": []}
    g.leg_cooldown = {"c1": {}, "smallcap": {}, "etf_rotation": {}}
    g.leg_starting_cash = {leg: context.portfolio.total_value * weights[leg] for leg in LEG_NAMES}
    g.leg_value_data = {leg: 0.0 for leg in LEG_NAMES}
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
    cd = get_current_data()[stock]
    if cd.last_price >= cd.high_limit:
        return False
    if cd.paused or cd.is_st or "ST" in cd.name or "*" in cd.name or "退" in cd.name:
        return False
    return True


def _filter_tradable(stocks):
    return [s for s in stocks if _is_tradable(s)]


def _maybe_reset_day_state(context):
    today = context.current_dt.strftime("%Y-%m-%d")
    if g.last_trade_day == today:
        return
    g.last_trade_day = today
    for leg_name in LEG_NAMES:
        expired = [code for code, until_day in g.leg_cooldown[leg_name].items() if until_day <= today]
        for code in expired:
            g.leg_cooldown[leg_name].pop(code, None)


def _active_leg_positions(context, leg_name):
    return [s for s in g.leg_holdings[leg_name] if s in context.portfolio.positions]


def _leg_stop_loss_pct(leg_name):
    return float(CONFIG.get("%s_stop_loss_pct" % leg_name, 0.08))


def _leg_codes_after_cooldown(leg_name, targets):
    blocked = set(g.leg_cooldown[leg_name].keys())
    return [code for code in targets if code not in blocked]


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
    targets = list(merged["code"].head(CONFIG["c1_top_k"]))
    return targets[: CONFIG["c1_max_positions"]]


def _select_smallcap_targets(context):
    """
    小市值腿：迁移 jq_horse v3 的核心筛选逻辑（国九 + 基本面质量）。
    """
    universe = _filter_tradable(get_index_stocks("399101.XSHE"))
    if not universe:
        return []

    candidate_limit = max(int(CONFIG["smallcap_pick_n"]) * 6, 30)
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

    # 与 jq_horse v3 一致：保留现有持仓，或仅选取价格不过高标的。
    last_prices = history(1, unit="1d", field="close", security_list=candidates)
    selected = [
        s for s in candidates
        if s in g.leg_holdings["smallcap"] or last_prices[s][-1] <= 50
    ]
    if not selected:
        selected = candidates
    return selected[: int(CONFIG["smallcap_pick_n"])]


def _short_by_market_cap(context, stock_list):
    if not stock_list:
        return []
    q = query(
        valuation.code,
        valuation.market_cap,
    ).filter(
        valuation.code.in_(stock_list),
        valuation.day == context.previous_date,
    ).order_by(valuation.market_cap.asc())
    df = get_fundamentals(q)
    return df["code"].unique().tolist() if df is not None and not df.empty else []


def _filter_audit_v3(context, code_list):
    if not code_list:
        return []
    final_list = []
    try:
        previous_date = context.previous_date
        last_year = previous_date.replace(year=previous_date.year - 3, month=1, day=1).strftime("%Y-%m-%d")
        bad_opinion_ids = [3, 4, 5, 7]
        for stock in code_list:
            q = query(
                finance.STK_AUDIT_OPINION.code,
                finance.STK_AUDIT_OPINION.pub_date,
                finance.STK_AUDIT_OPINION.opinion_type_id,
            ).filter(
                finance.STK_AUDIT_OPINION.code == stock,
                finance.STK_AUDIT_OPINION.pub_date >= last_year,
            )
            df = finance.run_query(q)
            if df is None or df.empty:
                final_list.append(stock)
                continue
            if not df["opinion_type_id"].isin(bad_opinion_ids).any():
                final_list.append(stock)
    except Exception:
        return list(code_list)
    return final_list


def _bonus_filter_v3(context, stock_list):
    if not stock_list:
        return []
    year = context.previous_date.year
    start_date = datetime.datetime(year=year, month=1, day=1)
    end_date = context.previous_date
    target_n = int(CONFIG["smallcap_pick_n"])
    try:
        if end_date.month in [5]:
            # 5月附近：用“当年已披露分红方案”估算分红收益率。
            # 这里读取分红方案（XR/XD）相关字段：
            # - code: 股票代码
            # - company_name: 公司名（调试/核查用）
            # - board_plan_pub_date: 董事会分红预案公告日
            # - bonus_amount_rmb: 每股分红金额（元）
            # - bonus_ratio_rmb: 分红率（金额口径）
            q = query(
                finance.STK_XR_XD.code,
                finance.STK_XR_XD.company_name,
                finance.STK_XR_XD.board_plan_pub_date,
                finance.STK_XR_XD.bonus_amount_rmb,
                finance.STK_XR_XD.bonus_ratio_rmb,
            ).filter(
                # 仅保留本年度以来公告的分红预案
                finance.STK_XR_XD.board_plan_pub_date > start_date,
                # 分红实施公告必须已经落地到当前回测日之前（避免未来信息）
                finance.STK_XR_XD.implementation_pub_date <= end_date,
                # 仅保留有分红的标的
                finance.STK_XR_XD.bonus_ratio_rmb > 0,
                # 限制在候选池内
                finance.STK_XR_XD.code.in_(stock_list),
            )
            expected_bonus_df = finance.run_query(q)
            if expected_bonus_df is not None and len(expected_bonus_df) > 0:
                bonus_list = expected_bonus_df["code"].unique().tolist()
                # 用最新收盘价估算股息率：bonus_ratio_rmb / close
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
                bonus_list = []
        else:
            report_date = datetime.datetime(year=year - 1, month=12, day=31)
            # 非5月窗口：回看上一年年报口径，剔除“年度不分配不转增”的公司
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
    return [ranked[0][0]] if ranked else []


def _stage_targets(context):
    _maybe_reset_day_state(context)
    g.day_count += 1

    c1_targets = list(g.leg_holdings["c1"])
    small_targets = list(g.leg_holdings["smallcap"])
    etf_targets = list(g.leg_holdings["etf_rotation"])

    if g.day_count % CONFIG["c1_rebalance_every_n_days"] == 0:
        c1_targets = _select_c1_targets(context)
    if g.day_count % CONFIG["smallcap_rebalance_every_n_days"] == 0:
        small_targets = _select_smallcap_targets(context)
    etf_targets = _select_etf_rotation_targets(context)

    c1_targets = _leg_codes_after_cooldown("c1", c1_targets)
    small_targets = _leg_codes_after_cooldown("smallcap", small_targets)
    etf_targets = _leg_codes_after_cooldown("etf_rotation", etf_targets)

    # 冲突消解：优先 c1，其次小市值，最后 ETF
    c1_set = set(c1_targets)
    small_targets = [s for s in small_targets if s not in c1_set]
    small_set = set(small_targets)
    etf_targets = [s for s in etf_targets if s not in c1_set and s not in small_set]

    g.pending_targets["c1"] = c1_targets
    g.pending_targets["smallcap"] = small_targets
    g.pending_targets["etf_rotation"] = etf_targets


def _rebalance_leg_sell(context, leg_name, targets):
    weight = float(g.weights[leg_name])
    current = _active_leg_positions(context, leg_name)

    # 先卖出不在目标中的旧仓
    for s in current:
        if s not in targets:
            place_or_emit_order(context, leg_name, s, 0, note="remove from targets")
            if s in g.leg_holdings[leg_name]:
                g.leg_holdings[leg_name].remove(s)

    if weight <= 0 or not targets:
        g.leg_holdings[leg_name] = []
        return


def _rebalance_leg_buy(context, leg_name, targets):
    weight = float(g.weights[leg_name])
    if weight <= 0 or not targets:
        g.leg_holdings[leg_name] = []
        return
    leg_capital = context.portfolio.total_value * weight
    per_value = leg_capital / max(len(targets), 1)
    for s in targets:
        if _is_tradable(s):
            place_or_emit_order(context, leg_name, s, per_value, note="rebalance target value")
    g.leg_holdings[leg_name] = list(targets)


def _rebalance_sell_phase(context):
    _stage_targets(context)
    for leg_name in LEG_NAMES:
        _rebalance_leg_sell(context, leg_name, list(g.pending_targets[leg_name]))


def _rebalance_buy_phase(context):
    for leg_name in LEG_NAMES:
        _rebalance_leg_buy(context, leg_name, list(g.pending_targets[leg_name]))


def _run_intraday_risk_checks(context):
    _maybe_reset_day_state(context)
    current_data = get_current_data()
    today = context.current_dt.strftime("%Y-%m-%d")
    for leg_name in LEG_NAMES:
        stop_loss_pct = _leg_stop_loss_pct(leg_name)
        for code in _active_leg_positions(context, leg_name):
            pos = context.portfolio.positions.get(code)
            if pos is None:
                continue
            avg_cost = float(getattr(pos, "avg_cost", 0.0) or 0.0)
            last_price = float(getattr(current_data[code], "last_price", 0.0) or 0.0)
            if avg_cost <= 0 or last_price <= 0:
                continue
            if last_price < avg_cost * (1.0 - stop_loss_pct):
                place_or_emit_order(context, leg_name, code, 0, note="stop loss trigger")
                if code in g.leg_holdings[leg_name]:
                    g.leg_holdings[leg_name].remove(code)
                g.leg_cooldown[leg_name][code] = today


def _record_strategy_state(context):
    g.leg_value_data = {leg: 0.0 for leg in LEG_NAMES}
    copy_leg_value = dict(g.leg_starting_cash)
    for leg_name in LEG_NAMES:
        for code in _active_leg_positions(context, leg_name):
            pos = context.portfolio.positions.get(code)
            if pos is None:
                continue
            current_value = float(getattr(pos, "value", 0.0) or 0.0)
            g.leg_value_data[leg_name] += current_value
            copy_leg_value[leg_name] += float(getattr(pos, "price", 0.0) - getattr(pos, "avg_cost", 0.0)) * float(
                getattr(pos, "total_amount", 0) or 0
            )

    record(
        c1_weight=g.weights["c1"] * 100,
        smallcap_weight=g.weights["smallcap"] * 100,
        etf_rotation_weight=g.weights["etf_rotation"] * 100,
        c1_return=round(copy_leg_value["c1"] / max(g.leg_starting_cash["c1"], 1.0) * 100 - 100, 2),
        smallcap_return=round(copy_leg_value["smallcap"] / max(g.leg_starting_cash["smallcap"], 1.0) * 100 - 100, 2),
        etf_rotation_return=round(copy_leg_value["etf_rotation"] / max(g.leg_starting_cash["etf_rotation"], 1.0) * 100 - 100, 2),
        exec_mode=1 if g.exec_mode == "redis_signal" else 0,
    )


def _log_strategy_summary(context):
    log.info(
        "preset=%s mode=%s c1=%s smallcap=%s etf=%s",
        ACTIVE_PRESET,
        g.exec_mode,
        g.pending_targets["c1"],
        g.pending_targets["smallcap"],
        g.pending_targets["etf_rotation"],
    )
