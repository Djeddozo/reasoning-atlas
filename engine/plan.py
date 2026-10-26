"""The plan as one tree: mission → goals (nested by horizon) → tasks.

Goals can sit under a bigger goal (`goals.parent_id`): life → year → quarter → month → week.
Each goal shows its own tasks (in progress, next up, blocked, done) and its progress counts
everything beneath it. Tasks that belong to no goal are listed separately, so nothing is hidden.
Used by the dashboard (Plan tab), `hq plan` and Telegram /plan.
"""
from . import db, loop, priority

HORIZONS = ["life", "goal", "year", "quarter", "month", "week"]  # vision first; the rest are goals (time layers are legacy)
STATUS_ORDER = {"doing": 0, "todo": 1, "blocked": 2, "proposed": 3}


def _rank(h: str) -> int:
    return HORIZONS.index(h) if h in HORIZONS else len(HORIZONS)


def tree(con, include_done_titles: int = 3) -> dict:
    rules = loop.load_constitution()
    priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
    goals = {g["id"]: dict(g) | {"children": [], "tasks": [], "done_tasks": []}
             for g in con.execute("SELECT * FROM goals WHERE status = 'active'")}
    loose = []
    from . import skilltree
    queue = {tid: n + 1 for n, tid in enumerate(skilltree.mine_order(con))}
    for t in con.execute("""SELECT id, title, owner, status, due, phase, effort_h, score, goal_id, done_at, impact, depends_on, created
                            FROM tasks WHERE status != 'dropped' ORDER BY score DESC, id"""):
        t = dict(t)
        if t["status"] != "done":  # Build in the plan: in the queue (where), or why it cannot be built yet
            t["queued"] = queue.get(t["id"])
            t["blocked"] = skilltree.blocked(con, t)
        g = goals.get(t["goal_id"])
        if g is None:
            if t["status"] != "done":
                loose.append(t)
        elif t["status"] == "done":
            g["done_tasks"].append(t)
        else:
            g["tasks"].append(t)

    roots = []
    for g in goals.values():
        parent = goals.get(g.get("parent_id"))
        (parent["children"] if parent and parent is not g else roots).append(g)

    def finish(g: dict, depth: int, seen: set) -> tuple[int, int]:
        seen.add(g["id"])
        g["children"] = [c for c in g["children"] if c["id"] not in seen]
        g["children"].sort(key=lambda c: (_rank(c["horizon"]), c["id"]))
        g["tasks"].sort(key=lambda t: (STATUS_ORDER.get(t["status"], 9), -(t["score"] or 0)))
        g["done_tasks"].sort(key=lambda t: t["done_at"] or "", reverse=True)
        done, total = len(g["done_tasks"]), len(g["done_tasks"]) + len(g["tasks"])
        for c in g["children"]:
            d, t = finish(c, depth + 1, seen)
            done, total = done + d, total + t
        g.update(depth=depth, done=done, total=total, pct=round(100 * done / total) if total else 0,
                 recent_done=[t["title"] for t in g["done_tasks"][:include_done_titles]], done_count=len(g["done_tasks"]))
        del g["done_tasks"]
        return done, total

    roots.sort(key=lambda g: (_rank(g["horizon"]), g["id"]))
    seen: set = set()
    for g in roots:
        finish(g, 0, seen)
    for g in sorted(goals.values(), key=lambda g: g["id"]):  # goals caught in a parent loop: show them anyway
        if g["id"] not in seen:
            roots.append(g); finish(g, 0, seen)
    return {"mission": rules["identity"].get("mission", ""), "goals": roots,
            "loose": sorted(loose, key=lambda t: (t["phase"] or 0, STATUS_ORDER.get(t["status"], 9), -(t["score"] or 0)))}


def _exists(con, table: str, row_id: int) -> None:
    if not con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone():
        raise ValueError(f"{table[:-1]} #{row_id} does not exist")


def set_parent(con, goal_id: int, parent_id: int | None) -> None:
    """Put a goal under another one (None = top level). Refuses loops and unknown ids."""
    _exists(con, "goals", goal_id)
    if parent_id is not None:
        if parent_id == goal_id:
            raise ValueError("a goal cannot be its own parent")
        p = parent_id
        while p is not None:
            row = con.execute("SELECT parent_id FROM goals WHERE id = ?", (p,)).fetchone()
            if row is None:
                raise ValueError(f"goal #{parent_id} does not exist")
            if row[0] == goal_id:
                raise ValueError("that would put the goal inside itself")
            p = row[0]
    db.update(con, "goals", goal_id, parent_id=parent_id)
    db.log(con, "me", "goal_parent", f"#{goal_id} → {parent_id or 'top'}")
    con.commit()


def link_task(con, task_id: int, goal_id: int | None) -> None:
    _exists(con, "tasks", task_id)
    if goal_id is not None:
        _exists(con, "goals", goal_id)
    db.update(con, "tasks", task_id, goal_id=goal_id)
    db.log(con, "me", "task_goal", f"#{task_id} → {goal_id or 'none'}")
    con.commit()


MARK = {"doing": "▶", "todo": "○", "blocked": "⏸", "proposed": "?"}


def text(t: dict, tasks_per_goal: int = 4) -> str:
    """Indented tree for the terminal and Telegram."""
    lines = [f"Mission: {t['mission']}", ""]

    def goal(g: dict):
        pad = "   " * g["depth"]
        lines.append(f"{pad}◆ {g['title']}  [{g['horizon']}] {g['done']}/{g['total']} ({g['pct']}%) · goal #{g['id']}")
        for task in g["tasks"][:tasks_per_goal]:
            due = f", due {task['due']}" if task["due"] else ""
            lines.append(f"{pad}   {MARK.get(task['status'], '·')} #{task['id']} {task['title']} ({task['owner']}{due})")
        if len(g["tasks"]) > tasks_per_goal:
            lines.append(f"{pad}   … {len(g['tasks']) - tasks_per_goal} more open")
        if g["done_count"]:
            lines.append(f"{pad}   ✓ {g['done_count']} done")
        for c in g["children"]:
            goal(c)

    for g in t["goals"]:
        goal(g)
    if not t["goals"]:
        lines.append("No goals yet: ./atlas.sh add goal \"...\" --horizon year")
    if t["loose"]:
        lines += ["", f"Not linked to a goal ({len(t['loose'])}):"]
        lines += [f"   {MARK.get(x['status'], '·')} #{x['id']} {x['title']}" for x in t["loose"][:8]]
        if len(t["loose"]) > 8:
            lines.append(f"   … {len(t['loose']) - 8} more")
        lines.append("   Link one: ./atlas.sh attach <task> <goal>")
    lines += ["", "▶ in progress  ○ to do  ⏸ blocked by another task"]
    return "\n".join(lines)
