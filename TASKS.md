# Testable tasks

Machine-readable source: [STATE/tasks.json](STATE/tasks.json). Done refers only to the individual task, not the whole application. RA-000 records the blueprint; RA-001 records intake of the real reference; implementation remains pending.

Every task belongs to one program and one path (STATE/tasks.json `program`, `path`; paths listed under `paths`; docs/MODEL.md). Record owner/session/branch in active-task.json when claiming work. Dependencies must be done; ready is a snapshot of current eligibility. Split large tasks into smaller numbered subtasks before execution, each with its own evidence and checkpoint. A checkbox alone is not completion evidence. Keep every new artifact and UI/code string in English.

## RA-000 — Record the blueprint and repository
Status: **done**. Dependencies: none. Owner: unassigned.

Acceptance: All document links and dependencies valid; artifacts published to the verified target; limitations explicit.

Validation: Blueprint validator and remote readback

Delivery evidence: Documents and commit links. Record commit, environment, results and limitations in STATE/evidence/RA-000.md.

## RA-001 — Preserve and inspect the original mockup
Status: **done**. Dependencies: none. Owner: unassigned.

Acceptance: Owner-supplied PNG preserved unchanged; SHA-256/dimensions recorded; visually inspected; regions and samples documented.

Validation: Strict reference validator, hash comparison and visual inspection

Delivery evidence: Manifest, measurements and RA-001 evidence. Record commit, environment, results and limitations in STATE/evidence/RA-001.md.

## RA-002 — Define data contracts and fixtures
Status: **done**. Dependencies: RA-000. Owner: unassigned.

Acceptance: Validate DATA_MODEL entities; fixture with 200 nodes/400 edges; cover duplicate imports and dependency cycles.

Validation: Schema/invariant tests including invalid fixtures

Delivery evidence: Schemas and tests. Record commit, environment, results and limitations in STATE/evidence/RA-002.md.

## RA-003 — Create development skeleton and local storage
Status: **done**. Dependencies: RA-002. Owner: Codex + Claude.

Acceptance: Lockfiles, documented startup and health endpoint; saved note survives restart; no live Engine dependency.

Validation: Fresh checkout startup and storage crash/restart test

Delivery evidence: Working local vertical slice. Record commit, environment, results and limitations in STATE/evidence/RA-003.md.

## RA-004 — Reproduce the reference-based visual shell
Status: **done**. Dependencies: RA-001, RA-003. Owner: Codex + Claude.

Acceptance: Reference composition, tokens, graph and research roadmap reproduced; loading/empty/offline states; keyboard and 200% zoom usable.

Validation: Fixed-fixture screenshot comparison and manual accessibility check

Delivery evidence: Screenshots, measured deviations and tuned tokens. Record commit, environment, results and limitations in STATE/evidence/RA-004.md.

## RA-005 — Implement intake and source provenance
Status: **done**. Dependencies: RA-003. Owner: Claude.

Acceptance: Persist notes and local sources; hash dedupe; retain original input; resume failed processing.

Validation: Duplicate import, restart and invalid source tests

Delivery evidence: Import tests and fixtures. Record commit, environment, results and limitations in STATE/evidence/RA-005.md.

## RA-006 — Implement map, search and inspector
Status: **done**. Dependencies: RA-004, RA-005. Owner: unassigned.

Acceptance: Selection, relationships, source passages, filters and camera state work; textual alternative available.

Validation: 200-node keyboard/graph scenario and search measurement

Delivery evidence: End-to-end evidence. Record commit, environment, results and limitations in STATE/evidence/RA-006.md.

## RA-007 — Implement skilltree and capability graph
Status: **done**. Dependencies: RA-002, RA-006. Owner: unassigned.

Acceptance: Shared skills across projects; typed GPU/language resources; reject dependency cycles; separate proof and availability.

Validation: Cycle, evidence retraction and cross-project tests

Delivery evidence: Graph fixtures and results. Record commit, environment, results and limitations in STATE/evidence/RA-007.md.

## RA-008 — Implement the Engine snapshot adapter
Status: **done**. Dependencies: RA-002. Owner: Claude.

Acceptance: Pinned synthetic Engine fixture normalizes IDs/proof/status; repeated snapshot does not change counts; no live writes.

Validation: Contract tests for missing fields and unknown versions

Delivery evidence: Adapter and synthetic fixtures. Record commit, environment, results and limitations in STATE/evidence/RA-008.md.

## RA-009 — Connect Engine through a proven read-only boundary
Status: **done**. Dependencies: RA-003, RA-008. Owner: Claude. Evidence: STATE/evidence/RA-009.md.

Acceptance: Configured endpoint/auth; read-only export demonstrated; offline/stale/schema mismatch visible; no rescore endpoint treated as pure read.

Validation: Test-instance audit/database comparison; unauthorized/offline/upgrade cases

Delivery evidence: Read-only integration evidence. Record commit, environment, results and limitations in STATE/evidence/RA-009.md.

## RA-010 — Submit a build proposal from Atlas
Status: **done**. Dependencies: RA-007, RA-009. Owner: unassigned.

Acceptance: Preview, explicit submission, external ID and duplicate reconciliation; enable writes only with upstream dedupe/correlation support.

Validation: Crash before/after external action and ledger save; repeated command ID

Delivery evidence: One task per command with failure evidence. Record commit, environment, results and limitations in STATE/evidence/RA-010.md.

## RA-011 — Track build delivery and capability evidence
Status: **done**. Dependencies: RA-010. Owner: unassigned.

Acceptance: Run/PR/merge/install states distinct; log cursor resumes; evidence attaches to correct capability.

Validation: Connection interruption; merged-but-not-installed PR

Delivery evidence: Status matrix and end-to-end checks. Record commit, environment, results and limitations in STATE/evidence/RA-011.md.

## RA-012 — Implement memory and AI proposals
Status: **done**. Dependencies: RA-005, RA-007. Owner: unassigned.

Acceptance: Versioned corrections, citations and review; reject invented sources; limits do not prevent local input.

Validation: Conflicting-source, prompt-injection and provider-failure scenarios

Delivery evidence: Memory tests and AI evaluations. Record commit, environment, results and limitations in STATE/evidence/RA-012.md.

## RA-013 — Implement Windows–Pi offline synchronization
Status: **review**. Dependencies: RA-003, RA-009. Owner: Claude (dev loop task #114). First slice: notes; evidence STATE/evidence/RA-013.md.

Acceptance: Single authority, durable outbox, revisions and tombstones; concurrent edits produce visible conflicts; client has no upstream credentials.

Validation: Disconnect during acknowledgment; restart; conflicting edits

Delivery evidence: Sync and recovery tests. Record commit, environment, results and limitations in STATE/evidence/RA-013.md.

## RA-014 — Implement Pi deployment and backup
Status: **todo**. Dependencies: RA-013. Owner: unassigned.

Acceptance: Separate port/service/data; ARM64 startup; consistent backup; rollback with compatible schema.

Validation: Restore into empty fixture environment and health check

Delivery evidence: Installation and recovery report. Record commit, environment, results and limitations in STATE/evidence/RA-014.md.

## RA-015 — Prove session-switch and usage-cap recovery
Status: **in_progress**. Dependencies: RA-003. Owner: Claude.

Acceptance: Interrupt a task; a fresh Codex/Claude session resumes using only the repository; no duplicate external effects.

Validation: Controlled stop before and after checkpoint

Delivery evidence: STATE/evidence/RA-015.md. Record commit, environment, results and limitations in STATE/evidence/RA-015.md.

## RA-016 — Complete release acceptance
Status: **todo**. Dependencies: RA-011, RA-012, RA-014, RA-015, RA-017. Owner: unassigned.

Acceptance: All functional/visual gates passed; limitations recorded; export/restore succeeds; English UI/code/docs audit passes.

Validation: Full TESTING matrix and release review

Delivery evidence: Release checklist and pinned commit. Record commit, environment, results and limitations in STATE/evidence/RA-016.md.

## RA-017 — Implement the in-app Vision Room
Status: **todo**. Dependencies: RA-003, RA-012. Owner: unassigned.

Acceptance: Vision and conversation together; persistent questions; answers cite vision revision; changes require diff review; offline pending and export work.

Validation: Restart during question; provider limit; reject proposed vision edit

Delivery evidence: Conversation and revision tests. Record commit, environment, results and limitations in STATE/evidence/RA-017.md.

## RA-018 — Publish separate vision page and questions space
Status: **done**. Dependencies: RA-000. Owner: unassigned.

Acceptance: Standalone readable VISION.md; persistent GitHub question issue; no false promise of automatic answers; English throughout.

Validation: Link check and issue readback

Delivery evidence: Vision page and issue #1. Record commit, environment, results and limitations in STATE/evidence/RA-018.md.

## RA-019 — Show live build progress and update the open app safely
Status: **done**. Dependencies: RA-003. Owner: Claude.

Acceptance: Agents report task steps; the app shows a progress bar and step list; builds go to new release folders and switch atomically; open windows reload by themselves and keep the note draft; a silent builder shows as paused.

Validation: Browser test: start a task, rebuild while the page is open, confirm reload with retained draft, done and stalled states; unit tests for progress and release switching

Delivery evidence: Screenshots and tests. Record commit, environment, results and limitations in STATE/evidence/RA-019.md.

## RA-020 — Define module manifests and the capability registry
Status: **done**. Dependencies: RA-002. Owner: Claude.

Acceptance: JSON schema for module manifests and capabilities (id, effect, status, home, schemas, evidence); registry rejects duplicate IDs, unknown effects and live status without evidence; hand-written L0 manifest for the Engine using the confirmed domain groups.

Validation: Contract tests with valid, duplicate and invalid manifests

Delivery evidence: Schemas, the Engine manifest and tests. Record commit, environment, results and limitations in STATE/evidence/RA-020.md.

## RA-021 — Build the Systems view
Status: **done**. Dependencies: RA-004, RA-020. Owner: unassigned.

Acceptance: Modules grouped by domain with their capabilities, status, integration level and evidence; usage lines to Atlas sections; capability detail; keyboard and list alternative.

Validation: Fixture with three modules; keyboard selection; 200% zoom; screenshot review by the owner

Delivery evidence: Screenshots and interaction tests. Record commit, environment, results and limitations in STATE/evidence/RA-021.md.

## RA-022 — Call capabilities through the registry
Status: **done**. Dependencies: RA-008, RA-020. Owner: Claude.

Acceptance: Read capabilities run through the adapter with provenance on every result; propose capabilities require preview and confirmation; act capabilities stay disabled; offline modules fail visibly.

Validation: Synthetic Engine snapshot; offline module; duplicate propose; provenance audit

Delivery evidence: Call log and tests. Record commit, environment, results and limitations in STATE/evidence/RA-022.md.

## RA-023 — Inventory other apps as platform modules
Status: **todo**. Dependencies: RA-020. Owner: unassigned.

Acceptance: Owner-confirmed purpose, domain and authority for each app; L0 manifest per app; overlaps such as LifeDashV5 and the Engine Direction resolved to one authority.

Validation: Owner review of each manifest

Delivery evidence: Manifests and owner decisions. Record commit, environment, results and limitations in STATE/evidence/RA-023.md.

## RA-024 — Remind the owner of open items inside Atlas
Status: **done**. Dependencies: RA-003. Owner: Claude.

Acceptance: 'Waiting on you' list combining agent reminders (versioned in STATE/owner-reminders.json) and the owner's own reminders (local SQLite); count in the header; tick and untick persist; Quick note can add a reminder; Systems shows apps still to dissect; existing notes survive the database upgrade.

Validation: Unit and HTTP tests including upgrade of a v1 database; browser run: add, tick, reload

Delivery evidence: Tests and screenshots. Record commit, environment, results and limitations in STATE/evidence/RA-024.md.

## RA-025 — Switch between Atlas, the Claude app and Codex without losing context
Status: **in_progress**. Dependencies: RA-015, RA-020. Owner: Claude.

Acceptance: When an intent has no live capability in the platform, Atlas explains why, names the app that can do it, prepares a handoff packet (task, objective, done so far, files, exact next action), copies it and opens the app; returning work appears through pushed checkpoints and progress; every switch is logged by intent.

Validation: Route web.research and code.edit to a tool; resume in a fresh Claude and Codex session from the packet alone; switch log counts

Delivery evidence: Handoff packets, switch log and session transcripts. Record commit, environment, results and limitations in STATE/evidence/RA-025.md.


## RA-026 — Work on projects from inside Atlas (workbench)
Status: **todo**. Dependencies: RA-022, RA-025. Owner: unassigned.

Acceptance: The owner gives an instruction inside Atlas; the chosen workbench (VQ-009) works on a branch, reports progress, commits and pushes; work it cannot do is routed through RA-025; usage limits leave a pushed checkpoint and a reminder.

Validation: Complete a small repository task from Atlas alone; force a usage limit mid-task and resume elsewhere

Delivery evidence: End-to-end run with commits and progress log. Record commit, environment, results and limitations in STATE/evidence/RA-026.md.

## RA-027 — Reload button and update marker with one-click restart
Status: **done**. Dependencies: RA-019. Owner: Claude.

Acceptance: A reload button in the header; a marker when service code or unbuilt screen code is newer than the running app; one click rebuilds and restarts through the launcher and reloads the page with drafts intact; without the launcher the marker explains how to restart.

Validation: Unit tests for change detection and restart exit code; browser run with a stand-in launcher: change code, marker, restart, reload

Delivery evidence: Tests and screenshots. Record commit, environment, results and limitations in STATE/evidence/RA-027.md.

## RA-028 — Map every Engine function into Atlas by cluster
Status: **done**. Dependencies: RA-020. Owner: Claude.

Acceptance: Every Engine module, table, dashboard action and command assigned to one cluster; each cluster has one Atlas home and stays whole; navigation changes and integration order proposed; owner accepts the placement and the merge direction.

Validation: Full read of HQ-APP at the pinned commit; owner review

Delivery evidence: docs/ENGINE_FUNCTION_MAP.md. Record commit, environment, results and limitations in STATE/evidence/RA-028.md.

## RA-029 — Merge Engine into the Atlas repository
Status: **done**. Dependencies: RA-003. Owner: Claude.

Acceptance: HQ-APP's full history merged; the Engine keeps its root layout; both test suites pass together; a reversible Pi switchover that pauses the dev loop, backs up the database, archives pull-request records, installs through the Engine's updater and restores settings; the Pi runs Atlas's main.

Validation: Combined test run; simulated switch and rollback on a copy of the Engine at the Pi commit; owner runs the switch on the Pi

Delivery evidence: Merge commit, test run, simulation log, Pi switch output. Record commit, environment, results and limitations in STATE/evidence/RA-029.md.


## RA-030 — Make the Pi the authenticated hub over Tailscale
Status: **done**. Dependencies: RA-029. Owner: Claude (owner runs the Pi step).

Acceptance: The hub serves Atlas's API on the tailnet only with a required token (the Engine's 'no token, no check' closed); health shows version and adapter state; no public exposure.

Validation: Requests without or with a wrong token are refused; reachable from a second device on Tailscale; not reachable from outside

Delivery evidence: Access tests and configuration notes. Record commit, environment, results and limitations in STATE/evidence/RA-030.md.


## RA-031 — Local screens on every device talk to the hub
Status: **todo**. Dependencies: RA-013, RA-030. Owner: unassigned.

Acceptance: Each device runs the local Atlas screen against the hub; reads are cached and marked stale when the hub is unreachable; new notes queue in an outbox and send once; one writer per dataset.

Validation: Two devices; hub offline during a save; restart; no duplicates

Delivery evidence: Multi-device test log. Record commit, environment, results and limitations in STATE/evidence/RA-031.md.

## RA-032 — Programs, paths and functions
Status: **done**. Dependencies: RA-020. Owner: Claude.

Acceptance: Programs nest (Atlas root, Engine and Finance below; Atlas can move under a bigger program); every build task names its program and path; the build view groups by program and path and folds finished work; the Engine skill-tree nodes mapped to functions or destinations.

Validation: Registry nesting tests; task program/path validation; browser check of the build view

Delivery evidence: docs/MODEL.md, tests and screenshots. Record commit, environment, results and limitations in STATE/evidence/RA-032.md.

## RA-033 — Focus-driven skill map with a zoomable view of everything
Status: **done**. Dependencies: RA-020, RA-032. Owner: Claude.

Acceptance: The map follows the chosen focus and shows its functions, areas and programs, including functions it needs from outside the focus; functions can belong to several focuses; "Everything" shows all programs, areas and functions with containment, dependency and same-work links, zoomable and pannable; unbuilt functions dimmed; details on selection; list alternative; the concept map stays available for focuses that have one.

Validation: Graph and focus tests; browser run through every focus, Everything, zoom, selection, list view and focus memory

Delivery evidence: Tests and screenshots. Record commit, environment, results and limitations in STATE/evidence/RA-033.md.

## RA-034 — Register every Engine (formerly HQ) function
Status: **done**. Dependencies: RA-033. Owner: Claude.

Acceptance: Every user-facing Engine function registered with its area, effect, source file and dependencies; live only where an Engine test exercises it; where it can be used today and its home section in Atlas; the map distinguishes live in the Engine from reachable on Atlas's screen; routing reports live-but-unreachable functions separately.

Validation: Registry validation of every evidence path; routing and manifest tests; browser check of the Engine focus, Everything and a selection

Delivery evidence: modules/engine.json, tests and screenshots. Record commit, environment, results and limitations in STATE/evidence/RA-034.md.

## RA-066 — Pi mobile access and Hub curation
Status: **done**. Program: atlas; path: foundation. Existing screen deployed privately; Hub duplicates hidden without data deletion. 588 tests pass. Owner deferred remaining mobile verification. Evidence: STATE/evidence/RA-066.md.

## RA-067 — Individual RA queue and builder waiting state
Status: **done**. Program: atlas; path: forge. Preserve #103 history, map unfinished RA steps to individual Engine tasks, enforce dependencies and owner holds, report Claude's reset and expose settings with a gear. Evidence: STATE/evidence/RA-067.md.

## RA-068 — RA steps live in the plan; statuses follow the repository
Status: **done**. Program: atlas; path: forge. The separate "Atlas RA build queue" is removed; every open RA step is an ordinary plan task under the goal "Build Reasoning Atlas", synced automatically from STATE/tasks.json (done there closes it here). Plan statuses reviewed against what exists. The PC's Update realigns after GitHub's history rewrite. Evidence: STATE/evidence/RA-068.md.

## RA-069 — Voice on the PC's hardware; hardware as live resources; chat thread
Status: **done**. Program: atlas; path: engine. The Pi CPU speech tier is removed; Atlas on the PC transcribes with Whisper on the PC (GPU when usable), set up under Systems › Voice; the PC and the Pi report themselves as live resources (Resources › Connected) while written resources stay editable; Quick note shows the conversation with Atlas. Evidence: STATE/evidence/RA-069.md.

## RA-070 — Pick and order what the builder builds; Brain screen
Status: **done**. Program: atlas; path: forge. Build on every open plan task (⚠ with the reason while it waits on another task) places it fifth in the builder's queue; Up next shows 5 or 10 and can be dragged; "All" opens the whole build order in its own window; Brain is a screen under Forge for reasoning with the system's brain, with a richer context. Evidence: STATE/evidence/RA-070.md.

## RA-071 — Vision → Goal → Task, deadlines instead of time layers
Status: **done**. Program: atlas; path: vision. The year, quarter, month and week layers are gone; a goal may carry a deadline the owner sets in Focus, and only that deadline (with the vision in focus) raises its tasks' priority. Evidence: STATE/evidence/RA-071.md.

## RA-072 — Cortex: the reasoning core's name, with a nickname
Status: **done**. Program: atlas; path: engine. Brain becomes Cortex in Atlas: the screen under Forge, Notes, Quick note, Waiting on you and the function map's area name; on the first visit the owner may give it a nickname, shown everywhere and answered to; ids keep `brain`. Evidence: STATE/evidence/RA-072.md.

## RA-073 — Mockups made in Claude or ChatGPT, shown in Atlas
Status: **done**. Program: atlas; path: engine. Atlas writes the mockup request and opens Claude or ChatGPT with it; the picture (or HTML) comes back by paste, drop or file as a round; approval holds and releases the build as before. Evidence: STATE/evidence/RA-073.md.

## RA-074 — Cortex as one full-window chat
Status: **done**. Program: atlas; path: apps/web. Cortex is a single chat like Claude or ChatGPT (Enter sends, Markdown, copy/edit/retry, drafts, commands, new conversation); mockups and mockup choices appear inside the conversation. Evidence: STATE/evidence/RA-074.md.

## RA-075 — Speak to Cortex from any screen, with 1 · 2 · 3 approvals
Status: **done**. Program: atlas; path: engine. The ✦ Cortex button (Ctrl+J) reads the screen and proposes up to three numbered actions, showing and highlighting each; nothing runs before Yes / 1 / 2 / 3, and every run has Undo. Waiting on you answers its first item with 1 · 2 · 3. Evidence: STATE/evidence/RA-075.md.

## RA-076 — Build › Live: plain steps by default, real output on request
Status: **done**. Program: atlas; path: apps/web. The live console shows plain, animated steps and the agent's latest words; >_ Output shows the real command output. Evidence: STATE/evidence/RA-076.md.

## RA-077 — Quick ✦ Cortex vs the full Cortex chat; conversations; mockups only when chatting
Status: **done**. Program: atlas; path: engine. The ✦ button does Quick note, finding (1–5, ← →), quick answers with choices and small changes; the Cortex chat can propose any function, asks back with choices, offers a mockup only for an idea with a visual side, has no Reason/File/Auto, and keeps earlier conversations behind a faint ☰. Evidence: STATE/evidence/RA-077.md.

## RA-078 — Quick note box removed; the ✦ button takes its place
Status: **done**. Program: atlas; path: apps/web. The right rail's Quick note is gone; ✦ keeps all its functions (Atlas, This computer, Reminder, attach and drop, voice, draft) and works offline for local notes. Evidence: STATE/evidence/RA-078.md.

## RA-079 — ✦ decides by itself; Settings and Agents tabs; no 1·2·3 on Today
Status: **done**. Program: atlas; path: engine. Cortex decides what ✦ input is and the result's colour shows it; shortcuts and a list of every setting under Systems › Settings; Today's number-key approvals removed; Systems › Agents treats coding agents as switchable resources. Evidence: STATE/evidence/RA-079.md.

## RA-080 — Builder falls back to the next ready coding agent when Claude is out
Status: **ready**. Program: atlas; path: engine. Depends on RA-079. When Claude's usage limit is reached, the dev loop runs the next ready agent (Codex CLI first) with the hand-over prompt and returns to Claude after the reset. Needs Codex CLI installed on the Pi.

## RA-081 — One numbering in ✦ and the chat; quick exchanges saved only on request
Status: **done**. Program: atlas; path: engine. Choices then proposals in one numbering; no Yes/No row; ✦ exchanges become a conversation only via Continue in the chat. Evidence: STATE/evidence/RA-081.md.

## RA-082 — Cortex answer quality: per-job model and thinking, no forced brevity, sees matching tasks
Status: **done**. Program: atlas; path: engine. `[models.jobs.chat]` can put the chat on Opus with thinking time; Cortex answers as fully as a question needs and sees tasks beyond the top 40 that match the conversation. Evidence: STATE/evidence/RA-082.md.

## RA-083 — Cortex chat on Claude Code: streaming, real turns, web search, Atlas look-ups, files
Status: **done**. Program: atlas; path: engine. The Cortex chat streams, keeps a Claude Code session per conversation, searches the web, looks things up in Atlas read-only, reads attached files and changes nothing except through numbered proposals; no API key needed. Evidence: STATE/evidence/RA-083.md.
