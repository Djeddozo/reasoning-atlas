"""Call capabilities through the registry (RA-022, docs/PLATFORM.md).

A section asks for a capability by its registry id; the gateway looks it up, applies the rule for its
effect and routes it to the module's adapter. Sections never reach another module's internals.

- `read` runs at once through the adapter. Every result carries provenance: capability, module, effect,
  the route it took, the module's version and the time.
- `propose` needs two steps: `preview` shows what would be sent and stores a pending call; only
  `confirm` runs it, once. The same proposal twice reuses the pending preview; confirming twice replays
  the stored outcome; a proposal identical to one already done needs `repeat` to be sent again.
- `act` stays disabled here; the owner's own buttons keep their direct routes, approved earlier.
- A module that cannot be reached fails visibly (offline, not connected, token refused); nothing is
  silently substituted.

Every attempt is written to `capability_calls` in Atlas's own database (the call log); `audit` checks
that every result in it carries complete provenance.
"""
import hashlib, json, sqlite3, uuid
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_calls (
  id TEXT PRIMARY KEY, capabilityId TEXT NOT NULL, module TEXT NOT NULL, effect TEXT NOT NULL,
  inputsHash TEXT NOT NULL, inputs TEXT, status TEXT NOT NULL, outcome TEXT, message TEXT,
  provenance TEXT, result TEXT, resultHash TEXT, createdAt TEXT NOT NULL, expiresAt TEXT, finishedAt TEXT);
CREATE INDEX IF NOT EXISTS capability_calls_key ON capability_calls (capabilityId, inputsHash, status);
"""
PREVIEW_MINUTES = 15
MAX_INPUT = 20000
MAX_STORED_RESULT = 20000
PROVENANCE = ("callId", "capabilityId", "module", "moduleName", "effect", "via", "version", "at")
# Outcome → HTTP status for the screen.
CODES = {"ok": 200, "unknown": 404, "disabled": 403, "unavailable": 409, "unreachable": 409, "invalid": 400,
         "not_configured": 503, "offline": 503, "unauthorized": 502, "expired": 410, "duplicate": 409,
         "in_progress": 409, "error": 502}


def migrate(db):
    db.executescript(SCHEMA)


def _now():
    return datetime.now(timezone.utc)


def _stamp(moment):
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(inputs):
    return json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class HubAdapter:
    """The Engine's adapter: reads and proposals go to the hub on the Pi (services/atlas/hub.py)."""
    via = "engine hub"

    def __init__(self, client=None):
        if client is None:
            from services.atlas import hubclient as client
        self.client = client

    def state(self):
        """(outcome, message, version) without raising; outcome 'ok' when the module answers."""
        status = self.client.status()
        if not status.get("configured"):
            return "not_configured", "No hub connected yet. Connect the Pi under Systems.", None
        if not status.get("reachable"):
            error = status.get("error") or "The hub cannot be reached"
            return ("unauthorized" if "token" in error else "offline"), error, None
        commit = status.get("runningCommit") or status.get("commit")
        return "ok", "", commit or f"hub protocol v{status.get('version') or '?'} (commit unknown)"

    def read(self, capability_id, inputs):
        from urllib.parse import urlencode
        return self.client.call(capability_id, urlencode(inputs))

    def propose(self, capability_id, inputs):
        return self.client.act(capability_id, inputs)


def _hub_outcome(code, body):
    """Map the hub client's (status, body) to an outcome and a message the screen can show."""
    message = (body or {}).get("error", "") if isinstance(body, dict) else ""
    if code == 200:
        return "ok", ""
    if code == 409 and "No hub connected" in message:
        return "not_configured", message
    if code in (503, 504):
        return "offline", message or "The module cannot be reached right now"
    if code == 502:
        return "unauthorized", message or "The hub refused the token"
    if code in (400, 404):
        return "invalid", message or f"The module refused the call ({code})"
    return "error", message or f"The module answered {code}"


class Gateway:
    def __init__(self, registry, connect, adapters=None):
        """`connect` yields a transaction on Atlas's database; `adapters` maps module id → adapter."""
        self.registry = registry
        self.connect = connect
        self.adapters = adapters if adapters is not None else {"engine": HubAdapter()}
        with self.connect() as db:
            migrate(db)

    # -- shared ---------------------------------------------------------------------------------
    def _provenance(self, call_id, cap, version, at):
        return {"callId": call_id, "capabilityId": cap["id"], "module": cap["module"], "moduleName": cap["moduleName"].strip(),
                "effect": cap["effect"], "via": getattr(self.adapters.get(cap["module"]), "via", None),
                "version": version, "at": _stamp(at), "source": cap.get("source"), "summary": cap["summary"]}

    def _envelope(self, outcome, message, provenance, **extra):
        return CODES[outcome], {"ok": outcome == "ok", "outcome": outcome, "message": message, "provenance": provenance, **extra}

    def _log(self, db, call_id, cap, inputs_hash, status, outcome, message, provenance, *, inputs=None, result=None,
             expires=None, finished=True):
        stored = None if result is None else _canonical(result)
        db.execute("""INSERT INTO capability_calls (id, capabilityId, module, effect, inputsHash, inputs, status, outcome,
                      message, provenance, result, resultHash, createdAt, expiresAt, finishedAt)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (call_id, cap["id"] if cap else "?", cap["module"] if cap else "?", cap["effect"] if cap else "?",
                    inputs_hash, inputs, status, outcome, message[:500], json.dumps(provenance),
                    stored if stored is not None and cap and cap["effect"] == "propose" and len(stored) <= MAX_STORED_RESULT else None,
                    stored and _digest(stored), provenance["at"] if provenance else _stamp(_now()), expires,
                    (provenance["at"] if provenance else _stamp(_now())) if finished else None))

    def _check(self, capability_id, inputs, effect):
        """Return (cap, None) when the call may go ahead, else (cap or None, (outcome, message))."""
        cap = self.registry["capabilities"].get(capability_id) if isinstance(capability_id, str) else None
        if cap is None:
            return None, ("unknown", f"{capability_id!r} is not a registered capability")
        if cap["effect"] == "act":
            return cap, ("disabled", f"{cap['id']} changes something directly; act capabilities are disabled in the registry. "
                                     "Use its own button in Atlas, which the owner approved for that one action.")
        if cap["effect"] != effect:
            verb = "preview and confirm it" if cap["effect"] == "propose" else "call it directly"
            return cap, ("invalid", f"{cap['id']} is a {cap['effect']} capability: {verb}")
        if cap["status"] not in ("tested", "live"):
            return cap, ("unavailable", f"{cap['id']} is {cap['status']}; it cannot be called until it is tested")
        if not cap["callable"] or cap["module"] not in self.adapters:
            return cap, ("unreachable", f"Atlas has no route to {cap['moduleName'].strip()} for {cap['id']} yet")
        if not isinstance(inputs, dict):
            return cap, ("invalid", "Inputs must be a JSON object")
        if len(_canonical(inputs)) > MAX_INPUT:
            return cap, ("invalid", "Inputs are too large")
        if effect == "read" and not all(isinstance(v, (str, int, float, bool)) for v in inputs.values()):
            return cap, ("invalid", "Read inputs must be plain values")
        return cap, None

    def _refuse(self, cap, inputs, problem):
        outcome, message = problem
        call_id = uuid.uuid4().hex
        provenance = self._provenance(call_id, cap, None, _now()) if cap else None
        with self.connect() as db:
            self._log(db, call_id, cap, _digest(_canonical(inputs) if isinstance(inputs, dict) else repr(inputs)),
                      "refused", outcome, message, provenance)
        return self._envelope(outcome, message, provenance)

    # -- read -----------------------------------------------------------------------------------
    def call(self, capability_id, inputs=None):
        inputs = {} if inputs is None else inputs
        cap, problem = self._check(capability_id, inputs, "read")
        if problem:
            return self._refuse(cap, inputs, problem)
        adapter = self.adapters[cap["module"]]
        call_id, at = uuid.uuid4().hex, _now()
        state, message, version = adapter.state()
        result = None
        if state == "ok":
            code, body = adapter.read(cap["id"], inputs)
            state, message = _hub_outcome(code, body)
            result = body.get("result") if state == "ok" and isinstance(body, dict) else None
        provenance = self._provenance(call_id, cap, version, at)
        with self.connect() as db:
            self._log(db, call_id, cap, _digest(_canonical(inputs)), "done" if state == "ok" else "failed", state,
                      message or "Read", provenance, result=result)
        return self._envelope(state, message, provenance, result=result)

    # -- propose --------------------------------------------------------------------------------
    def preview(self, capability_id, inputs=None):
        inputs = {} if inputs is None else inputs
        cap, problem = self._check(capability_id, inputs, "propose")
        if problem:
            return self._refuse(cap, inputs, problem)
        state, message, version = self.adapters[cap["module"]].state()
        key, at = _digest(_canonical(inputs)), _now()
        if state != "ok":  # fail before the owner confirms something that cannot be sent
            return self._refuse(cap, inputs, (state, message))
        with self.connect() as db:
            self._expire(db, at)
            pending = db.execute("SELECT id, provenance, expiresAt FROM capability_calls WHERE capabilityId=? AND inputsHash=? "
                                 "AND status='previewed' ORDER BY createdAt DESC LIMIT 1", (cap["id"], key)).fetchone()
            done = db.execute("SELECT id, finishedAt FROM capability_calls WHERE capabilityId=? AND inputsHash=? "
                              "AND status IN ('done', 'running') ORDER BY createdAt DESC LIMIT 1", (cap["id"], key)).fetchone()
            if pending:
                call_id, provenance, expires, reused = pending[0], json.loads(pending[1]), pending[2], True
            else:
                call_id, reused = uuid.uuid4().hex, False
                provenance = self._provenance(call_id, cap, version, at)
                expires = _stamp(at + timedelta(minutes=PREVIEW_MINUTES))
                self._log(db, call_id, cap, key, "previewed", None, "Waiting for confirmation", provenance,
                          inputs=_canonical(inputs), expires=expires, finished=False)
        preview = {"callId": call_id, "capabilityId": cap["id"], "summary": cap["summary"], "module": cap["moduleName"].strip(),
                   "effect": "propose", "inputs": inputs, "expiresAt": expires, "reused": reused,
                   "text": f"Atlas will ask {cap['moduleName'].strip()} to: {cap['summary']} Nothing is sent until you confirm.",
                   "duplicateOf": done and {"callId": done[0], "at": done[1]}}
        if done:
            preview["text"] += " The same proposal was already sent; confirm with repeat to send it again."
        return 200, {"ok": True, "outcome": "previewed", "message": preview["text"], "preview": preview, "provenance": provenance}

    def _expire(self, db, at):
        db.execute("UPDATE capability_calls SET status='expired', outcome='expired', finishedAt=? "
                   "WHERE status='previewed' AND expiresAt < ?", (_stamp(at), _stamp(at)))

    def _stored(self, row):
        provenance = json.loads(row["provenance"])
        outcome = row["outcome"] or ("in_progress" if row["status"] == "running" else "error")
        message = row["message"] or ""
        if row["status"] == "running":
            outcome, message = "in_progress", ("This proposal was sent and has not reported back. It is never sent again "
                                               "automatically; check the module before repeating it.")
        code, body = self._envelope(outcome, message, provenance, result=json.loads(row["result"]) if row["result"] else None)
        return code, {**body, "replayed": True}

    def confirm(self, call_id, repeat=False):
        if not isinstance(call_id, str) or not call_id:
            return self._envelope("invalid", "Give the callId from the preview", None)
        at = _now()
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            self._expire(db, at)
            row = db.execute("SELECT * FROM capability_calls WHERE id=?", (call_id,)).fetchone()
            if row is None:
                return self._envelope("unknown", "No preview with this callId", None)
            if row["status"] == "expired":
                return self._envelope("expired", "The preview expired; preview it again", json.loads(row["provenance"]))
            if row["status"] != "previewed":
                return self._stored(row)  # confirming twice never sends twice
            earlier = db.execute("SELECT id FROM capability_calls WHERE capabilityId=? AND inputsHash=? AND id<>? "
                                 "AND status IN ('done', 'running')", (row["capabilityId"], row["inputsHash"], call_id)).fetchone()
            if earlier and not repeat:
                return self._envelope("duplicate", f"The same proposal was already sent (call {earlier[0]}); confirm with repeat to send it again",
                                      json.loads(row["provenance"]), duplicateOf=earlier[0])
            # Write ahead: mark it running in the same step that claims it, so a second confirm or a crash
            # never sends it twice.
            claimed = db.execute("UPDATE capability_calls SET status='running' WHERE id=? AND status='previewed'", (call_id,)).rowcount
            cap = self.registry["capabilities"].get(row["capabilityId"])
            inputs = json.loads(row["inputs"])
            provenance = json.loads(row["provenance"])
        if not claimed:
            with self.connect() as db:
                db.row_factory = sqlite3.Row
                return self._stored(db.execute("SELECT * FROM capability_calls WHERE id=?", (call_id,)).fetchone())
        adapter = self.adapters.get(cap["module"]) if cap else None
        if adapter is None:
            outcome, message, body = "unreachable", "The capability is no longer routed", {}
        else:
            state, message, version = adapter.state()
            if state != "ok":
                outcome, body = state, {}
            else:
                code, body = adapter.propose(cap["id"], inputs)
                outcome, message = _hub_outcome(code, body)
                provenance = {**provenance, "version": version or provenance.get("version")}
        provenance["at"] = _stamp(_now())
        result = body if outcome == "ok" else None
        message = (body.get("message") if outcome == "ok" and isinstance(body, dict) else message) or "Done"
        stored = None if result is None else _canonical(result)
        with self.connect() as db:
            db.execute("UPDATE capability_calls SET status=?, outcome=?, message=?, provenance=?, result=?, resultHash=?, finishedAt=? WHERE id=?",
                       ("done" if outcome == "ok" else "failed", outcome, str(message)[:500], json.dumps(provenance),
                        stored if stored and len(stored) <= MAX_STORED_RESULT else None, stored and _digest(stored), provenance["at"], call_id))
        return self._envelope(outcome, str(message), provenance, result=result)

    # -- call log and audit ---------------------------------------------------------------------
    def calls(self, limit=50):
        with self.connect() as db:
            self._expire(db, _now())
            rows = db.execute("SELECT id, capabilityId, module, effect, status, outcome, message, provenance, createdAt, finishedAt "
                              "FROM capability_calls ORDER BY createdAt DESC, rowid DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
        return [{"callId": r[0], "capabilityId": r[1], "module": r[2], "effect": r[3], "status": r[4], "outcome": r[5],
                 "message": r[6], "provenance": json.loads(r[7]) if r[7] else None, "createdAt": r[8], "finishedAt": r[9]} for r in rows]


def audit(db):
    """Every call that returned or sent something must carry complete provenance. Returns the gaps."""
    migrate(db)
    checked, missing = 0, []
    for call_id, status, provenance in db.execute("SELECT id, status, provenance FROM capability_calls WHERE module<>'?'"):
        checked += 1
        data = json.loads(provenance) if provenance else {}
        gaps = [f for f in PROVENANCE if f not in data or (data[f] in (None, "") and f not in ("version", "via"))]
        if status in ("done", "running") and not data.get("version"):
            gaps.append("version")
        if data.get("callId") not in (None, call_id):
            gaps.append("callId mismatch")
        if gaps:
            missing.append({"callId": call_id, "status": status, "missing": gaps})
    return {"checked": checked, "complete": checked - len(missing), "missing": missing}
