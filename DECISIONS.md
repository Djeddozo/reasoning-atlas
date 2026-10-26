# Decision register

> **Name note (RA-054, 2026-10-21).** Until ADR-024 the always-on part of Atlas was a separate app called **HQ** (repository Djeddozo/HQ-APP, now archived). It is now **the Engine**, and Atlas is the one app. Records below use the current names; where a record describes HQ-APP itself (commits, repository, the HQ-folder) the old name is kept. Dated records under STATE/evidence/ and docs/reviews/ keep the wording of their day. Code names (`hq/`, `hq.sh`, `HQ_*` variables, hq.db) change in RA-055 and on the Pi in RA-042.

| ID | Date | Decision and status | Rationale / alternative |
|---|---|---|---|
| ADR-001 | 2026-10-20 | Confirmed: Djeddozo/Unconventional-Reasoning-Atlas is the target | Explicit owner URL; verified private, initially empty and writable; owner ID 73845182, repository ID 1426242100. Earlier HQ-folder fallback superseded. |
| ADR-002 | 2026-10-20 | Required: original Windows reference anchors the design | Initially unavailable, subsequently supplied by owner and preserved unchanged. No substitute generation. |
| ADR-003 | 2026-10-20 | Proposed: the Engine is the first integration partner | Strong Forge/Research/skilltree evidence; no destructive migration. |
| ADR-004 | 2026-10-20 | Proposed: independent adapter boundary | Avoids copying the Engine; snapshot-first because GET /api/data recalculates priorities. |
| ADR-005 | 2026-10-20 | Proposed: Atlas owns its data; the Engine retains its authority | Avoid duplicate schedulers and direct live Engine memory writes. |
| ADR-006 | 2026-10-20 | Proposed: web client + Python service, later Windows wrapper | Validate usability and visual fidelity first; exact framework versions pending. |
| ADR-007 | 2026-10-20 | Required: desktop AI and session handoff | Repository state and checks, not provider-specific chats, carry continuity. |
| ADR-008 | 2026-10-20 | Required: separate vision page and persistent questions | Owner wants to remember the purpose and keep asking questions. |
| ADR-009 | 2026-10-20 | Proposed: single-writer MVP with durable outbox | Prove conflict and recovery behavior before multi-master synchronization. |
| ADR-010 | 2026-10-20 | Confirmed: English remains the working language | Owner explicitly requested English for the program, branches, everything including code. Translate current artifacts; preserve original source provenance and published Git history. |
| ADR-011 | 2026-10-20 | Confirmed reference intake | The owner supplied the PNG after the missing-image report. Use the delivered bytes as authoritative; do not claim independent identity with inaccessible prior bytes. |

For new decisions, record ID, question, alternatives, choice, consequences, authorization source and any superseded decision. A proposal does not become owner-approved merely by appearing here.

## ADR-012 — Local milestone implementation
2026-10-20: implement the proposed React/TypeScript client with a Python standard-library HTTP service and SQLite, on loopback port 4310. Dependency versions are locked by npm. This minimizes local setup while retaining the independent Engine adapter boundary. The owner explicitly prioritizes a visible app with dimmed unbuilt components. Native packaging remains later work.

## ADR-013 — One central platform
2026-10-20, owner direction (confirmed): Atlas becomes the single platform instead of separate apps, with the Engine integrated completely; other apps follow. Domains that belong together must stay together so the owner keeps sight of each system's capabilities, while individual functions remain callable from other sections.
Proposed structure (awaiting owner acceptance): apps stay whole as domain modules with their own data and engines; each publishes a manifest of capabilities; Atlas calls them through a registry with provenance and shows everything in a Systems view; integration proceeds per module from L0 (listed) to L4 (Atlas is the primary interface while the module's engine keeps running). See docs/PLATFORM.md.
Consequences: supersedes the "first integration partner" framing of ADR-003; ADR-004 and ADR-005 (adapter boundary, separate authority) remain in force. No Engine code is rewritten inside Atlas.

## ADR-014 — Live build progress and safe rebuilds
2026-10-20, owner request (confirmed): the owner keeps the app open while agents build and wants to see construction progress in the app. Builds go to versioned release folders and switch through an atomic pointer, so files an open app uses are never overwritten; the client reloads by itself and keeps drafts. Agents report steps with scripts/progress.py; status lives in ignored runtime/build-status.json. A builder silent for 15 minutes is shown as paused.

## ADR-015 — Commit and push every checkpoint
2026-10-20, owner instruction (confirmed): always commit and push to GitHub, and save a checkpoint to GitHub before a Claude or Codex usage limit can interrupt the work. Push after every checkpoint, not only at session end. A local commit that is not pushed does not count as a checkpoint.

## ADR-016 — Atlas keeps the owner's open items
2026-10-20, owner request (confirmed): Atlas also serves as a notepad that reminds the owner of open items, starting with dissecting his other apps. Agent reminders are versioned in STATE/owner-reminders.json; the owner's own reminders and completion state stay local. VQ-006 confirmed Engine domain groups (Direction, Build, Research, Growth, Memory).

## ADR-017 — Work from inside Atlas, switch apps when needed
2026-10-20, owner direction (confirmed): the owner wants to work on his projects inside Atlas, with the Claude app and Codex as fallbacks, and to switch between them seamlessly. Atlas must know which functions it cannot perform and hand the work to the app that can.
Implemented foundation (RA-020): capabilities carry an intent; external apps are `tool` manifests; `resolve` returns here / switch to / planned. Proposed next: handoff packets and a switch log (RA-025), then an in-app workbench (RA-026). The workbench technology is an open owner decision (VQ-009).

## ADR-018 — One app, merged with Engine
2026-10-20, owner direction (confirmed): use all of the Engine's functions in Atlas, placed by cluster so related functions stay together; in the end one app instead of several. Assessment: one product with two processes (always-on engine on the Pi, interface on the PC) is the right shape; nothing needs to remain a separate app.
Open owner decision: the merge direction (Atlas into the Engine's repository, recommended; the Engine into Atlas's; or connect first and merge later), and whether the Engine's dev loop becomes Atlas's builder (VQ-009). Placement proposal: docs/ENGINE_FUNCTION_MAP.md (RA-028). Supersedes the "keep the Engine separate" framing of ADR-004/ADR-013 once the owner chooses; the Engine's rules and data ownership stay in force.
Placement rule (owner, 2026-10-20): place by cohesion, not origin. The Engine's clusters spread across sections; finance_vault becomes its own Finance section, kept whole, and fulfils the Engine's planned Budget & spending node.

## ADR-019 — Atlas is the one repository; the Pi is the hub
2026-10-20, owner decisions (confirmed), resolving ADR-018:
- **Merge direction:** The Engine's code and full history move into this repository; HQ-APP is archived read-only. The name HQ is retired; internal names (the `hq` package, `hq.sh`, Pi services) are renamed later in safe steps.
- **Hub:** the Raspberry Pi owns the data and runs the always-on work (dev loop, radar, schedules, Telegram, updater, recovery).
- **Screens:** every device runs a fast local Atlas screen that talks to the hub, with a read cache and an offline outbox (ADR-009). Several full copies with their own data are out of scope until RA-013.
- **Builder:** The Engine's dev loop on the Pi is Atlas's builder (answers VQ-009); the Claude app and Codex remain fallbacks through Continue building.
- **Remote access:** Tailscale only; the hub requires an access token (the Engine's current "no token, no check" behaviour must be closed).
Order: merge in the repository first; the Pi keeps running HQ-APP unchanged until the merged `main` passes its tests on the Pi; then a reversible switchover. Placement of functions stays as in docs/ENGINE_FUNCTION_MAP.md; the owner may still adjust the groups.

## ADR-020 — Programs, paths and functions
2026-10-20. Owner input: the Engine's structure is mostly good but must integrate with the rest of Atlas; Atlas may later be part of a bigger program; everything should be functions usable across sub-programs; builds must show what they are for, in Atlas's own words (no "goal"); the Engine suffered from information overload; keep Atlas's look and the Engine's interaction design. The owner asked the agent to decide how to continue.
Decision (agent, owner may adjust): programs nest (Atlas root; Engine, formerly HQ, and Finance below); each function has one home program and is usable by all through the registry; every build belongs to one program and one path; the Engine's skill tree becomes a view over the function graph, with its product branch as path destinations; duplicates between the Engine and Atlas resolved as listed in docs/MODEL.md; default views show what is active and fold finished work. The registry module `hq` is renamed `engine`.

## ADR-021 — The hub serves Engine functions read-only first
2026-10-20 (RA-030). Atlas's service runs on the Pi in hub mode next to the Engine (docs/HUB.md): Tailscale address only, required token on every request, the Engine's database opened read-only at the SQLite level, only allowlisted functions (`viaHub`). Screens on other devices reach it through their local Atlas service, which keeps the token. Writes follow per cluster, through the Engine's approval rules and change log. Correction to ADR-019: the Engine's dashboard already requires a token beyond localhost; the gap was smaller than stated.


## ADR-022 — One Update button for every machine (2026-10-20)
Context: the owner wants to update the PC, the Pi and the phone from Atlas's update button instead of terminals.
Decision: the Pi keeps the Engine's updater as the only way code reaches it (tests on a copy first, fast-forward, restart); Atlas only starts it through the hub, outside the hub's own service, and shows its progress. The PC installs by fast-forward only and restarts through its launcher. Hand-made changes and local commits always block, never get overwritten. Other screens load from the Pi and follow it.
Consequences: one click updates everything reachable; a failing test suite still stops the Pi update; the first hub with this action has to be installed once by the existing 15-minute job or `./hq.sh update`.

## ADR-023 — Focus is about directions; the Forge is where the work is ranked (2026-10-20)
Context: the owner found the Focus page too practical (queue, next steps) and wants focus on big, conceptual aims such as improving Atlas, improving the LLM system, creating a product or solving financial issues, with a smart recommendation over a longer timeline; the Forge should inherit the focus and show its own queue. The Engine's ranking must remain the one ranking.
Decision: a direction is a top-level Engine goal on a year or lifetime horizon (goals already nest: life → year → quarter → month → week). Focus chooses one direction (`hq/compass.py`, state `focus_direction`); the Engine's `priority.rescore` gives work under it a bonus and `priority.explain` says "in your focus", so there is still one ranking. The recommendation is rule-based and explains itself: progress behind the time used of the horizon, weeks without movement, work due soon, work that unblocks other work, a small momentum bonus. Ranked next steps, "Where the work leads" and the build mode (plan first, balanced, ideas first, management) move to Forge › Plan, which shows the inherited focus with In focus / Everything.
Consequences: goals that sit under no direction are listed so the owner can place them; Atlas's map focuses (STATE/focuses.json) remain map lenses for now and should later be linked to directions; a model-written recommendation can be added later through `models.complete` (a new job in the constitution, which the owner approves).

## ADR-024 — Top-down structure: Vision, Forge, Build progress; one name (2026-10-20)
Context: integrating the Engine brought many functions into Atlas; the owner wants a top-down app, from long-term vision goals in big words down to practical work, with all the Engine functions kept and the HQ name gone.
Decision (owner's choices): Focus is the vision page (lifetime visions, the focus among them with a recommendation over a longer timeline, year objectives under each); the Forge inherits the focus and is practical (Ideas → Plan → Build controls); Build progress is the visual live view of what runs, Atlas-first until its original plan is done. Every Engine function has one home (docs/STRUCTURE.md). The Engine name disappears everywhere in four stages: what the owner sees, documents and agents, code with temporary aliases, and the Pi.
Consequences: supersedes the navigation in ENGINE_FUNCTION_MAP.md and refines ADR-023 (directions become visions); new paths Vision, Forge, Build progress and One name in STATE/tasks.json.

## ADR-025 — Placement revised: Today, Library holds the map, Systems are connections, Horizon apart (2026-10-20)
Context: after RA-045 the owner found functions in the wrong sections: needs and terminal steps under Notes and Systems, a second "next steps" in Systems, the function map outside the Library, and the long-term research product (roadmap, experiments, hypotheses, research map) mixed into Atlas.
Decision: a Today section collects everything only the owner can settle; Notes holds only his thoughts; the Library holds the function map; Systems holds only connections; Horizon groups the long-term research vision, visibly apart and outside the build plan. The Engine's revisit will review placement regularly (RA-059).
Consequences: docs/STRUCTURE.md updated; the map opens on "Building Atlas"; the synthetic concept map and its inspector appear only in Horizon.

## ADR-026 — The plan is the database; Build merges with Build progress (2026-10-20)
Context: the owner found the Build queue too long and too much like the plan, many plan buttons pointless (move up/down, doing), and Build in the Forge overlapping with Build progress.
Decision: the plan holds every task, with ★ Important as the owner's way to put something first (the ranking pins it on top and the builder takes it next); Build shows the live build and a short Up next from the ranking, with settings folded; Build progress leaves the menu and lives in Forge › Build; a red Forge glow and a small Build card signal building; Ideas · Plan · Build are shown as three workflow stations.
Consequences: STATE/sections.json drops "Build progress"; its functions moved to Forge › Build; the old "Build progress" view redirects.
