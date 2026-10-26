#!/usr/bin/env bash
# Install Atlas's cron schedules (every line ending in "# hq"; other lines, e.g. "# hq-devloop", are kept).
# Run by setup-pi.sh and by restart.sh after every update, so schedules added in merged work go live
# without re-running the setup by hand. Writes the crontab only when it changes. Safe to run by hand.
set -euo pipefail
cd "$(dirname "$0")/.."
Atlas="$(pwd -P)"   # the real folder, also when started through the ~/hq-system link (RA-042)
if [ -f .env ]; then set -a; . ./.env; set +a; fi

# With the systemd unit (hq.service) enabled, systemd starts and restarts the dashboard and the bot;
# cron starting them too made duplicate processes that fought over the port and Telegram.
managed=""
. deploy/systemd-lib.sh   # reaches the user bus from cron too; without it this check failed there
if hq_managed; then managed=1; fi

current="$(crontab -l 2>/dev/null | grep -v '^$' || true)"
wanted="$( printf '%s\n' "$current" | grep -v '# hq$' | grep -v '^$' || true
  [ -n "$managed" ] || echo "@reboot cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh serve >> memory/serve.log 2>&1  # hq"
  echo "0 * * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh cycle >/dev/null && ./atlas.sh intake --retry >/dev/null  # hq"
  # Research: feeds every few hours (interval and limits under Research → Sources & limits); RSS/JSON only, no model calls
  echo "15 * * * * cd $Atlas && ./atlas.sh radar scan --due >/dev/null 2>> memory/update.log  # hq"
  echo "50 6 * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh radar scan >/dev/null && ./atlas.sh brief >/dev/null  # hq"
  # Claude links open tasks that build no skill tree node yet (undo: hq tree autolink --undo), before the re-evaluation
  echo "30 6 * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh tree autolink >> memory/brain.log 2>&1  # hq"
  # Claude re-evaluates the plan before the brief; thoughts that came in while no model was reachable are sorted hourly
  echo "35 6 * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh brain think >> memory/brain.log 2>&1  # hq"
  # revisit of features built earlier: checked daily, runs once [revisit] every_days have passed (drafts only)
  echo "40 5 * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh revisit run --due >> memory/brain.log 2>&1  # hq"
  # revalidation of the tools and models Atlas uses against their radar news: checked daily, runs once [revalidate] every_days have passed
  echo "45 5 * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh revalidate run --due >> memory/brain.log 2>&1  # hq"
  # overlap check of the open backlog (AI tasks and ideas) against Atlas's functions and the arsenal; only stale verdicts are redone
  echo "20 6 * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh overlap scan --quiet >> memory/brain.log 2>&1  # hq"
  echo "25 * * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh brain sort >> memory/brain.log 2>&1  # hq"
  # work a usage limit stopped (Claude Code, Codex): read from their session logs, no model calls; shown first on Today
  echo "*/5 * * * * cd $Atlas && ./atlas.sh unfinished scan >/dev/null 2>> memory/update.log  # hq"
  # pull requests: ready to merge → merged → installed, and tasks they close; one gh call, no model calls
  echo "*/10 * * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh delivery sync --quiet >> memory/update.log 2>&1  # hq"
  # merged work goes live within 15 min (tests first)
  echo "*/15 * * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh update --auto >> memory/update.log 2>&1  # hq"
  echo "30 3 * * * cp $Atlas/memory/hq.db $Atlas/memory/hq.backup.db  # hq"
  # private, encrypted daily snapshot of the data to a separate private repository (engine/backup.py; key and repo in .env)
  echo "40 3 * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh backup run >> memory/update.log 2>&1  # hq"
  echo "50 18 * * 0 cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh review --send >/dev/null 2>> memory/bot.log  # hq"
  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then  # Telegram bot: keep it running (restarts within 5 min after a crash or reboot)
    [ -n "$managed" ] || echo "*/5 * * * * cd $Atlas && set -a && . ./.env && set +a && { pgrep -f 'python3 -m (hq|engine) bot\$' >/dev/null || ./atlas.sh bot >> memory/bot.log 2>&1; }  # hq"
    echo "55 6 * * * cd $Atlas && set -a && . ./.env && set +a && ./atlas.sh bot --send-brief >/dev/null 2>> memory/bot.log  # hq"
  fi
)"

if [ "$wanted" = "$current" ]; then
  echo "Schedules up to date"
else
  printf '%s\n' "$wanted" | crontab -
  echo "Schedules installed"
fi
