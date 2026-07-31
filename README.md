# Quant Trading System (Recovered Source)

This repository contains a modular A-share quant workflow focused on small-cap universes
(CSI1000 / small-micro), with backtest, optimization, data sync, and live-monitoring layers.

## Architecture

- `quant/stock_data`: data clients/services (`sqlite`, `jqdatasdk`, cache)
- `quant/stock_filter`: value pre-selection
- `quant/stock_exclusion`: tradability exclusion rules
- `quant/stock_strategy`: strategy modules and weighted aggregation
- `quant/backtest`: backtest engine
- `quant/execution`: broker model, paper broker, risk guard, algo slicing
- `quant/live`: scanner, signal engine, intraday monitor, daemon, report/archive
- `quant/tools`: optimization/sync/analysis utilities
- `scripts`: operational scripts (JQ sync, data gap check, snapshot retry)

## Index-aware DB Layout

Runtime now supports a hot/cold/meta split:

- `data/runtime/market_csi300_500_latest.db`
- `data/runtime/market_csi1000_latest.db`
- `data/runtime/market_csi2000_latest.db`
- `data/archive/*.db`
- `data/meta/index_constituents.db`

Useful commands:

```bash
uv run python scripts/migrate_to_index_aware_db.py
uv run python scripts/roll_archive_runtime_db.py --runtime-db data/runtime/market_csi1000_latest.db --archive-db data/archive/market_csi1000_archive.db
uv run python scripts/validate_index_aware_db.py
```

## Entry

```bash
uv run python main.py --mode backtest --config config/final_freeze_2025.toml
```

Common modes:

- `backtest`
- `optimize`
- `scan-optimize`
- `iterative-optimize`
- `jq-bulk-sync`
- `live-daemon`
- `live-auto`
- `prepare-live-plan`
- `daily-report`

## Linux <-> miniQMT Gateway (Implemented)

New gateway package (recommended): `xtquant_gateway` (FastAPI fixed-route design, safety + persistence + metrics).

Linux side now supports broker selection in `live-daemon`:

- `daemon.broker_type = "paper"` (default)
- `daemon.broker_type = "qmt_http"` (Windows gateway mode)

Gateway settings are now shared by execution and market-data modules:

- `[gateway].base_url`
- `[gateway].token`
- `[gateway].timeout_sec`
- `[gateway].account_id`

And data module can consume real-time quote via:

- `[data].realtime_quote_source = "qmt_http"`

Example config: `config/live_qmt_http.toml`

Smoke test:

```bash
uv run python scripts/qmt_gateway_smoke_test.py \
  --base-url http://127.0.0.1:18080 \
  --token "$QMT_GATEWAY_TOKEN" \
  --account-id "$QMT_ACCOUNT_ID"
```

Start daemon in gateway mode:

```bash
uv run python main.py --mode live-daemon --config config/live_qmt_http.toml
```

Start connected pre-open -> live pipeline:

```bash
uv run python main.py --mode live-auto --config config/live_qmt_http.toml
```

Pre-open only (generate execution plan):

```bash
uv run python main.py --mode prepare-live-plan --config config/live_qmt_http.toml
```

Daily orchestration script:

```bash
bash scripts/run_daily_live_pipeline.sh
```

Optional local integration test (without Windows miniQMT yet):

```bash
uv run python scripts/mock_qmt_gateway.py --host 127.0.0.1 --port 18080 --token "$QMT_GATEWAY_TOKEN"
```

Start new gateway service:

```bash
cd deploy/gateway_server
uv sync --locked
cp .env.example .env
# edit .env, then:
uv run ../../scripts/run_xtquant_gateway.py --env-file .env
```

Win11 one-click startup:

```bat
cd deploy\gateway_server
start_gateway_with_miniqmt.bat
```

Client note:

- `quant/gateway` client is embedded in the main quant project and is the only supported client runtime path.
- We do not maintain a separate `xtquant-client` project profile.

## Recommended Production Baseline

- Frozen config for current cycle: `config/final_freeze_2025.toml`
- Compare/backup config: `config/branch_alpha_regime_switch.toml`
- Daily JQ sync capability is retained via:
  - `main.py --mode jq-bulk-sync`
  - `scripts/run_tomorrow_sync.sh`
  - `scripts/run_tomorrow_smallcap_sync.sh`
  - `scripts/jq_bulk_sync_smallcap.py`

## Cleanup Policy

- Keep production and sync essentials.
- Remove one-off experiment scripts/results after validation.
- Stop continuous re-optimization inside a single year regime; use periodic re-validation instead.

## Data Flow

1. Use JQ trial window to seed/evaluate strategies.
2. Persist to SQLite.
3. Start live path with QMT adapter (mock via paper broker first).
4. Keep accumulating live execution/market data for future re-evaluation.

## Notes

- `quant/tools/iterative_optimize.py` includes vectorbt prefilter in both stage1 and stage2.
- If JQ quota is exhausted for the day, sync-related tests should be considered externally blocked.
