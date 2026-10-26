#!/usr/bin/env bash
# Start Atlas's hub on the Pi (RA-030): the Engine's functions for Atlas screens on your other devices,
# over Tailscale only, with a required token. Safe to run again: it keeps the existing token and
# restarts the service. Undo: systemctl --user disable --now atlas-hub.service
set -euo pipefail
cd "$(dirname "$0")/.."
IP=$(tailscale ip -4 2>/dev/null | head -1) || true
[ -n "${IP:-}" ] || { echo "Tailscale is not running on the Pi. Run: sudo tailscale up"; exit 1; }
PORT=4320
TOKEN=$(python3 - "$IP" "$PORT" <<'PY'
import secrets, sys
from engine import envfile
token = envfile.read_value("ATLAS_HUB_TOKEN")
if len(token) < 20:
    token = secrets.token_urlsafe(32)
    envfile.set_value("ATLAS_HUB_TOKEN", token)
envfile.set_value("ATLAS_HUB_HOST", sys.argv[1])   # listen on the Tailscale address only
envfile.set_value("ATLAS_HUB_PORT", sys.argv[2])
print(token)
PY
)
. deploy/systemd-lib.sh
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/atlas-hub.service" <<UNIT
[Unit]
Description=Atlas hub: Engine functions for Atlas screens over Tailscale (token required)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$PWD
ExecStart=/bin/bash -c 'set -a; source .env; set +a; exec python3 -m services.atlas.hub'
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
UMask=0077
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable atlas-hub.service >/dev/null 2>&1
systemctl --user restart atlas-hub.service
sleep 3
if curl -fsS -m 5 -H "Authorization: Bearer $TOKEN" "http://$IP:$PORT/v1/health" >/dev/null; then
  STATE="The hub is running."
else
  STATE="The hub did not answer yet. Check: systemctl --user status atlas-hub.service --no-pager"
fi
NAME=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
echo
echo "$STATE"
echo "In Atlas on your PC, open Systems → Engine hub and paste:"
echo "  Hub address: http://$IP:$PORT"
[ -n "$NAME" ] && echo "  (or by name: http://$NAME:$PORT)"
echo "  Hub token:   $TOKEN"
echo "Your PC must be connected to Tailscale too. The token is also in .env (ATLAS_HUB_TOKEN)."
