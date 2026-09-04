# Part 5: Live Trading Pipeline and Gateway

> This part merges six live-trading/gateway documents: the live-trading automation pipeline, the QMT gateway protocol, the xtquant gateway V2 design, deployment separation, the xtdata sync strategy, and the QKA production-readiness checklist.
> Read this when preparing to go live or doing operations.

## Table of Contents

- [1. Live Trading Automation Pipeline](#1-live-trading-automation-pipeline)
- [2. QMT Gateway Protocol](#2-qmt-gateway-protocol)
- [3. xtquant_gateway V2 Design](#3-xtquant-gateway-v2-design)
- [4. Gateway Deployment Separation](#4-gateway-deployment-separation)
- [5. xtdata Minimal Necessary Sync Strategy](#5-xtdata-minimal-necessary-sync-strategy)
- [6. QKA Gateway Production-Readiness Checklist](#6-qka-gateway-production-readiness-checklist)

---

<a id="1-live-trading-automation-pipeline"></a>
# Live Trading Automation Pipeline (Single Execution Source)

The purpose of this pipeline: instead of reading scattered `output/*` files directly, the live strategy works around one unified "daily execution plan".

## Goals

- Chain pre-market preparation and intraday execution into one coherent flow.
- Keep only one execution snapshot per day (`execution_plan`) as the handoff artifact.

## Main Flow

1. Pre-market sync (optional): `jq-bulk-sync`
2. Pre-market backtest (using the current config)
3. Pre-market scan, building the day's watchlist
4. Write `data/runtime/live_execution_plan.json`
5. Start the live daemon, which consumes the watchlist in that plan

## Entry Commands

```bash
uv run python main.py --mode live-auto --config config/live_qmt_http.toml
```

Pre-market preparation only (recommended as a scheduled pre-market job):

```bash
uv run python main.py --mode prepare-live-plan --config config/live_qmt_http.toml
```

Available environment variables:

- `LIVE_AUTO_SYNC_BEFORE_OPEN=0|1` (default `1`)
- `LIVE_AUTO_MAX_LOOPS=<int>` (leave unset to run continuously)

## Why This Beats Reading `output/` Directly

- `output/` is full of assorted analysis artifacts, so it is easy to reference the wrong one.
- The execution handoff uses a file with a well-defined schema (`execution_plan`), containing:
  - Generation time and source config
  - Pre-market backtest metrics
  - Strategy snapshot
  - The day's watchlist

## Daily Script

Orchestrate the pre-market actions with a single script:

```bash
bash scripts/run_daily_live_pipeline.sh
```

Common environment variables:

- `RUN_SYNC_BEFORE_OPEN=1|0`
- `RUN_GATEWAY_ACCEPTANCE=1|0`
- `RUN_TRADING_SESSION=1|0`
- `PROFILE=live`
- `CONFIG_PATH=config/live_qmt_http.toml`

---

<a id="2-qmt-gateway-protocol"></a>

# QMT Gateway Protocol (Linux ↔ Windows)

This project already supports the `qmt_http` broker mode for `live-daemon`.

## Purpose

- The Linux side handles strategy, signals, risk control, and scheduling.
- The Windows side runs miniQMT + xtquant + the gateway service.
- Linux calls the gateway over HTTP to query account/holdings and to submit/cancel orders.

## Configuration

In `config/*.toml`:

- General gateway section (read by both `stock_data` and `execution`):
  - `[gateway] base_url / token / timeout_sec / account_id`
- Trading route:
  - `[daemon] broker_type = "qmt_http"`
- Market-data route:
  - `[data] realtime_quote_source = "qmt_http"` (the fallback is still the daily close price)

Reference file: `config/live_qmt_http.toml`

## Required Endpoints

The gateway must provide the following JSON APIs:

1. `POST /account`
   - Request: `{ "account_id": "..." }` (optional)
   - Response: `{ "data": { "cash": 0.0, "equity": 0.0, ... } }` or a raw dict

2. `POST /positions`
   - Request: `{ "account_id": "..." }` (optional)
   - Response:
     - `{ "data": { "000001.SZ": 1000, ... } }`, or
     - `{ "data": [ { "code": "000001.SZ", "quantity": 1000 }, ... ] }`

3. `POST /order`
   - Request:
     - `account_id` (optional)
     - `code` (`000001.SZ` / `600000.SH`)
     - `side` (`buy` / `sell`)
     - `quantity` (int)
     - `price` (float / null)
   - Response: `{ "data": { "order_id": "...", "status": "new|filled|..." } }`

4. `POST /cancel`
   - Request: `{ "account_id": "...", "order_id": "..." }` (`account_id` is optional)
   - Response: `{ "data": { "ok": true } }` or `{ "ok": true }`

5. `POST /order/status`
   - Request: `{ "account_id": "...", "order_id": "..." }` (`account_id` is optional)
   - Response: `{ "data": { "order_id": "...", "status": "...", ... } }`

## Optional Endpoints

Already implemented in the client helper, for monitoring convenience:

- `POST /quote` (`get_quote`)
- `POST /fills` (`get_fills`)

## XtQuant Interface Mapping

Recommended mapping inside the gateway (Windows side):

- `xtquant.xtdata` (market data)
  - Snapshot/query: `get_full_tick`, `get_market_data`, `get_market_data_ex`, `get_local_data`
  - Subscription: `subscribe_quote`, `subscribe_whole_quote`, `unsubscribe_quote`
- `xtquant.xttrader` (trading)
  - Order placement: `order_stock`, `order_stock_async`
  - Cancellation: `cancel_order_stock`
  - Queries: `query_stock_asset`, `query_stock_positions`, `query_stock_orders`, `query_stock_trades`
  - Callback pushes: `on_stock_order`, `on_stock_trade`, `on_order_error`, `on_cancel_error`, `on_stock_asset`, `on_stock_position`

Mapping suggestions:

- `/quote` → prefer the subscription-push cache, with `get_full_tick` as the fallback
- `/account` → `query_stock_asset`
- `/positions` → `query_stock_positions`
- `/order` → `order_stock` or `order_stock_async` + a local ack mapping
- `/cancel` → `cancel_order_stock`
- `/order/status` → local status cache + `query_stock_order(s)` as the fallback
- `/fills` → `query_stock_trades` + local deduplication

## QKA Refactoring Checklist

The current `qka` service dynamically exposes all trader methods through `/api/<method>`, which is simple but not suitable for production.

Recommended upgrades:

1. Fixed endpoints + strict request models.
   - Keep only the necessary paths (`/quote`, `/account`, `/positions`, `/order`, `/cancel`, `/order/status`, `/fills`).
   - Validate fields and ranges.
2. Asynchronous order state machine.
   - Use `order_stock_async` + a `req_id → order_id` mapping.
   - Consume callbacks to update in-memory state and persist it.
3. Idempotency and deduplication.
   - Add `client_order_id` to `/order`.
   - Return the previous result directly on a duplicate submission.
4. Server-side protection and fund management.
   - Pre-check `available_cash`, `frozen_cash`, lot size, per-order notional, and sellable quantity.
   - Reject locally on rule violations, without sending the order downstream.
5. Observability.
   - Per-endpoint latency, error codes, callback delay, and reconnect count.
6. Reliability.
   - Heartbeat + auto-reconnect + stale-quote detection + circuit breaker.

## Latency and Bottleneck Analysis

Before bringing up QMT, optimize the architecture itself first:

1. **Python HTTP stack overhead**
   - Dynamic reflective routing adds both overhead and serialization noise.
   - Remedy: fixed routes + pre-validation models + a keep-alive session.
2. **Synchronous query path**
   - Synchronously querying account/holdings/orders on every request blocks the main trading path.
   - Remedy: a callback-driven cache; queries only as a fallback.
3. **Cross-OS network hop**
   - Linux → Windows adds latency jitter.
   - Remedy: same LAN, fixed routes, no public internet, and small timeout and retry budgets.
4. **Proxy/routing mix-ups**
   - Environment proxies may hijack local addresses.
   - Remedy: the client bypasses proxies when talking to the gateway (already implemented in this repo).
5. **Market-data fan-out pressure**
   - Polling many instruments with the pull API can overwhelm the gateway.
   - Remedy: subscription push + a bounded ring buffer + batch endpoints.
6. **Risk checks only on the strategy side**
   - If the gateway has no guard, placing orders directly carries risk exposure.
   - Remedy: add a hard check on the gateway side as well (a server-side risk-control wall).

## Runbook

1. Start the gateway service on the Windows host that has miniQMT installed.
2. Verify that the Linux side can reach `[gateway].base_url`.
3. Run the smoke test:
   - `uv run python scripts/qmt_gateway_smoke_test.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID"`
4. Start the daemon:
   - `uv run python main.py --mode live-daemon --config config/live_qmt_http.toml`
5. Run through the acceptance checklist:
   - `uv run python scripts/qmt_gateway_acceptance.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID"`
   - Optional real small-order test:
     - `uv run python scripts/qmt_gateway_acceptance.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID" --test-order --order-code 000001.SZ --order-qty 100 --order-price 10.0`

## Security Notes

- Always set a token and restrict the source IPs.
- Do not expose the gateway directly to the public internet.
- Prefer LAN/VPN + firewall rules.

---

<a id="3-xtquant-gateway-v2-design"></a>

# xtquant_gateway V2 Design

This is the new gateway design that replaces the old way of exposing the dynamic `qka` API.

## Naming and Scope

- New package name: `xtquant_gateway`
- Positioning: the bridge between the Linux strategy engine and the Windows miniQMT/xtquant runtime, focused on low latency, safety first, and persistable state
- No backward compatibility with the old dynamic routes is required

## Endpoint Contract

- `GET /health/live`
- `GET /health/trader`
- `GET /health/quote`
- `GET /metrics/summary`
- `POST /quote`
- `POST /account`
- `POST /positions`
- `POST /order`
- `POST /cancel`
- `POST /order/status`
- `POST /fills`

## Core Design Decisions

1. **Low latency**
   - Fixed routes + strict request schema
   - The trading session keeps a long-lived connection
   - Quote cache, with a staleness check before placing orders

2. **Safety**
   - Server-side risk-control wall (lot size, available funds, reserved funds, per-order notional, daily order count/notional, sellable quantity)
   - Order placement is forbidden when quotes are stale
   - Reconnect fallback on critical paths

3. **Persistence**
   - Use sqlite (`data/xtquant_gateway.db`) to store orders, idempotency keys, fills, and API metrics
   - WAL mode enabled

4. **Observability**
   - Per-endpoint success/failure counts and p50/p95/p99 latency
   - Reconnect count and last reconnect error exposed in the metrics summary

## Startup Command

```bash
XTG_ACCOUNT_ID=... \
XTG_MINI_QMT_PATH="C:/path/to/miniqmt" \
XTG_TOKEN=... \
uv run python scripts/run_xtquant_gateway.py
```

## Server/Client Separation

- The Win11 gateway server and the Linux quant client use separate venvs.
- For the concrete steps see section 4 of this document (deployment separation of the gateway server and the quant client).

## Data Boundary Recommendations

- Keep backtesting independent, with no dependence on xtquant.
- xtquant data is used only for the live-trading main path and necessary real-time quote validation.
- Sync only the instruments and time windows the strategy needs, to reduce load and complexity.

---

<a id="4-gateway-deployment-separation"></a>

# Deployment Separation of the Gateway Server and the quant Client (Win10 Server + WSL2 Client)

## Topology

- **Win10**: runs the `xtquant_gateway` service, connecting to the miniQMT + xtquant runtime.
- **WSL2 (Ubuntu/Linux)**: runs the quant engine/backtest/live strategy, calling the gateway HTTP endpoints.

## Client Location (Important)

- `GatewayClient` is part of the quant runtime and should stay inside this repo (`quant/gateway`).
- Recommended path: use the main quant environment directly at the repo root (`uv sync --locked`).
- In one sentence: **both the client code and the client runtime live in the main quant project.**

## Why Separate Environments

- Avoid mixing Windows-only runtime dependencies into the Linux quant research stack
- Isolate operational risk: gateway restarts/upgrades do not affect the research environment
- The gateway and the quant engine can release on their own schedules

## Environment Layout

### Windows Server (Separate venv)

1. Create a venv in the gateway directory:
   - `uv venv .venv-gateway-server`
2. Activate and sync:
   - `cd deploy/gateway_server && uv sync --locked`
3. Start the service:
   - Copy `.env.example` to `.env` and fill in the values (`XTG_ACCOUNT_ID`, `XTG_MINI_QMT_PATH`, `XTG_TOKEN`)
   - Manual start: `uv run ../../scripts/run_xtquant_gateway.py --env-file .env`
   - One-click start (automatically launches miniQMT + the gateway): `start_gateway_with_miniqmt.bat`

### WSL2 quant Engine (Separate venv)

1. Keep the quant project venv separate:
   - `uv venv .venv-quant`
   - `uv sync --locked`

## Runtime Boundaries

- Backtest/optimization: fully independent of the xtquant runtime.
- Live trading path: through the gateway only.
- Real-time quote validation: the gateway `/quote` only serves the instruments the strategy needs.

## Quick Start of the Server

- Maintain a separate Windows-side server environment:
  - `cd deploy/gateway_server && uv sync --locked`
- Start the gateway with the existing script:
  - `uv run ../../scripts/run_xtquant_gateway.py --env-file .env`

## Why the `; python ...` Shorthand Doesn't Work

- If miniQMT and the gateway are started in the same command chain, miniQMT blocks the shell and the subsequent commands never get executed.
- Use a detached start (`start` / `Popen`), or the provided `start_gateway_with_miniqmt.bat`, which starts miniQMT first and then the gateway.

---

<a id="5-xtdata-minimal-necessary-sync-strategy"></a>

# xtdata Minimal Necessary Sync Strategy

This strategy defines what to sync from `xtdata`, when to sync it, and why.

## Principles

- Keep backtesting reproducible and independent of the gateway runtime.
- xtdata is used to **fill in missing market data outside trading hours**, and to **support live-trading risk-control checks intraday**.
- Sync only the necessary instruments and periods, to control latency and storage pressure.

## Time Windows

- **Day T 15:20 – next day 08:50 (outside trading hours)**:
  - Run the incremental sync job
  - Validate data quality and completeness
  - Refresh the backtest dataset and re-run validation when necessary
- **Intraday**:
  - No bulk historical data downloads
  - Only subscribe to/query real-time quotes, for live signals and risk control

## Minimal Necessary Sync Scope

1. Benchmark and regime indices
   - Daily bars, optionally 1-minute bars, for market-regime features
2. Candidate stock pool
   - Stocks in the strategy's candidate pool, not the whole market
3. Current holdings + watchlist
   - Keep minute/daily bars continuous for the risk-control and stop-loss logic
4. Dividend/adjustment factors
   - For clean stitching of historical prices

## Why This Holds Up

- The xtdata docs note that the number of single-stock subscriptions is limited; for high-subscription scenarios prefer whole-market subscription.
- Historical requests should limit the range/row count, to avoid long-response latency.
- Static data such as sectors/classifications only needs daily/weekly refreshes, not a call every time.

## Recommended xtdata Calls

- Historical incremental:
  - `download_history_data2(...)`: bulk download for the selected instruments
- Real-time:
  - `subscribe_whole_quote(...)`: whole-market subscription
  - `get_full_tick(...)`: point-query fallback
- Trading calendar:
  - `get_trading_calendar(...)`
- Adjustment factors:
  - `get_divid_factors(...)`

---

<a id="6-qka-gateway-production-readiness-checklist"></a>

# QKA Gateway Production-Readiness Checklist

This checklist is for upgrading `third-party/qka` from a demo bridge to a production-ready gateway.

## Current Progress

- Implemented:
  - Fixed routes (`/quote`, `/account`, `/positions`, `/order`, `/cancel`, `/order/status`, `/fills`)
  - Strict request models + token authentication (`Bearer`, with `X-Token` compatibility)
  - Idempotency cache for order `client_order_id`
  - Server-side pre-trade risk-control wall (cash/lot size/sellable quantity/daily limits)
  - Health-check routes (`/health/live`, `/health/trader`, `/health/quote`)
  - Stale-quote guard before order placement
  - Reconnect fallback on critical query/order paths
  - sqlite persistence of orders/idempotency/fills (`qka_gateway_state.db`)
  - `/metrics/summary`: per-endpoint p50/p95/p99 + status counts
  - One-click acceptance script (`scripts/qmt_gateway_acceptance.py`)
- To do:
  - Fuller histograms / long-term external metrics retention (Prometheus/TSDB)

## API Surface

- Replace the dynamic `/api/<method>` exposure with fixed routes:
  - `/quote`, `/account`, `/positions`, `/order`, `/cancel`, `/order/status`, `/fills`
- Add strict request/response schemas and field validation.
- Add a `client_order_id` idempotency key to `/order`.

## Trading Safety Wall (Server Side)

- Pre-trade checks:
  - Lot size is a multiple of 100, as is common for A-shares
  - Available cash and frozen funds
  - Per-order notional cap
  - Daily notional cap and daily order-count cap
  - Sell quantity <= available holdings
- On a validation failure, reject locally and do not send the order to xttrader.

## State and Recovery

- Use callbacks to drive the state cache:
  - Order status, fills, holdings, account
- Persist key snapshots to local sqlite:
  - Open orders, the latest `order_id` mapping, dedup keys
- On restart:
  - Rebuild state using the xttrader query APIs + the locally persisted cache

## Performance and Latency

- Keep one long-lived xttrader session and one HTTP client session.
- Use the async order path + callback confirmation.
- Avoid frequent full `query_*` polling on the hot path; use a cache + fallback polling.
- When the strategy queries many instruments in a batch, make batch quote requests on the gateway side.

## Operations

- Add health checks:
  - `/health/live`, `/health/trader`, `/health/quote`
- Add metrics:
  - Endpoint latency p50/p95/p99
  - Callback delay
  - Order rejection rate
  - Reconnect count
- Alerts:
  - Disconnect/reconnect loops
  - Stale quotes beyond the threshold
  - Bursts of consecutive order rejections
