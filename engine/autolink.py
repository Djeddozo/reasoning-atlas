"""Automatic node linking: Claude says which skill tree node each unlinked open task builds.

The first build step of the zoom-out view, whichever option Djeddi picks (docs/design/ZOOM_OUT.md §3):
two thirds of the queue built no node, so any 'where is this heading' view would show it as unsorted.

  - candidates  open tasks (not done or dropped) without capability_id whose link nobody has touched before:
                a task with any earlier capability_id change in brain_changes (Djeddi's edit, a re-evaluation,
                an undone auto-link) is left alone, so an undo or a deliberate unlink is never overridden
  - Claude      gets the nodes (title, branch, description, state) and the tasks, and names one node per task,
                or none when no node fits; it never creates nodes here (re-evaluation does that)
  - apply       each link through brain.set_field: logged in brain_changes with its reason, one batch per run,
                undone as a whole with `hq tree autolink --undo` (or one change with `hq brain undo <n>`)

Runs daily before the re-evaluation (deploy/schedules.sh) and on request (`hq tree autolink [--dry]`).
Only Atlas's own planning data is read and written; task titles are Djeddi's or Atlas's own words.
"""
import json

from . import brain, db, models

MAX_TASKS = 30          # tasks per run (one model call); the rest follow the next day
PREFIX = "link-"        # batch name prefix in brain_changes

SYSTEM = """You link tasks to the skill tree of Atlas, a personal management system for one person (Djeddi).
The skill tree lists capabilities ("nodes"): systems or skills Atlas has or could have. A task "builds" a node when
finishing it adds to or improves that capability. You get the nodes and a list of tasks that build no node yet.
For each task, choose the single node it most clearly builds. If no node fits well, give null; a wrong link is
worse than none. Do not invent nodes. Keep each reason under 15 words.

Reply with ONLY JSON:
{"links":[{"task_id":0,"capability_id":0,"reason":""}]}"""


def candidates(con, limit: int = MAX_TASKS) -> list[dict]:
    """Open tasks without a node whose link has never been set or changed before (by anyone)."""
    return [dict(r) for r in con.execute(
        """SELECT t.id, t.title, t.description, t.owner, t.status, t.goal_id FROM tasks t
           WHERE t.capability_id IS NULL AND t.status NOT IN ('done', 'dropped')
             AND NOT EXISTS (SELECT 1 FROM brain_changes b WHERE b.table_name = 'tasks' AND b.row_id = t.id
                             AND b.field = 'capability_id')
           ORDER BY t.status = 'proposed', t.score DESC, t.id LIMIT ?""", (limit,))]


def _context(con, tasks: list[dict], caps: dict) -> str:
    goals = {r[0]: r[1] for r in con.execute("SELECT id, title FROM goals")}
    return json.dumps({
        "nodes": [{"id": c["id"], "title": c["title"], "branch": c["branch"], "state": c["state"],
                   "description": (c.get("description") or "")[:200]} for c in caps.values()],
        "tasks": [{"id": t["id"], "title": t["title"], "description": (t["description"] or "")[:200],
                   "owner": t["owner"], "goal": goals.get(t["goal_id"], "")} for t in tasks],
    })


def _is_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)  # JSON true would otherwise read as id 1


def suggest(con, limit: int = MAX_TASKS) -> dict:
    """Claude's proposed links, validated; nothing is written. {'links': [...], 'unlinked': [...], 'error': ''}"""
    from . import skilltree
    tasks = candidates(con, limit)
    if not tasks:
        return {"links": [], "unlinked": [], "error": ""}
    caps = skilltree.nodes(con)
    if not caps:  # nothing to link to: no paid call
        return {"links": [], "unlinked": [t["id"] for t in tasks], "error": "", "no_nodes": True}
    try:
        out = models.parse_json(models.complete("link", SYSTEM, _context(con, tasks, caps)))
        if not isinstance(out, dict):
            raise ValueError("not an object")
    except (models.ModelUnavailable, ValueError) as e:
        return {"links": [], "unlinked": [t["id"] for t in tasks], "error": models.why_failed(e)}
    by_id = {t["id"]: t for t in tasks}
    links, seen = [], set()
    for ln in out.get("links") or []:
        if not isinstance(ln, dict):
            continue
        tid, cid = ln.get("task_id"), ln.get("capability_id")
        if not _is_id(tid) or tid not in by_id or tid in seen or not _is_id(cid) or cid not in caps:
            continue  # unknown task, a second link for one task, no node, or a node that does not exist
        seen.add(tid)
        links.append({"task_id": tid, "task": by_id[tid]["title"], "capability_id": cid, "node": caps[cid]["title"],
                      "reason": str(ln.get("reason") or "")[:200]})
    return {"links": links, "unlinked": [t["id"] for t in tasks if t["id"] not in seen], "error": ""}


def run(con, dry: bool = False, limit: int = MAX_TASKS) -> dict:
    """Suggest and (unless dry) apply the links in one undoable batch."""
    r = suggest(con, limit)
    r["batch"], r["applied"], r["dry"] = "", 0, dry
    if dry or not r["links"]:
        if r["error"]:
            db.log(con, "agent", "autolink", r["error"]); con.commit()
        return r
    last = con.execute("SELECT COALESCE(MAX(id), 0) FROM brain_changes").fetchone()[0]
    batch = f"{PREFIX}{db.now()}#{last + 1}"  # unique even for two runs within one second
    still = {t["id"] for t in candidates(con, limit=10_000)}  # the model call takes time: skip tasks linked meanwhile
    for ln in r["links"]:
        if ln["task_id"] not in still:
            continue
        r["applied"] += brain.set_field(con, "tasks", ln["task_id"], "capability_id", ln["capability_id"],
                                        f"auto-link: {ln['reason'] or 'builds ' + ln['node']}", batch)
    r["batch"] = batch
    db.log(con, "agent", "autolink", f"{r['applied']} task(s) linked to skill tree nodes, {len(r['unlinked'])} left ({batch})")
    con.commit()
    return r


def undo_last(con) -> str:
    """Undo the links of the most recent auto-link run; links changed again later (by Djeddi) stay as they are."""
    row = con.execute("SELECT batch FROM brain_changes WHERE batch LIKE ? ORDER BY id DESC LIMIT 1",
                      (PREFIX + "%",)).fetchone()
    if not row:
        return "No auto-links to undo."
    n, kept = 0, 0
    for c in con.execute("SELECT id FROM brain_changes WHERE batch = ? AND undone = 0 ORDER BY id DESC", (row[0],)).fetchall():
        try:
            brain.undo(con, c[0]); n += 1
        except ValueError:  # the link was changed again later: his change stays
            kept += 1
    con.commit()
    if not n:
        return "Nothing left to undo in the last auto-link run" + (f" ({kept} changed again later)." if kept else ".")
    return f"Undid {n} auto-link(s)" + (f"; {kept} kept because they were changed again later." if kept else ".")


def text(r: dict) -> str:
    if r.get("error"):
        return f"Auto-link skipped: {r['error']}"
    if r.get("no_nodes"):
        return f"The skill tree has no nodes yet, so {len(r['unlinked'])} open task(s) stay unlinked. Nothing was changed."
    if not r["links"] and not r["unlinked"]:
        return "Every open task already builds a node (or was linked by hand)."
    if r.get("dry"):
        lines = [f"Would link {len(r['links'])} task(s); {len(r['unlinked'])} fit no node."]
    else:
        skipped = len(r["links"]) - r["applied"]
        lines = [f"Linked {r['applied']} task(s); {len(r['unlinked'])} fit no node."
                 + (f" {skipped} skipped: linked or changed meanwhile." if skipped else "")]
    lines += [f"• #{ln['task_id']} {ln['task'][:60]} → {ln['node']}  ({ln['reason']})" for ln in r["links"]]
    if r.get("applied"):
        lines.append("Undo all: hq tree autolink --undo")
    return "\n".join(lines)
