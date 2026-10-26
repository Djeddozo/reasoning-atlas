"""Connected read-only boundary to the Engine (RA-009).

Atlas fetches one snapshot from the hub's `engine.system.snapshot_export` function (GET, token in the
Authorization header; the hub reads the Engine's database read-only and exports from a copy) and imports
it with adapters/hq/snapshot.py. Nothing here can write to the Engine: the only request is a GET to one
allowlisted read function. Every attempt is recorded, so the screen can tell apart a fresh connection,
stale data, an offline hub, a refused token and a version (schema) mismatch.
"""
import json, urllib.error, urllib.request
from datetime import datetime, timezone

from adapters.hq import snapshot as hq_snapshot

FUNCTION = "engine.system.snapshot_export"
STALE_AFTER_MINUTES = 60  # data captured longer ago than this is shown as stale
TIMEOUT = 20
SCHEMA = """
CREATE TABLE IF NOT EXISTS external_sync (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sourceSystem TEXT NOT NULL, attemptedAt TEXT NOT NULL,
  outcome TEXT NOT NULL, message TEXT NOT NULL, snapshotId TEXT);
"""
OUTCOMES = ("ok", "not_configured", "offline", "unauthorized", "schema_mismatch", "error")
MESSAGES = {
    "not_configured": "No hub connected yet. Connect the Pi under Systems to read the Engine's plan.",
    "offline": "The hub cannot be reached (is the Pi on and Tailscale connected on both devices?).",
    "unauthorized": "The hub refused the token. Paste the current hub token under Systems.",
}


def migrate(db):
    hq_snapshot.migrate(db)
    db.executescript(SCHEMA)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch(config, timeout=TIMEOUT):
    """GET the snapshot from the hub. Returns (outcome, snapshot or None, message); never raises."""
    if not config:
        return "not_configured", None, MESSAGES["not_configured"]
    req = urllib.request.Request(config["url"].rstrip("/") + f"/v1/functions/{FUNCTION}",
                                 headers={"Authorization": f"Bearer {config['token']}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return "ok", json.load(response).get("result"), "Snapshot received"
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
        except ValueError:
            body = {}
        if exc.code == 401:
            return "unauthorized", None, MESSAGES["unauthorized"]
        if exc.code == 404:  # the hub runs an Engine version without the export
            return "schema_mismatch", None, "The Pi runs an older Engine without the snapshot export. Update the Pi (Systems › Updates)."
        if exc.code == 409 and body.get("kind") == "schema_mismatch":
            return "schema_mismatch", None, body.get("error", "The Engine's database differs from what Atlas reads.")
        if exc.code == 503:
            return "offline", None, body.get("error", MESSAGES["offline"])
        return "error", None, body.get("error", f"The hub answered {exc.code}")
    except (urllib.error.URLError, OSError, ValueError):
        return "offline", None, MESSAGES["offline"]


def _record(db, outcome, message, snapshot_id=None):
    db.execute("INSERT INTO external_sync (sourceSystem, attemptedAt, outcome, message, snapshotId) VALUES (?,?,?,?,?)",
               (hq_snapshot.SOURCE, _now(), outcome, message[:500], snapshot_id))
    # Keep a short history; older attempts carry no extra information.
    db.execute("DELETE FROM external_sync WHERE id NOT IN (SELECT id FROM external_sync ORDER BY id DESC LIMIT 50)")


def sync(db, config=None, fetcher=None):
    """Fetch and import one snapshot. The existing read model stays as it is on any failure."""
    from services.atlas import hubclient
    migrate(db)
    config = config if config is not None else hubclient.load()
    outcome, snap, message = (fetcher or fetch)(config)
    result = None
    if outcome == "ok":
        try:
            result = hq_snapshot.import_snapshot(db, snap)
            message = (f"Imported {snap['snapshotId']}: {result['inserted']} new, {result['updated']} changed, "
                       f"{result['unchanged']} unchanged, {result['removed']} removed")
        except hq_snapshot.IncompatibleSnapshot as exc:
            outcome, message = "schema_mismatch", str(exc)
        except (ValueError, KeyError, TypeError) as exc:
            outcome, message = "error", f"The snapshot was refused: {exc}"
    with db:
        _record(db, outcome, message, result and result["snapshotId"])
    return {**connection(db), "import": result}


def _minutes_since(stamp, now=None):
    if not stamp:
        return None
    then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return int(((now or datetime.now(timezone.utc)) - then).total_seconds() // 60)


def connection(db, now=None):
    """What the screen shows: state, message, age of the data and the last attempts."""
    migrate(db)
    health = hq_snapshot.health(db)
    rows = db.execute("SELECT attemptedAt, outcome, message FROM external_sync WHERE sourceSystem=? ORDER BY id DESC LIMIT 5",
                      (hq_snapshot.SOURCE,)).fetchall()
    attempts = [{"at": r[0], "outcome": r[1], "message": r[2]} for r in rows]
    age = _minutes_since(health["capturedAt"], now)
    last = attempts[0] if attempts else None
    if last and last["outcome"] != "ok":
        state, message = last["outcome"], last["message"]
    elif health["status"] != "ok":
        state, message = "never", "Not read from the Engine yet."
    elif age is not None and age > STALE_AFTER_MINUTES:
        state, message = "stale", f"Last read {age} minutes ago; refresh to see the current plan."
    else:
        state, message = "connected", last["message"] if last else "Read from a snapshot."
    stale = health["status"] == "ok" and age is not None and age > STALE_AFTER_MINUTES
    return {"state": state, "message": message, "readOnly": True, "writes": False, "function": FUNCTION,
            "staleAfterMinutes": STALE_AFTER_MINUTES, "dataAgeMinutes": age, "dataStale": stale,
            "capturedAt": health["capturedAt"], "lastSyncAt": health["lastSyncAt"], "snapshotId": health["snapshotId"],
            "sourceCommit": health["upstreamCommit"], "counts": health["counts"], "attempts": attempts}
