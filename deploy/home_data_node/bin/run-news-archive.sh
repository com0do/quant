#!/usr/bin/env bash
# Home data node: daily archive of limit-up pools, news flashes, investor Q&A and concept tags (scripts/news_archive.py)
# into data/news/. None of these sources keeps a long history, so this archive is what later research runs on.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
cd "$REPO_ROOT"

[[ -f "$DEPLOY_DIR/.env" ]] && { set -a; . "$DEPLOY_DIR/.env"; set +a; }
[[ -f "$REPO_ROOT/.env" ]]    && { set -a; . "$REPO_ROOT/.env";    set +a; }

export PATH="$HOME/.local/bin:$PATH"
: "${NEWS_ARCHIVE_SOURCES:=ths_limit_up,em_zt_pool,em_fast,sina_7x24,irm_qa,concept_tags}"
: "${NEWS_ARCHIVE_LOOKBACK_HOURS:=30}"

echo "[$(date -Iseconds)] run-news-archive start sources=$NEWS_ARCHIVE_SOURCES"
set +e
uv run python scripts/news_archive.py --sources "$NEWS_ARCHIVE_SOURCES" --lookback-hours "$NEWS_ARCHIVE_LOOKBACK_HOURS"
rc=$?
set -e
echo "[$(date -Iseconds)] run-news-archive done rc=$rc"
exit "$rc"
