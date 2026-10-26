"""Read-only snapshot export of the Engine's plan for Atlas (RA-009).

Produces a document in the `Snapshot` shape of contracts/hq-adapter.openapi.json, which
adapters/hq/snapshot.py imports into Atlas's read model. The export:

- runs on an in-memory copy of the database, because computing skill-tree state seeds and plants
  nodes; nothing it does can reach the Engine's own file (no re-ranking, no planning cycle);
- reads only the columns listed in COLUMNS and refuses with SchemaMismatch when one is missing,
  so an Engine upgrade that renames or drops a column is reported instead of exported half-filled;
- converts the Engine's local timestamps to UTC (`...Z`), as the contract requires.
"""
import hashlib, json, sqlite3
from contextlib import closing
from datetime import datetime, timezone

EXPORT_VERSION = 1
# Entity type → (table, columns read). Keep in step with REQUIRED in adapters/hq/snapshot.py.
COLUMNS = {
    "goal": ("goals", ("id", "title", "horizon", "why", "status", "created", "updated")),
    "task": ("tasks", ("id", "title", "description", "goal_id", "owner", "status", "capability_id", "depends_on",
                       "impact", "effort_h", "confidence", "score", "created", "updated")),
    "resource": ("resources", ("id", "kind", "name", "details", "value", "unit", "updated", "status")),
    "idea": ("ideas", ("id", "title", "category", "description", "gate", "capability_id", "score", "created", "updated")),
    "run": ("dev_runs", ("id", "task_id", "branch", "status", "head", "pr_url", "summary", "started")),
    "research": ("research_notes", ("id", "question", "status", "answer", "idea_id", "created", "finished")),
    "capability": ("capabilities", ("id", "title", "description", "branch", "requires", "status", "created", "updated")),
}


class SchemaMismatch(ValueError):
    """The Engine database lacks columns this export reads: the Engine and Atlas versions differ."""


def utc(value):
    """Engine timestamps are local ISO strings without an offset; return UTC ending in Z, or None."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()  # the Engine writes local time on the machine it runs on
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def check_schema(con):
    """Return {table: [missing columns]} for every table the export reads (empty when compatible)."""
    missing = {}
    for table, columns in COLUMNS.values():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        gap = [c for c in columns if c not in have]
        if gap:
            missing[table] = gap
    return missing


def _entity(kind, eid, data):
    revision = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"externalId": str(eid), "entityType": kind, "revision": revision, "data": data}


def snapshot(con, commit="", captured=None):
    """Build a Snapshot from an open Engine connection without changing it."""
    from engine import skilltree
    missing = check_schema(con)
    if missing:
        raise SchemaMismatch("The Engine database differs from what this export reads: "
                             + "; ".join(f"{t} lacks {', '.join(c)}" for t, c in sorted(missing.items())))
    captured = captured or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with closing(sqlite3.connect(":memory:")) as copy:
        con.backup(copy)
        copy.row_factory = sqlite3.Row
        entities = []
        for kind, (table, columns) in COLUMNS.items():
            if kind == "capability":
                continue
            for row in copy.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"):
                data = {c: row[c] for c in columns if c != "id"}
                for field in ("created", "updated", "started", "finished"):
                    if field in data:
                        data[field] = utc(data[field])
                if kind == "resource":
                    if data.pop("status") == "retired":  # retired resources are history, like dropped tasks
                        entities.append({"externalId": str(row["id"]), "entityType": kind, "revision": "retired",
                                         "data": {}, "deleted": True})
                        continue
                if kind == "run" and data["task_id"] is None:  # not attributable to a task; left out
                    continue
                if kind == "research":
                    try:
                        data["answer"] = json.loads(data["answer"] or "{}")
                    except ValueError:
                        data["answer"] = {}
                if kind == "task":
                    data["depends_on"] = data["depends_on"] or ""
                entities.append(_entity(kind, row["id"], data))
        caps = skilltree.nodes(copy)  # computed state; seeds missing nodes on the copy only
        skilltree.proof(copy, caps)
        for cid, c in sorted(caps.items()):
            data = {"title": c["title"], "description": c.get("description") or "", "branch": c["branch"],
                    "requires": ",".join(str(r) for r in c["requires"]), "status": c["status"],
                    "state": c["state"], "proof": c["proof"], "created": utc(c.get("created")), "updated": utc(c.get("updated"))}
            entities.append(_entity("capability", cid, data))
    body = json.dumps(entities, sort_keys=True, default=str).encode()
    stamp = captured.replace("-", "").replace(":", "")
    return {"schemaVersion": 1, "sourceSystem": "hq", "sourceKind": "engine-export", "exportVersion": EXPORT_VERSION,
            "snapshotId": f"engine-{stamp}-{hashlib.sha256(body).hexdigest()[:12]}", "sourceCommit": commit or "unknown",
            "capturedAt": captured, "readOnly": True, "entities": entities}


def counts(con):
    """Rows per entity type as the Engine sees them, for comparing an export with its database (audit)."""
    out = {}
    for kind, (table, _) in COLUMNS.items():
        out[kind] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return out
