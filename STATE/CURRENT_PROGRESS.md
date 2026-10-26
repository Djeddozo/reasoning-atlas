# Latest checkpoint — RA-082

Per-job model settings (`[models.jobs.chat]`: Opus, thinking, longer answers); Cortex no longer forced to be brief and sees matching tasks beyond the top 40. The owner sets the constitution lines. Next: streaming and a warm session for speed; RA-080. See STATE/evidence/RA-082.md.

# Checkpoint — RA-081

One numbering for choices and proposals; no Yes/No row; ✦ exchanges are saved only when continued in the chat. Next: RA-080 (builder falls back to Codex). See STATE/evidence/RA-081.md.

# Checkpoint — RA-079

✦ decides by itself and shows the kind by colour; shortcuts and every setting under Systems › Settings; Today's number-key approvals removed; Systems › Agents lists coding agents as switchable resources. Next: RA-080, the builder falling back to Codex when Claude is out (needs Codex CLI on the Pi). See STATE/evidence/RA-079.md.

# Checkpoint — RA-078

Quick note is removed; the ✦ button carries all its functions and works offline for local notes. See STATE/evidence/RA-078.md.

# Checkpoint — RA-077

Two reaches: the ✦ button (Quick note, finding with 1–5 and ← →, quick answers with choices, small changes) and the Cortex chat (every function as proposals, choices, a mockup offer only for ideas with a visual side, earlier conversations behind ☰). Reason/File/Auto removed at the owner's choice. See STATE/evidence/RA-077.md.

# Checkpoint — RA-076

Build › Live shows plain, animated steps by default and the real output behind >_ Output. With RA-073 to RA-075 the owner's requests of 2026-10-25 evening are done: mockups made in Claude or ChatGPT, Cortex as one chat, the ✦ Cortex button with 1 · 2 · 3. Open: the owner closes the related tasks in the Pi plan. See STATE/evidence/RA-076.md.

# Checkpoint — RA-075

The ✦ Cortex button (Ctrl+J) works on every screen: it reads what is shown, proposes up to three numbered actions while showing and highlighting the item, and acts only after Yes / 1 / 2 / 3, with Undo. Waiting on you answers by 1 · 2 · 3. Next: simple steps by default on Build › Live. See STATE/evidence/RA-075.md.

# Checkpoint — RA-074

Cortex is one full-window chat (Enter sends, Markdown, copy/edit/retry, drafts, `/` commands, new conversation); mockups are worked on inside the conversation. Next: the speak-to-Cortex button with numbered proposals and 1/2/3 keys, then simple steps on Build › Live. See STATE/evidence/RA-074.md.

# Checkpoint — RA-073

Mockups are made in Claude or ChatGPT, not drawn by Atlas: Cortex › Visual first opens either app with the request filled in and takes the picture back by paste, drop or file; approval still holds the build. Next: the speak-to-Cortex button with numbered proposals, 1/2/3 keys, chat polish, and simple steps on Build › Live. See STATE/evidence/RA-073.md.

# Latest checkpoint — RA-068

The separate "Atlas RA build queue" is gone: every open RA step is an ordinary task in Forge › Plan under the goal "Build Reasoning Atlas", first in the builder's queue, and its status follows STATE/tasks.json automatically (the one-minute watcher and the hourly cycle). The plan was reviewed against what exists: 14 steps closed with reasons (STATE/evidence/RA-068.md). The PC's Update now realigns after GitHub's history rewrite instead of reporting hundreds of waiting changes. The "builder may merge RA steps" switch moved to Builder settings.

# Latest checkpoint — RA-022 (review)

Capabilities are callable through the registry (`services/atlas/capabilities.py`, `/api/capabilities/*`). Reads go through the Engine's hub adapter and carry provenance. Proposals need a preview and one confirmation, and a duplicate is never sent twice. Act capabilities are refused. An offline module fails visibly. Every call is logged and audited. 643 tests pass. The screen still uses `/api/engine` directly; moving sections onto the registry is next. See STATE/evidence/RA-022.md.

# Latest checkpoint — RA-015 (in progress)

Session-switch and usage-cap recovery: `python scripts/resume.py show` rebuilds where a task stopped from the repository alone, and a write-ahead ledger (STATE/effects.json) keeps pushes and other external effects from repeating. Scripted stops before and after a checkpoint pass; the live Claude/Codex drill is next. See STATE/evidence/RA-015.md.

# Latest checkpoint — RA-005

Intake and source provenance done: local files and note texts are kept as originals with SHA-256 dedupe, refused when invalid, and resumed after a crash or failure (Notes › Local sources, `/api/sources`). 620 tests pass. See STATE/evidence/RA-005.md.

# Latest checkpoint — RA-066

Pi screen and Hub cleanup deployed; 588 tests pass. Owner deferred further mobile work and requested Forge RA queue and Claude reset visibility next. See STATE/evidence/RA-066.md and docs/MOBILE_PI.md. Preserve memory/switch-to-atlas.json.

# Current progress — October 20, 2026

Completed: RA-005, RA-054, RA-065, RA-058, RA-051, RA-050, RA-049, RA-048, RA-047, RA-052, RA-064, RA-063, RA-062, RA-046, RA-059, RA-061, RA-060, RA-045, RA-044, RA-043, RA-053, RA-041, RA-040, RA-039, RA-038, RA-037, RA-036, RA-035, RA-030, RA-029, RA-032, RA-033, RA-034, RA-000, RA-001, RA-002, RA-003, RA-008, RA-018, RA-019, RA-020, RA-024, RA-027. In progress: RA-025. In review: RA-004, RA-057 (runs on the Pi, waits for your approval). In progress: RA-015 (live drill next). Ready: RA-021 (after RA-004 review).

Latest checkpoint: RA-004 (review) and RA-019. The visual shell matches the reference geometry within 2 px at 1394 × 855, passes keyboard, offline and 200% zoom checks, and lists known deviations. The app shows live build progress and updates itself when a new release is built, so it can stay open while agents work. Atlas now lists what is waiting on the owner (RA-024), starting with dissecting his other apps. 16 tests pass; blueprint validation passes.

## Owner direction
One central platform instead of loose apps (ADR-013). Apps stay whole as modules; their functions become callable capabilities; a Systems view keeps every capability in sight. Proposal: docs/PLATFORM.md. The Engine domain groups confirmed (VQ-006). Other apps deferred by the owner (VQ-007); Atlas reminds him.

## Module registry
RA-020 describes Atlas, the Engine (L0, five confirmed domains), the Claude app and Codex as modules with capabilities. For each intent the registry says whether work runs in Atlas or must switch to another app (ADR-017). Owner decision pending: how Atlas runs agents itself (VQ-009, in Waiting on you).

## Engine adapter
RA-008: read-only snapshot adapter for the Engine, pinned to HQ commit 1599d42 (now the Engine), tested on a synthetic snapshot (idempotent re-import, explicit unresolved references, incompatible versions rejected). RA-009 (review): the Engine exports a read-only snapshot through the hub (`engine.system.snapshot_export`); Atlas reads it on demand into its read model and shows connected, stale, offline, token refused or version mismatch under Systems › Connections (STATE/evidence/RA-009.md). The Engine already has usage-limit recovery and terminal steps worth reusing (docs/INTEGRATION_ENGINE.md).

## Hub
RA-030 (done): the Pi serves Engine functions over Tailscale with a required token; the PC is connected under Systems → Engine hub. Reads open the Engine's database read-only; actions call the Engine's own functions (docs/HUB.md).

## Owner attention (first Engine cluster in Atlas)
RA-035: Waiting on you shows the Engine's usage-limit stops, terminal steps, decisions, approvals and the owner's own tasks live, and each can be answered or closed from Atlas. 

## Brain (second Engine cluster)
RA-036: Quick note sends a thought to the Engine (default when the hub is online); the reply says what it became and where. Notes → Engine brain lists recent thoughts with their tasks, ideas and goals, and can undo what a thought added. 

## Focus and directions (ADR-023)
RA-040: Focus is about big directions (year or lifetime goals such as Improve Atlas, Create a product, Solve financial issues), shown on their timeline with a recommended focus and its reasons. Choosing one makes the Engine's own ranking put its work first; Forge › Plan inherits the focus and holds the practical side (ranked next steps, where the work leads, build mode).

## Direction (third Engine cluster, practical side now in Forge › Plan)
RA-037: the Focus panel shows the Engine's focus, next step and destination goal (synthetic only when the hub is offline). The Focus page lists the ranked next steps with why each ranks there, where the work leads, goal progress, and switches the build focus (re-ranks at once). Next cluster: Forge (ideas → plan → build, with the dev loop's live lanes).

## Engine functions
RA-034: all 85 Engine (formerly HQ) functions are registered (83 live in the Engine, with test evidence). They are visible on the map but not yet usable from Atlas's screen: next is the hub connection (RA-030), then the sections cluster by cluster (Owner attention and Brain first).

## Map
RA-033: the map follows the chosen focus (functions, areas, programs and what they need from outside); Everything shows all programs and functions with their links, zoomable. Focuses live in STATE/focuses.json.

## Programs, paths and functions
RA-032 (ADR-020, docs/MODEL.md): programs nest (Atlas root; Engine, formerly HQ, and Finance below); every build names its program and path; Build progress groups by path and folds finished work; the Engine's skill tree becomes a view over the function graph. Look: Atlas's; interaction: the Engine's.

## One app with the Engine
The Engine's code and full history are merged into this repository (ADR-019, RA-029); 500 tests pass together. The Pi switched on 2026-10-20 and now installs Atlas's main through the Engine's updater (tests first). Next the Pi becomes the authenticated hub (RA-030) and every device a local screen (RA-031). docs/ENGINE_FUNCTION_MAP.md places every Engine function by cluster (RA-028, in review).

## Resume building from Atlas
The header has a reload button and an update marker with one-click restart (RA-027). Build progress has a Continue building panel that hands the active, paused or next task to the Claude app or Codex with a full handoff text and logs the switch (RA-025, first slice).

## Owner priority
Visual app first, so the owner can follow component construction inside the app. Unbuilt components remain visible, dimmed and explicitly labeled. Never present synthetic research content as implemented capabilities.

## Forge
RA-039: Forge is in the navigation (Ideas → Plan → Build). Ideas shows every open Engine idea with where it fits and adds it to the plan under a goal, starts a new goal, or sets it aside; Claude can place them all. Plan and Build follow.

## Updates
RA-038 (ADR-022): one Update button in the header updates this computer (fast-forward, restart through Start Atlas.cmd) and the Pi (the Engine's updater, tests first, progress shown). Other screens follow the Pi.

## Structure (ADR-024, docs/STRUCTURE.md)
Top-down: Focus is the vision page (lifetime visions, the focus among them, year objectives); the Forge inherits the focus and is practical (Ideas → Plan → Build controls); Build progress is the visual live view. Every former HQ function has one home; HQ name disappears in four stages (RA-053, RA-054, RA-055, RA-042).

## Former HQ functions in Atlas (2026-10-20)
Every Engine function the old dashboard offered now has a home in Atlas, each judged first (evidence RA-047 to RA-052, RA-056): Map › Skill tree, Library › Research, Resources (with the arsenal), Memory (with the change log and undo), Systems (kill switch, use, budgets, models, self-maintenance, repairs, Claude sign-in, voice, constitution read-only). Talking to Atlas is one mode (RA-058): questions are answered from the plan, everything else is sorted top-down into visions, objectives, goals, tasks, ideas, skills or memory, with where it went and Undo; Telegram follows the same rule. After a change to Atlas's structure or mission, the Engine re-checks every open task, goal and idea once and puts one diff on Today for approval (RA-057).

## Next exact action
1. Owner: install the update on the Pi (Update button), then approve or refuse the plan re-evaluation that appears on Today (RA-057).
2. RA-031: Atlas's screen on every device, the phone first (it still uses the old dashboard); needs a sign-in for the Pi-served screen.
3. RA-056: switch the old dashboard off, with the owner's approval, once the phone has Atlas.
4. One name, stages 3–4 (stage 2, RA-054 documents and agents, done 2026-10-21): RA-055 (code and commands, with aliases; in review 2026-10-23: engine/ and atlas.sh), RA-042 (the Pi's folder, branch and services).
5. Then RA-021, RA-015, RA-023 and RA-025 as before.
