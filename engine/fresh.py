"""Just added: tasks, ideas and goals created recently stand out for a short while.

One rule: an item is new while less than `window` minutes have passed since it was created
(default 60). The window is the owner's setting (state 'fresh_minutes', 5 minutes to one day),
changed on Today. Screens read the `new` flag this module adds; nothing here changes the plan.
"""
from datetime import datetime, timedelta

from . import db

DEFAULT_MINUTES = 60
CHOICES = (15, 30, 60, 120, 240, 1440)
MIN_MINUTES, MAX_MINUTES = 5, 1440


def window(con) -> int:
    try:
        m = int(db.get_state(con, "fresh_minutes", "") or DEFAULT_MINUTES)
    except ValueError:
        m = DEFAULT_MINUTES
    return max(MIN_MINUTES, min(m, MAX_MINUTES))


def cutoff(con, now: datetime | None = None) -> str:
    return ((now or datetime.now()) - timedelta(minutes=window(con))).isoformat(timespec="seconds")


def is_new(created, since: str) -> bool:
    """`created` and `since` are local ISO timestamps (db.now()); an unknown date is never new."""
    if not created:
        return False
    try:
        return datetime.fromisoformat(str(created)) >= datetime.fromisoformat(since)
    except ValueError:
        return False


def mark(rows, since: str) -> list:
    """Add `new` to each dict in place and return the list."""
    for r in rows:
        r["new"] = is_new(r.get("created"), since)
    return rows


def recent(con, now: datetime | None = None, limit: int = 8) -> dict:
    """Today's 'Just added': open tasks, open ideas and active goals created within the window, newest first."""
    since = cutoff(con, now)
    items = [dict(r) for r in con.execute(
        """SELECT 'task' AS kind, id, title, created FROM tasks WHERE status NOT IN ('done', 'dropped') AND created >= ?
           UNION ALL SELECT 'idea', id, title, created FROM ideas WHERE gate = 'idea' AND created >= ?
           UNION ALL SELECT 'goal', id, title, created FROM goals WHERE status = 'active' AND created >= ?
           ORDER BY created DESC, id DESC LIMIT ?""", (since, since, since, limit))]
    return {"minutes": window(con), "choices": list(CHOICES), "items": mark(items, since)}


def set_window(con, minutes) -> str:
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        raise ValueError("minutes must be a whole number")
    if not MIN_MINUTES <= m <= MAX_MINUTES:
        raise ValueError(f"Choose between {MIN_MINUTES} minutes and one day")
    db.set_state(con, "fresh_minutes", m)
    db.log(con, "me", "fresh_window", str(m))
    con.commit()
    return f"New items stand out for {label(m)}."


def label(m: int) -> str:
    return "one day" if m == 1440 else f"{m // 60} h" if m % 60 == 0 else f"{m} min"
