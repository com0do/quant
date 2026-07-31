#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

CONFIG_PATH="${CONFIG_PATH:-config/live_qmt_http.toml}"
LOG_DIR="${LOG_DIR:-output}"
RUN_SYNC_BEFORE_OPEN="${RUN_SYNC_BEFORE_OPEN:-1}"
RUN_AUTO_ARCHIVE="${RUN_AUTO_ARCHIVE:-1}"
RUN_GATEWAY_ACCEPTANCE="${RUN_GATEWAY_ACCEPTANCE:-0}"
RUN_TRADING_SESSION="${RUN_TRADING_SESSION:-0}"

RUNTIME_WINDOW_DAYS="${RUNTIME_WINDOW_DAYS:-548}"
ARCHIVE_OVERFLOW_DAYS="${ARCHIVE_OVERFLOW_DAYS:-61}"
ARCHIVE_MOVE_CHUNK_DAYS="${ARCHIVE_MOVE_CHUNK_DAYS:-61}"

GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://127.0.0.1:18080}"
QMT_GATEWAY_TOKEN="${QMT_GATEWAY_TOKEN:-}"
QMT_ACCOUNT_ID="${QMT_ACCOUNT_ID:-}"

mkdir -p "${LOG_DIR}"
ts="$(date +%Y%m%d_%H%M%S)"
log_file="${LOG_DIR}/daily_live_pipeline_${ts}.log"

echo "[INFO] config=${CONFIG_PATH}" | tee -a "${log_file}"
echo "[INFO] run_sync_before_open=${RUN_SYNC_BEFORE_OPEN} run_auto_archive=${RUN_AUTO_ARCHIVE} run_gateway_acceptance=${RUN_GATEWAY_ACCEPTANCE} run_trading_session=${RUN_TRADING_SESSION}" | tee -a "${log_file}"

echo "[STEP] prepare live plan (single-source handoff)" | tee -a "${log_file}"
LIVE_AUTO_SYNC_BEFORE_OPEN="${RUN_SYNC_BEFORE_OPEN}" \
uv run python main.py --mode prepare-live-plan --config "${CONFIG_PATH}" | tee -a "${log_file}"

if [[ "${RUN_AUTO_ARCHIVE}" == "1" ]]; then
  echo "[STEP] roll runtime overflow to archive dbs" | tee -a "${log_file}"
  uv run python scripts/roll_archive_runtime_db.py \
    --runtime-db "data/runtime/market_csi300_500_latest.db" \
    --archive-db "data/archive/market_csi300_500_archive.db" \
    --window-days "${RUNTIME_WINDOW_DAYS}" \
    --overflow-days "${ARCHIVE_OVERFLOW_DAYS}" \
    --move-chunk-days "${ARCHIVE_MOVE_CHUNK_DAYS}" | tee -a "${log_file}"
  uv run python scripts/roll_archive_runtime_db.py \
    --runtime-db "data/runtime/market_csi1000_latest.db" \
    --archive-db "data/archive/market_csi1000_archive.db" \
    --window-days "${RUNTIME_WINDOW_DAYS}" \
    --overflow-days "${ARCHIVE_OVERFLOW_DAYS}" \
    --move-chunk-days "${ARCHIVE_MOVE_CHUNK_DAYS}" | tee -a "${log_file}"
  uv run python scripts/roll_archive_runtime_db.py \
    --runtime-db "data/runtime/market_csi2000_latest.db" \
    --archive-db "data/archive/market_csi2000_archive.db" \
    --window-days "${RUNTIME_WINDOW_DAYS}" \
    --overflow-days "${ARCHIVE_OVERFLOW_DAYS}" \
    --move-chunk-days "${ARCHIVE_MOVE_CHUNK_DAYS}" | tee -a "${log_file}"
fi

if [[ "${RUN_GATEWAY_ACCEPTANCE}" == "1" ]]; then
  if [[ -z "${QMT_GATEWAY_TOKEN}" ]]; then
    echo "[WARN] RUN_GATEWAY_ACCEPTANCE=1 but QMT_GATEWAY_TOKEN is empty, skip acceptance." | tee -a "${log_file}"
  else
    echo "[STEP] gateway acceptance check" | tee -a "${log_file}"
    uv run python scripts/qmt_gateway_acceptance.py \
      --base-url "${GATEWAY_BASE_URL}" \
      --token "${QMT_GATEWAY_TOKEN}" \
      --account-id "${QMT_ACCOUNT_ID}" | tee -a "${log_file}"
  fi
fi

if [[ "${RUN_TRADING_SESSION}" == "1" ]]; then
  echo "[STEP] start live daemon (consume execution plan)" | tee -a "${log_file}"
  uv run python main.py --mode live-daemon --config "${CONFIG_PATH}" | tee -a "${log_file}"
else
  echo "[DONE] pre-open preparation finished (daemon not started)." | tee -a "${log_file}"
fi
