"""The full overview: every task and idea in one list, filterable, with what blocks what.

`hq list` (open work) / `hq list --all` (also done) and the dashboard's Plan → All tasks view (GET /api/overview)
both use `items()`. Filters:
  status  – task statuses and/or idea gates, e.g. {"todo", "blocked", "idea"}; empty = default set
  goal    – a goal id (its sub-goals included), or 0 for tasks linked to no goal
  due     – overdue | today | week | none | YYYY-MM-DD (due on or before that day)
  kind    – all | tasks | ideas
  stage   – a delivery stage (STAGES), e.g. "review" for built work that waits for Djeddi's merge
Ideas have no goal or due date, so a goal or due filter shows tasks only.
"""
import subprocess
import time
from datetime import date, timedelta

from . import brain, db, loop, priority, routing

TASK_ORDER = ["doing", "todo", "blocked", "proposed", "done", "dropped"]
IDEA_ORDER = ["idea", "promoted", "dropped"]  # promoted = became a task (hq.flow)
OPEN_TASKS = {"doing", "todo", "blocked", "proposed"}
OPEN_IDEAS = {"idea"}
DUE_CHOICES = ("overdue", "today", "week", "none")
# Where each task stands on the way from idea to running code, left (finished) to right (not started).
STAGES = {"live": "Installed & live", "install": "Merged, waiting for install", "review": "Built, waiting for your review",
          "building": "Being built", "todo": "To do", "blocked": "Blocked", "proposed": "Proposed", "dropped": "Dropped"}
_COMMITS: dict = {"at": 0.0, "head": set(), "main": set()}


def _commits() -> tuple[set, set]:
    """Commits in the running code (HEAD) and in origin/main, cached for a minute (two git calls)."""
    if time.time() - _COMMITS["at"] > 60:
        def revs(ref):
            r = subprocess.run(["git", "rev-list", "--max-count=5000", ref], cwd=db.ROOT, capture_output=True, text=True, timeout=10)
            return set(r.stdout.split()) if r.returncode == 0 else set()
        _COMMITS.update(at=time.time(), head=revs("HEAD"), main=revs("origin/main"))
    return _COMMITS["head"], _COMMITS["main"]


def stages(con, all_tasks: list[dict]) -> dict[int, str]:
    """Delivery stage per task. Done AI work counts as live only once its commit is in the code Atlas runs;
    squash-merged work (commit not in main's history) counts as live when the running code includes all of main."""
    head, main = _commits()
    up_to_date = bool(main) and main <= head
    runs = {r["task_id"]: dict(r) for r in con.execute(
        "SELECT task_id, status, head FROM dev_runs WHERE id IN (SELECT MAX(id) FROM dev_runs GROUP BY task_id)")}
    out = {}
    for t in all_tasks:
        run, st = runs.get(t["id"]), t["status"]
        sha = (run or {}).get("head") or ""
        merged = bool(run) and (run["status"] == "merged" or sha in main)
        if st == "done" or (run and run["status"] == "pr_open" and sha in main):
            if not run or not sha or sha in head or (merged and sha not in main and up_to_date) or not main:
                out[t["id"]] = "live"
            else:
                out[t["id"]] = "install"
        elif st == "doing":
            out[t["id"]] = "review" if run and run["status"] == "pr_open" else "building"
        else:
            out[t["id"]] = st if st in STAGES else "todo"
    return out


def _ids(text: str | None) -> list[int]:
    return [int(x) for x in (text or "").split(",") if x.strip().isdigit()]


def _goal_ids(con, goal: int) -> set[int]:
    """The goal and every goal beneath it."""
    children: dict[int, list[int]] = {}
    for g in con.execute("SELECT id, parent_id FROM goals"):
        children.setdefault(g["parent_id"], []).append(g["id"])
    out, stack = set(), [goal]
    while stack:
        g = stack.pop()
        if g not in out:
            out.add(g)
            stack.extend(children.get(g, []))
    return out


def _due_ok(due: str | None, rule: str, today: date) -> bool:
    if rule == "none":
        return not due
    if not due:
        return False
    if rule == "overdue":
        return due < today.isoformat()
    if rule == "today":
        return due <= today.isoformat()
    if rule == "week":
        return due <= (today + timedelta(days=7)).isoformat()
    return due <= rule


def items(con, status: set[str] | None = None, goal: int | None = None, due: str | None = None,
          kind: str = "all", include_done: bool = False, today: date | None = None, search: str = "", sort: str = "status",
          created_from: str = "", created_to: str = "", recommendation: str = "", stage: str = "") -> dict:
    """Tasks and ideas matching the filters, each task with `blocked_by` (open prerequisites) and `blocks`."""
    today = today or date.today()
    if sort not in {"status", "newest", "oldest", "priority", "lowest"}:
        raise ValueError("Unknown sort order.")
    if recommendation not in {"", "recommended", "other"}:
        raise ValueError("Unknown recommendation filter.")
    if stage and stage not in STAGES:
        raise ValueError("Unknown delivery stage.")
    for value in (created_from, created_to):
        if value:
            date.fromisoformat(value)
    if created_from and created_to and created_from > created_to:
        raise ValueError("Created from must be before created to.")
    def matches(item):
        created = (item.get("created") or "")[:10]
        if created_from and (not created or created < created_from):
            return False
        if created_to and (not created or created > created_to):
            return False
        haystack = f"#{item['id']} {item['title']} {item.get('description') or ''}".casefold()
        return search.strip().casefold() in haystack

    if due and due not in DUE_CHOICES:
        try:
            date.fromisoformat(due)
        except ValueError:
            raise ValueError(f"due must be one of {', '.join(DUE_CHOICES)} or YYYY-MM-DD, not {due!r}")
    rules = loop.load_constitution()
    priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))

    focus = brain.focus(con)
    recommended = {r["task_id"]: r.get("why", "Recommended by Atlas")
                   for r in focus.get("recommended", []) + focus.get("ai_queue", [])}
    goals = {g["id"]: g["title"] for g in con.execute("SELECT id, title FROM goals")}
    all_tasks = [dict(t) for t in con.execute("SELECT * FROM tasks")]
    state = {t["id"]: t["status"] for t in all_tasks}
    stage_of = stages(con, all_tasks)
    blocks: dict[int, list[int]] = {}
    for t in all_tasks:
        if t["status"] not in ("done", "dropped"):
            for d in _ids(t["depends_on"]):
                blocks.setdefault(d, []).append(t["id"])

    want_tasks = status & set(TASK_ORDER) if status else (OPEN_TASKS | ({"done"} if include_done else set()))
    if stage and not status:  # a stage picked in the delivery bar looks across every status
        want_tasks = set(TASK_ORDER)
    want_ideas = status & set(IDEA_ORDER) if status else (OPEN_IDEAS | ({"promoted"} if include_done else set()))
    in_goal = None if goal is None else ({None} if goal == 0 else _goal_ids(con, goal))

    tasks = []
    models = routing.chosen(con)  # the model the dev loop picked, shown on tasks it is building or has built
    if kind in ("all", "tasks"):
        for t in all_tasks:
            if t["status"] not in want_tasks or stage and stage_of[t["id"]] != stage:
                continue
            if in_goal is not None and t["goal_id"] not in in_goal:
                continue
            if due and not _due_ok(t["due"], due, today):
                continue
            if not matches(t):
                continue
            is_recommended = t["id"] in recommended and t["status"] == "todo"
            if recommendation == "recommended" and not is_recommended or recommendation == "other" and is_recommended:
                continue
            deps = _ids(t["depends_on"])
            model = models.get(t["id"], {}) if t["status"] in ("doing", "done") else {}
            tasks.append({
                "created": t["created"], "updated": t["updated"],
                "recommended": is_recommended,
                "recommendation_reason": recommended[t["id"]] if is_recommended else (
                    "In progress" if t["status"] == "doing" else "Waiting on prerequisites" if t["status"] == "blocked"
                    else "Completed" if t["status"] == "done" else "Not in Atlas’s current recommendations"),
                "id": t["id"], "title": t["title"], "status": t["status"], "owner": t["owner"],
                "stage": stage_of[t["id"]], "stage_label": STAGES[stage_of[t["id"]]],
                "phase": t["phase"], "due": t["due"], "score": t["score"], "effort_h": t["effort_h"],
                "goal_id": t["goal_id"], "goal": goals.get(t["goal_id"], ""),
                "overdue": bool(t["due"]) and t["due"] < today.isoformat() and t["status"] not in ("done", "dropped"),
                "depends_on": deps,
                "blocked_by": [d for d in deps if state.get(d) != "done"],
                "blocks": sorted(blocks.get(t["id"], [])),
                "model": model.get("model", ""), "model_reason": model.get("reason", ""),
            })
        tasks.sort(key=lambda t: (TASK_ORDER.index(t["status"]) if t["status"] in TASK_ORDER else 9,
                                  -(t["score"] or 0), t["due"] or "9999", t["id"]))

    ideas = []
    if kind in ("all", "ideas") and goal is None and not due and not stage:
        for i in con.execute("SELECT id, title, description, created, updated, category, gate, task_id, score, impact, effort_h, cost_eur FROM ideas"):
            if i["gate"] in want_ideas and matches(dict(i)) and recommendation != "recommended":
                ideas.append(dict(i))
        ideas.sort(key=lambda i: (IDEA_ORDER.index(i["gate"]) if i["gate"] in IDEA_ORDER else 9, -(i["score"] or 0), i["id"]))

    if sort in {"newest", "oldest"}:
        for collection in (tasks, ideas):
            known = [i for i in collection if i.get("created")]
            unknown = [i for i in collection if not i.get("created")]
            known.sort(key=lambda i: (i["created"], i["id"]), reverse=sort == "newest")
            collection[:] = known + unknown
    elif sort in {"priority", "lowest"}:
        for collection in (tasks, ideas):
            collection.sort(key=lambda i: (i["score"] or 0, -i["id"]), reverse=sort == "priority")

    counts: dict[str, int] = {}
    for t in all_tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    counts["ideas"] = con.execute("SELECT COUNT(*) FROM ideas WHERE gate = 'idea'").fetchone()[0]  # open ones
    delivery = {k: 0 for k in STAGES}
    for v in stage_of.values():
        delivery[v] += 1
    return {"tasks": tasks, "ideas": ideas, "counts": counts, "sort": sort, "stage": stage,
            "delivery": [{"key": k, "label": label, "count": delivery[k]} for k, label in STAGES.items()],
            "goals": [{"id": k, "title": v} for k, v in sorted(goals.items())]}


def text(data: dict) -> str:
    lines = []
    status = None
    for t in data["tasks"]:
        if t["status"] != status:
            status = t["status"]
            n = sum(1 for x in data["tasks"] if x["status"] == status)
            lines.append(f"\n{status.upper()} ({n})")
        extra = []
        if t["goal"]:
            extra.append(f"goal: {t['goal']}")
        if t["due"]:
            extra.append(f"due {t['due']}" + (" (overdue)" if t["overdue"] else ""))
        if t["blocked_by"]:
            extra.append("waits on " + ", ".join(f"#{d}" for d in t["blocked_by"]))
        if t["blocks"]:
            extra.append("blocks " + ", ".join(f"#{d}" for d in t["blocks"]))
        if t.get("model"):
            extra.append("model " + routing.line(t["model"], t["model_reason"]))
        lines.append(f"  #{t['id']:<4} [{t['owner']}] {t['title']}" + (f"  · {' · '.join(extra)}" if extra else ""))
    if data["ideas"]:
        lines.append(f"\nIDEAS ({len(data['ideas'])})")
        for i in data["ideas"]:
            state = f"→ task #{i['task_id']}" if i["gate"] == "promoted" else i["gate"]
            lines.append(f"  #{i['id']:<4} [{i['category']}/{state}] {i['title']}  · score {i['score']:.2f}")
    if not lines:
        return "Nothing matches these filters."
    c = data["counts"]
    summary = " · ".join(f"{c[s]} {s}" for s in TASK_ORDER if c.get(s)) + f" · {c['ideas']} ideas"
    return f"All tasks: {summary}" + "\n".join(lines)
