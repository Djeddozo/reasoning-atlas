"""Compass: the owner's lifetime visions, the focus among them, and the year objectives under each (ADR-023, ADR-024).

Top-down, as on Atlas's Focus page:
  vision      a top-level goal on a lifetime horizon, in big words ("Advance the intelligence of Atlas",
              "Own products and income", "Financial stability")
  goal        anything under a vision ("Atlas finds opportunities and proposes new skills itself"); goals may
              hold smaller goals. No time layers (owner, 2026-10-25): a goal may have a deadline the owner sets,
              and only that deadline raises the priority of its tasks (priority.goal_deadlines).
Focus picks one vision (state key `focus_direction`); Atlas's ranking (`priority.rescore`) gives all work under
it a bonus, so the Forge's queue follows the focus without hiding the rest.

The recommendation is rule-based and explains itself. A lifetime is never "behind", so a vision is judged by
its objectives on their own timelines:
  behind      a goal with a deadline whose progress lags behind the share of its time already used
  neglect     weeks since the vision last moved
  due soon    work under it due within the urgency window
  unlocks     open work under it that other tasks wait on
  empty       a vision with no objectives yet needs a first one
  momentum    a small bonus for recent progress, so the focus does not jump around
Nothing here calls a model or writes, except `set_focus`, `create`, `add_objective` and `place`.
"""
from datetime import date, datetime

from . import brain, db

VISION_HORIZON = "life"
HORIZON_DAYS = {"week": 7, "month": 30, "quarter": 91, "year": 365, "life": 3650}
HORIZONS = ("week", "month", "quarter", "year", "life")
FOCUS_BONUS = 1.0           # rescore multiplies the score of work under the focused vision by (1 + this)
STATE_KEY = "focus_direction"


def _day(text: str | None) -> date | None:
    try:
        return datetime.fromisoformat(str(text)[:19]).date() if text else None
    except ValueError:
        return None


def _goals(con) -> dict[int, dict]:
    return {r["id"]: dict(r) for r in con.execute(
        "SELECT id, title, horizon, why, status, parent_id, created, due FROM goals WHERE status = 'active'")}


def subtree(con, goal_id: int, goals: dict | None = None) -> set[int]:
    """The goal and every active goal below it (loops in hand-edited data are ignored)."""
    goals = goals if goals is not None else _goals(con)
    out, todo = set(), [goal_id]
    while todo:
        g = todo.pop()
        if g in out:
            continue
        out.add(g)
        todo.extend(k for k, v in goals.items() if v.get("parent_id") == g)
    return out


def _blocks(con) -> dict[int, int]:
    out: dict[int, int] = {}
    for r in con.execute("SELECT depends_on FROM tasks WHERE status NOT IN ('done', 'dropped') AND depends_on != ''"):
        for i in (int(x) for x in str(r[0]).split(",") if x.strip().isdigit()):
            out[i] = out.get(i, 0) + 1
    return out


def _measure(con, goal: dict, ids: set[int], today: date, window_days: int, blocks: dict) -> dict:
    """Progress of the work under these goals, against the goal's own horizon."""
    marks = ",".join("?" * len(ids))
    tasks = [dict(t) for t in con.execute(
        f"SELECT id, status, due, done_at, updated FROM tasks WHERE goal_id IN ({marks}) AND status NOT IN ('dropped', 'proposed')",
        tuple(ids))]
    done = [t for t in tasks if t["status"] == "done"]
    open_ = [t for t in tasks if t["status"] != "done"]
    days = [d for d in (_day(t["done_at"]) for t in done) if d]
    moved = days + [d for d in (_day(t["updated"]) for t in tasks) if d] + [d for d in [_day(goal["created"])] if d]
    since = (today - max(moved)).days if moved else None
    start, end = _day(goal["created"]) or today, _day(goal.get("due"))
    used = min(1.0, max(0.0, (today - start).days / max(1, (end - start).days))) if end else 0.0  # only a deadline gives a timeline
    due_soon = sum(1 for t in open_ if _day(t["due"]) and (_day(t["due"]) - today).days <= window_days)
    return {"done": len(done), "total": len(tasks), "open": len(open_),
            "progress": round(len(done) / len(tasks), 3) if tasks else 0.0, "timeUsed": round(used, 3),
            "daysSinceMoved": since, "dueSoon": due_soon, "unlocks": sum(blocks.get(t["id"], 0) for t in open_),
            "recent": sum(1 for d in days if (today - d).days <= 28),
            "weekly": [sum(1 for d in days if w * 7 <= (today - d).days < (w + 1) * 7) for w in range(7, -1, -1)]}


def directions(con, today: date | None = None, window_days: int = 14) -> dict:
    """Visions with their objectives, timelines and reasons; goals that sit under no vision; the focus; the recommendation."""
    today = today or date.today()
    goals, blocks = _goals(con), _blocks(con)
    roots = [g for g in goals.values() if g["parent_id"] not in goals]
    loose = [{"id": g["id"], "title": g["title"], "horizon": g["horizon"], "due": g.get("due")} for g in roots if g["horizon"] != VISION_HORIZON]
    out = []
    for v in (g for g in roots if g["horizon"] == VISION_HORIZON):
        ids = subtree(con, v["id"], goals)
        whole = _measure(con, v, ids, today, window_days, blocks)
        objectives = []
        for o in sorted((goals[k] for k in ids if goals[k]["parent_id"] == v["id"]), key=lambda g: g["id"]):
            m = _measure(con, o, subtree(con, o["id"], goals), today, window_days, blocks)
            m["behind"] = round(m["timeUsed"] - m["progress"], 3) if m["total"] and o.get("due") else 0.0
            objectives.append({"id": o["id"], "title": o["title"], "why": o["why"], "horizon": o["horizon"], "due": o.get("due"), **m})
        score, reasons = 0.0, []
        late = [o for o in objectives if o["behind"] > 0.05]
        for o in sorted(late, key=lambda o: -o["behind"])[:2]:
            score += 3 * o["behind"]
            reasons.append(f"“{o['title']}” is {round(100 * o['progress'])}% done after {round(100 * o['timeUsed'])}% of the time to its deadline")
        if not objectives:
            score += 0.4
            reasons.append("no goals yet: what should it achieve?")
        elif not whole["total"]:
            score += 0.3
            reasons.append("its goals have no work yet")
        if whole["total"] and whole["daysSinceMoved"] is not None and whole["daysSinceMoved"] >= 14:  # only work can stall
            score += min(1.5, whole["daysSinceMoved"] / 28)
            reasons.append(f"has not moved for {whole['daysSinceMoved']} days")
        if whole["dueSoon"]:
            score += 0.6 * whole["dueSoon"]
            reasons.append(f"{whole['dueSoon']} task(s) due within {window_days} days")
        if whole["unlocks"]:
            score += 0.2 * whole["unlocks"]
            reasons.append(f"its open work unblocks {whole['unlocks']} other task(s)")
        if whole["recent"]:
            score += 0.3
            reasons.append(f"momentum: {whole['recent']} task(s) finished in the last 4 weeks")
        out.append({"id": v["id"], "title": v["title"], "why": v["why"], "created": v["created"], "objectives": objectives,
                    **{k: whole[k] for k in ("done", "total", "open", "progress", "daysSinceMoved", "dueSoon", "unlocks", "weekly")},
                    "score": round(score, 3), "reasons": reasons})
    out.sort(key=lambda d: (-d["score"], d["id"]))
    rec = out[0] if out and out[0]["score"] > 0 else None
    return {"visions": out, "loose": loose, "current": current_focus(con, goals),
            "recommended": {"id": rec["id"], "title": rec["title"], "reasons": rec["reasons"]} if rec else None}


def current_focus(con, goals: dict | None = None) -> int | None:
    goals = goals if goals is not None else _goals(con)
    value = db.get_state(con, STATE_KEY, "")
    gid = int(value) if str(value).isdigit() else None
    return gid if gid in goals else None


def focused_goals(con) -> set[int]:
    """The goals whose tasks are in focus (empty: no focus, everything ranks as before)."""
    gid = current_focus(con)
    return subtree(con, gid) if gid else set()


def set_focus(con, goal_id: int | None) -> str:
    goals = _goals(con)
    if goal_id is not None:
        g = goals.get(goal_id)
        if g is None:
            raise ValueError(f"goal #{goal_id} is not an active goal")
        if g["parent_id"] in goals or g["horizon"] != VISION_HORIZON:
            raise ValueError(f"'{g['title']}' is not a vision; focus on the vision it serves")
    db.set_state(con, STATE_KEY, goal_id or "")
    db.log(con, "me", "focus_direction", str(goal_id or "none"))
    con.commit()
    return f"Focus: {goals[goal_id]['title']}" if goal_id else "No focus: everything ranks on its own merits."


def _new_goal(con, title: str, why: str, horizon: str, parent: int | None, reason: str) -> int:
    title = (title or "").strip()
    if not title or len(title) > 120:
        raise ValueError("A title of 1–120 characters is needed")
    gid = db.insert(con, "goals", title=title, why=(why or "").strip()[:500], horizon=horizon, status="active", parent_id=parent)
    brain.record(con, "create", "goals", gid, reason)
    db.log(con, "me", "goal_created", f"#{gid} {title} ({horizon})")
    con.commit()
    return gid


def create(con, title: str, why: str = "") -> int:
    """A new lifetime vision (undoable through the change log)."""
    return _new_goal(con, title, why, VISION_HORIZON, None, "new vision (Focus)")


def add_objective(con, vision_id: int, title: str, why: str = "", due: str | None = None, horizon: str = "goal") -> int:
    """A new goal under a vision, with an optional deadline (the only thing that raises its tasks' priority)."""
    goals = _goals(con)
    if goals.get(vision_id, {}).get("horizon") != VISION_HORIZON or goals[vision_id]["parent_id"] in goals:
        raise ValueError(f"#{vision_id} is not a vision")
    gid = _new_goal(con, title, why, "goal", vision_id, f"new goal under vision #{vision_id}")
    if due:
        set_due(con, gid, due)
    return gid


def set_due(con, goal_id: int, due: str | None) -> str:
    """Set or clear a goal's deadline (undoable)."""
    if due:
        try:
            due = date.fromisoformat(str(due)[:10]).isoformat()
        except ValueError:
            raise ValueError("a deadline is a date, e.g. 2026-12-31") from None
    if not brain.set_field(con, "goals", goal_id, "due", due or None, "deadline set by Djeddi (Focus)" if due else "deadline cleared by Djeddi (Focus)"):
        return "No change."
    from . import loop, priority
    rules = loop.load_constitution()
    priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
    con.commit()
    return f"Deadline {due}: its tasks rise as it comes closer." if due else "Deadline removed."


def place(con, goal_id: int, vision_id: int | None) -> str:
    """Put a goal under a vision (or take it out)."""
    from . import plan
    plan.set_parent(con, goal_id, vision_id)
    con.commit()
    return "Placed under the vision." if vision_id else "Taken out of its vision."
