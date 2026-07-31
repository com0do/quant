# QKA Hardening Checklist

This checklist is for upgrading `third-party/qka` from a demo bridge to production gateway.

## Current Progress

- Implemented:
  - fixed routes (`/quote`, `/account`, `/positions`, `/order`, `/cancel`, `/order/status`, `/fills`)
  - strict request models + token auth (`Bearer` + backward-compatible `X-Token`)
  - order `client_order_id` idempotency cache
  - server-side pre-trade risk wall (cash/lot/sell-available/daily limit)
  - health routes (`/health/live`, `/health/trader`, `/health/quote`)
  - stale quote guard before order submit
  - reconnect-attempt fallback on key query/order paths
  - sqlite persistence for orders/idempotency/fills (`qka_gateway_state.db`)
  - `/metrics/summary` with endpoint p50/p95/p99 and status counters
  - one-click acceptance runner script (`scripts/qmt_gateway_acceptance.py`)
- Pending:
  - richer histograms / long-term external metrics sink (Prometheus/TSDB)

## API Surface

- Replace dynamic `/api/<method>` exposure with fixed routes:
  - `/quote`, `/account`, `/positions`, `/order`, `/cancel`, `/order/status`, `/fills`
- Add strict request/response schemas and field validation.
- Add `client_order_id` idempotency key for `/order`.

## Trading Safety Wall (Server-side)

- Pre-trade checks:
  - lot-size multiple (A-share usually 100)
  - available cash and frozen cash
  - max single-order notional
  - max daily notional and max daily order count
  - sell quantity <= available position
- Reject locally before sending to xttrader.

## State and Recovery

- Use callback-driven state cache:
  - order state, fills, position, asset
- Persist key state snapshots to local sqlite:
  - pending orders, last order_id mapping, dedupe keys
- On restart:
  - rebuild state from xttrader query APIs + local persisted cache

## Performance and Latency

- Keep one long-lived xttrader session and one HTTP client session.
- Use async order path and callback confirmation.
- Avoid frequent full `query_*` polling in hot path; use cache and fallback polling.
- Batch quote requests when strategy asks many symbols.

## Operations

- Add health endpoints:
  - `/health/live`, `/health/trader`, `/health/quote`
- Add metrics:
  - endpoint latency p50/p95/p99
  - callback lag
  - order reject rate
  - reconnect count
- Add alerting on:
  - disconnect/reconnect loops
  - stale quote age over threshold
  - continuous reject burst
