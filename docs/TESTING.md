# Validation and evidence

## Blueprint checks
The supplied validator checks required files, local Markdown links, JSON, task IDs and dependencies. With --require-reference it additionally requires the original PNG and validates its hash, dimensions and inspection status.
No runtime application is present; build, E2E, implementation screenshot comparison and Pi tests have not been run.

## Implementation gates
| Gate | Dataset / scenario | Required evidence |
|---|---|---|
| Storage | Save note, terminate process, restart | Same text/ID; transaction rollback on error |
| Graph | 200 nodes / 400 edges with fixed seed | Selection/filter/keyboard access; no lost edges |
| Dependencies | A depends B, B depends A | Rejection with path; prior dataset unchanged |
| AI | Conflicting sources and injected instructions | Suggestions with citations; source instructions not executed |
| Engine import | Same snapshot twice | Same entity counts and external IDs |
| Engine commands | Timeout/crash before and after side effect and ledger save | Unknown/reconcile state; no duplicate task |
| Offline | Pi unavailable during save/submit | Local text intact, pending visible |
| Sync | Two edits using the same revision | Both versions recoverable, no silent loss |
| Vision | Save question, fail provider, restart | Question restored without fabricated answer |
| Handoff | Interrupt task; new agent sees only repository | Reproducible next step without previous chat |
| Restore | Import backup into empty test directory | IDs, hashes, relationships and questions match |
| Pi | ARM64 host with recorded resource budget | Startup, health, shutdown, update and rollback logs |
| Visual | Original reference + fixed viewport/fixture | Annotated comparison, deviations documented |
| English | Audit UI, docs, source, comments, branches and issues | English artifacts; explicit provenance exceptions |

## Proposed performance targets
On a recorded Windows test host: local search p95 < 300 ms with 10,000 notes; selection < 100 ms with 200 nodes; graph interaction target 50+ fps; first local content < 2 s.
Record hardware and measurement method. These targets are not measured claims.
A large local LLM on Pi is not a required dependency. Measure idle RAM/CPU and define deployment limits on actual hardware.

## Evidence format
STATE/evidence/RA-xxx.md records commit, environment, exact check, result, artifacts, failures and limitations. Use synthetic screenshot data. Regular tests must not require paid models or live Engine; run live integration only as a separately configured test.
