"""Task do-over: one reviewed diff of the whole open plan (keep / change / done / drop / merge / new).

A do-over is written as a list of items, checked against the database, and stored as a proposal: one row in
`approvals` (the diff itself lives in `state` under `doover:<approval id>`). Nothing changes until Djeddi
approves it (phone, dashboard or `hq approvals <id> approved`); the hourly cycle then applies it, or
`hq doover apply <id>` does so at once. Every change goes through the brain's change log in one batch
(`doover-<id>`), so `hq doover undo <id>` (or /undo per change) sets it back. Nothing is deleted.

Item kinds
  keep    {"kind": "keep", "task": 4, "reason": "..."}
  change  {"kind": "change", "task": 7, "fields": {"due": "2026-10-25", "depends_on": ""}, "reason": "..."}
  done    {"kind": "done", "task": 8, "reason": "already built: ..."}        (already implemented)
  drop    {"kind": "drop", "task": 31, "reason": "..."}
  merge   {"kind": "merge", "task": 24, "into": "N1", "reason": "..."}       (into a task id or a new item's key)
  new     {"kind": "new", "key": "N1", "fields": {"title": "...", "owner": "ai", ...}, "after": [12, "N2"], "reason": "..."}

A dropped or merged task is removed from the `depends_on` of the tasks waiting on it (a merged one is
replaced by the task it went into), so nothing stays blocked behind a task that will never be done.

Re-evaluate (`replan`, the Today tab's "Re-evaluate tasks" button, `hq doover replan`): the brain re-evaluation
runs first (small, undoable changes); then `drift` checks whether the plan has drifted further than such
changes can fix. If so, and no do-over is already waiting, Claude drafts a whole-plan do-over that is stored
as a proposal like any other: nothing beyond the brain's small changes happens until Djeddi approves it.
"""
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import brain, db, edit, models

KINDS = ("keep", "change", "done", "drop", "merge", "new", "goal_change", "goal_place", "goal_drop", "goal_merge",
         "idea_change", "idea_drop")
GOAL_KINDS = ("goal_change", "goal_place", "goal_drop", "goal_merge")
IDEA_KINDS = ("idea_change", "idea_drop")
IDEA_FIELDS = {"title", "description"}
GOAL_FIELDS = {"title", "why", "due"}  # Vision → Goal → Task: a deadline, no time layers (owner, 2026-10-25)
CHANGE_FIELDS = set(edit.FIELDS["tasks"]) - {"status"}  # status changes are their own kinds (done, drop)
OPEN = ("todo", "doing", "blocked", "proposed")
MISSION = Path(__file__).resolve().parent.parent / "docs" / "MISSION.md"
# drift thresholds: past these, small re-prioritisation no longer keeps the plan honest
OVERDUE_MAX, STALE_DAYS, STALE_MAX, CHANGES_MAX = 3, 30, 5, 6

DRAFT = """You are the planning brain of Atlas, a personal management system for one person (Djeddi).
Re-evaluate the whole open plan top-down and write one do-over of it.

Top-down means: his VISIONS (lifetime aims, with year objectives under them) are the reference. Every goal must
serve a vision: keep it, place it under the vision it serves, change it, merge it, or drop it when no vision needs
it any more. Every task must serve a goal that still matters: a task whose goal is dropped, or that serves no
vision, is dropped or changed. Judge against what Atlas is NOW (ATLAS below): the former HQ app is merged into
Atlas and its name is retired; work that only maintains the former HQ app as a separate thing (its own dashboard, its separate
repository, its name) is outdated, unless it brings one of its functions into Atlas.

For each open task choose one item:
  {"kind": "keep", "task": ID, "reason": "..."}
  {"kind": "change", "task": ID, "fields": {"impact": 4, "due": "YYYY-MM-DD" or null, "owner": "me|ai|both",
                                            "goal_id": ID, "effort_h": 2, "depends_on": "1,2", "title": "..."}, "reason": "..."}
  {"kind": "done", "task": ID, "reason": "already built: ..."}
  {"kind": "drop", "task": ID, "reason": "..."}
  {"kind": "merge", "task": ID, "into": ID or "N1", "reason": "..."}
For goals (only where something should change; unchanged goals need no item):
  {"kind": "goal_change", "goal": ID, "fields": {"title": "...", "why": "..."}, "reason": "..."}  (never invent a deadline: only Djeddi sets "due")
  {"kind": "goal_place", "goal": ID, "under": VISION_OR_GOAL_ID, "reason": "..."}
  {"kind": "goal_drop", "goal": ID, "reason": "..."}
  {"kind": "goal_merge", "goal": ID, "into": GOAL_ID, "reason": "..."}
Visions themselves are Djeddi's: never change, merge or drop a vision.
For open ideas (IDEAS below; only where something should change):
  {"kind": "idea_change", "idea": ID, "fields": {"title": "...", "description": "..."}, "reason": "..."}
  {"kind": "idea_drop", "idea": ID, "reason": "outdated: ..." or "already built: ..."}
Ideas are a brainstorming room: drop one only when what Atlas is now makes it outdated or it already exists; an idea
that might still be worth something stays, even when no goal fits it yet.
Add missing work only where a priority clearly has no task:
  {"kind": "new", "key": "N1", "fields": {"title": "...", "owner": "me|ai", "impact": 3, "goal_id": ID}, "after": [ID or "N2"], "reason": "..."}
Rules: every item needs a short reason grounded in the visions, goals, thoughts or progress; each task and goal at most
once; prefer fixing and simplifying over adding; "ai" only for software work in the Atlas repository.

Reply with ONLY JSON: {"summary": "at most two short sentences in plain words for Djeddi: what changes mainly and why (no task numbers)", "items": [...]}"""


def _task(con, tid):
    return con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()


def validate(con, items: list[dict]) -> list[dict]:
    """Return cleaned items, or raise ValueError naming the first problem. Every open task may appear once."""
    if not isinstance(items, list) or not items:
        raise ValueError("a do-over needs at least one item")
    keys = {it.get("key") for it in items if isinstance(it, dict) and it.get("kind") == "new"}
    seen, out = set(), []
    for n, it in enumerate(items, 1):
        if not isinstance(it, dict) or it.get("kind") not in KINDS:
            raise ValueError(f"item {n}: kind must be one of {', '.join(KINDS)}")
        kind, reason = it["kind"], str(it.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"item {n}: every item needs a reason")
        clean = {"kind": kind, "reason": reason[:300]}
        if kind in IDEA_KINDS:
            iid = it.get("idea")
            row = con.execute("SELECT id FROM ideas WHERE id = ? AND gate = 'idea'", (iid,)).fetchone() if isinstance(iid, int) else None
            if row is None:
                raise ValueError(f"item {n}: idea {iid!r} is not an open idea")
            if ("idea", iid) in seen:
                raise ValueError(f"item {n}: idea #{iid} appears twice")
            seen.add(("idea", iid))
            clean["idea"] = iid
            if kind == "idea_change":
                fields = {f: " ".join(str(v or "").split()) if f == "title" else str(v or "").strip()
                          for f, v in dict(it.get("fields") or {}).items() if f in IDEA_FIELDS}
                fields = {f: v[:200] if f == "title" else v[:2000] for f, v in fields.items() if v}
                if not fields:
                    raise ValueError(f"item {n}: idea_change needs a title or description")
                clean["fields"] = fields
            out.append(clean)
            continue
        if kind in GOAL_KINDS:
            gid = it.get("goal")
            g = con.execute("SELECT * FROM goals WHERE id = ? AND status = 'active'", (gid,)).fetchone() if isinstance(gid, int) else None
            if g is None:
                raise ValueError(f"item {n}: goal {gid!r} is not an active goal")
            if g["horizon"] == "life" and g["parent_id"] is None:
                raise ValueError(f"item {n}: #{gid} is a vision; visions are Djeddi's to change")
            if ("goal", gid) in seen:
                raise ValueError(f"item {n}: goal #{gid} appears twice")
            seen.add(("goal", gid))
            clean["goal"] = gid
            if kind == "goal_change":
                fields = {f: v for f, v in dict(it.get("fields") or {}).items() if f in GOAL_FIELDS}
                if not fields:
                    raise ValueError(f"item {n}: goal_change needs fields from title, why, due")
                clean["fields"] = {f: edit.clean(con, "goals", f, v, gid) for f, v in fields.items()}
            elif kind in ("goal_place", "goal_merge"):
                target = it.get("under" if kind == "goal_place" else "into")
                ok = isinstance(target, int) and target != gid and con.execute(
                    "SELECT 1 FROM goals WHERE id = ? AND status = 'active'", (target,)).fetchone()
                if not ok:
                    raise ValueError(f"item {n}: {kind} needs another active goal")
                clean["under" if kind == "goal_place" else "into"] = target
            out.append(clean)
            continue
        if kind == "new":
            key, fields = str(it.get("key") or ""), dict(it.get("fields") or {})
            if not key or key.isdigit():
                raise ValueError(f"item {n}: a new task needs a key such as 'N1'")
            fields.pop("status", None)
            clean |= {"key": key, "fields": {f: edit.clean(con, "tasks", f, v) for f, v in fields.items()
                                             if f != "depends_on" and (v not in (None, "") or f == "title")}}
            if "title" not in clean["fields"]:
                raise ValueError(f"item {n}: a new task needs a title")
            after = list(it.get("after") or [])
            for a in after:
                if not (a in keys or isinstance(a, int) and _task(con, a)):
                    raise ValueError(f"item {n}: 'after' {a!r} is neither a task nor a new item")
            clean["after"] = after
            out.append(clean)
            continue
        tid = it.get("task")
        row = _task(con, tid) if isinstance(tid, int) else None
        if row is None:
            raise ValueError(f"item {n}: task {tid!r} does not exist")
        if row["status"] not in OPEN:
            raise ValueError(f"item {n}: task #{tid} is already {row['status']}")
        if tid in seen:
            raise ValueError(f"item {n}: task #{tid} appears twice")
        seen.add(tid)
        clean["task"] = tid
        if kind == "change":
            fields = dict(it.get("fields") or {})
            bad = set(fields) - CHANGE_FIELDS
            if not fields or bad:
                raise ValueError(f"item {n}: change needs fields from {', '.join(sorted(CHANGE_FIELDS))}")
            clean["fields"] = {f: edit.clean(con, "tasks", f, v, tid) for f, v in fields.items()}
        elif kind == "merge":
            into = it.get("into")
            if into == tid or not (into in keys or isinstance(into, int) and _task(con, into)):
                raise ValueError(f"item {n}: merge needs 'into' (another task id or a new item's key)")
            clean["into"] = into
        out.append(clean)
    return out


def counts(items: list[dict]) -> dict:
    return {k: sum(it["kind"] == k for it in items) for k in KINDS}


def render(con, items: list[dict]) -> str:
    """The diff as text: one line per item, grouped by kind."""
    title = lambda tid: (_task(con, tid) or {"title": "?"})["title"] if isinstance(tid, int) else ""
    labels = {"keep": "KEEP", "change": "CHANGE", "done": "CLOSE (already built)", "drop": "DROP",
              "merge": "MERGE", "new": "NEW", "goal_change": "GOAL CHANGE", "goal_place": "GOAL UNDER A VISION",
              "goal_drop": "GOAL DROP", "goal_merge": "GOAL MERGE", "idea_change": "IDEA CHANGE", "idea_drop": "IDEA DROP"}
    lines = []
    for kind in KINDS:
        group = [it for it in items if it["kind"] == kind]
        if not group:
            continue
        lines.append(f"{labels[kind]} ({len(group)})")
        for it in group:
            if kind in IDEA_KINDS:
                i = con.execute("SELECT title FROM ideas WHERE id = ?", (it["idea"],)).fetchone()
                lines.append(f"  idea #{it['idea']} {i[0] if i else '?'} — {it['reason']}")
                continue
            if kind in GOAL_KINDS:
                g = con.execute("SELECT title FROM goals WHERE id = ?", (it["goal"],)).fetchone()
                lines.append(f"  goal #{it['goal']} {g[0] if g else '?'} — {it['reason']}")
                continue
            if kind == "new":
                f = it["fields"]
                extra = ", ".join(f"{k}={v}" for k, v in f.items() if k not in ("title", "description"))
                after = f"; after {', '.join(map(str, it['after']))}" if it["after"] else ""
                lines.append(f"  + {it['key']} {f['title']} ({extra}{after}) — {it['reason']}")
                continue
            head = f"  #{it['task']} {title(it['task'])}"
            if kind == "change":
                row = _task(con, it["task"])
                diff = "; ".join(f"{k}: {row[k]!r} → {v!r}" for k, v in it["fields"].items()
                                 if k != "description" and row[k] != v)
                if "description" in it["fields"]:
                    diff += ("; " if diff else "") + "description updated"
                head += f"  [{diff}]"
            elif kind == "merge":
                head += f"  → {'#' if isinstance(it['into'], int) else ''}{it['into']}"
            lines.append(f"{head} — {it['reason']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def propose(con, items: list[dict], summary: str = "") -> int:
    """Store a validated do-over as a pending approval. Returns the approval id."""
    items = validate(con, items)
    c = counts(items)
    words = {"change": "changed", "done": "closed as built", "drop": "dropped", "merge": "merged", "new": "new", "keep": "kept"}
    moves = [f"{c[k]} {words[k]}" for k in ("change", "done", "drop", "merge", "new") if c.get(k)]
    goals = sum(c.get(k, 0) for k in GOAL_KINDS)
    if goals:
        moves.append(f"{goals} goal{'s' if goals > 1 else ''} reshaped")
    ideas = sum(c.get(k, 0) for k in IDEA_KINDS)
    if ideas:
        moves.append(f"{ideas} idea{'s' if ideas > 1 else ''} tidied")
    request = "Plan re-evaluation: " + (", ".join(moves) or "no changes") + (f" ({c['keep']} kept)" if c.get("keep") else "") \
        + ". Details: hq doover show <id>."
    aid = db.insert(con, "approvals", request=request, reason=(summary or "re-evaluation against docs/MISSION.md")[:500],
                    category="action", status="pending", created=db.now())
    db.update(con, "approvals", aid, request=request.replace("<id>", str(aid)))
    db.set_state(con, f"doover:{aid}", json.dumps({"items": items, "summary": summary, "applied": ""}))
    db.log(con, "agent", "doover_propose", f"approval #{aid}: {request}")
    con.commit()
    return aid


def changes(con, aid: int) -> dict:
    """The do-over in short, for Atlas's Today: the summary and, per kind, the titles with their reasons."""
    d = load(con, aid)
    title = lambda tid: (_task(con, tid) or {"title": "?"})["title"] if isinstance(tid, int) else ""
    groups: dict[str, list] = {}
    for it in d["items"]:
        if it["kind"] in IDEA_KINDS:
            i = con.execute("SELECT title FROM ideas WHERE id = ?", (it["idea"],)).fetchone()
            name = f"Idea: {i[0] if i else '?'}"
        elif it["kind"] in GOAL_KINDS:
            g = con.execute("SELECT title FROM goals WHERE id = ?", (it["goal"],)).fetchone()
            name = f"Goal: {g[0] if g else '?'}"
        else:
            name = it["fields"]["title"] if it["kind"] == "new" else title(it["task"])
        groups.setdefault(it["kind"], []).append({"title": name, "reason": it.get("reason", "")})
    return {"summary": d.get("summary", ""), "counts": counts(d["items"]), "groups": groups}


def load(con, aid: int) -> dict:
    data = db.get_state(con, f"doover:{aid}")
    if not data:
        raise ValueError(f"approval #{aid} is not a do-over")
    return json.loads(data)


def show(con, aid: int) -> str:
    d = load(con, aid)
    status = con.execute("SELECT status FROM approvals WHERE id = ?", (aid,)).fetchone()
    head = f"Do-over #{aid} ({status[0] if status else '?'}{', applied ' + d['applied'] if d['applied'] else ''})"
    return "\n".join(x for x in (head, d.get("summary", ""), "", render(con, d["items"])) if x is not None)


def _rewire(con, old: int, new: int | None, batch: str) -> None:
    """Tasks that waited on `old` wait on `new` instead (or on nothing more)."""
    for r in con.execute("SELECT id, depends_on FROM tasks WHERE status NOT IN ('done', 'dropped')").fetchall():
        deps = [int(x) for x in (r["depends_on"] or "").replace(" ", "").split(",") if x]
        if old not in deps:
            continue
        deps = [new if d == old else d for d in deps if not (d == old and (new is None or new == r["id"]))]
        brain.set_field(con, "tasks", r["id"], "depends_on", ",".join(str(d) for d in dict.fromkeys(deps)),
                        f"#{old} {'merged into #' + str(new) if new else 'dropped'} in the do-over", batch)


def apply(con, aid: int) -> str:
    """Apply an approved do-over in one undoable batch. Re-validates against the plan as it is now."""
    row = con.execute("SELECT status FROM approvals WHERE id = ?", (aid,)).fetchone()
    d = load(con, aid)
    if row is None or row["status"] != "approved":
        raise ValueError(f"do-over #{aid} is not approved ({row['status'] if row else 'missing'})")
    if d["applied"]:
        return f"Do-over #{aid} was already applied on {d['applied']}."
    items = validate(con, d["items"])  # the plan may have moved since the proposal
    batch, ids = f"doover-{aid}", {}
    for it in items:  # new tasks first, so merges and 'after' can point at them
        if it["kind"] == "new":
            ids[it["key"]] = tid = db.insert(con, "tasks", **it["fields"])
            brain.record(con, "create", "tasks", tid, it["reason"], batch=batch)
    resolve = lambda x: ids[x] if x in ids else x
    for it in items:
        if it["kind"] == "new" and it["after"]:
            db.update(con, "tasks", ids[it["key"]], depends_on=",".join(str(resolve(a)) for a in it["after"]))
        elif it["kind"] == "change":
            for f, v in it["fields"].items():
                brain.set_field(con, "tasks", it["task"], f, v, it["reason"], batch)
        elif it["kind"] == "done":
            brain.set_field(con, "tasks", it["task"], "status", "done", it["reason"], batch)
        elif it["kind"] == "drop":
            brain.set_field(con, "tasks", it["task"], "status", "dropped", it["reason"], batch)
            _rewire(con, it["task"], None, batch)
        elif it["kind"] == "merge":
            into, title = resolve(it["into"]), _task(con, it["task"])["title"]
            desc = (_task(con, into)["description"] or "").strip()
            brain.set_field(con, "tasks", into, "description", (desc + "\n" if desc else "") + f"(merged: #{it['task']} {title})",
                            it["reason"], batch)
            brain.set_field(con, "tasks", it["task"], "status", "dropped", f"merged into #{into}: {it['reason']}", batch)
            _rewire(con, it["task"], into, batch)
    for it in items:
        if it["kind"] == "idea_change":
            for f, v in it["fields"].items():
                brain.set_field(con, "ideas", it["idea"], f, v, it["reason"], batch)
        elif it["kind"] == "idea_drop":
            brain.set_field(con, "ideas", it["idea"], "gate", "dropped", it["reason"], batch)
        elif it["kind"] == "goal_change":
            for f, v in it["fields"].items():
                brain.set_field(con, "goals", it["goal"], f, v, it["reason"], batch)
        elif it["kind"] == "goal_place":
            old = con.execute("SELECT parent_id FROM goals WHERE id = ?", (it["goal"],)).fetchone()[0]
            from . import plan
            plan.set_parent(con, it["goal"], it["under"])  # refuses loops
            brain.record(con, "update", "goals", it["goal"], it["reason"], "parent_id", old, it["under"], batch=batch)
        elif it["kind"] == "goal_drop":
            brain.set_field(con, "goals", it["goal"], "status", "dropped", it["reason"], batch)
        elif it["kind"] == "goal_merge":
            for r in con.execute("SELECT id FROM tasks WHERE goal_id = ? AND status NOT IN ('done', 'dropped')", (it["goal"],)).fetchall():
                brain.set_field(con, "tasks", r[0], "goal_id", it["into"], f"goal merged into #{it['into']}", batch)
            for r in con.execute("SELECT id FROM goals WHERE parent_id = ? AND status = 'active'", (it["goal"],)).fetchall():
                brain.set_field(con, "goals", r[0], "parent_id", it["into"], f"goal merged into #{it['into']}", batch)
            brain.set_field(con, "goals", it["goal"], "status", "dropped", f"merged into #{it['into']}: {it['reason']}", batch)
    d["applied"] = db.now()
    db.set_state(con, f"doover:{aid}", json.dumps(d))
    db.log(con, "agent", "doover_apply", f"#{aid}: " + ", ".join(f"{n} {k}" for k, n in counts(items).items() if n))
    con.commit()
    _rescore(con)
    return f"Applied do-over #{aid} (undo: hq doover undo {aid})."


def _rescore(con) -> None:
    from . import loop, priority
    rules = loop.load_constitution()
    priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))


def apply_approved(con) -> list[int]:
    """Called by the hourly cycle: apply every approved do-over that has not been applied yet."""
    done = []
    for r in con.execute("SELECT key FROM state WHERE key LIKE 'doover:%'").fetchall():
        aid = int(r["key"].split(":", 1)[1])
        status = con.execute("SELECT status FROM approvals WHERE id = ?", (aid,)).fetchone()
        if status and status[0] == "approved" and not load(con, aid)["applied"]:
            try:
                apply(con, aid)
                done.append(aid)
            except ValueError as e:
                db.log(con, "hq", "doover_failed", f"#{aid}: {e}")
                con.commit()
    return done


def undo(con, aid: int) -> str:
    """Undo every change of an applied do-over, newest first."""
    rows = con.execute("SELECT id FROM brain_changes WHERE batch = ? AND undone = 0 ORDER BY id DESC",
                       (f"doover-{aid}",)).fetchall()
    for r in rows:
        brain.undo(con, r["id"])
    _rescore(con)
    return f"Undid {len(rows)} changes of do-over #{aid}." if rows else f"Nothing to undo for do-over #{aid}."


# ---------------- re-evaluate: drift check and a drafted do-over ----------------

def pending(con) -> int | None:
    """The id of a do-over still waiting for approval, if any (one at a time)."""
    for r in con.execute("""SELECT a.id FROM approvals a JOIN state s ON s.key = 'doover:' || a.id
                            WHERE a.status = 'pending' ORDER BY a.id DESC"""):
        return r[0]
    return None


def drift(con, reevaluation: dict | None = None) -> list[str]:
    """Reasons the plan has drifted beyond what small re-prioritisation fixes (empty: it has not)."""
    today = date.today().isoformat()
    stale_before = (datetime.now() - timedelta(days=STALE_DAYS)).isoformat(timespec="seconds")
    one = lambda q, *a: con.execute(q, a).fetchone()[0]
    reasons = []
    overdue = one("SELECT COUNT(*) FROM tasks WHERE status IN ('todo', 'doing', 'blocked') AND due IS NOT NULL AND due < ?", today)
    if overdue >= OVERDUE_MAX:
        reasons.append(f"{overdue} tasks overdue")
    stale = one("""SELECT COUNT(*) FROM tasks WHERE status IN ('todo', 'blocked', 'proposed')
                   AND COALESCE(updated, created, '') < ?""", stale_before)
    if stale >= STALE_MAX:
        reasons.append(f"{stale} tasks untouched for {STALE_DAYS}+ days")
    gone = {r[0] for r in con.execute("SELECT id FROM tasks WHERE status = 'dropped'")}
    stuck = [r["id"] for r in con.execute("SELECT id, depends_on FROM tasks WHERE status NOT IN ('done', 'dropped')")
             if gone & {int(x) for x in re.findall(r"\d+", r["depends_on"] or "")}]
    if stuck:
        reasons.append(f"{len(stuck)} task(s) wait on dropped tasks ({', '.join(f'#{i}' for i in stuck[:5])})")
    suggest = brain.focus(con).get("suggest") or []
    if suggest:
        reasons.append(f"{len(suggest)} drop/merge suggestion(s) open")
    if reevaluation and reevaluation.get("applied", 0) >= CHANGES_MAX:
        reasons.append(f"the re-evaluation changed {reevaluation['applied']} things at once")
    if structure_changed(con):
        reasons.append("Atlas's structure or mission changed since the plan was last re-checked")
    return reasons


STRUCTURE_KEY = "replan_structure"


def structure_signature() -> str:
    """What the plan is judged against: Atlas's structure and the mission. A change there is a major change (RA-057)."""
    import hashlib
    h = hashlib.sha1()
    for f in (ATLAS_NOW, MISSION):
        h.update(f.read_bytes() if f.exists() else b"")
    return h.hexdigest()[:16]


def structure_changed(con) -> bool:
    return db.get_state(con, STRUCTURE_KEY, "") != structure_signature()


ATLAS_NOW = Path(__file__).resolve().parent.parent / "docs" / "STRUCTURE.md"


def _draft_context(con, why: list[str]) -> str:
    from . import compass
    d = compass.directions(con)
    visions = [{"id": v["id"], "title": v["title"], "why": v["why"],
                "objectives": [{"id": o["id"], "title": o["title"], "horizon": o["horizon"]} for o in v["objectives"]]}
               for v in d["visions"]]
    atlas = ATLAS_NOW.read_text(encoding="utf-8")[:5000] if ATLAS_NOW.exists() else ""
    goals = [dict(r) | {"open_tasks": r["n"]} for r in con.execute(
        """SELECT g.id, g.title, g.horizon, g.why, g.parent_id, COUNT(t.id) AS n FROM goals g
           LEFT JOIN tasks t ON t.goal_id = g.id AND t.status NOT IN ('done', 'dropped')
           WHERE g.status = 'active' GROUP BY g.id""")]
    ideas = [{"id": r["id"], "title": r["title"], "category": r["category"], "about": (r["description"] or "")[:200]}
             for r in con.execute("SELECT id, title, category, description FROM ideas WHERE gate = 'idea' ORDER BY score DESC LIMIT 60")]
    return (f"VISIONS (the reference; focus: #{d['current'] or 'none'}):\n{json.dumps(visions)}\n\n"
            f"IDEAS (open):\n{json.dumps(ideas)}\n\n"
            f"GOALS (with parent and open tasks):\n{json.dumps(goals, default=str)}\n\n"
            f"ATLAS NOW (its structure; the former HQ app is part of it, its name retired):\n{atlas}\n\n"
            f"WHY NOW: {'; '.join(why)}\n\nPLAN:\n{brain._context(con)}")


def _usable(con, items) -> tuple[list[dict], int]:
    """Drop the items that do not validate (model output), one at a time. Returns (items, dropped)."""
    items = [dict(it, task=int(it["task"])) if isinstance(it, dict) and str(it.get("task", "")).isdigit() else it
             for it in (items if isinstance(items, list) else [])]
    items = [dict(it, into=int(it["into"])) if isinstance(it, dict) and str(it.get("into", "")).isdigit() else it
             for it in items]
    dropped = 0
    while items:
        try:
            return validate(con, items), dropped
        except ValueError as e:
            m = re.match(r"item (\d+):", str(e))
            if not m:
                break
            del items[int(m.group(1)) - 1]
            dropped += 1
    return [], dropped


def draft(con, why: list[str]) -> dict:
    """Ask Claude for a whole-plan do-over and store it as a proposal. Changes no task."""
    try:
        out = models.parse_json(models.complete("doover", DRAFT, _draft_context(con, why)))
        if not isinstance(out, dict):
            raise ValueError("not an object")
    except (models.ModelUnavailable, ValueError) as e:
        return {"approval": None, "error": f"no do-over drafted ({str(e)[:120]})"}
    db.set_state(con, STRUCTURE_KEY, structure_signature())  # judged against this structure, whatever Claude found
    items, dropped = _usable(con, out.get("items"))
    if not [it for it in items if it["kind"] != "keep"]:
        return {"approval": None, "dropped": dropped, "error": "Claude found nothing to change"}
    summary = str(out.get("summary") or "").strip()
    aid = propose(con, items, summary or "The plan drifted from the mission and goals.")  # why it drifted: in the log
    return {"approval": aid, "dropped": dropped}


def replan(con, force: bool = False) -> dict:
    """Brain re-evaluation, then a do-over proposal when the plan has drifted; force (the owner's button):
    always a whole-plan do-over, judged top-down against the visions."""
    r = brain.reevaluate(con)
    why = drift(con, r) or (["Djeddi asked for a full re-evaluation"] if force else [])
    out = {"reevaluate": r, "drift": why, "approval": None}
    waiting = pending(con)
    if why and waiting:
        out["approval"], out["note"] = waiting, f"do-over #{waiting} is still waiting for approval"
    elif why:
        out |= draft(con, why)
    db.log(con, "agent", "replan", f"applied={r.get('applied', 0)} drift={len(why)} doover={out['approval'] or ''}")
    con.commit()
    return out


def replan_text(out: dict) -> str:
    """One short message for the dashboard and the CLI."""
    r = out["reevaluate"]
    parts = [r["error"] if r.get("error") else f"Re-evaluated: {r['applied']} change(s)."]
    if not out["drift"]:
        parts.append("The plan has not drifted; no do-over needed.")
    elif out.get("note"):
        parts.append(f"Drift ({'; '.join(out['drift'])}), but {out['note']}.")
    elif out["approval"]:
        parts.append(f"Drift ({'; '.join(out['drift'])}): do-over #{out['approval']} waits for your approval (Today tab).")
    else:
        parts.append(f"Drift ({'; '.join(out['drift'])}); {out.get('error', 'no do-over drafted')}.")
    return " ".join(parts)
