# Moving the Pi's checkout to ~/atlas (RA-042)

One name, stage 4: the Engine's folder on the Pi becomes `~/atlas` on branch `main`. `~/hq-system` stays as a link to it,
so anything that still uses the old path keeps working. Everything is reversible.

## Before you start
- Wait until no builder run is in progress (Forge › Build › Live is idle). Pull requests waiting for review are fine.
- The checkout must have no local code changes (`git status`).

## 1. Dry run (changes nothing)
```
python3 ~/hq-system/deploy/move_to_atlas_folder.py --dry-run
```
It lists the folder move, the branch, the units it stops and starts again, and every line it changes in the systemd
user units (`hq.service`, `atlas-hub.service`, `atlas-web.service`, the recovery and builder timers), the crontab
(the `# hq` schedules and the `# hq-devloop` line) and Claude Code's gatekeeper hook (`~/.claude/settings.json`).
It also names `.env` lines and database rows that mention the old folder; those are not changed.

## 2. Move
```
python3 ~/hq-system/deploy/move_to_atlas_folder.py
```
It pauses the dev loop, backs up the database (`memory/hq-before-folder-move-<time>.db`), stops the running units,
switches to `main` if needed, renames the folder (one rename: `memory/`, `.env`, `runtime/` and `.devloop/` move with it),
leaves the link, rewrites the units, crontab, hook and the builder's worktree records, checks that every file in
`memory/` and `.env` is still there, starts the units again and restores the dev loop. If a step fails it rolls back.

## 3. Check
```
cd ~/atlas && ./hq.sh update && ./hq.sh link && systemctl --user status hq atlas-hub atlas-web --no-pager
```
Open the dashboard and Atlas on the phone; the hub health shows under Systems › Engine hub.

## Undo
```
python3 ~/atlas/deploy/move_to_atlas_folder.py --rollback
```

## Removing the old link later
`--status` lists what still uses `~/hq-system`; `--drop-link` removes the link only when that list is empty. It
removes the link itself, never the folder.

## Gatekeeper
Deny rules that name `/hq-system/` (for example the live `config/` and `memory/` rule) also hold for `~/atlas`
(`engine/gatekeeper.py`, `old_names`). The constitution's allow rule for edits names only `/hq-system/`; until the
owner adds `atlas`, edits in `~/atlas` go to Claude Code's own permission mode instead of being allowed by the rule.
