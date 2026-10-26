# One platform: modules, capabilities and the Systems view

> Update 2026-10-20 (ADR-018): the owner wants one app in the end. The Engine's functions are placed by cluster in [ENGINE_FUNCTION_MAP.md](ENGINE_FUNCTION_MAP.md); the merge direction is an open owner decision. The module and capability model below still applies inside the merged app: clusters become modules, and the registry still decides what runs where.

Status: **proposal** following the owner's direction of 2026-10-20 (ADR-013). The direction is confirmed; the structure below is a proposal until the owner accepts it.

## The owner's direction
> One central platform instead of loose apps. The Engine should be integrated completely. The Engine was designed to be self-learning and to render thoughts better, and certain domains belong together; extracting them out of context risks losing sight of what the system can do. Specific functions should still be callable because another section has use for them. Other apps are the same story.

Two requirements pull against each other:
1. **Coherence.** A domain's logic, data and learning loop stay together, so the owner can see and understand what each system does.
2. **Reuse.** Individual functions are available wherever they are useful, without copying them.

The proposal resolves this by separating *where a function lives* from *where it is used*.

## Core concepts

### Platform shell
Reasoning Atlas becomes the single entry point: one window, one navigation, one search, one notes inbox, one build-progress view. It does not absorb other apps' code. It hosts them.

### Domain module
A module is an existing app or a coherent part of one, kept **whole**: its own code, data, scheduler and learning loop. The Engine remains one module running on the Pi; its self-learning and prioritisation are not split into Atlas functions. A module is the authority for its own data (ADR-005).

Each module publishes a **manifest** describing:
- identity, owner repository, version and where it runs;
- the domains it covers and the data it owns;
- its capabilities (below);
- its integration level and health.

### Capability
A capability is one named function a module offers to the rest of the platform, for example `engine.research.find_options` or `engine.build.prerequisites`. Its contract states:

| Field | Meaning |
|---|---|
| `id` | `module.domain.name`; stable, never reused |
| `summary` | One sentence in plain English |
| `inputs` / `outputs` | JSON schemas |
| `effect` | `read` (no side effects), `propose` (creates a draft for approval) or `act` (changes something) |
| `status` | `planned`, `built`, `tested` or `live` |
| `home` | The module and domain it belongs to |
| `evidence` | Test or run that proves the status |

### Capability registry
Atlas indexes all manifests. When a section needs a function, it calls the capability through the registry, which routes the call to the module's adapter. Sections never import another module's internals.
- Every result carries provenance: the capability ID, module, version and time ("from the Engine · Research").
- `read` calls run directly. `propose` calls show a preview and require explicit confirmation. `act` calls are allowlisted per capability and need owner approval, in line with the Engine's rule that big decisions are approved first.
- A call to an offline module fails visibly; nothing is silently substituted.

### Systems view
A new Atlas section that keeps the whole platform in sight:
- modules drawn as clusters, grouped by domain, with their capabilities inside;
- each capability shows its status (planned, built, tested, live) and its evidence;
- lines show which Atlas sections use which capabilities;
- selecting a capability shows where it lives, who calls it, its last run and its contract;
- a list alternative for keyboard and screen-reader use.

This is the answer to "losing sight": reuse is visible as a connection back to the capability's home, never as a detached copy.

## Integration levels
Each module progresses independently. The Systems view shows the level.

| Level | Meaning | Atlas can |
|---|---|---|
| L0 Listed | Manifest written by hand | Show the module and its planned capabilities |
| L1 Read | Snapshot import through the adapter | Show its data on the map, read-only |
| L2 Callable | `read` and `propose` capabilities through the registry | Use its functions in any section |
| L3 Embedded | The module's screens appear as Atlas sections | Work in it without leaving Atlas |
| L4 Primary | Atlas is the module's main interface | Retire the module's own UI; its engine keeps running |

"Integrated completely" for the Engine means **L4**: Atlas becomes the Engine's interface while the Engine (scheduler, self-learning, priorities, devloop) keeps running on the Pi as the module's backend. No Engine logic is rewritten inside Atlas.

## Engine domain groups
Derived from the Engine modules examined in [INTEGRATION_ENGINE.md](INTEGRATION_ENGINE.md). Confirmed by the owner on 2026-10-20 (VQ-006).

| Domain | Engine parts | Example capabilities |
|---|---|---|
| Direction | dashboard, priority, goals and plans | current focus, ranked next steps |
| Build (Forge) | devloop, delivery, resume, checkpoint | propose a build, run status, delivery evidence |
| Research | research, deepresearch, arsenal | find options, sourced summary, reusable software |
| Growth | skilltree | proof of a skill |
| Memory | db | recall decisions, record a lesson |

## Other apps
Repositories seen on 2026-10-20: Life-Dash, LifeDashV5, finance_vault, Cilindervinder, Padelcode. Their purpose and state are not yet verified; the owner will describe them later and Atlas reminds him (one 'Dissect' item per app under Waiting on you). Each starts at L0 with a hand-written manifest after the owner confirms what it does and which domain it belongs to. LifeDashV5 overlaps with the Engine Direction (life areas and goals); one authority must be chosen before both are connected.

## Working from inside Atlas
The owner wants to work on his projects from inside Atlas rather than in a separate chat window, while keeping the Claude app and Codex as fallbacks (ADR-017). This needs three things.

**1. Know what Atlas can and cannot do.** Every capability has an *intent* (what the work is, such as `web.research` or `code.edit`) and a status. The registry's `resolve` decides, per intent:
- *here*: a live capability inside Atlas or a module; the work stays in Atlas;
- *switch to*: no live capability inside the platform, but an external app can do it (Claude app, Codex); Atlas hands the work over;
- *planned*: the platform will do this later, which is shown so the switch is understood as temporary.

External apps are manifests of kind `tool` (`modules/claude-desktop.json`, `modules/codex.json`), so switching is a decision recorded in data, not guesswork. Example today: web research is planned in the Engine, so Atlas switches to the Claude app.

**2. Switch without losing context.** A handoff packet carries everything the other app needs: the task ID, the objective, what was done, the relevant files and the exact next action. Atlas copies it and opens the app. The repository stays the shared memory (ADR-007), so any app can continue. Work returns to Atlas through commits, pushed checkpoints (ADR-015) and progress reports, which Atlas shows live (RA-019).

**3. Learn what is missing.** Every switch is logged with its intent. The most frequent switches show which capabilities to build into Atlas next, so the platform gradually needs the other apps less.

How Atlas runs an agent itself (the workbench) is an open owner decision (VQ-009): the Claude API with the owner's key, Claude Code running on the PC or Pi, or a local model on the GPU PC. Each has different costs, abilities and offline behaviour; the registry records the chosen workbench's abilities like any other module.

## Rules that keep this safe
- One authority per dataset; Atlas never writes another module's database directly.
- Capabilities are versioned; a breaking change gets a new ID or version.
- Modules can be offline; Atlas degrades to cached, visibly stale data.
- No capability is shown as `live` without evidence.
- Changes inside the Engine or another app go through that repository's own branch, tests and gates.

## Tasks
RA-020 manifest and registry contract (done) → RA-021 Systems view → RA-022 capability calls (review: `services/atlas/capabilities.py`, STATE/evidence/RA-022.md) → RA-023 app inventory → RA-025 handoff between apps → RA-026 workbench inside Atlas. See [TASKS.md](../TASKS.md).
