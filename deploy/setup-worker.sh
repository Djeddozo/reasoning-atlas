#!/usr/bin/env bash
# On a device that should build for Atlas (#187), e.g. the PC in WSL2 Ubuntu on Windows 11:
#   bash setup-worker.sh            (from a copy of this file, or deploy/setup-worker.sh in a clone)
# Checks what a worker needs, clones the repository into ~/atlas-worker, stores the hub address, name and key in
# that clone's .env (permissions 600) and installs a user service that builds while the device is on.
# First, on the Pi: bash deploy/add-worker.sh <name> "<device>"  (gives the key; you approve the worker in Atlas).
# Safe to run again. Undo: systemctl --user disable --now atlas-worker.service && rm -rf ~/atlas-worker
set -uo pipefail
REPO="${ATLAS_REPO:-https://github.com/Djeddozo/Unconventional-Reasoning-Atlas.git}"
DIR="${ATLAS_WORKER_DIR:-$HOME/atlas-worker}"
MIN_FREE_GB="${ATLAS_WORKER_MIN_FREE_GB:-10}"
fail=0
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  MISSING  %s\n        → %s\n' "$1" "$2"; fail=1; }

echo "Checking this device:"
grep -qi microsoft /proc/version 2>/dev/null && ok "WSL2 ($(uname -r))" || ok "Linux ($(uname -r)), not WSL: fine too"
command -v git >/dev/null && ok "git $(git --version | cut -d' ' -f3)" || bad "git" "sudo apt install -y git"
if command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  ok "Python $(python3 -c 'import platform; print(platform.python_version())')"
else
  bad "Python 3.11 or newer" "sudo apt install -y python3 (Ubuntu 24.04 has 3.12)"
fi
if command -v claude >/dev/null; then
  if claude -p "Reply with OK" --max-turns 1 >/dev/null 2>&1; then ok "Claude Code, logged in"; else bad "Claude Code login" "run: claude   (log in once with your subscription)"; fi
else
  bad "Claude Code" "curl -fsSL https://claude.ai/install.sh | bash   then: claude"
fi
if command -v gh >/dev/null; then
  gh auth status >/dev/null 2>&1 && ok "GitHub CLI, logged in" || bad "GitHub CLI login" "gh auth login   (needs write access to the repository, to push branches and open PRs)"
else
  bad "GitHub CLI" "sudo apt install -y gh   then: gh auth login"
fi
{ command -v tailscale || command -v tailscale.exe; } >/dev/null && ok "Tailscale" || bad "Tailscale" "install Tailscale on Windows (WSL2 reaches the Pi through it) or in WSL: curl -fsSL https://tailscale.com/install.sh | sh"
free_gb=$(df -Pk "$HOME" | awk 'NR==2 {print int($4/1048576)}')
[ "${free_gb:-0}" -ge "$MIN_FREE_GB" ] && ok "$free_gb GB free" || bad "at least $MIN_FREE_GB GB free disk (now ${free_gb:-?} GB)" "free space in WSL, or set ATLAS_WORKER_MIN_FREE_GB"
systemctl --user status >/dev/null 2>&1 && ok "systemd user services" || bad "systemd in WSL" "add [boot] systemd=true to /etc/wsl.conf, then in Windows: wsl --shutdown, and open Ubuntu again"
[ "$fail" = 0 ] || { echo; echo "Fix the items above and run this script again."; exit 1; }

echo; echo "The hub (from deploy/add-worker.sh on the Pi):"
read -rp "  hub address (http://<Pi's Tailscale address>:4320): " HUB
read -rp "  worker name (e.g. zenunit): " NAME
read -rsp "  key (hidden): " KEY; echo
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,23}$ && ${#KEY} -ge 24 && "$HUB" =~ ^https?:// ]] || { echo "That does not look right; run again."; exit 1; }
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$HUB/v1/health" || true)
[ -n "$code" ] && [ "$code" != "000" ] && ok "the hub answers at $HUB" || { echo "  The hub at $HUB does not answer: is Tailscale up on both sides?"; exit 1; }

if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q --ff-only || true; else git clone -q "$REPO" "$DIR" || exit 1; fi
ok "repository in $DIR"
umask 077
python3 - "$DIR/.env" "$HUB" "$NAME" "$KEY" <<'PY'
import sys
from pathlib import Path
path, hub, name, key = sys.argv[1:]
want = {"ATLAS_HUB_URL": hub, "ATLAS_WORKER_NAME": name, "ATLAS_WORKER_" + "TOKEN": key}
p = Path(path)
lines = [l for l in (p.read_text().splitlines() if p.exists() else []) if l.split("=", 1)[0] not in want]
p.write_text("\n".join(lines + [f"{k}={v}" for k, v in want.items()]) + "\n")
p.chmod(0o600)
PY
ok "hub settings in $DIR/.env (only you can read it)"

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/atlas-worker.service" <<UNIT
[Unit]
Description=Atlas builder worker: claims tasks from the hub on the Pi and builds them (#185)
After=network-online.target

[Service]
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStartPre=/usr/bin/git -C $DIR pull -q --ff-only
ExecStart=/usr/bin/env python3 -m engine worker run
Restart=on-failure
RestartSec=120

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now atlas-worker.service && ok "service atlas-worker.service is running"
echo
echo "Done. It builds once you have approved worker '$NAME' in Atlas and the builder is on."
echo "Watch it: journalctl --user -u atlas-worker -f    Stop it: systemctl --user stop atlas-worker"
echo "To keep it running while Windows is on, see docs/WORKERS.md (start WSL at log-on)."
