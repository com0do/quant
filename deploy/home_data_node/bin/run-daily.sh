#!/usr/bin/env bash
# =============================================================================
# Home data node: one-stop daily maintenance (the only entry point, called by quant-daily.service)
#
# Order:
#   1. JoinQuant website check-in to collect credits
#   2. Incremental JQ sync of CSI 1000/2000/300+500
#   3. News / theme archive into data/news/ (limit-up pools, news flashes, investor Q&A, concept tags)
#   4. 3-year LRU: delete SQLite rows older than the cutoff + VACUUM
#   5. (optional) R2 cold backup
# =============================================================================
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
cd "$REPO_ROOT"

[[ -f "$DEPLOY_DIR/.env" ]] && { set -a; . "$DEPLOY_DIR/.env"; set +a; }
[[ -f "$REPO_ROOT/.env" ]]    && { set -a; . "$REPO_ROOT/.env";    set +a; }

export PATH="$HOME/.local/bin:$PATH"
: "${DATA_RETENTION_YEARS:=3}"

LOG_TAG="[quant-daily $(date -Iseconds)]"
SYNC_RC=0
SIGNIN_RC=0

echo "$LOG_TAG start repo=$REPO_ROOT"

mkdir -p output reports data/runtime data/meta

# ---- 1) Check-in (a failure does not block the sync) ---------------------------------------------
if [[ "${JQ_SIGNIN_ENABLE:-true}" == "true" ]]; then
    echo "$LOG_TAG step=signin"
    set +e
    bash "$DEPLOY_DIR/bin/run-jq-signin.sh"
    SIGNIN_RC=$?
    set -e
    if [[ "$SIGNIN_RC" -ne 0 ]]; then
        echo "$LOG_TAG warn signin failed rc=$SIGNIN_RC (continuing)"
    fi
else
    echo "$LOG_TAG skip signin (JQ_SIGNIN_ENABLE=false)"
fi

# ---- 2) Data sync -----------------------------------------------------------
echo "$LOG_TAG step=sync"
set +e
bash "$DEPLOY_DIR/bin/run-jq-sync.sh"
SYNC_RC=$?
set -e
if [[ "$SYNC_RC" -ne 0 ]]; then
    echo "$LOG_TAG warn sync partial failure rc=$SYNC_RC"
fi

# ---- 3) News / theme archive (a failure does not block the later steps) -------------------
if [[ "${NEWS_ARCHIVE_ENABLE:-true}" == "true" ]]; then
    echo "$LOG_TAG step=news-archive"
    bash "$DEPLOY_DIR/bin/run-news-archive.sh" || echo "$LOG_TAG warn news archive failed"
else
    echo "$LOG_TAG skip news archive (NEWS_ARCHIVE_ENABLE=false)"
fi

# ---- 4) 3-year LRU purge ------------------------------------------------------
echo "$LOG_TAG step=retention years=$DATA_RETENTION_YEARS"
bash "$DEPLOY_DIR/bin/run-retention-purge.sh" || echo "$LOG_TAG warn retention purge failed"

# ---- 5) Optional R2 cold backup ------------------------------------------------------
if [[ "${R2_ENABLE:-false}" == "true" ]]; then
    echo "$LOG_TAG step=r2-backup"
    bash "$DEPLOY_DIR/bin/run-r2-backup.sh" || echo "$LOG_TAG warn r2 backup failed"
else
    echo "$LOG_TAG skip r2 (R2_ENABLE=false)"
fi

echo "$LOG_TAG done signin_rc=$SIGNIN_RC sync_rc=$SYNC_RC"

if [[ "${SUSPEND_AFTER_SYNC:-false}" == "true" ]]; then
    echo "$LOG_TAG suspend"
    sleep 5
    systemctl suspend || true
fi

# Return non-zero only if the sync failed completely (a check-in failure does not count)
exit "$SYNC_RC"
