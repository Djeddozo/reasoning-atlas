"""Skill tree: the capabilities of Djeddi's digital company, and what the agent should build next.

Each node is a capability (a system or skill Atlas has, or could have): Memory, Self-building, Prototype
builder, First digital product… A node requires other nodes, and tasks build it:

  unlocked   built: marked as built, or all of its tasks are done
  building   work has started (a task is in progress or done, or a PR is open)
  available  everything it requires is unlocked: it can be built now (the frontier)
  locked     waits for a node it requires

Ideas hang on the node they build on (ideas.capability_id), so the tree grows with every idea; Claude's
re-evaluation places new ideas and proposes new nodes (hq.brain), Djeddi can edit everything (hq.edit).

Build order. The dev loop does not just take the highest-scoring AI task any more (hq.devloop.pick_task):
  - logical: a task whose node is still locked waits (its foundations are not there yet)
  - frontier first: tasks on available/building nodes get a bonus
  - focus (Today → Build path): 'plan' favours tasks linked to goals, 'ideas' favours work that came from
    ideas, 'balanced' neither
  - big decisions: a task that is large (over [devloop] approve_over_hours), opens a new product, or
    touches something external or irreversible, is built only after Djeddi approves it (an approval on his
    phone and on Today). Until then the agent works on the next task.
"""
import json
import re

from . import brain, db, loop, priority

BRANCHES = [("core", "Foundation"), ("ops", "Operations"), ("mind", "Thinking"), ("reach", "Reach"),
            ("make", "Making"), ("product", "Products"), ("self", "Self-improving intelligence")]
BRANCH_KEYS = {k for k, _ in BRANCHES}
FOCUS = {"plan": "Plan first: tasks that move your goals", "balanced": "Balanced: plan and ideas alike",
         "ideas": "Ideas first: build what came from your ideas",
         "management": "Management agent first: planning, reasoning and reliable build coordination"}
FOCUS_BONUS = 1.6
FRONTIER_BONUS = 1.4
EXTERNAL = re.compile(r"\b(pay|payment|buy|purchas\w*|subscri\w*|accounts?|sign[ -]?up|publish\w*|deploy\w*|release|"
                      r"launch\w*|e-?mail\w*|send\w*|delet\w*|migrat\w*|install\w*|domain|stripe|shop)\b", re.I)

# The starting tree: what Atlas already is (built), and the path towards making products.
SEED = [
    # key, title, branch, requires, built, description
    ("memory", "Memory", "core", [], True, "One database for everything Atlas knows."),
    ("planning", "Planning & ranking", "core", ["memory"], True, "Goals, tasks and a ranked list of what to do next."),
    ("dashboard", "Dashboard", "core", ["planning"], True, "This page."),
    ("phone", "Phone & Telegram", "core", ["planning"], True, "Brief, approvals and thoughts from your phone."),
    ("devloop", "Self-building", "ops", ["planning"], True, "Claude Code on the Pi builds AI tasks and opens PRs."),
    ("control", "Usage & kill switch", "ops", ["devloop"], True, "Token and cost tracking; stop everything at once."),
    ("updates", "Automatic updates", "ops", ["devloop"], True, "Merged work installs itself after the tests pass."),
    ("finance", "Budget & spending", "ops", ["control"], False, "Budgets and a spend log; later a virtual card with hard limits."),
    ("workstreams", "Parallel workstreams", "ops", ["devloop", "control"], False, "Several agents on separate projects at once."),
    ("brain", "Brain", "mind", ["planning"], True, "Thoughts in, Claude sorts them into the plan."),
    ("chat", "Chat with Claude", "mind", ["brain"], True, "Ask about your plan; new plans are filed."),
    ("review", "Weekly review", "mind", ["planning"], True, "A written review of the week."),
    ("local_ai", "Local models (GPU PC)", "mind", ["brain"], False, "Private, free voice and text models on your GPU PC."),
    ("intake", "Intake: links & screenshots", "reach", ["brain"], True, "Send a link or image; Atlas reads and files it."),
    ("radar", "Radar", "reach", ["memory"], True, "Daily scan of what is new."),
    ("pdf", "Reading PDFs & scans", "reach", ["intake"], False, "Documents and scans become notes and tasks."),
    ("calendar", "Calendar & email (read-only)", "reach", ["intake"], False, "Atlas sees your schedule and mail, never sends."),
    ("arsenal", "Arsenal", "make", ["memory"], True, "Everything you built, searchable for reuse."),
    ("sandbox", "Sandbox for outside code", "make", ["devloop"], False, "Try GitHub tools safely, without credentials."),
    ("prototype", "Prototype builder", "make", ["devloop", "arsenal"], False, "Turns an idea into a small working app on its own branch."),
    ("packages", "Product packages", "make", ["prototype", "sandbox"], False, "Spec, build, test and package a product end to end."),
    ("first_product", "First digital product", "product", ["prototype"], False, "One of your ideas, shipped and usable."),
    ("physical", "Physical products", "product", ["packages"], False, "Designs for 3D printing and making, from your ideas."),
    ("shop", "Launch & sell", "product", ["packages", "finance"], False, "Publish and sell (accounts and money need your approval)."),
    # Atlas improving its own intelligence: how it remembers, plans its own work and learns
    ("better_memory", "Better memory", "self", ["memory", "brain"], False, "Recalls the right facts and lessons when it decides; condenses the rest."),
    ("checkpoints", "Checkpoint & resume", "self", ["devloop", "control"], False, "Saves progress before usage or context limits, so work resumes where it stopped."),
    ("parallel_sense", "Noticing parallel work", "self", ["planning", "devloop"], False, "Sees which tasks are independent and proposes building them side by side."),
    ("parallel_builds", "Parallel builds on branches", "self", ["parallel_sense", "checkpoints"], False, "Several tasks at once on separate branches; one approval for a batch of PRs."),
    ("auto_research", "Auto research per node", "self", ["brain", "radar"], False, "For a new node, researches how to build it and drafts the tasks."),
    ("learning", "Learning from outcomes", "self", ["better_memory", "devloop"], False, "Reviews merged and failed work, records lessons and adjusts how it plans."),
]


# Open roadmap tasks (seeds/roadmap.toml) that build a node of the starting tree: title words → node key.
ROADMAP_LINKS = [("calendar and email", "calendar"), ("sandbox (docker", "sandbox"), ("github skill radar", "sandbox"),
                 ("resource checks", "finance"), ("project packages", "packages"), ("parallel workstreams", "workstreams"),
                 ("gpu pc as hq", "local_ai"), ("voice notes in", "local_ai")]
# Tasks that build a node of a branch planted later (grow): title words → node key. These move onto the new node.
BRANCH_LINKS = [("checkpoint task progress", "checkpoints"), ("parallel on separate branches", "parallel_builds"),
                ("node → auto research", "auto_research"), ("node -> auto research", "auto_research")]


def ids(text: str | None) -> list[int]:
    return [int(x) for x in str(text or "").split(",") if x.strip().isdigit()]


def seed(con) -> int:
    """Plant the starting tree once (an empty table only); later branches grow into an existing tree."""
    if con.execute("SELECT 1 FROM capabilities LIMIT 1").fetchone():
        return grow(con)
    keys: dict[str, int] = {}
    for key, title, branch, req, built, desc in SEED:
        keys[key] = db.insert(con, "capabilities", title=title, branch=branch, description=desc,
                              requires=",".join(str(keys[r]) for r in req), status="unlocked" if built else "auto")
    linked = 0
    for t in con.execute("SELECT id, title FROM tasks WHERE capability_id IS NULL AND status NOT IN ('done', 'dropped')").fetchall():
        key = next((k for words, k in ROADMAP_LINKS if words in t["title"].lower()), None)
        if key:
            con.execute("UPDATE tasks SET capability_id = ? WHERE id = ?", (keys[key], t["id"]))
            linked += 1
    db.log(con, "hq", "skilltree_seed", f"{len(SEED)} capabilities, {linked} roadmap tasks linked")
    con.commit()
    return len(SEED)


def grow(con) -> int:
    """Plant each SEED branch that has no capability yet (e.g. a branch added after the first seed), once.

    A branch Djeddi dropped keeps its dropped rows, so it is never planted again. Requirements on nodes of
    other branches are found by title; open tasks matching BRANCH_LINKS move onto the new nodes (undoable).
    """
    have = {r[0] for r in con.execute("SELECT DISTINCT branch FROM capabilities")}
    missing = {b for _, _, b, _, _, _ in SEED} - have
    if not missing:
        return 0
    keys = {k: r[0] for k, title, *_ in SEED
            if (r := con.execute("SELECT id FROM capabilities WHERE title = ? AND status != 'dropped' ORDER BY id LIMIT 1",
                                 (title,)).fetchone())}
    planted = []
    for key, title, branch, req, built, desc in SEED:
        if branch not in missing:
            continue
        keys[key] = db.insert(con, "capabilities", title=title, branch=branch, description=desc,
                              requires=",".join(str(keys[r]) for r in req if r in keys),
                              status="unlocked" if built else "auto")
        brain.record(con, "create", "capabilities", keys[key], f"new skill tree branch: {branch}")
        planted.append(key)
    linked = 0
    for t in con.execute("SELECT id, title FROM tasks WHERE status NOT IN ('done', 'dropped')").fetchall():
        key = next((k for words, k in BRANCH_LINKS if words in t["title"].lower() and k in planted), None)
        if key and brain.set_field(con, "tasks", t["id"], "capability_id", keys[key], f"builds {key} (new branch)"):
            linked += 1
    db.log(con, "hq", "skilltree_grow", f"branches {', '.join(sorted(missing))}: {len(planted)} capabilities, {linked} tasks linked")
    con.commit()
    return len(planted)


# ---------------- state ----------------

def nodes(con) -> dict[int, dict]:
    """Every capability with its computed state, tasks and ideas."""
    seed(con)
    caps = {r["id"]: dict(r) for r in con.execute("SELECT * FROM capabilities WHERE status != 'dropped' ORDER BY id")}
    prs = {r[0] for r in con.execute("SELECT task_id FROM dev_runs WHERE status IN ('running', 'pr_open')")}
    for c in caps.values():
        c["requires"] = [i for i in ids(c["requires"]) if i in caps]
        c["tasks"] = [dict(r) | {"pr": r["id"] in prs} for r in con.execute(
            """SELECT id, title, owner, status, effort_h, goal_id FROM tasks
               WHERE capability_id = ? AND status != 'dropped' ORDER BY status = 'done', score DESC""", (c["id"],))]
        c["ideas"] = [dict(r) for r in con.execute(
            "SELECT id, title, category FROM ideas WHERE capability_id = ? AND gate = 'idea' ORDER BY score DESC", (c["id"],))]
        work = [t for t in c["tasks"] if t["status"] != "proposed"]  # drafts are not work yet
        c["done"] = sum(t["status"] == "done" for t in work)
        c["total"] = len(work)
    state: dict[int, str] = {}

    def resolve(cid: int, seen: frozenset = frozenset()) -> str:
        if cid in state:
            return state[cid]
        c = caps[cid]
        if cid in seen:  # a loop in 'requires' (hand-edited): treat as locked rather than recursing forever
            return "locked"
        if c["status"] == "unlocked" or (c["total"] and c["done"] == c["total"]):
            s = "unlocked"
        elif any(resolve(r, seen | {cid}) != "unlocked" for r in c["requires"]):
            s = "locked"
        elif c["done"] or any(t["status"] == "doing" or t["pr"] for t in c["tasks"]):
            s = "building"
        else:
            s = "available"
        state[cid] = s
        return s

    for cid in caps:
        caps[cid]["state"] = resolve(cid)

    def depth(cid: int, seen: frozenset = frozenset()) -> int:
        reqs = [r for r in caps[cid]["requires"] if r not in seen]
        return 0 if not reqs else 1 + max(depth(r, seen | {cid}) for r in reqs)
    for cid in caps:
        caps[cid]["depth"] = depth(cid)
    return caps


def get_focus(con) -> str:
    f = db.get_state(con, "build_focus", "balanced")
    return f if f in FOCUS else "balanced"


def set_focus(con, focus: str) -> str:
    if focus not in FOCUS:
        raise ValueError(f"focus must be one of: {', '.join(FOCUS)}")
    db.set_state(con, "build_focus", focus)
    db.log(con, "me", "build_focus", focus)
    con.commit()
    return FOCUS[focus]


# ---------------- build order ----------------

def gate(task: dict, node: dict | None, conf: dict) -> str:
    """Why this task needs Djeddi's OK before the agent builds it ('' = it does not)."""
    if outside(task):
        return OUTSIDE_GATE
    limit = float(conf.get("approve_over_hours", 4) or 0)
    if limit and float(task.get("effort_h") or 0) > limit:
        return f"big: about {task['effort_h']:g} hours of work"
    if node and node["branch"] == "product":
        return f"builds a product ({node['title']})"
    if node and node["state"] == "available" and not node["done"]:  # its first task, even if it is the only one
        return f"opens a new capability: {node['title']}"
    m = EXTERNAL.search(f"{task.get('title', '')} {task.get('description', '')}")
    if m:
        return f"touches something external or irreversible ('{m.group(0)}')"
    return ""


OUTSIDE_GATE = "uses outside code: always needs your manual approval (decision #75)"


def outside(task: dict) -> bool:
    """A task that uses outside code (from a research option): never built without an approved approval,
    not even from Djeddi's own queue or by Run."""
    from . import research
    return research.OUTSIDE_MARK in (task.get("description") or "")


def _approval(con, task_id: int) -> str | None:
    row = con.execute("SELECT status FROM approvals WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    return row[0] if row else None


def build_order(con, conf: dict | None = None) -> dict:
    """AI tasks in the order the agent should build them, with the reason and whether Djeddi must approve."""
    from . import devloop
    conf = conf or devloop.cfg()
    rules = loop.load_constitution()
    priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
    caps = nodes(con)
    focus = get_focus(con)
    goals = {r[0] for r in con.execute("SELECT id FROM goals WHERE status = 'active'")}
    ready, waiting = [], []
    for t in priority.next_tasks(con, None, owner="ai"):  # all of them (#181: a cap of 60 hid the rest of the queue)
        t = dict(t)
        if t.get("queued_by") == "me":  # Djeddi's own queue is handled separately and always first (build_plan)
            continue
        node = caps.get(t.get("capability_id"))
        why = []
        if node and node["state"] == "locked":
            missing = [caps[r]["title"] for r in node["requires"] if caps[r]["state"] != "unlocked"]
            waiting.append({"task": t, "node": node["title"], "why": f"waits for {', '.join(missing)}"})
            continue
        weight = float(t["score"] or 0) + 0.01
        if node:
            if node["state"] in ("available", "building"):  # the frontier, not maintenance on built capabilities
                weight *= FRONTIER_BONUS
            why.append(f"builds {node['title']}")
        from_idea = "(from idea #" in (t["description"] or "") or bool(node and node.get("idea_id"))
        if focus == "plan" and t["goal_id"] in goals:
            weight *= FOCUS_BONUS
            why.append("moves a goal")
        elif focus == "ideas" and from_idea:
            weight *= FOCUS_BONUS
            why.append("from your ideas")
        if focus == "management" and node and node["branch"] in {"mind", "ops", "self"}:
            weight *= FOCUS_BONUS
            why.append("strengthens your management agent")
        g = gate(t, node, conf)
        ready.append({"task": t, "node": node["title"] if node else "", "weight": round(weight, 3),
                      "why": ", ".join(why) or "top of the plan", "gate": g,
                      "approval": _approval(con, t["id"]) if g else None})
    ready.sort(key=lambda e: (e["task"]["status"] != "doing", -e["weight"]))
    return {"focus": focus, "ready": ready, "waiting": waiting}


def queue(con, task_id: int, mine: bool = True, top: bool = False) -> None:
    """Djeddi puts a task in the agent's queue himself (mine) or takes it out. His queue runs first, in his order."""
    if mine:
        when = db.now()
        if top:
            first = con.execute("SELECT MIN(queued_at) FROM tasks WHERE queued_by = 'me' AND status NOT IN ('done', 'dropped')"
                                ).fetchone()[0]
            when = "0" + first if first else when  # sorts before every other queued task
        con.execute("UPDATE tasks SET queued_by = 'me', queued_at = ? WHERE id = ?", (when, task_id))
    else:
        con.execute("UPDATE tasks SET queued_by = '', queued_at = NULL WHERE id = ?", (task_id,))
    con.commit()


def mine_order(con) -> list[int]:
    """Djeddi's own build queue, in his order."""
    return [r[0] for r in con.execute("""SELECT id FROM tasks WHERE queued_by = 'me' AND status NOT IN ('done', 'dropped')
                                         ORDER BY status = 'doing' DESC, queued_at, id""")]


def set_order(con, ids: list[int]) -> list[int]:
    """Djeddi's order (dragged in Forge › Build): these tasks first, in this order, then the rest of his queue.
    Placing a task here puts it in his queue (his OK to build it, as queuing always was)."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    known = {r[0] for r in con.execute(f"SELECT id FROM tasks WHERE status NOT IN ('done', 'dropped') AND id IN ({','.join('?' * len(ids))})",
                                       ids)} if ids else set()
    missing = [i for i in ids if i not in known]
    if missing:
        raise ValueError("not open: " + ", ".join(f"#{i}" for i in missing))
    order = ids + [i for i in mine_order(con) if i not in ids]
    for n, tid in enumerate(order):
        con.execute("UPDATE tasks SET queued_by = 'me', queued_at = ? WHERE id = ?", (f"0000-{n:05d}", tid))
    db.log(con, "me", "queue_order", ",".join(map(str, order[:20])))
    con.commit()
    return order


def place(con, task_id: int, position: int = 5) -> int:
    """Put a task into Djeddi's queue at `position` (1-based; default the last of the five shown): what stood
    there moves one down. With fewer queued tasks it joins the end. Returns its position."""
    order = [i for i in mine_order(con) if i != task_id]
    at = min(len(order), max(0, position - 1))
    order.insert(at, task_id)
    set_order(con, order)
    return at + 1


# ---------------- the Build next lane (#182, owner 2026-10-26) ----------------
# A short, ordered list Djeddi sets by hand: the builder takes these first, in this order, before his queue and
# before any score. Places live in tasks.build_next and change only through brain.set_field, so every change is
# in the change log and can be undone. A task leaves the lane when it is built (its pull request is open or
# merged), done or dropped.

def lane_cap() -> int:
    from . import devloop
    return max(1, int(devloop.cfg().get("build_next_max", 5) or 5))


def lane(con) -> list[int]:
    return [r[0] for r in con.execute("""SELECT id FROM tasks WHERE build_next IS NOT NULL AND status NOT IN ('done', 'dropped')
                                         ORDER BY build_next, id""")]


def _write_lane(con, order: list[int], reason: str, batch: str) -> None:
    from . import brain
    for n, tid in enumerate(order, 1):
        brain.set_field(con, "tasks", tid, "build_next", n, reason, batch=batch)


def set_lane(con, task_id: int, position: int | None) -> str:
    """Put a task at `position` (1-based) in the Build next lane, move it there, or take it out (None / 0)."""
    from . import brain
    batch = "lane-" + db.now()
    t = con.execute("SELECT id, title, owner, status FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
    if t is None or t["status"] in ("done", "dropped"):
        raise ValueError(f"task #{task_id} is not open")
    order = [i for i in lane(con) if i != t["id"]]
    if not position:
        if not brain.set_field(con, "tasks", t["id"], "build_next", None, "taken out of Build next by Djeddi", batch=batch):
            return f"Task #{t['id']} is not in Build next."
        _write_lane(con, order, "Build next closes up", batch)
        con.commit()
        return f"Task #{t['id']} left Build next."
    if t["owner"] not in ("ai", "both"):
        raise ValueError(f"task #{t['id']} is yours, not the builder's; hand it to the builder first")
    cap = lane_cap()
    at = min(max(1, int(position)), len(order) + 1, cap)
    order.insert(at - 1, t["id"])
    out = order[cap:]
    for tid in out:  # a full lane: the last one steps back to the normal queue
        brain.set_field(con, "tasks", tid, "build_next", None, "pushed out of a full Build next", batch=batch)
    _write_lane(con, order[:cap], "placed in Build next by Djeddi", batch)
    db.log(con, "me", "build_next", f"#{t['id']} at {at}: " + ",".join(map(str, order[:cap])))
    con.commit()
    return (f"Task #{t['id']} is number {at} in Build next: the builder takes it "
            + ("next." if at == 1 else f"after #{order[at - 2]}.")
            + (" " + ", ".join(f"#{i}" for i in out) + " moved back to the queue." if out else ""))


def set_lane_order(con, ids: list) -> str:
    """The lane in a new order (dragged in Forge › Build); only tasks already in it."""
    now = lane(con)
    order = [int(i) for i in dict.fromkeys(ids) if int(i) in now] if isinstance(ids, list) else []
    if not order:
        raise ValueError("order must list tasks in Build next")
    order += [i for i in now if i not in order]
    _write_lane(con, order, "Build next reordered by Djeddi", "lane-" + db.now())
    con.commit()
    return "Build next: " + " → ".join(f"#{i}" for i in order) + "."


def move_in_lane(con, task_id: int, direction: str) -> str:
    """edit.move for a task in the lane: one place up or down within it."""
    order = lane(con)
    i = order.index(task_id)
    j = i - 1 if direction == "up" else i + 1
    if not 0 <= j < len(order):
        return f"Task #{task_id} is already {'first' if direction == 'up' else 'last'} in Build next."
    order[i], order[j] = order[j], order[i]
    _write_lane(con, order, "Build next reordered by Djeddi", "lane-" + db.now())
    con.commit()
    return f"Task #{task_id} is number {j + 1} in Build next."


def lane_cleanup(con) -> int:
    """Built (pull request open or merged), done or dropped tasks leave the lane; the rest close up."""
    from . import brain
    gone = [r[0] for r in con.execute("""SELECT t.id FROM tasks t WHERE t.build_next IS NOT NULL AND (t.status IN ('done', 'dropped')
                                         OR EXISTS (SELECT 1 FROM dev_runs r WHERE r.task_id = t.id AND r.status IN ('pr_open', 'merged')))""")]
    if not gone:
        return 0
    batch = "lane-" + db.now()
    for tid in gone:
        brain.set_field(con, "tasks", tid, "build_next", None, "built, done or dropped: leaves Build next", batch=batch)
    _write_lane(con, lane(con), "Build next closes up", batch)
    con.commit()
    return len(gone)


def blocked(con, task) -> str:
    """Why a task cannot be built now ('' = it can): open dependencies or a hold in the repository's plan."""
    from . import atlasplan
    deps = [int(x) for x in str(task["depends_on"] or "").replace(" ", "").split(",") if x.isdigit()]
    if deps:
        open_ = [r[0] for r in con.execute(f"SELECT id FROM tasks WHERE id IN ({','.join('?' * len(deps))}) AND status NOT IN ('done', 'dropped')", deps)]
        if open_:
            return "waits for " + ", ".join(f"#{i}" for i in open_)
    return atlasplan.block_reason(task)


def adopt_existing_queue(con) -> int:
    """Once: tasks Djeddi gave to the AI before the queue existed (→ AI, ideas → AI queue) become his queue."""
    if db.get_state(con, "queue_adopted"):
        return 0
    rows = con.execute("""SELECT DISTINCT c.row_id, MIN(c.ts) ts FROM brain_changes c JOIN tasks t ON t.id = c.row_id
                          WHERE c.table_name = 'tasks' AND c.undone = 0 AND t.owner IN ('ai', 'both')
                          AND t.status NOT IN ('done', 'dropped') AND COALESCE(t.queued_by, '') = ''
                          AND ((c.field = 'owner' AND c.reason = 'queued for the AI by Djeddi')
                               OR (c.action = 'create' AND c.reason = 'queued by Djeddi'))
                          GROUP BY c.row_id""").fetchall()
    for r in rows:
        con.execute("UPDATE tasks SET queued_by = 'me', queued_at = ? WHERE id = ?", (r["ts"], r["row_id"]))
    db.set_state(con, "queue_adopted", db.now())
    con.commit()
    return len(rows)


def _source(t: dict, caps: dict, goals: dict) -> str:
    node = caps.get(t.get("capability_id"))
    if "(from idea #" in (t.get("description") or "") or (node and node.get("idea_id")):
        return "from an idea"
    if t.get("goal_id") in goals:
        return f"for goal: {goals[t['goal_id']]}"
    return "Atlas's plan"


def build_plan(con, conf: dict | None = None) -> dict:
    """What the agent is going to build, in order:
    mine      tasks Djeddi queued himself: first, in his order, no approval needed (queuing is his OK)
    proposed  AI tasks from the plan, ideas or Claude, in build order; big steps wait for approval
    waiting   what cannot start now, and why (another task first, locked capability, PR open, failed twice)
    """
    from . import devloop
    conf = conf or devloop.cfg()
    adopt_existing_queue(con)
    order = build_order(con, conf)
    caps = nodes(con)
    goals = dict(con.execute("SELECT id, title FROM goals WHERE status = 'active'").fetchall())
    runs = dict(con.execute("SELECT task_id, status FROM dev_runs WHERE status IN ('running', 'pr_open')").fetchall())
    from . import atlasplan, resume
    failed = {}
    limited = {r[0] for r in con.execute("SELECT session_id FROM interruptions WHERE tool = 'devloop'")}  # the limit may show only in the log (#158)
    for run in con.execute("SELECT id,task_id,summary FROM dev_runs WHERE status IN ('failed','tests_failed','stopped','no_changes')"):
        if not resume.is_limit(run["summary"]) and f"run-{run['id']}" not in limited:
            failed[run["task_id"]] = failed.get(run["task_id"], 0) + 1
    status = dict(con.execute("SELECT id, status FROM tasks").fetchall())

    def item(t: dict, source: str, why: str = "", node: str = "", gate_: str = "", approval=None) -> dict:
        state, note = "ready", ""
        deps = [d for d in ids(t.get("depends_on")) if status.get(d) not in ("done", "dropped")]
        if runs.get(t["id"]) == "running":
            state, note = "working", "the agent is on it now"
        elif runs.get(t["id"]) == "pr_open":
            state, note = "pr", "pull request waiting for your review and merge"
        elif atlasplan.block_reason(t):
            state, note = "blocked", atlasplan.block_reason(t)
        elif failed.get(t["id"], 0) >= 2:
            state, note = "failed", f"failed {failed[t['id']]} times; Run tries again"
        elif deps:
            state, note = "blocked", "after " + ", ".join(f"#{d}" for d in deps) + ("; Run builds it anyway" if source == "you" else "")
        elif gate_ and approval != "approved":
            state = "needs_ok"
            note = {"pending": "waiting for your OK", "denied": "you said not now"}.get(approval, "needs your OK") + f": {gate_}"
        return {"id": t["id"], "title": t["title"], "owner": t.get("owner"), "source": source, "why": why, "node": node, "state": state,
                "note": note, "gate": gate_, "approval": approval, "effort_h": t.get("effort_h")}

    lane_cleanup(con)
    lane_ids = lane(con)
    next_lane = []
    for tid in lane_ids:
        t = dict(con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())
        node = caps.get(t.get("capability_id"))
        out = OUTSIDE_GATE if outside(t) else ""
        next_lane.append(item(t, "you", "you put this here", node["title"] if node else "", out, _approval(con, tid) if out else None)
                         | {"place": len(next_lane) + 1})
    mine = []
    for r in con.execute("""SELECT * FROM tasks WHERE queued_by = 'me' AND owner IN ('ai', 'both')
                            AND status NOT IN ('done', 'dropped') AND build_next IS NULL ORDER BY status = 'doing' DESC, queued_at"""):
        t = dict(r)
        node = caps.get(t.get("capability_id"))
        out = OUTSIDE_GATE if outside(t) else ""
        it = item(t, "you", "you queued it", node["title"] if node else "", out, _approval(con, t["id"]) if out else None)
        if node and node["state"] == "locked" and it["state"] == "ready":
            it["note"] = "its capability is still locked; built anyway because you queued it"
        mine.append(it)
    proposed = [item(e["task"], _source(e["task"], caps, goals), e["why"], e["node"], e["gate"], e["approval"])
                for e in order["ready"] if e["task"]["id"] not in lane_ids]
    waiting = [i for i in proposed if i["state"] in ("blocked", "pr", "failed")]
    proposed = [i for i in proposed if i not in waiting]
    waiting += [item(e["task"], _source(e["task"], caps, goals), e["why"], e["node"]) | {"state": "locked", "note": e["why"]}
                for e in order["waiting"]]
    waiting = [w for w in waiting if w["id"] not in lane_ids]
    nxt = next((i for i in next_lane + mine + proposed if i["state"] == "ready"), None)
    return {"focus": order["focus"], "lane": next_lane, "lane_cap": lane_cap(), "mine": mine, "proposed": proposed,
            "waiting": waiting, "next_id": nxt["id"] if nxt else None}


def why_nothing(plan: dict) -> str:
    """Why the agent cannot start anything right now, in one sentence ('' = it can)."""
    if plan["next_id"]:
        return ""
    mine = plan["mine"]
    for state, msg in (("blocked", "Your queued task #{id} waits for another task ({note}). Press Run on it to build it anyway."),
                       ("failed", "Your queued task #{id} {note}."),
                       ("pr", "Your queued task #{id} already has a pull request: review and merge it.")):
        hit = next((i for i in mine if i["state"] == state), None)
        if hit:
            return msg.format(id=hit["id"], note=hit["note"])
    ok = [i for i in plan["proposed"] if i["state"] == "needs_ok"]
    if ok:
        return f"{len(ok)} task(s) wait for your OK (#{ok[0]['id']}: {ok[0]['gate']}). Approve in the build queue, or press Run on one."
    if plan["waiting"]:
        w = plan["waiting"][0]
        return f"Nothing is ready: #{w['id']} {w['note']}. Press Run on a task to build it anyway."
    return "The build queue is empty. Queue a task for the AI (→ AI on a task or idea), or add one with + Task."


def ask_approval(con, task: dict, reason: str) -> None:
    """One approval per big task: Approve/Deny on the phone and on Today. The agent moves on meanwhile."""
    from . import gatekeeper
    row = con.execute("SELECT description FROM tasks WHERE id = ?", (task["id"],)).fetchone()
    manual = outside({"description": row[0] if row else ""})
    if manual and "Trust:" not in reason:  # outside code: the security gate's findings go with the question (#75)
        from . import sandbox
        reason = f"{reason}. {sandbox.evidence(con, task['id'])}"
    gatekeeper.request_approval(con, f"Build task #{task['id']} {task['title']}?",
                                f"{reason}. The agent builds it only after you approve.",
                                gatekeeper.MANUAL_ONLY if manual else "action", wait=0, task_id=task["id"])
    db.log(con, "hq", "build_gate", f"task #{task['id']}: {reason}")
    con.commit()


def decide(con, task_id: int, ok: bool) -> str:
    """Approve or decline building a big task (the pending approval if there is one, else a new record)."""
    task = con.execute("SELECT id, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError(f"task #{task_id} does not exist")
    full = con.execute("SELECT description FROM tasks WHERE id = ?", (task_id,)).fetchone()
    manual = outside({"description": full[0]})
    if ok and manual:
        from . import flow
        if flow.actor() != "me":  # decision #75: no agent and no automatic path approves outside code
            raise ValueError(f"task #{task_id} uses outside code: only Djeddi can approve it, by hand")
    status = "approved" if ok else "denied"
    pending = con.execute("SELECT id FROM approvals WHERE task_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                          (task_id,)).fetchone()
    if pending:
        con.execute("UPDATE approvals SET status = ?, decided = ? WHERE id = ?", (status, db.now(), pending[0]))
    else:
        db.insert(con, "approvals", request=f"Build task #{task_id} {task['title']}?", reason="decided on the Build path",
                  category="outside_code" if manual else "action", status=status, decided=db.now(), task_id=task_id)
    db.log(con, "me", f"build_{'ok' if ok else 'no'}", f"task #{task_id}")
    con.commit()
    return f"Approved: the agent may build #{task_id}." if ok else f"Not now: the agent skips #{task_id}."


# ---------------- changes ----------------

def add(con, title: str, branch: str, requires: list[int], description: str = "", idea_id: int | None = None,
        reason: str = "added by Djeddi", batch: str = "", actor: str = "me") -> int:
    title = title.strip()
    if not title:
        raise ValueError("a capability needs a title")
    if branch not in BRANCH_KEYS:
        raise ValueError(f"branch must be one of: {', '.join(sorted(BRANCH_KEYS))}")
    known = {r[0] for r in con.execute("SELECT id FROM capabilities WHERE status != 'dropped'")}
    cid = db.insert(con, "capabilities", title=title[:120], branch=branch, description=description[:1000],
                    requires=",".join(str(i) for i in requires if i in known), idea_id=idea_id)
    brain.record(con, "create", "capabilities", cid, reason, batch=batch)
    if idea_id and con.execute("SELECT 1 FROM ideas WHERE id = ? AND gate = 'idea'", (idea_id,)).fetchone():
        brain.set_field(con, "ideas", idea_id, "capability_id", cid, f"grew into capability #{cid}", batch)
    db.log(con, actor, "capability_add", f"#{cid} {title[:80]}")
    con.commit()
    return cid


def would_loop(con, cid: int, requires: list[int]) -> bool:
    """Would node cid requiring these nodes create a cycle?"""
    reqs = {r["id"]: ids(r["requires"]) for r in con.execute("SELECT id, requires FROM capabilities")}
    stack, seen = list(requires), set()
    while stack:
        n = stack.pop()
        if n == cid:
            return True
        if n not in seen:
            seen.add(n)
            stack.extend(reqs.get(n, []))
    return False


# ---------------- queue this: a node becomes work ----------------

def draft_task(con, cid: int) -> int:
    """'Queue this' on a node: a draft AI task that builds it (status proposed: the agent ignores it).

    Djeddi edits the draft, then queue_draft puts it in his queue. A node keeps one open draft: asking again
    returns the existing one. The link is tasks.capability_id, the same link every other task on the tree uses.
    """
    caps = nodes(con)
    node = caps.get(cid)
    if node is None:
        raise ValueError(f"capability #{cid} does not exist")
    old = con.execute("SELECT id FROM tasks WHERE capability_id = ? AND status = 'proposed' AND owner IN ('ai', 'both') "
                      "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    if old:
        return old[0]
    open_ = [t for t in node["tasks"] if t["status"] not in ("done", "proposed")]
    title = (f"Next step for {node['title']}" if node["done"] or open_ else f"Build {node['title']}")
    lines = [node["description"] or ""]
    missing = [caps[r]["title"] for r in node["requires"] if caps[r]["state"] != "unlocked"]
    if missing:
        lines.append(f"Note: still needs {', '.join(missing)}; queuing it builds it anyway.")
    if open_:
        lines.append("Already open on this node: " + "; ".join(f"#{t['id']} {t['title']}" for t in open_[:5]))
    if node["ideas"]:
        lines.append("Ideas on this node: " + "; ".join(f"#{i['id']} {i['title']}" for i in node["ideas"][:5]))
    tid = db.insert(con, "tasks", title=title[:200], description="\n".join(x for x in lines if x)[:4000], owner="ai",
                    status="proposed", capability_id=cid)
    brain.record(con, "create", "tasks", tid, f"draft from skill tree node {node['title']}")
    db.log(con, "me", "node_draft", f"capability #{cid} → draft task #{tid}")
    con.commit()
    return tid


def queue_draft(con, task_id: int, fields: dict | None = None) -> str:
    """Save Djeddi's edits to a draft and put it in his build queue (todo, queued by him: built first)."""
    from . import edit
    row = con.execute("SELECT status, owner FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ValueError(f"task #{task_id} does not exist")
    if row["status"] in ("done", "dropped"):
        raise ValueError(f"task #{task_id} is {row['status']}")
    fields = {k: v for k, v in (fields or {}).items() if k != "status"}
    if fields:
        edit.edit(con, "tasks", task_id, fields)
    brain.set_field(con, "tasks", task_id, "status", "todo" if row["status"] == "proposed" else row["status"],
                    "queued from the skill tree by Djeddi")
    if con.execute("SELECT owner FROM tasks WHERE id = ?", (task_id,)).fetchone()[0] == "me":
        brain.set_field(con, "tasks", task_id, "owner", "ai", "queued for the AI by Djeddi")
    queue(con, task_id)
    db.log(con, "me", "node_queue", f"task #{task_id}")
    con.commit()
    return f"Queued #{task_id} for the agent: it is built after the tasks already in your queue."


# ---------------- views ----------------

PROOF_TEXT = {"confirmed": "Confirmed", "partial": "Partly shown", "suggested": "Suggested", "none": "Not yet"}
SUGGEST_AT = 0.3  # word-overlap score from which unlinked built work is offered as possible proof


def proof(con, caps: dict[int, dict]) -> None:
    """How sure Atlas is that Djeddi has each capability, from evidence only (adds 'proof' and 'evidence' per node):

      confirmed  he marked it built, or all of its work is done (tasks he confirmed, or PRs merged for them)
      partial    some of its tasks are done, or a PR for one of them is merged: progress is shown
      suggested  nothing linked yet, but merged PRs or done tasks elsewhere look like it (word overlap):
                 inferred, not verified; he confirms (the node is marked built, undoable) or dismisses it
      none       still to acquire

    Installing a library or tool is never proof on its own: only done work and merged code count.
    No model calls: database and word overlap only."""
    from . import overlap
    prs: dict[int, list[dict]] = {}
    for r in con.execute("SELECT number, title, stage, task_ids FROM prs WHERE state = 'MERGED'"):
        for t in ids(r["task_ids"]):
            prs.setdefault(t, []).append(dict(r))
    dismissed = json.loads(db.get_state(con, "proof:dismissed", "{}") or "{}")
    pool = None
    for c in caps.values():
        done = [t for t in c["tasks"] if t["status"] == "done"]
        c["evidence"] = [{"kind": "task", "ref": f"#{t['id']}", "title": t["title"],
                          "prs": [f"PR #{p['number']}" + (" (installed)" if p["stage"] == "live" else "") for p in prs.get(t["id"], [])]}
                         for t in done]
        if c["status"] == "unlocked" or (c["total"] and c["done"] == c["total"]):
            c["proof"] = "confirmed"
        elif done:
            c["proof"] = "partial"
        else:
            c["proof"] = "none"
            if pool is None:  # built work not linked to any node: merged PRs and done tasks
                pool = [{"ref": f"PR #{r['number']}", "title": r["title"], "words": overlap._words(f"{r['title']} {(r['body'] or '')[:400]}")}
                        for r in con.execute("SELECT number, title, body FROM prs WHERE state = 'MERGED' ORDER BY number DESC LIMIT 80")]
                pool += [{"ref": f"#{r['id']}", "title": r["title"], "words": overlap._words(f"{r['title']} {r['description'] or ''}")}
                         for r in con.execute("SELECT id, title, description FROM tasks WHERE status = 'done' AND capability_id IS NULL")]
            mine = overlap._words(f"{c['title']} {c['description'] or ''}")
            skip = set(dismissed.get(str(c["id"]), []))
            hits = sorted(((overlap._score(mine, e["words"]), e) for e in pool if e["ref"] not in skip), key=lambda x: -x[0])
            c["suggested"] = [{"ref": e["ref"], "title": e["title"], "score": round(sc, 2)} for sc, e in hits[:2] if sc >= SUGGEST_AT]
            if c["suggested"]:
                c["proof"] = "suggested"
        c.setdefault("suggested", [])
        c["proof_text"] = PROOF_TEXT[c["proof"]]


def confirm(con, cid: int, ref: str = "") -> str:
    """Djeddi confirms he has this capability: the node is marked built (brain change log, undoable)."""
    reason = f"confirmed by Djeddi{' from ' + ref if ref else ''}"
    if not brain.set_field(con, "capabilities", cid, "status", "unlocked", reason):
        return "Already marked as built."
    db.log(con, "me", "proof_confirm", f"capability {cid} {ref}")
    con.commit()
    return "Marked as built (undo under Memory › What changed)."


def dismiss(con, cid: int, ref: str) -> str:
    """Not proof of this capability: the suggestion is not offered again."""
    seen = json.loads(db.get_state(con, "proof:dismissed", "{}") or "{}")
    seen.setdefault(str(cid), [])
    if ref not in seen[str(cid)]:
        seen[str(cid)].append(ref)
    db.set_state(con, "proof:dismissed", json.dumps(seen))
    db.log(con, "me", "proof_dismiss", f"capability {cid} {ref}")
    con.commit()
    return "Noted: not proof of this skill."


def tree(con) -> dict:
    """Everything the Build path on Today shows."""
    caps = nodes(con)
    proof(con, caps)
    focus = get_focus(con)
    loose = [dict(r) for r in con.execute(
        "SELECT id, title, category FROM ideas WHERE gate = 'idea' AND capability_id IS NULL ORDER BY score DESC LIMIT 12")]
    counts = {s: sum(c["state"] == s for c in caps.values()) for s in ("unlocked", "building", "available", "locked")}
    counts["proof"] = {p: sum(c["proof"] == p for c in caps.values()) for p in PROOF_TEXT}
    return {"branches": [{"key": k, "title": t} for k, t in BRANCHES], "nodes": list(caps.values()),
            "loose_ideas": loose, "counts": counts, "focus": focus, "focus_text": FOCUS[focus],
            "plan": (plan := build_plan(con)),
            "next": (plan["mine"] + plan["proposed"])[:5], "waiting": plan["waiting"][:8]}


def text(con) -> str:
    t = tree(con)
    c = t["counts"]
    lines = [f"Skill tree: {c['unlocked']} unlocked · {c['building']} building · {c['available']} ready to build · "
             f"{c['locked']} locked", f"Focus: {t['focus_text']}", ""]
    frontier = [n for n in t["nodes"] if n["state"] in ("building", "available")]
    if frontier:
        lines.append("Can be built now:")
        lines += [f"• {n['title']}" + (f" ({n['done']}/{n['total']} tasks)" if n["total"] else " (no tasks yet)")
                  for n in frontier[:8]]
    p = t["plan"]
    if p["mine"]:
        lines += ["", "Your queue (built first):"] + [f"• #{i['id']} {i['title']}" + (f" – {i['note']}" if i["note"] else "")
                                                     for i in p["mine"]]
    if p["proposed"]:
        lines += ["", "Proposed by Atlas (after your queue):"]
        lines += [f"• #{i['id']} {i['title']} ({i['source']})" + (f" – {i['note']}" if i["note"] else "")
                  for i in p["proposed"][:6]]
    if p["waiting"]:
        lines += ["", "Waiting:"] + [f"• #{i['id']} {i['title']} – {i['note']}" for i in p["waiting"][:6]]
    if not p["next_id"]:
        lines += ["", why_nothing(p)]
    return "\n".join(lines)


def as_json(con) -> str:
    return json.dumps(tree(con), default=str)
