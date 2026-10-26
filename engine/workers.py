"""Builder on several devices, step 1 (#185): the hub hands out work, workers build it.

The hub is the Pi. It keeps the plan, the queue, the Build next lane, the constitution's limits and the pull
requests, and it is the only writer to the plan (RA-031, one writer per dataset). A worker builds: the Pi itself,
and later other devices (the PC through WSL2, #187). Each worker has a number of slots, one build per slot.

Leases. A worker claims a task and gets a lease that runs out unless the worker sends a heartbeat. The Pi's own
slots are held by file locks (.devloop/lock, lock-2, …) that the operating system releases when a process dies, so
its leases need no expiry. A remote worker's lease expires after `lease_seconds` without a heartbeat: the run is
recorded as stopped, the task goes back to the queue, and whoever takes it next resumes from its checkpoint
(engine/checkpoint.py, part of every build prompt).

Slots. The first build that starts while nothing else is building picks as before (devloop.pick_task, and may
stack on an unreviewed PR). Every further build, on any worker, only takes a task from the Side by side batch
Djeddi accepted (#72, parallel.may_run_alongside) that is independent of everything being built, and it starts
from origin/main, never stacked. Slots per worker are a setting (default 1, so nothing changes until he raises it)
within `[devloop] max_slots_per_worker` when that is set, and never above SLOT_CEILING.

Remote workers. Registering one creates an approval (a new system connects to Atlas); it can claim work only after
Djeddi approves it. It talks to the hub over Tailscale with its own token, which lives in .env on both sides
(ATLAS_WORKER_TOKEN_<NAME> on the hub, ATLAS_WORKER_TOKEN on the worker), never in the database. It gets the
whole build prompt from the hub, builds in its own worktree under the gatekeeper hook, checks the work the same way
(tests, outside-code rule), pushes the branch, opens the PR and reports summary, tokens and result back.
"""
import hmac
import json
import os
import re
from datetime import datetime, timedelta

from . import db

PI = "pi"
SLOT_CEILING = 4
LEASE_SECONDS = 300
HEARTBEAT_SECONDS = 60
NAME = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
RESULTS = ("pr_open", "no_changes", "tests_failed", "failed", "stopped")
ONLINE_SECONDS = 15 * 60  # an idle remote worker asks for work every five minutes, a building one beats every minute


def _now() -> datetime:
    return datetime.now()


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def ceiling() -> int:
    from . import loop
    v = loop.load_constitution().get("devloop", {}).get("max_slots_per_worker")
    return max(1, min(int(v), SLOT_CEILING)) if v not in (None, "", 0) else SLOT_CEILING


# ---------------- the register ----------------

def ensure_pi(con) -> dict:
    """The hub's own worker; it is part of Atlas, so it needs no approval."""
    r = con.execute("SELECT * FROM workers WHERE name = ?", (PI,)).fetchone()
    if r is None:
        db.insert(con, "workers", name=PI, device="Raspberry Pi (hub)", slots=1, status="active", created=db.now())
        r = con.execute("SELECT * FROM workers WHERE name = ?", (PI,)).fetchone()
    return dict(r)


def get(con, name: str) -> dict | None:
    if name == PI:
        return ensure_pi(con)
    r = con.execute("SELECT * FROM workers WHERE name = ?", (name,)).fetchone()
    if r and r["status"] == "pending" and r["approval_id"]:  # Djeddi's answer arrives through the approvals table
        a = con.execute("SELECT status FROM approvals WHERE id = ?", (r["approval_id"],)).fetchone()
        if a and a["status"] in ("approved", "denied"):
            new = "active" if a["status"] == "approved" else "revoked"
            con.execute("UPDATE workers SET status = ? WHERE id = ?", (new, r["id"]))
            db.log(con, "hq", f"worker_{new}", f"{name} (approval #{r['approval_id']} {a['status']})")
            con.commit()
            r = con.execute("SELECT * FROM workers WHERE id = ?", (r["id"],)).fetchone()
    return dict(r) if r else None


def register(con, name: str, device: str = "", slots: int = 1) -> str:
    """A new device that may build: asks Djeddi's approval first; nothing connects before he answers."""
    name = (name or "").strip().lower()
    if not NAME.match(name) or name == PI:
        raise ValueError("Name the worker with 2-24 lowercase letters, digits or '-', e.g. zenunit")
    if con.execute("SELECT 1 FROM workers WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"a worker named {name} is already registered")
    slots = max(1, min(int(slots or 1), ceiling()))
    device = " ".join((device or "").split())[:80] or name
    aid = db.insert(con, "approvals", request=f"Let the device {device} build for Atlas as worker '{name}' ({slots} slot{'s' if slots > 1 else ''})",
                    reason="A new system connects to the hub: it receives build prompts, pushes branches and opens pull "
                           f"requests. It needs ATLAS_WORKER_TOKEN_{name.upper().replace('-', '_')} in the hub's .env.",
                    category="access", status="pending", created=db.now())
    db.insert(con, "workers", name=name, device=device, slots=slots, status="pending", approval_id=aid, created=db.now())
    db.log(con, "agent" if os.environ.get("HQ_ACTOR") == "agent" else "me", "worker_register", f"{name} on {device}: approval #{aid}")
    con.commit()
    return f"Worker {name} registered; it can build once you approve #{aid}."


def revoke(con, name: str) -> str:
    w = get(con, name)
    if not w or name == PI:
        raise ValueError(f"there is no remote worker named {name}")
    con.execute("UPDATE workers SET status = 'revoked' WHERE id = ?", (w["id"],))
    db.log(con, "me", "worker_revoke", name)
    con.commit()
    return f"Worker {name} can no longer claim work (its token stops working)."


def slots(con, name: str = PI) -> int:
    w = get(con, name)
    return max(1, min(int(w["slots"] or 1), ceiling())) if w else 0


def set_slots(con, n, name: str = PI) -> str:
    w = get(con, name)
    if not w:
        raise ValueError(f"there is no worker named {name}")
    n = int(n)
    if not 1 <= n <= ceiling():
        raise ValueError(f"Choose 1 to {ceiling()} slots")
    con.execute("UPDATE workers SET slots = ? WHERE id = ?", (n, w["id"]))
    db.log(con, "me", "worker_slots", f"{name}: {n}")
    con.commit()
    return (f"{name}: {n} slot{'s' if n > 1 else ''}."
            + (" Beyond the first, a slot only takes tasks from an accepted Side by side batch." if n > 1 else ""))


def listing(con) -> list[dict]:
    ensure_pi(con)
    out = []
    for r in con.execute("SELECT name FROM workers ORDER BY name != ?, name", (PI,)):
        w = get(con, r["name"])
        busy = con.execute("SELECT COUNT(*) FROM build_leases WHERE worker = ? AND status = 'active'", (w["name"],)).fetchone()[0]
        out.append({k: w[k] for k in ("name", "device", "status", "approval_id", "last_seen")} | {"slots": slots(con, w["name"]), "busy": busy})
    return out


# ---------------- devices at a glance (#193), read-only ----------------

def _key(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _registered(con) -> list[dict]:
    """The register as it reads now, without writing: the Pi even before its row exists, a pending worker as active or
    revoked once its approval is answered (get() writes that down later)."""
    rows = [dict(r) for r in con.execute("SELECT * FROM workers ORDER BY name != ?, name", (PI,))]
    if not any(r["name"] == PI for r in rows):
        rows.insert(0, {"name": PI, "device": "Raspberry Pi (hub)", "slots": 1, "status": "active", "approval_id": None, "last_seen": None})
    for r in rows:
        if r["status"] == "pending" and r.get("approval_id"):
            a = con.execute("SELECT status FROM approvals WHERE id = ?", (r["approval_id"],)).fetchone()
            if a and a["status"] in ("approved", "denied"):
                r["status"] = "active" if a["status"] == "approved" else "revoked"
    return rows


def machines(con) -> list[dict]:
    """The machines that report themselves (Resources › Connected, engine/hardware.py): id, host, role."""
    out = []
    for r in con.execute("""SELECT id, name, source, detected FROM resources WHERE source LIKE 'detected:%'
                            AND (status IS NULL OR status = 'active') ORDER BY id"""):
        try:
            role = (json.loads(r["detected"] or "{}") or {}).get("role", "")
        except ValueError:
            role = ""
        out.append({"id": r["id"], "host": r["source"][len("detected:"):], "role": role, "name": r["name"]})
    return out


def machine_of(w: dict, found: list[dict]) -> dict | None:
    """A worker's Connected card: the machine whose host name is the worker's name or device; the Pi is the hub's own."""
    keys = {_key(w["name"]), _key(w.get("device"))} - {""}
    hit = next((m for m in found if _key(m["host"]) in keys), None)
    if hit is None and w["name"] == PI:
        import socket
        pis = [m for m in found if m["role"] == "pi"]
        hit = next((m for m in pis if _key(m["host"]) == _key(socket.gethostname())), pis[0] if pis else None)
    return hit


def display_name(w: dict, found: list[dict]) -> str:
    """The device's name as on its Connected card (its host, e.g. DjedsberryPi or ZenUnit), never the raw worker id
    when a better one is known."""
    m = machine_of(w, found)
    if m:
        return m["host"]
    return "Pi" if w["name"] == PI else (w.get("device") or w["name"]).split(" (")[0][:40]


def display_names(con) -> dict[str, str]:
    found = machines(con)
    return {w["name"]: display_name(w, found) for w in _registered(con)}


def _ago(ts) -> float | None:
    try:
        return (_now() - datetime.fromisoformat(str(ts))).total_seconds()
    except (TypeError, ValueError):
        return None


def roles(con) -> list[dict]:
    """Each device's builder role, light and read-only (also for Resources › Connected): who it is, whether it may
    build, its slots in use and what it builds now."""
    found, cap = machines(con), ceiling()
    running = [dict(r) for r in con.execute("""SELECT r.id, r.task_id, t.title, COALESCE(NULLIF(r.worker, ''), ?) AS worker
                                               FROM dev_runs r LEFT JOIN tasks t ON t.id = r.task_id
                                               WHERE r.status = 'running' ORDER BY r.id""", (PI,))]
    out = []
    for w in _registered(con):
        m = machine_of(w, found)
        mine = [{"run": r["id"], "task_id": r["task_id"], "title": r["title"] or "", "slot": slot_of(con, r["id"])}
                for r in running if r["worker"] == w["name"]]
        age = None if w["name"] == PI else _ago(w.get("last_seen"))
        online = w["status"] == "active" and (w["name"] == PI or (age is not None and age <= ONLINE_SECONDS))
        out.append({"name": w["name"], "display": display_name(w, found), "device": w.get("device") or "",
                    "role": "hub" if w["name"] == PI else "worker", "status": w["status"], "approval_id": w.get("approval_id"),
                    "slots": max(1, min(int(w.get("slots") or 1), cap)), "ceiling": cap, "busy": len(mine), "building": mine,
                    "last_seen": w.get("last_seen") or "", "online": online, "resource_id": m["id"] if m else None})
    return out


def _reported(con, name: str) -> tuple[str, bool]:
    """What a remote worker last heard when it got no work, or said itself (own), while that is recent."""
    try:
        r = json.loads(db.get_state(con, f"worker_idle_{name}", "") or "{}")
    except ValueError:
        return "", False
    age = _ago(r.get("at"))
    return (str(r.get("why") or ""), bool(r.get("own"))) if age is not None and age <= ONLINE_SECONDS else ("", False)


def devices(con, nxt=None) -> list[dict]:
    """Forge › Build (#193): roles() plus, for a device with nothing to build, why in one plain sentence."""
    from . import atlasplan, devloop, usage
    out = roles(con)
    conf = devloop.cfg()
    shared = ("the kill switch is on" if usage.killed() else "the builder is switched off" if not conf["enabled"] else
              "the builder is paused" if devloop.paused(con) else
              "it waits for Claude's usage limit to reset" if atlasplan.reset_status(con).get("waiting") else
              (f"the daily budget is used up ({devloop.limit_reached(con)})" if devloop.limit_reached(con) else "") or
              ("the review batch is full: pull requests wait for your review" if devloop.batch_full(con) else ""))
    busy = busy_tasks(con)
    for d in out:
        d["idle"] = ""
        if d["busy"]:
            continue
        if d["status"] == "pending":
            why = f"waits for your approval #{d['approval_id']} (Today › Needs your input)"
        elif d["status"] != "active":
            why = "its access is revoked"
        elif not d["online"]:
            why = (f"offline since {d['last_seen'][:16].replace('T', ' ')}" if d["last_seen"] else "offline: it has not contacted the hub yet")
        elif shared:
            why = shared[0].upper() + shared[1:]
        elif d["role"] == "worker" and (said := _reported(con, d["name"]))[0] and (said[1] or not busy):
            why = said[0]
        elif busy:
            why = ("Another device is building; a second build only takes a task from an accepted Side by side batch "
                   "(Forge › Build › Queue)" if not _accepted(con) else
                   "No other task of the accepted Side by side batch is ready and independent of what is building")
        elif d["role"] == "hub":
            why = f"Ready: next is #{nxt['id']} {nxt['title']}" if nxt else "Nothing is ready to build"
        else:
            why = "Asks the hub for work every few minutes"
        d["idle"] = why[0].upper() + why[1:]
    return out


def _accepted(con) -> bool:
    from . import parallel
    return bool(parallel.accepted(con))


def token_ok(name: str, given: str) -> bool:
    """The worker's token from the hub's environment or .env; never stored in the database."""
    from . import envfile
    key = f"ATLAS_WORKER_TOKEN_{name.upper().replace('-', '_')}"
    want = os.environ.get(key) or envfile.read_value(key)
    return bool(want) and len(want) >= 24 and hmac.compare_digest(want.encode(), (given or "").encode())


# ---------------- leases ----------------

def lease(con, task_id: int, run_id: int, worker: str, slot: int, remote: bool) -> int:
    now = _now()
    return db.insert(con, "build_leases", task_id=task_id, run_id=run_id, worker=worker, slot=slot, status="active",
                     claimed=_iso(now), heartbeat=_iso(now), expires=_iso(now + timedelta(seconds=LEASE_SECONDS)) if remote else None)


def slot_of(con, run_id: int) -> int:
    r = con.execute("SELECT slot FROM build_leases WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
    return int(r[0]) if r and r[0] else 1


def end_lease(con, run_id: int, status: str = "released", result: str = "") -> None:
    con.execute("UPDATE build_leases SET status = ?, result = ? WHERE run_id = ? AND status = 'active'", (status, result[:200], run_id))


def expire(con) -> list[int]:
    """Remote leases without a heartbeat in time: the run stops, the task returns to the queue (resumed from its checkpoint)."""
    out = []
    for r in con.execute("SELECT * FROM build_leases WHERE status = 'active' AND expires IS NOT NULL AND expires < ?",
                         (_iso(_now()),)).fetchall():
        con.execute("UPDATE build_leases SET status = 'expired' WHERE id = ?", (r["id"],))
        con.execute("""UPDATE dev_runs SET status = 'stopped', finished = ?, summary = trim(summary || char(10) || ?)
                       WHERE id = ? AND status = 'running'""",
                    (db.now(), f"Lease expired: worker {r['worker']} stopped reporting; the task returns to the queue.", r["run_id"]))
        con.execute("UPDATE tasks SET status = 'todo' WHERE id = ? AND status = 'doing'", (r["task_id"],))
        db.log(con, "hq", "worker_lease_expired", f"task #{r['task_id']} on {r['worker']} (run {r['run_id']})")
        out.append(r["task_id"])
    if out:
        con.commit()
    return out


def busy_tasks(con) -> list[int]:
    """Tasks being built now, on any worker."""
    return [r[0] for r in con.execute("SELECT DISTINCT task_id FROM dev_runs WHERE status = 'running' AND task_id IS NOT NULL")]


def pick_alongside(con, busy: list[int], task_id: int | None = None):
    """A task for a further slot: in the accepted Side by side batch, ready, and independent of everything building."""
    from . import overlap, parallel
    b = parallel.accepted(con)
    if not b:
        return None
    conf, mods = parallel.cfg(), overlap.modules()
    waits = parallel._waits(con)
    flying = [f for f in parallel.in_flight(con, conf, mods) if f["id"] in busy]
    ready = parallel.ready(con)
    for tid in ([task_id] if task_id else ready):
        if tid in busy or tid not in b["open"] or tid not in ready:
            continue
        t = parallel._task(con, tid, conf, mods)
        if not any(parallel.why_not(t, f, waits) for f in flying):
            return con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    return None


def why_not_alongside(con, busy: list[int]) -> str:
    if not busy:
        return ""
    from . import parallel
    if not parallel.accepted(con):
        return ("A build is running. Another can run next to it only from an accepted Side by side batch "
                "(Forge › Build › Queue).")
    return "A build is running, and no other task of the accepted Side by side batch is ready and independent of it."


# ---------------- remote workers, through the hub ----------------

def _remote(con, name: str, given: str) -> dict:
    w = get(con, name) if name and name != PI else None
    if not w or not token_ok(name, given):
        raise PermissionError("Unknown worker or wrong worker token")
    if w["status"] != "active":
        raise PermissionError(f"Worker {name} is {w['status']}" + (f": approve #{w['approval_id']} first" if w["status"] == "pending" else ""))
    con.execute("UPDATE workers SET last_seen = ? WHERE id = ?", (db.now(), w["id"]))
    return w


def claim(con, name: str, given: str, idle: str = "") -> dict:
    """A remote worker asks for work: {"task": None, "why": …} or the whole job, with a lease.
    `idle`: the worker cannot take work now and says why (e.g. low disk); it only keeps its place on Forge › Build."""
    from . import devloop, routing, skilltree, usage
    _remote(con, name, given)
    expire(con)
    conf = devloop.cfg()
    why = (" ".join(str(idle).split())[:200] if idle else "the kill switch is on" if usage.killed() else "the builder is off" if not conf["enabled"] else
           "the builder is paused" if devloop.paused(con) else devloop.limit_reached(con) or
           ("the review batch is full" if devloop.batch_full(con) else ""))
    if not why and con.execute("SELECT COUNT(*) FROM build_leases WHERE worker = ? AND status = 'active'", (name,)).fetchone()[0] >= slots(con, name):
        why = f"all {slots(con, name)} slot(s) of {name} are busy"
    task = None
    if not why:
        busy = busy_tasks(con)
        task = pick_alongside(con, busy) if busy else devloop.pick_task(con, ask=True)
        why = "" if task else (why_not_alongside(con, busy) if busy else "nothing is ready to build")
    if not task:
        db.set_state(con, f"worker_idle_{name}", json.dumps({"at": _iso(_now()), "why": why, "own": bool(idle)}))  # Forge › Build says why (#193)
        con.commit()
        return {"task": None, "why": why, "retry_seconds": HEARTBEAT_SECONDS * 5}
    tag = datetime.now().strftime("%m%d%H%M%S")
    branch = f"{conf['branch_prefix']}{task['id']}-{devloop.slug(task['title'])}-{tag}"
    rid = db.insert(con, "dev_runs", task_id=task["id"], branch=branch, status="running", started=db.now(), head="", worker=name)
    db.set_state(con, f"worker_idle_{name}", "")
    db.update(con, "tasks", task["id"], status="doing")
    held = con.execute("SELECT COUNT(*) FROM build_leases WHERE worker = ? AND status = 'active'", (name,)).fetchone()[0]
    lid = lease(con, task["id"], rid, name, held + 1, remote=True)
    choice = routing.choose(con, task)
    devloop.route(con, rid, task["id"], choice)
    db.log(con, "hq", "devloop_start", f"task #{task['id']} on {branch} (worker {name})")
    con.commit()
    with open(devloop.log_path(rid), "a") as log:
        log.write(f"{datetime.now():%H:%M} claimed by worker {name}\n")
    return {"task": task["id"], "title": task["title"], "lease": lid, "run": rid, "branch": branch, "base": "origin/main",
            "prompt": devloop.build_prompt(con, task, branch), "model": choice["model"] or "",
            "outside_ok": bool(skilltree.outside(dict(task))),  # passed the outside-code approval in pick_task
            "max_turns": int(conf["max_turns"]), "max_minutes": int(conf["max_minutes"]),
            "heartbeat_seconds": HEARTBEAT_SECONDS, "lease_seconds": LEASE_SECONDS}


def _own_lease(con, name: str, lid: int) -> dict:
    r = con.execute("SELECT * FROM build_leases WHERE id = ?", (int(lid),)).fetchone()
    if not r or r["worker"] != name:
        raise PermissionError("That lease is not yours")
    if r["status"] != "active":
        raise ValueError(f"The lease is {r['status']}; stop this build")
    return dict(r)


def heartbeat(con, name: str, given: str, lid: int, lines: list[str] | None = None) -> dict:
    """Keeps the lease alive and adds the worker's new log lines to Build › Live. Tells it when to stop."""
    from . import devloop, usage
    _remote(con, name, given)
    r = _own_lease(con, name, lid)
    con.execute("UPDATE build_leases SET heartbeat = ?, expires = ? WHERE id = ?",
                (_iso(_now()), _iso(_now() + timedelta(seconds=LEASE_SECONDS)), r["id"]))
    con.commit()
    if lines:
        with open(devloop.log_path(r["run_id"]), "a") as log:
            log.writelines(str(l)[:400].rstrip("\n") + "\n" for l in lines[-200:])
    return {"stop": bool(r["stop"]) or usage.killed() or devloop.stop_requested(con, r["run_id"])}


def report(con, name: str, given: str, lid: int, result: dict) -> str:
    """The worker's result: recorded on the run as if the Pi had built it; the lease is released."""
    from . import devloop, resume
    _remote(con, name, given)
    r = _own_lease(con, name, lid)
    status = str(result.get("status", ""))
    if status not in RESULTS:
        raise ValueError(f"status must be one of {', '.join(RESULTS)}")
    summary = str(result.get("summary", ""))[:2000]
    pr = str(result.get("pr_url", ""))[:300]
    if status == "pr_open" and not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+/(pull|compare)/", pr):
        raise ValueError("pr_open needs the pull request's GitHub address")
    num = lambda k, f=int: f(result.get(k) or 0)
    con.execute("""UPDATE dev_runs SET status = ?, summary = ?, pr_url = ?, head = ?, finished = ?, turns = ?, tokens_in = ?,
                   tokens_out = ?, cost_usd = ?, seconds = ? WHERE id = ?""",
                (status, summary, pr, str(result.get("head", ""))[:64], db.now(), num("turns"), num("tokens_in"),
                 num("tokens_out"), num("cost_usd", float), num("seconds"), r["run_id"]))
    if status == "pr_open":
        resume.resolve_task(con, r["task_id"])
    else:
        db.update(con, "tasks", r["task_id"], status="todo")
        if resume.is_limit(summary):
            from . import atlasplan
            atlasplan.note_limit(con, summary)
    end_lease(con, r["run_id"], "released", status)
    db.log(con, "hq", f"devloop_{status}", f"task #{r['task_id']} (worker {name})")
    con.commit()
    return f"Recorded: task #{r['task_id']} {status.replace('_', ' ')}."


def text(con) -> str:
    lines = []
    for w in listing(con):
        lines.append(f"{w['name']:<10} {w['status']:<8} {w['busy']}/{w['slots']} slot(s) busy · {w['device']}"
                     + (f" · approval #{w['approval_id']}" if w["status"] == "pending" else "")
                     + (f" · last seen {w['last_seen']}" if w["last_seen"] else ""))
    return "\n".join(lines)
