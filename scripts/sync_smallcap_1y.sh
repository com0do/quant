#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

START_DATE="${START_DATE:-2024-12-23}"
END_DATE="${END_DATE:-2025-12-23}"
ONLY_SNAPSHOTS="${ONLY_SNAPSHOTS:-1}"
SNAPSHOT_CHUNK_SIZE="${SNAPSHOT_CHUNK_SIZE:-300}"
CONSTITUENTS_DB="${CONSTITUENTS_DB:-data/meta/index_constituents.db}"
DB_CSI1000="${DB_CSI1000:-data/runtime/market_csi1000_latest.db}"
DB_CSI2000="${DB_CSI2000:-data/runtime/market_csi2000_latest.db}"

EXTRA_ARGS=()
if [[ "${ONLY_SNAPSHOTS}" == "1" ]]; then
  EXTRA_ARGS+=(--only-snapshots)
else
  EXTRA_ARGS+=(--sync-snapshots)
fi
EXTRA_ARGS+=(--snapshot-chunk-size "${SNAPSHOT_CHUNK_SIZE}")

echo "[1/2] Sync CSI1000 (${START_DATE}..${END_DATE})"
uv run python scripts/jq_bulk_sync_smallcap.py \
  --index-code 000852.XSHG \
  --start-date "${START_DATE}" \
  --end-date "${END_DATE}" \
  --db-path "${DB_CSI1000}" \
  --constituents-db "${CONSTITUENTS_DB}" \
  "${EXTRA_ARGS[@]}"

echo "[2/2] Sync CSI2000-like (${START_DATE}..${END_DATE})"
uv run python scripts/jq_bulk_sync_smallcap.py \
  --index-code 399303.XSHE \
  --start-date "${START_DATE}" \
  --end-date "${END_DATE}" \
  --db-path "${DB_CSI2000}" \
  --constituents-db "${CONSTITUENTS_DB}" \
  "${EXTRA_ARGS[@]}"

echo "Done."
