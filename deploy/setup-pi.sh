#!/usr/bin/env bash
# One-time Atlas setup on the Raspberry Pi. Safe to re-run.
# Usage (inside the cloned repo):  bash deploy/setup-pi.sh
set -euo pipefail
cd "$(dirname "$0")/.."
Atlas="$(pwd -P)"   # the real folder, also when started through the ~/hq-system link (RA-042)

python3 -c 'import sys; assert sys.version_info >= (3, 11), "Atlas needs Python 3.11+"'

# 1. Secrets file with a fresh token and a private push topic
if [ ! -f .env ]; then
  TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
  TOPIC="hq-$(python3 -c "import secrets;print(secrets.token_hex(8))")"
  sed -e "s|^HQ_TOKEN=.*|HQ_TOKEN=$TOKEN|" -e "s|^HQ_NTFY_TOPIC=.*|HQ_NTFY_TOPIC=$TOPIC|" deploy/env.example > .env
  chmod 600 .env
  echo "Created .env"
fi
./atlas.sh link >/dev/null   # adds HQ_TOKEN to an existing .env that has none
set -a; . ./.env; set +a

# 2. Memory database
[ -f memory/hq.db ] || ./atlas.sh init --seed

# 3. Schedules (replaces any previous Atlas lines; restart.sh re-applies them after every update)
bash deploy/schedules.sh

# 4. Gatekeeper for Claude Code on this Pi (user-level, so it only applies here)
mkdir -p ~/.claude
if [ -f ~/.claude/settings.json ] && ! grep -q "hq gate" ~/.claude/settings.json; then
  cp ~/.claude/settings.json ~/.claude/settings.backup.json
  echo "Existing ~/.claude/settings.json backed up to settings.backup.json"
fi
sed "s|~/hq-system|$Atlas|" deploy/claude-settings.pi.json > ~/.claude/settings.json
echo "Gatekeeper hook installed for Claude Code"

# 5. Start the dashboard now (localhost only until Tailscale is set up)
pkill -f "python3 -m (hq|engine) serve" 2>/dev/null || true   # restart, so it uses the current token
sleep 1
nohup ./atlas.sh serve >> memory/serve.log 2>&1 &
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  pkill -f 'python3 -m (hq|engine) bot$' 2>/dev/null || true   # restart, so it runs the current code
  nohup ./atlas.sh bot >> memory/bot.log 2>&1 &
  echo "Telegram bot started"
fi

cat <<EOF

Done. Last step on your phone:
  1. Install the "ntfy" app and subscribe to topic:  $HQ_NTFY_TOPIC
  2. Test a push:  ./atlas.sh ask "Test from Atlas" --reason "setup check"
Phone access: install Tailscale (see README), then: bash deploy/phone-dashboard.sh
Dashboard link (open once per browser; it remembers the token):  $(./atlas.sh link)
From your laptop without Tailscale: ssh -L 8765:127.0.0.1:8765 $(whoami)@$(hostname -I | awk '{print $1}'), then http://localhost:8765/?t=$HQ_TOKEN
Show the link again any time: ./atlas.sh link   (or /dashboard in Telegram)
EOF
