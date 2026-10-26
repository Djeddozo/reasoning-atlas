# Integration investigation: Engine, Forge and Research

## Identification
On October 20, 2026, the GitHub connection exposed Life-Dash, Cilindervinder, Padelcode, LifeDashV5, finance_vault and HQ-APP. HQ-APP and LifeDashV5 README files were read; Life-Dash README.md returned 404. No separately named Forge/Research repository appeared in this accessible set. This does not prove other private repositories do not exist.

Strongest match: **Djeddozo/HQ-APP**, pinned commit **1599d42cfb03fa381b3bbd2b8c66254b3e670e3c** (October 18).
README, CLAUDE.md, hq/dashboard.py, hq/skilltree.py and hq/research.py were examined. Commit 983b0bf84f42cee8a78cad9cb0f13734b9debd06 explicitly connects skill proof, Research Tools/Developments and ideas in Forge.
Forge and Research are the Engine modules. This is a strong evidence-based match, not a claim of explicit personal confirmation by the owner.

## Reuse map
| Engine module | Atlas use | Boundary |
|---|---|---|
| skilltree.py | capabilities, requires, proof/evidence | Preserve Engine status; no second scheduler |
| research.py / deepresearch.py | sourced options and draft tasks | Calls on explicit action within the Engine's rules |
| dashboard.py | data, task detail, logs and actions | Not assumed to be a stable versioned public API |
| devloop.py | branches, tests, PRs and build state | Supported controlled routes only |
| delivery.py | PR/merge/install evidence | Merged is not installed |
| resume.py / checkpoint.py | usage-cap detection/checkpoints | Supplement with portable Git handoff |
| arsenal.py | find reusable existing software | Preserve origin and licensing information |
| db.py | Engine memory | Never direct-write live hq.db |

## Observed HTTP surface
GET /api/data, /api/task-detail, /api/run?run=...&after=..., /api/overview.
POST /api/action uses op/id/text; observed operations include node_research, tool_idea and note_idea.
Authentication uses X-HQ-Token. Correction (2026-10-20, RA-030): `hq serve` refuses to listen beyond localhost without HQ_TOKEN, and the Tailscale HTTPS setup runs `hq link`, which creates one; an absent token only permits requests on the Pi itself.
Crucially, build_data calls priority.rescore: GET /api/data is not a proven side-effect-free read. Start with a controlled snapshot/export or a future explicit read-only endpoint. No existing Engine /v1 API is claimed.

## Adapter boundary to implement
Atlas talks to an adapter contract: health, capabilities, importSnapshot, listEntities, proposeBuild, getRun and reconcileCommand. The initial OpenAPI contract defines the health/import/proposal/reconciliation subset in contracts/hq-adapter.openapi.json; remaining read operations must be specified before implementation.
- Normalize types while preserving external IDs, source commit, snapshot ID and original status.
- Negotiate capabilities before enabling actions.
- Phase 1: synthetic snapshots, then a controlled export from a read-only source copy.
- Phase 2: authenticated read model pinned to a tested Engine version.
- Phase 3: preview → explicit submission → external task ID. Viewing never starts a run.
- Phase 4: log cursors, timeout recovery and evidence links.

## Idempotency and uncertainty
Commands carry commandId, externalTarget, expectedRevision, operation and payload. Store requestHash and result in a durable ledger. Reusing an ID with different content returns a conflict.
The Engine does not have demonstrated upstream idempotency-key support. Write-through stays disabled until upstream deduplication/lookup or a reliable correlation-ID mechanism is implemented. An adapter alone cannot guarantee exactly-once behavior across a crash between the external side effect and recording the result.
An uncertain outcome becomes unknown and must be reconciled before retry. Differentiate timeout, unauthorized, unreachable and schema mismatch. Stale data never certifies current execution.
Allowlist operations; shell strings and arbitrary database paths are not API inputs.

## Preserve the existing program
Do not copy Forge to build a competing runtime. Add an Atlas adapter. Any necessary Engine change requires a separate Engine branch/PR, its existing tests and gates, and no live edits.
Rollback disconnects the adapter and freezes its outbox while preserving Atlas cache. The Engine data remains unchanged. Write pilots use only a synthetic test instance.
LifeDashV5 may later contribute life areas/goals, but do not introduce duplicate authorities immediately.

## Snapshot adapter (RA-008, implemented)
`adapters/hq/snapshot.py` imports a snapshot in the contract's `Snapshot` shape. It never opens hq.db, calls the Engine or runs the Engine code; a test enforces this.
- **Pinned upstream.** Only HQ-APP commits listed in `SUPPORTED_UPSTREAM` are accepted (now 1599d42). Unknown snapshot versions, source systems, entity types or Engine status values are rejected as incompatible rather than guessed.
- **Normalization.** Capabilities keep the Engine's computed state (unlocked, building, available, locked) as availability and the Engine's proof (confirmed, partial, suggested, none) as proofLevel; done tasks become evidence. Task statuses map proposed→draft, todo→ready, doing→in_progress; dev-run statuses map to Run states. Every record keeps an externalRef with the Engine's ID, revision, original status, snapshot and commit. IDs come from `import_identity("hq", type, id)`.
- **Explicit gaps.** References to items outside the snapshot become unresolved edges (`hq:capability:14`), never silent local duplicates. Dropped tasks, ideas and capabilities are excluded and counted.
- **Idempotent, all-or-nothing.** Everything is validated before writing; one transaction writes the read model (`external_refs`, `external_imports`). The same snapshot again reports all records unchanged. Older snapshots and reused snapshot IDs with different content are refused. Explicit `deleted` entities become tombstones.
- **Fixture.** `fixtures/hq-snapshot-1599d42.json`: capability titles from the Engine's built-in seed; all other entities invented and labelled synthetic.
- **Use.** `python scripts/hq_import.py <snapshot.json> [--check]`; `GET /api/hq/status`, `GET /api/hq/entities`.

## Reuse candidates found while reading HQ-APP 1599d42
- `engine/resume.py` already detects work Claude Code, Codex or the dev loop could not finish because of a usage limit (table `interruptions`: objective, files, commits, last words, reset time). Atlas should read this through the adapter for RA-015/RA-025 instead of rebuilding it.
- `engine/commands.py` keeps "Run in your terminal" steps for the owner (`shell_commands`); a natural source for Atlas's Waiting on you.
- `engine/needs.py` collects decisions only the owner can make; overlaps with Atlas reminders, so one authority must be chosen when the Engine reaches L2.


## Connected read-only boundary (RA-009, in review)
Since ADR-019 the Engine lives in this repository, so the export is here, not an HQ-APP change.
- **Export.** `engine/export.py` builds a `Snapshot` (sourceKind `engine-export`, exportVersion 1) on an in-memory copy of the Engine database: no re-ranking, no planning cycle, nothing reaches the Engine's file. Missing columns raise `SchemaMismatch`.
- **Endpoint and auth.** The hub's read function `engine.system.snapshot_export` (GET, bearer token from `runtime/hub.json`, read-only SQLite connection). The rescoring `GET /api/data` of the old dashboard is not exposed; a test proves it fails on the hub's read-only connection.
- **Atlas side.** `services/atlas/enginesync.py` performs the one GET and the RA-008 import, and records each attempt. States: connected, stale (> 60 min), offline, unauthorized, schema_mismatch (older Engine without the export, missing columns, newer export version), not_configured, never. A failed read keeps the cached read model. `GET /api/hq/connection`, `POST /api/hq/sync`; Systems › Connections › Engine read model.
- **Compatibility.** Engine exports are accepted by export version (`SUPPORTED_EXPORTS`), not by commit; export and adapter are tested together in tests/test_engine_export.py.
