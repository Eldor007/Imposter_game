#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
cd /opt/imposter-bot
git pull --ff-only origin main
python3 -m unittest discover -s tests -v
install -m 644 imposter-bot.service /etc/systemd/system/imposter-bot.service
systemctl daemon-reload
systemctl restart imposter-bot
systemctl --no-pager status imposter-bot
