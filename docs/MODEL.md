# Programs, paths and functions

Status: decided by the agent at the owner's request (ADR-020, 2026-10-20); the owner may adjust it. It governs how the Engine, Finance and later programs fit into Atlas.

## The owner's requirements
1. The Engine's structure was mostly good; integrate it with the rest of Atlas, not beside it.
2. Atlas may be the whole program now and a part of a bigger program later.
3. Everything should be expressed as functions that other parts can use; a coherent group (the Engine, Finance) may stay together, but its functions must be usable by other sub-programs.
4. Builds must show clearly what they are for (Atlas, or another plan). Use Atlas's own words; avoid "goal".
5. The Engine suffered from information overload, for example finished tasks that never went away.

## Three concepts

### Program
A unit the owner thinks of as one thing: Atlas, the Engine (formerly HQ), Finance. Programs **nest**: every program has an optional parent. Today Atlas is the root and the Engine and Finance are its sub-programs. If Atlas later becomes part of something bigger, only its parent changes. A program owns its data and its sections, and publishes functions.

### Function
One thing a program can do, registered once with exactly one home program (the capability registry, RA-020). Any other program may use it through the registry, which records where results came from. A function's state (planned, built, tested, live) and its evidence are the single answer to "can the system do this?"

### Path
The visible building route of a program: from where it is to a destination, as ordered, dependent steps. Every build task belongs to **one program and one path**. "Path" replaces "goal" for building work; a path ends in a destination (a function going live, or an outcome such as "first digital product"). Several paths can exist per program; at most a few are active at once.

## The skill tree becomes a view, not a second system
The Engine's skill tree mixes two kinds of nodes. They are separated:

| Engine skill-tree node | Kind | Becomes |
|---|---|---|
| Memory, Brain, Chat with Claude, Weekly review, Intake, Radar, Arsenal | built function | Engine functions (clusters Memory, Brain, Direction, Research, Assets) |
| Planning & ranking, Dashboard | built function | Forge Plan; the dashboard is replaced by Atlas's screen |
| Phone & Telegram | built function | Engine channel (Brain, Owner attention) |
| Self-building, Usage & kill switch, Automatic updates | built function | Forge Build and System |
| Budget & spending | planned function | **Finance** program (finance_vault), not the Engine |
| Parallel workstreams, Local models, Reading PDFs, Calendar & email, Sandbox, Prototype builder, Product packages | planned functions | Engine functions, status planned |
| Better memory, Checkpoint & resume, Noticing parallel work, Parallel builds, Auto research, Learning from outcomes ("self" branch) | planned functions | The Engine's own improvement path |
| First digital product, Physical products, Launch & sell ("product" branch) | **destinations** | ends of paths, not functions |

The Engine's branches (core, ops, mind, reach, make, product, self) map to clusters, and the product branch to path destinations. The skill tree view shows the function graph (state, proof, what unlocks what) and the paths leading through it. The Engine's proof levels map onto function status and evidence.

## Duplicates between the Engine and Atlas, resolved
| Duplicate | Decision |
|---|---|
| Two task lists (Atlas's STATE/tasks.json; the Engine's tasks in hq.db) | Both stay for now, each tagged with program and path; one Build view shows both. Atlas's repository tasks remain the source for agents working on Atlas itself; moving them into the Engine so its dev loop can build them is a later, explicit step. |
| Two build-progress systems (dev loop console; Atlas's progress bar) | One view: Forge → Build shows dev-loop lanes and agent-reported progress side by side, both tagged with program and path. |
| Two "waiting on you" lists (the Engine needs and approvals; Atlas reminders) | One list, Waiting on you; each item keeps its source. |
| Two update paths (the Engine's updater on the Pi; Atlas's restart marker on screens) | Kept: the hub updates itself, screens follow with the marker. |
| Two checkpoint mechanisms (the Engine checkpoint/resume; Atlas handoff) | Engine's interruptions feed Continue building (RA-025). |
| Arsenal and the registry's "reuse" | Arsenal indexes code; the registry indexes functions. Both are searched when deciding to build. |

## Against information overload
- **Default views show what is active**: the active paths, their next few steps and anything waiting on the owner. Everything else is one click away, not on screen.
- **Finished work folds away**: done steps collapse into a count per path and move to Archive after 14 days. Nothing is deleted.
- **Progress is summarised per path** (done/total, what is building now), not as long task lists.
- **Every build line names its program and path**, so a glance shows what it serves.

## Look and behaviour
Atlas's visual language (dark workspace, violet nodes, orange highlights, the map) is the look of every section, including the Engine's (owner, 2026-10-20). The Engine's dashboard interaction design is kept, because it was designed around how the engine works:
- optimistic actions: an item leaves at once, returns if the server refuses, Undo for 7 seconds (backed by the engine's change log);
- Today first: work stopped by a usage limit and terminal steps on top, then needs and focus;
- each function lives in one place; other places link to it rather than repeat it;
- Forge as one header with a stage strip: Ideas → Plan → Build;
- answering needs and approvals in place, by tap, typing or voice;
- a bottom tab bar on phones;
- replayable guides for substantial features (engine/guides.py).

## Words
Program, path, function, destination for building work. Since ADR-024 (docs/STRUCTURE.md) "goal" is used at the top: lifetime **visions** and the **year objectives** under them, on the Focus page. Quarter and month goals are plan items in the Forge. Builds still belong to a program and a path.

## Order of work
1. Registry: nestable programs; Atlas, Engine, Finance (RA-032).
2. Build view: program and path on every build, finished work folded (RA-032).
3. Engine tasks gain program and path when its planning moves into Forge (with the Engine API, RA-030 onwards).
4. Skill tree view over the function graph: first slice done (RA-033, focus-driven map and Everything); proof and build state from the Engine follow with the Engine API.
