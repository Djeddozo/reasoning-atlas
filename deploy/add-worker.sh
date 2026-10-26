#!/usr/bin/env bash
# On the Pi (the hub): let another device build for Atlas (#185, #187). Usage: bash deploy/add-worker.sh zenunit "ZenUnit PC (WSL2)" [slots]
# Creates the worker's own key in the Pi's .env (ATLAS_WORKER_TOKEN_<NAME>, never in the database), registers the
# worker, which asks your approval in Atlas, and prints the key once so you can give it to setup-worker.sh on the
# device. Safe to run again: an existing key is kept. Undo: ./atlas.sh worker revoke <name>
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:-}"; DEVICE="${2:-$NAME}"; SLOTS="${3:-1}"
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,23}$ ]] || { echo "Usage: bash deploy/add-worker.sh <name, e.g. zenunit> \"<device>\" [slots]"; exit 1; }
KEY=$(python3 - "$NAME" <<'PY'
import secrets, sys
from engine import envfile
var = "ATLAS_WORKER_TOKEN_" + sys.argv[1].upper().replace("-", "_")
key = envfile.read_value(var)
if len(key) < 24:
    key = secrets.token_urlsafe(32)
    envfile.set_value(var, key)
print(key)
PY
)
./atlas.sh worker add "$NAME" --device "$DEVICE" --slots "$SLOTS" || true   # already registered: keep it
HOST=$(python3 -c "from engine import envfile; print(envfile.read_value('ATLAS_HUB_HOST') or '')")
PORT=$(python3 -c "from engine import envfile; print(envfile.read_value('ATLAS_HUB_PORT') or '4320')")
echo
echo "Worker '$NAME' registered. Approve it in Atlas (Today › Needs your input) before it can build."
echo "On the device, run deploy/setup-worker.sh and give it:"
echo "  hub address: http://${HOST:-<Tailscale address of the Pi>}:$PORT"
echo "  name:        $NAME"
echo "  key:         $KEY"
echo "(The key is shown only here; run this script again to see it.)"
