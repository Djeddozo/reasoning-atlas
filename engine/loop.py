"""The Atlas loop: observe → re-evaluate → re-rank → (think) → log → brief.

`run_cycle` is what a scheduler (cron / systemd timer) calls every hour.
It never spends, sends or installs anything; those go through approvals.
"""
import tomllib
from datetime import date, timedelta

from . import db, priority

CONSTITUTION = db.ROOT / "config" / "constitution.toml"
BRIEFS = db.ROOT / "briefs"


LOCAL = CONSTITUTION.with_name("local.toml")  # Pi-only additions (git-ignored), e.g. from enable-devloop.sh


def merge(base: dict, extra: dict) -> dict:
    """Overlay `extra` on `base`: tables merge, lists of rule tables are appended, values replace."""
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        elif isinstance(v, list) and isinstance(out.get(k), list) and all(isinstance(x, dict) for x in v):
            out[k] = out[k] + v
        else:
            out[k] = v
    return out


def load_constitution() -> dict:
    with open(CONSTITUTION, "rb") as f:
        rules = tomllib.load(f)
    if LOCAL.exists():  # settings that belong to this Pi only, so updates from GitHub never collide with them
        with open(LOCAL, "rb") as f:
            rules = merge(rules, tomllib.load(f))
    return rules


def seed(con, path=db.ROOT / "seeds" / "roadmap.toml") -> dict:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    keys: dict[str, int] = {}
    for g in data.get("goals", []):
        keys[g["key"]] = db.insert(con, "goals", title=g["title"], horizon=g.get("horizon", "quarter"), why=g.get("why", ""),
                                   parent_id=keys.get(g.get("parent")))
    pending_deps = []
    for t in data.get("tasks", []):
        fields = {k: v for k, v in t.items() if k not in ("key", "after", "goal")}
        if "goal" in t:
            fields["goal_id"] = keys[t["goal"]]
        keys[t["key"]] = db.insert(con, "tasks", **fields)
        if t.get("after"):
            pending_deps.append((keys[t["key"]], t["after"]))
    for tid, after in pending_deps:
        db.update(con, "tasks", tid, depends_on=",".join(str(keys[a]) for a in after))
    for i in data.get("ideas", []):
        db.insert(con, "ideas", **i)
    for m in data.get("memories", []):
        db.insert(con, "memories", source="seed", **m)
    db.log(con, "hq", "seed", f"{len(data.get('tasks', []))} tasks, {len(data.get('goals', []))} goals")
    con.commit()
    return {k: len(v) for k, v in data.items()}


def run_cycle(con, think: bool = False) -> dict:
    rules = load_constitution()
    phase = rules["identity"].get("current_phase", 0)
    n = priority.rescore(con, rules.get("priority"), current_phase=phase)

    today = date.today().isoformat()
    overdue = con.execute(
        "SELECT id, title FROM tasks WHERE due < ? AND status IN ('todo','doing')", (today,)
    ).fetchall()
    pending = con.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()[0]

    try:  # pick up Approve/Deny taps that arrived while nothing was waiting
        from . import gatekeeper
        import time as _t
        gatekeeper.poll_replies(con, rules.get("gatekeeper", {}), int(_t.time()) - 86400)
    except Exception:
        pass

    from . import doover
    applied = doover.apply_approved(con)  # do-overs Djeddi approved since the last cycle

    proposals = 0
    if think:
        from . import agent
        proposals = agent.think(con, rules)
        priority.rescore(con, rules.get("priority"), current_phase=phase)

    db.log(con, "hq", "cycle", f"rescored={n} overdue={len(overdue)} approvals={pending} proposals={proposals} doovers={applied}")
    con.commit()
    return {"rescored": n, "overdue": [dict(r) for r in overdue], "pending_approvals": pending, "proposals": proposals, "doovers_applied": applied}


def brief(con, write: bool = True) -> str:
    """Morning brief: what to do today, what Atlas is waiting on, what's coming."""
    rules = load_constitution()
    priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
    today = date.today()
    lines = [f"# Atlas brief — {today:%A %d %B %Y}", ""]

    top = priority.next_tasks(con, 3, owner="me")
    lines.append("## Do next")
    lines += [f"{i}. **{t['title']}** (#{t['id']}, ~{t['effort_h']:g}h{', due ' + t['due'] if t['due'] else ''})"
              for i, t in enumerate(top, 1)] or ["Nothing open — add tasks or goals."]
    if top:
        lines.append(f"_Why first: {priority.explain(con, top[0], rules.get('priority'), today)}_")

    ai = priority.next_tasks(con, 3, owner="ai")
    if ai:
        lines += ["", "## Atlas / Claude Code can build next"]
        lines += [f"- {t['title']} (#{t['id']})" for t in ai]

    from . import needs
    waiting = needs.items(con)
    if waiting:
        lines += ["", "## Needs your input (answer with `hq needs answer <key> ...` or on the dashboard)"]
        lines += [f"- {i['key']} {i['title']}" + (f" ({i['why']})" if i["why"] else "") for i in waiting]

    proposed = con.execute("SELECT * FROM tasks WHERE status = 'proposed' ORDER BY created DESC LIMIT 5").fetchall()
    if proposed:
        lines += ["", "## Proposed by Atlas (accept with `hq accept <id>`)"]
        lines += [f"- #{t['id']} {t['title']}" for t in proposed]

    week = (today + timedelta(days=7)).isoformat()
    soon = con.execute(
        "SELECT * FROM tasks WHERE due BETWEEN ? AND ? AND status NOT IN ('done','dropped') ORDER BY due",
        (today.isoformat(), week),
    ).fetchall()
    if soon:
        lines += ["", "## Due this week"]
        lines += [f"- {t['due']} · {t['title']} [{t['status']}]" for t in soon]

    ideas = con.execute("SELECT * FROM ideas WHERE gate = 'idea' ORDER BY score DESC LIMIT 3").fetchall()
    if ideas:
        lines += ["", "## Top ideas"]
        lines += [f"- #{i['id']} {i['title']} ({i['category']}; make it a task: hq promote {i['id']})" for i in ideas]

    done = con.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done'").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM tasks WHERE status != 'dropped'").fetchone()[0]
    lines += ["", f"_Progress: {done}/{total} tasks done · phase {rules['identity'].get('current_phase', 0)}_"]

    text = "\n".join(lines)
    if write:
        BRIEFS.mkdir(exist_ok=True)
        (BRIEFS / f"{today.isoformat()}.md").write_text(text)
    return text
