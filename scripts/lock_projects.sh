#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

echo "[LOCK] root quant project"
uv lock

echo "[LOCK] gateway server project"
(cd deploy/gateway_server && uv lock)

echo "[DONE] all project lockfiles updated"
