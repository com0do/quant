# -*- coding: utf-8 -*-
"""
cc_op1 — CSI 1000 增强多因子截面策略（聚宽移植版 · 挽歌版 ARCHIVED）

⚠️ 状态：已归档，仅供对照实验。不得上实盘，不得对外传播参数细节。
⚠️ 结论：v1.0 跑不赢基准；v1.1 “对齐引擎”后换手暴增、收益更差（见日盈亏巨柱）。
⚠️ 死因与演进方法：docs/01_foundation.md §5「策略生命周期：保鲜、衰减、挽歌与演进」

本文件把本地 `csi1000_enhanced` + `config/final_freeze_2025.toml` 翻译成聚宽脚本的一次失败尝试。

v1.1 变更（已证明过度对齐有害，勿再复制）：
  1. 去掉 PE 硬过滤（pe>0 & pe<500）——只留 NaN 过滤，让 z-score 处理极端值
  2. 新增 sell_score 阈值退出：排名严重下滑立刻卖，不等它掉出 top-K
     （本地 sell_score_threshold=0.94，回来找源码里的 _sell_scores 逻辑）
  3. 新增 MA20 破位结构化卖出：连续 2 日收盘 < MA20 且已过 min_hold → 卖
  4. 崩盘冷静期改为强制清仓（本地 _is_market_crash 触发时 exposure→0）
  5. 修 breadth 里 MA20 把今天算进去的 bug（应该是 [-21:-1] 而不是 [-20:]）
  6. 加详细日志：每次调仓打印 top-K 的得分明细，方便对比本地

===============================================================================
参数血统（对应本地仓库的位置）
-------------------------------------------------------------------------------
打分公式        —— quant/stock_strategy/csi1000_enhanced.py::buy_scores
交易/风控参数   —— config/final_freeze_2025.toml
体制切换/防挤兑 —— quant/backtest 里的 regime switch + anti-fomo + crash cooldown

关键参数：
    universe            = 中证 1000 成分股（000852.XSHG）
    top_k               = 5
    entry_interval_days = 2
    stop_loss_pct       = 5%
    trailing_stop_pct   = 10%
    min_hold_days       = 2
    exposure            = 0.80 ~ 1.00（月度动态 + 体制调节）
    anti_fomo_ret5      = 10%（+ 量比 > 2.2 + 上影 > 0.45 时视为追高）
    regime breadth      = 0.60 risk_on / 0.44 risk_off，越冷越少仓、越宽松越进攻
    crash cooldown      = ret5 < -8% 或 ret20 < -14% 或 breadth < 0.25 → 冷却 1 天
===============================================================================

⚠️ 使用前必读
-------------------------------------------------------------------------------
1. 本地在 2025 全年样本内跑出年化 +39.8% / Sharpe 2.0 / 最大回撤 10%，超额基准
   ~10 个百分点；**但 2026 Q1 样本外测试年化 -32%、跑输基准 ~72 个百分点**——
   参数在 2025 之后已经出现明显衰减，请把本脚本视为"起点"，先在聚宽做 2026 Q1/Q2
   独立验证，不要直接切实盘。
2. 打分用 T-1 及之前的数据（`set_option('avoid_future_data', True)`），
   下单固定在 09:35，规避集合竞价异常价。
3. 因子 z-score 在**当日候选池内**做，跟本地按整个 CSI 1000 池做不完全等价，
   但差异很小（候选池 = CSI 1000 减去 ST/停牌/次新/涨停锁死）。
"""

from jqdata import *
import numpy as np
import pandas as pd
from datetime import timedelta


# =============================================================================
# 参数区（顶部集中，改这里就够，不用往下翻）
# =============================================================================

# --- 股票池 ---
UNIVERSE_INDEX      = "000852.XSHG"       # 中证 1000
BENCHMARK_INDEX     = "000852.XSHG"       # 回测基准

# --- 打分权重（对齐 csi1000_enhanced 默认） ---
W_VALUE             = 0.7716              # -0.6*z(pe) - 0.4*z(pb)
W_QUALITY           = 0.8284              # z(roe)
W_MOM20             = 0.55                # z(ret_20)
W_MOM60             = 0.25                # z(ret_60)
W_LOWVOL            = 0.30                # -z(vol_20)

# --- 交易参数（对齐 final_freeze_2025.toml） ---
TOP_K               = 4
MAX_POSITIONS       = 4
ENTRY_INTERVAL_DAYS = 2                   # 每 N 个交易日调一次仓
MIN_HOLD_DAYS       = 2

# --- 卖出保护 ---
STOP_LOSS_PCT       = 0.05                # 相对成本 -5% 止损
TRAILING_STOP_PCT   = 0.10                # 从持仓最高价回撤 -10% 止盈

# --- 排名下滑退出（对齐本地 sell_score_threshold=0.94）---
# 本地含义：把持仓的 sell 得分归一化到 [0,1]，> 0.94 就卖。
# 等价翻译：持仓在当日候选池排名后 6% 就卖（比如 500 只候选，排名 470 之后）
SELL_RANK_PERCENTILE_THR   = 0.94

# --- MA20 破位结构化卖出（对齐本地 sell_ma_break_confirm_days=2）---
STRUCTURAL_MA_BREAK_ENABLE = True
STRUCTURAL_MA_WINDOW       = 20
STRUCTURAL_MA_CONFIRM_DAYS = 2            # 连续 N 日收盘 < MA20 才卖

# --- 反 FOMO 过滤（挡住临买前的追高） ---
ANTI_FOMO_ENABLE           = True
ANTI_FOMO_RET5_TH          = 0.10          # 5 日涨幅 > 10%
ANTI_FOMO_VOL_RATIO_TH     = 2.2           # 昨日量 / MA20 量 > 2.2
ANTI_FOMO_UPPER_SHADOW_TH  = 0.45          # 上影 / (H-L) > 0.45（三项同时命中才拦）

# --- 200 日均线例外通道（哪怕不在 MA200 上方，只要动量 & 量能合理也放行） ---
MA200_EXCEPTION_ENABLE     = True
MA200_EXC_MIN_RET5         = 0.03
MA200_EXC_VOL_RATIO_MIN    = 1.2
MA200_EXC_VOL_RATIO_MAX    = 3.2
REQUIRE_ABOVE_MA200        = False         # 冻结版并没有强制 MA200 之上；保持关闭

# --- 体制切换（宽度 = CSI 1000 中在 MA20 之上的比例） ---
REGIME_SWITCH_ENABLE       = True
REGIME_BREADTH_RISK_ON     = 0.60
REGIME_BREADTH_RISK_OFF    = 0.44
REGIME_TOPK_DELTA_ON       = 0
REGIME_TOPK_DELTA_OFF      = -1
REGIME_ENTRY_MULT_ON       = 0.5           # risk_on 时把调仓周期 *0.5（更勤）
REGIME_ENTRY_MULT_OFF      = 2.0           # risk_off 时 *2.0（更懒）
REGIME_EXPOSURE_MULT_ON    = 1.00
REGIME_EXPOSURE_MULT_OFF   = 0.74

# --- 月度动态仓位（在体制乘子基础上再截断到 [MIN, MAX]） ---
MONTHLY_EXPOSURE_MIN       = 0.80
MONTHLY_EXPOSURE_MAX       = 1.00

# --- 崩盘冷静期 ---
CRASH_RET5_TH              = -0.08
CRASH_RET20_TH             = -0.14
CRASH_BREADTH_TH           = 0.25
CRASH_COOLDOWN_DAYS        = 1

# --- 连亏冷静期 ---
PAUSE_AFTER_LOSS_DAYS      = 2             # 连续 2 个交易日整体收益 < 0
PAUSE_AFTER_LOSS_LEN       = 1             # 就停 1 天

# --- 因子历史窗口 ---
PRICE_HIST_DAYS            = 260            # 覆盖 60 日动量 + 200 日均线 + 缓冲
MIN_LIST_DAYS              = 60             # 上市不足 60 天的次新股跳过
MIN_TURNOVER_MONEY_YUAN    = 5_000_000     # 昨日成交额 < 500 万剔除

# --- 执行 ---
RUN_TIME                   = "09:35"
COMMISSION_OPEN            = 0.0003
COMMISSION_CLOSE           = 0.0013
SLIPPAGE_PCT               = 0.0005


# =============================================================================
# 初始化
# =============================================================================

def initialize(context):
    set_benchmark(BENCHMARK_INDEX)
    set_option("use_real_price", True)
    set_option("avoid_future_data", True)
    log.set_level("order", "error")
    log.set_level("system", "error")

    # 交易成本
    set_order_cost(
        OrderCost(
            open_tax=0,
            close_tax=0.001,
            open_commission=COMMISSION_OPEN,
            close_commission=COMMISSION_CLOSE,
            min_commission=5,
        ),
        type="stock",
    )
    set_slippage(PriceRelatedSlippage(SLIPPAGE_PCT))

    # 会随时间变化的运行时状态
    g.last_rebalance_date = None            # 上次调仓的自然日
    g.cost_book = {}                        # code -> 建仓价
    g.high_water = {}                       # code -> 持仓期最高价
    g.entry_date = {}                       # code -> 建仓日
    g.cooldown_until = None                 # 冷静期截止日（含）
    g.equity_yday = None                    # 昨日权益快照（用于连亏检测）
    g.consec_loss_days = 0                  # 连续下跌天数
    g.ma_break_streak = {}                  # code -> 连续 < MA20 的天数（v1.1）
    g.last_scored = None                    # 上次打分结果快照（v1.1，供 sell 用）

    # 每个交易日 09:35 触发主流程
    run_daily(main_flow, time=RUN_TIME, reference_security=BENCHMARK_INDEX)
    # 收盘后记账（15:01）
    run_daily(after_close_bookkeeping, time="15:01",
              reference_security=BENCHMARK_INDEX)


# =============================================================================
# 主流程
# =============================================================================

def main_flow(context):
    _refresh_hwm(context)                    # 每日 push high_water
    _update_ma_break_streak(context)         # v1.1 每日 push MA20 破位计数

    # 1. 打分（每天都算，供 sell 判断用；本地引擎每天都算）
    scored = _score_universe(context)
    if scored is not None and not scored.empty:
        g.last_scored = scored

    # 2. 体制识别 & 崩盘检测
    regime = _detect_regime(context)
    crash_now = _maybe_trigger_crash_cooldown(context, regime)

    # 3. 崩盘立刻全仓平（对齐本地 engine.py::_is_market_crash 路径）
    if crash_now:
        log.warning("[crash] 强制清仓所有持仓")
        _process_sells(context, force_all=True, cooldown=False,
                       sell_score=None, extra_sell_codes=None,
                       ignore_min_hold=True)
        return

    # 4. 冷静期（连亏 / 上一日崩盘残留）：只跑保护性卖出，不新买
    if g.cooldown_until is not None and context.current_dt.date() <= g.cooldown_until:
        log.info("[cooldown] 静默日 %s，只跑止损/止盈/结构化卖出，不开新仓",
                 context.current_dt.date())
        _process_sells(context, force_all=False, cooldown=True,
                       sell_score=g.last_scored, extra_sell_codes=None)
        return

    # 5. 到不到调仓日？（非调仓日也要跑保护性卖出）
    if not _is_rebalance_day(context, regime["entry_mult"]):
        _process_sells(context, force_all=False, cooldown=False,
                       sell_score=g.last_scored, extra_sell_codes=None)
        return

    if scored is None or scored.empty:
        log.warning("[score] 打分为空，跳过本次调仓")
        return

    # 6. 反 FOMO
    filtered = _apply_anti_fomo(context, scored) if ANTI_FOMO_ENABLE else scored

    # 7. 目标持仓 = 前 top_k
    top_k = max(1, TOP_K + regime["topk_delta"])
    target_codes = list(filtered.index[:top_k])

    # 8. 打印调仓前 top-K 明细（v1.1 加，方便对照本地）
    _log_top_picks(context, filtered, target_codes, top_k, regime)

    # 9. 卖出：掉出 top-K + 排名严重下滑 + 结构性 + 硬止损
    hold_codes = list(context.portfolio.positions.keys())
    to_sell_out = [c for c in hold_codes if c not in target_codes]
    _process_sells(context, force_all=False, cooldown=False,
                   sell_score=scored, extra_sell_codes=to_sell_out)

    # 10. 买入
    exposure = _target_exposure(context, regime)
    _process_buys(context, target_codes, exposure=exposure)

    g.last_rebalance_date = context.current_dt.date()


def after_close_bookkeeping(context):
    """收盘后更新连亏计数、清空已清仓的记账。"""
    equity_now = context.portfolio.total_value
    if g.equity_yday is not None:
        if equity_now < g.equity_yday:
            g.consec_loss_days += 1
        else:
            g.consec_loss_days = 0
    g.equity_yday = equity_now

    # 连亏冷静期
    if g.consec_loss_days >= PAUSE_AFTER_LOSS_DAYS:
        until = context.current_dt.date() + timedelta(days=PAUSE_AFTER_LOSS_LEN)
        if g.cooldown_until is None or until > g.cooldown_until:
            g.cooldown_until = until
            log.warning("[cooldown] 连亏 %d 天，冷静到 %s", g.consec_loss_days, until)
        g.consec_loss_days = 0

    # 清理不再持仓的记账
    live = set(context.portfolio.positions.keys())
    for book in (g.cost_book, g.high_water, g.entry_date):
        for k in list(book.keys()):
            if k not in live:
                book.pop(k, None)


# =============================================================================
# 打分：value + quality + momentum + low_vol
# =============================================================================

def _score_universe(context):
    universe = _get_tradable_universe(context)
    if not universe:
        return None

    # 基本面（value + quality）
    q = query(
        valuation.code,
        valuation.pe_ratio,
        valuation.pb_ratio,
        indicator.roe,
    ).filter(valuation.code.in_(universe))
    fund = get_fundamentals(q, date=context.previous_date)
    if fund is None or fund.empty:
        log.warning("[score] fundamentals 空")
        return None
    fund = fund.set_index("code")

    # v1.1: 只剔除 NaN，不硬砍 PE 区间——让 z-score 处理极端值
    # （本地 csi1000_enhanced 只做 pd.to_numeric，不砍范围；跟本地对齐）
    fund = fund[
        fund["pe_ratio"].notna()
        & fund["pb_ratio"].notna()
        & fund["roe"].notna()
    ]
    if fund.empty:
        return None

    codes = fund.index.tolist()

    # 价格特征
    px = get_price(
        codes,
        end_date=context.previous_date,
        count=PRICE_HIST_DAYS,
        frequency="daily",
        fields=["close"],
        panel=False,
        fill_paused=False,
        skip_paused=False,
    )
    if px is None or px.empty:
        return None

    # 长表 → 宽表（date × code）
    close = px.pivot_table(index="time", columns="code", values="close", aggfunc="last")
    close = close.reindex(columns=codes)

    ret_20 = close.pct_change(20).iloc[-1]
    ret_60 = close.pct_change(60).iloc[-1]
    vol_20 = close.pct_change().rolling(20).std(ddof=0).iloc[-1]

    feat = pd.DataFrame({
        "ret_20": ret_20,
        "ret_60": ret_60,
        "vol_20": vol_20,
    }).reindex(codes)

    x = fund.join(feat, how="inner")
    # 只在候选池内做 z-score（跟本地整池打分接近，误差可接受）
    value_z   = -0.6 * _zscore(x["pe_ratio"]) - 0.4 * _zscore(x["pb_ratio"])
    quality_z =  _zscore(x["roe"])
    mom20_z   =  _zscore(x["ret_20"])
    mom60_z   =  _zscore(x["ret_60"])
    lowvol_z  = -_zscore(x["vol_20"])

    score = (
        W_VALUE   * value_z
        + W_QUALITY * quality_z
        + W_MOM20   * mom20_z
        + W_MOM60   * mom60_z
        + W_LOWVOL  * lowvol_z
    )
    return score.sort_values(ascending=False)


def _zscore(s):
    s = pd.to_numeric(s, errors="coerce")
    m, sd = s.mean(), s.std(ddof=0)
    if not np.isfinite(sd) or sd <= 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - m) / sd


# =============================================================================
# 候选池：CSI 1000 成分 → 过滤 ST / 停牌 / 次新 / 流动性 / 涨跌停 / 科创创业
# =============================================================================

def _get_tradable_universe(context):
    raw = get_index_stocks(UNIVERSE_INDEX, date=context.previous_date)
    if not raw:
        return []
    cur = get_current_data()
    last_prices = history(1, unit="1d", field="close", security_list=raw)
    prev = context.previous_date

    # 昨日 money（成交额）用于流动性
    money = history(1, unit="1d", field="money", security_list=raw)

    keep = []
    for code in raw:
        info = cur[code]
        # 停牌 / ST / 退市
        if info.paused or info.is_st:
            continue
        if "退" in (info.name or ""):
            continue
        # 剔除科创（688 开头）、北交所（8/4 开头）——冻结版策略只跑主板 + 创业板
        # 创业板（300）也常见涨跌幅 20%、波动较大，保留但不特殊处理
        if code.startswith("688") or code.startswith("8") or code.startswith("4"):
            continue
        # 次新
        start_date = get_security_info(code).start_date
        if (prev - start_date).days < MIN_LIST_DAYS:
            continue
        # 流动性
        try:
            m_val = float(money[code][-1])
            if not np.isfinite(m_val) or m_val < MIN_TURNOVER_MONEY_YUAN:
                continue
        except Exception:
            continue
        # 涨跌停锁死（当前不能开仓；已持仓的另议）
        try:
            lp = float(last_prices[code][-1])
        except Exception:
            continue
        in_pos = code in context.portfolio.positions
        if not in_pos and lp >= info.high_limit:
            continue
        if not in_pos and lp <= info.low_limit:
            continue
        keep.append(code)
    return keep


# =============================================================================
# 反 FOMO
# =============================================================================

def _apply_anti_fomo(context, scored):
    codes = list(scored.index)
    if not codes:
        return scored
    px = get_price(
        codes, end_date=context.previous_date, count=25, frequency="daily",
        fields=["open", "high", "low", "close", "volume"],
        panel=False, fill_paused=False, skip_paused=False,
    )
    if px is None or px.empty:
        return scored

    # 按 code 拆
    drop = []
    for code, g_df in px.groupby("code"):
        if len(g_df) < 21:
            continue
        g_df = g_df.sort_values("time")
        close = g_df["close"].values
        vol   = g_df["volume"].values
        ret5  = (close[-1] / close[-6]) - 1.0 if close[-6] > 0 else 0.0
        vol_ratio = vol[-1] / (vol[-21:-1].mean() + 1e-9)
        hi, lo, op, cl = g_df["high"].iloc[-1], g_df["low"].iloc[-1], g_df["open"].iloc[-1], close[-1]
        rng = max(hi - lo, 1e-9)
        upper_shadow = (hi - max(op, cl)) / rng

        if (
            ret5 > ANTI_FOMO_RET5_TH
            and vol_ratio > ANTI_FOMO_VOL_RATIO_TH
            and upper_shadow > ANTI_FOMO_UPPER_SHADOW_TH
        ):
            drop.append(code)

    if drop:
        log.info("[anti_fomo] 挡掉 %d 只：%s", len(drop), [c[:6] for c in drop[:5]])
    return scored.drop(index=drop, errors="ignore")


# =============================================================================
# 体制识别（breadth-based）
# =============================================================================

def _detect_regime(context):
    universe = get_index_stocks(UNIVERSE_INDEX, date=context.previous_date)
    if not universe:
        return {"mode": "neutral", "breadth": 0.5,
                "entry_mult": 1.0, "exposure_mult": 1.0, "topk_delta": 0}

    px = get_price(
        universe, end_date=context.previous_date, count=25,
        frequency="daily", fields=["close"],
        panel=False, fill_paused=False, skip_paused=False,
    )
    if px is None or px.empty:
        return {"mode": "neutral", "breadth": 0.5,
                "entry_mult": 1.0, "exposure_mult": 1.0, "topk_delta": 0}

    above = 0
    total = 0
    for _, g_df in px.groupby("code"):
        if len(g_df) < 21:
            continue
        g_df = g_df.sort_values("time")
        cls = g_df["close"].values
        # v1.1 修 bug: MA20 应该是"昨日之前 20 天的均线"，不能把 T-1 塞进 MA 再和 T-1 比
        ma20 = cls[-21:-1].mean()          # 之前是 cls[-20:].mean() 把最后一天也算进去了
        if cls[-1] > ma20:
            above += 1
        total += 1
    breadth = above / total if total else 0.5

    if not REGIME_SWITCH_ENABLE:
        return {"mode": "off", "breadth": breadth,
                "entry_mult": 1.0, "exposure_mult": 1.0, "topk_delta": 0}

    if breadth >= REGIME_BREADTH_RISK_ON:
        return {"mode": "risk_on", "breadth": breadth,
                "entry_mult": REGIME_ENTRY_MULT_ON,
                "exposure_mult": REGIME_EXPOSURE_MULT_ON,
                "topk_delta": REGIME_TOPK_DELTA_ON}
    if breadth <= REGIME_BREADTH_RISK_OFF:
        return {"mode": "risk_off", "breadth": breadth,
                "entry_mult": REGIME_ENTRY_MULT_OFF,
                "exposure_mult": REGIME_EXPOSURE_MULT_OFF,
                "topk_delta": REGIME_TOPK_DELTA_OFF}
    # 中性档：线性插值
    span = max(REGIME_BREADTH_RISK_ON - REGIME_BREADTH_RISK_OFF, 1e-9)
    t = (breadth - REGIME_BREADTH_RISK_OFF) / span
    entry_mult = REGIME_ENTRY_MULT_OFF + t * (REGIME_ENTRY_MULT_ON - REGIME_ENTRY_MULT_OFF)
    expo_mult  = REGIME_EXPOSURE_MULT_OFF + t * (REGIME_EXPOSURE_MULT_ON - REGIME_EXPOSURE_MULT_OFF)
    return {"mode": "neutral", "breadth": breadth,
            "entry_mult": entry_mult, "exposure_mult": expo_mult, "topk_delta": 0}


def _maybe_trigger_crash_cooldown(context, regime):
    """指数级崩盘检测：ret5<-8% 或 ret20<-14% 或 breadth<0.25 → 立即清仓 + 冷静 N 天。
    返回 True 表示今天就是崩盘日（需要强制清仓）。"""
    px = attribute_history(BENCHMARK_INDEX, 21, "1d", ["close"], df=False)
    close = px["close"]
    if len(close) < 21:
        return False
    ret5  = close[-1] / close[-6] - 1.0
    ret20 = close[-1] / close[0] - 1.0
    triggered = (ret5 < CRASH_RET5_TH) or (ret20 < CRASH_RET20_TH) or (regime["breadth"] < CRASH_BREADTH_TH)
    if triggered:
        until = context.current_dt.date() + timedelta(days=CRASH_COOLDOWN_DAYS)
        if g.cooldown_until is None or until > g.cooldown_until:
            g.cooldown_until = until
            log.warning("[crash] ret5=%.2f%% ret20=%.2f%% breadth=%.2f → 清仓 + 冷静到 %s",
                        ret5 * 100, ret20 * 100, regime["breadth"], until)
    return triggered


# =============================================================================
# 目标仓位 & 调仓节奏
# =============================================================================

def _target_exposure(context, regime):
    base = 1.0 * regime["exposure_mult"]
    return float(np.clip(base, MONTHLY_EXPOSURE_MIN, MONTHLY_EXPOSURE_MAX))


def _is_rebalance_day(context, entry_mult):
    if g.last_rebalance_date is None:
        return True
    span = max(1, int(round(ENTRY_INTERVAL_DAYS * entry_mult)))
    return (context.current_dt.date() - g.last_rebalance_date).days >= span


# =============================================================================
# 卖出：止损 / 移动止盈 / 最小持仓天数 / 目标外
# =============================================================================

def _process_sells(context, force_all=False, cooldown=False, sell_score=None,
                    extra_sell_codes=None, ignore_min_hold=False):
    """卖出流程（v1.1 大重写，对齐本地 engine.py::risk-exits）
       优先级：
         1) 崩盘强平（force_all + ignore_min_hold） — 无条件卖
         2) 硬止损 (-5% cost) — 忽略 min_hold
         3) 移动止盈 (-10% from HWM)
         4) MA20 连续 2 日破位（结构化卖出）
         5) 排名严重下滑（sell_urgency > 阈值）
         6) 掉出 top-K（extra_sell_codes）
       cooldown=True 时只保留 1-4，不做 5-6（因为不打算再买回）
    """
    positions = dict(context.portfolio.positions)
    if not positions:
        return
    cur = get_current_data()
    today = context.current_dt.date()

    # 为 sell_score-based 退出准备"排名分位"：0.0=最强，1.0=最弱
    rank_pct_by_code = {}
    if sell_score is not None and not sell_score.empty:
        # sell_score 是 buy_scores（越高越应该买）；反过来当"应该卖"用
        n = len(sell_score)
        for i, code in enumerate(sell_score.index):
            rank_pct_by_code[code] = i / max(1, n - 1)  # 排名越前分位越低（=不该卖）

    sold = []
    for code, pos in positions.items():
        info = cur[code]
        if info.paused:
            continue

        entry = g.entry_date.get(code)
        held_days = (today - entry).days if entry else 999

        cost = g.cost_book.get(code, pos.avg_cost)
        price = pos.price
        hwm = g.high_water.get(code, price)
        loss_pct = (price - cost) / cost if cost > 0 else 0
        drawdown = (price - hwm) / hwm if hwm > 0 else 0

        must_sell = False
        reason = ""

        # (1) 崩盘强平：忽略一切保护
        if force_all and (ignore_min_hold or held_days >= MIN_HOLD_DAYS):
            must_sell = True; reason = "force_all"

        # (2) 硬止损：本地也是不受 min_hold 保护，急跌就卖
        elif loss_pct <= -STOP_LOSS_PCT:
            must_sell = True; reason = "stop_loss"

        # (3) 移动止盈
        elif held_days >= MIN_HOLD_DAYS and drawdown <= -TRAILING_STOP_PCT:
            must_sell = True; reason = "trailing_stop"

        # (4) MA20 连续 N 日破位（v1.1 新增结构化卖出）
        elif (STRUCTURAL_MA_BREAK_ENABLE
              and held_days >= MIN_HOLD_DAYS
              and g.ma_break_streak.get(code, 0) >= STRUCTURAL_MA_CONFIRM_DAYS):
            must_sell = True; reason = "ma20_break_%dd" % g.ma_break_streak[code]

        # (5) 排名严重下滑（v1.1 新增，对齐本地 sell_score_threshold=0.94）
        elif (not cooldown
              and held_days >= MIN_HOLD_DAYS
              and code in rank_pct_by_code
              and rank_pct_by_code[code] >= SELL_RANK_PERCENTILE_THR):
            must_sell = True
            reason = "rank_drop_p%.2f" % rank_pct_by_code[code]

        # (6) 掉出 top-K（原有逻辑）
        elif (not cooldown
              and extra_sell_codes
              and code in extra_sell_codes
              and held_days >= MIN_HOLD_DAYS):
            must_sell = True; reason = "out_of_top_k"

        if must_sell:
            _order_close(context, code)
            sold.append((code, reason, loss_pct))

    if sold:
        log.info("[sell] %s", [(c[:6], r, "%+.1f%%" % (l * 100)) for c, r, l in sold])


def _process_buys(context, target_codes, exposure):
    if not target_codes:
        return
    total_value = context.portfolio.total_value * exposure
    per_slot = total_value / max(1, len(target_codes))

    cur = get_current_data()
    for code in target_codes:
        if code in context.portfolio.positions:
            continue
        info = cur[code]
        if info.paused:
            continue
        # 涨停锁死跳过
        try:
            last_px = history(1, unit="1m", field="close", security_list=[code])[code][-1]
            if last_px >= info.high_limit:
                continue
        except Exception:
            pass
        _order_open(context, code, per_slot)


# =============================================================================
# 下单收口（带簿记）
# =============================================================================

def _order_open(context, code, target_value):
    if target_value < 5000:  # 一手都买不起就算了
        return
    order = order_target_value(code, target_value)
    if order is None or order.filled <= 0:
        return
    price = order.price or order.avg_cost or 0
    g.cost_book[code] = price
    g.high_water[code] = price
    g.entry_date[code] = context.current_dt.date()
    log.info("[buy]  %s value=%.0f price=%.3f", code, target_value, price)


def _order_close(context, code):
    order = order_target_value(code, 0)
    if order is None:
        return
    log.info("[close] %s filled=%s", code, getattr(order, "filled", None))


def _refresh_hwm(context):
    """每天把持仓的最高价往上走。"""
    positions = context.portfolio.positions
    if not positions:
        return
    for code, pos in positions.items():
        price = pos.price
        if not price:
            continue
        if code not in g.high_water or price > g.high_water[code]:
            g.high_water[code] = price


def _update_ma_break_streak(context):
    """v1.1 每天维护 code -> 连续 close < MA20 的天数。清仓的 code 自动清 0。"""
    positions = context.portfolio.positions
    if not positions:
        g.ma_break_streak.clear()
        return
    codes = list(positions.keys())
    try:
        px = get_price(
            codes, end_date=context.previous_date,
            count=STRUCTURAL_MA_WINDOW + 1, frequency="daily",
            fields=["close"], panel=False,
            fill_paused=True, skip_paused=False,
        )
    except Exception as exc:
        log.warning("[ma_streak] get_price failed: %s", exc)
        return
    if px is None or px.empty:
        return

    for code in codes:
        sub = px[px["code"] == code].sort_values("time") if "code" in px.columns else px
        cls = sub["close"].dropna().values
        if len(cls) < STRUCTURAL_MA_WINDOW + 1:
            continue
        ma20 = cls[-(STRUCTURAL_MA_WINDOW + 1):-1].mean()
        if cls[-1] < ma20:
            g.ma_break_streak[code] = g.ma_break_streak.get(code, 0) + 1
        else:
            g.ma_break_streak[code] = 0

    # 清掉已经卖掉的
    for k in list(g.ma_break_streak.keys()):
        if k not in codes:
            g.ma_break_streak.pop(k, None)


def _log_top_picks(context, scored, target_codes, top_k, regime):
    """v1.1 每次调仓打印 top-K 得分明细，方便对齐本地 backtest 的 buy_scores 输出。"""
    if scored is None or scored.empty:
        return
    picks = []
    for code in target_codes:
        s = float(scored.get(code, float("nan")))
        picks.append("%s=%.2f" % (code[:6], s))
    log.info("[rebalance] regime=%s breadth=%.2f expo=%.2f k=%d picks=%s (pool=%d)",
             regime["mode"], regime["breadth"],
             _target_exposure(context, regime), top_k,
             ",".join(picks), len(scored))
