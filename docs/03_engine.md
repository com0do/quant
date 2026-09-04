# 3. Engine and Parameters

> This document merges 3 engine/run-related docs: the engine and parameter reference, the frozen runbook, and the submission checklist.
> Read this one when you want to tune parameters, run backtests, or prepare a commit.

## Table of Contents

- [1. Engine and Parameter Reference](#1-engine-and-parameter-reference)
- [2. Frozen Runbook](#2-frozen-runbook)
- [3. Submission Checklist](#3-submission-checklist)

---

<a id="1-engine-and-parameter-reference"></a>
# Engine and Parameter Reference (Final)

This document targets the current frozen setup and explains three things:

1. What the parameters mean (`config/final_freeze_2025.toml`)
2. How the backtest engine works (`quant/backtest/engine.py`)
3. How `regime_select` (`_calc_regime_switch_params` in the code) decides its state and takes effect

---

## 1) Parameter Reference (by module)

### Data

- `source`: data source, currently `sqlite`
- `sqlite_db_path`: main DB path (prices, snapshots, index daily bars)
- `start_date` / `end_date`: backtest time window
- `benchmark_index`: benchmark index code (currently CSI 1000)

### Strategy - Core Trading Parameters

- `strategy_names` / `strategy_weights`: strategy list and weights
- `buy_score_threshold` / `sell_score_threshold`: buy/sell score thresholds
- `top_k` / `max_positions`: number of candidates per round and maximum number of holdings
- `entry_interval_days`: rebalance/entry cadence (in trading days)
- `stop_loss_pct`: fixed stop-loss
- `trailing_stop_pct`: trailing stop-loss
- `min_hold_days`: minimum holding days

### Strategy - Risk and Entry Constraints

- `max_single_trade_risk_pct`: per-trade risk budget
- `max_single_position_loss_pct`: maximum tolerable loss per stock
- `max_total_exposure_pct`: portfolio total exposure cap
- `open_vol_min` / `open_vol_max`: volatility-window filter (too low means no elasticity, too high means unstable)
- `require_above_ma200`: whether to force entries only above MA200
- `allow_short_below_ma200_exception` and `ma200_exception_*`: exception conditions for below MA200

### Strategy - Market Filter and Dynamic Position Sizing

- `market_regime_gate_enable`: master switch for the market gate
- `market_regime_min_win_prob`: lower bound of the combined market/stock win probability
- `market_regime_min_breadth`: lower bound of market breadth
- `monthly_dynamic_exposure_enable`: monthly soft dynamic position sizing
- `monthly_exposure_min_pct` / `monthly_exposure_max_pct`: monthly lower/upper bounds on position size
- `crash_ret5_threshold` / `crash_ret20_threshold` / `crash_breadth_threshold`: hard crash criteria
- `crash_cooldown_days`: number of flat (no-position) cooldown days after a crash

### Strategy - Regime Select (parameter switching)

- `regime_param_switch_enable`: parameter-switching switch
- `regime_switch_breadth_risk_on` / `regime_switch_breadth_risk_off`: breadth thresholds for risk-on/off
- `regime_switch_top_k_delta_on` / `_off`: increment/decrement applied to `top_k` in the risk state
- `regime_switch_sell_thr_delta_on` / `_off`: offset applied to the sell threshold in the risk state
- `regime_switch_entry_interval_mult_on` / `_off`: multiplier applied to rebalance frequency in the risk state
- `regime_switch_exposure_mult_on` / `_off`: multiplier applied to the available exposure cap in the risk state

### Execution

- `buy_fee` / `sell_fee`: trading fee rates
- `slippage_bps`: slippage (bps)

---

## 2) How the Engine Works (day by day)

The engine's main flow can be summarized as:

1. **Load and feature construction**
   - Fetch prices, snapshots and factors from SQLite
   - Build wide tables and technical features (MA, MACD, volume, chart-pattern signals)

2. **Daily loop**
   - Update market state (breadth, trend, crash criteria)
   - Generate the day's tradable pool (trading suspension / liquidity filters + optional valuation filter)
   - Compute `buy_scores` / `sell_scores`

3. **Risk control first, then trading**
   - On a crash trigger, close all positions first and enter cooldown
   - Routine risk exits: stop-loss, trailing stop-loss, sell-score signals
   - On rebalance days, align holdings to the target and open new positions

4. **Position-size and order constraints**
   - Total exposure cap constraints (static + monthly dynamic + regime_select)
   - Per-trade risk budget and the capital budget jointly constrain order size
   - Trading costs are applied uniformly per the `execution` config

5. **Output**
   - Equity curve, trade details, core metrics (annualized return, excess return, Sharpe ratio, drawdown)

---

## 3) regime_select Logic (`_calc_regime_switch_params`)

This logic is "parameter switching"; it does not directly change strategy scores.

### 3.1 State Determination

Inputs:

- `breadth = mean(close > ma20)` (market breadth)
- `bench_ret20` (benchmark 20-day return)

Determination:

- `risk_on`: `breadth >= risk_on_threshold` and `bench_ret20 > 0`
- `risk_off`: `breadth <= risk_off_threshold` or `bench_ret20 < 0`
- otherwise `neutral`

### 3.2 Parameter Mapping

State-dependent transforms applied to the base parameters:

- `top_k = base_top_k + delta`
- `sell_threshold = base_sell_threshold + delta`
- `entry_interval_days = round(base_entry_interval * mult)`
- `exposure_cap = base_exposure_cap * mult`

With boundary protections:

- `top_k >= 1`
- `entry_interval_days >= 1`
- `sell_threshold` is clamped to a reasonable range
- `exposure_cap` does not exceed the original cap

### 3.3 Intuition

- **risk_on**: can be more aggressive (faster rebalancing / higher position size or a wider candidate set)
- **risk_off**: can be more conservative (slower rebalancing / lower position size / stricter exits)

In the current frozen config, the core conservative action is that `risk_off` lowers effective position size and slows the cadence.

---

## 4) Why Freeze the Parameters

- Too many in-year sample iterations amplify the same-cycle coupling of "news - business - market action", so extrapolation risk is high
- Within a plateau region, further tuning yields diminishing marginal returns while overfitting risk rises
- The current setup already delivers executable returns and controlled drawdown, so moving into a "monitoring first" phase makes more sense

---

<a id="2-frozen-runbook"></a>

# Final Frozen Runbook

## 1) Current Frozen Config

- Main config: `config/final_freeze_2025.toml`
- Comparison config: `config/branch_alpha_regime_switch.toml`

Both configs are kept in the repo. The historical parameter files under `config/` have also been restored for later comparison.

## 2) Final Backtest Run

```bash
uv run python main.py --mode backtest --config config/final_freeze_2025.toml
```

Output files (cleaned down to a minimal set):

- `output/backtest_metrics.json`
- `output/backtest_equity_curve.csv`
- `output/backtest_trades.csv`

## 3) JoinQuant Data-Pulling Capability (retained)

The following pipelines can continue to be used:

- `uv run python main.py --mode jq-bulk-sync --config config/final_freeze_2025.toml`
- `scripts/run_tomorrow_sync.sh`
- `scripts/run_tomorrow_smallcap_sync.sh`
- `scripts/jq_bulk_sync_smallcap.py`
- `scripts/sync_index_daily_to_main.py`
- `scripts/sync_minute_smallcap_batch.py`
- `scripts/check_missing_smallcap_data.py`
- `scripts/prepare_snapshot_retry.py`

## 4) Termination Rules to Stop Overfitting

- Stop continuously tuning parameters on the same year's sample.
- Re-optimization requires at least one of the following to be triggered:
  - Excess return vs. the benchmark is negative for 2 consecutive months;
  - Live/simulated drawdown exceeds 1.3 times the drawdown cap of the frozen period;
  - A significant change in market structure (trading value / volatility center keeps drifting).

## 5) Suggested Follow-Up Maintenance

- Do only "validation and monitoring"; no routine parameter search.
- Produce a rolling evaluation of the frozen config against the benchmark once a month.
- Accumulate at least one quarter of new data before starting the next round of parameter re-estimation.

---

<a id="3-submission-checklist"></a>

# Submission Checklist

This checklist is for "committing only what stable production needs", so experimental junk is not committed along with it.

## A. Recommended to Commit (code and docs)

- All actual runtime code changes under `quant/`
- `config/final_freeze_2025.toml`
- `config/branch_alpha_regime_switch.toml` (comparison config)
- `config/adaptive_plus_base.toml` (base compatibility config)
- `README.md`
- `docs/INDEX.md`
- `docs/FINAL_FREEZE_RUNBOOK.md`
- `docs/ENGINE_PARAMETER_REGIME_GUIDE.md`
- `docs/SUBMISSION_CHECKLIST.md`

## B. Explicitly Do Not Commit (runtime artifacts)

- `output/*`
- `data/*.db-wal`
- `data/*.db-shm`
- `data/backups/*`

## C. Optional (depending on team conventions)

- `third-party/`: commit only if it contains assets that genuinely require version pinning

## D. Pre-Commit Checks

- The backtest command runs through:
  - `uv run python main.py --mode backtest --config config/final_freeze_2025.toml`
- The JoinQuant sync pipeline works (at least the command is executable):
  - `uv run python main.py --mode jq-bulk-sync --config config/final_freeze_2025.toml`
- The doc entry points are complete:
  - `docs/INDEX.md` already includes the frozen runbook, the parameter/engine reference, and the submission checklist
