"""Coding agents as switchable resources (RA-079, owner 2026-10-25).

The owner wants to switch between capable coding agents whenever one runs out (a usage limit, an outage), and Atlas
should know them as resources. This module keeps the catalogue, finds which are installed on this machine, keeps
his fallback order, records them as resources (kind 'agent', read-only, like detected hardware), and writes a
hand-over prompt so any agent can continue a task from the repository alone.

What each agent can do differs. Only Claude Code is driven by the dev loop today; the others take over through the
hand-over prompt (the repository's AGENTS.md, HANDOFF.md and checkpoints carry the context). Automatic switching in
the dev loop is the next step (see `builder` per agent).
"""
import json
import shutil

from . import db

CATALOG = [
    {"id": "claude", "name": "Claude Code", "maker": "Anthropic", "command": "claude",
     "headless": "claude -p \"…\" --output-format stream-json", "resume": "claude --resume <session>",
     "limits": "Atlas reads its usage-limit messages and reset time", "builder": "drives the dev loop now",
     "handover": "native: it reads CLAUDE.md, AGENTS.md and HANDOFF.md"},
    {"id": "codex", "name": "Codex CLI", "maker": "OpenAI", "command": "codex",
     "headless": "codex exec \"…\"", "resume": "codex resume",
     "limits": "its stopped sessions are found by Atlas's usage-limit recovery", "builder": "planned: next fallback for the dev loop",
     "handover": "reads AGENTS.md; give it the hand-over prompt"},
    {"id": "gemini", "name": "Gemini CLI", "maker": "Google", "command": "gemini",
     "headless": "gemini -p \"…\"", "resume": "", "limits": "not read yet", "builder": "not yet",
     "handover": "give it the hand-over prompt"},
    {"id": "aider", "name": "Aider", "maker": "open source (any model)", "command": "aider",
     "headless": "aider --message \"…\"", "resume": "", "limits": "depends on the model behind it", "builder": "not yet",
     "handover": "give it the hand-over prompt"},
    {"id": "opencode", "name": "OpenCode", "maker": "open source (any model)", "command": "opencode",
     "headless": "opencode run \"…\"", "resume": "", "limits": "depends on the model behind it", "builder": "not yet",
     "handover": "give it the hand-over prompt"},
]
IDS = [a["id"] for a in CATALOG]
DEFAULT_ORDER = ["claude", "codex"]


def order(con) -> list[str]:
    try:
        saved = [x for x in json.loads(db.get_state(con, "agent_order", "") or "[]") if x in IDS]
    except ValueError:
        saved = []
    return saved or DEFAULT_ORDER


def set_order(con, ids: list) -> str:
    """His fallback order: the first available agent is used; when it runs out, the next one."""
    clean = []
    for x in ids if isinstance(ids, list) else []:
        if str(x) in IDS and str(x) not in clean:
            clean.append(str(x))
    if not clean:
        raise ValueError("Name at least one agent: " + ", ".join(IDS))
    db.set_state(con, "agent_order", json.dumps(clean))
    db.log(con, "me", "agent_order", " > ".join(clean))
    con.commit()
    return "Agent order: " + " → ".join(next(a["name"] for a in CATALOG if a["id"] == x) for x in clean) + "."


def _waiting(con, aid: str) -> str:
    if aid == "claude":
        from . import atlasplan
        try:
            r = atlasplan.reset_status(con)
        except Exception:  # noqa: BLE001 - status must never break the overview
            r = None
        if isinstance(r, dict) and r.get("waiting"):
            return "usage limit reached; retry after " + str(r.get("retry_at") or "the reset")
    return ""


def status(con) -> dict:
    """Every known agent: installed here or not, whether it is out (usage limit), and its place in the order."""
    rank = order(con)
    items = []
    for a in CATALOG:
        path = shutil.which(a["command"]) or ""
        out = _waiting(con, a["id"])
        items.append({**a, "installed": bool(path), "path": path, "waiting": out,
                      "position": rank.index(a["id"]) + 1 if a["id"] in rank else None,
                      "state": "not installed" if not path else "out: " + out if out else "ready"})
    usable = [i for i in sorted(items, key=lambda i: i["position"] or 99) if i["position"] and i["state"] == "ready"]
    return {"order": rank, "agents": items, "next": usable[0]["id"] if usable else ""}


def sync_resources(con) -> int:
    """Installed agents appear among the resources (kind 'agent', read-only like detected machines)."""
    n = 0
    for a in status(con)["agents"]:
        key = "detected:agent-" + a["id"]
        row = con.execute("SELECT id FROM resources WHERE source = ?", (key,)).fetchone()
        details = f"{a['maker']}; {a['state']}; builder: {a['builder']}"
        if a["installed"]:
            if row:
                db.update(con, "resources", row[0], details=details, status="active", detected=json.dumps({"path": a["path"]}))
            else:
                db.insert(con, "resources", kind="agent", name=a["name"], details=details, source=key, status="active",
                          detected=json.dumps({"path": a["path"]}))
                db.log(con, "hq", "resource_detected", f"agent {a['name']}")
            n += 1
        elif row:
            db.update(con, "resources", row[0], details=details, status="retired")
    con.commit()
    return n


def handover(con, task_id: int | None = None) -> str:
    """A prompt any coding agent can start from: the repository rules, the checkpoint, and the task."""
    lines = ["You continue work in the Reasoning Atlas repository (Djeddi's personal management system).",
             "First read AGENTS.md, then STATE/CURRENT_PROGRESS.md and HANDOFF.md. Follow the rules there: English, small "
             "verified steps, tests (python3 -m unittest discover tests), a checkpoint and evidence file, commit and push.",
             "Never edit config/constitution.toml, never handle secrets, never delete data."]
    if task_id:
        t = con.execute("SELECT id, title, description FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
        if t is None:
            raise ValueError(f"task #{task_id} does not exist")
        lines += [f"Task #{t['id']}: {t['title']}", (t["description"] or "").strip()[:3000],
                  f"When done, say \"closes task #{t['id']}\" in the commit or pull request."]
    else:
        lines.append("Pick the top task from STATE/CURRENT_PROGRESS.md or ./hq.sh next --owner ai.")
    return "\n\n".join(x for x in lines if x)
