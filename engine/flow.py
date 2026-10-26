"""Where things go: the one rule for task vs idea, the map from a thought to the plan, and a report per thought.

The rule (also given to Claude when it sorts a brain dump):
  task  – something Djeddi has decided to do: a concrete action ("I need to", "todo", a deadline). Goes into
          the plan and is ranked.
  idea  – something he might do ("maybe", "what if", "would be nice"). Waits in the idea list, unranked
          against the plan, until it is promoted.
  When unsure: idea. Promoting is one command; an unwanted task clutters the plan.

An idea has three states (ideas.gate): idea (open) → promoted (became ideas.task_id) or dropped.
Undoing the task a promotion created reopens the idea.

A task has two actions for Djeddi: Run (hand it to the agent, front of its queue) or Delete (set it aside as an
unwanted suggestion; undoable). Done for agent-built work is agent-only, set when the agent has implemented it
(the dev loop after a merge, or Claude Code running with HQ_ACTOR=agent). Tasks owned by Djeddi (physical or
offline, owner "me") cannot be built by the agent, so he confirms them himself: the "I did it" button,
`hq done <id>`, or a dump/voice note saying so (confirm_done, undoable in the brain).
"""
import re

import os

from . import db

AGENT_ACTORS = {"agent", "hq"}  # who may mark a task done


def actor() -> str:
    """'agent' inside the dev loop (HQ_ACTOR=agent), otherwise 'me'."""
    return "agent" if os.environ.get("HQ_ACTOR") == "agent" else "me"


def _open_task(con, task_id: int):
    r = con.execute("SELECT id, title, status, owner, depends_on FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not r:
        raise ValueError(f"task #{task_id} does not exist")
    if r["status"] in ("done", "dropped"):
        raise ValueError(f"task #{task_id} is already {r['status']}")
    return r


def complete(con, task_id: int, who: str) -> str:
    """Mark a task done: only the agent (or Atlas itself) may, after implementing it."""
    r = _open_task(con, task_id)
    if who not in AGENT_ACTORS and r["owner"] != "me":
        raise PermissionError("Tasks are marked done by the agent once it has implemented them. "
                              f"Use Run to hand task #{task_id} to the agent, or Delete to discard it.")
    db.update(con, "tasks", task_id, status="done")
    db.log(con, who, "tasks_done", str(task_id))
    con.commit()
    from . import priority
    priority.rerank(con, "task done", f"#{task_id}")
    return f"Done: #{task_id} {r['title']}"


def confirm_done(con, task_id: int, evidence: str = "", thought_id: int | None = None) -> str:
    """Djeddi did a task of his own (owner 'me'): close it, logged in the brain with his words, undoable."""
    from . import brain, priority
    r = _open_task(con, task_id)
    if r["owner"] != "me":
        raise PermissionError(f"Task #{task_id} is the agent's to build; only your own tasks can be confirmed.")
    db.update(con, "tasks", task_id, status="done")
    brain.record(con, "update", "tasks", task_id, ("you confirmed: " + evidence)[:300] if evidence else "you confirmed it is done",
                 "status", r["status"], "done", thought_id=thought_id)
    db.log(con, "me", "tasks_done", f"{task_id} (confirmed)")
    con.commit()
    priority.rerank(con, "task done", f"#{task_id}")
    return f"Done: #{task_id} {r['title']}"


def run(con, task_id: int) -> str:
    """Run: hand the task to the agent, at the front of Djeddi's queue (built first, no approval needed)."""
    from . import brain, skilltree
    r = _open_task(con, task_id)
    if r["owner"] not in ("ai", "both"):
        brain.set_field(con, "tasks", task_id, "owner", "ai", "handed to the agent by Djeddi (Run)")
    if r["status"] in ("proposed", "blocked"):
        brain.set_field(con, "tasks", task_id, "status", "todo", "handed to the agent by Djeddi (Run)")
    skilltree.queue(con, task_id, mine=True, top=True)
    db.log(con, "me", "tasks_run", str(task_id))
    con.commit()
    return f"Task #{task_id} handed to the agent (first in its queue)."


def queue_for_build(con, task_id: int, position: int = 5) -> str:
    """Build (Forge › Plan): hand the task to the builder at the bottom of the five shown in Up next; what stood
    fifth moves to sixth. Refused, with the reason, while it waits for another task."""
    from . import brain, skilltree
    r = _open_task(con, task_id)
    why = skilltree.blocked(con, r)
    if why:
        raise ValueError(f"Task #{task_id} cannot be built yet: {why}.")
    if r["owner"] not in ("ai", "both"):
        brain.set_field(con, "tasks", task_id, "owner", "ai", "handed to the builder by Djeddi (Build)")
    if r["status"] in ("proposed", "blocked"):
        brain.set_field(con, "tasks", task_id, "status", "todo", "handed to the builder by Djeddi (Build)")
    at = skilltree.place(con, task_id, position)
    db.log(con, "me", "tasks_queue", f"{task_id} at {at}")
    con.commit()
    return f"Task #{task_id} is number {at} in the builder's queue."


def discard(con, task_id: int) -> str:
    """Delete: discard the task as a suggestion. It is set to dropped, not erased, and can be undone."""
    from . import brain, skilltree
    _open_task(con, task_id)
    brain.set_field(con, "tasks", task_id, "status", "dropped", "deleted by Djeddi")
    skilltree.queue(con, task_id, mine=False)
    db.log(con, "me", "tasks_dropped", str(task_id))
    con.commit()
    return f"Task #{task_id} deleted (undo under Memory › What changed)."

RULE = """A TASK is something he has decided to do: a concrete action, a "need to", "todo", "must" or a deadline.
An IDEA is something he might do: "maybe", "what if", "would be nice", a possibility without commitment.
When unsure, make it an idea (promoting later is one step; a wrong task clutters his plan)."""

MAP = """Where things go in Atlas

  You say something (hq dump, Telegram text or voice, dashboard brain dump)
    → saved first as a thought (nothing is lost; hq brain thoughts)
    → Claude sorts it with one rule:
        decided to do it ("need to", "todo", a deadline)  → TASK, in your plan and ranked (hq next, hq list)
        might do it ("maybe", "what if")                  → IDEA, in the idea list (hq ideas)
        unsure                                            → IDEA
      plus goals, notes (memory) and notes on existing tasks.
    → the reply lists every item it made, with its number.
    No model reachable: lines starting 'todo:' become tasks, 'idea:' ideas; the rest waits (hq brain sort).

  An idea becomes a task (promoted) when
    you promote it:  hq promote <id> [--ai]   · Telegram /promote <id> [ai]   · dashboard → My task / → AI queue
    or Claude's re-evaluation sees you clearly want it (hq brain think; logged, undoable).
  The idea then shows '→ task #N' and leaves the open idea list. Undoing that task reopens the idea.

  Direct, without sorting:  hq add task|idea "..."  ·  hq inbox "todo: ..." / "idea: ..."
  Undo anything Claude did: hq brain log, then hq brain undo <n> (Telegram /undo <n>)."""


def placed(con, thought_id: int) -> list[dict]:
    """What a thought became: every task, idea and goal created from it (not undone), with where it now is."""
    out = []
    for c in con.execute("""SELECT table_name, row_id FROM brain_changes WHERE thought_id = ? AND action = 'create'
                            AND undone = 0 ORDER BY id""", (thought_id,)):
        out.append(where(con, c["table_name"], c["row_id"]))
    return [x for x in out if x]


def where(con, table: str, row_id: int) -> dict | None:
    """One item, with a plain sentence saying where it is in Atlas."""
    if table == "tasks":
        r = con.execute("""SELECT t.title, t.status, t.owner, g.title AS goal FROM tasks t LEFT JOIN goals g ON g.id = t.goal_id
                           WHERE t.id = ?""", (row_id,)).fetchone()
        if not r:
            return None
        place = {"done": "done", "dropped": "dropped"}.get(r["status"]) or (
            "in Forge › Plan" + (f" under “{r['goal']}”" if r["goal"] else "") + (", for the builder" if r["owner"] == "ai" else ""))
        return {"kind": "task", "id": row_id, "title": r["title"], "place": place}
    if table == "ideas":
        r = con.execute("""SELECT i.title, i.gate, i.task_id, c.title AS node FROM ideas i
                           LEFT JOIN capabilities c ON c.id = i.capability_id WHERE i.id = ?""", (row_id,)).fetchone()
        if not r:
            return None
        place = {"promoted": f"promoted → task #{r['task_id']}", "dropped": "dropped"}.get(
            r["gate"], "in Forge › Ideas" + (f", for the skill “{r['node']}”" if r["node"] else ""))
        return {"kind": "idea", "id": row_id, "title": r["title"], "place": place}
    if table == "goals":
        r = con.execute("""SELECT g.title, g.horizon, g.parent_id, p.title AS parent, p.horizon AS parent_horizon, p.parent_id AS grand
                           FROM goals g LEFT JOIN goals p ON p.id = g.parent_id WHERE g.id = ?""", (row_id,)).fetchone()
        if not r:
            return None
        if r["parent_id"] is None:
            place = "a new vision in Focus" if r["horizon"] == "life" else "in Forge › Plan, not yet under a vision"
            kind = "vision" if r["horizon"] == "life" else "goal"
        elif r["parent_horizon"] == "life" and r["grand"] is None:
            place, kind = f"a goal under “{r['parent']}” in Focus", "goal"
        else:
            place, kind = f"in Forge › Plan under “{r['parent']}”", "goal"
        return {"kind": kind, "id": row_id, "title": r["title"], "place": place}
    if table == "memories":
        r = con.execute("SELECT kind, content FROM memories WHERE id = ?", (row_id,)).fetchone()
        return {"kind": r["kind"], "id": row_id, "title": r["content"][:80], "place": "in Memory"} if r and r["kind"] != "forgotten" else None
    return None


def lines(items: list[dict]) -> str:
    return "\n".join(f"• {i['kind']} #{i['id']} {i['title']} – {i['place']}" for i in items)


def report(con, thought_id: int, summary: str) -> str:
    """The reply to a brain dump: Claude's summary, then every item and where it went."""
    items = placed(con, thought_id)
    return summary + ("\n" + lines(items) if items else "\nNothing new in the plan (kept as a note).")


def route_prefixed(con, text: str, thought_id: int | None = None) -> dict:
    """Lines starting 'todo:'/'task:' become tasks, 'idea:' ideas (logged in the brain, undoable).

    Used by the brain dump when no model is reachable."""
    from . import brain
    added = {"task": [], "idea": []}
    for line in re.split(r"[\n;]+", text):
        p, _, rest = line.partition(":")
        p, rest = p.strip().lower(), rest.strip()
        if rest and p in ("todo", "task"):
            rid = db.insert(con, "tasks", title=rest)
            brain.record(con, "create", "tasks", rid, f"from a '{p}:' line", thought_id=thought_id)
            added["task"].append(rid)
        elif rest and p == "idea":
            rid = db.insert(con, "ideas", title=rest)
            brain.record(con, "create", "ideas", rid, "from an 'idea:' line", thought_id=thought_id)
            added["idea"].append(rid)
    return added
