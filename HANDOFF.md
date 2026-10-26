# Latest checkpoint — RA-068

The separate "Atlas RA build queue" is gone: every open RA step is an ordinary task in Forge › Plan under the goal "Build Reasoning Atlas", first in the builder's queue, and its status follows STATE/tasks.json automatically (the one-minute watcher and the hourly cycle). The plan was reviewed against what exists: 14 steps closed with reasons (STATE/evidence/RA-068.md). The PC's Update now realigns after GitHub's history rewrite instead of reporting hundreds of waiting changes. The "builder may merge RA steps" switch moved to Builder settings.

# Latest checkpoint — RA-055 (review)

The Engine package is `engine/` and its command `atlas.sh`; `hq/` and `hq.sh` are thin aliases (same module objects). Deploy scripts, jobs and imports use the new names; process checks match both; the gatekeeper's old deny rules also hold for the new names. 656 tests pass. Open: the Pi's updater and dev loop on the new names after merge, and the owner's constitution allow rule for `./atlas.sh`. See STATE/evidence/RA-055.md.

# Latest checkpoint — RA-022 (review)

Capabilities are callable through the registry (`services/atlas/capabilities.py`, `/api/capabilities/*`). Reads go through the Engine's hub adapter and carry provenance. Proposals need a preview and one confirmation, and a duplicate is never sent twice. Act capabilities are refused. An offline module fails visibly. Every call is logged and audited. 643 tests pass. The screen still uses `/api/engine` directly; moving sections onto the registry is next. See STATE/evidence/RA-022.md.

# Latest checkpoint — RA-015

Resume from the repository alone: run `python scripts/resume.py show` first in any new session. Wrap external effects with `effect begin KEY … && <effect> && effect finish KEY done`. Scripted drill passes; next is the live Claude/Codex drill (STATE/evidence/RA-015.md, Exact next action).

# Latest checkpoint — RA-005

Intake and source provenance done: local files and note texts are kept as originals with SHA-256 dedupe, refused when invalid, and resumed after a crash or failure (Notes › Local sources, `/api/sources`). 620 tests pass. See STATE/evidence/RA-005.md.

# Latest checkpoint — RA-066

Pi screen and Hub cleanup deployed; 588 tests pass. Owner deferred further mobile work and requested Forge RA queue and Claude reset visibility next. See STATE/evidence/RA-066.md and docs/MOBILE_PI.md. Preserve memory/switch-to-atlas.json.

# Resume Reasoning Atlas

Work in Djeddozo/Unconventional-Reasoning-Atlas on main (feature branches from main). The repository now also contains the Engine (engine/ and atlas.sh, old names hq/ and hq.sh as aliases; config/, deploy/): engine work follows the engine section of CLAUDE.md. Read AGENTS.md, VISION.md, STATE/CURRENT_PROGRESS.md, TASKS.md, DECISIONS.md and docs/PLATFORM.md. Verify remote and preserve changes.

Latest checkpoints: RA-019 done (live build progress, safe rebuilds) and RA-004 in review (visual shell verified against the reference; see STATE/evidence/RA-004.md). Start the app by double-clicking `Start Atlas.cmd`; it can stay open while you build.

Also done: RA-024 (Waiting on you reminders) and RA-020 (module manifests in modules/, registry and routing in packages/contracts/modules.py). The owner can also start you from Atlas (Build progress > Continue building), which copies a handoff text naming the task. Also done: RA-008 (the Engine snapshot adapter, adapters/hq/snapshot.py; see docs/INTEGRATION_ENGINE.md), RA-027 (reload, update marker, one-click restart); RA-025 in progress.

Next: RA-021 Systems view (after the owner reviews RA-004) or RA-015. Do not ask about the other apps or VQ-009; Atlas reminds the owner.

Report progress with `python scripts/progress.py` so the owner sees it in the app. Keep English throughout. Preserve the original reference PNG. The Engine is read through a read-only snapshot export over the hub (RA-009, in review); no writes.

Run `python -m unittest discover -s tests -v` (500 tests, the Engine and Atlas), `python scripts/validate_blueprint.py --require-reference` and `npm run build`. Save task evidence and update all state files at each checkpoint; commit and push to GitHub every checkpoint (ADR-015). Add owner questions with `python scripts/remind.py`.
