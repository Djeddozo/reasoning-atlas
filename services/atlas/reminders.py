"""Reminders: things waiting on the owner.

Two sources, one list:
- Agent reminders live in the repository (STATE/owner-reminders.json), so every
  agent and computer sees them and they are versioned with the plan.
- Owner reminders are written in the app (Quick note > Remind me) and stay in
  the local SQLite database with the notes; they never enter Git.
Completion is stored locally for both, so ticking an item never edits the repo.
"""
import json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_FILE = ROOT / "STATE" / "owner-reminders.json"
SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (id TEXT PRIMARY KEY, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', createdAt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reminder_state (id TEXT PRIMARY KEY, doneAt TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def migrate(db):
    db.executescript(SCHEMA)
    if db.execute("PRAGMA user_version").fetchone()[0] < 2:
        db.execute("PRAGMA user_version=2")


def agent_items(path=None):
    try:
        data = json.loads(Path(path or AGENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = []
    for item in data.get("reminders", []):
        if item.get("id") and item.get("title"):
            items.append({"id": item["id"], "title": item["title"], "detail": item.get("detail", ""),
                          "link": item.get("link"), "group": item.get("group"), "source": "agent",
                          "createdAt": item.get("createdAt")})
    return items


def list_all(db, agent_path=None):
    db.row_factory = sqlite3.Row
    done = {r["id"]: r["doneAt"] for r in db.execute("SELECT id, doneAt FROM reminder_state")}
    owner = [{**dict(r), "link": None, "group": None, "source": "owner"}
             for r in db.execute("SELECT * FROM reminders ORDER BY createdAt DESC")]
    items = owner + agent_items(agent_path)
    for item in items:
        item["doneAt"] = done.get(item["id"])
    return items


def add(db, identifier, title, detail=""):
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError("Reminder title must contain 1–300 characters")
    if not isinstance(detail, str) or len(detail) > 20000:
        raise ValueError("Reminder detail is too long")
    identifier = "owner-" + str(uuid.UUID(identifier))
    db.execute("INSERT OR IGNORE INTO reminders VALUES (?,?,?,?)", (identifier, title.strip(), detail, now()))
    row = db.execute("SELECT title, detail FROM reminders WHERE id=?", (identifier,)).fetchone()
    if tuple(row) != (title.strip(), detail):
        raise ValueError("Reminder identity already exists with different content")
    return identifier


def set_done(db, identifier, done, agent_path=None):
    known = {i["id"] for i in agent_items(agent_path)}
    known |= {r[0] for r in db.execute("SELECT id FROM reminders")}
    if identifier not in known:
        raise KeyError("Unknown reminder")
    if done:
        db.execute("INSERT INTO reminder_state VALUES (?,?) ON CONFLICT(id) DO UPDATE SET doneAt=excluded.doneAt "
                   "WHERE reminder_state.doneAt IS NULL", (identifier, now()))
    else:
        db.execute("INSERT INTO reminder_state VALUES (?,NULL) ON CONFLICT(id) DO UPDATE SET doneAt=NULL", (identifier,))
