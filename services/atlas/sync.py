"""Windows–Pi offline synchronization of notes (RA-013, ADR-009).

One authority, no multi-master. The Pi's hub holds the authoritative copy of the synced notes in its own
database (runtime/sync-authority.db, never the Engine's). A PC keeps working offline: every edit or delete
becomes a command in a durable outbox in the PC's Atlas database, with its own id and the revision it was
based on. Syncing sends the outbox in order and then pulls what changed since the last cursor.

- Revisions: every note on the authority carries a revision that grows by one per accepted change.
- Tombstones: a delete is a revision with deleted=1; the body is kept, so it can be restored.
- Idempotent: the authority stores the result of every command id it accepted. A command sent again
  (the acknowledgment was lost, the PC restarted) returns the stored result and changes nothing.
- Conflicts: a command whose base revision is not the current one is refused, never applied over the
  newer version. Both versions are kept (authority: sync_conflicts; PC: the outbox row) and shown under
  Systems › Connections until the owner keeps his version or theirs. No silent last-write-wins.
- Credentials: the PC only holds its own hub session (runtime/hub.json). Commands, the outbox and the
  authority's answers never contain a token, and the authority needs no upstream credentials either.
"""
import json, uuid
from datetime import datetime, timezone

MAX_BODY = 20000
MAX_BATCH = 200
OPS = ("upsert", "delete")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _note_id(value):
    return str(uuid.UUID(str(value)))


def _body(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_BODY:
        raise ValueError("A note must contain 1–20,000 characters")
    return value


# The authority (the Pi)

def migrate_authority(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS sync_notes (id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0, updatedAt TEXT NOT NULL, deviceId TEXT NOT NULL, seq INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS sync_commands (commandId TEXT PRIMARY KEY, deviceId TEXT NOT NULL, noteId TEXT NOT NULL,
        op TEXT NOT NULL, result TEXT NOT NULL, appliedAt TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sync_conflicts (id INTEGER PRIMARY KEY AUTOINCREMENT, commandId TEXT NOT NULL UNIQUE,
        deviceId TEXT NOT NULL, noteId TEXT NOT NULL, baseRevision INTEGER, currentRevision INTEGER NOT NULL,
        mine TEXT, theirs TEXT, createdAt TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    if not db.execute("SELECT 1 FROM sync_meta WHERE key='authorityId'").fetchone():
        db.execute("INSERT INTO sync_meta VALUES ('authorityId', ?)", (str(uuid.uuid4()),))
        db.execute("INSERT INTO sync_meta VALUES ('seq', '0')")


def authority_id(db):
    return db.execute("SELECT value FROM sync_meta WHERE key='authorityId'").fetchone()[0]


def _next_seq(db):
    seq = int(db.execute("SELECT value FROM sync_meta WHERE key='seq'").fetchone()[0]) + 1
    db.execute("UPDATE sync_meta SET value=? WHERE key='seq'", (str(seq),))
    return seq


def _state(row):
    return None if row is None else {"id": row[0], "body": row[1], "revision": row[2], "deleted": bool(row[3]),
                                     "updatedAt": row[4], "deviceId": row[5], "seq": row[6]}


def _current(db, note_id):
    return _state(db.execute("SELECT id, body, revision, deleted, updatedAt, deviceId, seq FROM sync_notes WHERE id=?",
                             (note_id,)).fetchone())


def _validate_command(cmd):
    if not isinstance(cmd, dict):
        raise ValueError("Each command must be an object")
    if cmd.get("op") not in OPS:
        raise ValueError("op must be upsert or delete")
    base = cmd.get("baseRevision")
    if base is not None and (not isinstance(base, int) or isinstance(base, bool) or base < 1):
        raise ValueError("baseRevision must be a positive integer or null for a new note")
    return {"commandId": _note_id(cmd.get("commandId")), "noteId": _note_id(cmd.get("noteId")), "op": cmd["op"],
            "baseRevision": base, "body": _body(cmd.get("body")) if cmd["op"] == "upsert" else None}


def apply(db, device_id, commands):
    """Apply a batch in order, all in the caller's transaction. Returns one result per command.

    A command id seen before returns its stored result with duplicate=True (no second change)."""
    if not isinstance(device_id, str) or not 1 <= len(device_id) <= 80:
        raise ValueError("deviceId is required")
    if not isinstance(commands, list) or len(commands) > MAX_BATCH:
        raise ValueError(f"Send a list of at most {MAX_BATCH} commands")
    commands = [_validate_command(c) for c in commands]  # all or nothing: refuse the batch before writing
    results, blocked = [], set()  # a note with a conflict in this batch: its later commands were based on the refused one
    for cmd in commands:
        seen = db.execute("SELECT result FROM sync_commands WHERE commandId=?", (cmd["commandId"],)).fetchone()
        if seen:
            results.append({**json.loads(seen[0]), "duplicate": True})
            if results[-1]["status"] == "conflict":
                blocked.add(cmd["noteId"])
            continue
        cur = _current(db, cmd["noteId"])
        current_rev = cur["revision"] if cur else None
        held = cmd["noteId"] in blocked
        if held or cmd["baseRevision"] != current_rev or (cmd["op"] == "delete" and (cur is None or cur["deleted"])):
            blocked.add(cmd["noteId"])
            if cur is None:
                result = {"commandId": cmd["commandId"], "noteId": cmd["noteId"], "status": "conflict",
                          "reason": "The note does not exist on the authority", "current": None}
            else:
                db.execute("INSERT INTO sync_conflicts (commandId, deviceId, noteId, baseRevision, currentRevision, mine, theirs, createdAt) "
                           "VALUES (?,?,?,?,?,?,?,?)", (cmd["commandId"], device_id, cmd["noteId"], cmd["baseRevision"],
                                                        current_rev, cmd["body"], cur["body"], now()))
                result = {"commandId": cmd["commandId"], "noteId": cmd["noteId"], "status": "conflict",
                          "reason": "waits for the earlier conflict on this note" if held
                          else "already deleted" if cmd["op"] == "delete" and cmd["baseRevision"] == current_rev
                          else f"based on revision {cmd['baseRevision']}, the authority has revision {current_rev}",
                          "current": {k: cur[k] for k in ("body", "revision", "deleted", "updatedAt", "deviceId")}}
        else:
            revision = (current_rev or 0) + 1
            body = cmd["body"] if cmd["op"] == "upsert" else cur["body"]  # a tombstone keeps the last body
            seq = _next_seq(db)
            db.execute("INSERT INTO sync_notes VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body, "
                       "revision=excluded.revision, deleted=excluded.deleted, updatedAt=excluded.updatedAt, "
                       "deviceId=excluded.deviceId, seq=excluded.seq",
                       (cmd["noteId"], body, revision, int(cmd["op"] == "delete"), now(), device_id, seq))
            result = {"commandId": cmd["commandId"], "noteId": cmd["noteId"], "status": "applied", "revision": revision,
                      "deleted": cmd["op"] == "delete"}
        db.execute("INSERT INTO sync_commands VALUES (?,?,?,?,?,?)", (cmd["commandId"], device_id, cmd["noteId"], cmd["op"],
                                                                     json.dumps(result), now()))
        results.append({**result, "duplicate": False})
    return results


def changes(db, since=0, limit=500):
    """Notes changed after cursor `since`, tombstones included, in order. The cursor is the last seq returned."""
    since = int(since or 0)
    rows = [_state(r) for r in db.execute("SELECT id, body, revision, deleted, updatedAt, deviceId, seq FROM sync_notes "
                                          "WHERE seq > ? ORDER BY seq LIMIT ?", (since, int(limit)))]
    head = int(db.execute("SELECT value FROM sync_meta WHERE key='seq'").fetchone()[0])
    return {"authorityId": authority_id(db), "notes": rows, "cursor": rows[-1]["seq"] if rows else since,
            "more": bool(rows) and rows[-1]["seq"] < head}


# The client (the PC)

def migrate_client(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS sync_outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, commandId TEXT NOT NULL UNIQUE,
        noteId TEXT NOT NULL, op TEXT NOT NULL, body TEXT, baseRevision INTEGER, status TEXT NOT NULL DEFAULT 'pending',
        createdAt TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, lastError TEXT, result TEXT);
    CREATE TABLE IF NOT EXISTS sync_mirror (id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL,
        deleted INTEGER NOT NULL, updatedAt TEXT, deviceId TEXT);
    CREATE TABLE IF NOT EXISTS sync_client (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    if not db.execute("SELECT 1 FROM sync_client WHERE key='deviceId'").fetchone():
        db.execute("INSERT INTO sync_client VALUES ('deviceId', ?)", (f"pc-{uuid.uuid4().hex[:12]}",))


def _get(db, key, default=None):
    row = db.execute("SELECT value FROM sync_client WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set(db, key, value):
    db.execute("INSERT INTO sync_client VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def _expected_revision(db, note_id):
    """The revision the authority will have once this PC's earlier open commands for the note are accepted."""
    row = db.execute("SELECT revision FROM sync_mirror WHERE id=?", (note_id,)).fetchone()
    ahead = db.execute("SELECT COUNT(*) FROM sync_outbox WHERE noteId=? AND status IN ('pending','sent')", (note_id,)).fetchone()[0]
    rev = (row[0] if row else 0) + ahead
    return rev or None


def enqueue(db, note_id, op, body=None):
    """Record an edit (upsert) or delete in the outbox. Works offline; returns the command id."""
    if op not in OPS:
        raise ValueError("op must be upsert or delete")
    note_id = _note_id(note_id)
    if op == "upsert":
        body = _body(body)
    else:
        body = None
        local = view(db, note_id)
        if local is None or local["deleted"]:
            raise KeyError("Unknown note")
    command_id = str(uuid.uuid4())
    db.execute("INSERT INTO sync_outbox (commandId, noteId, op, body, baseRevision, createdAt) VALUES (?,?,?,?,?,?)",
               (command_id, note_id, op, body, _expected_revision(db, note_id), now()))
    return command_id


def view(db, note_id):
    for note in notes(db, include_deleted=True):
        if note["id"] == note_id:
            return note
    return None


def notes(db, include_deleted=False):
    """The authority's last known notes with this PC's open edits on top, each marked synced, pending or conflict."""
    out = {r[0]: {"id": r[0], "body": r[1], "revision": r[2], "deleted": bool(r[3]), "updatedAt": r[4], "state": "synced"}
           for r in db.execute("SELECT id, body, revision, deleted, updatedAt FROM sync_mirror")}
    for cid, nid, op, body, status, created in db.execute(
            "SELECT commandId, noteId, op, body, status, createdAt FROM sync_outbox WHERE status IN ('pending','sent','conflict') ORDER BY seq"):
        base = out.get(nid, {"id": nid, "revision": None, "body": "", "updatedAt": created})
        out[nid] = {**base, "body": body if op == "upsert" else base.get("body", ""), "deleted": op == "delete",
                    "state": "conflict" if status == "conflict" else "pending", "commandId": cid}
    items = sorted(out.values(), key=lambda n: n.get("updatedAt") or "", reverse=True)
    return items if include_deleted else [n for n in items if not n["deleted"]]


def flush(db_connect, transport):
    """Send the open outbox in order, then pull. `db_connect()` gives one transaction per use;
    `transport` has push(deviceId, commands) and pull(since). A lost connection leaves commands 'sent';
    the next flush sends the same command ids again and the authority answers from its record."""
    with db_connect() as db:
        device = _get(db, "deviceId")
        rows = db.execute("SELECT commandId, noteId, op, body, baseRevision FROM sync_outbox WHERE status IN ('pending','sent') "
                          "ORDER BY seq LIMIT ?", (MAX_BATCH,)).fetchall()
        commands = [{"commandId": r[0], "noteId": r[1], "op": r[2], "body": r[3], "baseRevision": r[4]} for r in rows]
        if commands:  # durable before the request: a crash after this point resends, never forgets
            db.executemany("UPDATE sync_outbox SET status='sent', attempts=attempts+1 WHERE commandId=?", [(c["commandId"],) for c in commands])
        _set(db, "lastAttemptAt", now())
    try:
        results = transport.push(device, commands) if commands else []
        with db_connect() as db:
            for res in results:
                _acknowledge(db, res)
        pulled = 0
        while True:
            with db_connect() as db:
                since = int(_get(db, "cursor", "0"))
            page = transport.pull(since)
            with db_connect() as db:
                known = _get(db, "authorityId")
                if known and known != page["authorityId"]:
                    raise SyncError("This hub holds a different notes database than the one this PC synced with; nothing was changed")
                _set(db, "authorityId", page["authorityId"])
                for n in page["notes"]:
                    db.execute("INSERT INTO sync_mirror VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body, "
                               "revision=excluded.revision, deleted=excluded.deleted, updatedAt=excluded.updatedAt, deviceId=excluded.deviceId "
                               "WHERE excluded.revision >= sync_mirror.revision",
                               (n["id"], n["body"], n["revision"], int(n["deleted"]), n["updatedAt"], n["deviceId"]))
                _set(db, "cursor", page["cursor"])
                pulled += len(page["notes"])
            if not page.get("more"):
                break
        with db_connect() as db:
            _set(db, "lastSuccessAt", now())
            _set(db, "lastError", "")
        return {"sent": len(commands), "pulled": pulled, **status(db_connect)}
    except SyncError as exc:
        with db_connect() as db:
            _set(db, "lastError", str(exc))
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:  # offline, timeout, refused: the outbox stays as it is
        with db_connect() as db:
            _set(db, "lastError", f"The hub could not be reached; your changes wait in the outbox ({type(exc).__name__})")
        return {"sent": 0, "pulled": 0, **status(db_connect)}


class SyncError(Exception):
    pass


def _acknowledge(db, res):
    cid = res.get("commandId")
    if res.get("status") == "applied":
        row = db.execute("SELECT noteId, op, body FROM sync_outbox WHERE commandId=?", (cid,)).fetchone()
        db.execute("UPDATE sync_outbox SET status='acked', result=?, lastError=NULL WHERE commandId=?", (json.dumps(res), cid))
        if row:
            body = row[2] if row[1] == "upsert" else (db.execute("SELECT body FROM sync_mirror WHERE id=?", (row[0],)).fetchone() or [""])[0]
            db.execute("INSERT INTO sync_mirror VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body, "
                       "revision=excluded.revision, deleted=excluded.deleted, updatedAt=excluded.updatedAt WHERE excluded.revision >= sync_mirror.revision",
                       (row[0], body, res["revision"], int(res.get("deleted", False)), now(), None))
    elif res.get("status") == "conflict":
        db.execute("UPDATE sync_outbox SET status='conflict', result=?, lastError=? WHERE commandId=?",
                   (json.dumps(res), res.get("reason"), cid))
        # later open commands for the same note were based on this one; hold them as conflicts too
        row = db.execute("SELECT noteId, seq FROM sync_outbox WHERE commandId=?", (cid,)).fetchone()
        if row:
            db.execute("UPDATE sync_outbox SET status='conflict', lastError='waits for the earlier conflict on this note' "
                       "WHERE noteId=? AND seq>? AND status IN ('pending','sent')", (row[0], row[1]))


def conflicts(db):
    """Every conflict with both versions: this PC's (mine) and the authority's (theirs)."""
    out = []
    for cid, nid, op, body, base, created, result in db.execute(
            "SELECT commandId, noteId, op, body, baseRevision, createdAt, result FROM sync_outbox WHERE status='conflict' ORDER BY seq"):
        res = json.loads(result) if result else {}
        theirs = res.get("current")
        if theirs is None:
            row = db.execute("SELECT body, revision, deleted, updatedAt FROM sync_mirror WHERE id=?", (nid,)).fetchone()
            theirs = row and {"body": row[0], "revision": row[1], "deleted": bool(row[2]), "updatedAt": row[3]}
        out.append({"commandId": cid, "noteId": nid, "createdAt": created, "baseRevision": base,
                    "reason": res.get("reason") or "waits for the earlier conflict on this note",
                    "mine": {"op": op, "body": body}, "theirs": theirs})
    return out


def resolve(db, command_id, keep):
    """keep='mine': send this PC's version again on top of the authority's current revision.
    keep='theirs': accept the authority's version. Either way the other version stays in the outbox record."""
    row = db.execute("SELECT noteId, op, body, result FROM sync_outbox WHERE commandId=? AND status='conflict'", (command_id,)).fetchone()
    if not row:
        raise KeyError("Unknown conflict")
    if keep not in ("mine", "theirs"):
        raise ValueError("keep must be mine or theirs")
    db.execute("UPDATE sync_outbox SET status=? WHERE commandId=?", ("resolved_" + keep, command_id))
    if keep == "theirs":
        return command_id
    note_id, op, body, result = row
    current = json.loads(result).get("current") if result else None  # no result: held behind an earlier conflict
    if current:  # the authority's revision becomes the new base
        db.execute("INSERT INTO sync_mirror VALUES (?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET body=excluded.body, "
                   "revision=excluded.revision, deleted=excluded.deleted, updatedAt=excluded.updatedAt WHERE excluded.revision >= sync_mirror.revision",
                   (note_id, current["body"], current["revision"], int(current["deleted"]), current.get("updatedAt")))
    if op == "delete" and result and (current is None or current["deleted"]):
        return command_id  # already gone on the authority
    db.execute("INSERT INTO sync_outbox (commandId, noteId, op, body, baseRevision, createdAt) VALUES (?,?,?,?,?,?)",
               (str(uuid.uuid4()), note_id, op, body, _expected_revision(db, note_id), now()))
    return command_id


def status(db_connect, stale_minutes=60):
    with db_connect() as db:
        counts = dict(db.execute("SELECT status, COUNT(*) FROM sync_outbox GROUP BY status").fetchall())
        last_ok = _get(db, "lastSuccessAt")
        error = _get(db, "lastError") or None
        mirrored = db.execute("SELECT COUNT(*) FROM sync_mirror WHERE deleted=0").fetchone()[0]
        cursor = int(_get(db, "cursor", "0"))
    age = None
    if last_ok:
        age = round((datetime.now(timezone.utc) - datetime.fromisoformat(last_ok.replace("Z", "+00:00"))).total_seconds() / 60)
    return {"pending": counts.get("pending", 0), "sent": counts.get("sent", 0), "conflicts": counts.get("conflict", 0),
            "acknowledged": counts.get("acked", 0), "notes": mirrored, "cursor": cursor, "lastSuccessAt": last_ok,
            "minutesSinceSync": age, "stale": last_ok is None or age is None or age > stale_minutes, "lastError": error}


class HubTransport:
    """Talks to the authority through the hub with this PC's own hub session (runtime/hub.json)."""

    def __init__(self, config, timeout=10):
        self.config, self.timeout = config, timeout

    def _call(self, path, body=None):
        import urllib.request
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.config["url"].rstrip("/") + path, data=data,
                                     headers={"Authorization": f"Bearer {self.config['token']}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return json.load(response)

    def push(self, device_id, commands):
        return self._call("/v1/sync/push", {"deviceId": device_id, "commands": commands})["results"]

    def pull(self, since):
        return self._call(f"/v1/sync/pull?since={int(since)}")
