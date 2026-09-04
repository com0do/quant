#!/usr/bin/env bash
# Home data node: JoinQuant website check-in to collect credits (needs a Mainland China IP + Playwright)
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
cd "$REPO_ROOT"

[[ -f "$DEPLOY_DIR/.env" ]] && { set -a; . "$DEPLOY_DIR/.env"; set +a; }
[[ -f "$REPO_ROOT/.env" ]]    && { set -a; . "$REPO_ROOT/.env";    set +a; }

export PATH="$HOME/.local/bin:$PATH"
export JQ_DEBUG_DIR="${JQ_DEBUG_DIR:-$REPO_ROOT/output}"

echo "[$(date -Iseconds)] run-jq-signin start"

# Make sure Chromium is installed on first run (idempotent)
if ! uv run python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
    echo "[install] playwright python package missing — run: uv pip install playwright scipy Pillow numpy"
    exit 1
fi
if [ ! -d "$HOME/.cache/ms-playwright" ] && [ ! -d "$HOME/.local/share/ms-playwright" ]; then
    echo "[install] playwright install chromium..."
    uv run playwright install chromium
fi

set +e
uv run python scripts/jq_auto_claim.py
rc=$?
set -e

echo "[$(date -Iseconds)] run-jq-signin done rc=$rc"
exit "$rc"
