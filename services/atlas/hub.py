"""Atlas hub (RA-030): the Engine's functions, served to Atlas screens over Tailscale.

Runs on the Pi next to the Engine:

    python -m services.atlas.hub --host <tailscale-ip> --port 4320

- Every request needs `Authorization: Bearer <ATLAS_HUB_TOKEN>` (from the environment or .env).
  Without a token the hub refuses to start; it also refuses to listen on all interfaces.
- Reads (GET /v1/functions/<id>) open the Engine's database read-only at the SQLite level.
- Actions (POST /v1/actions/<id>) call exactly the Engine functions Atlas's own dashboard calls for the
  same button, so the Engine's logging, memory and planning cycle apply unchanged; they are logged
  with actor "me" and source "atlas".
- Only functions listed in FUNCTIONS and ACTIONS are callable; each is a registered Engine capability
  with `viaHub: true` in modules/engine.json (a test keeps the two in step).
"""
import argparse, hmac, json, os, sqlite3, sys, time
from contextlib import closing
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine.export import SchemaMismatch  # noqa: E402
VERSION = 1
DEFAULT_PORT = 4320


def engine_db():
    return Path(os.environ["HQ_DB"]) if os.environ.get("HQ_DB") else ROOT / "memory" / "hq.db"


def read_only(path):
    if not Path(path).exists():
        raise FileNotFoundError("The Engine's database was not found on this computer")
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _rows(rows):
    return [dict(r) for r in rows]


def _needs(con, params):
    """What only the owner can settle, plus 'already done?' suggestions from merged work (keys S<id>)."""
    from engine import needs
    out = needs.items(con)
    for r in con.execute("""SELECT c.id, c.task_id, c.pr_number, c.evidence, t.title, p.url FROM reconcile c
                            JOIN tasks t ON t.id = c.task_id LEFT JOIN prs p ON p.number = c.pr_number
                            WHERE c.status = 'open' AND t.status NOT IN ('done', 'dropped') ORDER BY c.id DESC LIMIT 10"""):
        out.append({"key": f"S{r['id']}", "kind": "already_done", "id": r["id"], "title": f"Already done? #{r['task_id']} {r['title']}",
                    "why": f"PR #{r['pr_number']} mentions it" + (f": {r['evidence'][:200]}" if r["evidence"] else "."),
                    "options": [], "holds": [], "url": r["url"] or ""})
    return out


def _terminal_steps(con, params):
    from engine import commands
    return commands.items(con)


def _interrupted(con, params):
    from engine import resume
    return resume.items(con)


def _next_steps(con, params):
    from engine import priority
    n = max(1, min(int(params.get("n", ["10"])[0]), 50))
    from engine import compass
    goals = {r[0]: r[1] for r in con.execute("SELECT id, title FROM goals")}
    focus = compass.focused_goals(con)
    out = []
    for t in priority.next_tasks(con, n=n):
        row = dict(t)
        row["goal"] = goals.get(row.get("goal_id"), "")
        row["inFocus"] = row.get("goal_id") in focus
        row["why"] = priority.explain(con, t).split(" is on top: ", 1)[-1]  # the reasons, without the title
        out.append(row)
    return out


def _explain_rank(con, params):
    """The build explanation of one task (priority.explain_full); missing reasoning comes back marked missing."""
    from engine import priority
    row = con.execute("SELECT * FROM tasks WHERE id = ?", (int(params["id"][0]),)).fetchone()
    if row is None:
        raise ValueError("Task not found.")
    return priority.explain_full(con, row)


def _focus(con, params):
    from engine import priority, skilltree
    row = con.execute("SELECT value FROM state WHERE key = 'build_focus'").fetchone()
    focus = row[0] if row and row[0] in skilltree.FOCUS else "balanced"
    nxt = _rows(priority.next_tasks(con, n=1))
    goal = None
    if nxt and nxt[0].get("goal_id"):
        goal = con.execute("SELECT id, title, horizon FROM goals WHERE id = ?", (nxt[0]["goal_id"],)).fetchone()
    if goal is None:  # else the active goal with the most open work
        goal = con.execute("""SELECT g.id, g.title, g.horizon FROM goals g LEFT JOIN tasks t ON t.goal_id = g.id
                              AND t.status IN ('todo', 'doing') WHERE g.status = 'active'
                              GROUP BY g.id ORDER BY COUNT(t.id) DESC, g.id LIMIT 1""").fetchone()
    return {"focus": focus, "text": skilltree.FOCUS[focus], "options": skilltree.FOCUS,
            "next": nxt[0] if nxt else None, "destination": dict(goal) if goal else None}


def _on_copy(con, fn):
    """Run an Engine read that also tidies up as it goes (plants skill-tree nodes, fills rule placements)
    on an in-memory copy, so a read through the hub can never change the Engine's data."""
    with closing(sqlite3.connect(":memory:")) as copy:
        con.backup(copy)
        copy.row_factory = sqlite3.Row
        return fn(copy)


def _zoom_out(con, params):
    from engine import direction
    r = _on_copy(con, direction.rollup)  # rule-based, no model calls
    return {"insights": r["insights"], "goals": r["goals"], "nodes": r["nodes"][:12]}


def _usage(con, params):
    from engine import usage
    return usage.summary(con)


def _recall(con, params):
    from engine import db
    query = params.get("q", [""])[0][:200]
    return [r for r in _rows(db.recall(con, query, limit=12)) if r["kind"] != "forgotten"][:10] if query.strip() else []


def _recent_thoughts(con, params):
    from engine import brain
    n = max(1, min(int(params.get("n", ["10"])[0]), 50))
    out = brain.thoughts(con, n)
    for t in out:  # what each thought became, with the title of every item, so the screen can say where it went
        t["changes"] = []
        for c in con.execute("""SELECT id, action, table_name, row_id, undone FROM brain_changes
                                WHERE thought_id = ? ORDER BY id""", (t["id"],)):
            row = con.execute(f"SELECT title FROM {c['table_name']} WHERE id = ?", (c["row_id"],)).fetchone() \
                if c["table_name"] in ("tasks", "ideas", "goals") else None
            t["changes"].append({**dict(c), "title": row[0] if row else ""})
    return out


def _ideas(con, params):
    from engine import dashboard, fresh

    def read(c):
        goals = [dict(g) for g in c.execute("SELECT id, title, horizon FROM goals WHERE status = 'active' ORDER BY id")]
        from engine import explore
        ideas = [i | {"explored": explore.get(c, i["id"])} for i in dashboard._ideas(c) if i["gate"] == "idea"]  # with where each fits
        return {"ideas": fresh.mark(ideas, fresh.cutoff(c)), "goals": goals}
    return _on_copy(con, read)


UPDATE_STATE = ROOT / "runtime" / "hub-update.json"
UPDATE_STALE = 30 * 60  # an update run older than this that never reported back is treated as finished


def _read_update_state():
    try:
        return json.loads(UPDATE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_update_state(value):
    UPDATE_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = UPDATE_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    os.replace(tmp, UPDATE_STATE)


def _update_running():
    state = _read_update_state()
    return state.get("state") == "running" and time.time() - state.get("startedAt", 0) < UPDATE_STALE


def _update_status(con, params):
    from engine import updater
    return {"current": updater.version(), "behind": updater.local_status()["behind"],
            "running": _update_running(), "last": _read_update_state()}


def _directions(con, params):
    from engine import compass
    return compass.directions(con)


def _run_status(con, params):
    """Forge › Build and Build progress: the builder's state, queue, lanes, pull requests and delivery.
    These Engine reads tidy up as they go (close crashed runs, plant skill-tree nodes), so they run on a copy."""
    from engine import delivery, devloop
    if params.get("run"):  # one run's live console, only the lines after `after`
        return devloop.live(con, int(params["run"][0]), int(params.get("after", ["0"])[0] or 0))

    def read(c):
        o = devloop.overview(c)
        o["delivery"] = delivery.today(c)
        o["runs"] = [{k: r.get(k) for k in ("id", "task_id", "title", "status", "started", "seconds", "pr_url", "summary", "model",
                                            "worker", "device", "slot")} for r in o.get("runs", [])]
        return o
    return _on_copy(con, read)


def _plan_tree(con, params):
    from engine import fresh, plan

    def read(c):  # tree() re-scores first, so it runs on a copy
        t, since = plan.tree(c), fresh.cutoff(c)

        def walk(g):
            fresh.mark([g], since)
            fresh.mark(g["tasks"], since)
            for child in g["children"]:
                walk(child)
        for g in t["goals"]:
            walk(g)
        fresh.mark(t["loose"], since)
        t["fresh_minutes"] = fresh.window(c)
        return t
    return _on_copy(con, read)


def _overview(con, params):
    from engine import dashboard
    from urllib.parse import urlencode
    query = urlencode({k: v[-1] for k, v in params.items()})
    return _on_copy(con, lambda c: dashboard.overview_query(c, query))


def _task_detail(con, params):
    from engine import taskdetail
    return _on_copy(con, lambda c: taskdetail.detail(c, int(params["id"][0])))


def _progress(con, params):
    """Today's glance: the Engine's progress figures, your next three steps, open proposals, the last weekly review."""
    from engine import fresh, priority, progress

    def read(c):
        s = progress.stats(c)
        mine = [dict(t) for t in priority.next_tasks(c, 3, owner="me")]
        drafts = c.execute("SELECT COUNT(*) FROM tasks WHERE status = 'proposed'").fetchone()[0]
        return {**{k: s[k] for k in ("today", "done_today", "done_week", "done_last_week", "added_week", "streak", "best_streak",
                                     "open", "overdue", "due_soon", "stalled", "daily", "goals")},
                "mine": fresh.mark([{k: t[k] for k in ("id", "title", "due", "effort_h", "status", "created")} for t in mine],
                                   fresh.cutoff(c)),
                "just_added": fresh.recent(c),
                "proposals": drafts, "review": progress.last_review(c),
                "why_first": priority.why_first(c, "me")}
    return _on_copy(con, read)


def _do_over_changes(con, params):
    from engine import doover
    return doover.changes(con, int(params["id"][0]))


def _chat(con, params):
    from engine import chat
    n = max(1, min(200, int((params.get("n") or ["40"])[0])))
    return chat.history(con, n, earlier=bool(params.get("earlier")))


def _agents(con, params):
    from engine import agents
    if params.get("handover") is not None:
        task = (params.get("handover") or [""])[0]
        return {"prompt": agents.handover(con, int(task) if task else None)}
    return agents.status(con)


def _conversations(con, params):
    from engine import chat
    return chat.conversations(con)


def _sandbox_runs(con, params):
    """Outside code tested in the sandbox, newest first; ?task=N: the runs that are evidence for that task."""
    from engine import sandbox
    task = int(params["task"][0]) if params.get("task") else None
    out = []
    for r in sandbox.runs(con, task, limit=20):
        r["flag_summary"] = sandbox.summarize_flags(r["flags"]) if r["source"] != "selfcheck" else ""
        r["flags"] = r["flags"][:30]
        out.append(r)
    return {"runs": out, "evidence": sandbox.evidence(con, task) if task else "",
            "gate": sandbox.gate_report(con, task) if task else None}  # source, trust, licence, scan, run (#75)


def _skill_tree(con, params):
    """The Engine's skill tree as a lens on Atlas's map: capabilities by branch, state, proof and build order.
    With ?node=N: that node in full, with its open tasks and the latest research on how to build it."""
    from engine import research, skilltree

    def read(c):
        t = skilltree.tree(c)
        keep = ("id", "title", "branch", "state", "done", "total", "proof", "proof_text", "requires", "description",
                "evidence", "suggested", "depth", "status")
        nodes = [{k: n.get(k) for k in keep} | {"open": sum(1 for x in n["tasks"] if x["status"] not in ("done", "dropped"))}
                 for n in t["nodes"]]
        out = {"branches": t["branches"], "nodes": nodes, "counts": t["counts"]}
        if params.get("node"):
            cid = int(params["node"][0])
            node = next((n for n in t["nodes"] if n["id"] == cid), None)
            if node is None:
                raise ValueError(f"no node #{cid}")
            out["node"] = {**{k: node.get(k) for k in keep}, "tasks": node["tasks"][:12], "ideas": node.get("ideas", [])[:8],
                           "research": research.latest(c, cid)}
        return out
    return _on_copy(con, read)


def _research_feed(con, params):
    from engine import radar
    def read(c):
        out = radar.curated(c)
        keep = ("id", "source", "label", "kind", "title", "url", "summary", "state", "found", "topics", "why", "impact", "matched", "prereq", "radar_id")
        out["feed"] = [{k: i.get(k) for k in keep} for i in out["feed"]]
        for i in out["feed"]:
            i["summary"] = (i["summary"] or "")[:400]
        return out
    return _on_copy(con, read)


def _research_note(con, params):
    from engine import deepresearch
    if params.get("id"):
        return deepresearch.note(con, int(params["id"][0]))
    return deepresearch.notes(con)


def _resources(con, params):
    """Resources; each Connected machine carries its builder role (#193: one device, one card)."""
    from engine import resources, workers
    out = resources.listing(con, retired=params.get("retired", ["0"])[0] == "1")
    by_id = {d["resource_id"]: d for d in workers.roles(con) if d["resource_id"]}
    for c in out["connected"]:
        c["builder"] = by_id.get(c["id"])
    return out


def _arsenal(con, params):
    from engine import arsenal
    q = (params.get("q") or [""])[0][:200]
    projects = [dict(r) for r in con.execute("""SELECT p.id, p.name, p.description, p.scanned, COUNT(a.id) AS assets
                                                FROM projects p LEFT JOIN assets a ON a.project_id = p.id GROUP BY p.id ORDER BY p.name""")]
    for p in projects:
        p["description"] = (p["description"] or "")[:300]
    hits = [{"project": r["project"], "name": r["name"], "kind": r["kind"], "signature": r["signature"], "doc": (r["doc"] or "")[:200],
             "path": r["path"], "line": r["line"]} for r in arsenal.find(con, q, 25)] if q.strip() else []
    return {"projects": projects, "hits": hits, "query": q}


MEMORY_KINDS = ("lesson", "decision", "preference", "fact", "info")


def _memories(con, params):
    """The Engine's memory: counts per kind and the newest entries, by kind and/or words."""
    import re
    kind = (params.get("kind") or [""])[0]
    words = re.findall(r"[A-Za-z0-9]{2,}", (params.get("q") or [""])[0])[:8]
    where, args = [], []
    if kind in MEMORY_KINDS:
        where.append("m.kind = ?"); args.append(kind)
    elif kind != "all":  # by default what was learned and decided; research notices are under Info
        where.append("NOT (m.kind = 'info' AND m.source = 'radar') AND m.kind != 'forgotten'")
    else:
        where.append("m.kind != 'forgotten'")  # an undone memory is kept, never shown
    sql = "SELECT m.* FROM memories m"
    if words:
        sql = "SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid"
        where.insert(0, "memories_fts MATCH ?"); args.insert(0, " OR ".join(f'"{w}"' for w in words))
    sql += (" WHERE " + " AND ".join(where) if where else "") + (" ORDER BY rank" if words else " ORDER BY m.id DESC") + " LIMIT 80"
    counts = dict(con.execute("SELECT kind, COUNT(*) FROM memories WHERE kind != 'forgotten' GROUP BY kind").fetchall())
    return {"kinds": list(MEMORY_KINDS), "counts": counts, "items": _rows(con.execute(sql, args).fetchall())}


def _change_log(con, params):
    from engine import brain
    n = max(1, min(int((params.get("n") or ["60"])[0]), 200))
    out = []
    for c in brain.changes(con, n):
        out.append({"id": c["id"], "ts": c["ts"], "action": c["action"], "table": c["table_name"], "row": c["row_id"],
                    "text": brain.describe_change(c), "reason": c["reason"], "undone": bool(c["undone"]), "thought": c["thought_id"]})
    return out


UPKEEP = (  # the Engine's self-maintenance, as it shows on Systems: label, when it runs, log actions (done, failed)
    ("Plan re-evaluation", "after real change, at most every 3 h", ("brain_reevaluate",), ()),
    ("Whole-plan do-over", "when the plan drifts, or on request", ("doover_propose", "doover_apply"), ("doover_failed",)),
    ("Placement review", "weekly", ("placement_review",), ("placement_failed",)),
    ("Feature revisit", "monthly", ("revisit",), ("revisit_failed",)),
    ("Tool and model recheck", "weekly", ("revalidate",), ("revalidate_failed",)),
    ("Overlap and worth check", "daily, and before each build", ("overlap_check", "overlap_settled"), ("overlap_model_failed",)),
    ("Skill tree linking", "daily", ("autolink",), ()),
    ("Memory review", "daily", ("think",), ()),
    ("Research scan", "every few hours", ("radar_scan",), ()),
    ("Arsenal index", "on request", ("arsenal_scan",), ()),
    ("Updates", "every 15 minutes", ("update",), ("update_tests_failed",)),
    ("Private backup", "daily, encrypted, to your private repository", ("backup", "backup_local"), ("backup_failed",)),
)


def _system(con, params):
    """Systems: kill switch, budgets, model routing, repairs, self-maintenance, voice and Claude sign-in, the constitution."""
    from engine import budget, claudeauth, loop, recovery, usage, voicesetup
    conf = loop.load_constitution()
    upkeep = []
    for label, when, done, failed in UPKEEP:
        acts = done + failed
        r = con.execute(f"SELECT ts, action, detail FROM log WHERE action IN ({','.join('?' * len(acts))}) ORDER BY id DESC LIMIT 1",
                        acts).fetchone()
        upkeep.append({"label": label, "when": when, "last": r["ts"] if r else None, "failed": bool(r and r["action"] in failed),
                       "detail": (r["detail"] or "")[:160] if r else ""})
    voice = voicesetup.status(con)
    for t in voice["tiers"].values():  # never a key, only whether one is set
        t.pop("key", None)
    try:
        text = loop.CONSTITUTION.read_text()[:40000]
    except OSError:
        text = ""
    rec = recovery.overview(con)
    return {"killed": usage.killed(), "budgets": budget.status(con),
            "routing": conf.get("models", {}).get("routing", {}), "phase": conf.get("current_phase"),
            "repairs": {"enabled": rec["enabled"], "status": rec["status"], "checked": rec["checked"],
                        "incidents": [{k: i.get(k) for k in ("id", "source", "error_type", "status", "count", "last_seen")} for i in rec["incidents"][:8]]},
            "upkeep": upkeep, "voice": voice, "claude": claudeauth.login.status() | {"url": "", "session": ""},
            "constitution": text}


def _snapshot_export(con, params):
    """The plan as one Snapshot document for Atlas's read model (RA-009). Works on a copy; never rescored."""
    from engine import export
    return export.snapshot(con, commit=git_version()[1] or "")


def _cortex(con, params):
    from engine import chat, db
    return {"name": chat.name(con), "default": chat.DEFAULT_NAME, "named": bool(db.get_state(con, "cortex_named", ""))}


def _parallel(con, params):
    """Forge › Build › Queue: AI tasks that can be built side by side (#72); worked out on a copy, as the queue is."""
    from engine import parallel
    return _on_copy(con, parallel.view)


def _parallel_batch(con, body):
    """Accept the proposed batch of independent tasks, or dismiss that set (both undoable)."""
    from engine import parallel
    ids = lambda k: [int(i) for i in body[k]] if isinstance(body.get(k), list) else None
    if "accept" in body:
        return parallel.accept(con, ids("accept"))
    if "dismiss" in body:
        return parallel.dismiss(con, ids("dismiss") or [])
    raise ValueError("Say accept or dismiss, with the batch's task ids")


def _mockups(con, params):
    from engine import mockup
    if params.get("image"):  # one picture: ?image=<mockup>&round=<n>
        import base64
        data, kind = mockup.image(con, int(params["image"][0]), int((params.get("round") or ["1"])[0]))
        return {"type": kind, "data": base64.b64encode(data).decode()}
    return mockup.view(con)


# Capability id → read-only implementation. Keep in step with `viaHub` in modules/engine.json.
FUNCTIONS = {
    "engine.attention.needs": _needs,
    "engine.attention.terminal_steps": _terminal_steps,
    "engine.attention.interrupted_work": _interrupted,
    "engine.direction.ranked_next_steps": _next_steps,
    "engine.direction.explain_rank": _explain_rank,
    "engine.direction.current_focus": _focus,
    "engine.direction.zoom_out": _zoom_out,
    "engine.system.usage": _usage,
    "engine.memory.recall": _recall,
    "engine.brain.recent_thoughts": _recent_thoughts,
    "engine.build.ideas": _ideas,
    "engine.system.update_status": _update_status,
    "engine.direction.directions": _directions,
    "engine.build.run_status": _run_status,
    "engine.build.plan_tree": _plan_tree,
    "engine.build.overview": _overview,
    "engine.build.task_detail": _task_detail,
    "engine.direction.progress": _progress,
    "engine.build.do_over_changes": _do_over_changes,
    "engine.brain.conversation": _chat,
    "engine.brain.conversations": _conversations,
    "engine.assets.agents": _agents,
    "engine.growth.skill_tree": _skill_tree,
    "engine.growth.sandbox_runs": _sandbox_runs,
    "engine.research.research_feed": _research_feed,
    "engine.research.sourced_summary": _research_note,
    "engine.assets.resources": _resources,
    "engine.assets.arsenal_find": _arsenal,
    "engine.memory.memories": _memories,
    "engine.memory.change_log": _change_log,
    "engine.system.health": _system,
    "engine.system.snapshot_export": _snapshot_export,
    "engine.brain.cortex": _cortex,
    "engine.brain.mockups": _mockups,
    "engine.build.parallel": _parallel,
}


def _answer_need(con, body):
    from engine import delivery, loop, needs
    key = str(body.get("key", ""))
    if key[:1] == "S" and key[1:].isdigit():  # an 'already done?' suggestion: done | partial | not_related
        answer = str(body.get("text", ""))
        if answer not in ("done", "partial", "not_related"):
            raise ValueError("Answer done, partial or not_related")
        return delivery.decide(con, int(key[1:]), answer)
    message = needs.answer_key(con, str(body.get("key", "")), str(body.get("text", "")), str(body.get("verdict", "")), "atlas")
    loop.run_cycle(con)  # as the dashboard does after an answer: the plan reacts at once
    return message


def _finish_terminal_step(con, body):
    from engine import commands
    return commands.finish(con, int(body["id"]), "done" if body.get("status") == "done" else "dismissed")


def _finish_interrupted(con, body):
    from engine import resume
    return resume.finish(con, int(body["id"]), "resolved" if body.get("status") == "resolved" else "dismissed",
                         "marked finished in Atlas" if body.get("status") == "resolved" else "not needed (Atlas)")


def _write_thought(con, body):
    text = str(body.get("text", "")).strip()
    if not text or len(text) > 8000:
        raise ValueError("Write a thought of 1–8000 characters")
    return _file(con, text)


def _file(con, text):
    from engine import dump, intake
    urls, rest = intake.find_urls(text)
    rest = rest if urls else text  # keep line breaks (todo:/idea: lines) when there is no link
    said = [intake.describe(intake.add_link(con, u, note=rest[:300])) for u in urls[:5]]  # a link is read and saved to memory
    if rest:
        said.append(dump.process(con, rest, source="atlas"))  # raw text saved first; sorted by Claude or kept 'unsorted'
    return "\n".join(said) or "Nothing to add."


def _talk(con, body):
    """One way in (RA-058): a question is answered from the plan, anything else is sorted into it.
    With "full": true (the Cortex chat) the answer also carries numbered proposals over every owner action,
    choices to pick from and a mockup offer for an idea with a visual side (RA-077)."""
    from engine import talk
    if not body.get("full"):
        return talk.say(con, str(body.get("text", "")), "atlas", str(body.get("mode") or "auto"))["reply"]
    views = [str(v)[:40] for v in body.get("views", [])][:40] if isinstance(body.get("views"), list) else []
    out = talk.say(con, str(body.get("text", "")), "atlas", str(body.get("mode") or "auto"), action_catalog(), views)
    return {"mode": out["mode"], "reply": out["reply"], "proposals": out.get("proposals", []),
            "choices": out.get("choices", []), "mockup": out.get("mockup")}


# Owner actions the Cortex chat may not propose: sign-ins and codes, installing code or outside packages, uploads,
# the machine's own reports, and the chat's own plumbing. Everything else it may propose; he confirms each one.
NOT_PROPOSABLE = {"engine.build.claude_login", "engine.system.updates", "engine.system.voice_setup", "engine.brain.ingest",
                  "engine.brain.voice_input", "engine.brain.undo_changes", "engine.brain.speak_to", "engine.brain.talk",
                  "engine.assets.report_hardware", "engine.brain.new_conversation",
                  "engine.brain.chat_stream", "engine.brain.chat_file", "engine.brain.cortex_apply", "engine.brain.cortex_autonomy"}


def action_catalog() -> dict:
    """Every owner action the Cortex chat may propose, described from the hub's own code: the registry summary,
    the body keys the action reads, and a hint where the body is nested."""
    import inspect, json as _json, re
    from engine import screen
    try:
        reg = {c["id"]: c.get("summary", "") for c in _json.loads((ROOT / "modules" / "engine.json").read_text())["capabilities"]}
    except (OSError, ValueError, KeyError):
        reg = {}
    out = {}
    for aid, fn in ACTIONS.items():
        if aid in NOT_PROPOSABLE:
            continue
        if aid in screen.HINTS:
            out[aid] = screen.HINTS[aid]
            continue
        try:
            keys = sorted(set(re.findall(r"body(?:\.get\(|\[)[\"'](\w+)[\"']", inspect.getsource(fn))))
        except (OSError, TypeError):
            keys = []
        doc = " ".join((reg.get(aid) or inspect.getdoc(fn) or "").split())[:180]
        out[aid] = doc + (f" Body keys: {', '.join(keys)}." if keys else " Body: {}.")
    return out


def _voice_input(con, body):
    """Quick note 🎙 through the Whisper tiers (task #143): the recording so far while talking (`partial`), then the
    whole recording at Stop. Returns the transcript for editing; nothing is filed until it is sent."""
    import base64
    from engine import models, talk
    audio = base64.b64decode(str(body.get("data", "")), validate=True)
    try:
        return talk.transcribe(con, audio, str(body.get("type", ""))[:80], None, bool(body.get("partial")), "atlas")
    except models.ModelUnavailable as exc:
        con.commit()  # the kept recording and its note survive the refusal
        raise ValueError("Recording saved; it is transcribed and filed when speech-to-text is back." if not body.get("partial")
                         else f"Speech-to-text is unavailable now ({exc})") from exc


def _undo_thought(con, body):
    from engine import brain
    if isinstance(body.get("changes"), list):  # the Undo after an action: exactly its change-log entries, newest first
        ids = sorted({int(x) for x in body["changes"]}, reverse=True)
        if not ids:
            raise ValueError("Nothing to undo.")
        for cid in ids:
            brain.undo(con, cid)
        return "Undone."
    tid = int(body["thought"])
    ids = [r[0] for r in con.execute("SELECT id FROM brain_changes WHERE thought_id = ? AND undone = 0 ORDER BY id DESC", (tid,))]
    if not ids:
        raise ValueError("Nothing left to undo for this thought")
    for cid in ids:  # newest first, as the dashboard's Undo does
        brain.undo(con, cid)
    return f"Undone: {len(ids)} change(s) from thought #{tid}. The thought itself is kept."


def _set_focus(con, body):
    from engine import priority, skilltree
    text = skilltree.set_focus(con, str(body.get("focus", "")))
    priority.rerank(con, "build focus", str(body.get("focus")))  # the order follows at once, as after any change
    return f"Focus: {text}"


def _dev_controls(con, body):
    from engine import devloop
    if "pause" in body:
        devloop.set_paused(con, bool(body["pause"]))
        return "The builder is paused: no new runs start." if body["pause"] else "The builder may start runs again (within its limits)."
    if body.get("stop"):
        return devloop.stop(con)
    if "autonomy" in body:
        return devloop.set_autonomy(con, str(body["autonomy"]))
    if isinstance(body.get("limit"), dict):
        return devloop.set_limit(con, str(body["limit"].get("kind", "")), body["limit"].get("value", 0))
    if "batch" in body:
        return devloop.set_batch(con, body["batch"])
    if "auto_merge" in body:  # the owner lets the builder merge its own pull requests for RA steps
        from engine import atlasplan
        return atlasplan.set_auto_merge(con, bool(body["auto_merge"]))
    if "override_today" in body:
        return devloop.set_override(con, bool(body["override_today"]))
    if "slots" in body:  # builds at once on a device (#185, #193); further slots only take an accepted Side by side batch
        from engine import workers
        return workers.set_slots(con, body["slots"], str(body.get("worker") or workers.PI))
    raise ValueError("Unknown builder control")


def _dev_run(con, body):
    from engine import devloop
    task = body.get("task")
    task = int(task) if task not in (None, "") else None
    reason = devloop.why_not_now(con, task)
    if reason:
        raise ValueError(reason)
    return devloop.start_background(task_id=task)


def _pr_conflicts(con, body):
    from engine import devloop
    if body.get("scan"):
        return devloop.scan(con)
    run = int(body["resolve"])
    reason = devloop.why_not_resolve(con, run)
    if reason:
        raise ValueError(reason)
    return devloop.start_resolve(run)


def _build_queue(con, body):
    from engine import brain, priority, skilltree
    if body.get("top"):
        skilltree.queue(con, int(body["top"]), mine=True, top=True)
        return "Moved to the front of your queue."
    if body.get("draft"):
        return skilltree.queue_draft(con, int(body["draft"]), body.get("fields") if isinstance(body.get("fields"), dict) else {})
    if isinstance(body.get("owner"), dict):
        task, to = int(body["owner"]["task"]), str(body["owner"].get("to"))
        if to not in ("me", "ai"):
            raise ValueError("owner must be me or ai")
        brain.set_field(con, "tasks", task, "owner", to, "queued for the agent by Djeddi (Atlas)" if to == "ai" else "taken back by Djeddi (Atlas)")
        skilltree.queue(con, task, mine=to == "ai")
        priority.rerank(con, "queue changed", f"#{task}")
        return f"Task #{task} queued for the builder." if to == "ai" else f"Task #{task} is yours again."
    raise ValueError("Unknown queue change")


def _build_order(con, body):
    """Djeddi drags the builder's queue into his own order."""
    from engine import skilltree
    ids = body.get("order")
    if not isinstance(ids, list) or not ids or len(ids) > 200:
        raise ValueError("order must be a list of task ids")
    order = skilltree.set_order(con, ids)
    return f"The builder takes them in your order: " + ", ".join(f"#{i}" for i in order[:5]) + ("…" if len(order) > 5 else ".")


def _quick_build(con, body):
    from engine import quickbuild
    return quickbuild.start(con, str(body.get("prompt", "")), str(body.get("request_id", "")))["message"]


def _task_status(con, body):
    """Done (own tasks only; the builder's tasks close on evidence), doing, to do, delete (set aside, undoable),
    or hand to the builder."""
    from engine import db, devloop, flow, priority
    task, to = int(body["task"]), str(body.get("status", ""))
    if to == "done":
        return flow.confirm_done(con, task, "marked done in Atlas")
    if to == "dropped":
        message = flow.discard(con, task)
    elif to == "builder":  # Build in the plan: into the builder's queue as the last of the five shown (owner, 2026-10-25)
        message = flow.queue_for_build(con, task)
    elif to in ("important", "normal"):  # what Djeddi marks himself always comes first (owner, 2026-10-20)
        from engine import brain, skilltree
        row = con.execute("SELECT owner, status FROM tasks WHERE id = ?", (task,)).fetchone()
        if row is None or row["status"] in ("done", "dropped"):
            raise ValueError(f"task #{task} is not open")
        if to == "important":
            top = con.execute("SELECT COALESCE(MAX(score), 0) FROM tasks WHERE status IN ('todo', 'doing')").fetchone()[0]
            brain.set_field(con, "tasks", task, "impact", 5, "marked important by Djeddi (Atlas)")
            brain.set_field(con, "tasks", task, "pinned_score", round(float(top) + 1, 3), "marked important by Djeddi (Atlas)")
            if row["owner"] in ("ai", "both"):
                skilltree.queue(con, task, mine=True, top=True)  # the builder takes it first
            message = f"Task #{task} marked important: it ranks first" + (" and the builder takes it next." if row["owner"] in ("ai", "both") else ".")
        else:
            brain.set_field(con, "tasks", task, "pinned_score", None, "no longer important (Atlas)")
            brain.set_field(con, "tasks", task, "impact", 3, "no longer important (Atlas)")
            message = f"Task #{task} ranks on its own merits again."
    elif to in ("doing", "todo"):
        from engine import brain
        if not brain.set_field(con, "tasks", task, "status", to, f"set to {to} by Djeddi (Atlas)"):
            return "Nothing changed."
        message = f"Task #{task}: {to}."
    else:
        raise ValueError("status is done, doing, todo, dropped, builder, important or normal")
    priority.rerank(con, "task status", f"#{task} {to}")
    return message


def _reorder(con, body):
    from engine import edit, priority
    message = edit.move(con, int(body["task"]), str(body.get("direction", "")))
    priority.rerank(con, "task moved", f"#{body['task']}")
    return message


def _do_over(con, body):
    from engine import doover
    if body.get("undo"):
        return doover.undo(con, int(body["undo"]))
    return doover.replan_text(doover.replan(con, force=True))  # the owner asked: always a full top-down re-evaluation; approval in Today


def _revisit(con, body):
    from engine import revisit
    if body.get("placement"):  # review where Atlas's functions live now (RA-059): proposals become draft tasks
        return revisit.placement_text(revisit.placement(con))
    return revisit.text(revisit.run(con, force=True))


def _weekly_review(con, body):
    from engine import progress
    return progress.review(con)  # figures first; Claude's short observation only when a model is reachable


def _choose_direction(con, body):
    from engine import compass, priority
    goal = body.get("goal")
    message = compass.set_focus(con, int(goal) if goal not in (None, "") else None)
    priority.rerank(con, "focus", str(goal or "none"))  # the Forge queue follows at once
    return message


def _shape_directions(con, body):
    from engine import compass
    if isinstance(body.get("create"), dict):
        c = body["create"]
        gid = compass.create(con, str(c.get("title", "")), str(c.get("why", "")))
        return f"Vision #{gid} created. Give it a goal, or choose it as your focus."
    if isinstance(body.get("objective"), dict):  # a goal under a vision (Vision → Goal → Task, owner 2026-10-25)
        o = body["objective"]
        gid = compass.add_objective(con, int(o["vision"]), str(o.get("title", "")), str(o.get("why", "")), o.get("due") or None)
        return f"Goal #{gid} added" + (f", deadline {o['due']}" if o.get("due") else "") + ". Its tasks live in Forge › Plan."
    if isinstance(body.get("due"), dict):
        return compass.set_due(con, int(body["due"]["goal"]), body["due"].get("date") or None)
    if isinstance(body.get("rename"), dict):
        from engine import brain
        title = " ".join(str(body["rename"].get("title", "")).split())[:120]
        if not title:
            raise ValueError("A goal needs a title")
        brain.set_field(con, "goals", int(body["rename"]["goal"]), "title", title, "renamed by Djeddi (Focus)")
        return "Renamed."
    if body.get("drop"):
        from engine import brain
        gid = int(body["drop"])
        if con.execute("SELECT 1 FROM goals WHERE id = ? AND horizon = 'life' AND parent_id IS NULL", (gid,)).fetchone():
            raise ValueError("A vision is dropped by choosing no focus and leaving it; set it aside under Forge › Plan if you must.")
        brain.set_field(con, "goals", gid, "status", "dropped", "dropped by Djeddi (Focus)")
        return "Goal set aside (undo under Memory › What changed)."
    vision = body.get("vision", body.get("direction"))
    return compass.place(con, int(body["goal"]), int(vision) if vision not in (None, "") else None)


MAX_FILE = 10 * 1024 * 1024
UPLOADS = ("engine.brain.ingest", "engine.brain.voice_input", "engine.brain.mockup", "engine.brain.chat_file")  # base64 bodies up to MAX_FILE


def _ingest(con, body):
    """A screenshot, photo or PDF from Atlas's Quick note: kept in memory/intake and read like a Telegram upload."""
    import base64, re as _re
    from engine import intake
    name = _re.sub(r"[^A-Za-z0-9._-]+", "-", str(body.get("name", "file")))[-80:].strip("-.") or "file"
    data = base64.b64decode(str(body.get("data", "")), validate=True)
    if not data or len(data) > MAX_FILE:
        raise ValueError("Send a file of up to 10 MB")
    intake.FILES_DIR.mkdir(parents=True, exist_ok=True)
    path = intake.FILES_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
    path.write_bytes(data)
    return intake.describe(intake.add_file(con, path, str(body.get("note", ""))[:500]))


def _confirm_proof(con, body):
    from engine import skilltree
    cid, ref = int(body["node"]), str(body.get("ref", ""))[:80]
    return skilltree.dismiss(con, cid, ref) if body.get("dismiss") else skilltree.confirm(con, cid, ref)


def _node_research(con, body):
    from engine import research
    if body.get("pick"):  # an option of a finished research becomes a draft task on the node
        tid = research.pick(con, int(body["pick"]), int(body.get("option", 0)))
        return f"Draft task #{tid} on the node: accept it under Proposals in Forge › Plan."
    rid = research.research(con, int(body["node"]))
    r = research.load(con, rid)
    return r["summary"] or f"Research #{rid}: {len(r['options'])} way(s) to build it."


def _draft_node_task(con, body):
    from engine import skilltree
    tid = skilltree.draft_task(con, int(body["node"]))
    return f"Draft task #{tid} builds this node: accept it under Proposals in Forge › Plan."


def _mark_finding(con, body):
    from engine import radar
    state = str(body.get("state", ""))
    if state not in ("starred", "dismissed", "new"):
        raise ValueError("state must be starred, dismissed or new")
    radar.set_state(con, int(body["item"]), state)
    return {"starred": "Saved under Research › Saved.", "dismissed": "Hidden from the feed.", "new": "Back in the feed."}[state]


def _research_to_idea(con, body):
    from engine import deepresearch, radar
    if body.get("note"):
        return f"Idea #{deepresearch.to_idea(con, int(body['note']))} is in Forge › Ideas."
    approach = str(body.get("approach", ""))
    if approach and approach not in radar.APPROACH:
        raise ValueError("approach must be adopt, adapt or build")
    iid = radar.set_state(con, int(body["item"]), "idea", approach)
    return (f"{radar.APPROACH[approach][0]}: " if approach else "") + f"idea #{iid} is in Forge › Ideas."


def _deep_research(con, body):
    from engine import deepresearch
    n = deepresearch.ask(con, str(body.get("question", "")), int(body["item"]) if body.get("item") else None)
    return f"Research #{n['id']} " + ("is ready under Deep research." if n["status"] == "done" else "could not reach a model; related items are listed.")


def _research_sources(con, body):
    from engine import radar
    new = body.get("settings") or {}
    if not isinstance(new, dict):
        raise ValueError("settings must be an object")
    return radar.save_settings(con, new)


def _tune_interests(con, body):
    from engine import radar
    lists = {k: body[k] for k in ("mute", "boost", "add") if k in body}
    if any(not isinstance(v, list) for v in lists.values()):
        raise ValueError("mute, boost and add must be lists of words")
    return radar.save_tuning(con, **lists)


def _radar_scan(con, body):
    from engine import launch
    launch.detached(["engine", "radar", "scan"], ROOT / "memory" / "radar.log", "radar")
    return "Scanning the sources now; new findings appear in the feed in a minute or two."


def _edit_resource(con, body):
    from engine import resources
    if body.get("retire") or body.get("restore"):
        resources.retire(con, int(body.get("retire") or body.get("restore")), back=bool(body.get("restore")))
        return "Back in use." if body.get("restore") else "Retired: kept, but no longer listed."
    fields = body.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    if body.get("id"):
        resources.edit(con, int(body["id"]), fields)
        return "Saved."
    resources.add(con, fields)
    return f"Added: {fields.get('name')}."


def _report_hardware(con, body):
    """A machine reports what it is (Atlas on the PC, through the hub); one live resource row per machine."""
    from engine import hardware
    report = body.get("report")
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    clean = {k: report.get(k) for k in ("host", "role", "model", "os", "arch", "cpu", "cores", "memory_gb", "gpus",
                                        "disk_free_gb", "disk_total_gb")}
    clean["gpus"] = [g for g in (clean["gpus"] or []) if isinstance(g, dict)][:4]
    rid = hardware.upsert(con, clean)
    return f"Resource #{rid} is up to date: {hardware.describe(clean)[0]}."


def _arsenal_scan(con, body):
    from engine import launch
    launch.detached(["engine", "arsenal", "scan"], ROOT / "memory" / "arsenal.log", "arsenal")
    return "Indexing your project folders again; the counts update in a minute."


def _record_memory(con, body):
    from engine import db
    kind, text = str(body.get("kind", "")), " ".join(str(body.get("text", "")).split())[:2000]
    if kind not in MEMORY_KINDS[:4]:
        raise ValueError("kind must be lesson, decision, preference or fact")
    if len(text) < 4:
        raise ValueError("Write what should be remembered")
    mid = db.insert(con, "memories", kind=kind, content=text, tags=" ".join(str(body.get("tags", "")).split())[:200], source="me")
    from engine import brain
    brain.record(con, "create", "memories", mid, "remembered (Atlas)")  # so Undo can take it back (#186)
    db.log(con, "me", "add_memory", f"#{mid} {kind}")
    return f"Remembered as a {kind}."


def _kill_switch(con, body):
    from engine import usage
    if "on" not in body:
        raise ValueError("Say on: true to stop everything, or on: false to resume")
    return usage.kill(con, "Atlas") if body["on"] else usage.unkill(con)


def _repairs(con, body):
    from engine import db
    on = bool(body.get("enabled"))
    db.set_state(con, "recovery_enabled", "1" if on else "0")
    db.log(con, "me", "recovery_toggle", "on" if on else "off")
    return "Automatic repairs on: small, tested fixes to crashes install by themselves." if on else "Automatic repairs off: crashes are only recorded."


def _claude_login(con, body):
    """Reconnect the Claude subscription on the Pi. The sign-in link and session live in the hub's memory only;
    the code is passed straight to Claude's own command-line tool and never logged or stored."""
    from engine import claudeauth, db
    step = str(body.get("step", "status"))
    login = claudeauth.login
    if step == "status":
        out = login.status()
    elif step == "check":
        out = login.refresh()
    elif step == "start":
        out = login.start()
    elif step == "cancel":
        out = login.cancel()
    elif step == "submit":
        out = login.submit(str(body.get("session", "")), str(body.get("code", "")))
    else:
        raise ValueError("step must be status, check, start, submit or cancel")
    if step in ("start", "submit", "cancel"):
        db.log(con, "me", f"claude_auth_{step}", "Claude connection")
    return out


def _voice_setup(con, body):
    """Test a transcription tier, or set the GPU PC's address. Keys never come through Atlas: they belong in the Pi's .env."""
    from engine import voicesetup
    if body.get("key"):
        raise ValueError("Keys are never sent through Atlas. Put HQ_STT_CLOUD_KEY in the Pi's .env (see Run in your terminal).")
    if body.get("test"):
        r = voicesetup.test(con, str(body["test"]))
        return r.get("message") or ("Works." if r.get("ok") else "Did not work.")
    if body.get("tier") == "gpu":
        return voicesetup.save(con, str(body["tier"]), str(body.get("url", "")))
    raise ValueError("Send test: <tier>, or tier: gpu with url")


def _fresh_window(con, body):
    from engine import fresh
    return fresh.set_window(con, body.get("minutes"))


def _name_cortex(con, body):
    from engine import chat
    return chat.set_name(con, body.get("name"))


def _mockup(con, body):
    """Visual first (#147): choose per task, request, give feedback (a new round), approve, set aside, reopen."""
    from engine import mockup
    if isinstance(body.get("preview"), dict):  # a ```svg or ```html block in the chat: the page the sandboxed frame shows (#183)
        p = body["preview"]
        return mockup.drawing(str(p.get("code", "")), str(p.get("kind", "")))
    if isinstance(body.get("drawing"), dict):  # keep a drawing from the chat as a mockup, for a task or an idea
        d = body["drawing"]
        num = lambda k: int(d[k]) if str(d.get(k) or "").strip() else None
        out = mockup.save_drawing(con, str(d.get("code", "")), str(d.get("kind", "")), str(d.get("title", "")),
                                  num("task"), num("idea"), str(d.get("feedback", "")))
        where = f" for task #{out['task']}" if out["task"] else f" for idea #{out['idea']}" if out["idea"] else ""
        return (f"Saved as mockup #{out['id']} round {out['round']}{where}. It is in Cortex with the other mockups"
                + (": approve it there and the builder builds to match it." if out["task"] else "."))
    if "choose" in body:
        return mockup.choose(con, int(body["choose"]), bool(body.get("visual")))
    if isinstance(body.get("request"), dict):  # Atlas no longer draws: the mockup is made in Claude or ChatGPT (owner, 2026-10-25)
        r = body["request"]
        out = mockup.start(con, str(r.get("title", "")), str(r.get("brief", "")), str(r.get("section", "")))
        return f"Mockup #{out['id']} is open: make it in Claude or ChatGPT, then add it here."
    mid = int(body["mockup"])
    if "data" in body:  # a picture or HTML page brought back from Claude or ChatGPT
        import base64
        out = mockup.add_round(con, mid, base64.b64decode(str(body["data"]), validate=True), str(body.get("type", "")),
                               str(body.get("feedback", "")), str(body.get("by", "upload")))
        return f"Round {out['round']} added. Approve it, or ask for changes and make the next round."
    if "feedback" in body:
        raise ValueError("Atlas does not draw mockups itself any more: make the next round in Claude or ChatGPT and add it")
    do = {"approve": mockup.approve, "set_aside": mockup.set_aside, "reopen": mockup.reopen}.get(str(body.get("do", "")))
    if do is None:
        raise ValueError("Say what to do: choose, request, feedback, or do = approve | set_aside | reopen")
    return do(con, mid)


def _speak_to(con, body):
    """The magic button (owner, 2026-10-25): what he says on a screen → Cortex's reply and up to three proposals.
    Nothing changes here; Atlas runs a proposal's action only after he says yes."""
    from engine import screen
    if "keep" in body:  # "Continue in the chat": only then the quick exchange becomes a conversation
        return screen.keep(con, body["keep"])
    views = body.get("views") if isinstance(body.get("views"), list) else []
    return screen.propose(con, str(body.get("text", "")), str(body.get("view", ""))[:60], str(body.get("visible", ""))[:8000], views,
                          history=body.get("history") if isinstance(body.get("history"), list) else None)  # quick reach: screen.QUICK


def _agent_order(con, body):
    """His fallback order of coding agents, and their record among the resources."""
    from engine import agents
    msg = agents.set_order(con, body.get("order")) if "order" in body else "Agents checked."
    n = agents.sync_resources(con)
    return f"{msg} {n} installed here, listed under Resources."


def _chat_stream(con, body, emit=None):
    """The Cortex chat on Claude Code (RA-083): real turns, web search, Atlas look-ups, attached files; streamed when
    called through /v1/stream/. Without Claude Code it falls back to the one-shot answer (RA-077)."""
    from engine import chat, cortex, cortex_actions, models, talk
    views = [str(v)[:40] for v in body.get("views", [])][:40] if isinstance(body.get("views"), list) else []
    text = str(body.get("text", ""))
    conv = chat.current(con)
    picked = cortex_actions.typed(con, conv, text, ACTIONS)  # "all", "1 and 3", "yes": the latest options (#186)
    if picked and "answer" in picked:
        text = picked["answer"]  # a quick-choice answer (#160) is his reply to Cortex
    elif picked:
        chat.add(con, "me", text)
        chat.add(con, "claude", picked["reply"])
        con.commit()
        if emit:
            emit({"t": "text", "d": picked["reply"]})
        return {"mode": "answer", "reply": picked["reply"], "proposals": picked.get("proposals", []), "choices": [], "picked": True}
    if cortex.available():
        try:
            out = cortex.run(con, text, emit, views, action_catalog(), ACTIONS)
            cortex_actions.remember(con, conv, out.get("choices", []), out.get("proposals", []))
            return {"mode": "answer", **out}
        except models.ModelUnavailable as exc:
            if emit:
                emit({"t": "status", "d": f"Claude Code did not answer ({str(exc)[:120]}); trying the other way."})
            con.execute("DELETE FROM chat WHERE id = (SELECT MAX(id) FROM chat WHERE role = 'me' AND text = ?)", (text.strip(),))  # asked again below
    out = talk.say(con, text, "atlas", "ask", action_catalog(), views)
    cortex_actions.remember(con, conv, out.get("choices", []), out.get("proposals", []))
    if emit:
        emit({"t": "text", "d": out["reply"]})
    return {"mode": out["mode"], "reply": out["reply"], "proposals": out.get("proposals", []), "choices": out.get("choices", []),
            "mockup": out.get("mockup")}


def _cortex_apply(con, body):
    """He tapped an option of Cortex (chat or ✦ button): its steps run in order, all or nothing, with one Undo (#186).
    The other options stay open; the same option typed later is not applied twice."""
    from engine import chat, cortex_actions
    p = body.get("proposal")
    if not isinstance(p, dict):
        raise ValueError("Send the option to apply")
    allowed = action_catalog()
    if p.get("steps"):
        steps = cortex_actions.clean_steps(p["steps"], allowed)
        if not steps:
            raise ValueError("This option has a step Atlas does not accept")
        p = {**p, "steps": steps}
    elif str(p.get("action") or "") not in allowed:
        raise ValueError("This option is not an action Cortex may offer")
    out = cortex_actions.apply(con, p, ACTIONS)
    cortex_actions.mark(con, chat.current(con), p)
    return out["message"] or "Done."


def _cortex_autonomy(con, body):
    """System › Settings: what Cortex may do at once. {"level": "routine"|"ask"}; {} reads it."""
    from engine import cortex_actions
    if body.get("level"):
        cortex_actions.set_level(con, str(body["level"]))
    lv = cortex_actions.level(con)
    return {"level": lv, "text": cortex_actions.TEXT[lv], "levels": list(cortex_actions.LEVELS)}


def _chat_file(con, body):
    """A file for the current Cortex conversation (Cortex can read it)."""
    import base64
    from engine import cortex
    name = cortex.attach(con, str(body.get("name", "file")), base64.b64decode(str(body.get("data", "")), validate=True))
    return {"name": name}


def _build_next(con, body):
    """The Build next lane (#182): {"task": N, "position": k} puts #N at place k (1 = next); position 0 or null takes it
    out; {"order": [ids]} reorders the lane. The builder takes these first, before the queue and any score."""
    from engine import priority, skilltree
    if isinstance(body.get("order"), list):
        message = skilltree.set_lane_order(con, body["order"])
    else:
        pos = body.get("position")
        message = skilltree.set_lane(con, int(body["task"]), int(pos) if pos not in (None, "", False) else None)
    priority.rerank(con, "build next", message[:80])
    return message


def _new_conversation(con, body):
    """New conversation, or {"open": n}: back to an earlier one."""
    from engine import chat
    if body.get("open") not in (None, ""):
        return chat.open_conversation(con, int(body["open"]))
    return chat.new_conversation(con)



def _explore_idea(con, body):
    from engine import explore
    e = explore.explore(con, int(body["idea"]))
    return e["summary"] or "Explored."


def _idea_to_plan(con, body):
    from engine import placement
    idea = int(body["idea"])
    if body.get("take"):  # Take it: the AI decides task or goal, from the exploration or the placement
        from engine import explore
        return explore.take(con, idea)
    if isinstance(body.get("new_goal"), dict):  # Start as a new goal, the idea its first task
        g = body["new_goal"]
        gid, tid = placement.to_new_goal(con, idea, str(g.get("title", "")), str(g.get("why", "")))
        return f"New goal #{gid} started, with the idea as its first task #{tid}."
    tid = placement.to_plan(con, idea, int(body["goal"]))
    return f"Idea is now task #{tid} in your plan. Send it to Build when you want the agent to make it."


def _place_ideas(con, body):
    from engine import placement
    r = placement.refresh(con, use_model=True)
    return (f"Claude placed {r['ai']} idea(s)." if r["ai"] else
            f"Claude is not reachable ({r['error'] or 'no answer'}); the suggestions come from word overlap for now.")


def _edit_plan(con, body):
    """Create or edit tasks, ideas, goals and skill-tree nodes (validated by the Engine, logged, undoable),
    or set an open idea aside."""
    from engine import brain, edit, priority
    if isinstance(body.get("edit"), dict):
        e = body["edit"]
        message = edit.edit(con, str(e.get("table", "")), int(e["id"]), dict(e.get("fields") or {}), "edited by Djeddi (Atlas)")
        priority.rerank(con, "edited", f"{e.get('table')} #{e['id']}")
        return message
    if isinstance(body.get("create"), dict):
        c = body["create"]
        table, fields = str(c.get("table", "")), dict(c.get("fields") or {})
        parent = fields.pop("parent_id", None) if table == "goals" else None
        rid = edit.create(con, table, fields, "added by Djeddi (Atlas)")
        if parent not in (None, ""):
            from engine import plan
            plan.set_parent(con, rid, int(parent))  # a goal under a bigger goal (life → year → quarter → month)
        priority.rerank(con, "added", f"{table} #{rid}")
        return f"Added {table[:-1] if table.endswith('s') else table} #{rid}."
    idea = int(body["drop_idea"])
    row = con.execute("SELECT gate FROM ideas WHERE id = ?", (idea,)).fetchone()
    if row is None or row[0] != "idea":
        raise ValueError(f"idea #{idea} is not open")
    brain.set_field(con, "ideas", idea, "gate", "dropped", "set aside by Djeddi (Atlas)")
    db_log(con, "idea_dropped", f"idea #{idea} via atlas")
    return f"Idea #{idea} set aside: kept, not deleted (undoable in the Engine's change log)."


def _start_update_runner():
    """Run the update outside the hub's own service: the update restarts the hub, which must not stop it."""
    from engine import launch
    launch.detached(["services.atlas.hub", "--run-update"], ROOT / "memory" / "update.log", "update")


def restart_and_wait():
    """Restart the Engine and the hub on the new code, and wait for the restart script to finish.

    The update runs as its own short-lived systemd unit; anything it starts in the background is stopped with
    it when it ends, which is how a detached restart got lost before. Waiting here avoids that."""
    import subprocess
    subprocess.run(["bash", str(ROOT / "deploy" / "restart.sh")], cwd=ROOT, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def _delivery_after_install(con):
    """Merged PRs whose commit has just been installed move to 'installed' at once, as `hq update` does; the
    Update button used to skip this, so the cards kept saying 'Update installs it' until the next 10-minute sync."""
    try:
        from engine import delivery
        delivery.sync(con, remote=False)
    except Exception:  # bookkeeping must never stop the restart
        pass


def run_update(wait_ms=120000):
    """The update itself (the Engine's updater: tests on a copy first, then install and restart); reports to UPDATE_STATE.

    Other Engine processes (the hourly cycle, scans, the builder) may hold the database for a while, so this waits up
    to two minutes for it instead of 15 seconds. If installing worked but a later step failed (for example the log
    line could not be written), the restart still happens, so the Pi never stays on new files with old processes."""
    from engine import updater
    _write_update_state({"state": "running", "startedAt": time.time(), "message": "Running the tests on the new version…"})
    before = git_version()[1]
    try:
        with closing(read_write(engine_db())) as con:
            con.execute(f"PRAGMA busy_timeout = {int(wait_ms)}")
            result = updater.apply(con, do_restart=False)
            if result.get("status") == "updated":
                _delivery_after_install(con)
        _write_update_state({"state": result.get("status", "done"), "finishedAt": time.time(),
                             "message": result.get("message", ""), "target": result.get("target", "")})
        if result.get("status") == "updated":
            restart_and_wait()
    except Exception as exc:  # report every failure to the screen instead of leaving "running"
        after = git_version()[1]
        installed = bool(after and before and after != before)
        if installed:
            restart_and_wait()
        stage = (f"installed {before} → {after} and restarted, but a later step failed: {exc}" if installed else
                 f"nothing was installed ({exc}). Try again; if it repeats, run ./hq.sh update on the Pi to see why.")
        _write_update_state({"state": "updated" if installed else "failed", "finishedAt": time.time(),
                             "message": f"Update {stage}", "target": after if installed else ""})


def _update(con, body):
    from engine import updater
    if body.get("check"):
        info = updater.check()  # fetches from GitHub
        return (f"{info['behind']} new change(s) on GitHub." if info["behind"] else "The Pi is up to date.") \
            + (" " + info["blocker"] if info["blocker"] else "")
    if _update_running():
        return "An update is already running on the Pi."
    _write_update_state({"state": "running", "startedAt": time.time(), "message": "Starting…"})
    _start_update_runner()
    db_log(con, "update_requested", "via atlas")
    return "Updating the Pi: tests run first (a few minutes), then it installs and restarts."


def db_log(con, action, detail):
    from engine import db
    db.log(con, "me", action, detail)


# Capability id → write through the Engine's own functions. Keep in step with `viaHub` in modules/engine.json.
STREAMS = {"engine.brain.chat_stream": _chat_stream}  # POST /v1/stream/<id>: newline-delimited JSON events, then {"t": "done"}

STREAMS = {"engine.brain.chat_stream": _chat_stream}  # POST /v1/stream/<id>: newline-delimited JSON events, then {"t": "done"}

ACTIONS = {
    "engine.attention.answer_needs": _answer_need,
    "engine.attention.finish_terminal_step": _finish_terminal_step,
    "engine.attention.finish_interrupted": _finish_interrupted,
    "engine.brain.write_thought": _write_thought,
    "engine.brain.undo_changes": _undo_thought,
    "engine.brain.cortex_apply": _cortex_apply,
    "engine.brain.cortex_autonomy": _cortex_autonomy,
    "engine.direction.set_focus": _set_focus,
    "engine.build.idea_to_plan": _idea_to_plan,
    "engine.build.explore_idea": _explore_idea,
    "engine.brain.ingest": _ingest,
    "engine.brain.talk": _talk,
    "engine.brain.name_cortex": _name_cortex,
    "engine.brain.mockup": _mockup,
    "engine.brain.new_conversation": _new_conversation,
    "engine.brain.chat_stream": _chat_stream,
    "engine.brain.chat_file": _chat_file,
    "engine.brain.speak_to": _speak_to,
    "engine.assets.agent_order": _agent_order,
    "engine.direction.fresh_window": _fresh_window,
    "engine.brain.voice_input": _voice_input,
    "engine.growth.confirm_proof": _confirm_proof,
    "engine.growth.node_research": _node_research,
    "engine.growth.draft_node_task": _draft_node_task,
    "engine.research.mark_finding": _mark_finding,
    "engine.research.research_to_idea": _research_to_idea,
    "engine.research.find_options": _deep_research,
    "engine.research.research_sources": _research_sources,
    "engine.research.tune_interests": _tune_interests,
    "engine.research.radar_scan": _radar_scan,
    "engine.assets.edit_resource": _edit_resource,
    "engine.build.build_order": _build_order,
    "engine.assets.report_hardware": _report_hardware,
    "engine.assets.arsenal_scan": _arsenal_scan,
    "engine.memory.record_lesson": _record_memory,
    "engine.system.kill_switch": _kill_switch,
    "engine.system.repairs": _repairs,
    "engine.build.claude_login": _claude_login,
    "engine.system.voice_setup": _voice_setup,
    "engine.build.place_ideas": _place_ideas,
    "engine.build.edit_plan": _edit_plan,
    "engine.system.updates": _update,
    "engine.direction.choose_direction": _choose_direction,
    "engine.direction.shape_directions": _shape_directions,
    "engine.build.dev_controls": _dev_controls,
    "engine.build.dev_loop": _dev_run,
    "engine.build.pr_conflicts": _pr_conflicts,
    "engine.build.build_queue": _build_queue,
    "engine.build.quick_build": _quick_build,
    "engine.build.task_status": _task_status,
    "engine.build.reorder_tasks": _reorder,
    "engine.build.build_next": _build_next,
    "engine.build.parallel_batch": _parallel_batch,
    "engine.build.do_over": _do_over,
    "engine.system.revisit": _revisit,
    "engine.direction.weekly_review": _weekly_review,
}


def _last_change(con):
    try:
        return con.execute("SELECT COALESCE(MAX(id), 0) FROM brain_changes").fetchone()[0]
    except sqlite3.Error:
        return 0


def read_write(path):
    from engine import db
    if not Path(path).exists():
        raise FileNotFoundError("The Engine's database was not found on this computer")
    con = db.connect(Path(path))
    con.execute("PRAGMA busy_timeout = 15000")  # the Engine itself may be writing at the same moment
    return con


def git_version():
    from services.atlas.handoff import git_head
    return git_head(ROOT)


def load_token():
    token = os.environ.get("ATLAS_HUB_TOKEN", "")
    if not token:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("ATLAS_HUB_TOKEN="):
                    token = line.split("=", 1)[1].strip()
    return token


def sync_db():
    """The authority for synced notes (RA-013): Atlas's own file next to the hub, never the Engine's database."""
    return Path(os.environ["ATLAS_SYNC_DB"]) if os.environ.get("ATLAS_SYNC_DB") else ROOT / "runtime" / "sync-authority.db"


def sync_authority(path):
    from services.atlas import sync
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    sync.migrate_authority(con)
    con.commit()
    return con


def make_server(host, port, token, db_path=None, sync_path=None):
    if not token or len(token) < 20:
        raise SystemExit("Refusing to start the hub without a strong ATLAS_HUB_TOKEN (run deploy/atlas-hub.sh).")
    if host in ("0.0.0.0", "::", ""):
        raise SystemExit("Refusing to listen on every interface; give the Tailscale address with --host.")
    db_path = db_path or engine_db()
    sync_path = sync_path or sync_db()
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    running_commit = git_version()[1]  # the code this process runs; the disk may be newer after an update

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, value):
            payload = json.dumps(value, default=lambda o: o.isoformat() if isinstance(o, (date, datetime)) else str(o)).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def authorised(self):
            given = self.headers.get("Authorization", "")
            ok = given.startswith("Bearer ") and hmac.compare_digest(given[7:].encode(), token.encode())
            if not ok:
                time.sleep(0.5)  # slows down guessing
            return ok

        def do_GET(self):
            if not self.authorised():
                return self.send(401, {"error": "Missing or wrong hub token"})
            parts = urlsplit(self.path)
            if parts.path == "/v1/health":
                branch, commit = git_version()
                return self.send(200, {"status": "ok", "version": VERSION, "branch": branch, "commit": commit,
                                       "runningCommit": running_commit, "restartNeeded": bool(commit and running_commit and commit != running_commit),
                                       "startedAt": started, "engineDatabase": Path(db_path).exists(),
                                       "functions": sorted(FUNCTIONS), "actions": sorted(ACTIONS), "sync": 1})
            if parts.path == "/v1/sync/pull":
                from services.atlas import sync
                try:
                    since = int((parse_qs(parts.query).get("since") or ["0"])[0])
                    with closing(sync_authority(sync_path)) as con:
                        return self.send(200, sync.changes(con, since))
                except ValueError as exc:
                    return self.send(400, {"error": str(exc)})
            if parts.path.startswith("/v1/functions/"):
                fid = parts.path[len("/v1/functions/"):]
                if fid not in FUNCTIONS:
                    return self.send(404, {"error": f"{fid} is not available through the hub"})
                try:
                    with closing(read_only(db_path)) as con:
                        result = FUNCTIONS[fid](con, parse_qs(parts.query))
                    return self.send(200, {"function": fid, "result": result})
                except FileNotFoundError as exc:
                    return self.send(503, {"error": str(exc)})
                except SchemaMismatch as exc:
                    return self.send(409, {"error": str(exc), "kind": "schema_mismatch"})
                except (KeyError, TypeError) as exc:
                    return self.send(400, {"error": f"{fid} needs {exc}"})
                except (ValueError, sqlite3.Error) as exc:
                    return self.send(500, {"error": f"{fid} failed: {exc}"})
            return self.send(404, {"error": "Unknown endpoint"})

        def worker_call(self, what):
            """A remote builder (#185): its own token, not the hub token; it can only claim, report and keep its lease."""
            from engine import workers
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > 200000:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Send a JSON object")
                name, given = self.headers.get("X-Atlas-Worker", ""), self.headers.get("Authorization", "")[7:]
                with closing(read_write(db_path)) as con:
                    if what == "claim":
                        out = workers.claim(con, name, given, str(body.get("idle") or ""))
                    elif what == "heartbeat":
                        out = workers.heartbeat(con, name, given, int(body["lease"]), body.get("lines") if isinstance(body.get("lines"), list) else None)
                    else:
                        out = {"message": workers.report(con, name, given, int(body["lease"]), body.get("result") or {})}
                return self.send(200, out)
            except PermissionError as exc:
                time.sleep(0.5)  # slows down guessing
                return self.send(403, {"error": str(exc)})
            except (ValueError, KeyError, TypeError) as exc:
                return self.send(409 if "lease is" in str(exc) else 400, {"error": str(exc)})
            except sqlite3.Error as exc:
                return self.send(500, {"error": f"worker {what} failed: {exc}"})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path in ("/v1/worker/claim", "/v1/worker/heartbeat", "/v1/worker/report"):
                return self.worker_call(path.rsplit("/", 1)[-1])
            if not self.authorised():
                return self.send(401, {"error": "Missing or wrong hub token"})
            if path == "/v1/sync/push":
                return self.sync_push()
            if path.startswith("/v1/stream/"):
                return self.stream(path[len("/v1/stream/"):])
            if not path.startswith("/v1/actions/"):
                return self.send(404, {"error": "Unknown endpoint"})
            aid = path[len("/v1/actions/"):]
            if aid not in ACTIONS:
                return self.send(404, {"error": f"{aid} is not available through the hub"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > (int(MAX_FILE * 1.4) if aid in UPLOADS else 20000):
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Send a JSON object")
                with closing(read_write(db_path)) as con:
                    before = _last_change(con)
                    message = ACTIONS[aid](con, body)
                    con.commit()
                    changes = [r[0] for r in con.execute("SELECT id FROM brain_changes WHERE id > ? AND undone = 0 ORDER BY id", (before,))] \
                        if aid != "engine.brain.undo_changes" else []
                return self.send(200, {"action": aid, "message": message, "changes": changes})
            except FileNotFoundError as exc:
                return self.send(503, {"error": str(exc)})
            except (ValueError, KeyError, TypeError, PermissionError) as exc:
                return self.send(400, {"error": str(exc)})
            except sqlite3.Error as exc:
                return self.send(500, {"error": f"{aid} failed: {exc}"})

        def stream(self, aid):
            """A streamed action: one JSON event per line while it runs, the result last ({"t": "done"})."""
            if aid not in STREAMS:
                return self.send(404, {"error": f"{aid} cannot be streamed"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > 20000:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Send a JSON object")
            except (ValueError, TypeError) as exc:
                return self.send(400, {"error": str(exc)})
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            gone = []

            def emit(event):
                if gone:
                    return  # the screen went away: the turn still finishes and is kept
                try:
                    self.wfile.write((json.dumps(event, default=str) + "\n").encode())
                    self.wfile.flush()
                except OSError:
                    gone.append(True)
            try:
                with closing(read_write(db_path)) as con:
                    before = _last_change(con)
                    result = STREAMS[aid](con, body, emit)
                    con.commit()
                    changes = [r[0] for r in con.execute("SELECT id FROM brain_changes WHERE id > ? AND undone = 0 ORDER BY id", (before,))]
                emit({"t": "done", "d": {"message": result, "changes": changes}})
            except FileNotFoundError as exc:
                emit({"t": "error", "d": str(exc)})
            except (ValueError, KeyError, TypeError, PermissionError, sqlite3.Error) as exc:
                emit({"t": "error", "d": str(exc)})
            except Exception as exc:  # noqa: BLE001 - e.g. no model reachable: say so instead of a broken stream
                emit({"t": "error", "d": f"{exc.__class__.__name__}: {str(exc)[:200]}"})
            self.close_connection = True

        def sync_push(self):
            """Apply one PC's outbox batch in a single transaction (all or nothing), then answer per command."""
            from services.atlas import sync
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > sync.MAX_BATCH * (sync.MAX_BODY + 400):
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Send a JSON object")
                with closing(sync_authority(sync_path)) as con:
                    with con:
                        results = sync.apply(con, body.get("deviceId"), body.get("commands"))
                return self.send(200, {"results": results})
            except (ValueError, TypeError) as exc:
                return self.send(400, {"error": str(exc)})
            except sqlite3.Error as exc:
                return self.send(500, {"error": f"sync failed: {exc}"})

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
                return
            super().handle_error(request, client_address)

    return Server((host, port), Handler)


def follow_updates(server, running_commit, every=60, head=None):
    """Under systemd (Restart=always): stop cleanly when the code on disk is newer than the code running,
    so systemd starts the hub again on the new code. Covers updates whose restart step did not run."""
    import threading
    head = head or (lambda: git_version()[1])

    def loop():
        while True:
            time.sleep(every)
            disk = head()
            if disk and running_commit and disk != running_commit and not _update_running():
                print(f"New code on disk ({disk}, running {running_commit}): restarting the hub", flush=True)
                server.shutdown()
                return
    threading.Thread(target=loop, daemon=True, name="hub-follow-updates").start()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("ATLAS_HUB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ATLAS_HUB_PORT", DEFAULT_PORT)))
    parser.add_argument("--run-update", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.run_update:
        return run_update()
    server = make_server(args.host, args.port, load_token())
    print(f"Atlas hub on http://{args.host}:{server.server_port} (token required)", flush=True)
    if os.environ.get("INVOCATION_ID"):  # started by systemd, which brings it back after a clean stop
        follow_updates(server, git_version()[1])
    server.serve_forever()


if __name__ == "__main__":
    main()
