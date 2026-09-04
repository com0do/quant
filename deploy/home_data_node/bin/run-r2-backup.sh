#!/usr/bin/env bash
# =============================================================================
# Take a SQLite-consistent backup of data/runtime/*.db and data/meta/*.db, compress it with xz,
# add the news archive data/news/ as news.tar.xz, and upload it to Cloudflare R2.
#   - uses `sqlite3 .backup` for consistency (never tar a file that is being written)
#   - upload path: r2://<bucket>/<prefix>/YYYYMMDD/<file>.db.xz
#   - keeps the most recent R2_KEEP_LAST days
# =============================================================================
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
cd "$REPO_ROOT"

[[ -f "$DEPLOY_DIR/.env" ]] && { set -a; . "$DEPLOY_DIR/.env"; set +a; }

: "${R2_ENABLE:=false}"
if [[ "$R2_ENABLE" != "true" ]]; then
    echo "[skip] R2_ENABLE=false"
    exit 0
fi

: "${R2_BUCKET:=quant-data}"
: "${R2_HOSTNAME_PREFIX:=home}"
: "${R2_KEEP_LAST:=14}"

STAMP="$(date +%Y%m%d)"
STAGING="$(mktemp -d -t quant-r2-XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT

echo "[$(date -Iseconds)] r2-backup start  stamp=$STAMP  staging=$STAGING"

backup_one() {
    local src="$1"
    if [[ ! -f "$src" ]]; then
        echo "[skip] missing $src"
        return 0
    fi
    local name
    name="$(basename "$src")"
    local mid="$STAGING/$name"
    echo "  [.] sqlite backup  $src"
    sqlite3 "$src" ".backup '$mid'"
    echo "  [.] xz -T0 -6      $name"
    xz -T0 -6 --force "$mid"
    ls -lh "$mid.xz" | awk '{print "  [ok]", $9, $5}'
}

for db in data/runtime/*.db data/meta/*.db; do
    backup_one "$db"
done

if [[ -d data/news ]]; then
    echo "  [.] tar -J        data/news"
    tar -C data -cJf "$STAGING/news.tar.xz" news
    ls -lh "$STAGING/news.tar.xz" | awk '{print "  [ok]", $9, $5}'
fi

# Upload
DEST="r2:$R2_BUCKET/$R2_HOSTNAME_PREFIX/$STAMP"
echo
echo "[upload] rclone copy $STAGING/ -> $DEST"
rclone copy "$STAGING" "$DEST" --progress --stats-one-line --stats=30s

# Update the "latest" alias so CI / other nodes do not need to know the date
LATEST="r2:$R2_BUCKET/$R2_HOSTNAME_PREFIX/latest"
echo "[upload] rclone sync  $STAGING/ -> $LATEST"
rclone sync "$STAGING" "$LATEST" --stats-one-line --stats=30s

# Prune old snapshots (sort date directory names lexicographically in descending order and keep the first N)
echo
echo "[cleanup] keeping last $R2_KEEP_LAST daily snapshots"
mapfile -t OLD_DIRS < <(
    rclone lsd "r2:$R2_BUCKET/$R2_HOSTNAME_PREFIX" 2>/dev/null \
        | awk '{print $NF}' \
        | grep -E '^[0-9]{8}$' \
        | sort -r \
        | tail -n +$((R2_KEEP_LAST + 1))
)
for d in "${OLD_DIRS[@]:-}"; do
    [[ -z "$d" ]] && continue
    echo "  [rm] $d"
    rclone purge "r2:$R2_BUCKET/$R2_HOSTNAME_PREFIX/$d" || true
done

echo
echo "[$(date -Iseconds)] r2-backup done"
