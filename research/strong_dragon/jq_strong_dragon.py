# -*- coding: utf-8 -*-
"""
JoinQuant strategy: approximation of the "强龙出现" (strong dragon appears) main-up-wave signal.

Paste this whole file into a JoinQuant strategy (daily or minute frequency both work). It is a research
script: the local study in research/strong_dragon/README.md measured this exact rule on ~2,200 CSI 1000 /
CSI 2000 stocks from 2025-01 to 2026-03 and it LOST money (portfolio -47% while CSI 1000 rose 33%).
Use it to reproduce/verify on JoinQuant's full universe, or paper trading only. Never live-trade it as is.

Signal (evaluated on yesterday's close, orders at today's open):
  RED   = MA5 > MA10 > MA20 and close / close[3 bars ago] - 1 >= RED_RET3            (main up-wave)
  GREEN = a RED in the previous GREEN_LOOKBACK bars, no RED today, close < MA5        (up-wave over)
  Entry : first RED after a non-RED day (fresh RED); skipped if today opens at limit-up
  Exit  : GREEN, or close <= cost * (1 - STOP_LOSS); postponed while the open is at limit-down
Sizing (from the deck): POSITION_PCT of total value per stock, at most MAX_POSITIONS holdings.
"""
import numpy as np
import pandas as pd

RED_RET3 = 0.10
GREEN_LOOKBACK = 15
STOP_LOSS = 0.08
MAX_POSITIONS = 3
POSITION_PCT = 0.30
MIN_MONEY_20D = 5e7          # average daily trading value over 20 days, CNY
MIN_LISTED_DAYS = 60
HISTORY_BARS = 45            # MA20 + green look-back + ret3, with margin
BENCHMARK = "000852.XSHG"    # CSI 1000


EPS = 1e-9                   # ties (e.g. MA10 == MA20 on flat prices) must not depend on float noise


# JoinQuant runs an old pandas (< 0.24): no shift(fill_value=...) and no notna()/isna(), so the code below
# sticks to float arithmetic and notnull()/isnull().
def _signals_one(s):
    ma5, ma10, ma20 = s.rolling(5).mean(), s.rolling(10).mean(), s.rolling(20).mean()
    ret3 = s / s.shift(3) - 1
    red = (ma5 > ma10 * (1 + EPS)) & (ma10 > ma20 * (1 + EPS)) & (ret3 >= RED_RET3 - EPS)
    red_f = red.astype(float)
    recent_red = red_f.shift(1).rolling(GREEN_LOOKBACK, min_periods=1).max().fillna(0) > 0
    green = recent_red & ~red & (s < ma5 * (1 - EPS))
    fresh = red & ~(red_f.shift(1).fillna(0) > 0)
    return red, green, fresh, ret3


def compute_signals(close):
    """close: wide DataFrame (rows = dates ascending, columns = codes; NaN = suspended).

    Each column is evaluated on its traded days only (suspended days are skipped, like skip_paused=True),
    then re-aligned to the full date index. Returns red, green, fresh_red (bool) and ret3 frames.
    """
    # Leading NaNs (not yet listed) behave the same either way; only gaps inside the series need the slow path.
    gappy = (close.notnull().astype(int).cummax() > 0) & close.isnull()
    with_gaps = list(close.columns[gappy.any()])
    plain = close.drop(columns=with_gaps)
    frames = dict(zip(("red", "green", "fresh", "ret3"), _signals_one(plain)))
    if with_gaps:
        out = {k: {} for k in frames}
        for code in with_gaps:
            for k, v in zip(frames, _signals_one(close[code].dropna())):
                out[k][code] = v
        for k in frames:
            frames[k] = pd.concat([frames[k], pd.DataFrame(out[k]).reindex(close.index)], axis=1)
    frames = {k: v.reindex(index=close.index, columns=close.columns) for k, v in frames.items()}
    for k in ("red", "green", "fresh"):
        frames[k] = frames[k].fillna(False).astype(bool)
    return frames["red"], frames["green"], frames["fresh"], frames["ret3"]


def initialize(context):
    set_benchmark(BENCHMARK)
    set_option("use_real_price", True)
    set_option("avoid_future_data", True)
    set_order_cost(OrderCost(open_tax=0, close_tax=0.0005, open_commission=0.0003, close_commission=0.0003,
                             close_today_commission=0, min_commission=5), type="stock")
    set_slippage(PriceRelatedSlippage(0.002), type="stock")
    log.set_level("order", "error")
    g.entry = {}  # code -> {"date": str, "cost": float}
    run_daily(trade_at_open, time="09:30", reference_security=BENCHMARK)


def _universe(context):
    day = context.previous_date
    secs = get_all_securities(["stock"], date=day)
    listed_days = (pd.Timestamp(day) - pd.to_datetime(secs["start_date"])).dt.days
    secs = secs[listed_days >= MIN_LISTED_DAYS]
    current = get_current_data()
    keep = []
    for code in secs.index:
        if not (code.endswith(".XSHG") or code.endswith(".XSHE")):
            continue
        cd = current[code]
        if cd.paused or cd.is_st or "退" in cd.name:  # "退" marks delisting-arrangement names on the exchange
            continue
        keep.append(code)
    return keep


def _history(codes, end_date):
    df = get_price(codes, end_date=end_date, count=HISTORY_BARS, frequency="daily",
                   fields=["close", "money", "paused"], skip_paused=False, fq="pre", panel=False)
    df.loc[df["paused"] == 1, "close"] = np.nan
    close = df.pivot(index="time", columns="code", values="close").sort_index()
    money = df.pivot(index="time", columns="code", values="money").sort_index()
    return close, money


def trade_at_open(context):
    current = get_current_data()
    held = list(context.portfolio.positions.keys())
    universe = _universe(context)
    codes = sorted(set(universe) | set(held))
    if not codes:
        return
    close, money = _history(codes, context.previous_date)
    red, green, fresh, ret3 = compute_signals(close)
    last = close.index[-1]

    # 1) exits
    for code in held:
        pos = context.portfolio.positions[code]
        if pos.closeable_amount <= 0:
            continue
        cd = current[code]
        if cd.paused or cd.day_open <= cd.low_limit:
            continue
        prev_close = close[code].iloc[-1] if code in close.columns else np.nan
        cost = g.entry.get(code, {}).get("cost", pos.avg_cost)
        reason = None
        if code in green.columns and bool(green[code].iloc[-1]):
            reason = "green"
        elif prev_close == prev_close and prev_close <= cost * (1 - STOP_LOSS):
            reason = "stop_loss"
        if reason:
            order_target_value(code, 0)
            log.info("SELL %s reason=%s cost=%.2f prev_close=%.2f" % (code, reason, cost, prev_close))
            g.entry.pop(code, None)

    # 2) entries
    slots = MAX_POSITIONS - len([c for c in context.portfolio.positions if context.portfolio.positions[c].total_amount > 0])
    if slots > 0:
        _buy(context, current, universe, money, fresh, ret3, last, slots)
    record(holdings=len(context.portfolio.positions))


def _buy(context, current, universe, money, fresh, ret3, last, slots):
    liquid = money.rolling(20, min_periods=10).mean().loc[last] >= MIN_MONEY_20D
    cand = fresh.loc[last] & liquid.reindex(fresh.columns).fillna(False)
    cand = [c for c in cand[cand].index if c in universe and c not in context.portfolio.positions]
    cand.sort(key=lambda c: ret3.loc[last, c], reverse=True)
    value = context.portfolio.total_value * POSITION_PCT
    for code in cand:
        if slots <= 0:
            break
        cd = current[code]
        if cd.paused or cd.day_open >= cd.high_limit:
            log.info("SKIP %s: opens at limit-up, cannot buy" % code)
            continue
        if context.portfolio.available_cash < value * 0.5:
            break
        o = order_target_value(code, min(value, context.portfolio.available_cash))
        if o is not None:
            g.entry[code] = {"date": str(context.current_dt.date()), "cost": cd.day_open}
            slots -= 1
            log.info("BUY %s ret3=%.1f%% open=%.2f" % (code, ret3.loc[last, code] * 100, cd.day_open))
