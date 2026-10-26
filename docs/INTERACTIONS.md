# Interactions and states

| Context | Action | Result | Failure / recovery |
|---|---|---|---|
| Intake | Save note | Persist locally before offering AI processing | Offline: saved-local; never disappear silently |
| Map | Select node | Inspector with type, relations, sources, evidence and actions | Missing source shown as unresolved/tombstone |
| Map | Open relationship | Explain provenance, author and confidence | Reject suggestion without deleting source |
| Map | Zoom/pan/filter | Persist view camera and filters | Reset changes presentation only |
| Skilltree | Select blocked capability | Show missing prerequisites and evidence | Cycle errors expose the involved path |
| Experiments | Record a test | Hypothesis, method, inputs, result and conclusion | Failed/inconclusive differs from disproven |
| Hypotheses | Add hypothesis | Draft with falsifiable question and sources | Draft remains when no model is available |
| Research | Adopt / Adapt / Build | Route and reusable-work proposal preview | The Engine's outside-code gates remain authoritative |
| Forge | Submit proposal | Preview → explicit submission → external ID | Timeout requires reconciliation, not blind replay |
| Build | Inspect run | State, log cursor, branch, commit, tests and PR | Disconnected data marked stale with timestamp |
| Memory | Correct record | New revision with reason; history retained | Undo exposes diff and affected relationships |
| Vision | Ask question | Persist question before provider call | Limit leaves pending/blocked state with same ID |
| Vision | Accept clarification | Show vision diff, require explicit acceptance | Reject retains Q&A without changing vision |
| Export | Export data | JSON/Markdown and attachment manifest | Failure preserves previous export |
| Settings | Configure connection | Probe and display read/write scope | Never put secrets in browser logs or URLs |

## Reference navigation
Map, Roadmap, Experiments, Notes, Hypotheses and Focus; library items Papers, Resources, Tags and Archive. Map shows Focus, Selected Node and Quick Note on the right and Research Roadmap below the graph.
Skilltree, project and capability views should be discoverable without redesigning this composition. Place the proposed Vision Room as a secondary navigation entry/help destination and visually review its addition. Keep software ROADMAP.md distinct from the research roadmap in the UI.

## State semantics
Task: draft/ready/in_progress/blocked/review/done.
Run: queued/running/interrupted/succeeded/failed/cancelled.
Delivery: built/tested/pr-open/merged/installed.
These are separate axes: merged is not installed. Capability proof and current availability are also separate.

## Search and access
Ctrl+K opens search/actions; / focuses search outside an editor; Esc closes overlays; Enter opens selection. Search title, tag, source and exact ID. Filters never delete data. Empty results expose a reset-filter action.
Keyboard navigation and a textual list provide equivalent graph information. Pinned nodes remain positioned after refresh. Browser zoom and reduced motion must remain usable.

## Undo and concurrency
Store author, revision and change ID. Use optimistic concurrency; conflicting revisions expose both versions. Reversible local actions offer Undo.
An acknowledged Engine action can only be undone through a supported compensation. Do not invent rollback for a started run. Non-reversible or uncertain actions are not optimistically displayed as successful.

## Build inside Atlas
Capability details offer Research approaches, Create build proposal, Open in Forge and View execution. Show access scope, external target ID and source version. Do not require an embedded terminal.
Do not base integration on iframes; authentication and headers may prevent embedding. The adapter is the durable boundary.
