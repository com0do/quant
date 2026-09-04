#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
        echo "Run with sudo: sudo bash uninstall.sh"; exit 1
fi

for u in quant-daily.timer quant-daily.service \
         quant-jq-sync.timer quant-jq-sync.service \
         quant-jq-signin.timer quant-jq-signin.service \
         quant-r2-backup.timer quant-r2-backup.service; do
    systemctl disable --now "$u" 2>/dev/null || true
    rm -f "/etc/systemd/system/$u"
done
systemctl daemon-reload
echo "[ok] systemd units removed. data/ and .env were left untouched."
