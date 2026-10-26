#!/usr/bin/env bash
# Make the Atlas dashboard reachable on your phone through Tailscale (private; not on the internet).
# Run after `sudo tailscale up` has succeeded on the Pi.
set -euo pipefail
cd "$(dirname "$0")/.."
IP=$(tailscale ip -4 2>/dev/null | head -1) || true
[ -n "${IP:-}" ] || { echo "Tailscale is not running. Run: sudo tailscale up"; exit 1; }
if grep -q '^HQ_HOST=' .env; then sed -i "s|^HQ_HOST=.*|HQ_HOST=$IP|" .env; else echo "HQ_HOST=$IP" >> .env; fi
./atlas.sh link >/dev/null   # creates HQ_TOKEN if .env has none
set -a; . ./.env; set +a
pkill -f "python3 -m (hq|engine) serve" 2>/dev/null || true
sleep 1
nohup ./atlas.sh serve >> memory/serve.log 2>&1 &
sleep 1
echo
echo "Open this on your phone (with the Tailscale app connected), then add it to your home screen:"
echo "  http://$IP:8765/?t=$HQ_TOKEN"
