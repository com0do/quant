#!/usr/bin/env bash
# home_data_node/setup.sh — one-shot deploy / upgrade (idempotent)
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
RUN_AS_USER="${SUDO_USER:-$USER}"
RUN_AS_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"

c_ok()   { printf "\033[32m[ok]\033[0m %s\n" "$*"; }
c_warn() { printf "\033[33m[warn]\033[0m %s\n" "$*"; }
c_err()  { printf "\033[31m[err]\033[0m %s\n" "$*" >&2; }
c_step() { printf "\n\033[36m==> %s\033[0m\n" "$*"; }

if [[ "$(id -u)" -ne 0 ]]; then
        c_err "Run with sudo: sudo bash setup.sh"
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
        c_err "systemd is required"
    exit 1
fi

if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
        c_err "$DEPLOY_DIR/.env does not exist; run cp .env.example .env first"
    exit 1
fi

if [[ ! -f "$REPO_ROOT/.env" ]]; then
        c_warn "$REPO_ROOT/.env does not exist; sync/check-in will be missing JQ_USERNAME/JQ_PASSWORD"
fi

set -a
# shellcheck disable=SC1091
. "$DEPLOY_DIR/.env"
set +a

: "${DAILY_ONCALENDAR:=*-*-* 00:10:00 UTC}"

c_step "Installing system dependencies"
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq jq rclone sqlite3 tar ca-certificates curl xz-utils >/dev/null
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q jq rclone sqlite tar ca-certificates curl xz >/dev/null
fi
c_ok "System dependencies in place"

c_step "Checking uv"
if sudo -u "$RUN_AS_USER" bash -c 'command -v uv >/dev/null 2>&1'; then
        c_ok "uv already installed"
else
    sudo -u "$RUN_AS_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
        c_ok "uv installed"
fi

c_step "uv sync"
sudo -u "$RUN_AS_USER" bash -lc "cd '$REPO_ROOT' && $RUN_AS_HOME/.local/bin/uv sync"
c_ok "Python dependencies in place"

if [[ "${JQ_SIGNIN_ENABLE:-true}" == "true" ]]; then
        c_step "Installing Playwright (JoinQuant check-in)"
    sudo -u "$RUN_AS_USER" bash -lc "cd '$REPO_ROOT' && $RUN_AS_HOME/.local/bin/uv pip install playwright Pillow numpy"
    sudo -u "$RUN_AS_USER" bash -lc "cd '$REPO_ROOT' && $RUN_AS_HOME/.local/bin/uv run playwright install chromium" || \
                c_warn "playwright install chromium failed; run it manually later"
fi

if [[ "${TAILSCALE_ENABLE:-false}" == "true" ]]; then
    c_step "Tailscale"
    if ! command -v tailscale >/dev/null 2>&1; then
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
    systemctl enable --now tailscaled
    if [[ -n "${TAILSCALE_AUTHKEY:-}" ]]; then
        tailscale up --authkey "$TAILSCALE_AUTHKEY" --hostname "quant-data-$(hostname)" --accept-routes || true
    fi
fi

if [[ "${R2_ENABLE:-false}" == "true" ]]; then
        c_step "Configuring rclone R2"
    RCLONE_DIR="$RUN_AS_HOME/.config/rclone"
    RCLONE_CONF="$RCLONE_DIR/rclone.conf"
    mkdir -p "$RCLONE_DIR"
    if ! grep -q '^\[r2\]' "$RCLONE_CONF" 2>/dev/null; then
        cat >> "$RCLONE_CONF" <<EOF

[r2]
type = s3
provider = Cloudflare
access_key_id = ${R2_ACCESS_KEY_ID}
secret_access_key = ${R2_SECRET_ACCESS_KEY}
endpoint = ${R2_ENDPOINT}
region = auto
acl = private
EOF
        chown -R "$RUN_AS_USER:$RUN_AS_USER" "$RCLONE_DIR"
        chmod 600 "$RCLONE_CONF"
    fi
fi

c_step "Generating the systemd unit (single quant-daily task)"
UNIT_DIR="/etc/systemd/system"
render_unit() {
    local src="$1"
    local dst="$UNIT_DIR/$(basename "$src")"
    sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" \
        -e "s|@RUN_AS_USER@|$RUN_AS_USER|g" \
        -e "s|@RUN_AS_HOME@|$RUN_AS_HOME|g" \
        -e "s|@DAILY_ONCALENDAR@|${DAILY_ONCALENDAR}|g" \
        "$src" > "$dst"
        c_ok "Wrote $dst"
}

render_unit "$DEPLOY_DIR/systemd/quant-daily.service"
render_unit "$DEPLOY_DIR/systemd/quant-daily.timer"

# Disable the old split timers (upgrade path)
for old in quant-jq-sync quant-jq-signin quant-r2-backup; do
    systemctl disable --now "${old}.timer" 2>/dev/null || true
    systemctl disable --now "${old}.service" 2>/dev/null || true
done

systemctl daemon-reload
systemctl enable --now quant-daily.timer

c_step "Done"
cat <<EOF

Enabled: quant-daily.timer  (check-in + sync + ${DATA_RETENTION_YEARS:-3}-year purge + optional R2)

  systemctl list-timers | grep quant
    sudo systemctl start quant-daily.service      # run once right now
  sudo journalctl -u quant-daily -f

Repo : $REPO_ROOT
User : $RUN_AS_USER

EOF
