# The hub: Atlas on every device, the Engine on the Pi

The Pi owns the data and runs the always-on Engine (ADR-019). The **hub** is Atlas's service on the Pi that lets Atlas screens on your other devices use the Engine's functions, over Tailscale only.

## Set it up (once)
1. On the Pi (Termius), in the Atlas folder (`~/hq-system`):
   ```
   bash deploy/atlas-hub.sh
   ```
   It creates a hub token in `.env` (kept if it exists), starts the service `atlas-hub.service` on the Pi's Tailscale address (port 4320) and prints the address and the token.
2. On your PC, make sure Tailscale is connected. In Atlas open **Systems → Engine hub**, paste the address and the token, and press **Connect**. The header then shows **Hub · N ms**.

The token stays in `runtime/hub.json` on the PC (ignored by Git) and is never sent to the browser. Running the script again keeps the same token and restarts the hub. After every update the Engine's restart script restarts the hub too, so it always runs the current code.

## What the hub does now
- Serves nine Engine functions to read (including recent thoughts and what each became, and where the plan leads; that one runs on an in-memory copy because the skill tree plants missing nodes as it reads): what needs you, terminal steps, work stopped by a usage limit, ranked next steps, current focus, usage, and memory recall (`viaHub` in modules/engine.json).
- Opens the Engine's database read-only at the SQLite level for those reads: no read can change your data.
- Offers six actions. Setting the build focus (RA-037) re-ranks the plan at once. Writing a thought and undoing what a thought added (RA-036) are used by Quick note and Notes → Engine brain; a thought is saved before Claude sorts it, so nothing is lost when no model is reachable. Three more (RA-035) are used by Waiting on you: answer a need (decision, approval or your own task), finish or dismiss a terminal step, and finish or dismiss work stopped by a usage limit. Each calls the Engine function the Engine's own dashboard calls for the same button, so logging (as you, source "atlas"), decision memories and re-planning work unchanged. Atlas never writes the Engine's database itself.
- Requires the token on every request, waits half a second after a wrong one, refuses to start without a strong token or on all interfaces.
- Writing (answering needs, writing to the brain, starting builds) comes next, through the Engine's own approval rules.

## Troubleshooting
- "Hub offline": the Pi is off, Tailscale is disconnected on one device, or the service stopped: `systemctl --user status atlas-hub.service --no-pager` on the Pi.
- "The hub refused the token": paste the token again (`grep ATLAS_HUB_TOKEN .env` on the Pi).
- Stop the hub: `systemctl --user disable --now atlas-hub.service`.
