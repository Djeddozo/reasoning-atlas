# Knowledge, skilltree, capability and memory model

## One identity, multiple views
The graph consists of typed nodes and edges. A skilltree is a dependency projection; the universe is a spatial projection; a project groups references. Layout coordinates are presentation data, not semantic meaning.
Use UUIDs for local entities. Preserve namespaced external IDs such as hq:capability:12 through an identity map. Matching titles do not prove identical entities.

## Entities
All records include id, schemaVersion, createdAt, updatedAt and revision.
| Entity | Additional required fields | Meaning |
|---|---|---|
| KnowledgeItem | title, body, kind, provenanceIds, epistemicStatus | Note, concept, question or hypothesis |
| Source | uri or attachmentId, retrievedAt, contentHash, sourceType | Provenance; unknown author/date stays unknown |
| Claim | statement, evidenceIds, counterEvidenceIds, status | proposed/supported/disputed/retracted |
| Experiment | hypothesisId, method, inputIds, status, result, conclusion | Reproducible investigation, including failed/inconclusive outcomes |
| Goal | title, horizon, successCriteria, status | Desired outcome, optional parentGoalId |
| Project | title, goalIds, nodeIds, status | Bounded work across domains |
| Skill | title, domain, proficiency, evidenceIds | Human learned ability; level may be unknown |
| Capability | title, requiresIds, evidenceIds, proofLevel, availability | Demonstrable outcome enabled by combined resources |
| Resource | title, resourceType, attributes, availability, observedAt | GPU, time, material, account or expertise |
| Task | title, acceptanceCriteria, owner, status, externalRef | Executable step with evidence |
| Run | taskId, provider, status, checkpoint, artifactIds | One execution attempt, not the task itself |
| Decision | question, choice, rationale, alternatives, sourceIds | Decision that can be superseded |
| Memory | category, text, sourceIds, confidenceLabel, validFrom, expiresAt | Episode, fact, preference, procedure or lesson |
| VisionQuestion | question, status, visionRevision, answerIds | Persistent question independent of provider availability |
| Answer | questionId, text, citationIds, provider, generatedAt, status | Draft until reviewed |
| Attachment | contentHash, mediaType, sizeBytes, localPath | File outside Git with controlled export |
| Edge | fromId, toId, relation, provenanceIds, status | Typed connection with evidence |

## Relationships and invariants
supports/contradicts connect claims and evidence; depends_on points from dependent to prerequisite; enables links resources/skills to capabilities; contributes_to links work to goals; derived_from preserves provenance; uses links projects to reusable assets.
- No self-loop or cycle in capability/task depends_on. General associations may cycle.
- Missing external targets remain unresolved rather than silently becoming local duplicates.
- Every AI relationship carries origin=ai, available provider/model metadata, a concise explanation and reviewStatus=proposed.
- Verified evidence is distinct from self-report. Retraction triggers re-evaluation.
- An installed library alone does not prove expertise or capability.
- Optional motivational XP must not replace evidence.
- Upserts match sourceSystem + entityType + externalId; repeated imports are idempotent.
- Store UTC timestamps; render in the configured user zone (initially Europe/Paris). Unknown dates remain null.

## Memory layers
Raw input remains preserved. Curated memory holds reviewed facts/preferences. Episodic memory records events and results. Procedural memory stores reusable methods. Working context is a temporary session selection.
Summaries reference their source records and have separate versions. Retrieval cites entity ID and passage. Semantic indexes are rebuildable; losing embeddings must not erase knowledge. Expired preferences are not presented as current instructions.
Store useful explanations, assumptions, decisions and evidence, not hidden internal chain-of-thought.

## Storage proposal
Atlas owns a SQLite database with foreign keys and transactions: nodes, edges, sources, versions, external_refs, events, outbox, questions, answers and view_state. Validate JSON attributes where used. Large attachments use content-addressed storage.
Implemented so far (RA-005): `sources` with SHA-256 identity, original bytes in `runtime/sources/` and processing states pending/processing/done/failed/invalid; `note_sources` links notes to the source of their exact text (services/atlas/intake.py).
The Engine remains authoritative for its tasks, runs, capabilities and memory. Atlas stores a read model and external references alongside its own knowledge.
Schema changes require migration versions, backup, dry run and recovery test. Soft deletion uses tombstones and restoration; permanent deletion/retention require a separate design. Exports preserve IDs and schemaVersion.

## Example
A language-coach project uses Dutch-language proficiency, a GPU and existing transcription software. Spoken-practice capability requires transcription and exercise design. A paper may support a claim about feedback without proving the capability works. An actual experiment and result provide capability evidence.
