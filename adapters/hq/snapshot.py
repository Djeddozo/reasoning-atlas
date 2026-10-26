"""HQ snapshot adapter (RA-008): read-only import of an HQ export into Atlas.

Atlas never opens HQ's database, calls HQ's API or runs HQ code here. It reads a
snapshot document (contracts/hq-adapter.openapi.json, schema `Snapshot`), checks
that it comes from an HQ version this adapter was tested against, normalizes it
into Atlas entities (packages/contracts) and stores a read model with external
references. Re-importing the same snapshot changes nothing.
"""
import hashlib, json, sqlite3
from datetime import datetime, timezone

from packages.contracts import import_identity, validate_graph

SOURCE = "hq"
# HQ commits whose database shapes this adapter was written and tested against.
SUPPORTED_UPSTREAM = {
    "1599d42cfb03fa381b3bbd2b8c66254b3e670e3c": "HQ-APP main, 2026-10-18 (hq/db.py, hq/skilltree.py)",
}
# Exports produced by the Engine in this repository (hq/export.py, ADR-019) carry an export version instead of a
# pinned HQ-APP commit: the export and this adapter are tested together (tests/test_engine_export.py).
SUPPORTED_EXPORTS = {1: "hq/export.py EXPORT_VERSION 1 (RA-009)"}
ENTITY_TYPES = ("capability", "goal", "task", "resource", "idea", "run", "research")
REQUIRED = {  # HQ fields the adapter needs, per entity type (hq/db.py SCHEMA at the pinned commit)
    "capability": ("title", "branch", "requires", "status", "state", "proof"),
    "goal": ("title", "horizon", "status"),
    "task": ("title", "status", "owner"),
    "resource": ("kind", "name"),
    "idea": ("title", "gate"),
    "run": ("task_id", "status"),
    "research": ("question", "status"),
}
ALLOWED = {  # HQ's own status vocabularies; anything else means an untested HQ change
    ("capability", "status"): {"auto", "unlocked", "dropped"},
    ("capability", "state"): {"unlocked", "building", "available", "locked"},
    ("capability", "proof"): {"confirmed", "partial", "suggested", "none"},
    ("capability", "branch"): {"core", "ops", "mind", "reach", "make", "product", "self"},
    ("goal", "status"): {"active", "paused", "achieved", "dropped"},
    ("goal", "horizon"): {"week", "month", "quarter", "year", "life"},
    ("task", "status"): {"proposed", "todo", "doing", "blocked", "done", "dropped"},
    ("task", "owner"): {"me", "ai", "both"},
    ("resource", "kind"): {"money", "hardware", "account", "time", "skill", "contact", "service"},
    ("idea", "gate"): {"idea", "promoted", "dropped"},
    ("run", "status"): {"running", "pr_open", "merged", "closed", "no_changes", "tests_failed", "failed", "stopped", "resolved"},
    ("research", "status"): {"working", "done", "failed"},
}
TASK_STATUS = {"proposed": "draft", "todo": "ready", "doing": "in_progress", "blocked": "blocked", "done": "done"}
RUN_STATUS = {"running": "running", "pr_open": "succeeded", "merged": "succeeded", "resolved": "succeeded",
              "no_changes": "succeeded", "closed": "cancelled", "stopped": "interrupted",
              "tests_failed": "failed", "failed": "failed"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS external_refs (
  sourceSystem TEXT NOT NULL, entityType TEXT NOT NULL, externalId TEXT NOT NULL,
  atlasId TEXT NOT NULL, contentHash TEXT NOT NULL, record TEXT NOT NULL,
  snapshotId TEXT NOT NULL, sourceCommit TEXT NOT NULL, importedAt TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (sourceSystem, entityType, externalId));
CREATE TABLE IF NOT EXISTS external_imports (
  snapshotId TEXT PRIMARY KEY, sourceSystem TEXT NOT NULL, sourceCommit TEXT NOT NULL, capturedAt TEXT NOT NULL,
  importedAt TEXT NOT NULL, inserted INTEGER NOT NULL, updated INTEGER NOT NULL, unchanged INTEGER NOT NULL,
  removed INTEGER NOT NULL, excluded INTEGER NOT NULL);
"""


class IncompatibleSnapshot(ValueError):
    """The snapshot comes from an HQ version or format this adapter was not tested with."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be a UTC timestamp ending in Z")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _ids(text):
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def check_envelope(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be a JSON object")
    if snapshot.get("schemaVersion") != 1:
        raise IncompatibleSnapshot(f"Unsupported snapshot schemaVersion {snapshot.get('schemaVersion')!r}; expected 1")
    if snapshot.get("sourceSystem") != SOURCE:
        raise IncompatibleSnapshot(f"Snapshot is from {snapshot.get('sourceSystem')!r}, not HQ")
    for field in ("snapshotId", "sourceCommit"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
            raise ValueError(f"Snapshot is missing {field}")
    if snapshot.get("sourceKind") == "engine-export":
        if snapshot.get("exportVersion") not in SUPPORTED_EXPORTS:
            raise IncompatibleSnapshot(f"Engine export version {snapshot.get('exportVersion')!r} is not supported by this Atlas "
                                       f"(supported: {sorted(SUPPORTED_EXPORTS)}); update Atlas or the Engine so both match")
    elif snapshot["sourceCommit"] not in SUPPORTED_UPSTREAM:
        raise IncompatibleSnapshot(f"HQ commit {snapshot['sourceCommit'][:12]} has not been tested with this adapter; "
                                   "add it to SUPPORTED_UPSTREAM only after checking HQ's schema")
    _timestamp(snapshot.get("capturedAt"), "capturedAt")
    if not isinstance(snapshot.get("entities"), list):
        raise ValueError("Snapshot entities must be a list")


def _check_entity(entity, index):
    where = f"entities[{index}]"
    if not isinstance(entity, dict):
        raise ValueError(f"{where} must be an object")
    kind = entity.get("entityType")
    if kind not in ENTITY_TYPES:
        raise ValueError(f"{where}: unknown entityType {kind!r}")
    for field in ("externalId", "revision"):
        if not isinstance(entity.get(field), str) or not entity[field]:
            raise ValueError(f"{where}: missing {field}")
    data = entity.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{where}: data must be an object")
    if entity.get("deleted"):
        return
    for field in REQUIRED[kind]:
        if field not in data or data[field] is None:
            raise ValueError(f"{where} ({kind} {entity['externalId']}): missing HQ field {field!r}")
    for (k, field), allowed in ALLOWED.items():
        if k == kind and data[field] not in allowed:
            raise IncompatibleSnapshot(f"{where} ({kind} {entity['externalId']}): unknown {field} {data[field]!r}")


def normalize(snapshot):
    """Return {"records": [...], "tombstones": [...], "excluded": n} or raise without side effects."""
    check_envelope(snapshot)
    seen = set()
    for index, entity in enumerate(snapshot["entities"]):
        _check_entity(entity, index)
        key = (entity["entityType"], entity["externalId"])
        if key in seen:
            raise ValueError(f"Duplicate {key[0]} {key[1]} in snapshot")
        seen.add(key)
    live = {k: {} for k in ENTITY_TYPES}
    tombstones = []
    for entity in snapshot["entities"]:
        if entity.get("deleted"):
            tombstones.append((entity["entityType"], entity["externalId"]))
        else:
            live[entity["entityType"]][entity["externalId"]] = entity
    # Dropped tasks, ideas and capabilities are history, not part of the current read model.
    excluded = 0
    for kind, field, value in (("task", "status", "dropped"), ("idea", "gate", "dropped"), ("capability", "status", "dropped")):
        for eid in [i for i, e in live[kind].items() if e["data"][field] == value]:
            del live[kind][eid]
            excluded += 1

    stamp = snapshot["capturedAt"]
    uid = lambda kind, eid: import_identity(SOURCE, kind, eid)
    ref = lambda kind, eid: f"hq:{kind}:{eid}"
    records, edges = [], []

    def base(kind, entity, entity_type, original=None):
        updated = entity["data"].get("updated") or stamp
        return {"id": uid(kind, entity["externalId"]), "entityType": entity_type, "schemaVersion": 1,
                "createdAt": min(updated, stamp), "updatedAt": stamp, "revision": 1,
                "externalRef": {"system": SOURCE, "entityType": kind, "externalId": entity["externalId"],
                                "revision": entity["revision"], "snapshotId": snapshot["snapshotId"],
                                "sourceCommit": snapshot["sourceCommit"], "originalStatus": original}}

    def edge(from_id, kind, eid, relation, edge_key):
        present = eid in live[kind]
        edges.append({"id": uid("edge", edge_key), "entityType": "Edge", "schemaVersion": 1, "createdAt": stamp,
                      "updatedAt": stamp, "revision": 1, "fromId": from_id,
                      "toId": uid(kind, eid) if present else ref(kind, eid), "relation": relation,
                      "provenanceIds": [], "status": "established" if present else "unresolved"})

    done_tasks = {}
    for eid, e in live["task"].items():
        if e["data"]["status"] == "done" and e["data"].get("capability_id") is not None:
            done_tasks.setdefault(str(e["data"]["capability_id"]), []).append(uid("task", eid))
    for eid, e in live["capability"].items():
        d = e["data"]
        rec = base("capability", e, "Capability", d["status"])
        requires = _ids(d["requires"])
        rec.update(title=d["title"], requiresIds=[uid("capability", r) for r in requires if r in live["capability"]],
                   evidenceIds=sorted(done_tasks.get(eid, [])), proofLevel=d["proof"], availability=d["state"],
                   branch=d["branch"], description=d.get("description", ""))
        records.append(rec)
        for r in requires:
            if r not in live["capability"]:
                edge(rec["id"], "capability", r, "depends_on", f"capability:{eid}:requires:{r}")
    for eid, e in live["goal"].items():
        d = e["data"]
        rec = base("goal", e, "Goal", d["status"])
        rec.update(title=d["title"], horizon=d["horizon"], successCriteria=d.get("why", ""), status=d["status"])
        records.append(rec)
    for eid, e in live["task"].items():
        d = e["data"]
        rec = base("task", e, "Task", d["status"])
        rec.update(title=d["title"], acceptanceCriteria=d.get("description", ""), owner=d["owner"],
                   status=TASK_STATUS[d["status"]])
        records.append(rec)
        if d.get("capability_id") is not None:
            edge(rec["id"], "capability", str(d["capability_id"]), "builds", f"task:{eid}:capability")
        if d.get("goal_id") is not None:
            edge(rec["id"], "goal", str(d["goal_id"]), "serves", f"task:{eid}:goal")
        for dep in _ids(d.get("depends_on")):
            edge(rec["id"], "task", dep, "depends_on", f"task:{eid}:depends:{dep}")
    for eid, e in live["resource"].items():
        d = e["data"]
        rec = base("resource", e, "Resource")
        observed = d.get("updated")
        rec.update(title=d["name"], resourceType=d["kind"], availability="available",
                   attributes={"details": d.get("details", ""), "value": d.get("value"), "unit": d.get("unit", "")},
                   observedAt=_timestamp(observed, "resource updated") if observed else None)
        records.append(rec)
    for eid, e in live["idea"].items():
        d = e["data"]
        rec = base("idea", e, "KnowledgeItem", d["gate"])
        rec.update(title=d["title"], body=d.get("description", ""), kind="note", provenanceIds=[], epistemicStatus="proposed")
        records.append(rec)
        if d.get("capability_id") is not None:
            edge(rec["id"], "capability", str(d["capability_id"]), "builds_on", f"idea:{eid}:capability")
    for eid, e in live["run"].items():
        d = e["data"]
        task = str(d["task_id"])
        rec = base("run", e, "Run", d["status"])
        rec.update(taskId=uid("task", task) if task in live["task"] else ref("task", task), provider="hq-devloop",
                   status=RUN_STATUS[d["status"]], checkpoint=d.get("head") or "", artifactIds=[])
        records.append(rec)
    for eid, e in live["research"].items():
        d = e["data"]
        answer = d.get("answer") or {}
        rec = base("research", e, "KnowledgeItem", d["status"])
        rec.update(title=d["question"], body=answer.get("summary", "") if isinstance(answer, dict) else "",
                   kind="question", provenanceIds=[], epistemicStatus="proposed")
        records.append(rec)
    validate_graph(records, edges)
    return {"records": records + edges, "tombstones": tombstones, "excluded": excluded}


def migrate(db):
    db.executescript(SCHEMA)


def _hash(record):
    stable = {k: v for k, v in record.items() if k not in ("updatedAt",)}
    stable["externalRef"] = {k: v for k, v in record.get("externalRef", {}).items() if k != "snapshotId"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def import_snapshot(db, snapshot):
    """Validate everything first, then write the read model in one transaction. Returns an ImportResult."""
    normalized = normalize(snapshot)
    migrate(db)
    sid = snapshot["snapshotId"]
    if db.execute("SELECT 1 FROM external_imports WHERE snapshotId=?", (sid,)).fetchone():
        previous = db.execute("SELECT capturedAt, sourceCommit FROM external_imports WHERE snapshotId=?", (sid,)).fetchone()
        if tuple(previous) != (snapshot["capturedAt"], snapshot["sourceCommit"]):
            raise ValueError(f"Snapshot {sid} was imported before with different content")
    latest = db.execute("SELECT MAX(capturedAt) FROM external_imports WHERE sourceSystem=?", (SOURCE,)).fetchone()[0]
    if latest and snapshot["capturedAt"] < latest:
        raise ValueError(f"Snapshot {sid} is older than the last import ({latest}); refusing to go back in time")
    counts = dict(inserted=0, updated=0, unchanged=0, removed=0)
    at = now()
    with db:
        for record in normalized["records"]:
            ext = record.get("externalRef") or {"entityType": "edge", "externalId": record["id"]}
            key = (SOURCE, ext["entityType"], ext["externalId"])
            digest = _hash(record)
            row = db.execute("SELECT contentHash, deleted FROM external_refs WHERE sourceSystem=? AND entityType=? AND externalId=?", key).fetchone()
            if row and row[0] == digest and not row[1]:
                counts["unchanged"] += 1
                continue
            counts["updated" if row else "inserted"] += 1
            db.execute("INSERT INTO external_refs VALUES (?,?,?,?,?,?,?,?,?,0) ON CONFLICT(sourceSystem, entityType, externalId) DO UPDATE SET "
                       "atlasId=excluded.atlasId, contentHash=excluded.contentHash, record=excluded.record, snapshotId=excluded.snapshotId, "
                       "sourceCommit=excluded.sourceCommit, importedAt=excluded.importedAt, deleted=0",
                       (*key, record["id"], digest, json.dumps(record, sort_keys=True), sid, snapshot["sourceCommit"], at))
        for kind, eid in normalized["tombstones"]:
            cur = db.execute("UPDATE external_refs SET deleted=1, snapshotId=?, importedAt=? WHERE sourceSystem=? AND entityType=? AND externalId=? AND deleted=0",
                             (sid, at, SOURCE, kind, eid))
            counts["removed"] += cur.rowcount
        db.execute("INSERT INTO external_imports VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(snapshotId) DO UPDATE SET importedAt=excluded.importedAt, "
                   "inserted=excluded.inserted, updated=excluded.updated, unchanged=excluded.unchanged, removed=excluded.removed, excluded=excluded.excluded",
                   (sid, SOURCE, snapshot["sourceCommit"], snapshot["capturedAt"], at, counts["inserted"], counts["updated"],
                    counts["unchanged"], counts["removed"], normalized["excluded"]))
    return {"snapshotId": sid, **counts, "excluded": normalized["excluded"]}


def read_model(db):
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT record FROM external_refs WHERE sourceSystem=? AND deleted=0", (SOURCE,)).fetchall()
    return [json.loads(r["record"]) for r in rows]


def health(db):
    """Adapter health in the shape of the contract's Health schema, plus read-model counts."""
    migrate(db)
    last = db.execute("SELECT snapshotId, sourceCommit, importedAt, capturedAt FROM external_imports WHERE sourceSystem=? "
                      "ORDER BY capturedAt DESC, importedAt DESC LIMIT 1", (SOURCE,)).fetchone()
    counts = dict(db.execute("SELECT entityType, COUNT(*) FROM external_refs WHERE sourceSystem=? AND deleted=0 GROUP BY entityType", (SOURCE,)).fetchall())
    return {"status": "ok" if last else "offline", "schemaVersion": 1, "mode": "snapshot",
            "upstreamCommit": last[1] if last else None, "lastSyncAt": last[2] if last else None,
            "snapshotId": last[0] if last else None, "capturedAt": last[3] if last else None,
            "capabilities": {"readSnapshot": True, "writeProposals": False}, "counts": counts,
            "supportedUpstream": sorted(SUPPORTED_UPSTREAM), "supportedExports": sorted(SUPPORTED_EXPORTS)}
