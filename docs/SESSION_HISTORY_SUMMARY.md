# Quant Project Session History Summary

## 1. Business Goal

- Build an A-share small-cap quantitative trading system.
- Focus universe on CSI1000 / small-micro-cap segment.
- Workflow:
  - Backtest first
  - Then live trading with QMT adapter
  - Accumulate live data for future evaluation/optimization

## 2. Historical Functional Requirements (Consolidated)

- Modular architecture:
  - `stock_filter`, `stock_exclusion`, `stock_strategy`, `stock_data`, `backtest`, `execution`, `live`, `tools`
- Data:
  - JQ local SDK (`jqdatasdk`) integration
  - SQLite as primary store (replace pickle-based flow)
  - Incremental sync with checkpoint/resume
  - Separate CSI1000/CSI2000 one-year DBs
- Strategy:
  - Multi-strategy parallel scoring + weighted aggregation
  - Buy/sell split logic
  - D+2 holding constraint + stop-loss + trailing stop
- Optimization:
  - grid/iterative optimization
  - hard constraints (`excess_ann_return > 0`, drawdown guard)
  - vectorbt-assisted prefilter for stage1 + stage2
  - multi-process utilization
- Live execution:
  - QMT mock + real-adapter skeleton
  - TWAP/VWAP slicing
  - timeout cancel/re-submit + passive/aggressive chase
  - exposure/liquidity constraints
  - daemon monitor + daily report + archive
- Intraday:
  - minute-level intraday sell monitor merged with daily sell scores

## 3. Data State (Current Snapshot)

- `data/market_cache_csi1000_1y.db`
  - daily prices populated
  - fundamentals/factors snapshots mostly populated
- `data/market_cache_csi2000_1y.db`
  - daily prices populated
  - snapshots incomplete due to daily JQ quota windows

## 4. Incident Summary

- Source files under `quant/` were unexpectedly lost in workspace.
- Emergency restoration was done with loader stubs + pyc.
- This is not maintainable long-term.
- Current recovery objective is full source restoration to maintainable code.

## 5. Recovery Target (This Phase)

- Replace all `Recovered loader stub` files with plain-source Python modules.
- Keep feature parity with historical requirements listed above.
- Preserve:
  - vectorbt stage1+stage2 prefilter iterative optimization
  - multi-thread/process optimization path
  - live daemon + qmt mock execution loop
  - JQ trial-window evaluation workflow

## 6. Validation Targets After Rewrite

- Backtest:
  - `scripts/backtest_csi1000_baseline.py`
- Optimization:
  - `scripts/optimize_csi1000_baseline.py`
  - `quant.tools.iterative_optimize.run_iterative_optimize(...)`
- Live smoke:
  - `quant.live.daemon` one-cycle dry run
- Sync smoke:
  - `scripts/jq_bulk_sync_smallcap.py` startup + checkpoint path
  - if quota exceeded, mark as external-blocked (not code failure)
