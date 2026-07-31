# XtQuant Gateway V2 Design

This is a new gateway design that replaces the old `qka`-style dynamic API exposure.

## Naming and Scope

- New package name: `xtquant_gateway`
- Purpose: low-latency, safety-first, state-persistent bridge between Linux strategy engine and Windows miniQMT/xtquant runtime
- No backward-compatibility requirement with legacy dynamic routes

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
   - fixed routes and strict request schema
   - keep trader session alive
   - quote cache with stale guard check before order submit

2. **Safety**
   - server-side risk wall (lot-size, available cash, reserve cash, single order notional, daily order count/notional, sell available)
   - stale quote guard to block orders on old quotes
   - reconnect fallback on key paths

3. **Persistence**
   - sqlite store (`data/xtquant_gateway.db`) for orders, idempotency keys, fills, api metrics
   - WAL mode enabled

4. **Observability**
   - endpoint status counts and p50/p95/p99 latency
   - reconnect count and last reconnect error in metrics summary

## Start Command

```bash
XTG_ACCOUNT_ID=... \
XTG_MINI_QMT_PATH="C:/path/to/miniqmt" \
XTG_TOKEN=... \
uv run python scripts/run_xtquant_gateway.py
```

## Server/Client Separation

- Win11 gateway server and Linux quant client should use separate virtual environments.
- See `docs/GATEWAY_DEPLOYMENT_SPLIT.md` for exact setup steps.

## Data Boundary Recommendation

- Backtest remains independent from xtquant.
- xtquant data should be used only for live trading execution path and required real-time quote checks.
- Keep syncing only required symbols/time windows to reduce load and complexity.
