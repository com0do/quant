#!/usr/bin/env bash
# GitHub Actions / any CI: pull the latest data/runtime/*.db snapshots from Cloudflare R2.
# Required environment variables: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT
# Optional: R2_BUCKET (default quant-data), R2_HOSTNAME_PREFIX (default home)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "${R2_ACCESS_KEY_ID:-}" || -z "${R2_SECRET_ACCESS_KEY:-}" || -z "${R2_ENDPOINT:-}" ]]; then
    echo "::notice::R2 secrets not configured (R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT); skipping data pull"
    exit 0
fi

: "${R2_BUCKET:=quant-data}"
: "${R2_HOSTNAME_PREFIX:=home}"

if ! command -v rclone >/dev/null 2>&1; then
    echo "::error::rclone not installed"
    exit 1
fi

mkdir -p ~/.config/rclone
cat > ~/.config/rclone/rclone.conf <<EOF
[r2]
type = s3
provider = Cloudflare
access_key_id = ${R2_ACCESS_KEY_ID}
secret_access_key = ${R2_SECRET_ACCESS_KEY}
endpoint = ${R2_ENDPOINT}
region = auto
acl = private
EOF

bash deploy/home_data_node/bin/pull-from-r2.sh
