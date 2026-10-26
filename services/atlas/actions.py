"""For you: what the owner has to do, with its context, a checklist and room for the owner's input.

Shown in Notes › For you, and readable from any terminal session:
    python -m services.atlas.actions list            # open items with progress
    python -m services.atlas.actions show <id>       # context, checklist, answered questions, owner input
    python -m services.atlas.actions prompt <id>     # the same as a ready-to-paste prompt for Claude/Codex

Items are written by agents in the repository (STATE/owner-actions.json), so every agent and computer sees
them and they are versioned with the plan. Ticks, the owner's input and "done" are stored in the local
SQLite database next to the notes and never enter Git. Remove an item from the file once it is settled.
"""
import argparse, json, sqlite3, sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_FILE = ROOT / "STATE" / "owner-actions.json"
DB_PATH = ROOT / "runtime" / "atlas.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS action_steps (actionId TEXT NOT NULL, stepId TEXT NOT NULL, doneAt TEXT, PRIMARY KEY (actionId, stepId));
CREATE TABLE IF NOT EXISTS action_state (id TEXT PRIMARY KEY, input TEXT NOT NULL DEFAULT '', inputAt TEXT, doneAt TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def migrate(db):
    db.executescript(SCHEMA)
    if db.execute("PRAGMA user_version").fetchone()[0] < 5:
        db.execute("PRAGMA user_version=5")


def agent_items(path=None):
    try:
        data = json.loads(Path(path or AGENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = []
    for item in data.get("actions", []):
        if not (item.get("id") and item.get("title")):
            continue
        steps = [{"id": s["id"], "text": s["text"], "detail": s.get("detail", ""), "command": s.get("command", "")}
                 for s in item.get("steps", []) if s.get("id") and s.get("text")]
        answered = [{"q": a["q"], "a": a["a"]} for a in item.get("answered", []) if a.get("q") and a.get("a")]
        items.append({"id": item["id"], "title": item["title"], "context": item.get("context", ""),
                      "steps": steps, "answered": answered, "ask": item.get("ask", ""), "link": item.get("link"),
                      "createdAt": item.get("createdAt")})
    return items


def list_all(db, agent_path=None):
    db.row_factory = sqlite3.Row
    ticks = {(r["actionId"], r["stepId"]): r["doneAt"] for r in db.execute("SELECT * FROM action_steps")}
    state = {r["id"]: dict(r) for r in db.execute("SELECT * FROM action_state")}
    items = agent_items(agent_path)
    for item in items:
        s = state.get(item["id"], {})
        item["input"], item["inputAt"], item["doneAt"] = s.get("input", ""), s.get("inputAt"), s.get("doneAt")
        for step in item["steps"]:
            step["doneAt"] = ticks.get((item["id"], step["id"]))
    return items


def _item(identifier, agent_path=None):
    for item in agent_items(agent_path):
        if item["id"] == identifier:
            return item
    raise KeyError("Unknown item")


def set_step(db, identifier, step, done, agent_path=None):
    if step not in {s["id"] for s in _item(identifier, agent_path)["steps"]}:
        raise KeyError("Unknown step")
    db.execute("INSERT INTO action_steps VALUES (?,?,?) ON CONFLICT(actionId, stepId) DO UPDATE SET doneAt=excluded.doneAt",
               (identifier, step, now() if done else None))


def set_input(db, identifier, text, agent_path=None):
    _item(identifier, agent_path)
    if not isinstance(text, str) or len(text) > 20000:
        raise ValueError("Input must be text of at most 20,000 characters")
    db.execute("INSERT INTO action_state (id, input, inputAt) VALUES (?,?,?) "
               "ON CONFLICT(id) DO UPDATE SET input=excluded.input, inputAt=excluded.inputAt", (identifier, text, now()))


def set_done(db, identifier, done, agent_path=None):
    _item(identifier, agent_path)
    db.execute("INSERT INTO action_state (id, doneAt) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET doneAt=excluded.doneAt",
               (identifier, now() if done else None))


def as_text(item, prompt=False):
    """One item as plain text; with prompt=True framed as the start of a new agent session."""
    lines = []
    if prompt:
        lines += [f"I am working on this item from Atlas › Notes › For you (id `{item['id']}`). "
                  "Read the context, what is done and my input, then help me with the next open step.", ""]
    done = sum(1 for s in item["steps"] if s.get("doneAt"))
    lines += [f"# {item['title']}" + (" (done)" if item.get("doneAt") else f" ({done}/{len(item['steps'])} steps done)")]
    if item.get("context"):
        lines += ["", item["context"]]
    if item["steps"]:
        lines += ["", "## Checklist"]
        for s in item["steps"]:
            lines.append(f"- [{'x' if s.get('doneAt') else ' '}] {s['text']}")
            if s.get("detail"):
                lines += [f"      {l}" for l in s["detail"].splitlines()]
            if s.get("command"):
                lines.append(f"      $ {s['command']}")
    if item["answered"]:
        lines += ["", "## Answered questions"]
        for a in item["answered"]:
            lines += [f"Q: {a['q']}", f"A: {a['a']}", ""]
    if item.get("ask"):
        lines += ["", f"## Asked of the owner\n{item['ask']}"]
    lines += ["", "## Owner input", item.get("input") or "(none yet)"]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m services.atlas.actions", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DB_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("--all", action="store_true", help="include items marked done")
    for name in ("show", "prompt"):
        sub.add_parser(name).add_argument("id")
    args = parser.parse_args(argv)
    if Path(args.db).exists():
        with closing(sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)) as db:
            try:
                items = list_all(db)
            except sqlite3.OperationalError:  # the app has not created the tables yet
                items = [{**i, "input": "", "doneAt": None} for i in agent_items()]
    else:
        items = [{**i, "input": "", "doneAt": None} for i in agent_items()]
    if args.cmd == "list":
        shown = [i for i in items if args.all or not i.get("doneAt")]
        for i in shown:
            done = sum(1 for s in i["steps"] if s.get("doneAt"))
            print(f"{i['id']:<32} {done}/{len(i['steps'])}  {i['title']}{'  [done]' if i.get('doneAt') else ''}"
                  f"{'  [has input]' if i.get('input') else ''}")
        if not shown:
            print("Nothing waiting for the owner.")
        return 0
    match = [i for i in items if i["id"] == args.id]
    if not match:
        print(f"Unknown item: {args.id}", file=sys.stderr)
        return 1
    print(as_text(match[0], prompt=args.cmd == "prompt"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
