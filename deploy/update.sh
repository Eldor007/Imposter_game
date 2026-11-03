#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
cd /opt/imposter-bot
exec 9>/run/lock/imposter-bot-update.lock
flock -n 9 || { echo 'Another bot update is running'; exit 1; }
[[ "$(git branch --show-current)" == main ]] || { echo 'Expected branch main'; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'Tracked files have local changes; update stopped'; exit 1; }
git fetch --quiet origin main
git merge --ff-only origin/main
python3 -m unittest discover -s tests -v
STATE_FILE=/var/lib/imposter-bot/state.json python3 bot.py --check-state
# Take a consistent final snapshot while only this bot is stopped.
systemctl stop imposter-bot
trap 'systemctl start imposter-bot' ERR
if [[ -s /var/lib/imposter-bot/state.json ]]; then
  install -d -m 700 /var/backups/imposter-bot
  backup="/var/backups/imposter-bot/state-$(date -u +%Y%m%dT%H%M%S)-$$.json"
  install -m 600 /var/lib/imposter-bot/state.json "$backup"
  echo "Game snapshot: $backup"
fi
install -m 644 imposter-bot.service /etc/systemd/system/imposter-bot.service
systemctl daemon-reload
systemctl start imposter-bot
trap - ERR
sleep 2
systemctl --no-pager status imposter-bot
