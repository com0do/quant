# QMT Gateway Protocol (Linux <-> Windows)

This project now supports `qmt_http` broker mode for `live-daemon`.

## Purpose

- Linux runs strategy, signal, risk, and scheduling.
- Windows runs miniQMT + xtquant + gateway server.
- Linux calls gateway HTTP APIs to query account/position and submit/cancel orders.

## Config Keys

In `config/*.toml`:

- Shared gateway section (`stock_data` and `execution` both use this):
  - `[gateway] base_url/token/timeout_sec/account_id`
- Trading route:
  - `[daemon] broker_type = "qmt_http"`
- Quote route:
  - `[data] realtime_quote_source = "qmt_http"` (fallback keeps using daily close)

Reference file: `config/live_qmt_http.toml`

## Required Endpoints

Gateway should provide JSON APIs:

1. `POST /account`
   - request: `{ "account_id": "..." }` (optional)
   - response: `{ "data": { "cash": 0.0, "equity": 0.0, ... } }` or raw dict

2. `POST /positions`
   - request: `{ "account_id": "..." }` (optional)
   - response:
     - `{ "data": { "000001.SZ": 1000, ... } }`, or
     - `{ "data": [ { "code": "000001.SZ", "quantity": 1000 }, ... ] }`

3. `POST /order`
   - request:
     - `account_id` (optional)
     - `code` (`000001.SZ` / `600000.SH`)
     - `side` (`buy`/`sell`)
     - `quantity` (int)
     - `price` (float/null)
   - response: `{ "data": { "order_id": "...", "status": "new|filled|..." } }`

4. `POST /cancel`
   - request: `{ "account_id": "...", "order_id": "..." }` (`account_id` optional)
   - response: `{ "data": { "ok": true } }` or `{ "ok": true }`

5. `POST /order/status`
   - request: `{ "account_id": "...", "order_id": "..." }` (`account_id` optional)
   - response: `{ "data": { "order_id": "...", "status": "...", ... } }`

## Optional Endpoints

These are implemented in client helper methods and useful for monitoring:

- `POST /quote` (`get_quote`)
- `POST /fills` (`get_fills`)

## XtQuant Interface Mapping

Recommended gateway internals (Windows side):

- `xtquant.xtdata` (market data)
  - snapshot/query: `get_full_tick`, `get_market_data`, `get_market_data_ex`, `get_local_data`
  - subscribe: `subscribe_quote`, `subscribe_whole_quote`, `unsubscribe_quote`
- `xtquant.xttrader` (trading)
  - order: `order_stock`, `order_stock_async`
  - cancel: `cancel_order_stock`
  - query: `query_stock_asset`, `query_stock_positions`, `query_stock_orders`, `query_stock_trades`
  - callback push: `on_stock_order`, `on_stock_trade`, `on_order_error`, `on_cancel_error`, `on_stock_asset`, `on_stock_position`

Mapping suggestion:

- `/quote` -> cache-first (from subscribe push), then fallback to `get_full_tick`
- `/account` -> `query_stock_asset`
- `/positions` -> `query_stock_positions`
- `/order` -> `order_stock` or `order_stock_async` + local ack map
- `/cancel` -> `cancel_order_stock`
- `/order/status` -> local status cache + `query_stock_order(s)` fallback
- `/fills` -> `query_stock_trades` + local dedupe

## QKA Improvement List

Current `qka` server dynamically exposes all trader methods through `/api/<method>`. It is simple but not ideal for production.

Recommended upgrades:

1. Fixed endpoints and strict request models.
   - Keep only required paths (`/quote`, `/account`, `/positions`, `/order`, `/cancel`, `/order/status`, `/fills`).
   - Validate payload fields and range checks.
2. Async order state machine.
   - Use `order_stock_async` with `req_id -> order_id` mapping.
   - Consume callback push to update in-memory state and persistence.
3. Idempotency and dedupe.
   - Add `client_order_id` in `/order`.
   - Return previous result for duplicate submit.
4. Protection and balance management.
   - pre-check `available_cash`, `frozen_cash`, lot size, max order notional, sell available quantity.
   - reject instead of sending invalid request downstream.
5. Observability.
   - per-endpoint latency, error code counters, callback lag, reconnect count.
6. Reliability.
   - heartbeat + auto reconnect + stale quote detection + circuit breaker.

## Latency and Bottleneck Analysis

Before opening QMT, optimize architecture first:

1. **Python HTTP stack overhead**
   - Dynamic reflection routing adds overhead and serialization noise.
   - Fix: static routes + pre-validated models + keep-alive session.
2. **Synchronous query path**
   - Query account/positions/orders on every request can block trading path.
   - Fix: callback-driven cache, query as fallback only.
3. **Cross-OS network hop**
   - Linux -> Windows adds latency jitter.
   - Fix: same LAN, pinned route, no public network, set short timeout and retry budget.
4. **Proxy/misroute issues**
   - Environment proxy may hijack local addresses.
   - Fix: client-side disable proxy for gateway calls (already applied in this repo).
5. **Quote fanout pressure**
   - polling many symbols with pull API can saturate gateway.
   - Fix: subscription push + bounded in-memory ring buffer + batch endpoints.
6. **Order risk checks in strategy side only**
   - If gateway has no guard, dangerous direct order path remains.
   - Fix: duplicate hard guards on gateway side (server-side risk wall).

## Runbook

1. Start gateway service on Windows host where miniQMT is installed.
2. Verify Linux can reach `[gateway].base_url`.
3. Run smoke test:
   - `uv run python scripts/qmt_gateway_smoke_test.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID"`
4. Start daemon:
   - `uv run python main.py --mode live-daemon --config config/live_qmt_http.toml`
5. Run acceptance checklist:
   - `uv run python scripts/qmt_gateway_acceptance.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID"`
   - Optional tiny order test:
     - `uv run python scripts/qmt_gateway_acceptance.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID" --test-order --order-code 000001.SZ --order-qty 100 --order-price 10.0`

## Security Notes

- Always set a token and restrict gateway source IP.
- Do not expose gateway to public internet directly.
- Prefer LAN/VPN + firewall rules.
