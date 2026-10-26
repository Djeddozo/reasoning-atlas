"""Hand work from Atlas to another app without losing context (RA-025).

Atlas cannot run a builder itself yet (VQ-009). When the owner wants to
continue building, Atlas writes a handoff packet for the app the registry
names for the work (for example the Claude app or Codex), and logs the switch
so the most frequent ones show what Atlas should learn to do itself.
"""
import json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path

from packages.contracts import modules
from services.atlas import continuity, progress

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "https://github.com/Djeddozo/Unconventional-Reasoning-Atlas"
SCHEMA = "CREATE TABLE IF NOT EXISTS handoffs (id TEXT PRIMARY KEY, taskId TEXT NOT NULL, tool TEXT NOT NULL, intent TEXT NOT NULL, createdAt TEXT NOT NULL)"
HOW_TO = {
    "claude-desktop": "Open the Claude app, start a task with the folder {folder} connected, and paste this text.",
    "codex": "Open the ChatGPT app, go to Codex and the Reasoning Atlas project (folder {folder}), and paste this text.",
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def migrate(db):
    db.execute(SCHEMA)
    if db.execute("PRAGMA user_version").fetchone()[0] < 3:
        db.execute("PRAGMA user_version=3")


def git_head(root=ROOT):
    """Return (branch, short commit) by reading .git directly; no git process needed."""
    git = Path(root) / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if not head.startswith("ref: "):
        return None, head[:7]
    ref = head[5:]
    branch = ref.removeprefix("refs/heads/")
    try:
        return branch, (git / ref).read_text(encoding="utf-8").strip()[:7]
    except OSError:
        pass
    try:
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return branch, line[:7]
    except OSError:
        pass
    return branch, None


def candidates(root=ROOT):
    """Tasks worth continuing: the active or paused one first, then in progress, review and ready."""
    tasks = json.loads((Path(root) / "STATE/tasks.json").read_text(encoding="utf-8"))["tasks"]
    status = progress.read()
    rank = {"in_progress": 1, "review": 2, "ready": 3}
    chosen = [t for t in tasks if t["status"] in rank]
    chosen.sort(key=lambda t: (rank[t["status"]], t["id"]))
    active = status["taskId"] if status["state"] in ("building", "stalled", "failed") else None
    if active:
        chosen = [t for t in tasks if t["id"] == active] + [t for t in chosen if t["id"] != active]
    names = {p["id"]: p["name"] for p in json.loads((Path(root) / "STATE/tasks.json").read_text(encoding="utf-8")).get("paths", [])}
    return [{"id": t["id"], "title": t["title"], "status": t["status"], "active": t["id"] == active,
             "program": t.get("program", "atlas"), "path": t.get("path"), "pathName": names.get(t.get("path"))} for t in chosen]


def packet(task_id, tool, root=ROOT):
    root = Path(root)
    registry = modules.load_registry(root / "modules", root)
    route = modules.resolve(registry, "code.edit")
    tools = {c["module"]: c for c in route["switchTo"] + route["alsoAvailableIn"]}
    if tool not in tools:
        raise ValueError(f"{tool} cannot continue building")
    tasks = {t["id"]: t for t in json.loads((root / "STATE/tasks.json").read_text(encoding="utf-8"))["tasks"]}
    if task_id not in tasks:
        raise ValueError(f"Unknown task {task_id}")
    task = tasks[task_id]
    branch, commit = git_head(root)
    status = progress.read()
    lines = [
        f"Continue building Reasoning Atlas: {task_id} — {task['title']}.",
        "",
        f"Repository: {REPOSITORY}" + (f", branch {branch}" if branch else "") + (f", last commit {commit}" if commit else "") + ".",
        f"Local folder on the owner's computer: {root}",
        "",
        f"Program and path: {task.get('program', 'atlas')} › {task.get('path', 'unassigned')} (docs/MODEL.md).",
        f"Task status: {task['status']}. Depends on: {', '.join(task['dependsOn']) or 'nothing'}.",
        f"Acceptance: {task['acceptance']}",
        f"Validation: {task['validation']}",
    ]
    if status["taskId"] == task_id and status["state"] != "idle":
        total = len(status["steps"])
        lines += ["", f"Where it stopped: {status['state']}, step {max(status['step'], 1)} of {total} "
                      f"({status['message']}), last update {status['updatedAt']}. Steps: {'; '.join(status['steps'])}."]
    state = continuity.resume(root)
    if state["taskId"] == task_id:
        lines += ["", continuity.text(state)]
    evidence = root / "STATE" / "evidence" / f"{task_id}.md"
    reads = ["AGENTS.md", "STATE/CURRENT_PROGRESS.md", "HANDOFF.md"] + ([f"STATE/evidence/{task_id}.md"] if evidence.exists() else [])
    lines += [
        "",
        f"Read first: {', '.join(reads)}. Inspect git status and preserve existing changes.",
        "",
        "Before anything else run python scripts/resume.py show: it rebuilds where work stopped from the repository and lists external effects to verify, never repeat.",
        "",
        "While you work:",
        f"- Claim the task with python scripts/resume.py claim {task_id} --owner <app> --session <id>; at each checkpoint run python scripts/resume.py checkpoint --next \"<exact next action>\" and commit.",
        "- Wrap every external effect (push, pull request, message) in python scripts/resume.py effect begin KEY … and effect finish KEY done.",
        f"- Report progress so the owner sees it live in Atlas: python scripts/progress.py start {task_id} \"{task['title']}\" \"<step 1>\" \"<step 2>\" …, then step <n>, then done.",
        "- Commit and push every checkpoint to GitHub (ADR-015). An unpushed commit is not a checkpoint.",
        "- If you need the owner's input, add it with python scripts/remind.py add so it appears under Waiting on you.",
        "- Keep everything in English. Update the state files and STATE/evidence at each checkpoint.",
        "",
        f"Handed over from Atlas on {now()} to {tools[tool].get('moduleName', tool)}.",
    ]
    return {"text": "\n".join(lines), "tool": tool, "toolName": tools[tool].get("moduleName", tool),
            "howTo": HOW_TO.get(tool, "Open {folder} in the app and paste this text.").format(folder=root), "intent": "code.edit"}


def log(db, task_id, tool, intent="code.edit"):
    identifier = str(uuid.uuid4())
    db.execute("INSERT INTO handoffs VALUES (?,?,?,?,?)", (identifier, task_id, tool, intent, now()))
    return identifier


def summary(db):
    db.row_factory = sqlite3.Row
    counts = [dict(r) for r in db.execute("SELECT intent, tool, COUNT(*) AS count FROM handoffs GROUP BY intent, tool ORDER BY count DESC")]
    recent = [dict(r) for r in db.execute("SELECT * FROM handoffs ORDER BY createdAt DESC LIMIT 10")]
    return {"counts": counts, "recent": recent}
