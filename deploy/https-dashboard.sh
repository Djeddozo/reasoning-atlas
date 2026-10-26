#!/usr/bin/env bash
# Serve the Atlas dashboard over HTTPS inside your tailnet, so browsers allow the microphone (Talk button).
# Uses Tailscale's free certificates: the address becomes https://<pi-name>.<your-tailnet>.ts.net[:port] and is
# reachable only from your own Tailscale devices. Safe to re-run.
#
# Ports (in .env): HQ_PORT is where the dashboard listens on the Pi (default 8765); HQ_HTTPS_PORT is the
# HTTPS port Tailscale serves it on (default 443). Pick free ones if other apps already use them.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env 2>/dev/null || true; set +a
PORT="${HQ_PORT:-8765}"
HTTPS_PORT="${HQ_HTTPS_PORT:-443}"
case "$PORT$HTTPS_PORT" in *[!0-9]*) echo "HQ_PORT and HQ_HTTPS_PORT must be numbers."; exit 1;; esac

command -v tailscale >/dev/null || { echo "Tailscale is not installed on this Pi (see README)."; exit 1; }
NAME=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Self",{}).get("DNSName","").rstrip("."))' || true)
[ -n "$NAME" ] || { echo "Tailscale is not connected. Run: sudo tailscale up"; exit 1; }

URL="https://$NAME"
[ "$HTTPS_PORT" = "443" ] || URL="$URL:$HTTPS_PORT"

# The dashboard itself listens only on the Pi; Tailscale serves it as $URL
python3 -c "from engine import envfile; envfile.set_value('HQ_HOST', '127.0.0.1'); envfile.set_value('HQ_URL', '$URL')"
if ! sudo tailscale serve --bg --https="$HTTPS_PORT" "$PORT"; then
  cat <<'MSG'
Tailscale could not serve HTTPS yet. Turn on HTTPS once in the Tailscale admin console:
  https://login.tailscale.com/admin/dns  →  enable MagicDNS and "HTTPS Certificates"
then run this script again.
MSG
  exit 1
fi
bash deploy/restart.sh
sleep 4
echo
echo "Done ($URL → dashboard on port $PORT). The old direct http:// address no longer works."
echo "Open this once on your phone and laptop (Tailscale connected), then add it to the home screen:"
./atlas.sh link
echo "The Talk button now works in Chrome and Safari. Undo: sudo tailscale serve reset"
