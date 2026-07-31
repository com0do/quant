# Live Auto Pipeline (Single Source)

This pipeline avoids directly consuming ad-hoc `output/*` files as live strategy source.

## Goal

- One connected flow for pre-open and open-session execution.
- Keep a single daily execution snapshot (`execution_plan`) as the handoff artifact.

## Flow

1. pre-open sync (optional): `jq-bulk-sync`
2. pre-open backtest with current config
3. pre-open scan to build watchlist
4. write `data/runtime/live_execution_plan.json`
5. start live daemon and consume the plan watchlist

## Entry

```bash
uv run python main.py --mode live-auto --config config/live_qmt_http.toml
```

Preparation-only entry (recommended for scheduled pre-open job):

```bash
uv run python main.py --mode prepare-live-plan --config config/live_qmt_http.toml
```

Optional env:

- `LIVE_AUTO_SYNC_BEFORE_OPEN=0|1` (default `1`)
- `LIVE_AUTO_MAX_LOOPS=<int>` (empty means run continuously)

## Why better than reading output directly

- `output/` keeps analytical artifacts and can contain many unrelated files.
- execution handoff uses one explicit schema file (`execution_plan`) with:
  - generation time and source config
  - pre-open backtest metrics
  - strategy snapshot
  - watchlist for the day

## Daily script

Use one script to orchestrate pre-open steps:

```bash
bash scripts/run_daily_live_pipeline.sh
```

Common envs:

- `RUN_SYNC_BEFORE_OPEN=1|0`
- `RUN_GATEWAY_ACCEPTANCE=1|0`
- `RUN_TRADING_SESSION=1|0`
- `PROFILE=live`
- `CONFIG_PATH=config/live_qmt_http.toml`
