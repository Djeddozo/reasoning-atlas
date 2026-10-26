"""Prerequisites: before something is built or adopted, what has to be there first? (informs, never blocks)

For a task, a research finding or an idea, Atlas looks for evidence in what it already knows, without model calls:
  - the skill tree: the task's own node and what that node requires; for loose text, the nodes it is about
    (word overlap). A node that is not built yet is a prerequisite; a built one is something he already has.
  - the plan: tasks it depends on that are still open.
  - outside code (a GitHub link, "install", a library or framework): runs only in the sandbox and needs his
    approval (constitution rule 2), so the sandbox node is checked too.

The result is shown on task details and tool findings, and handed to the dev loop as context ("build this
first or work around it"). It never stops a build: prerequisites inform the plan, they do not forbid
experiments.
"""
import re

from . import overlap, skilltree

ABOUT_AT = 0.25    # word-overlap score from which a node counts as "this is about that node"
OUTSIDE = re.compile(r"github\.com/|\bpip install\b|\bnpm i(?:nstall)?\b|\b(?:library|framework|sdk|package|open[- ]source)\b", re.I)


def _node_line(n: dict, caps: dict) -> dict:
    missing = [caps[r]["title"] for r in n["requires"] if r in caps and caps[r]["state"] != "unlocked"]
    return {"node_id": n["id"], "title": n["title"], "state": n["state"], "missing": missing}


def check(con, text: str, capability_id: int | None = None, depends_on: str = "", caps: dict | None = None) -> dict:
    """What this needs first ('needs'), what it builds on that is already there ('have'), and notes."""
    caps = caps if caps is not None else skilltree.nodes(con)
    needs, have, notes, seen = [], [], [], set()
    if capability_id in caps:
        node = caps[capability_id]
        for r in node["requires"]:
            if r in caps and r not in seen:
                seen.add(r)
                (have if caps[r]["state"] == "unlocked" else needs).append(_node_line(caps[r], caps) | {"why": f"{node['title']} requires it"})
        seen.add(capability_id)
    words = overlap._words(text)
    about = sorted(((overlap._score(words, overlap._words(f"{n['title']} {n.get('description') or ''}")), n)
                    for cid, n in caps.items() if cid not in seen), key=lambda x: -x[0])
    for sc, n in about[:3]:
        if sc < ABOUT_AT:
            break
        seen.add(n["id"])
        (have if n["state"] == "unlocked" else needs).append(_node_line(n, caps) | {"why": "about the same capability"})
    for d in skilltree.ids(depends_on):
        t = con.execute("SELECT id, title, status FROM tasks WHERE id = ?", (d,)).fetchone()
        if t and t["status"] not in ("done", "dropped"):
            needs.append({"task_id": t["id"], "title": t["title"], "state": t["status"], "missing": [], "why": "it depends on this task"})
    if OUTSIDE.search(text or ""):
        sandbox = next((n for n in caps.values() if "sandbox" in n["title"].lower()), None)
        notes.append("Outside code: it runs only in the sandbox, with no credentials, after your approval."
                     + (f" Sandbox node: {sandbox['state']}." if sandbox else " There is no sandbox node in the skill tree yet."))
        if sandbox and sandbox["state"] != "unlocked" and sandbox["id"] not in seen:
            needs.append(_node_line(sandbox, caps) | {"why": "outside code is tested in the sandbox first"})
    return {"needs": needs, "have": have, "notes": notes, "ready": not needs}


def for_task(con, task_id: int, caps: dict | None = None) -> dict:
    t = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not t:
        raise ValueError("Task not found.")
    return check(con, f"{t['title']} {t['description'] or ''}", t["capability_id"], t["depends_on"] or "", caps)


def prompt_block(con, task_id: int) -> str:
    """For the dev loop prompt: what is not there yet, so the agent builds on it or works around it."""
    try:
        p = for_task(con, task_id)
    except ValueError:
        return ""
    if not p["needs"] and not p["notes"]:
        return ""
    lines = [f"- {n['title']} ({n['state']}; {n['why']})" for n in p["needs"]] + [f"- {x}" for x in p["notes"]]
    return ("\nPrerequisites Atlas found (information, not a stop): these are not built yet. Use what exists, keep "
            "the change small, and say in your summary if one of them should be built first.\n" + "\n".join(lines) + "\n")


def line(p: dict) -> str:
    if p["ready"] and not p["notes"]:
        return "Nothing missing" + (f"; builds on {', '.join(h['title'] for h in p['have'])}" if p["have"] else "") + "."
    parts = [f"needs first: {', '.join(n['title'] for n in p['needs'])}"] if p["needs"] else []
    return "; ".join(parts + p["notes"])
