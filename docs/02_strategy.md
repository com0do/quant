# 2. Strategy and Signals

> This document merges 4 strategy-related docs: the cross-sectional strategy development guide, price-volume patterns and psychology, the A/B/C/D chart-pattern signal examples, and the strategy schools survey.
> Required reading before learning quant or studying JoinQuant community strategies.

## Table of Contents

- [1. Cross-Sectional Strategy Development Guide](#1-cross-sectional-strategy-development-guide)
- [2. Price-Volume Patterns and Market Psychology](#2-price-volume-patterns-and-market-psychology)
- [3. A/B/C/D Chart-Pattern Signal Examples](#3-chart-pattern-signal-examples)
- [4. Strategy Schools Survey and Integration Decisions (multi-factor / Turtle / Chan Theory)](#4-strategy-schools-survey)

---

<a id="1-cross-sectional-strategy-development-guide"></a>
# Cross-Sectional Strategy Development Guide (Restored Version)

## 1. Strategy Type Definition

- Strategy type: cross-sectional strategy
- Core action: compare stocks at the same point in time and buy the top N by score.

## 2. Factor Framework (directly implementable)

## 1) Factor Categories

- Momentum: 20-day / 60-day returns
- Value: PE, PB (extensible to PEV)
- Quality: ROE, gross margin, revenue growth
- Risk: volatility, turnover rate, liquidity
- Technical: MA, MACD, RSI, KDJ, volume trend, etc.

## 2) Basic Scoring Template

```text
Score = momentum * 0.3 + value * 0.3 + quality * 0.3 + low_volatility * 0.1
```

Example of executable definitions:

- Value score = `1 / PB`
- Quality score = `ROE`
- Momentum score = `20-day return`
- Low-volatility score = `1 / 20-day return standard deviation`

## 3. Trading Rule Parameters

- Rebalance cycle: daily / weekly / monthly (weekly/monthly are common for cross-sectional)
- Number of holdings: 20 / 30 / 50
- Stock universe: whole market / CSI 300 / CSI 500 / CSI 1000 / micro-cap
- Filters: ST, trading suspension, recently listed stocks (e.g. listed fewer than 60 days)

## 4. Risk Parameters (mandatory)

- Max single-stock position: 5%~10%
- Max industry position: 20%~30%
- Take-profit / stop-loss: fixed stop-loss + trailing stop-loss
- Maximum drawdown control: cut positions or stop trading once triggered

## 5. Backtest Parameters and Evaluation Metrics

## 1) Backtest Parameters

- Horizon: >= 5 years recommended (if data permissions are limited, validate on 1 year first)
- Trading costs:
  - Buy 0.03%
  - Sell 0.13%
- Slippage: at least 0.05%~0.1% recommended; raise to 0.1%~0.3% for small-cap stress tests

## 2) Core Evaluation Metrics

- Annualized return
- Maximum drawdown
- Sharpe ratio (rule-of-thumb threshold: > 1.5 is steadier)
- Win rate
- Profit/loss ratio
- Year-by-year stability (out-of-sample consistency)

## 6. Minimal Runnable Workflow

1. Prepare the data interface (JQ/QMT/local SQLite)
2. Write the factor computation
3. Write cross-sectional ranking and scoring
4. Write the rebalance and trade-execution logic
5. Backtest + out-of-sample validation
6. Track with small live capital (verify execution quality first)

## 7. Weekly Execution Example

1. Fetch the stock universe every Monday
2. Remove ST, suspended and recently listed stocks
3. Compute factors: momentum, low volatility, technical trend
4. Standardize and score with equal or custom weights
5. Pick the top 30 and hold them equally weighted
6. Rebalance again next Monday

---

<a id="2-price-volume-patterns-and-market-psychology"></a>

# Price-Volume Patterns and Market-Psychology Expectation Rulebook

> Goal: turn the "market psychology behind the chart" into executable quantitative rules that serve buy timing and sell-side risk control.  
> Scope: small-cap trading in CSI 1000 / CSI 2000, daily decisions + minute-level execution monitoring.

## 1. Core Idea: Read the Psychology First, Then Write the Rules

The key point you raised is spot on:  
**A double top / head-and-shoulders is not the lines themselves, but the shift in consensus among market participants on "whether the previous high can still be broken".**

In the quantitative implementation we do not "guess charts subjectively"; we do three steps:

- Use price structure to identify "whether repeated pushes to the top have failed"
- Use trading volume to judge "whether this rebound is healthy, or a bull trap luring in bag-holders"
- Use the trend framework (`MA200`/`MA50`) as a filter to decide whether opening positions is allowed

This amounts to mapping "what would I think if I were a market participant" into computable conditions.

---

## 2. Three-Layer Trend Framework: Long, Medium, Short (per your habit)

- **Long-term trend**: `MA200`
  - By default, do not buy symbols with `close < MA200`
  - Keep only a short-term exception channel (see Section 5)
- **Medium-term trend**: `MA50`
  - Used to identify whether the medium-term structure has turned bad (e.g. falling back to `MA50` after a top push)
- **Short-term behavior**: `ret_5` + volume ratio + `MACD`
  - Do not look only at a "red candle" (up candle); it must be combined with volume and MACD strength

---

## 3. Double Top / Head-and-Shoulders: Why They Lean Toward Sell Signals

Psychological process (quant view):

- First push to the top: disagreement between bulls and bears starts to appear
- Second push (or the head) fails to break through effectively: holders grow more uneasy
- Break below the neckline on rising volume: bullish conviction weakens, selling pressure is released

The current quantitative approximation in the system (`top_break_risk`):

- Multiple recent approaches to the high ("double top / multiple top" characteristics)
- A pullback below `MA50` with short-term weakening
- Volume expanding at the same time (`vol_ratio20` is high)

Result:

- Buy-score penalty
- Higher sell urgency

---

## 4. Inverse Double Bottom / Inverse Head-and-Shoulders: Why They Lean Toward Buy Signals

Psychological process (quant view):

- After a decline, the first rebound fails: the market is still pessimistic
- The second dip no longer makes a new low and selling pressure fades: marginal panic weakens
- Higher right shoulder + neckline breakout: capital consensus turns, and the trend may reverse

Quantitative mapping (current implementation):

- Confirmed low-level rebound: `ret_5 > 0`, `close > MA5`, `MA5 >= MA10`
- MACD strengthening: `DIF > DEA` and `HIST > 0`
- Volume match: `vol_ratio20` expands moderately (avoid rebounds on no volume)
- Still bounded by `MA200/MA50` (to prevent blind bottom-fishing inside a long bear structure)

---

## 5. "Dead-Cat Bounce / Bull Trap" Defense (something you care about in particular)

The scenario you mentioned:  
**In a decline, a rising line is suddenly pulled up on abnormally large volume; it is likely retail picking up the bags, followed by further declines.**

The risk criteria already built in (`trap_rebound`):

- Background still weak: `MA20 < MA60` or `ret_20 < 0`
- Short-term bounce: `ret_5 > 0`
- Abnormal volume surge: `vol_ratio20 > 1.8`
- MACD not strengthening: `HIST < 0` and `DIF < DEA`

Handling:

- Significant buy-score deduction (do not chase by default)
- Higher sell urgency (existing holdings are reduced / stopped out first)

---

## 6. The "Short-Term Exception" Below MA200

You stressed "normally we don't trade below MA200, but short-term is an exception".  
The system already supports an exception channel (on by default):

- `ret_5 >= 0.04`
- `vol_ratio20` within `[1.2, 2.4]`
- `MACD: DIF > DEA`

Only when all are satisfied is a small short-term participation allowed; otherwise it is filtered out directly.

---

## 7. Mapping to Current Strategy Modules

- `low_heat_quality_rebound`:
  - Leans toward "defensive mid-term + low-level rebound"
  - Adds `trap_rebound`, `top_break_risk`, and `MA200/MA50` penalties
- `adaptive_regime_midterm`:
  - Leans toward "market regime switching"
  - Adds the above price-volume-MACD filters in both the low-level and high-level pullback sub-scores

---

## 8. Practical Interpretation Template (for post-trade review)

When "long lower shadow + volume surge + small bounce" appears, first ask three questions:

- Is this a weak zone below `MA200`?
- Has MACD genuinely strengthened (rather than a weak bounce)?
- Is the volume surge "moderate absorption" or an "abnormal spike and pullback"?

If the answer is `weak zone + weak MACD + abnormal volume surge`, treat it as a bull trap and avoid taking the bag.

---

## 9. Important Disclaimer

- Market psychology cannot be "seen through"; it can only be approximated by "behavioral proxy variables".
- A chart pattern is never a single-point signal; it must be used together with trend, volume and risk budget.
- These rules rank below the iron laws of capital safety (total position, per-trade risk, pause after consecutive losses, etc.).


---

<a id="3-chart-pattern-signal-examples"></a>

# Chart-Pattern Signal Examples (A/B/C/D) and Trading Details

> This document maps the buy/sell points in the illustrations to executable in-system signals, and adds live-trading details such as "do not rush to sell on a normal pullback; defend first on an abnormal climax top".  
> Keywords: trend line, support/resistance, head-and-shoulders (top/bottom), breakout confirmation, price-volume and MACD resonance.

## 1. Basic Definitions (unify the vocabulary first)

- `support_20`: 20-day dynamic support (rolling lowest price)
- `resistance_20`: 20-day dynamic resistance (rolling highest price)
- `support_break_down`: close breaks below support (support breakdown)
- `resistance_break_up`: close breaks above resistance (upward breakout)
- `vol_ratio20`: current trading volume / 20-day average volume (strength of volume expansion)
- `macd_dif > macd_dea`: MACD bullish confirmation
- `ma20 > ma60`: short/medium-term trend is up

Note: support/resistance are computed dynamically on a rolling basis, not by hand-drawing fixed lines.

---

## 2. Chart A: Support Breakdown in an Uptrend (sell point)

### Meaning of the Pattern

- After several oscillations in an uptrend, key support is lost, meaning the original structure is broken.

### Quantitative Mapping

- `support_break_down = 1`
- `uptrend_break_down = 1` (breakdown against an uptrend background)
- If `vol_ratio20 > 1` (especially > 1.3), treat it as a "breakdown on heavy volume", which carries higher risk

### Trading Actions

- Significant buy-score deduction
- Sell score rises, and the priority of reducing/closing positions goes up
- If an abnormal climax-top reversal appears at the same time (see Section 6), lean directly toward defense

---

## 3. Chart B: Head-and-Shoulders Top After an Uptrend + Neckline Breakdown (sell point)

### Meaning of the Pattern

- In essence: "an increasing number of failed pushes higher + the key neckline (support) is broken".
- Market psychology shifts from "buy the dip" to "sell the rally".

### Quantitative Mapping (proxies; no manual geometric recognition)

- `top_break_risk = 1` (top-failure risk)
- Often accompanied by `support_break_down = 1`
- If the upper shadow is long, or there is a spike and pullback on heavy volume, sell urgency rises further

### Trading Actions

- Strong buy penalty
- Faster selling (stacks with stop-loss and trailing stop-loss)

---

## 4. Chart C: Breakout Above Resistance After a Decline (buy point)

### Meaning of the Pattern

- After a downtrend structure, the first valid breakout above resistance often marks the start of a trend reversal.

### Quantitative Mapping

- `downtrend_break_up = 1`
- For a "breakout after multiple bottom tests", `inverse_top_break_signal = 1`
- When `macd_dif > macd_dea` and volume is not overheated (commonly 0.9~1.8x), buy credibility is higher

### Trading Actions

- Raise the buy score
- Still bounded by the main trend (e.g. MA200 and risk-control gates)

---

## 5. Chart D: Rhythm of Adding, Selling, and Re-Buying (avoid frequent flip-flopping)

### Meaning of the Pattern

- A trend is not a straight line; it typically shows "advance - pullback - advance again".
- A good strategy does not trade every fluctuation, but "acts after the structure is confirmed".

### Quantitative Mapping

- `resistance_break_up` + healthy trend -> allow adding / holding
- `support_break_down` / `top_break_risk` -> lean toward selling
- Breakout again after a pullback -> triggers the buy point again (second entry)

### Trading Actions

- Use score aggregation rather than an all-or-nothing single signal, to reduce noise trading
- Combine with `entry_interval_days` and `min_hold_days` to control trading cadence

---

## 6. Key Supplement: Prevent "Acting Too Early / Exiting Too Early"

This is the part of live trading that most easily drags down returns; the system implements two complementary signals:

### 1) `normal_pullback_signal`: Normal Pullback, Do Not Rush to Sell

#### Intent of the Determination

- The goal is to identify "healthy pullbacks within an uptrend", so that a normal shakeout is not misread as a reversal.

#### Typical Conditions (logic)

- Prior upward momentum exists (e.g. 20-day gain, moving-average relationships still healthy)
- There is a drawdown from the recent high, but the magnitude is within an "acceptable range"
- The pullback phase is not a volume-driven panic (volume does not run out of control)
- Down-day volume is weaker than up-day volume (selling pressure is controlled)

#### Trading Actions

- If a sell is triggered only by the sell score, but `normal_pullback_signal` is hit at the same time, postpone the sell
- Purpose: extend the holding time of trend positions and reduce "getting shaken off the train"

### 2) `abnormal_climax_reversal`: Abnormal Climax-Top Reversal, Defend Early

#### Intent of the Determination

- Identify "short-term overheated climax top + confirmation of weakening", to prevent profits from being given back quickly.

#### Typical Conditions (logic)

- A rapid short-term rally + clearly heavy volume (overheating)
- A spike-and-pullback shape appears (e.g. upper-shadow risk)
- A subsequent decline confirms the weakening

#### Trading Actions

- Directly raise sell priority (can override the "keep holding" logic)
- Purpose: shrink risk before top risk expands

### Relationship Between the Two (most important)

- `normal_pullback_signal` is responsible for "don't sell too early"
- `abnormal_climax_reversal` is responsible for "don't sell too late"
- Together, the core is: **hold patiently within a trend; defend decisively when something abnormal happens**

---

## 7. Order of Operations From Chart Intuition to System Execution

1. First look at the long/medium-term structure (`MA200`, `MA50`, `MA20/MA60`)
2. Then look at pattern confirmation (support/resistance breakdowns, top/bottom risk proxies)
3. Finally look at volume + MACD (filter true vs. false breakouts)
4. Use `normal_pullback_signal` and `abnormal_climax_reversal` to control "how early or late to sell"

This turns "chart experience" into a rule system that is backtestable, reviewable and explainable.


---

<a id="4-strategy-schools-survey"></a>

# Strategy Schools Survey and Integration Decisions

> This document evaluates three common A-share retail/private-fund schools — **multi-factor (cross-sectional), Turtle (trend breakout), and Chan Theory (structural technical analysis)** — on whether they should be integrated into this repo, and in what priority.
>
> **Important disclaimer**: this document is only a record of decisions and evaluations. As of writing, the repo has **only multi-factor/cross-sectional implementations** (`csi1000_enhanced`, `simple_3factor`, `jq_multifactor_clone`, `value_activity_dow`, `dow_trend`, `adaptive_regime_midterm`, `low_heat_quality_rebound`, `price_only_cross_section`). Turtle and Chan Theory are **not yet implemented**. At all times, the docs must stay aligned with facts and implementation.

---

## 1. The Three Schools at a Glance

### 1) Multi-Factor / Cross-Sectional

- **Core logic**: at the same point in time, score all stocks on factors such as "momentum + value + quality + low volatility + sentiment", hold the top N, and rebalance periodically.
- **Representative implementation**: this repo's main line; over 60% of the strategies shared in the JoinQuant community are of this kind.
- **Location in the repo**:
  - `quant/stock_strategy/csi1000_enhanced.py` (5 factors: value + quality + mom20 + mom60 + low_vol)
  - `quant/stock_strategy/simple_3factor.py` (3-factor baseline)
  - `quant/stock_strategy/jq_multifactor_clone.py` (local clone of the JoinQuant multi-factor sample)
- **Pros**: explainable, backtestable, optimizable, parallelizable; relatively stable out-of-sample performance.
- **Risks**: a crowded space with continuously decaying alpha; needs periodic walk-forward validation.

### 2) Turtle Trading Rules (Turtle / Donchian Breakout)

- **Core logic**: enter on a 20/55-day Donchian channel breakout; ATR sets the volatility unit (the N value); pyramid adds (add one unit for every 0.5N up, at most 4 units); stop-loss at 2N or on a 10-day reverse channel.
- **Location in the repo**: **not implemented**. Searching for `turtle` / `donchian` / `ATR` finds no matches. `dow_trend.py` is **not Turtle**; it is just a simple moving-average alignment score.
- **Pros**: extremely simple logic; solid returns in trending markets; decades of historical validation; few parameters so little room for overfitting.
- **Risks**:
  - Consecutive false breakouts in choppy markets lead to large drawdowns;
  - Limit-up/limit-down boards on A-share small caps make breakouts hard to capture;
  - The add-on mechanism is hard to size on a small account (100 shares per lot);
  - Channel breakout is essentially "chasing highs", which conflicts with the repo's existing `anti_fomo_filter_enable` logic.

### 3) Chan Theory

- **Core logic**: fractal → stroke → segment → central pivot zone → three classes of buy/sell points. It identifies market structure through pattern analysis.
- **Location in the repo**: **not implemented**. Searching for `缠论` ("Chan Theory") / `中枢` ("central pivot zone") / `分型` ("fractal") finds no matches.
- **Pros**: fairly recognizable on central-zone breakouts in choppy markets; has a loyal following.
- **Risks**:
  - Definitions are fine-grained but have many subjective parameters (e.g. "fractal strength threshold", "central-zone overlap ratio"), so quantitative implementations overfit easily;
  - Implementations differ enormously between schools, and community code quality is uneven;
  - Conflicts with ADR-3 (≤ 3 core factors per strategy) — Chan Theory needs multi-layer nested determinations, which look highly explainable but are actually very brittle;
  - Works mainly at intraday/hourly level, which does not match this repo's daily decision cadence;
  - Lacks reproducible statistical evidence in A-share empirical research.

---

## 2. Decision Matrix

| Dimension | Multi-factor | Turtle | Chan Theory |
|------|--------|------|------|
| Compatible with ADR-3 (≤3 factors, explainable) | ✅ Satisfied by design | ✅ Very few parameters | ❌ Many subjective parameters |
| Compatible with the existing engine (cross-sectional scoring) | ✅ Directly compatible | ⚠️ Needs a "signal-type" integration | ⚠️ Structural mismatch |
| Compatible with existing risk control / anti-chasing | ✅ | ⚠️ Chasing highs by nature; `anti_fomo` must be relaxed | ✅ |
| Compatible with JoinQuant data | ✅ | ✅ Daily bars suffice | ✅ Daily bars suffice |
| Fit for A-share small caps | ✅ Main battlefield | ⚠️ Limit-up/limit-down interference | ⚠️ Needs minute data |
| Integration effort (person-day estimate) | Already exists | 3-5 days | 15-30 days |
| Go-live pass rate (walk-forward, our experience) | Medium | Medium-low | Unknown |

## 3. Integration Decisions

### Main Line: Multi-Factor / Cross-Sectional (keep deepening, don't change the structure)

- Keep `csi1000_enhanced` as the frozen production version.
- Keep using `walk_forward` + `factor_analyze` for periodic health checks.
- When factors decay, first use `online_learning.py` (EGD) for slow weight fine-tuning; if that is not enough, downgrade to `simple_3factor` (fewer factors, less overfitting).

### Secondary Line (pilot recommended): Turtle / Channel Breakout

- **Priority**: medium-low (does not affect the frozen version; serves as another leg of the multi-strategy pool).
- **Implementation plan**:
  1. Add `quant/stock_strategy/turtle_breakout.py`:
     - Entry: break above the 20-day Donchian upper band + volume confirmation (`vol_ratio20 >= 1.2`)
     - Add-ons: add once for every 0.5 × ATR20 up, at most 3 times
     - Stop-loss: 2 × ATR20 or a break below the 10-day Donchian lower band, whichever triggers first
     - Take-profit: break below the 10-day Donchian lower band
  2. Register it in `registry.py`; parameters go in the `[strategy]` section of `config/*.toml`.
  3. Run walk-forward with small positions (`main.py --mode walk-forward`); all three windows must show positive excess return before it may enter the ensemble.
  4. Run an equal-weight ensemble with the main strategy using 20-30% of the position, and observe for half a year.
- **Termination condition**: Sharpe ratio < 0.3 for 6 consecutive months, or maximum drawdown > 1.5x the main strategy's, then retire directly.
- **What not to do**: do not hook up a separate new engine architecture just for Turtle. It must adapt to the existing `buy_scores` / `sell_scores` interface, even if some bridging code is needed.

### Deferred: Chan Theory

- **Priority**: low (**not integrated** for now).
- **Reasons**:
  - Serious conflict with ADR-3 (multi-layer rules, strong parameter dependence).
  - Needs minute data to show its edge, which does not match this repo's daily cadence.
  - Community quant implementations vary in quality, and there is no reproducible A-share empirical evidence.
- **Re-evaluation conditions** (reopen only when all three are met):
  - An official or authoritative academic paper provides a reproducible A-share Chan Theory backtest;
  - Our minute-data pipeline is mature and has been in production for over a year;
  - There is clear research headcount willing to invest ≥ 4 weeks for a full implementation + walk-forward validation.
- **Until then**: if you are curious about Chan Theory, write pure research scripts in the `test/` directory first, **and do not enter the `quant/stock_strategy/` main path**.

---

## 4. General Gates for Any New School to Enter Production

1. **Code**: must implement the `buy_scores(ctx)` / `sell_scores(ctx)` interface and register in `registry.py`.
2. **Backtest**: `main.py --mode backtest` runs through and produces the same metrics structure as the main strategy.
3. **Validation**: `main.py --mode walk-forward` shows positive excess return in at least two of the three windows; the worst-window drawdown does not exceed 1.3x the main strategy's.
4. **Paper trading**: at least one week of stable `broker_type = "paper"` operation.
5. **Docs**: add a `05_XXX.md` in this directory, clearly documenting the school's logic, parameter meanings, validation results, and retirement conditions.
6. **Risk control**: fully obey `10_foundation/03_risk_rulebook.md`; do not build your own risk control that bypasses existing limits.

---

## 5. Update History

- First version: recorded the comparison of the three schools and the integration decisions. The repo currently has only multi-factor/cross-sectional implementations. Turtle is planned to be added (secondary line); Chan Theory is deferred.
