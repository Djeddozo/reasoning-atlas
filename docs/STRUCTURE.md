# Structure: from vision to the work running now

Status: decided by the owner on 2026-10-20 (ADR-024); placement revised by the owner the same day (ADR-025). It replaces the navigation proposals in ENGINE_FUNCTION_MAP.md and the "avoid the word goal" rule in MODEL.md where they differ. Atlas is the only name; the always-on part on the Pi is "the Engine", a part of Atlas, never a separate app.

## Four layers, top-down

| Layer | Screen | Answers | Contents |
|---|---|---|---|
| 1. Vision | **Focus** | Where am I going in life, and what matters most now? | Lifetime visions in big words (for example: Advance the intelligence of Atlas; Own products and income; Financial stability). The focus chosen among them, recommended over a longer timeline with its reasons. The year objectives under each vision (for example "Atlas finds opportunities and proposes new skills itself" sits under "Advance the intelligence of Atlas"). No tasks, no queue. |
| 2. Workshop | **Forge** | What do I do about it? | Inherits the focus. **Ideas** (where each fits) → **Plan** (quarter and month goals, tasks, ranking with reasons, where the work leads, do-over) → **Build** (the controls: queue, approvals, start / pause / stop the builder, quick build, pull requests, delivery). Everything here is usable, not decorative. |
| 3. Live | **Forge › Build** (was Build progress, merged by ADR-026) | What is the system doing right now? | A visual picture, not controls: every build lane with its program and path, the step it is on, a live console, what finished recently. Until Atlas's original plan is complete, Atlas's own path is the main picture; afterwards every program's paths share the screen. Links to Forge › Build to act. |
| 4. Ground | Notes, Map, Research, Memory, Resources, Systems | What do I know and have, and is the system healthy? | Writing to the brain, the knowledge map and skill tree, research, memory, resources and the arsenal, and the system's own health (usage, budgets, updates, repairs). |

## Navigation (ADR-025)
| Group | Sections | Holds |
|---|---|---|
| Reasoning Atlas | **Today** · Focus · Forge (Ideas · Plan · Build) · **Cortex** · **Notes** | Today: everything only the owner can settle (decisions, approvals, terminal steps, work stopped by a usage limit, reminders); the brief follows (RA-046). Notes: only the owner's thoughts (the Engine brain and local notes). |
| Library | **Map** · Research · Resources · Memory · Tags · Archive | Knowledge and what exists. The function map (with Everything) is part of the Library. |
| Connections | **Systems** | Only connections: the Engine hub now; Claude, Telegram and voice, models and the GPU PC, other apps later. No next steps, needs or terminal steps (they live in Forge and Today). |
| Horizon | Research map · Roadmap · Experiments · Hypotheses | A long-term product vision built on Atlas's functions: kept visible for thinking, visibly apart, outside the build plan and the Library. The research map's lens (cross-domain reasoning) lives here too. |

The sections a function can live in are listed in STATE/sections.json. Every function of Atlas and its programs names exactly one (`atlasSection` in modules/*.json); the registry refuses an unknown section and two functions that do the same thing (the same intent), so the test suite, and with it the Pi's update check, refuses a misplaced or duplicated function. The overlap check that runs before every build compares the work with this registry, so a build hears "this exists in Notes, extend it" instead of building a second copy.

## The Forge (ADR-026)
- **Plan** is the database of everything to build. Per task: ★ Important (ranks first; the builder takes it next), Build (hand it to the builder), Done (your own tasks), Delete; details on click. What the owner marks himself always comes first.
- **Build** shows what is being built and what comes next: the live lanes with console, a short "Up next" (five at most, from the plan's ranking), pull requests and recently finished runs; builder settings, quick build and Atlas's own paths are folded below.
- **Cortex** (owner, 2026-10-25) is Atlas's reasoning core and has its own screen under Forge: reason together, file decisions into the plan with Undo. It goes by Cortex unless the owner gives it a nickname (asked on the first visit, changeable); the nickname shows everywhere in Atlas, including the function map, and the reasoning core answers to it. Code and function ids keep the word `brain`. Below the conversation, **Visual first** (#147): tasks with a visual side get the choice *mockup first* or *build without*; a mockup is iterated on feedback in rounds and holds the build until approved (`engine/mockup.py`, docs/design/MOCKUPS.md).
- Goals (owner, 2026-10-25): **Vision → Goal → Task.** No year, quarter, month or week layers. A goal may have a deadline the owner sets in Focus; that deadline (inherited by goals and tasks below it) is the only thing about a goal that raises its tasks' priority, besides the vision in focus. Goals are added, renamed, given a deadline and set aside in Focus; the Forge shows the work.
- The repository's own build plan (RA steps in STATE/tasks.json) has no screen of its own: each open step is a task in Plan under the goal "Build Reasoning Atlas", first for the builder, and its status follows the repository (RA-068). Whether the builder may merge its own RA pull requests is a Builder setting.
- While anything is being built, the Forge glows red in the menu, and a small Build card above Quick note shows progress and opens Forge › Build.

## What re-evaluates itself (RA-063)
Re-ranking follows every change by rule (no model). Claude re-evaluates the plan daily and after real change (five new thoughts, tasks or ideas, at most every three hours), within the daily budgets; a drifted plan becomes one short re-evaluation on Today to approve. Before every build the same overlap call asks whether the task is still worth building now, against the vision in focus and the plan. Placement is reviewed weekly.

Placement is not fixed: the Engine's revisit reviews it monthly and after every large change (RA-059).

## Rules
- **Top-down inheritance.** The focus chosen in layer 1 ranks work in layer 2 (the Engine's own priority engine, one ranking), and layer 3 groups what runs by the same paths. Nothing in a lower layer redefines a higher one.
- **Big words up, practical words down.** Visions and year objectives are "goals"; work in the Forge belongs to a program and a path (MODEL.md); a lower-level goal (quarter, month) is a plan item in the Forge, not a vision.
- **One place per function.** Every function of the former HQ app has exactly one home (table below); other screens link to it.
- **One name.** No screen, message, document or command shows "HQ". The rename runs in four stages with old names working during the move.

## Where every former HQ function lives

| Cluster | Home | Status 2026-10-20 |
|---|---|---|
| Direction: visions, focus, recommendation | Focus | first version (RA-040); Vision layer RA-041 |
| Direction: ranked next steps, explain rank, where it leads, build mode | Forge › Plan | done (RA-037, RA-040) |
| Direction: brief, cycle, progress, weekly review | Today and Build progress | RA-046 |
| Forge: ideas, placement, overlap, prerequisites | Forge › Ideas | done (RA-039); overlap and prerequisites in RA-045 |
| Forge: plan tree, edit, reorder, overview, task detail, do-over | Forge › Plan | RA-045 |
| Forge: dev loop, controls, queue, approvals, quick build, checkpoints, routing, PR conflicts, delivery, login | Forge › Build | RA-043 |
| Forge: run status, lanes, console | Forge › Build | RA-044, RA-062 |
| Brain: write, dump, sort, ingest, PDFs, voice, ask | Quick note, Notes › Brain | write, list done (RA-036); voice, intake, ask in RA-052 |
| Brain: undo plan changes (`engine.brain.undo_changes`, per change or everything from a thought) | Memory › What changed (Notes links there) | moved from Notes (#126): undo belongs with the change log, not with the owner's thoughts |
| Owner attention: needs, approvals, terminal steps, interrupted work | Today | done (RA-035, RA-060) |
| Skill tree: tree, proof, research how, autolink | Map › Skill tree | RA-047 |
| Research: radar, feed, sources, deep research | Research | RA-048 |
| Assets: arsenal, resources | Resources | RA-049 |
| Memory: recall, lessons, change log, undo, think | Memory | RA-050; undo moved here (#126) |
| System: usage, kill switch, budgets, models, updates, repairs, revisit, revalidate, schedules, constitution, voice set-up | Systems | updates done (RA-038); rest RA-051 |
| Channels: Telegram, phone | The Engine, under Atlas's name | RA-052, RA-031 |

## Retiring the HQ name (four stages)
1. **What the owner sees** (RA-053): screens, Telegram, CLI output, briefs, notifications say Atlas or the Engine.
2. **Documents and agents** (RA-054): docs, decision records, CLAUDE.md/AGENTS.md, agent and dev-loop prompts; done 2026-10-21, the old name is noted in DECISIONS.md.
3. **Code** (RA-055): the `hq` package and `hq.sh` get Atlas names; the old names keep working as thin aliases until nothing uses them. Built 2026-10-23: `engine/` and `atlas.sh`, aliases `hq/` and `hq.sh` (in review; STATE/evidence/RA-055.md).
4. **The Pi** (RA-042): the folder, branch, services and database file move to Atlas names, with a dry run and rollback.
The Engine's own dashboard is retired when every cluster has its home in Atlas (RA-056).
