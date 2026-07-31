# XtData Minimal Sync Policy

This policy defines what to sync from `xtdata`, when to sync, and why.

## Principle

- Backtest should remain reproducible and independent from gateway runtime.
- xtdata is used to **accumulate missing market data outside trading hours** and **support live risk checks during trading**.
- Sync only required symbols and periods to control latency and storage pressure.

## Time windows

- **T day 15:20-08:50 (off-market)**:
  - run incremental sync jobs
  - validate data quality and completeness
  - refresh backtest dataset and rerun validation if needed
- **Trading session**:
  - no bulk history download
  - only realtime quote subscription / query needed for live signal and risk

## Minimal necessary sync scope

1. benchmark and regime indices
   - daily bars and optional 1m bars for market state features
2. candidate universe
   - symbols in strategy candidate pool (not full market)
3. current holdings + watchlist
   - ensure minute/day continuity for risk and stop logic
4. corporate action essentials
   - dividend/adjustment factors needed for clean historical alignment

## Why this is defensible

- xtdata docs recommend limiting single-stock subscriptions and prefer whole-quote mode for high-subscription scenarios.
- history requests should be bounded by needed range/count to avoid long response latency.
- static data (sector/classification) should be updated daily/weekly, not per-request.

## Suggested xtdata calls

- history incremental:
  - `download_history_data2(...)` by selected symbols
- realtime:
  - `subscribe_whole_quote(...)` for broader watch universe
  - `get_full_tick(...)` for point-in-time fallback checks
- trading calendar:
  - `get_trading_calendar(...)`
- adjustment factors:
  - `get_divid_factors(...)`
