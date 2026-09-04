#!/usr/bin/env bash
# Delete market data older than DATA_RETENTION_YEARS years from data/runtime and data/archive
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_ENV="$(dirname "$0")/../.env"
[[ -f "$DEPLOY_ENV" ]] && { set -a; . "$DEPLOY_ENV"; set +a; }
: "${DATA_RETENTION_YEARS:=3}"

export PATH="$HOME/.local/bin:$PATH"

PY=(uv run python scripts/purge_sqlite_retention.py --years "$DATA_RETENTION_YEARS")

"${PY[@]}" --glob "data/runtime/*.db"
if compgen -G "data/archive/*.db" >/dev/null; then
    "${PY[@]}" --glob "data/archive/*.db"
fi

for var in JQ_SYNC_CSI1000_DB JQ_SYNC_CSI2000_DB JQ_SYNC_CSI300_500_DB; do
    p="${!var:-}"
    [[ -n "$p" && -f "$p" ]] && "${PY[@]}" --db-path "$p"
done
