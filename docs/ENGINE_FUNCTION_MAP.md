# Engine function map: every Engine function, and where it lives in Atlas

Status: **proposal** (RA-028), based on a full read of HQ-APP at commit 1599d42 (2026-10-18): 50 modules, 32 tables, about 60 dashboard actions and 50 CLI commands. The owner's direction (2026-10-20): use all of the Engine's functions inside Atlas, decide where each belongs, and keep together what belongs together.

## Principle
The Engine's functions are grouped into **clusters**: sets of modules that share tables, call each other and form one workflow. A cluster is placed in Atlas **as a whole**; it is never split across sections. Other sections may *show* or *call* a cluster's functions (through the capability registry, with provenance), but the cluster keeps one home. The Engine's own Forge already does this: Ideas → Plan → Build are stages of one workshop, so they stay one Atlas section.

## The eight clusters

| # | Cluster | Engine modules | Engine tables | Atlas home | Also shown in |
|---|---|---|---|---|---|
| 1 | **Brain** — capture and sort thoughts ("write to brain") | brain, dump, flow, chat, intake/, voice (models.transcribe, voicesetup, voiceback), interfaces/telegram | thoughts, intake, chat, brain_changes | **Quick note becomes "Write to brain"**; a **Brain** page under Notes (thoughts, what the Engine made of them, undo) | Header (voice), phone via the Engine's Telegram bot |
| 2 | **Forge** — Ideas → Plan → Build, one workshop | *Ideas:* placement, overlap, prereq · *Plan:* plan, priority, edit, overview, doover, progress, taskdetail · *Build:* devloop, quickbuild, checkpoint, routing, delivery, gatekeeper, claudeauth | ideas, idea_places, goals, tasks, dev_runs, quick_build_requests, prs, reconcile, overlap_checks, approvals (build) | **New Forge section** with the three stages; Atlas's **Build progress** becomes Forge → Build (the Engine's live console, queue, lanes, history, merged with Atlas's own build bar) | Focus panel (next step), Continue building |
| 3 | **Direction** — where it is heading | direction, priority (explain), loop (cycle, brief), progress (review, streaks) | goals, tasks (read) | **Focus panel and a Today view**: mission, current focus, next step, long-term goal, weekly review | Map (goal nodes) |
| 4 | **Skill tree** — what can be built, proven and reused | skilltree, research (node research), autolink | capabilities, node_research | **Map → Skill tree view** (RA-007): nodes, state, proof, confirm or dismiss, "Research how", focus | Forge → Build (build order), Systems |
| 5 | **Assets** — what the owner has | arsenal, resources | projects, assets, resources | **Resources** (Library): hardware, accounts, time, money, skills, plus the arsenal of everything built | Skill tree (reuse), Forge (overlap) |
| 6 | **Research** — what is new and what it means | radar, deepresearch | radar, research_notes, skills | **Research** (Library, replaces "Papers"): feed, saved, deep research, sources and limits | Brain (an item can become an idea) |
| 7 | **Memory** — what the Engine has learned | db.recall, relevant_memories, agent (think) | memories (+ full-text index) | **Memory** (Library, RA-012): lessons, decisions, preferences, facts, searchable | Every cluster (relevant memories in context) |
| 8 | **Owner attention** — what only the owner can settle | needs, commands, resume, approvals via gatekeeper | approvals, shell_commands, interruptions, reconcile (answers) | **Waiting on you**: one list merged with Atlas's reminders (decisions, approvals, terminal steps, work stopped by a usage limit) | Header count, Continue building (interruptions) |
| — | **System** — the Engine keeping itself healthy | usage (kill switch), budget, models, routing, updater, recovery, revisit, revalidate, envfile, guides | model_usage, incidents, revisits, revalidations, state, log | **Systems → the Engine**: usage and cost, budgets, kill switch, updates, repairs, revisits, revalidations, constitution | Header: global kill switch |

System is listed separately because it is the Engine's self-maintenance rather than a working area; it belongs with the module registry in Systems.

### How this relates to the confirmed domains (VQ-006)
The five confirmed domains remain: Direction (3), Build (2), Research (6), Growth (4 and 5), Memory (7). The full read adds **Brain** (1), **Owner attention** (8) and **System**, which the Engine treats as separate departments. Proposed: add them as domains of the Engine module (owner to confirm).

## What changes in Atlas's navigation
| Now | Proposed |
|---|---|
| Map | Map (knowledge) and Skill tree views |
| Roadmap | Roadmap (research directions, unchanged) |
| Experiments, Hypotheses | unchanged (Atlas's own research work) |
| Notes | Notes + Brain |
| Focus | Focus becomes **Today**: direction, waiting on you, brief |
| — | **Forge** (Ideas, Plan, Build), absorbing Build progress |
| Systems | Systems, including the Engine's System cluster |
| Papers | **Research** |
| Resources | Resources + arsenal |
| Tags, Archive | unchanged; **Memory** added |

## Building inside Atlas (the Claude connection)
The Engine already contains the builder: **devloop** runs Claude Code on the Pi with the owner's subscription, one task per run on a branch, with checkpoints before limits (checkpoint), model choice per task (routing), daily limits and a kill switch (usage), approvals for risky steps (gatekeeper), pull requests tracked to installation (delivery), and recovery of work stopped by usage limits (resume). Proposed answer to VQ-009: **Atlas's workbench is the Engine's dev loop**. Atlas's Forge → Build shows its lanes and console and lets the owner start, pause, stop and approve, and Continue building offers "Build in the Engine" next to the Claude app and Codex.

## One app (ADR-018)
The owner wants a single app in the end, not several apps to switch between. That is the target.

**What must stay separate, and why it is still one app.** An always-on part has to keep running on the Pi while the PC is off (the dev loop building at night, radar scans, cron jobs, the Telegram bot, the updater and the repair watchdog). The window the owner works in runs on the PC. These are two processes of one product, like a mail server and a mail app: one codebase, one install, one owner per dataset, one interface. They are not separate apps.

**Which way to merge.** HQ-APP already has everything an always-on app needs: Pi deployment, updater, self-building dev loop, recovery watchdog, Telegram, budgets and the gatekeeper. Options:

| Option | What happens | Trade-off |
|---|---|---|
| A. Atlas moves into the Engine's repository (recommended) | Atlas's service, client, contracts, tests and plan move into HQ-APP as its new interface; the Engine's dashboard is retired cluster by cluster as Atlas takes each one over | The Engine's dev loop, updater and deployment work for Atlas from day one; the repository keeps the Engine's name until renamed |
| B. The Engine moves into Atlas's repository | The Engine moves into this repository | Its deployment, updater, dev loop, cron and systemd setup must be re-pointed and re-tested first |
| C. Connect first, merge later | Two repositories; Atlas calls a new Engine API | Lowest immediate risk, but two apps remain until the merge |

In every option, actions go through the Engine's own rules (approvals, change log with undo, constitution limits), so moving the interface does not weaken the Engine's safeguards, and each dataset keeps one owner.

## Integration order (proposed tasks)
1. **The Engine API for Atlas** (HQ-side, separate Engine branch and PR): versioned, authenticated endpoints; side-effect-free reads (snapshot export, RA-009); actions that call the Engine's existing functions.
2. **Owner attention** first: merging the Engine's needs, approvals, terminal steps and interruptions into Waiting on you is small and immediately useful.
3. **Brain**: Quick note writes to the Engine's brain; Brain page.
4. **Forge** with Build: dev loop lanes, queue and controls in Atlas; Continue building gains "Build in the Engine".
5. **Direction / Today**, then **Skill tree** (RA-007), **Research**, **Assets**, **Memory**, **System**.
Each step moves the Engine's integration level for that cluster from L0 to L3, and to L4 once the Engine's own dashboard is no longer needed for it.

## Not placed in the Atlas interface
- **Telegram bot** stays an Engine channel (phone capture and approvals); its input lands in the same brain Atlas shows.
- **Cron jobs, systemd units, the updater and recovery watchdog** keep running in the Engine; Atlas shows their state.

## Other apps follow the same rule
Placement follows cohesion, not origin. The Engine covers many areas, so its clusters spread over several Atlas sections. An app that is one coherent area moves in whole as its own section.
- **finance_vault → Finance** (owner direction, 2026-10-20): its own section, kept whole. The Engine's planned skill-tree node "Budget & spending" (unbuilt, Phase 4 `finance.py`) should be fulfilled by finance_vault rather than built a second time in the Engine. The Engine's existing `budget.py` is different: limits on the Engine's own AI usage, which stays under System. Financial data stays local and out of Git.
- The other apps are placed once dissected (Waiting on you, RA-023).

## Registered (RA-034)
All 85 user-facing Engine functions are in modules/engine.json with evidence, use today and home section:

| Area | Functions | Live | Home in Atlas |
|---|---|---|---|
| Brain | 12 | 12 | Notes › Brain |
| Forge | 25 | 25 | Forge |
| Direction | 9 | 9 | Today |
| Skill tree | 7 | 7 | Map › Skill tree |
| Assets | 3 | 3 | Resources |
| Research | 7 | 6 | Research |
| Memory | 4 | 3 | Memory |
| Owner attention | 7 | 7 | Waiting on you |
| System | 11 | 11 | Systems › Engine |
