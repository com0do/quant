# Gateway Deployment Split (Win10 server + WSL2 client)

## Topology

- **Win10**: run `xtquant_gateway` service, connected to miniQMT + xtquant runtime.
- **WSL2 (Ubuntu/Linux)**: run quant engine/backtest/live strategy and call gateway HTTP endpoints.

## Client placement (important)

- `GatewayClient` is part of the quant runtime and is expected to stay embedded in this repo (`quant/gateway`).
- The default and recommended path is: use the root quant environment directly (`uv sync --locked` in repo root).
- In short: **client code and client runtime are embedded in the main quant project**.

## Why split environments

- avoid mixing Win-only runtime dependencies with Linux quant research stack
- isolate operational risk: gateway restart/upgrade does not touch research environment
- independent release cadence for gateway and quant engine

## Environment layout

### Windows server (separate venv)

1. Create venv in gateway folder:
   - `uv venv .venv-gateway-server`
2. Activate and sync:
   - `cd deploy/gateway_server && uv sync --locked`
3. Start service:
   - Copy `.env.example` to `.env` and fill values (`XTG_ACCOUNT_ID`, `XTG_MINI_QMT_PATH`, `XTG_TOKEN`)
   - Manual: `uv run ../../scripts/run_xtquant_gateway.py --env-file .env`
   - One-click (auto-start miniQMT + gateway): `start_gateway_with_miniqmt.bat`

### WSL2 quant engine (separate venv)

1. Keep quant project venv independent:
   - `uv venv .venv-quant`
   - `uv sync --locked`

## Runtime boundaries

- Backtest/optimization: independent from xtquant runtime.
- Live trading path: uses gateway only.
- Realtime quote checks: gateway `/quote` only for required symbols.

## Quick server start

- Keep a dedicated Windows-side server environment:
  - `cd deploy/gateway_server && uv sync --locked`
- Start gateway service with the existing script:
  - `uv run ../../scripts/run_xtquant_gateway.py --env-file .env`

## Why not shortcut `; python ...`

- If miniQMT process is started in the same command chain and blocks the shell, the next command will never execute.
- Use a detached launch (`start`/`Popen`) or use the provided `start_gateway_with_miniqmt.bat`, which launches miniQMT first and then starts gateway.
