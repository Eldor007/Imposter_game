#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run: sudo bash deploy/install.sh'; exit 1; }
command -v python3 >/dev/null
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
cd /opt/imposter-bot
id imposter >/dev/null 2>&1 || useradd --system --home /var/lib/imposter-bot --shell /usr/sbin/nologin imposter
if [[ ! -s /etc/imposter-bot.env ]]; then
  read -r -s -p 'Paste NEW game bot token from BotFather (hidden): ' bot_token </dev/tty
  printf '\n' >/dev/tty
  [[ "$bot_token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || { echo 'Invalid token format'; exit 1; }
  umask 077
  printf 'BOT_TOKEN=%s\n' "$bot_token" > /etc/imposter-bot.env
  unset bot_token
fi
chmod 600 /etc/imposter-bot.env
python3 - <<'PY'
import json, urllib.request
from pathlib import Path
token = Path('/etc/imposter-bot.env').read_text().strip().split('=', 1)[1]
try:
    with urllib.request.urlopen('https://api.telegram.org/bot' + token + '/getMe', timeout=15) as r:
        info = json.load(r)
    print('Token verified: @' + info['result']['username'])
except Exception:
    raise SystemExit('Token/network check failed. Edit /etc/imposter-bot.env and retry.') from None
PY
install -m 644 imposter-bot.service /etc/systemd/system/imposter-bot.service
systemctl daemon-reload
systemctl enable --now imposter-bot
sleep 2
systemctl --no-pager --full status imposter-bot
echo 'Installed. Logs: sudo journalctl -u imposter-bot -n 30 --no-pager'
