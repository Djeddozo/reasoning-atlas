#!/usr/bin/env bash
# Restart Atlas's long-running parts (dashboard, Telegram bot) so they run the current code.
# Used by `hq update` / the dashboard Update button / Telegram /update; safe to run by hand.
cd "$(dirname "$0")/.."
sleep 2                                   # let the request that triggered the update finish
set -a; . ./.env 2>/dev/null; set +a
. deploy/systemd-lib.sh
if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/atlas-web.service" ]; then
  npm run build >> memory/update.log 2>&1 &&
    systemctl --user --no-block restart atlas-web.service >> memory/update.log 2>&1
fi
# Checked as "is the unit installed", not "is it active right now", and with the user bus reachable from
# cron: an update that ran from cron (no bus) or landed between restarts fell through to the manual
# branch below and started a stray server that held the port, so hq.service crash-looped for hours.
if hq_managed; then
  hq_kill_strays >> memory/update.log 2>&1
  # systemd runs the dashboard and the bot (hq.service, Restart=always): starting copies here as well made two
  # servers fight over the port, and the loser crash-looped (OSError, address in use). Let systemd restart them.
  bash deploy/schedules.sh >> memory/update.log 2>&1
  if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/atlas-hub.service" ]; then   # Atlas hub (RA-030) runs the new code too
    systemctl --user --no-block restart atlas-hub.service >> memory/update.log 2>&1 || true
  fi
  echo "$(date '+%F %T') restarting hq.service" >> memory/update.log
  exec systemctl --user --no-block restart hq.service   # queued in systemd: completes although this script runs inside the unit
fi
# no systemd unit (manual setup): restart the processes ourselves
pkill -f "python3 -m (hq|engine) serve" 2>/dev/null
pkill -f "python3 -m (hq|engine) bot$" 2>/dev/null
sleep 1
nohup ./atlas.sh serve >> memory/serve.log 2>&1 &
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  nohup ./atlas.sh bot >> memory/bot.log 2>&1 &
fi
bash deploy/schedules.sh >> memory/update.log 2>&1   # schedules added in merged work go live without re-running setup
echo "$(date '+%F %T') restarted" >> memory/update.log
