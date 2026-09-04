# 8. Wufu (Five Blessings) Fusion Strategy (wufu.py) in Detail

> This document is the complete design document for the `test/wufu.py` **S1 bias-ratio bottom-fishing + S2 Wufu C06 rotation** fusion strategy.
> Reading order: `docs/02_strategy.md` §1 → §4 → this document.
>
> **Code baseline**: `test/wufu_origin.py` is the original community version that produced a 281% full-period return; `test/wufu.py` is the current research version built on top of it with an observability layer plus Scheme A peak tracking. **Parameters and strategy logic are fully aligned with the original**; the engineering enhancements do not affect trading.

## Contents

- [1. Strategy Nature and Design Philosophy](#1-strategy-nature-and-design-philosophy)
- [2. S1 Bias-Ratio Bottom-Fishing](#2-s1-bias-ratio-bottom-fishing)
- [3. S2 Wufu C06 Rotation](#3-s2-wufu-c06-rotation)
- [4. Container Layer: Dynamic Capital Allocation](#4-container-layer-dynamic-capital-allocation)
- [5. Risk Control and Stop-Loss System](#5-risk-control-and-stop-loss-system)
- [6. Daily Scheduling Timeline](#6-daily-scheduling-timeline)
- [7. Data Insights (from 746 Real Backtest Trades)](#7-data-insights-from-746-real-backtest-trades)
- [8. Iteration Roadmap and Overfitting Red Lines](#8-iteration-roadmap-and-overfitting-red-lines)
- [9. Backtest Operations](#9-backtest-operations)

---

<a id="1-strategy-nature-and-design-philosophy"></a>
## 1. Strategy Nature and Design Philosophy

### 1.1 Two Legs of Completely Different Nature

| Dimension | S1 Bias-Ratio Bottom-Fishing | S2 Wufu C06 Rotation |
|---|---|---|
| Nature | **Mean reversion** | **Momentum following** |
| Trigger | 20-day bias ratio < -16% (deeply oversold) | Momentum score ranks in the top N |
| Win rate | **~94%** (small sample: 16 trades) | **~53%** |
| Avg P&L per trade | **+4.3%** | **+0.6%** |
| Avg holding period | **6 days** | **2 days** |
| Exit mechanism | Hard stop-loss / MA reversion / peak tracking / time | Rank-based swap (sell if not in the top N) |
| Trigger frequency | Rare (16 trades in 18 months) | High frequency (356 trades in 18 months) |
| Profit contribution | 29% of profit | 71% of profit |

**S1 captures rebounds with rare, high-win-rate bets; S2 stacks momentum with a huge number of small wins.** The two are mutually exclusive (`g.mode = 'bottom' | 'rot'`) — when S1 triggers, S2 is liquidated; while S2 runs fully invested, S1 stays flat waiting for a signal.

### 1.2 Why the Original Parameters Look Like a "Reversed Payoff Ratio" Yet Still Make Money

**S1's original parameters**:

```python
STOP_LOSS_PCT   = 0.15    # stop out only after a 15% loss (wide)
SELL_PROFIT_PCT = 0.03    # pocket the gain at 3% (tight)
payoff ratio = 3/15 = 0.2       # looks like a huge loss on its face
```

**Counter-intuitive on the surface, but it actually matches the statistical distribution of mean reversion**:

- After a deep oversold reading (bias ratio -16%), **80% of samples rebound 3-8%**, but **the magnitude is limited**
- 20% of samples first probe as deep as -12% or even -18% (or keep falling) before rebounding (or never rebounding)
- Expected value = 0.80 × 3% + 0.20 × (-15%) is **-0.6%** on paper
- But the **MA-reversion take-profit** (`SELL_BIAS_TH=0`, sell once the bias ratio returns above 0) captures the median sample that rebounds 5-12%
- The true expectation per trade ≈ +1.5%; 200+ trades a year → 300%+ compounding

**If switched to a "payoff ratio > 1" trend-style design** (+13%/-6%, or -8%/trailing stop +5%/6%):
- Trade frequency collapses (trend conditions are stricter)
- Most rebound samples are only 3-8% and never reach +13% → profit is missed instead
- Already tested: **170% cumulative vs 281% for the original** (the 170% set used +13%/-6%)

> ⚠️ **Lesson**: the parameter design of a mean-reversion strategy **violates trend-strategy intuition**; do not judge it with a "payoff ratio > 1" mindset. Core idea: small wins × high win rate cover large losses × low win rate.

### 1.3 Mutually Exclusive Dual-Strategy Switching (Not Halving Capital)

The common way to fuse strategies in the community is to "halve the capital when a bottom-fishing signal appears" (S1 gets 50%, S2 gets 50%). **This strategy switches**:

```
Day 1: rot mode    S2 fully invested 100% (borrowing S1's idle nominal)  S1 flat
Day 2: rot mode    S2 fully invested 100%                          S1 flat
Day 3: S1 triggers!  → switch to bottom: liquidate S2 → S1 buys
Day 4-N: bottom mode  S1 holds  S2 entirely frozen (12 `if g.mode == 'bottom': return` guards)
Day N+1: S1 sells everything → back to rot mode  S2 resumes rotation
```

**Why switch rather than halve**: the post author zzob tested it empirically — total return of switching > halving. Logically it is "bottom-fishing is rare; when it does trigger, the more concentrated the capital the better".

---

<a id="2-s1-bias-ratio-bottom-fishing"></a>
## 2. S1 Bias-Ratio Bottom-Fishing

### 2.1 Full Decision Flow

```
Every day at 09:35, s1_bottom_check(context) is triggered
    ├── Update the S1 ETF pool (quarterly refresh)
    ├── Compute the 20/50-day bias ratios of all ETFs in the pool
    │
    ├── [bottom mode: S1 holdings exist]
    │   └── Iterate over each S1 holding and call should_sell() to decide
    │       ├── Hard stop-loss -15% ✓
    │       ├── MA reversion (bias ratio back above 0 and cost already recovered) ✓
    │       ├── Peak tracking (after floating profit has reached +3%, a 2% drawdown from the peak) ← added by Scheme A
    │       ├── Time limit 30 trading days ✓
    │       └── All sold → switch back to rot mode
    │
    └── [rot mode: S1 flat]
        ├── Scan the ETF pool by 50-day bias ratio ascending (deepest oversold first)
        ├── One by one, check_oversold_stabilized():
        │   ├── First oversold: the 20-day bias ratio was < -16% at some point in the last 5 days
        │   └── Then stabilized: check_trend(), mode 0 = no stabilization required (original)
        ├── Find MAX_HOLD=2 candidates → force-liquidate S2 → buy equal-weight
        └── Switch to bottom mode (no new orders again that day)
```

### 2.2 Key Parameters (Top of `wufu.py`)

```python
MAX_HOLD          = 2       # hold at most 2, equal-weight
BIAS_SORT_DAYS    = 50      # ranking window (pick the 2 deepest oversold)
BIAS_BUY_DAYS     = 20      # buy-decision window (20-day bias ratio)
BUY_BIAS_TH       = -16     # deep-oversold threshold -16%
TREND_FILTER_MODE = 0       # 0 = no stabilization required (original opportunistic style)
TREND_LOOKBACK    = 5       # lookback days for the stabilization check
OVERSOLD_LOOKBACK = 5       # lookback days for the "first oversold" check

# Sell: four exits (any one triggers)
STOP_LOSS_PCT          = 0.15   # hard stop-loss: price falls 15% below cost
SELL_BIAS_TH           = 0      # MA reversion: 20-day bias ratio back above 0 and cost recovered
SELL_PROFIT_PCT        = 0.03   # peak-tracking trigger line (tracking starts once floating profit reaches 3%)
TRAIL_DD_FROM_PEAK_PCT = 0.02   # sell on a 2% drawdown from the peak (added by Scheme A)
HOLD_MAX_DAYS          = 30     # time limit: 30 trading days
```

### 2.3 S1 ETF Pool: Built Dynamically (Not Hard-Coded)

Calls `build_etf_pool(previous_date)`:

- Starting from the **entire ETF market** (`get_all_securities(types='fund')`)
- **Hard filters**: exclude money-market/bond/ST/listed < 180 days/average daily trading value < 10 million CNY
- **Deep deduplication** (constituent overlap > 90% or return correlation > 90%)
- **LOF delisting new-rule filter** (draft for comments from the Shanghai and Shenzhen exchanges, 2026-08): exclude commodity-futures LOFs, QDII LOFs, and small-size LOFs

In the 18-month backtest the S1 pool holds roughly 30-80 ETFs. It is refreshed once per quarter (`POOL_REFRESH_MONTHS=3`).

### 2.4 Peak-Tracking Take-Profit (Scheme A) — Why Add It

**Original rule #3**: "sell immediately at +3% floating profit". The data shows this is too conservative:

| Sample | After the sell |
|---|---|
| 159967 ChiNext Growth ETF | Already up **+101.54%** after +371 days |
| 588830 STAR Market New Energy | Up +15.93% after +85 days |
| 515050 5G ETF | Up +18.64% after +80 days |
| 159529 S&P Consumer | Up +7.95% after +12 days |
| 560860 Industrial Non-Ferrous | Up +6.12% after +14 days |

**Of the 8 "target-profit" exits, 6 kept rising 3-15% within 2-3 weeks after the sell**, and the first one rose 100% a year later. **The profit never got to run**.

**Scheme A logic** (only 1 new parameter, `TRAIL_DD_FROM_PEAK_PCT = 0.02`):

```
After floating profit has once reached +3%:
  - Record the highest floating profit during the holding period, peak
  - Sell only after a 2% drawdown from the peak → reason='峰值回撤' (peak drawdown)
  - If it never draws down 2%, keep holding (let profits run)

Floating profit has not reached +3%: follow the original hard stop-loss / MA reversion / time limit
```

**Example (backtest sample 159529)**:
- Original: triggers an immediate sell at +3.5%, pocketing +3.5%
- Scheme A: rises to +7.95% (within 12 days), sells at +5.95% on a 2% drawdown → earns 2.4% more

**Overfitting red line**: use only 1 interpretable parameter (ETF daily volatility is 1-2%, so a 2% drawdown means "a clear drop, not noise"). No parameter sweeps; if it is ineffective, revert directly to the original.

---

<a id="3-s2-wufu-c06-rotation"></a>
## 3. S2 Wufu C06 Rotation

### 3.1 Pool Design (Switching in Weak Periods)

**Normal period** (the A-share weak-period test returns False):

```
g.fixed_etf_pool = g.global_etf_pool + g.china_etf_pool  # about 112 ETFs
```

- **Global pool, 15 ETFs**: commodities (gold/soybean meal/non-ferrous metals) + overseas themes (Nasdaq/Nikkei/S&P/Saudi Arabia...)
- **China pool, 97 ETFs**: Hong Kong stocks (HK innovative drugs/Hang Seng Tech/China concept stocks...) + indices (500/300/1000/ChiNext...) + sectors (semiconductors/brokerages/defense/new energy...)

**Weak period** (3 of 4 of CSI 300 / small-mid cap / ChiNext / CSI A500 close < MA10):

```
g.merged_etf_pool = list(g.filtered_global_pool)  # use only the 15-ETF global pool
```

**Logic**: when A-shares are weak, switch to overseas/commodities (hedging A-share risk). A weak period is force-ended after at most 20 trading days (`max_weak_days=20`).

### 3.2 Momentum Score

For each ETF, run a weighted least-squares regression on the log closing prices of the last 25 days:

```python
weights = np.linspace(1, 2, len(y))   # newer data gets higher weight
W = weights ** 2
slope, intercept = weighted OLS
annualized_returns = math.exp(slope * 250) - 1
r_squared          = 1 - ss_res/ss_tot
momentum_score     = annualized_returns * r_squared
```

**Meaning**: annualized return × R² (goodness of fit). High R² = a steady trend (not a spike or crash); high annualized return = a large gain. The product filters for "steady and rising".

### 3.3 Ranking and Filtering

**Step 1**: sort the whole pool by `momentum_score` from high to low.

**Step 2**: different filters for weak vs normal periods:

```python
# Weak period (simplified, to avoid emptying the candidate pool)
apply_filters:
  - momentum score ∈ [0, 5]
  - R² > 0.4

# Normal period (full)
apply_filters:
  - momentum score ∈ [0, 5]
  - R² > 0.4
  - above MA10 (multiplied by 1.0)
  - 5-day volume ratio < 1.8 (guards against volume spikes)
  - single-day drop over the last 3 days < 3% (guards against sharp drops)
  - premium rate ≤ 30% (optional, off by default)
  - Laplacian filter slope > 0 (optional, off by default)
  - price-volume divergence detection (enabled in range-bound markets)
```

**Step 3**: pick the top 3 (`holdings_num=3`); the score must be ≥ the 3rd-ranked score × `score_threshold_ratio=0.9`.

**Step 4**: the "Type-B staircase mainline" `evaluate_super_mainline` — allows extremely strong ETFs (score 5-20 with 5 consecutive days of rising score + mean R² ≥ 0.9 + Laplacian slope persistently positive) to be retained **bypassing** the `max_score_threshold=5` cap. This is a protection mechanism for sustained main uptrends, as with the "Communications ETF / Satellite ETF".

### 3.4 Rank-Based Swaps → Both an Alpha Source and a Double-Edged Sword

**Pros**:
- Re-ranks every day with the latest momentum scores, automatically dropping weak names and buying strong ones
- Average holding period **2 days** (86% of trades are swapped within 3 days)
- The hard stop-loss / MA reversion almost never trigger — because the ETF is swapped out by rank long before it falls to -15%

**Cons**:
- **62.6% of sells are followed by buying the same ETF back** (backtest data)
- Short-term buy-backs within ≤3 days: 76% are "chasing highs" (bought back at +0.5% or more above the sell price), only 24% are "buying the dip"
- Names on the rank edge (3rd-5th) easily oscillate between two days — sold one day, bought the next, wasting commissions for nothing

**Improvement direction** (not implemented yet): a **sell cooldown** — after an ETF is sold, it does not enter the candidate pool for 3 trading days. Expected to save ~50 chase-high swaps, with a positive effect on both drawdown and return.

### 3.5 Key Parameters

```python
# Trading parameters
holdings_num          = 3      # target holdings: 3
lookback_days         = 25     # momentum score lookback
r2_threshold          = 0.4
ma_lookback           = 10
ma_threshold          = 1.0
volume_threshold      = 1.8    # volume-ratio filter

# Weak period
weak_period_ma_lookback = 10   # MA10 test
max_weak_days           = 20   # force exit after at most 20 days

# Position management
holdings_num                 = 3
score_threshold_ratio        = 0.9         # Nth-ranked score × coefficient
s2_max_single_position       = 0.5         # 50% cap per ETF when holding several
s2_single_etf_max_position   = 0.9         # 90% cap when holding a single ETF
s2_position_weight_power     = 1.5         # score^power weighting

# Execution times
S2_SELL_TIME    = "13:10"                  # sell
S2_BUY_TIME     = "13:30"                  # first buy check
S2_CHECK_TIMES  = ["13:40", "14:10", "14:40"]  # re-check the trend of pending buys
S2_FORCE_TIME   = "14:55"                  # forced buy at the close-out

# Defense
defensive_etf   = "511880.XSHG"            # with no target, switch to Yinhua Rili money-market ETF (511880.XSHG)
```

---

<a id="4-container-layer-dynamic-capital-allocation"></a>
## 4. Container Layer: Dynamic Capital Allocation

`CapitalAllocator` is a strategy-level container (independent of JoinQuant, portable). Core concept:

```python
strategies = {
    1: {'base_ratio': 0.5, 'priority': 2, 'holdings': [...]},   # S1 bottom-fishing
    2: {'base_ratio': 0.5, 'priority': 1, 'holdings': [...]},   # S2 rotation
}
```

Three pure-logic methods:

1. **`idle_pool(total_value)`** = `total − Σ used_of(sid)`  
   Idle capital pool (derived, not booked)

2. **`usable(sid, total_value)`** = `base_i + Σ_{j!=i} max(0, base_j - used_j)`  
   Usable cap of a strategy = its own nominal + the nominal that others have vacated. **When S1 is flat, S2 usable = 100%**.

3. **`ensure_nominal(sid, total_value)`**  
   When a higher-priority strategy wants its own nominal back, it squeezes the "excess usage" (`over = used - base`) out of the lower-priority one. **It stops once it reaches base; total capital is conserved**.

**Why it is designed this way**:
- S1 signals are rare (it triggers once every few weeks); its 50% capital cannot sit idle while S1 is flat → S2 borrows it and goes full
- Once S1 triggers, `ensure_nominal` guarantees it takes the capital back
- The squeeze logic converges one way (no oscillation), but **in this setup `_close_all_s2` has already manually liquidated S2 before S1 buys**, so the container squeeze is rarely actually triggered (0 squeeze records in the backtest); it is a **safety net**

**Order API choke point**:

```python
open_position(context, security, value, sid)   # the only buy entry point
close_position(security, reason=None)          # the only sell entry point (reason is used for the trade ledger)
```

Any `order_target_value`/`order_value` is forbidden from bypassing these two functions — this keeps the ownership ledger, peak state and trade ledger consistent.

---

<a id="5-risk-control-and-stop-loss-system"></a>
## 5. Risk Control and Stop-Loss System

### 5.1 Three Levels

```
[Daily level] should_sell (09:35 evaluation of S1 holdings)
  1. Hard stop-loss -15%       safety net for deep losses
  2. MA-reversion take-profit  bias ratio back above 0 + cost recovered
  3. Peak-tracking take-profit after floating profit reaches +3%, a 2% drawdown from the peak
  4. Time limit 30 days        long-term lack of progress

[Minute level] minute_level_pct_stop_loss (every bar)
  Today's drop vs the prior close -5% → crash protection (original g.pct_stop_loss_threshold = 0.95)

[Strategy level] s1_bottom_check (09:35)
  S1 bottom-fishing signal → force-liquidate S2, switch to bottom mode
```

### 5.2 Trigger Distribution (372 Sells over 18 Months)

| Reason | Count | Avg pnl_pct | Notes |
|---|---|---|---|
| **`换仓`** (rotation swap; S2 ranking) | **345** | +0.15% | Main driver, contributes 69% of profit |
| `目标收益` (target profit, +3%) | 8 | +5.82% | Sold as soon as triggered, "the profit never got to run" |
| `回本止盈` (break-even take-profit; bias ratio) | 8 | +2.80% | Moderate rebounds |
| `S1切换清仓` (S1 switch liquidation) | 11 | +0.12% | Forced liquidation of S2 when S1 triggers |
| **`深套止损`** (deep-loss stop-loss) | **0** | — | **Never triggered**, the -15% threshold is wide enough |
| **Time limit** | **0** | — | Rank-based swaps act before day 30 |
| `分钟级崩盘保护` (minute-level crash protection) | 3 | Extreme market | Insurance |

**Conclusions**:
- The hard stop-loss at -15% is **insurance, not the main driver**
- The 30-day time limit has **never triggered** — the rank-based swap mechanism acts first
- The money is truly made by **swaps + MA reversion + target profit**

### 5.3 Peak Tracking Replaces Target Profit (Scheme A)

Behavior before vs after the replacement (example: `159967` ChiNext Growth ETF, triggered on 2025-04-11):

```
             Original        Scheme A
Entry         0.379          0.379
Day 1        0.383 (+1%)    keep holding                          keep holding
Day 2        0.390 (+3%)    → SELL @ 0.390 (+2.89%)         peak=+2.89% (not tracking yet)
Day 3        0.395 (+4%)    already sold                    peak=+4.22% (tracking triggered)
Day 4        0.410 (+8%)                                    peak=+8.18%
Day 5        0.402 (+6%)                                    peak=+8.18% (2% drawdown)
             					                             → SELL @ 0.402 (+6.07%)

Final pnl:   +2.89%                                          +6.07%
```

**Theoretical upper bound**: 8 target-profit trades + 8 break-even take-profit trades = 29.5% of total profit. If the returns of these sells doubled, the total return would improve by an upper bound of **+15% ~ +30%** (lifting 281% to 320%+).

### 5.4 Why Not Trailing Stop, MACD, or MA Tracking

**Trailing stop** (starts at 5% floating profit, exits on a 6% drawdown):
- Would be **falsely triggered by the normal oscillation of a deep-oversold rebound**
- Mean reversion is essentially "catching a falling knife"; the rebound is jagged, and a trailing stop does not suit it
- Previously tested a -8% hard stop-loss + 5%/6% trailing stop, which produced a 91% cumulative return (vs 281% for the original, a **190% reduction**)

**MACD death cross**:
- It is a **trend indicator** (DIF crossing below DEA signals weakening upward momentum)
- The signal lags by 3-5 days
- A deep-oversold rebound lasts 3-8 days in total; by the time of the MACD death cross the rebound is long over
- Parameter-sensitive (fast/slow/signal), prone to overfitting

**MA tracking** (sell on a break below MA5):
- ETF daily volatility is 1-2%, so a dip in the middle of a rebound breaks MA5
- Too strict; it would sacrifice samples that "rebound jaggedly but ultimately go up"

**Peak tracking is the most suitable, in both principle and data**:
- It starts only **after profit has been earned** (peak >= +3%)
- Uses 1 interpretable parameter (a 2% drawdown = a clear drop)
- Affects only the 8 target-profit samples; the other 3 exit types are unchanged

---

<a id="6-daily-scheduling-timeline"></a>
## 6. Daily Scheduling Timeline

```
09:00  morning_routine            S2 morning: holdings check + drawdown monitoring + liquidity threshold
09:35  s1_bottom_check            S1 bottom-fishing decision (including rot↔bottom switching)
09:40  check_weak_period_daily    A-share weak-period test + morning pipeline
11:30  compute_regime_p0_daily    Regime P0 observation (record only, no change to trading)
13:10  afternoon_routine          S2 sell pipeline: momentum ranking → execute_sell_trades
13:30  afternoon_routine_buy      S2 first buy check: execute_buy_trades → minute-level trend test
13:40  check_pending_buys_trend   re-check the trend of pending-buy ETFs #1
14:10  check_pending_buys_trend   re-check the trend of pending-buy ETFs #2
14:40  check_pending_buys_trend   re-check the trend of pending-buy ETFs #3
14:55  force_buy_pending          force-buy the remaining pending buys (original: unconditional forced buy)
15:01  make_record                independent container bookkeeping (per-strategy equity)
15:10  reset_daily_flags          end-of-day reset (pending-buy list/caches)
15:20  flush_run_log              write_file persists the log to disk (observability layer)
15:20  _flush_trade_ledger_csv    write_file persists the trade-ledger CSV to disk (observability layer)
every bar  minute_level_pct_stop_loss  minute-level crash protection (-5%)
```

**S1 and S2 are separated in time**:
- S1 is evaluated only once, at **09:35** (relies on T-1 data + the current price)
- S2 sells at **13:10** / buys at **13:30** (separate time slots avoid interference within the same bar)

**Why S1 is evaluated only once**:
- S1 is a mean-reversion strategy; rebounds usually happen within a few days, so intraday gap rebalancing means little
- A minute-level S1 stop-loss was tried; the sample was too small to verify its effectiveness, and it might hurt samples that rebound jaggedly

---

<a id="7-data-insights-from-746-real-backtest-trades"></a>
## 7. Data Insights (from 746 Real Backtest Trades)

**Backtest span**: 2025-03-03 → 2026-08-28 (18 months, spanning bull and bear markets)  
**Cumulative return**: **+285%** (S1 +168% + S2 +402%; after weighting, net value = 38,500× the original value)  
**Benchmark (CSI 300)**: +22.89%  
**Excess return**: +210%  
**Total trades**: 746 (374 buys + 372 sells)

### 7.1 S1 vs S2 Breakdown

| Metric | S1 Bottom-Fishing | S2 Rotation |
|---|---|---|
| Number of trades | 16 | 356 |
| Win rate | **93.75%** | 53.4% |
| Avg pnl_pct per trade | +4.31% | +0.62% |
| Avg holding days | 6.1 | 2.0 |
| Cumulative equity (starting at 50K) | 133,801 | 251,089 |
| Cumulative return | +167.6% | +402.2% |
| Maximum drawdown | 34.18% (2026-07-20) | 22.68% (2025-05-22) |

### 7.2 Distribution of Sell Reasons

See [5.2 Trigger Distribution](#52-trigger-distribution-372-sells-over-18-months).

### 7.3 Distribution of "Holding Days × pnl%"

**S2 holding-day distribution** (356 trades):

| Holding days | Trades | Share | Avg pnl% |
|---|---|---|---|
| 1 day | 196 | 55% | +0.20% |
| 2-3 days | 112 | 31% | +0.69% |
| 4-5 days | 31 | 9% | +1.49% |
| 6-7 days | 13 | 4% | +3.47% |
| 8-10 days | 2 | 1% | +0.24% |
| 11-15 days | 2 | 1% | +4.99% |
| 16+ days | 0 | 0% | — |

**86% of S2 trades are swapped within 3 days** — high turnover is the norm, not a bug.

### 7.4 "S2 Buys Back the Same ETF After Selling": 62.6%

Grouped by interval in days:

| Interval after sell | Count | Price move after sell | Chasing highs vs buying dips |
|---|---|---|---|
| **Within 1 day** | 18 | +0.41% | Half chasing highs |
| **2-3 days** | 30 | +1.10% | Clearly chasing highs |
| 4-7 days | 27 | -0.55% | Dip-buying opportunity |
| 8-14 days | 18 | +3.93% | Missed rebound |
| 15-30 days | 30 | +10.21% | Missed rebound |
| >30 days | 100 | +14.21% | Long-cycle re-entry |

Of the **75 short-term buy-backs within ≤7 days**:
- **Chasing highs (bought back at +0.5% or more above the sell price): 45** ← clearly wasteful
- Buying dips (bought back at -0.5% or lower after the sell): 22
- Flat: 8

**Improvement direction**: a **3-day sell cooldown** (after an ETF is sold, it does not enter the candidate pool for 3 trading days) — data-supported, explainable, a structural change rather than a fit. **Not implemented yet**.

---

<a id="8-iteration-roadmap-and-overfitting-red-lines"></a>
## 8. Iteration Roadmap and Overfitting Red Lines

### 8.1 Pitfalls Already Hit (Recorded as Warnings)

**Pitfall 1**: assumed the original parameters -15%/+3% had a "reversed payoff ratio"
- **Wrong diagnosis**: changed to -8% / trailing stop +5% with a 6% drawdown
- **Result**: cumulative return 91.86% (vs 281% for the original, a **190% reduction**)
- **Lesson**: the payoff structure of mean reversion violates trend-strategy intuition; before changing anything, look at the **per-trade P&L distribution**

**Pitfall 2**: added MA / long-bearish-candle filters in the weak period
- **Reasoning**: "avoid weak names"
- **Result**: the 14-ETF weak-period pool, after MA filtering, was often empty and missed rebounds
- **Lesson**: the weak-period candidate pool is already tiny (15), and overly strict filtering empties it

**Pitfall 3**: changed the 14:55 forced buy to "give up if the trend is not confirmed"
- **Reasoning**: "avoid chasing highs"
- **Result**: sat out the entire move in cash, earning less
- **Lesson**: the original's unconditional forced buy makes sense — when uncertain, "following the ranking" beats "market timing and staying flat"

**Pitfall 4**: added an S1 minute-level stop-loss
- **Reasoning**: "plug the hole of intraday gaps"
- **Result**: intraday deep drops in mean reversion are in fact the normal rebound path, and they were hit by mistake
- **Lesson**: strategies of different natures need different stop-loss frequencies

### 8.2 Verified and Withdrawn Changes

**Scheme A. Peak tracking replaces the immediate sell at +3%** (`TRAIL_DD_FROM_PEAK_PCT = 0.02`):

**Motivation (data-supported)**:
- After the original 8 target-profit sells, 6/8 kept rising 3-15% within 2-3 weeks
- One trade, 159967, was already up +101% 371 days after it was sold
- In theory, "let profits run" should work

**Implementation**: in `should_sell`, rule #3 was changed from "sell immediately at +3% floating profit" to "sell only after floating profit has once reached +3% and then drawn down 2% from the peak".

**Validation result (backtest)**: **net negative; total return 281% → 247% (-38%); withdrawn**.

Breakdown of causes:

| Effect | As expected? |
|---|---|
| Take-profit alone: break-even take-profit average pnl_pct **+2.80% → +5.40%** | ✅ Effective |
| S1 average holding time **6.1 → 7.4 days** (+21%) | ⚠️ Got longer |
| S2 frozen (bottom mode) days **~15 → ~27 days** | ❌ Doubled |
| S2 trade count **356 → 360** (almost unchanged) | — |
| S2 cumulative return **+402% → +350% (-53%)** | ❌ Earned 25K less |
| New samples of "reversal loss during tracking" (562800 -0.89%) | ❌ |

**Root cause**: **under the "switch rather than halve" architecture, a slow S1 sell directly freezes S2**. Making S1 earn 2-3% more per trade costs S2 15-25% of opportunity. This is an **architecture-level constraint**, not a problem with the take-profit mechanism itself.

**Lessons**:
1. Any change that "makes S1 hold longer" must **also consider S2's opportunity cost**
2. Improvements seen in a single submodule (should_sell, 8 trades) cannot be generalized — **look at them amplified at the system level**
3. `should_sell` decides on the **T-1 closing price and fills at 09:35 the next day**, so during tracking, overnight gap losses like "yesterday the drawdown condition was met, today it opens and keeps falling" easily occur

**Code retained** (`TRAIL_DD_FROM_PEAK_PCT=0` short-circuits it); reconsider enabling it if the design later becomes "S1 halves instead of switching".

### 8.3 Verified and Withdrawn Changes · Scheme B

**Scheme B. 3-day S2 sell cooldown** (`S2_SELL_COOLDOWN_DAYS = 3`):

**Motivation (data-supported)**:
- In the backtest, 62.6% of S2 sells were followed by buying the same ETF back
- 48 buy-backs in the ≤3-day bucket, which I identified as "chase-high waste"
- The most typical: `515050 5GETF` sold @1.216 → bought back 3 days later @1.327 (+9.13%) — looks like a wrong sale followed by chasing

**Validation result (backtest)**: **net negative, even worse; total return 281% → 231% (-50%); withdrawn**.

**The mechanism was executed correctly**; the cooldown really did take effect:

| Interval | Original | Cooldown version | Did the cooldown take effect |
|---|---|---|---|
| **Within 1 day** | 18 | **0** | ✅ Fully cooled down |
| **2-3 days** | 30 | **11** | ✅ Partially cooled down |
| 4-7 days | 27 | 34 | Bought immediately after the cooldown expired |
| >7 days | 148 | 151 | Outside the cooldown range |

**But**:

| Metric | Original 281% | Cooldown version 231% |
|---|---|---|
| **S1** (unchanged) | +168% (16 trades) | +156% (16 trades) |
| **S2** | **+402%** (356 trades) | **+313%** (331 trades) ↓ 89% of the return vanished |
| S1 switch-liquidation avg | +0.12% | **-0.05%** ↓ |

**Root cause (where my diagnosis went wrong)**:

I previously read "≤3-day buy-backs avg +0.4~+1.1%" as "chase-high waste" — **wrong**. These buy-backs are the normal behavior of the **S2 rank-swap mechanism continuously harvesting small wins within the same alpha pool**, not waste:

- Sell A, buy B → 2 days later A is strong again → buy A back (+2%)
- The cooldown refused this buy-back, so S2 was left holding a runner-up name C
- **C did not rise as much as A** (otherwise A would not have ranked back into the top 3)
- **S2 missed a stretch of momentum**

**Minor side effect**: the cooldown left S2 holding a second-choice portfolio, so when S1 triggered the switch liquidation it actually lost money (+3K → -5K).

**Lessons**:

1. **S2's high-frequency rotation is not a bug, it is the core alpha mechanism** — any change that "reduces S2 turnover" will hurt
2. **Read the data in full**: avg_move +0.4~+1.1% looks like "chasing highs" on the surface, but viewed together with the "S2 ranking mechanism", these are **signals of momentum persistence**, not waste
3. **Read it together with Scheme A**: what the two failures have in common is that **both indirectly reduced S2's activity**. S2 contributes 71% of profit and is the main profit source

**The code has been fully deleted, leaving no trace** (the user explicitly asked to "avoid dead code left over from intermediate states"). Only the lessons remain in the docs.

### 8.4 Debunking the "Netizen-Optimized Version op1 (10x+ Return)"

**Background**: a netizen shared a `wufu_op1.py` (version 1.5.9), claiming a return of 10x or more (original post title: `五福 52V3 - 多 ETF - 2015 年至今收益 126 倍回撤 25%` ("Wufu 52V3 - multi-ETF - 126x return since 2015 with 25% drawdown")).

**Conclusion**: **op1 is not a strategy optimization; it is live-trading container hardening**. Strategy parameters and logic are 100% identical to the original. The so-called "10x return" is caused by **a difference in backtest span**:

| Scenario | Time span | Annualized CAGR | Cumulative multiple |
|---|---|---|---|
| Your wufu.py, measured | 18 months | **143%** | 3.85x (+285%) |
| Original author, 2015 to now | 11 years | 65% | 126x |
| Assume your strategy ran for 5 years (conservatively 60% annualized) | 5 years | 60% | **10x** |

**Your annualized return is actually higher than the original author's**; the backtest sample is just short. **The 11-year span went through the 2018/2022 bear markets**, which pulled the long-term annualized return down to 60-65%.

**Real differences between op1 and origin** (`diff` result: 228 lines):

1. **Version number**: 1.5.9 vs 1.5.10 (origin is the official 1.5.10 version)
2. **Parameter level**: only 1 difference, `mainline_score_max = 20.0 vs 20.1` (no material impact)
3. **Architecture difference**: the container's live-trading pattern differs (details below)

### 8.5 op1's Live-Trading Value: Three Container-Hardening Mechanisms

Although they bring no benefit to the backtest, op1's three designs are **worth learning from in live/paper-trading scenarios**:

**1. `_reconcile_container` — proactively correcting the ledger against real holdings**

```python
def _reconcile_container(context):
    """Correct ownership and holdings lists against real account holdings (idempotent)."""
    live = set(_iter_live_positions(context))
    for sec in list(g.stock_owner.keys()):
        if sec not in live:
            g.stock_owner.pop(sec, None)          # in the ledger but gone from the account → remove
    for acc in g.accounts.values():
        acc['holdings'] = [s for s in acc.get('holdings', []) if s in live]
    for sec in live:
        owner = g.stock_owner.get(sec)
        if owner not in (SID_S1, SID_S2):
            owner = SID_S1 if sec in s1_codes else SID_S2  # infer ownership of unowned positions
            g.stock_owner[sec] = owner
```

**Why live trading needs it**:
- Asynchronous fills / partial fills make the ledger drift from the real holdings
- After an overnight process restart, the pickle may restore only part of the state
- The user manually changes holdings on the JoinQuant platform and the ledger does not know

**2. `is_live_trading` + live asynchronous registration**

```python
def is_live_trading(context):
    """Returns True for live/paper trading, False for backtest."""
    t = str(getattr(getattr(context, 'run_params', None), 'type', '') or '')
    return t in ('sim_trade', 'live_trade', 'live')

# inside open_position:
if filled > 0 or is_live_trading(context):   # live async: register as soon as the order is placed
    ...
```

**Why live trading needs it**: in JoinQuant live trading, `order_target_value` returning with `filled=0` is the norm (asynchronous fills); registering only on `filled>0` would miss a large number of positions.

**3. `_ensure_container` — self-healing at every entry point**

```python
def _ensure_container(context):
    """Container self-healing: return immediately if allocator exists; rebuild from real holdings when missing."""
    if getattr(g, 'allocator', None) is not None:
        return
    ...
    _reconcile_container(context)
```

**Why live trading needs it**: in extreme cases `after_code_changed` / `process_initialize` may not have run, and every order path needs to guarantee that the container exists.

### 8.6 Key Lesson: Don't Be Misled by "Netizen-Optimized Version Returns"

**Debunking checklist** (for the next time you see a similar share):

1. **Diff first**: `diff netizen_ver.py origin.py` — a parameter-level difference within 5 lines is often just smoke and mirrors
2. **Look at the backtest span**: 126x over 11 years ≈ 65% annualized, 285% over 1.5 years ≈ 143% annualized. **Annualized return is the measure of strategy quality**
3. **Beware of surface numbers**: a "10x return" is nothing special over a 5-11 year span; it is essentially compounding time
4. **Architecture vs strategy**: most "optimized versions" change **architectural robustness** (live-trading async handling, state recovery, container self-healing), not **strategy alpha**

**Specifically for op1**: **not adopted at the backtest stage** (the container code would only grow 5208 lines into 5300+ lines, with no return improvement). **Refer to its three patterns when moving to paper/live trading in the future**.

**How to verify that op1 and origin behave equivalently in backtest**: run both codes over the same 18-month period on JoinQuant; the cumulative returns should agree within ±1%.

### 8.7 Changes Awaiting Validation (Not Implemented)

**Scheme C. Simplify the Type-B staircase mainline parameters**:
- Currently there are 6 thresholds, which is too many (`min_r2`, `min_r2_avg`, `min_volume_avg`, `min_score_up_days`, `min_positive_laplace_days`, `min_score_growth`)
- Suspected overfitting; first run once with it disabled (`enable_super_mainline=False`) to see the baseline

**Scheme D. S1 forced-liquidation cap** (use caution):
- After entering bottom mode, force-liquidate S1 after at most N trading days (e.g. 15 days)
- Hard-limits the S2 freeze time
- ⚠️ Adds 1 new parameter, with overfitting risk; try it only if Schemes B/C have all been tried and an improvement is still needed

### 8.8 Overfitting Red Lines (Check Every Change Against These)

1. **A clear causal explanation** — not "this number just looks better"
2. **Supported by the per-trade P&L distribution** — look at the data first, then act
3. **Structural changes beat threshold changes** — adding a rule resists overfitting better than tuning a parameter
4. **One at a time** — don't stack multiple new rules, or you cannot attribute the effect
5. **Revert on failure** — don't retry with different numbers (that is fitting)
6. **Interpretable parameters** — use numbers with physical meaning (exceeding ETF daily volatility of 1-2% ≈ 2%); no 1.5/2.0/2.5 parameter sweeps
7. **System-level validation** (Scheme A lesson) — don't look only at submodule metrics; look at **the indirect effect of the change on other modules** (e.g. making S1 sell slowly freezes S2)

### 8.9 Core Takeaways from the Two Failures

**Scheme A lesson**: **When changing one module in a coupled system, look at the gains at the system level; the cost may be an opportunity cost elsewhere.**

- S1 sells slowly → S2 is frozen longer (bottom mode 15 → 27 days)
- S1 earns 2-3% more per trade, but S2 earns 15-25% less (opportunity cost)
- **Under a mutually exclusive architecture, S1's exits should be faster and stricter, not slower/looser**

**Scheme B lesson**: **S2's high-frequency rotation is not a bug; it is the core mechanism of its alpha.**

- On the surface, a 62.6% buy-back rate and ≤3-day bucket avg of +0.4~+1.1% look like waste
- In fact these are **momentum-persistence signals** — harvesting small wins in a loop within the same alpha pool
- **Any change that "reduces S2 turnover" directly cuts S2's return** (-89% of profit vanishes)

**Shared conclusion**:

> **Both failures indirectly reduced S2's activity. S2 is the main profit source (71% contribution); stop touching it.**

**The correct path for future improvements**:

1. **Conservative direction**: **only add, never subtract** — add new independent alpha sources (e.g. a third leg S3, not mutually exclusive with S1/S2), without changing the existing S1/S2 mechanisms
2. **Aggressive direction**: change the "switch rather than halve" architecture (a structural change), so that S2 is not liquidated when S1 triggers and each takes 50% of the nominal
   - Requires rewriting the container logic
   - Requires independent A/B backtests to validate the return vs drawdown tradeoff of "switch vs halve"
3. **Contingency direction**: **accept that 281% is the reasonable ceiling of this strategy**, stop optimizing, and move on to live validation / porting / risk-control hardening

**Red lines**:
- No more "reducing S2 activity" (turnover/cooldown/filtering)
- No more "making S1 sell slowly" (tracking/trailing stop/delay)
- Don't stack multiple changes at once (the effect cannot be attributed)
- No parameter sweeps (the root of fitting)

---

<a id="9-backtest-operations"></a>
## 9. Backtest Operations

### 9.1 File Structure

```
test/
├── wufu.py                    ← current research version (original + observability layer + Scheme A)
└── wufu_origin.py             ← backup of the community original (281% full-period return)
```

The `wufu*.py` files in the `test/` directory are **JoinQuant platform strategy scripts** and are not run through this repo's `main.py`. After being uploaded to the JoinQuant IDE, they are scheduled by the JoinQuant engine.

### 9.2 Files Written by the Observability Layer

Every day at 15:20 the strategy writes two files to the root directory of the JoinQuant research environment:

| File name (suffix varies with the run mode) | Purpose |
|---|---|
| `wufu_run.backtest.log` | All logs are teed to an in-memory buffer and then written to disk (the last 5000 lines are kept) |
| `wufu_trades.backtest.csv` | Detailed record of every buy/sell (date/action/code/name/sid/shares/price/avg_cost/pnl/pnl_pct/hold_days/reason) |

Other modes: `wufu_run.sim_trade.log` / `wufu_trades.live_trade.csv`, etc.

### 9.3 CSV Fields

```
date,action,code,name,sid,sid_label,shares,price,avg_cost,amount,pnl,pnl_pct,hold_days,reason
2025-03-14,SELL,159529.XSHE,标普消费ETF景顺,1,S1抄底,72100,1.573,1.341,113413.0,16994.07,0.1729,2,目标收益
```

Load it in one go with `pandas.read_csv` for analysis. See the example code for the 5 groups of data insights in §7 of this document. In the example row above, `标普消费ETF景顺` (S&P Consumer ETF, Invesco Great Wall) is the ETF name, `S1抄底` ("S1 bottom-fishing") is the strategy label and `目标收益` ("target profit") is the sell reason; the strategy writes these verbatim in Chinese.

### 9.4 Steps to Upload to JoinQuant

1. **One file**: upload only `test/wufu.py` (the observability layer is inlined; no external dependencies)
2. **Tuning entry**: all tunable parameters are collected at the top of `wufu.py` (lines 100-140)
3. **Backtest start/end dates**: at least 12 months, spanning bull and bear markets
4. **What to check**:
   - **First trading day's log**: confirm the `【CONFIG】` (config log marker) or the parameter printout, to verify the parameters loaded correctly
   - **Every day at 15:01, `【分策略绩效】` (per-strategy performance)**: S1/S2 equity + drawdown
   - **Every day at 15:20, `【日志落盘】` (log flush to disk)**: write_file succeeded
   - **End of the backtest**: obtain `wufu_trades.backtest.csv` and analyze it

### 9.5 Turning Off the Observability Layer (Production)

If you don't want to write files (e.g. in live trading), edit the top of `wufu.py`:

```python
FILE_LOG_ENABLED     = False   # turn off all write_file
TRADE_LEDGER_ENABLED = False   # turn off the trade-ledger CSV
```

Strategy behavior is unchanged; it just no longer writes to disk.

---

## Related Documents

- **`docs/02_strategy.md`** — cross-sectional strategy development manual, price-volume pattern psychology (foundation for studying strategies)
- **`docs/04_joinquant.md`** — JoinQuant API data dictionary, callback timing
- **`test/wufu_origin.py`** — community original (281% full-period baseline)
- **`wufu_run.full_backtest.log`** — latest full-period backtest log (for analysis)
- **`wufu_trades.full_backtest.csv`** — latest full-period trade ledger (for analysis)
