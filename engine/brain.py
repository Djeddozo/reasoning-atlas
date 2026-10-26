"""The brain: one place for every thought, and Claude keeping the plan in order.

Thoughts. Everything Djeddi drops (typed, spoken, Telegram, dashboard) is stored as a raw thought first,
without him choosing "task" or "idea". Claude sorts it (hq.dump does the sorting): new tasks, ideas,
goals, notes, or updates to existing ones. A thought that could not be sorted (no model reachable) stays
'unsorted' and is sorted later (`hq brain sort`, hourly).

Re-evaluation. Daily, or on request, Claude reads the goals, open tasks, ideas, recent thoughts and
progress, and re-prioritises: impact, effort, due date, goal, owner (setting owner 'ai' queues software
work for the dev loop), and promotes ideas that Djeddi clearly wants done into tasks (idea gate 'promoted',
ideas.task_id; the full map of where things go is in hq.flow). Dropping or merging
tasks is only suggested; Djeddi confirms.

Every change Claude makes is recorded in `brain_changes` with its reason and can be undone. Nothing is
deleted: undoing a created task sets it to 'dropped'. Djeddi's thoughts are his own words; text from web
pages or files never enters this path.
"""
import json
from datetime import date, datetime, timedelta

from . import db, loop, models, priority

TASK_FIELDS = {"impact", "effort_h", "due", "goal_id", "owner", "confidence", "capability_id"}
IDEA_FIELDS = {"impact", "category", "effort_h", "capability_id"}
MAX_NEW_CAPABILITIES = 3
OWNERS = {"me", "ai", "both"}
CATEGORIES = {"system", "learning", "work", "school", "creative", "money", "physical", "general"}
MAX_CHANGES = 12

REEVALUATE = """You are the planning brain of Atlas, a personal management system for one person (Djeddi).
You get his goals, open tasks, ideas, his recent thoughts (his own words, newest first) and his progress.
Re-evaluate the plan so that what matters most right now is at the top, and say what he should do next.
Treat management_focus as his explicit current direction: prefer capabilities that make the agent better
at managing builds, explaining priorities, tracking progress and reducing his coordination work.
For each recommendation explain the observed problem, why now, expected benefit, dependencies,
tradeoffs and uncertainty, and a concrete success check. Ground this in the supplied evidence;
state when evidence is missing. Do not invent user needs or claim a feature is necessary without support.

The long-term aim is a digital company serving him: Atlas gains skills and systems (the skill tree,
"capabilities": each requires others and is built by tasks) until it can turn his ideas into digital or
physical products. Keep the tree in step with his ideas.

You may:
- change a task's impact (1-5), effort_h, due (YYYY-MM-DD or null), goal_id, owner ("me", "ai" or "both";
  "ai" only for software work an AI agent can do in the Atlas code repository, which queues it for the agent),
  or capability_id (the capability the task builds)
- change an idea's impact (1-5), category, or capability_id (the capability the idea builds on)
- promote an idea to a task when his thoughts or goals show he wants it done
- add a capability (at most 3) when ideas or goals need a skill or system the tree does not have yet:
  title, branch (core, ops, mind, reach, make, product or self), requires (ids of existing capabilities),
  idea_id when it grows from one idea
- suggest (not apply) dropping or merging tasks
Rules: base every change on his thoughts, goals, deadlines or progress, with a short reason; do not invent
new work; keep changes few and meaningful (at most 12); leave things as they are when they are fine.

Reply with ONLY JSON:
{"changes":[{"table":"tasks|ideas","id":0,"field":"","value":null,"reason":""}],
 "promote":[{"idea_id":0,"owner":"me|ai","reason":""}],
 "capabilities":[{"title":"","branch":"","requires":[0],"idea_id":null,"description":"","reason":""}],
 "suggest":[{"action":"drop|merge","task_ids":[0],"reason":""}],
 "recommended":[{"task_id":0,"why":"","rationale":{"problem":"","why_now":"","benefit":"","tradeoffs":"","success_check":""}}],
 "ai_queue":[{"task_id":0,"why":"","rationale":{"problem":"","why_now":"","benefit":"","tradeoffs":"","success_check":""}}],
 "focus":"2-3 sentences: what to focus on now and why"}"""


# ---------------- thoughts ----------------

def add_thought(con, text: str, source: str = "me") -> int:
    tid = db.insert(con, "thoughts", text=text, source=source, status="new", created=db.now())
    db.log(con, "me", "thought", text[:120])
    return tid


def finish_thought(con, thought_id: int, status: str, result: str) -> None:
    con.execute("UPDATE thoughts SET status = ?, result = ?, sorted_at = ? WHERE id = ?",
                (status, result[:500], db.now(), thought_id))


def thoughts(con, n: int = 20) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM thoughts ORDER BY id DESC LIMIT ?", (n,))]


def sort_pending(con, limit: int = 10) -> int:
    """Sort thoughts that are still unsorted (e.g. no model was reachable when they came in).

    Each thought is claimed first (status 'sorting'), so two overlapping runs never sort the same one twice.
    A claim older than an hour (a run that crashed) is taken over."""
    from . import dump
    done = 0
    stale = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    rows = con.execute("""SELECT id, text FROM thoughts WHERE status = 'unsorted'
                          OR (status = 'sorting' AND sorted_at < ?) ORDER BY id LIMIT ?""", (stale, limit)).fetchall()
    for r in rows:
        claimed = con.execute("""UPDATE thoughts SET status = 'sorting', sorted_at = ? WHERE id = ?
                                 AND (status = 'unsorted' OR (status = 'sorting' AND sorted_at < ?))""",
                              (db.now(), r["id"], stale)).rowcount
        con.commit()
        if not claimed:
            continue
        # prefix lines (todo:/idea:) were already added when it came in; only the free text is left to sort
        rest = dump.free_text(r["text"])
        if not rest:
            finish_thought(con, r["id"], "sorted", "Only todo:/idea: lines; already added.")
            con.commit()
        else:
            dump.sort_thought(con, r["id"], rest, retry=True)
        done += con.execute("SELECT status FROM thoughts WHERE id = ?", (r["id"],)).fetchone()[0] == "sorted"
    return done


# ---------------- change log ----------------

def record(con, action: str, table: str, row_id: int, reason: str, field: str = "", old=None, new=None,
           thought_id: int | None = None, batch: str = "") -> int:
    return db.insert(con, "brain_changes", ts=db.now(), action=action, table_name=table, row_id=row_id, field=field,
                     old_value=json.dumps(old), new_value=json.dumps(new), reason=reason[:300],
                     thought_id=thought_id, batch=batch, undone=0)


def set_field(con, table: str, row_id: int, field: str, value, reason: str, batch: str = "") -> bool:
    row = con.execute(f"SELECT {field} FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None or row[0] == value:
        return False
    db.update(con, table, row_id, **{field: value})
    record(con, "update", table, row_id, reason, field, row[0], value, batch=batch)
    return True


def changes(con, n: int = 20) -> list[dict]:
    rows = con.execute("SELECT * FROM brain_changes ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        title = con.execute(f"SELECT title FROM {r['table_name']} WHERE id = ?", (r["row_id"],)).fetchone() \
            if r["table_name"] in ("tasks", "ideas", "goals", "capabilities") else \
            con.execute("SELECT name FROM resources WHERE id = ?", (r["row_id"],)).fetchone() if r["table_name"] == "resources" else None
        r["title"] = title[0] if title else ""
        r["old"], r["new"] = json.loads(r["old_value"] or "null"), json.loads(r["new_value"] or "null")
        out.append(r)
    return out


def describe_change(c: dict) -> str:
    what = {"tasks": "task", "ideas": "idea", "goals": "goal", "capabilities": "capability", "resources": "resource"}.get(c["table_name"], c["table_name"])
    if c["action"] == "create":
        return f"added {what} #{c['row_id']} {c['title']}"
    return f"{what} #{c['row_id']} {c['title']}: {c['field']} {c['old']} → {c['new']}"


def undo(con, change_id: int) -> str:
    c = con.execute("SELECT * FROM brain_changes WHERE id = ?", (change_id,)).fetchone()
    if c is None:
        raise ValueError(f"change #{change_id} does not exist")
    if c["undone"]:
        return "Already undone."
    table, rid = c["table_name"], c["row_id"]
    if c["action"] == "update":
        newer = con.execute("""SELECT id FROM brain_changes WHERE table_name = ? AND row_id = ? AND field = ?
                               AND action = 'update' AND undone = 0 AND id > ? ORDER BY id DESC LIMIT 1""",
                            (table, rid, c["field"], change_id)).fetchone()
        if newer:  # restoring the old value would silently erase the later change
            raise ValueError(f"change #{newer[0]} changed {c['field']} again later; undo that one first")
        db.update(con, table, rid, **{c["field"]: json.loads(c["old_value"])})
        if table == "ideas" and c["field"] == "gate" and json.loads(c["new_value"]) == "promoted":
            _unpromote(con, rid)
    elif c["action"] == "create":  # nothing is deleted: the row is set aside
        if table == "tasks":
            db.update(con, "tasks", rid, status="dropped")
            _reopen_idea(con, rid)
        elif table == "ideas":
            db.update(con, "ideas", rid, gate="dropped")
        elif table in ("goals", "capabilities"):
            db.update(con, table, rid, status="dropped")
        elif table == "resources":
            db.update(con, "resources", rid, status="retired")
        elif table == "memories":
            db.update(con, "memories", rid, kind="forgotten")
    con.execute("UPDATE brain_changes SET undone = 1 WHERE id = ?", (change_id,))
    db.log(con, "me", "brain_undo", f"change #{change_id}")
    con.commit()
    return f"Undone: {describe_change(dict(c) | {'title': '', 'old': json.loads(c['old_value']), 'new': json.loads(c['new_value'])})}"


def _reopen_idea(con, task_id: int) -> None:
    """The task a promoted idea became was undone: the idea is open again (and its gate change marked undone)."""
    for i in con.execute("SELECT id FROM ideas WHERE task_id = ? AND gate = 'promoted'", (task_id,)).fetchall():
        db.update(con, "ideas", i["id"], gate="idea", task_id=None)
        con.execute("""UPDATE brain_changes SET undone = 1 WHERE table_name = 'ideas' AND row_id = ? AND field = 'gate'
                       AND reason = ? AND undone = 0""", (i["id"], f"became task #{task_id}"))


def _unpromote(con, idea_id: int) -> None:
    """The promotion of an idea was undone: the task it became is set aside (and its create change marked undone)."""
    task_id = con.execute("SELECT task_id FROM ideas WHERE id = ?", (idea_id,)).fetchone()[0]
    db.update(con, "ideas", idea_id, task_id=None)
    if task_id and con.execute("SELECT 1 FROM tasks WHERE id = ? AND status NOT IN ('done', 'dropped')", (task_id,)).fetchone():
        db.update(con, "tasks", task_id, status="dropped")
        con.execute("""UPDATE brain_changes SET undone = 1 WHERE table_name = 'tasks' AND row_id = ?
                       AND action = 'create' AND undone = 0""", (task_id,))


# ---------------- ideas → tasks ----------------

def promote_idea(con, idea_id: int, owner: str = "me", reason: str = "queued by Djeddi", batch: str = "") -> int:
    idea = con.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if idea is None:
        raise ValueError(f"idea #{idea_id} does not exist")
    if idea["gate"] != "idea":
        raise ValueError(f"idea #{idea_id} is already {idea['gate']}" + (f" (task #{idea['task_id']})" if idea["task_id"] else "")
                         + "; only open ideas become tasks")
    if owner not in OWNERS:
        raise ValueError("owner must be me, ai or both")
    tid = db.insert(con, "tasks", title=idea["title"], description=(idea["description"] or "") + f"\n(from idea #{idea_id})",
                    owner=owner, impact=idea["impact"] or 3, effort_h=idea["effort_h"] or 2,
                    capability_id=idea["capability_id"])  # keeps its place in the skill tree (and its foundations)
    record(con, "create", "tasks", tid, reason, batch=batch)
    if not batch and owner in ("ai", "both"):  # Djeddi queued it himself (dashboard, /queue, hq queue)
        from . import skilltree
        skilltree.queue(con, tid)
    db.update(con, "ideas", idea_id, task_id=tid)
    set_field(con, "ideas", idea_id, "gate", "promoted", f"became task #{tid}", batch=batch)
    db.log(con, "me" if not batch else "agent", "idea_to_task", f"idea #{idea_id} → task #{tid} ({owner})")
    con.commit()
    return tid


# ---------------- re-evaluation ----------------

# What the whole-plan jobs (reevaluate, doover) see (#188): enough to judge the plan, within a prompt budget.
CONTEXT = {"tasks": 150, "about": 200, "memories": 25, "memory_chars": 600, "thought_days": 30, "thoughts": 40,
           "max_tokens": 60000}
# Least relevant first: (list, how many always stay). A list's tail is its least relevant part.
TRIM_ORDER = (("ideas", 10), ("relevant_memories", 8), ("recent_thoughts", 15), ("open_tasks", 60),
              ("ideas", 0), ("relevant_memories", 0), ("recent_thoughts", 0), ("open_tasks", 20))


def tokens(text: str) -> int:
    """A rough prompt size: about four characters per token."""
    return len(text) // 4


def trim(ctx: dict, max_tokens: int) -> dict:
    """Drop the least relevant items until the context fits; returns {list: dropped count}."""
    dropped = {}
    for key, keep in TRIM_ORDER:
        while tokens(json.dumps(ctx, default=str)) > max_tokens and len(ctx[key]) > keep:
            over = tokens(json.dumps(ctx, default=str)) - max_tokens
            per = max(1, tokens(json.dumps(ctx[key][-1], default=str)))
            n = max(1, min(len(ctx[key]) - keep, over // per))  # remove roughly what is needed in one go
            del ctx[key][-n:]
            dropped[key] = dropped.get(key, 0) + n
    return dropped


def _context(con) -> str:
    from . import progress, skilltree

    def rows(q, *a):
        return [dict(r) for r in con.execute(q, a).fetchall()]
    s = progress.stats(con)
    since = (date.today() - timedelta(days=CONTEXT["thought_days"])).isoformat()
    thoughts = rows("SELECT id, created, text FROM thoughts WHERE created >= ? ORDER BY id DESC LIMIT ?", since, CONTEXT["thoughts"])
    tasks_open = rows("""SELECT id, title, owner, status, impact, effort_h, due, goal_id, depends_on, score, capability_id,
                         substr(COALESCE(description, ''), 1, ?) AS about FROM tasks WHERE status NOT IN ('done', 'dropped')
                         ORDER BY score DESC LIMIT ?""", CONTEXT["about"], CONTEXT["tasks"])
    from . import compass
    d = compass.directions(con)
    ctx = {
        "atlas_now": ("The former HQ app is merged into Atlas and its name is retired. Visions (lifetime aims with year "
                      "objectives) are the reference: goals should serve a vision, tasks a goal that still matters."),
        "visions": [{"id": v["id"], "title": v["title"], "objectives": [o["title"] for o in v["objectives"]]} for v in d["visions"]],
        "vision_in_focus": d["current"],
        "management_focus": db.get_state(con, "management_focus", ""),
        "today": date.today().isoformat(),
        "goals": rows("SELECT id, title, horizon, why, parent_id FROM goals WHERE status = 'active'"),
        "open_task_count": con.execute("SELECT COUNT(*) FROM tasks WHERE status NOT IN ('done', 'dropped')").fetchone()[0],
        "open_tasks": tasks_open,
        "ideas": rows("""SELECT id, title, category, impact, gate, capability_id FROM ideas WHERE gate = 'idea'
                         ORDER BY score DESC LIMIT 30"""),
        "capabilities": [{"id": c["id"], "title": c["title"], "branch": c["branch"], "state": c["state"],
                          "requires": c["requires"]} for c in skilltree.nodes(con).values()],
        "recent_thoughts": thoughts,
        "progress": {k: s[k] for k in ("done_week", "done_last_week", "streak", "open")},
        "recently_done": [r["title"] for r in s["recent"][:8]],
        "relevant_memories": [{"kind": m["kind"], "content": m["content"][:CONTEXT["memory_chars"]]} for m in db.relevant_memories(
            con, " ".join(r["text"] for r in thoughts[:20]) + " " + " ".join(r["title"] for r in tasks_open[:30]), CONTEXT["memories"])],
    }
    dropped = trim(ctx, CONTEXT["max_tokens"])
    if dropped:  # logged, so a thin answer can be traced to a trimmed prompt
        db.log(con, "hq", "context_trimmed", ", ".join(f"{n} {k}" for k, n in dropped.items()))
        ctx["trimmed"] = dropped
    return json.dumps(ctx, default=str)


def _valid(table: str, field: str, value, con):
    """Return the cleaned value, or raise ValueError when Claude proposed something not allowed."""
    if table == "tasks" and field not in TASK_FIELDS or table == "ideas" and field not in IDEA_FIELDS:
        raise ValueError(f"{table}.{field} may not be changed")
    if field == "impact":
        return max(1, min(5, int(value)))
    if field == "effort_h":
        return max(0.0, float(value))
    if field == "confidence":
        return max(0.0, min(1.0, float(value)))
    if field == "due":
        return date.fromisoformat(str(value)).isoformat() if value else None
    if field == "goal_id":
        if value is None:
            return None
        if not con.execute("SELECT 1 FROM goals WHERE id = ? AND status = 'active'", (int(value),)).fetchone():
            raise ValueError("unknown goal")
        return int(value)
    if field == "owner":
        if value not in OWNERS:
            raise ValueError("bad owner")
        return value
    if field == "capability_id":
        if value is None:
            return None
        if not con.execute("SELECT 1 FROM capabilities WHERE id = ? AND status != 'dropped'", (int(value),)).fetchone():
            raise ValueError("unknown capability")
        return int(value)
    if field == "category":
        if value not in CATEGORIES:
            raise ValueError("bad category")
        return value
    raise ValueError("unsupported field")


def reevaluate(con) -> dict:
    """Ask Claude to re-prioritise the plan; apply allowed changes (logged, undoable); store focus."""
    from . import skilltree
    batch = "re-" + db.now()
    try:
        out = models.parse_json(models.complete("reevaluate", REEVALUATE, _context(con)))
        if not isinstance(out, dict):
            raise ValueError("not an object")
    except (models.ModelUnavailable, ValueError) as e:
        result = {"applied": 0, "focus": "", "error": models.why_failed(e)}
        _store_focus(con, {}, batch, result["error"])
        return result

    open_tasks = {r[0] for r in con.execute("SELECT id FROM tasks WHERE status NOT IN ('done', 'dropped')")}
    ideas = {r[0] for r in con.execute("SELECT id FROM ideas WHERE gate = 'idea'")}
    applied, skipped = 0, 0
    for ch in (out.get("changes") or [])[:MAX_CHANGES]:
        table, rid, field = ch.get("table"), ch.get("id"), ch.get("field")
        if (table == "tasks" and rid in open_tasks) or (table == "ideas" and rid in ideas):
            try:
                applied += set_field(con, table, rid, field, _valid(table, field, ch.get("value"), con),
                                     str(ch.get("reason") or "re-evaluation"), batch)
                continue
            except (ValueError, TypeError):
                pass
        skipped += 1
    for p in (out.get("promote") or [])[:5]:
        if p.get("idea_id") in ideas:
            try:  # an idea listed twice is promoted once
                promote_idea(con, p["idea_id"], p.get("owner") if p.get("owner") in OWNERS else "me",
                             str(p.get("reason") or "promoted by re-evaluation"), batch)
                applied += 1
            except ValueError:
                skipped += 1
    for c in (out.get("capabilities") or [])[:MAX_NEW_CAPABILITIES]:
        try:
            idea = c.get("idea_id") if c.get("idea_id") in ideas else None
            skilltree.add(con, str(c.get("title") or ""), str(c.get("branch") or ""),
                          [int(i) for i in (c.get("requires") or []) if isinstance(i, int)],
                          str(c.get("description") or ""), idea, str(c.get("reason") or "new capability")[:300],
                          batch, actor="agent")
            applied += 1
        except (ValueError, TypeError, AttributeError):
            skipped += 1
    db.log(con, "agent", "brain_reevaluate", f"applied={applied} skipped={skipped}")
    _store_focus(con, out, batch)
    con.commit()
    return {"applied": applied, "skipped": skipped, "focus": out.get("focus", ""), "batch": batch}


def _store_focus(con, out: dict, batch: str, error: str = "") -> None:
    open_tasks = {r["id"]: r["title"] for r in con.execute(
        "SELECT id, title FROM tasks WHERE status NOT IN ('done', 'dropped', 'blocked')")}
    def pick(key):
        selected = []
        for item in out.get(key) or []:
            if not isinstance(item, dict) or item.get("task_id") not in open_tasks:
                continue
            rationale = item.get("rationale") or {}
            if not isinstance(rationale, dict):
                rationale = {}
            selected.append({"task_id": item["task_id"], "title": open_tasks[item["task_id"]],
                             "why": str(item.get("why", ""))[:400],
                             "rationale": {k: str(v)[:1500] for k, v in rationale.items()
                                           if k in {"problem", "why_now", "benefit", "tradeoffs", "success_check"}}})
        return selected[:6]
    suggest = [{"action": s.get("action"), "task_ids": [i for i in s.get("task_ids", []) if i in open_tasks],
                "reason": str(s.get("reason", ""))[:200]}
               for s in (out.get("suggest") or []) if isinstance(s, dict) and s.get("action") in ("drop", "merge")][:5]
    db.set_state(con, "brain_focus", json.dumps({"at": db.now(), "batch": batch, "focus": str(out.get("focus") or "")[:800],
                                                 "recommended": pick("recommended"), "ai_queue": pick("ai_queue"),
                                                 "suggest": [s for s in suggest if s["task_ids"]], "error": error}))
    con.commit()


CHANGE_TRIGGER = 5          # new thoughts, tasks and ideas since the last re-evaluation that justify another one
MIN_HOURS_BETWEEN = 3       # and not more often than this (each is one model call, within [budgets] reevaluate/doover)


def changed_since_reevaluation(con) -> tuple[int, float | None]:
    """How much new input arrived since the last re-evaluation, and how many hours ago that was."""
    from datetime import datetime
    at = (json.loads(db.get_state(con, "brain_focus", "null") or "null") or {}).get("at")
    if not at:
        return 10 ** 6, None
    n = sum(con.execute(f"SELECT COUNT(*) FROM {t} WHERE created > ?", (at,)).fetchone()[0] for t in ("thoughts", "tasks", "ideas"))
    try:
        hours = (datetime.now() - datetime.fromisoformat(at)).total_seconds() / 3600
    except ValueError:
        hours = None
    return n, hours


def reevaluate_if_changed(con) -> dict | None:
    """The hourly cycle's smart overlayer: re-rank always happens by rule (free); Claude re-evaluates the plan
    (and drafts a do-over when it drifted) only when real input arrived: CHANGE_TRIGGER new items, at most every
    MIN_HOURS_BETWEEN hours. Over budget the model call falls back, so tokens stay bounded."""
    from . import doover
    n, hours = changed_since_reevaluation(con)
    if hours is not None and hours < MIN_HOURS_BETWEEN:
        return None
    if n < CHANGE_TRIGGER and not doover.structure_changed(con):  # a new structure or mission is real change too (RA-057)
        return None
    return doover.replan(con)


def focus(con) -> dict:
    """The latest re-evaluation, with a plain ranking as fallback when Claude has not run yet."""
    data = json.loads(db.get_state(con, "brain_focus", "null") or "null") or {}
    open_ids = {r[0] for r in con.execute("SELECT id FROM tasks WHERE status NOT IN ('done','dropped','blocked')")}
    for key in ("recommended", "ai_queue"):
        data[key] = [r for r in data.get(key, []) if r.get("task_id") in open_ids]
    if not data.get("recommended"):
        rules = loop.load_constitution()
        priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
        data["recommended"] = [{"task_id": t["id"], "title": t["title"], "why": "highest score in the plan"}
                               for t in priority.next_tasks(con, 4, owner="me")]
        data.setdefault("focus", "")
    data.setdefault("ai_queue", [])
    data.setdefault("suggest", [])
    return data


def accept_suggestion(con, action: str, task_ids: list[int], reason: str = "") -> str:
    """Djeddi confirms a drop/merge suggestion. Merge keeps the first task and drops the others."""
    if action == "drop":
        for t in task_ids:
            set_field(con, "tasks", t, "status", "dropped", f"dropped on suggestion: {reason}")
    elif action == "merge" and len(task_ids) > 1:
        keep = task_ids[0]
        for t in task_ids[1:]:
            title = con.execute("SELECT title FROM tasks WHERE id = ?", (t,)).fetchone()
            if title:
                con.execute("UPDATE tasks SET description = trim(description || char(10) || ?) WHERE id = ?",
                            (f"(merged: {title[0]})", keep))
                set_field(con, "tasks", t, "status", "dropped", f"merged into #{keep}: {reason}")
    else:
        raise ValueError("unknown suggestion")
    data = focus(con)
    data["suggest"] = [s for s in data.get("suggest", []) if s.get("task_ids") != task_ids]
    db.set_state(con, "brain_focus", json.dumps(data))
    db.log(con, "me", f"brain_{action}", ",".join(map(str, task_ids)))
    con.commit()
    return f"{action.capitalize()} done."


def text(con) -> str:
    f = focus(con)
    lines = []
    if f.get("focus"):
        lines += [f["focus"], ""]
    lines.append("Recommended now:")
    lines += [f"• #{r['task_id']} {r['title']}" + (f" ({r['why']})" if r["why"] else "") for r in f["recommended"]]
    if f["ai_queue"]:
        lines += ["", "For the AI agent:"] + [f"• #{r['task_id']} {r['title']}" for r in f["ai_queue"]]
    recent = [c for c in changes(con, 6) if not c["undone"]]
    if recent:
        lines += ["", "Latest changes by Claude (undo: /undo <n>):"]
        lines += [f"[{c['id']}] {describe_change(c)} – {c['reason']}" for c in recent]
    if f.get("error"):
        lines += ["", f"Last re-evaluation: {f['error']}"]
    return "\n".join(lines)
