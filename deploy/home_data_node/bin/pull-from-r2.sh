#!/usr/bin/env bash
# =============================================================================
# Pull the latest snapshot from R2 into the local data/. Useful for:
#   - an office Linux machine: run backtests on the latest data pushed from home
#   - a GitHub Actions runner: run this right after checkout in the workflow
#   - bootstrapping a newly bought machine
#
# Usage:
#   R2_BUCKET=quant-data R2_HOSTNAME_PREFIX=home \
#     bash deploy/home_data_node/bin/pull-from-r2.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

# Defaults may come from the top-level .env or deploy/home_data_node/.env
for env_file in "$REPO_ROOT/.env" "$REPO_ROOT/deploy/home_data_node/.env"; do
    [[ -f "$env_file" ]] && { set -a; . "$env_file"; set +a; }
done

: "${R2_BUCKET:?R2_BUCKET is not set}"
: "${R2_HOSTNAME_PREFIX:=home}"
SRC="r2:$R2_BUCKET/$R2_HOSTNAME_PREFIX/latest"

mkdir -p data/runtime data/meta
STAGING="$(mktemp -d -t quant-pull-XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT

echo "[pull] rclone copy $SRC -> $STAGING"
rclone copy "$SRC" "$STAGING" --stats-one-line

echo "[decompress] xz -d"
for xz in "$STAGING"/*.xz; do
    [[ -f "$xz" ]] || continue
    xz -d "$xz"
done

echo "[move] $STAGING/* -> data/{runtime,meta}/"
for f in "$STAGING"/*.db; do
    [[ -f "$f" ]] || continue
    name="$(basename "$f")"
    case "$name" in
        index_constituents.db)          mv -f "$f" "data/meta/$name" ;;
        market_*.db)                    mv -f "$f" "data/runtime/$name" ;;
        *)                              mv -f "$f" "data/$name" ;;
    esac
done

echo "[done] local data is now in sync with R2 latest"
ls -lh data/runtime/*.db data/meta/*.db 2>/dev/null
