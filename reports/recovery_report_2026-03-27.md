# Recovery Report (2026-03-27)

## Scope

- Recovered missing Python modules under `quant/`.
- Re-ran historical core tests that are executable in current environment.

## Source Recovery Result

- `quant/` now contains `53` Python source files (`*.py`).
- For modules whose original source was missing, recovery stubs were created and linked to preserved bytecode under `.recovered_pyc/`.
- Core modules with damaged bytecode were rebuilt as functional Python source:
  - `quant/config.py`
  - `quant/app.py`
  - `quant/stock_data/market_db.py`
  - `quant/live/daemon.py`
  - `quant/execution/runner.py`
  - `quant/tools/iterative_optimize.py`
- Full module import check status: `ok_count=47`, `bad_count=0`.

## Test Replay Status

### Passed

- Import regression: all recoverable modules import successfully.
- Backtest regression:
  - Command: `uv run python scripts/backtest_csi1000_baseline.py`
  - Status: PASS
  - Key metric: `ann_return=0.2741`, `max_drawdown=-0.0932`
- Optimization regression:
  - Command: `uv run python scripts/optimize_csi1000_baseline.py`
  - Status: PASS
  - Artifacts:
    - `output/csi1000_optimize_results.csv`
    - `output/csi1000_optimize_best.json`
    - `output/csi1000_backtest_best_metrics.json`
- App entry smoke test:
  - Command: `run_backtest_app(...)`
  - Status: PASS

### Blocked (External Quota)

- JQ sync online regression:
  - Command: `uv run python scripts/jq_bulk_sync_smallcap.py ...`
  - Status: BLOCKED
  - Reason: JQ daily query quota exceeded (`100万条` limit reached).

## Data State Snapshot

- `data/market_cache_csi1000_1y.db`
  - `prices_daily=244000`
  - `fundamentals_snapshot=234255`
  - `factors_snapshot=234255`
  - `sync_progress=1000`
- `data/market_cache_csi2000_1y.db`
  - `prices_daily=484096`
  - `fundamentals_snapshot=0`
  - `factors_snapshot=0`
  - `sync_progress=1984`

## Notes

- Recovery stubs preserve runtime capability but are not equivalent to original handwritten source for all modules.
- For full maintainability restoration, next phase should replace stub-backed modules with reconstructed source one-by-one.
