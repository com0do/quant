#!/usr/bin/env bash
# Daily JoinQuant check-in for Gitee Go (.workflow/jq-signin.yml). JoinQuant only serves Mainland-China IPs,
# so this runs on Gitee's runners; GitHub-hosted runners are region-blocked.
# Credentials JQ_USERNAME / JQ_PASSWORD are Gitee Go general variables associated with the pipeline and exported
# by the pipeline file (never stored in the repo).
set -euo pipefail

PIP_INDEX="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
# The default Playwright CDN is overseas and stalls from Gitee runners; npmmirror serves the same builds.
PW_MIRROR="${PLAYWRIGHT_DOWNLOAD_HOST:-https://cdn.npmmirror.com/binaries/playwright}"

python3 -V
python3 -m pip install -q --disable-pip-version-check -i "$PIP_INDEX" playwright numpy pillow

# Fail fast before the slow browser install: a region block or missing credentials means the run cannot succeed.
python3 - <<'PY'
import sys, urllib.request
sys.path.insert(0, "scripts")
import jq_auto_claim as j
try:
    html = urllib.request.urlopen(j.LOGIN_URLS[0], timeout=20).read().decode("utf-8", "replace")
except Exception as exc:
    print("[ci] login page probe failed:", exc)
    sys.exit(0)
blocked = any(m in html for m in j.REGION_BLOCK_MARKERS)
print("[ci] login page reachable, region blocked:", blocked)
sys.exit(2 if blocked else 0)
PY

has() { [ -n "${!1:-}" ] && echo yes || echo no; }
echo "[ci] credentials present: JQ_USERNAME=$(has JQ_USERNAME) JQ_PASSWORD=$(has JQ_PASSWORD)"
if [ "$(has JQ_USERNAME)" = no ] || [ "$(has JQ_PASSWORD)" = no ]; then
    echo "[ci] define JQ_USERNAME / JQ_PASSWORD as Gitee Go general variables and associate them with this pipeline"
    exit 1
fi

# jq_auto_claim launches headless=True, which only needs chromium-headless-shell (about half of full Chrome).
if ! PLAYWRIGHT_DOWNLOAD_HOST="$PW_MIRROR" python3 -m playwright install --with-deps --only-shell chromium; then
    echo "[ci] mirror download failed, retrying from the default Playwright CDN"
    python3 -m playwright install --with-deps --only-shell chromium
fi

mkdir -p output
JQ_DEBUG_DIR=output python3 scripts/jq_auto_claim.py
