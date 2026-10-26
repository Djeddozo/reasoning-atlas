# Builder workers: building on more than one device

Tasks #185 (hub and workers) and #187 (the PC as a worker).

## How it fits together

- **The hub is the Pi.** It keeps the plan, the queue, the Build next lane, the constitution's limits and the pull requests. It is the only writer to the plan (RA-031, one writer per dataset).
- **A worker builds.** The Pi is the first worker. Other devices can join, for example the PC (ZenUnit) through WSL2. A worker claims one task at a time per slot and gets a **lease**. It sends a heartbeat every minute. When the heartbeats stop for 5 minutes, the lease expires and the task returns to the queue, and the next build resumes from its checkpoint.
- **Slots.** Every worker has 1 slot by default. A second or later slot only takes a task from the **Side by side** batch you accepted (Forge › Build › Queue, #72). That task must share no files with what is being built, and it starts from origin/main, never stacked. The constitution can cap slots with `[devloop] max_slots_per_worker`, and the code never allows more than 4.
- **Safety.** A device can build only after you approve it. It has its own key: in the Pi's `.env` as `ATLAS_WORKER_TOKEN_<NAME>`, and in the worker's own `.env`. The key lets it claim work, renew its lease and report, and nothing else. It never reaches the hub's database. On the worker, Claude Code runs under the same gatekeeper hook, and the work gets the same check (tests, outside-code rule) before it is pushed. Stop and the kill switch reach a remote build on its next heartbeat.

## The PC (ZenUnit) as a worker

1. **WSL2 with Ubuntu** (Windows PowerShell as administrator): `wsl --install -d Ubuntu-24.04`, restart, then open Ubuntu and create the user.
2. **systemd in WSL**: in Ubuntu, `printf '[boot]\nsystemd=true\n' | sudo tee /etc/wsl.conf`, then in PowerShell `wsl --shutdown` and open Ubuntu again.
3. **Tools** in Ubuntu: `sudo apt update && sudo apt install -y git python3 gh curl`, then `curl -fsSL https://claude.ai/install.sh | bash`. Log in with `claude` (your subscription) and `gh auth login` (with write access to the repository).
4. **Tailscale**: already on Windows. WSL2 reaches the Pi through it. Check with `curl -s -o /dev/null -w '%{http_code}\n' http://<Tailscale address of the Pi>:4320/v1/health` (401 is fine: the hub answers).
5. **On the Pi**: `bash deploy/add-worker.sh zenunit "ZenUnit PC (WSL2)"`. It prints the hub address, the name and the key. Approve the worker in Atlas (Today › Needs your input).
6. **On the PC**, in Ubuntu: `curl -fsSL https://raw.githubusercontent.com/Djeddozo/Unconventional-Reasoning-Atlas/main/deploy/setup-worker.sh -o setup-worker.sh && bash setup-worker.sh`. It checks everything above and at least 10 GB of free disk, clones the repository to `~/atlas-worker`, asks for the three values (the key is typed hidden), and starts `atlas-worker.service`.
7. **Keep it running while Windows is on**: WSL stops when no window is open. In PowerShell: `schtasks /create /tn "Atlas worker" /sc onlogon /tr "wsl.exe -d Ubuntu-24.04 --exec /bin/sh -c 'systemctl --user start atlas-worker; sleep infinity'"`.

Watch it: `journalctl --user -u atlas-worker -f` on the PC, or Forge › Build › Live, which names the worker per lane. Stop it: `systemctl --user stop atlas-worker`. Remove it: `./hq.sh worker revoke zenunit` on the Pi.

## Commands

| Where | Command | What it does |
|---|---|---|
| Pi | `./hq.sh worker` | workers, slots in use, last seen |
| Pi | `./hq.sh worker slots 2` | builds at once on the Pi |
| Pi | `bash deploy/add-worker.sh NAME "DEVICE"` | key + registration (asks your approval) |
| Pi | `./hq.sh worker revoke NAME` | the worker can no longer claim work |
| worker | `python3 -m engine worker run [--once]` | the worker loop (the service runs this) |

The worker does not claim work when less than `ATLAS_WORKER_MIN_FREE_GB` (default 5 GB) is free.
