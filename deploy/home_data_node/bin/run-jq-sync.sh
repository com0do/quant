#!/usr/bin/env bash
# =============================================================================
# Daily JQ sync entry point of the home data node
# Called by the systemd unit quant-jq-sync.service.
# Can also be run by hand: bash deploy/home_data_node/bin/run-jq-sync.sh
# =============================================================================
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# Load configuration (the top-level .env has higher priority)
[[ -f "$DEPLOY_DIR/.env" ]]   && { set -a; . "$DEPLOY_DIR/.env";   set +a; }
[[ -f "$REPO_ROOT/.env" ]]    && { set -a; . "$REPO_ROOT/.env";    set +a; }

: "${JQ_USERNAME:?JQ_USERNAME is not set in the top-level .env}"
: "${JQ_PASSWORD:?JQ_PASSWORD is not set in the top-level .env}"
: "${JQ_SYNC_LOOKBACK_DAYS:=60}"

END_DATE="$(date +%Y-%m-%d)"
START_DATE="$(date -d "${JQ_SYNC_LOOKBACK_DAYS} days ago" +%Y-%m-%d)"

echo "[$(date -Iseconds)] run-jq-sync start  window=[$START_DATE, $END_DATE]  repo=$REPO_ROOT"

mkdir -p output reports data/runtime data/meta

# uv may only be on the user's PATH
export PATH="$HOME/.local/bin:$PATH"

sync_one() {
    local idx="$1"
    local db="$2"
    local label="$3"

    if [[ -z "$db" ]]; then
                echo "[skip] $label: database path not configured"
        return 0
    fi

    echo
    echo "==== $label ($idx -> $db) ===="

        # scripts/jq_bulk_sync_smallcap.py tracks incremental progress in the sync_progress table itself,
        # so passing a wide window does not re-fetch data; days missed during a power outage are filled in too.
    if ! uv run python scripts/jq_bulk_sync_smallcap.py \
            --index-code "$idx" \
            --start-date "$START_DATE" \
            --end-date "$END_DATE" \
            --db-path "$db" \
            --constituents-db "data/meta/index_constituents.db" \
            --sync-snapshots; then
                echo "[warn] $label sync failed (possibly the daily quota is used up / a network glitch); continuing with the next tier"
        return 1
    fi
}

FAIL=0
sync_one "000852.XSHG" "${JQ_SYNC_CSI1000_DB:-}"    "CSI 1000"    || FAIL=$((FAIL + 1))
sync_one "399303.XSHE" "${JQ_SYNC_CSI2000_DB:-}"    "CSI 2000"    || FAIL=$((FAIL + 1))
sync_one "000905.XSHG" "${JQ_SYNC_CSI300_500_DB:-}" "CSI 300+500" || FAIL=$((FAIL + 1))

# Write a health snapshot
STATUS_FILE="output/home_data_node_sync_status.json"
python3 - "$STATUS_FILE" "$FAIL" "$START_DATE" "$END_DATE" <<'PY'
import json, os, sqlite3, sys
from datetime import datetime

out_path, fail_count, start, end = sys.argv[1:]
report = {
    "ts": datetime.utcnow().isoformat() + "Z",
    "window": {"start": start, "end": end},
    "fail_count": int(fail_count),
    "dbs": {},
}
for name in ("JQ_SYNC_CSI1000_DB", "JQ_SYNC_CSI2000_DB", "JQ_SYNC_CSI300_500_DB"):
    p = os.environ.get(name, "")
    if not p or not os.path.exists(p):
        continue
    try:
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM prices_daily"
        ).fetchone()
        report["dbs"][p] = {
            "min_date": rows[0], "max_date": rows[1], "row_count": rows[2],
            "size_mb": round(os.path.getsize(p) / 1_048_576, 1),
        }
        conn.close()
    except Exception as e:
        report["dbs"][p] = {"error": str(e)}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"[status] {out_path}")
print(json.dumps(report, ensure_ascii=False, indent=2))
PY

echo
echo "[$(date -Iseconds)] run-jq-sync done  fail=$FAIL"
exit "$FAIL"
