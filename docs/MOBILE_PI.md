# Atlas on the Pi and phone

The Pi still uses `/home/user/hq-system`, but its remote is Reasoning Atlas. Do not clone or deploy archived HQ-APP. The existing React screen is served by `atlas-web.service` on loopback port 4310. `atlas-hub.service` remains the authenticated Engine API on the Tailscale address, port 4320. `hq.service` runs the legacy dashboard and Telegram; it is retained for compatibility.

## Open the app

Connect Tailscale on the phone with the Pi owner's account, then open **https://<pi-name>.<tailnet>.ts.net:4310/**. Alternatively open **https://<pi-name>.<tailnet>.ts.net:8840/** and select **Reasoning Atlas**. Use the browser's Add to Home screen for a shortcut. This is the existing Atlas interface, with phone navigation at the bottom. It needs connectivity; offline synchronization (RA-013/RA-031) is not implemented by this deployment.

The screen accepts only the configured owner's Tailscale identity from Serve, with an exact Host and Origin check. It stays bound to loopback. The API token remains in private `runtime/hub.json`; the browser never receives it. Tailscale Serve replaces incoming identity headers: https://tailscale.com/docs/features/tailscale-serve. Do not expose this listener through another proxy or enable Funnel.

## Installation and maintenance

After tests, run `npm run build`, then `PYTHONPATH=. python deploy/install-atlas-web.py`, and `sudo tailscale serve --bg --https=4310 http://127.0.0.1:4310`. The installer reuses the existing Engine credentials and account identity, creates a private runtime configuration, and installs the user service. It also corrects `HQ_HOST` to loopback: Serve already owns the tailnet port 8770, so binding the backend there causes a restart loop. The first `.env` backup stays in `runtime/env.pre-mobile` (private, never commit).

`deploy/restart.sh` builds the screen and restarts its service when Engine updates are installed. A failed build keeps the previous release. Service checks: `systemctl --user status atlas-web atlas-hub hq`. No directory or Engine service rename is required for mobile access.

## Hub curation

`python deploy/curate-pi-hub.py` previews the explicitly identified infrastructure entries and duplicate `systemd:` keys replaced by `system:` keys. `--apply` backs up the registry through SQLite and hides registrations only. No lifecycle actions, container removal, filesystem deletion or application-data changes occur. The candidate Padelslot deployment and its helpers are represented by the actual Padelslot app. Offline HelloAsso and LifeDash are preserved. The Atlas web service is the single Atlas app entry; its API and legacy dashboard are components.

Backup: `/var/lib/pi-app-hub/data/registry-before-RA-066-20261020T150915Z.sqlite3`. For rollback, restore affected registry rows' previous `removed` and `overrides` values from this snapshot, preserving newer records. Disable only `atlas-web.service` and its port 4310 Serve route to remove mobile hosting. The original Engine and app data remain intact.

## Resume in Codex or Claude desktop

Open this repository and paste: “Resume RA-066 from HANDOFF.md and STATE/evidence/RA-066.md. Inspect Git changes and live service state first. Keep the existing mobile screen and curated Hub entries; preserve offline applications and all data. Never deploy HQ-APP.”
