"""Editing the plan by hand: tasks, ideas, goals and skill-tree capabilities (dashboard ✎ and + buttons).

Every field is validated, and every change goes through the brain's change log (hq.brain.set_field /
record), so Djeddi's edits show up in "What changed" and can be undone like Claude's. Nothing is deleted:
removing an item sets it to dropped.
"""
from datetime import date

from . import brain, db

OWNERS = brain.OWNERS
HORIZONS = {"goal", "life", "week", "month", "quarter", "year"}  # vision (life) or goal; the time layers are legacy

# table → field → kind; the dashboard builds its form from the same names
FIELDS = {
    "tasks": {"title": "title", "description": "text", "owner": OWNERS, "impact": "impact", "effort_h": "hours",
              "due": "date", "goal_id": "goal", "capability_id": "capability", "depends_on": "task_ids",
              "status": {"todo", "doing", "dropped"}},  # done is agent-only (hq.flow.complete)
    "ideas": {"title": "title", "description": "text", "category": brain.CATEGORIES, "impact": "impact",
              "effort_h": "hours", "capability_id": "capability", "gate": {"idea", "dropped"}},
    "goals": {"title": "title", "why": "text", "horizon": HORIZONS, "due": "date", "status": {"active", "paused", "achieved", "dropped"}},
    "capabilities": {"title": "title", "description": "text", "branch": None, "requires": "capability_ids",
                     "status": {"auto", "unlocked", "dropped"}},
}


def _ids(value) -> list[int]:
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    return [int(x) for x in str(value or "").replace(" ", "").split(",") if x]


def clean(con, table: str, field: str, value, row_id: int | None = None):
    """The validated value, or ValueError with a message for Djeddi."""
    from . import skilltree
    if table not in FIELDS or field not in FIELDS[table]:
        raise ValueError(f"{field} cannot be edited here")
    kind = FIELDS[table][field]
    if field == "branch":
        kind = skilltree.BRANCH_KEYS
    if isinstance(kind, set):
        if value not in kind:
            raise ValueError(f"{field} must be one of: {', '.join(sorted(kind))}")
        return value
    if kind == "title":
        v = str(value or "").strip()
        if not v:
            raise ValueError("the title cannot be empty")
        return v[:200]
    if kind == "text":
        return str(value or "").strip()[:4000]
    if kind == "impact":
        v = int(value)
        if not 1 <= v <= 5:
            raise ValueError("impact is 1 to 5")
        return v
    if kind == "hours":
        v = float(value)
        if v < 0 or v > 1000:
            raise ValueError("hours must be between 0 and 1000")
        return v
    if kind == "date":
        return date.fromisoformat(str(value)).isoformat() if value else None
    if kind == "goal":
        if value in (None, ""):
            return None
        if not con.execute("SELECT 1 FROM goals WHERE id = ? AND status != 'dropped'", (int(value),)).fetchone():
            raise ValueError(f"goal #{value} does not exist")
        return int(value)
    if kind == "capability":
        if value in (None, ""):
            return None
        if not con.execute("SELECT 1 FROM capabilities WHERE id = ? AND status != 'dropped'", (int(value),)).fetchone():
            raise ValueError(f"capability #{value} does not exist")
        return int(value)
    if kind == "task_ids":
        ids = _ids(value)
        for i in ids:
            if i == row_id:
                raise ValueError("a task cannot wait for itself")
            if not con.execute("SELECT 1 FROM tasks WHERE id = ?", (i,)).fetchone():
                raise ValueError(f"task #{i} does not exist")
        return ",".join(str(i) for i in dict.fromkeys(ids))
    if kind == "capability_ids":
        ids = _ids(value)
        for i in ids:
            if not con.execute("SELECT 1 FROM capabilities WHERE id = ? AND status != 'dropped'", (i,)).fetchone():
                raise ValueError(f"capability #{i} does not exist")
        if row_id and skilltree.would_loop(con, row_id, ids):
            raise ValueError("that would make the tree go in a circle")
        return ",".join(str(i) for i in dict.fromkeys(ids))
    raise ValueError(f"{field} cannot be edited here")


def edit(con, table: str, row_id: int, fields: dict, reason: str = "edited by Djeddi") -> str:
    """Change several fields at once; all are validated before anything is written."""
    if table not in FIELDS:
        raise ValueError(f"cannot edit {table}")
    if not con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone():
        raise ValueError(f"{table} #{row_id} does not exist")
    values = {f: clean(con, table, f, v, row_id) for f, v in fields.items()}
    changed = [f for f, v in values.items() if brain.set_field(con, table, row_id, f, v, reason)]
    if table == "tasks" and "owner" in changed:  # giving a task to the AI by hand puts it in his own queue
        from . import skilltree
        skilltree.queue(con, row_id, mine=values["owner"] in ("ai", "both"))
    db.log(con, "me", "edit", f"{table} #{row_id}: {', '.join(changed) or 'no change'}")
    con.commit()
    return f"Saved ({', '.join(changed)})." if changed else "Nothing changed."


def create(con, table: str, fields: dict, reason: str = "added by Djeddi") -> int:
    """A new task, idea, goal or capability, logged (undo sets it aside)."""
    from . import skilltree
    if table not in FIELDS:
        raise ValueError(f"cannot add to {table}")
    if table == "capabilities":
        return skilltree.add(con, clean(con, table, "title", fields.get("title")), fields.get("branch") or "make",
                             _ids(clean(con, table, "requires", fields.get("requires") or "")),
                             clean(con, table, "description", fields.get("description", "")), reason=reason)
    values = {f: clean(con, table, f, v) for f, v in fields.items() if v not in (None, "") or f == "title"}
    rid = db.insert(con, table, **values)
    brain.record(con, "create", table, rid, reason)
    if table == "tasks" and values.get("owner") in ("ai", "both"):
        skilltree.queue(con, rid)
    db.log(con, "me", "create", f"{table} #{rid}")
    con.commit()
    return rid


def move(con, task_id: int, direction: str) -> str:
    """Move a task one place up or down in its queue, or 'auto' to give it back to the computed ranking.

    Tasks in Djeddi's own agent queue swap places there (queued_at); all others swap places in the ranked list
    (the same order as `hq next`) by pinning their scores, which rescoring then keeps."""
    if direction not in ("up", "down", "auto"):
        raise ValueError("direction is up, down or auto")
    t = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not t or t["status"] in ("done", "dropped"):
        raise ValueError(f"task #{task_id} is not open")
    if direction == "auto":
        con.execute("UPDATE tasks SET pinned_score = NULL WHERE id = ?", (task_id,))
        db.log(con, "me", "move", f"tasks #{task_id}: auto")
        con.commit()
        from . import priority
        priority.rerank(con, "task order", f"#{task_id} back to automatic")
        return f"Task #{task_id} follows the computed ranking again."
    if t["build_next"] is not None:  # in the Build next lane: move within it (#182)
        from . import skilltree
        return skilltree.move_in_lane(con, task_id, direction)
    mine = t["queued_by"] == "me" and t["owner"] in ("ai", "both")
    if mine:
        rows = con.execute("""SELECT id, queued_at FROM tasks WHERE queued_by = 'me' AND owner IN ('ai', 'both')
                              AND status NOT IN ('done', 'dropped') ORDER BY status = 'doing' DESC, queued_at, id""").fetchall()
    else:
        rows = con.execute("""SELECT id, score FROM tasks WHERE status IN ('todo', 'doing') AND COALESCE(queued_by, '') != 'me'
                              ORDER BY status = 'doing' DESC, score DESC, id""").fetchall()
    ids = [r["id"] for r in rows]
    if task_id not in ids:
        raise ValueError(f"task #{task_id} is not in a queue that can be reordered (it is waiting on another task)")
    i = ids.index(task_id)
    j = i - 1 if direction == "up" else i + 1
    if not 0 <= j < len(ids):
        return f"Task #{task_id} is already {'first' if direction == 'up' else 'last'}."
    a, b = rows[i], rows[j]
    if mine:
        va, vb = a["queued_at"], b["queued_at"]
        if va == vb:  # identical timestamps: spread them so the swap shows
            vb = va + "~"
        con.execute("UPDATE tasks SET queued_at = ? WHERE id = ?", (vb, a["id"]))
        con.execute("UPDATE tasks SET queued_at = ? WHERE id = ?", (va, b["id"]))
    else:
        va, vb = a["score"], b["score"]
        if va == vb:
            va, vb = (va + 0.001, vb) if direction == "up" else (va, vb + 0.001)
        con.execute("UPDATE tasks SET pinned_score = ?, score = ? WHERE id = ?", (vb, vb, a["id"]))
        con.execute("UPDATE tasks SET pinned_score = ?, score = ? WHERE id = ?", (va, va, b["id"]))
    db.log(con, "me", "move", f"tasks #{task_id}: {direction} (past #{b['id']})")
    con.commit()
    return f"Task #{task_id} moved {direction}, past #{b['id']}."
