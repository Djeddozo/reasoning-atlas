"""The repository's RA steps as ordinary tasks in the plan (owner, 2026-10-25).

There is no separate "RA build queue" any more. Every unfinished RA step in STATE/tasks.json is one task in the
Engine's plan, under one goal ("Build Reasoning Atlas"), owned by the builder and first in its queue; its status
follows the repository on every sync: a step marked done there is closed here (logged and undoable), a step that is
in review or waits on another step is held with the reason. Stable RA identifiers keep each task's run history.
`sync` runs by itself (the one-minute watcher and the hourly cycle), so the plan never needs a button to catch up.

Also here, because the builder uses them for any task: the usage-limit wait (provider-reported reset times kept
apart from estimates; the watcher never forces a run) and the owner's switch that lets the builder merge its own
pull requests for RA steps.
"""
import json
import hashlib
import re
from datetime import datetime, timedelta

from . import db

PREFIX = "Atlas build plan: "  # titles before 2026-10-25; still recognised
GOAL = "Build Reasoning Atlas"
TASKS = db.ROOT / "STATE" / "tasks.json"
MERGE_KEY, LIMIT_KEY = "atlasplan:auto_merge", "devloop:limit_until"


def repository_steps() -> list[dict]:
    try:
        data = json.loads(TASKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [t for t in data["tasks"] if str(t.get("id", "")).startswith("RA-")]


def ready_steps() -> list[dict]:
    return [t for t in repository_steps() if t.get("status") == "ready"]


def step_id(task) -> str:
    title = str(task["title"] or "")
    match = re.match(r"^(RA-\d+)\b", title) or (re.search(r"\b(RA-\d+)\b", title) if title.startswith(PREFIX) else None)
    return match.group(1) if match else ""


def block_reason(task, steps=None) -> str:
    identifier = step_id(task)
    if not identifier:
        return ""
    items = {t["id"]: t for t in (repository_steps() if steps is None else steps)}
    item = items.get(identifier)
    if not item:
        return identifier + " is absent from the repository plan"
    if item.get("automationHold"):
        return item["automationHold"]
    if item["status"] not in ("ready", "todo"):
        return identifier + ": repository status " + item["status"]
    deps = [d for d in item.get("dependsOn", []) if items.get(d, {}).get("status") != "done"]
    return "Waiting for " + ", ".join(deps) + " to be completed in the repository" if deps else ""


def _goal(con) -> int:
    row = con.execute("SELECT id FROM goals WHERE title = ? AND status = 'active'", (GOAL,)).fetchone()
    if row:
        return row[0]
    from . import brain
    gid = db.insert(con, "goals", title=GOAL, horizon="year", status="active",
                    why="The repository's own build plan (STATE/tasks.json, steps RA-xxx), followed by the builder first.")
    brain.record(con, "create", "goals", gid, "the repository's RA steps live in the plan")
    return gid


def sync(con) -> dict:
    """Mirror the repository's RA steps into the plan. Idempotent; never reopens what the owner dropped."""
    from . import brain, skilltree
    steps = repository_steps()
    if not steps:
        return {"added": 0, "closed": 0, "updated": 0}
    revision = hashlib.sha256(json.dumps(steps, sort_keys=True).encode()).hexdigest()
    existing = {}
    for r in con.execute("SELECT id, title, status, goal_id FROM tasks WHERE title LIKE 'RA-%' OR title LIKE ? ORDER BY id",
                         (PREFIX + "%",)):
        if step_id(r):
            existing.setdefault(step_id(r), dict(r))
    gid, added, closed, updated = None, 0, 0, 0
    for t in steps:
        old = existing.get(t["id"])
        title = f"{t['id']} · {t['title']}"[:200]
        if t["status"] == "done":
            if old and old["status"] not in ("done", "dropped"):
                brain.set_field(con, "tasks", old["id"], "status", "done", f"{t['id']} is done in the repository plan")
                closed += 1
            continue
        if old and old["status"] == "dropped":
            continue  # the owner dropped it here: never brought back automatically
        gid = gid or _goal(con)
        desc = (f"Build only {t['id']}: {t['title']}. Program: {t.get('program', 'atlas')}; path: {t.get('path', '')}. "
                "Read AGENTS.md, STATE/active-task.json and STATE/tasks.json. Check what already exists before building; "
                "if it is already done, record the evidence and mark the step done instead. One RA step per run. "
                f"Acceptance: {t.get('acceptance', '')}. Validation: {t.get('validation', '')}. "
                "Record evidence, update STATE/tasks.json, test and checkpoint.")
        if old:
            busy = con.execute("SELECT 1 FROM dev_runs WHERE task_id = ? AND status IN ('running', 'pr_open')", (old["id"],)).fetchone()
            fields = {"title": title, "goal_id": old["goal_id"] or gid}
            if not busy:
                fields["description"] = desc
            if old["status"] == "done":  # closed here, but the repository still lists it as open
                fields["status"] = "todo"
            if any(con.execute(f"SELECT {k} FROM tasks WHERE id = ?", (old["id"],)).fetchone()[0] != v for k, v in fields.items()):
                db.update(con, "tasks", old["id"], **fields)
                updated += 1
            tid = old["id"]
        else:
            tid = db.insert(con, "tasks", title=title, description=desc, owner="ai", impact=5, effort_h=2, goal_id=gid)
            brain.record(con, "create", "tasks", tid, f"{t['id']} from the repository plan")
            added += 1
        if t["status"] in ("ready", "todo") and not con.execute("SELECT queued_by FROM tasks WHERE id = ?", (tid,)).fetchone()[0]:
            skilltree.queue(con, tid, mine=True, top=True)  # the repository plan goes first for the builder
    db.set_state(con, "atlasplan:revision", revision)
    if added or closed or updated:
        db.log(con, "hq", "atlasplan_sync", f"{added} added, {closed} closed, {updated} updated")
    con.commit()
    return {"added": added, "closed": closed, "updated": updated}


def due(con) -> bool:
    revision = hashlib.sha256(json.dumps(repository_steps(), sort_keys=True).encode()).hexdigest()
    return revision != db.get_state(con, "atlasplan:revision", "")


def auto_merge(con) -> bool:
    return db.get_state(con, MERGE_KEY, "") == "1"


def set_auto_merge(con, on: bool) -> str:
    db.set_state(con, MERGE_KEY, "1" if on else "")
    db.log(con, "me", "atlasplan_auto_merge", "on" if on else "off")
    con.commit()
    return ("The builder merges its own pull requests for RA steps once the Pi's tests pass." if on
            else "Pull requests for RA steps wait for you to merge them.")


def parsed_reset(text: str, now: datetime | None = None):
    """Use provider-reported time and zone; unknown reset times stay unknown."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    now = now or datetime.now().astimezone()
    m = re.search(r"\|(\d{10})\b", text or "")
    if m:
        return datetime.fromtimestamp(int(m.group(1)), tz=now.tzinfo)
    zone = re.search(r"\(([A-Za-z_]+/[A-Za-z_/]+)\)", text or "")
    if zone:
        try:
            now = now.astimezone(ZoneInfo(zone.group(1)))
        except ZoneInfoNotFoundError:
            return None
    m = re.search(r"(?:resets?|try again)(?: at)?\s+(\d{1,2})(?::(\d{2}))?\s?([ap])\.?m?", text or "", re.I)
    if not m:
        m = re.search(r"(?:resets?|try again)(?: at)?\s+(\d{1,2}):(\d{2})()", text or "", re.I)
    if not m:
        return None
    h = int(m.group(1))
    if m.group(3):
        if not 1 <= h <= 12: return None
        h = h % 12 + (12 if m.group(3).lower() == "p" else 0)
    minute = int(m.group(2) or 0)
    if h > 23 or minute > 59: return None
    t = now.replace(hour=h, minute=minute, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def until(text: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    return parsed_reset(text, now) or now + timedelta(hours=5)


def note_limit(con, text: str, observed_at: datetime | None = None) -> None:
    observed_at = observed_at or datetime.now().astimezone()
    reported = parsed_reset(text, observed_at)
    retry = reported or observed_at + timedelta(hours=5)
    db.set_state(con, LIMIT_KEY, retry.isoformat(timespec="minutes"))
    db.set_state(con, "devloop:reset_info", json.dumps({
        "reported": bool(reported), "at": reported.isoformat(timespec="minutes") if reported else "",
        "retry_at": retry.isoformat(timespec="minutes"), "source": "Claude Code usage-limit response",
        "observed_at": observed_at.isoformat(timespec="seconds"), "message": text[:300]}))


def waiting(con) -> bool:
    try:
        value = datetime.fromisoformat(db.get_state(con, LIMIT_KEY, ""))
        return value > datetime.now(value.tzinfo)
    except (ValueError, TypeError):
        return False


def reset_status(con) -> dict:
    try:
        info = json.loads(db.get_state(con, "devloop:reset_info", "{}"))
    except ValueError:
        info = {}
    return {**info, "waiting": waiting(con), "retry_at": db.get_state(con, LIMIT_KEY, ""),
            "watch": json.loads(db.get_state(con, "atlasplan:watch", "{}"))}


def tick(con):
    """One-minute watcher: keep the RA steps in the plan current, and start the next run after a usage-limit reset
    when autonomy is Continuous. Never forces a run."""
    from . import devloop, usage
    db.set_state(con, "atlasplan:watch", json.dumps({"checked_at": datetime.now().astimezone().isoformat(timespec="seconds"), "interval_seconds": 60}))
    con.commit()
    if due(con):
        sync(con)
    if devloop.autonomy(con) != "continuous" or not devloop.cfg()["enabled"]: return "not continuous"
    if devloop.paused(con) or usage.killed() or waiting(con): return "waiting"
    if devloop.batch_full(con) or devloop.limit_reached(con): return "blocked"
    if not devloop.can_start(con): return "blocked" if devloop.current(con) else "nothing ready"  # a free slot and work for it (#185)
    return devloop.start_background(force=False)


def is_plan_task(task) -> bool:
    return bool(step_id(task))


if __name__ == "__main__":
    con = db.connect()
    try:
        print(tick(con))
    finally:
        con.close()
