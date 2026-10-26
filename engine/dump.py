"""Brain dump: free-form thoughts (typed or spoken) → structured additions to the plan.

The raw text is always saved to memory first, so nothing is lost even if no model is reachable.
With a model available, Atlas extracts tasks, ideas, goals, decisions and updates to existing tasks,
links new tasks to existing ones, and points out reusable code from the arsenal.
Task vs idea follows one rule (hq.flow.RULE): decided → task in the plan, maybe → idea; unsure → idea.
The reply lists every item made and where it went (hq.flow.report).

Every dump is also a thought in the brain (hq.brain): what Claude creates from it is logged there with
the reason and can be undone. A thought that could not be sorted is kept 'unsorted' and sorted later.
"""
import json
import re

from . import arsenal, brain, db, flow, models, priority

SYSTEM = """You turn a person's spoken or typed brain dump into structured plan updates for their
personal management system (Atlas). Use their own intent; do not invent work they did not express.
Link to existing tasks and goals by id where it fits. If reusable code from their arsenal is relevant,
mention it in the task description ("reuse: <name> from <project>").

""" + flow.RULE + """

If he says he did something that matches an open task he owns (owner "me"), list it under "done"; only
when he clearly says it is finished, never for planned or partial work.

Where things go, top-down (CONTEXT has his VISIONS with their objectives, the GOALS and the skill tree NODES):
- a new lifetime aim he clearly states → a goal with "vision": true and no "under"; never invent one
- any other aim → a goal "under" the vision or goal it serves, when one fits; "due" only when he names a date
  (a deadline is the only thing that raises its tasks' priority, so never guess one)
- tasks and ideas get "goal_id" when a goal fits, and "node" (a skill tree node id) when they build that capability
- lessons he learned, decisions, preferences and facts → "memories"

Reply with ONLY JSON:
{"tasks":[{"title":"","description":"","owner":"me|ai|both","impact":1-5,"effort_h":0,"due":"YYYY-MM-DD or null","after":[task ids],"goal_id":id or null,"node":id or null}],
 "ideas":[{"title":"","category":"system|learning|work|school|creative|money|physical|general","description":"","goal_id":id or null,"node":id or null}],
 "goals":[{"title":"","vision":false,"why":"","under":id or null,"due":"YYYY-MM-DD or null"}],
 "memories":[{"kind":"lesson|decision|fact|preference","content":""}],
 "updates":[{"task_id":id,"note":""}],
 "done":[{"task_id":id,"evidence":"his words"}],
 "summary":"one short sentence describing what you added"}"""


def _context(con, text: str) -> str:
    def rows(q):
        return [dict(r) for r in con.execute(q).fetchall()]
    from . import compass
    d = compass.directions(con)
    return json.dumps({
        "visions": [{"id": v["id"], "title": v["title"], "objectives": [{"id": o["id"], "title": o["title"]} for o in v["objectives"]]}
                    for v in d["visions"]],
        "goals": rows("SELECT id,title,horizon,parent_id FROM goals WHERE status='active'"),
        "nodes": rows("SELECT id,title FROM capabilities WHERE status != 'dropped'"),
        "open_tasks": rows("SELECT id,title,status,due FROM tasks WHERE status NOT IN ('done','dropped') ORDER BY score DESC LIMIT 40"),
        "arsenal_matches": [dict(project=r["project"], name=r["name"], kind=r["kind"], doc=r["doc"][:120])
                            for r in arsenal.find(con, text, 8)],
    }, default=str)


PREFIXES = ("todo", "task", "idea")


def free_text(text: str) -> str:
    """The parts of a thought without a todo:/task:/idea: prefix (those are added directly by _fallback)."""
    keep = []
    for line in re.split(r"[\n;]+", text):
        p, _, rest = line.partition(":")
        if not (rest.strip() and p.strip().lower() in PREFIXES) and line.strip():
            keep.append(line.strip())
    return "\n".join(keep)


def _fallback(con, text: str, thought_id: int | None = None) -> tuple[str, int]:
    """No model: route lines with explicit prefixes, keep the rest as notes. Returns (message, items added)."""
    added = flow.route_prefixed(con, text, thought_id)
    return (f"Saved as a note; {len(added['task'])} tasks and {len(added['idea'])} ideas from prefixes (no model reachable).",
            len(added["task"]) + len(added["idea"]))


def process(con, text: str, source: str = "me") -> str:
    text = text.strip()
    if not text:
        return "Nothing to add."
    db.insert(con, "memories", kind="info", content=text, tags="dump", source="me")
    tid = brain.add_thought(con, text, source)
    con.commit()  # keep the raw text even if the model call fails, and release the write lock while it runs
    return sort_thought(con, tid, text)


def sort_thought(con, thought_id: int, text: str, retry: bool = False) -> str:
    """Let Claude turn one thought into plan updates (logged per item in the brain, undoable), then re-rank."""
    msg = _sort(con, thought_id, text, retry)
    priority.rerank(con, "thought", f"#{thought_id}")
    return msg


def _sort(con, thought_id: int, text: str, retry: bool = False) -> str:
    try:
        out = models.parse_json(models.complete("dump", SYSTEM, f"CONTEXT:\n{_context(con, text)}\n\nBRAIN DUMP:\n{text}"))
    except (models.ModelUnavailable, ValueError) as e:
        if retry:  # still no model: leave it for the next try, without adding prefix items twice
            con.execute("UPDATE thoughts SET status = 'unsorted' WHERE id = ?", (thought_id,))
            con.commit()
            return "Still unsorted (no model reachable)."
        msg, n = _fallback(con, text, thought_id)
        # sorted only when every line was a todo:/idea: line; free text waits for a model (hq brain sort)
        brain.finish_thought(con, thought_id, "unsorted" if free_text(text) else "sorted", msg)
        db.log(con, "hq", "dump_fallback", str(e)[:200])
        con.commit()
        return flow.report(con, thought_id, msg) if n else msg
    why = f"from your thought #{thought_id}"

    valid = {r[0] for r in con.execute("SELECT id FROM tasks")}
    goals = {r[0] for r in con.execute("SELECT id FROM goals WHERE status = 'active'")}
    nodes = {r[0] for r in con.execute("SELECT id FROM capabilities")}
    node = lambda x: x.get("node") if x.get("node") in nodes else None
    for g in out.get("goals", []):
        under = g.get("under") if g.get("under") in goals else None
        vision = bool(g.get("vision") or g.get("horizon") == "life") and not under  # a lifetime aim under another aim is a goal
        due = g.get("due") if isinstance(g.get("due"), str) and len(g["due"]) == 10 and g["due"][4] == "-" else None
        rid = db.insert(con, "goals", title=g["title"], horizon="life" if vision else "goal", why=g.get("why", ""),
                        parent_id=under, due=due)
        brain.record(con, "create", "goals", rid, why, thought_id=thought_id)
        goals.add(rid)
    for t in out.get("tasks", []):
        after = ",".join(str(i) for i in t.get("after", []) if i in valid)
        rid = db.insert(con, "tasks", title=t["title"], description=t.get("description", ""),
                        owner=t.get("owner", "me") if t.get("owner") in brain.OWNERS else "me",
                        impact=int(t.get("impact", 3)), effort_h=float(t.get("effort_h", 1)), due=t.get("due") or None,
                        depends_on=after, goal_id=t.get("goal_id") if t.get("goal_id") in goals else None, capability_id=node(t))
        brain.record(con, "create", "tasks", rid, why, thought_id=thought_id)
    for i in out.get("ideas", []):
        rid = db.insert(con, "ideas", title=i["title"], category=i.get("category", "general"), description=i.get("description", ""),
                        capability_id=node(i))
        brain.record(con, "create", "ideas", rid, why, thought_id=thought_id)
    for m in out.get("memories", []):
        kind = m.get("kind") if m.get("kind") in ("lesson", "decision", "fact", "preference") else "decision"
        rid = db.insert(con, "memories", kind=kind, content=m["content"], tags="dump", source="me")
        brain.record(con, "create", "memories", rid, why, thought_id=thought_id)
    for u in out.get("updates", []):
        if u.get("task_id") in valid:
            old = con.execute("SELECT description FROM tasks WHERE id = ?", (u["task_id"],)).fetchone()[0]
            new = ((old or "") + "\n" + u["note"]).strip()
            db.update(con, "tasks", u["task_id"], description=new)
            brain.record(con, "update", "tasks", u["task_id"], why, "description", old, new, thought_id=thought_id)
    for d in out.get("done", []):
        try:
            flow.confirm_done(con, int(d.get("task_id")), str(d.get("evidence") or ""), thought_id)
        except (ValueError, TypeError, PermissionError):
            pass  # unknown, already closed or not his task: ignored, the thought stays as a note
    summary = out.get("summary") or "Added to Atlas."
    brain.finish_thought(con, thought_id, "sorted", summary)
    db.log(con, "hq", "dump", summary)
    con.commit()
    return flow.report(con, thought_id, summary)
