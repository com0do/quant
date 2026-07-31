#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DB_PATH="${DB_PATH:-data/runtime/market_csi1000_latest.db}"
START_DATE="${START_DATE:-2025-12-17}"
END_DATE="${END_DATE:-2025-12-23}"
BATCH_SIZE="${BATCH_SIZE:-180}"
START_OFFSET="${START_OFFSET:-}"
LOG_DIR="${LOG_DIR:-output}"
MAX_BATCHES="${MAX_BATCHES:-30}"

mkdir -p "$LOG_DIR"

echo "[STEP] detect JQ permission window..."
perm_json="$(uv run python scripts/detect_jq_permission_window.py --probe-code 000852.XSHG --start-date 2000-01-01 --end-date 2100-12-31 || true)"
readarray -t perm_range < <(python - "${perm_json}" <<'PY'
import json, re, sys
raw = sys.argv[1]
m = re.search(r"\{[\s\S]*\}\s*$", raw)
if not m:
    raise SystemExit(0)
try:
    obj = json.loads(m.group(0))
except Exception:
    raise SystemExit(0)
print(str(obj.get("allowed_start", "")).strip())
print(str(obj.get("allowed_end", "")).strip())
PY
<<< "${perm_json}")
perm_start="${perm_range[0]:-}"
perm_end="${perm_range[1]:-}"
if [[ -n "${perm_start}" && -n "${perm_end}" ]]; then
  readarray -t adj_dates < <(python - "${START_DATE}" "${END_DATE}" "${perm_start}" "${perm_end}" <<'PY'
import sys
from datetime import date
s_in=date.fromisoformat(sys.argv[1]); e_in=date.fromisoformat(sys.argv[2])
s_lim=date.fromisoformat(sys.argv[3]); e_lim=date.fromisoformat(sys.argv[4])
s=max(s_in, s_lim); e=min(e_in, e_lim)
print(s.isoformat()); print(e.isoformat())
PY
)
  START_DATE="${adj_dates[0]}"
  END_DATE="${adj_dates[1]}"
  echo "[INFO] permission_window=${perm_start}..${perm_end}"
  echo "[INFO] adjusted_sync_window=${START_DATE}..${END_DATE}"
fi

if python - "${START_DATE}" "${END_DATE}" <<'PY'
import sys
from datetime import date
raise SystemExit(0 if date.fromisoformat(sys.argv[1]) <= date.fromisoformat(sys.argv[2]) else 1)
PY
then
  :
else
  echo "[DONE] adjusted window is empty, skip today."
  exit 0
fi

if [[ -z "${START_OFFSET}" ]]; then
  START_OFFSET="$(uv run python - "${DB_PATH}" <<'PY'
import sqlite3
import sys
conn=sqlite3.connect(sys.argv[1])
cur=conn.cursor()
try:
    n=cur.execute("select count(distinct code) from prices_minute").fetchone()[0]
except Exception:
    n=0
print(int(n))
conn.close()
PY
)"
fi

echo "[INFO] db=${DB_PATH}"
echo "[INFO] start=${START_DATE} end=${END_DATE}"
echo "[INFO] start_offset=${START_OFFSET} batch_size=${BATCH_SIZE}"

spare_raw="$(uv run python - <<'PY'
from quant.stock_data.jq_client import JqClient
jq=JqClient()
q=jq.query_count()
print(f"__SPARE__{int(q.get('spare', 0))}")
try:
    jq.logout()
except Exception:
    pass
PY
)"
spare="$(printf "%s\n" "${spare_raw}" | rg -o "__SPARE__[0-9]+" | rg -o "[0-9]+" || true)"
spare="${spare:-0}"
if [[ "${spare}" -le 0 ]]; then
  echo "[DONE] no quota left today (spare=${spare})."
  exit 0
fi
echo "[INFO] spare_before=${spare}"

echo "[STEP] sync index daily..."
uv run python scripts/sync_index_daily_to_main.py \
  --db-path "${DB_PATH}" \
  --start-date "${START_DATE}" \
  --end-date "${END_DATE}"

offset="${START_OFFSET}"
batch=1
while true; do
  if [[ "${batch}" -gt "${MAX_BATCHES}" ]]; then
    echo "[DONE] reached MAX_BATCHES=${MAX_BATCHES}, stop."
    break
  fi
  echo "[STEP] minute batch=${batch} offset=${offset} limit=${BATCH_SIZE}"
  log_file="${LOG_DIR}/minute_sync_batch_${batch}.log"
  uv run python scripts/sync_minute_smallcap_batch.py \
    --db-path "${DB_PATH}" \
    --start-date "${START_DATE}" \
    --end-date "${END_DATE}" \
    --offset "${offset}" \
    --limit "${BATCH_SIZE}" | tee "${log_file}"

  if rg -q "\\[STOP\\] quota reached" "${log_file}"; then
    echo "[DONE] quota reached; stop batches."
    break
  fi
  if rg -q "账号权限仅能获取" "${log_file}"; then
    echo "[DONE] permission window reached; stop batches."
    break
  fi
  if rg -q "codes=0" "${log_file}"; then
    echo "[DONE] no more symbols for this ranking window."
    break
  fi

  offset=$((offset + BATCH_SIZE))
  batch=$((batch + 1))
done

echo "[STEP] quota summary..."
uv run python - <<'PY'
from quant.stock_data.jq_client import JqClient
jq=JqClient()
print(jq.query_count())
try:
    jq.logout()
except Exception:
    pass
PY

echo "[DONE] tomorrow sync workflow finished."

