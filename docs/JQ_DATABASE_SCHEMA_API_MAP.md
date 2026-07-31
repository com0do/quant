# JQ SQLite Data Dictionary and API Mapping

This document explains each core table in the local SQLite market databases and which JoinQuant API calls populate it.

Reference API doc: [JoinQuant JQData API](https://www.joinquant.com/help/api/doc?name=JQDatadoc&id=9842)

## Databases in Use

- `data/market_cache_csi1000_1y.db`
- `data/market_cache_csi2000_1y.db`

Both are maintained by `scripts/jq_bulk_sync_smallcap.py`.

## Table-Level Mapping

- `prices_daily`
  - Purpose: daily OHLCV and market fields per stock.
  - Key columns: `code`, `date`, `open`, `high`, `low`, `close`, `volume`, `money`, `paused`, `high_limit`, `low_limit`, `avg`, `pre_close`
  - Primary key: `(code, date)`
  - Source API: `jqdatasdk.get_price(...)` with `frequency="daily"` and listed fields.

- `index_members`
  - Purpose: index constituents snapshot for sync date.
  - Key columns: `index_code`, `date`, `code`
  - Primary key: `(index_code, date, code)`
  - Source API: `jqdatasdk.get_index_stocks(index_code, date=...)`

- `fundamentals_snapshot`
  - Purpose: valuation/fundamental snapshot per date and stock.
  - Key columns: `date`, `code`, `pe_ratio`, `pb_ratio`, `turnover_ratio`
  - Primary key: `(date, code)`
  - Source API: `jqdatasdk.get_fundamentals(query(...), date=...)` using `valuation.*`

- `factors_snapshot`
  - Purpose: compact factor snapshot for cross-sectional scoring.
  - Key columns: `date`, `code`, `roe`, `market_cap`, `pe_ratio`, `pb_ratio`, `turnover_ratio`
  - Primary key: `(date, code)`
  - Source API: `jqdatasdk.get_fundamentals(query(...), date=...)` using `valuation.*` and `indicator.roe`

- `sync_progress`
  - Purpose: incremental checkpoint for daily price sync.
  - Key columns: `code`, `next_date`, `done`, `updated_at`, `last_error`
  - Usage: prevents repeated pulling of already completed price ranges.

- `snapshot_progress`
  - Purpose: incremental checkpoint for snapshot sync by trading date.
  - Key columns: `date`, `done`, `updated_at`, `last_error`, `rows_fundamentals`, `rows_factors`
  - Usage: only retries incomplete dates; completed dates are skipped.

- `meta`
  - Purpose: lightweight metadata for the DB and sync range.
  - Key columns: `key`, `value` (and optional `updated_at` in migrated schemas)
  - Typical keys: `index_code`, `start_date`, `end_date`

## Why `snapshot_left=0` but `snapshot_incomplete_dates>0`

This can happen when all scheduled dates were processed (`done=1`), but upstream returns fewer stocks than `prices_daily` has on that date (for example, 999 vs 1000). In that case:

- Pipeline status is "completed for all dates"
- Coverage is still partially incomplete due to source-side availability differences

This is expected and not necessarily a local sync failure.

## Incremental Sync Behavior (No Duplicate Pulls)

- Price path:
  - Controlled by `sync_progress.next_date` and `done`
  - Already completed codes/date ranges are skipped

- Snapshot path:
  - Controlled by `snapshot_progress.done`
  - `scripts/prepare_snapshot_retry.py` can selectively reset only incomplete dates to `done=0` for reattempts

## Current Sync Summary (latest run)

- CSI1000: `sync_left_price=0`, `snapshot_left=0`
- CSI2000: `sync_left_price=0`, `snapshot_left=0`
- Remaining `snapshot_incomplete_dates` indicates source-side partial daily coverage, not unscheduled tasks.
