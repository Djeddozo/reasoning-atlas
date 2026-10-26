"""Priority engine: turns everything in memory into one ranked list of what to do next.

score = (impact x confidence x urgency x unlock x goal) / effort_factor

  impact      1..5, how much it moves a goal forward
  confidence  0..1, how likely it is to deliver that impact
  urgency     rises as the due date approaches; overdue tasks get the maximum
  unlock      bonus for tasks that other tasks are waiting on (foundations first)
  goal        bonus for tasks linked to an active goal
  focus       bonus for tasks under the direction chosen in Focus (hq.compass); without a focus, no effect
  effort      sqrt of hours, so big tasks are not buried but quick wins float up

Tasks whose dependencies are not done are 'blocked' and score 0.
Weights live in config/constitution.toml under [priority] so they can be tuned.
"""
import math
from datetime import date

DEFAULTS = {"urgency_window_days": 14, "unlock_bonus": 0.25, "goal_bonus": 0.2, "phase_penalty": 0.15}


def _urgency(due: str | None, today: date, window: int) -> float:
    if not due:
        return 1.0
    days = (date.fromisoformat(due) - today).days
    if days <= 0:
        return 3.0
    if days >= window:
        return 1.0
    return 1.0 + 2.0 * (1 - days / window)


def _deps(row) -> list[int]:
    return [int(x) for x in (row["depends_on"] or "").split(",") if x.strip().isdigit()]


def _context(con) -> dict:
    """What every task's score depends on besides the task itself."""
    from . import compass
    waiting_on: dict[int, int] = {}
    for t in con.execute("SELECT depends_on FROM tasks WHERE status NOT IN ('done','dropped')"):
        for d in _deps(t):
            waiting_on[d] = waiting_on.get(d, 0) + 1
    return {"done": {r["id"] for r in con.execute("SELECT id FROM tasks WHERE status = 'done'")},
            "active_goals": {r["id"] for r in con.execute("SELECT id FROM goals WHERE status = 'active'")},
            "focus": compass.focused_goals(con), "goal_due": goal_deadlines(con), "waiting_on": waiting_on}


def _factors(t, ctx: dict, w: dict, today: date, current_phase: int) -> dict:
    """The parts of one task's score (multiplied, divided by the square root of the effort) and the result."""
    from . import compass
    blocked = any(d not in ctx["done"] for d in _deps(t))
    parts = {"impact": t["impact"], "confidence": t["confidence"],
             "urgency": round(_urgency(effective_due(t, ctx["goal_due"]), today, w["urgency_window_days"]), 3),
             "unlocks": round(1 + w["unlock_bonus"] * ctx["waiting_on"].get(t["id"], 0), 3),
             "goal": 1 + (w["goal_bonus"] if t["goal_id"] in ctx["active_goals"] else 0),
             "focus": 1 + (compass.FOCUS_BONUS if t["goal_id"] in ctx["focus"] else 0),
             "phase": round(max(0.3, 1 - w["phase_penalty"] * max(0, t["phase"] - current_phase)), 3),
             "effort_divisor": round(math.sqrt(max(t["effort_h"], 0.25)), 3)}
    if blocked or t["status"] == "proposed":
        score = 0.0
    else:
        score = (parts["impact"] * parts["confidence"] * parts["urgency"] * parts["unlocks"] * parts["goal"]
                 * parts["focus"] * parts["phase"] / parts["effort_divisor"])
    pinned = t["pinned_score"] is not None and score > 0  # Djeddi moved it by hand (hq.edit.move); blocked tasks stay at 0
    return {"parts": parts, "computed": round(score, 3), "blocked": blocked, "pinned": pinned,
            "score": round(t["pinned_score"] if pinned else score, 3)}


def rescore(con, weights: dict | None = None, today: date | None = None, current_phase: int = 0) -> int:
    """Recompute scores for all open tasks and ideas. Returns number of tasks updated."""
    w = {**DEFAULTS, **(weights or {})}
    today = today or date.today()
    tasks = con.execute("SELECT * FROM tasks WHERE status NOT IN ('done','dropped')").fetchall()
    ctx = _context(con)
    for t in tasks:
        f = _factors(t, ctx, w, today, current_phase)
        status = t["status"]
        if f["blocked"] and status in ("todo", "doing"):
            status = "blocked"
        elif not f["blocked"] and status == "blocked":
            status = "todo"
        con.execute("UPDATE tasks SET score = ?, status = ? WHERE id = ?", (f["score"], status, t["id"]))

    # The Build next lane (#182): a relative pin. Its tasks rank just above every other open task, in lane order,
    # recomputed every time, so a task placed by hand cannot drift down as other scores change.
    lane = [r[0] for r in con.execute("""SELECT id FROM tasks WHERE build_next IS NOT NULL AND status IN ('todo', 'doing')
                                         ORDER BY build_next DESC""")]
    if lane:
        top = con.execute(f"SELECT COALESCE(MAX(score), 0) FROM tasks WHERE status IN ('todo', 'doing') AND id NOT IN ({','.join('?' * len(lane))})",
                          lane).fetchone()[0]
        for n, tid in enumerate(lane, 1):
            con.execute("UPDATE tasks SET score = ? WHERE id = ?", (round(float(top) + n, 3), tid))

    for i in con.execute("SELECT * FROM ideas WHERE gate != 'dropped'").fetchall():
        cost_factor = 1 + i["cost_eur"] / 100
        score = i["impact"] * i["confidence"] / (math.sqrt(max(i["effort_h"], 0.25)) * cost_factor)
        con.execute("UPDATE ideas SET score = ? WHERE id = ?", (round(score, 3), i["id"]))

    con.commit()
    return len(tasks)


def goal_deadlines(con) -> dict[int, str]:
    """Per active goal: the earliest deadline set on it or on a goal above it (Vision → Goal → Task, owner 2026-10-25).
    A deadline is the only thing about a goal that raises its tasks' priority."""
    goals = {r["id"]: (r["parent_id"], r["due"]) for r in con.execute("SELECT id, parent_id, due FROM goals WHERE status = 'active'")}
    out = {}
    for gid in goals:
        dues, seen, g = [], set(), gid
        while g in goals and g not in seen:
            seen.add(g)
            if goals[g][1]:
                dues.append(goals[g][1])
            g = goals[g][0]
        if dues:
            out[gid] = min(dues)
    return out


def effective_due(t, goal_due: dict) -> str | None:
    """The task's own deadline or its goal's, whichever comes first."""
    dues = [d for d in (t["due"], goal_due.get(t["goal_id"])) if d]
    return min(dues) if dues else None


def next_tasks(con, n: int | None = 5, owner: str | None = None):
    """Open tasks in ranked order; n=None: all of them."""
    q = "SELECT * FROM tasks WHERE status IN ('todo','doing')"
    args: list = []
    if owner:
        q += " AND owner IN (?, 'both')"
        args.append(owner)
    q += " ORDER BY status = 'doing' DESC, score DESC"
    if n is not None:
        q += " LIMIT ?"
        args.append(n)
    return con.execute(q, args).fetchall()


def explain(con, t, weights: dict | None = None, today: date | None = None) -> str:
    """One sentence on why task `t` ranks where it does, built from the same factors as the score."""
    w = {**DEFAULTS, **(weights or {})}
    today = today or date.today()
    why = [f"impact {t['impact']}/5"]
    due = effective_due(t, goal_deadlines(con))
    if due:
        days = (date.fromisoformat(due) - today).days
        whose = "" if due == t["due"] else " (its goal's deadline)"
        if days <= 0:
            why.append(("overdue" if days < 0 else "due today") + whose)
        elif days < w["urgency_window_days"]:
            why.append(f"due in {days} day{'s' if days != 1 else ''}{whose}")
    waiting = sum(t["id"] in _deps(r) for r in
                  con.execute("SELECT depends_on FROM tasks WHERE status NOT IN ('done','dropped') AND depends_on != ''"))
    if waiting:
        why.append(f"unlocks {waiting} waiting task{'s' if waiting != 1 else ''}")
    if t["goal_id"]:
        g = con.execute("SELECT title FROM goals WHERE id = ? AND status = 'active'", (t["goal_id"],)).fetchone()
        if g:
            why.append(f"serves goal '{g['title']}'")
        from . import compass
        if t["goal_id"] in compass.focused_goals(con):
            why.append("in your focus")
    why.append(f"~{t['effort_h']:g}h" + (" (quick win)" if t["effort_h"] <= 1 else ""))
    return f"#{t['id']} {t['title']} is on top: " + ", ".join(why) + "."


def score_parts(con, task_id: int, weights: dict | None = None, today: date | None = None, current_phase: int = 0) -> dict:
    """Why one task has its score, read-only (#180): the factors, the stored score, a hand-placed score, its place in
    the Build next lane and why it cannot be built now."""
    t = con.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
    if t is None:
        raise ValueError(f"task #{task_id} does not exist")
    from . import skilltree
    f = _factors(t, _context(con), {**DEFAULTS, **(weights or {})}, today or date.today(), current_phase)
    lane = t["build_next"] if "build_next" in t.keys() else None
    return {"id": t["id"], "title": t["title"], "status": t["status"], "stored_score": t["score"], "parts": f["parts"],
            "computed_score": f["computed"], "hand_placed_score": t["pinned_score"], "in_build_next": lane is not None,
            "note": ("in the Build next lane: ranks above every other open task" if lane is not None and t["status"] in ("todo", "doing")
                     else "placed by hand: its score is fixed" if f["pinned"] else "blocked: score 0" if f["blocked"] else ""),
            "blocked_reason": skilltree.blocked(con, t), "why": explain(con, t, weights, today)}


def explain_full(con, t, weights: dict | None = None, today: date | None = None) -> dict:
    """The build explanation of one task, from stored facts only (same factors as `explain`).

    Fields: problem, why_now, expected_result, goal, dependencies, effort, uncertainty, success_criterion.
    A field with nothing stored is {"missing": True, "text": ""}, never invented."""
    def field(text):
        text = (text or "").strip()
        return {"text": text, "missing": not text}

    desc = (t["description"] or "").strip()
    success = next((ln.split(":", 1)[1].strip() if ":" in ln[:25] else ln[9:].strip() for ln in desc.splitlines()
                    if ln.lower().startswith(("success criterion", "done when", "success:"))), "")
    problem = "\n".join(ln for ln in desc.splitlines() if ln.strip() and ln.strip() != success
                        and not ln.lower().startswith(("success criterion", "done when", "success:")))
    goal = con.execute("SELECT id, title, why FROM goals WHERE id = ?", (t["goal_id"],)).fetchone() if t["goal_id"] else None
    deps = []
    for i in _deps(t):
        d = con.execute("SELECT id, title, status FROM tasks WHERE id = ?", (i,)).fetchone()
        if d:
            deps.append(dict(d))
    ranked = explain(con, t, weights, today).split(" is on top: ", 1)[-1].rstrip(".")
    conf = t["confidence"]
    return {
        "id": t["id"], "title": t["title"],
        "problem": field(problem),
        "why_now": field(ranked),
        "expected_result": field(f"impact {t['impact']}/5" + (f" for the goal '{goal['title']}'" if goal else "")
                                 if t["impact"] else ""),
        "goal": {**field(goal["title"] if goal else ""), "why": (goal["why"] or "") if goal else ""},
        "dependencies": {**field(", ".join(f"#{d['id']} {d['title']} ({d['status']})" for d in deps)), "items": deps,
                         "missing": False, "none": not deps},
        "effort": field(f"about {t['effort_h']:g} h" if t["effort_h"] else ""),
        "uncertainty": field(f"{round(conf * 100)}% chance it delivers the impact" if conf is not None else ""),
        "success_criterion": field(success),
    }


def rerank(con, trigger: str, detail: str = "") -> dict:
    """Re-rank right after new information (a thought, a finished task, radar items), not only hourly.

    Logs what triggered it, whether the top task changed, and one sentence on why the top task is first."""
    from . import db, loop
    rules = loop.load_constitution()
    weights, phase = rules.get("priority"), rules.get("identity", {}).get("current_phase", 0)

    def tops():
        return {o: (r["id"] if (r := next(iter(next_tasks(con, 1, owner=o)), None)) else None) for o in ("me", "ai")}

    before = tops()
    rescore(con, weights, current_phase=phase)
    after = tops()
    changed = [o for o in after if after[o] != before[o]]
    why = {}
    for o, tid in after.items():
        if tid:
            why[o] = explain(con, con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone(), weights)
    msg = f"{trigger}{' ' + detail if detail else ''}: " + (
        "; ".join(f"top {o} #{before[o] or '-'} → #{after[o] or '-'}" for o in changed) if changed else "top unchanged")
    if why.get("me"):
        msg += f". {why['me']}"
    db.log(con, "hq", "rerank", msg[:500])
    con.commit()
    return {"trigger": trigger, "changed": changed, "top": after, "why": why}


def last_rerank(con) -> str:
    r = con.execute("SELECT ts, detail FROM log WHERE action = 'rerank' ORDER BY id DESC LIMIT 1").fetchone()
    return f"{r['ts']} — {r['detail']}" if r else ""


def why_first(con, owner: str = "me", weights: dict | None = None) -> dict:
    """The single explanation source: top task of `owner`, its one-line 'why first', and the last re-rank.

    Read by Today, Telegram /next and the brief (and the '?' pop-up / model-why display)."""
    t = next(iter(next_tasks(con, 1, owner=owner)), None)
    r = con.execute("SELECT ts, detail FROM log WHERE action = 'rerank' ORDER BY id DESC LIMIT 1").fetchone()
    return {"task": t["id"] if t else None,
            "why": explain(con, t, weights) if t else "",
            "last_rerank": {"ts": r["ts"], "detail": r["detail"]} if r else None}
