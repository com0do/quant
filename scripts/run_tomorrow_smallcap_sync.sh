#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

START_DATE="${START_DATE:-2024-12-24}"
END_DATE="${END_DATE:-2025-12-31}"
SNAPSHOT_CHUNK_SIZE="${SNAPSHOT_CHUNK_SIZE:-300}"
CONSTITUENTS_DB="${CONSTITUENTS_DB:-data/meta/index_constituents.db}"
DB_CSI1000="${DB_CSI1000:-data/runtime/market_csi1000_latest.db}"
DB_CSI2000="${DB_CSI2000:-data/runtime/market_csi2000_latest.db}"

echo "[0/3] Re-check missing status"
uv run python scripts/check_missing_smallcap_data.py --start-date "${START_DATE}" --end-date "${END_DATE}"

echo "[1/3] Prepare snapshot retry set (only missing/incomplete dates)"
uv run python scripts/prepare_snapshot_retry.py --db-path "${DB_CSI1000}" --start-date "${START_DATE}" --end-date "${END_DATE}"
uv run python scripts/prepare_snapshot_retry.py --db-path "${DB_CSI2000}" --start-date "${START_DATE}" --end-date "${END_DATE}"

echo "[2/3] Sync missing snapshots for CSI1000 (no repeat for done=1 dates)"
uv run python scripts/jq_bulk_sync_smallcap.py \
  --index-code 000852.XSHG \
  --start-date "${START_DATE}" \
  --end-date "${END_DATE}" \
  --db-path "${DB_CSI1000}" \
  --constituents-db "${CONSTITUENTS_DB}" \
  --only-snapshots \
  --snapshot-chunk-size "${SNAPSHOT_CHUNK_SIZE}"

echo "[3/3] Sync missing snapshots for CSI2000 (no repeat for done=1 dates)"
uv run python scripts/jq_bulk_sync_smallcap.py \
  --index-code 399303.XSHE \
  --start-date "${START_DATE}" \
  --end-date "${END_DATE}" \
  --db-path "${DB_CSI2000}" \
  --constituents-db "${CONSTITUENTS_DB}" \
  --only-snapshots \
  --snapshot-chunk-size "${SNAPSHOT_CHUNK_SIZE}"

echo "[DONE] Re-check result"
uv run python scripts/check_missing_smallcap_data.py --start-date "${START_DATE}" --end-date "${END_DATE}"
