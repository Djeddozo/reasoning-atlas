# Switching the Pi from HQ-APP to Atlas

After this switch the Pi runs the merged Atlas repository (ADR-019): the same engine, now updated from Atlas's `main`. Your data stays in the same `memory/hq.db`; only where the code comes from changes. Everything is reversible.

## Before you start
- Open Termius and connect to the Pi, then go to the Engine folder (the one with `hq.sh`).
- Let any dev-loop work finish: no task may be building or waiting as a pull request on HQ-APP. Merge or close those pull requests first. If HQ-APP gets new commits, ask an agent to merge the Engine into Atlas again before switching.

## 1. Check (changes nothing)
Copy this as one line. It downloads the switch script from Atlas and only checks:

```
git fetch https://github.com/Djeddozo/Unconventional-Reasoning-Atlas.git main && git show FETCH_HEAD:deploy/switch_to_atlas.py > /tmp/switch_to_atlas.py && python3 /tmp/switch_to_atlas.py --check
```

Expected: "Checks passed. Atlas's main is N commit(s) ahead of this Pi's Atlas …". Otherwise it lists what to fix first.

## 2. Switch
```
python3 /tmp/switch_to_atlas.py
```
In order, it:
1. pauses the dev loop, remembering its exact setting;
2. backs up the database (`memory/hq-before-atlas-<time>.db`) and keeps the old commit as branch `hq-app-final-<time>`;
3. points the checkout at Atlas;
4. archives HQ-APP's pull-request records (table `prs_hq_app`), because pull-request numbers restart in the new repository;
5. installs Atlas's `main` through the Engine's own updater, which first runs the full test suite on the Pi;
6. restores the dev loop setting and restarts the dashboard and bot.

If any step fails, it rolls itself back and says why.

## 3. Afterwards
- The Engine's dashboard, Telegram bot, schedules and dev loop keep working as before. Updates now come from Atlas.
- Archive HQ-APP on GitHub (Settings → Archive this repository) once you are satisfied; it stays readable.

## Undo
```
python3 deploy/switch_to_atlas.py --rollback
```
This returns the remote, the code, the pull-request records and the dev loop setting to how they were. The database backup is kept.

## Tested
Simulated in an agent workspace on a copy of HQ-APP at 1599d42: check, switch (the Engine's updater ran all 500 tests and installed the merge), and rollback. Not yet run on the real Pi: step 1 confirms its Python version (3.11 or newer), GitHub login and the absence of work in flight.
