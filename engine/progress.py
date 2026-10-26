"""Progress tracking: how the plan is actually moving.

`stats(con)`   – tasks done per day (last 28 days), this week vs last week, streak, progress per goal,
                 overdue / due soon / stalled tasks, and what came in this week. Used by the dashboard
                 (Plan → Progress), `hq progress` and Telegram /progress.
`review(con)`  – the weekly review: what got done, how each goal moved, what slipped, and the focus for
                 next week. Saved as a memory (kind 'review'); `hq review --send` delivers it to Telegram.

Weeks run Monday–Sunday. A day counts towards the streak when at least one task was completed;
today not being finished yet does not break it.
"""
import json
from datetime import date, datetime, timedelta

from . import db, loop, models, priority, routing

REVIEW_SYSTEM = """You write a short weekly observation for a personal management system.
Given the week's numbers as JSON, write 2-3 plain sentences: what went well, what slipped, and one
concrete suggestion for next week. Concise, factual, no hype, no exclamation marks. Plain text only."""


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _count(con, sql: str, *args) -> int:
    return con.execute(sql, args).fetchone()[0] or 0


def streaks(done_days: set[date], today: date) -> tuple[int, int]:
    """(current streak, best streak) in days with at least one completed task."""
    current, day = 0, today if today in done_days else today - timedelta(days=1)
    while day in done_days:
        current += 1
        day -= timedelta(days=1)
    best = run = 0
    prev = None
    for d in sorted(done_days):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best, prev = max(best, run), d
    return current, best


def stats(con, today: date | None = None, days: int = 28) -> dict:
    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())
    last_week = week_start - timedelta(days=7)
    first = today - timedelta(days=days - 1)

    done_rows = con.execute("SELECT id, title, goal_id, done_at FROM tasks WHERE status = 'done' AND done_at IS NOT NULL").fetchall()
    per_day: dict[date, int] = {}
    for r in done_rows:
        d = _d(r["done_at"])
        if d:
            per_day[d] = per_day.get(d, 0) + 1
    current, best = streaks(set(per_day), today)

    def done_between(a: date, b: date) -> int:  # a inclusive, b exclusive
        return sum(n for d, n in per_day.items() if a <= d < b)

    goals = []
    for g in con.execute("SELECT * FROM goals WHERE status = 'active' ORDER BY id"):
        tasks = con.execute("SELECT status, due, done_at FROM tasks WHERE goal_id = ? AND status != 'dropped'", (g["id"],)).fetchall()
        nxt = con.execute("SELECT id, title, status FROM tasks WHERE goal_id = ? AND status IN ('todo','doing') "
                          "ORDER BY status = 'doing' DESC, score DESC, id LIMIT 1", (g["id"],)).fetchone()
        total, done = len(tasks), sum(t["status"] == "done" for t in tasks)
        goals.append({
            "id": g["id"], "title": g["title"], "horizon": g["horizon"], "total": total, "done": done,
            "pct": round(100 * done / total) if total else 0,
            "done_week": sum(1 for t in tasks if t["status"] == "done" and (_d(t["done_at"]) or date.min) >= week_start),
            "overdue": sum(1 for t in tasks if t["status"] != "done" and t["due"] and _d(t["due"]) and _d(t["due"]) < today),
            "next": {"id": nxt["id"], "title": nxt["title"], "status": nxt["status"]} if nxt else None,
        })

    open_q = "SELECT id, title, owner, due, status, updated FROM tasks WHERE status NOT IN ('done', 'dropped', 'proposed')"
    open_tasks = [dict(r) for r in con.execute(open_q)]
    overdue = sorted(({**t, "days_late": (today - _d(t["due"])).days} for t in open_tasks
                      if _d(t["due"]) and _d(t["due"]) < today), key=lambda t: -t["days_late"])
    due_soon = sorted((t for t in open_tasks if _d(t["due"]) and today <= _d(t["due"]) <= today + timedelta(days=7)),
                      key=lambda t: t["due"])
    stalled = [t for t in open_tasks if t["status"] == "doing" and (_d(t["updated"]) or today) <= today - timedelta(days=7)]

    recent = sorted((dict(r) for r in done_rows), key=lambda r: r["done_at"], reverse=True)[:10]
    models = routing.chosen(con, [r["id"] for r in recent])
    return {
        "today": today.isoformat(),
        "week_start": week_start.isoformat(),
        "daily": [{"date": (first + timedelta(days=i)).isoformat(), "done": per_day.get(first + timedelta(days=i), 0)}
                  for i in range(days)],
        "done_today": per_day.get(today, 0),
        "done_week": done_between(week_start, today + timedelta(days=1)),
        "done_last_week": done_between(last_week, week_start),
        "added_week": _count(con, "SELECT COUNT(*) FROM tasks WHERE created >= ? AND status != 'proposed'", week_start.isoformat()),
        "streak": current,
        "best_streak": best,
        "open": len(open_tasks),
        "done_total": len(done_rows),
        "goals": goals,
        "overdue": overdue[:10],
        "due_soon": due_soon[:10],
        "stalled": stalled[:10],
        "recent": [{"id": r["id"], "title": r["title"], "done_at": r["done_at"],
                    "model": models.get(r["id"], {}).get("model", ""), "model_reason": models.get(r["id"], {}).get("reason", "")}
                   for r in recent],
        "intake_week": _intake_week(con, week_start),
    }


def _intake_week(con, week_start: date) -> int:
    try:
        return _count(con, "SELECT COUNT(*) FROM intake WHERE created >= ?", week_start.isoformat())
    except Exception:  # intake table not present in older databases
        return 0


def summary(s: dict) -> str:
    """Short plain-text status for Telegram /progress and `hq progress`."""
    trend = s["done_week"] - s["done_last_week"]
    lines = [f"This week: {s['done_week']} done ({'+' if trend >= 0 else ''}{trend} vs last week), "
             f"{s['done_today']} today. Streak {s['streak']} day(s), best {s['best_streak']}."]
    for g in s["goals"]:
        if g["total"]:
            lines.append(f"• {g['title']}: {g['done']}/{g['total']} ({g['pct']}%), {g['done_week']} this week"
                         + (f", {g['overdue']} overdue" if g["overdue"] else "")
                         + (f". Next: #{g['next']['id']} {g['next']['title']}" if g.get("next") else ". Nothing open"))
    if s["overdue"]:
        lines.append("Overdue: " + "; ".join(f"#{t['id']} {t['title']} ({t['days_late']}d)" for t in s["overdue"][:5]))
    built = [t for t in s["recent"] if t.get("model")][:3]
    if built:
        lines.append("Built lately: " + "; ".join(f"#{t['id']} {t['title']} ({routing.line(t['model'], t['model_reason'])})"
                                                  for t in built))
    if s["stalled"]:
        lines.append("In progress for over a week: " + "; ".join(f"#{t['id']} {t['title']}" for t in s["stalled"][:5]))
    return "\n".join(lines)


def review(con, today: date | None = None, save: bool = True) -> str:
    """The weekly review (normally run on Sunday evening)."""
    today = today or date.today()
    s = stats(con, today)
    week_start = date.fromisoformat(s["week_start"])
    done = con.execute("SELECT id, title FROM tasks WHERE status = 'done' AND done_at >= ? ORDER BY done_at",
                       (week_start.isoformat(),)).fetchall()
    rules = loop.load_constitution()
    priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
    focus = priority.next_tasks(con, 3, owner="me")

    lines = [f"Weekly review · week of {week_start:%d %B %Y}", "",
             f"Completed {s['done_week']} task(s) (last week {s['done_last_week']}); {s['added_week']} added; "
             f"{s['intake_week']} item(s) saved via intake. Streak {s['streak']} day(s)."]
    if done:
        lines += ["", "Done:"] + [f"• {r['title']}" for r in done[:12]] + ([f"• … and {len(done) - 12} more"] if len(done) > 12 else [])
    moved = [g for g in s["goals"] if g["total"]]
    if moved:
        lines += ["", "Goals:"] + [f"• {g['title']}: {g['done']}/{g['total']} ({g['pct']}%), +{g['done_week']} this week"
                                   for g in moved]
    if s["overdue"]:
        lines += ["", "Slipped:"] + [f"• #{t['id']} {t['title']} (due {t['due']})" for t in s["overdue"][:6]]
    if focus:
        lines += ["", "Focus next week:"] + [f"• #{t['id']} {t['title']}" for t in focus]

    try:  # optional observation; the numbers above never depend on a model
        brief = {k: s[k] for k in ("done_week", "done_last_week", "added_week", "streak", "open")}
        brief.update(goals=[{k: g[k] for k in ("title", "done", "total", "done_week", "overdue")} for g in moved],
                     overdue=[t["title"] for t in s["overdue"][:6]], done=[r["title"] for r in done[:12]])
        note = models.complete("review", REVIEW_SYSTEM, json.dumps(brief)).strip()
        if note:
            lines += ["", note[:800]]
    except (models.ModelUnavailable, ValueError):
        pass

    text = "\n".join(lines)
    if save:
        db.insert(con, "memories", kind="review", source="hq", tags="weekly review", content=text)
        db.log(con, "hq", "weekly_review", f"done={s['done_week']}")
        con.commit()
    return text


def last_review(con) -> dict | None:
    r = con.execute("SELECT content, created FROM memories WHERE kind = 'review' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None
