"""Atlas command line. Run `python -m engine --help`.

Everything the Telegram/voice front-end (`hq bot`) does maps onto these commands,
so the interface layer stays thin and the memory stays the single source of truth.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import brain, db, loop, priority


def _con():
    con = db.connect()
    db.init(con)
    return con


def cmd_init(a):
    con = _con()
    if a.seed:
        if con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]:
            sys.exit("Database already has tasks; not seeding twice.")
        print("Seeded:", loop.seed(con))
    print(f"Atlas memory ready at {db.DB_PATH}")


def cmd_next(a):
    con = _con()
    rules = loop.load_constitution()
    priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
    rows = priority.next_tasks(con, a.n, owner=a.owner)
    if not rows:
        print("Nothing open. Add a task with `hq add task \"...\"`.")
    for r in rows:
        due = f" · due {r['due']}" if r["due"] else ""
        print(f"#{r['id']:<3} {r['score']:>5.2f}  [{r['owner']}] {r['title']}  (~{r['effort_h']:g}h{due})")


def cmd_add(a):
    con = _con()
    if a.kind == "task":
        rid = db.insert(con, "tasks", title=a.text, impact=a.impact, effort_h=a.effort, due=a.due,
                        owner=a.owner or "me", phase=a.phase, depends_on=a.after or "", goal_id=a.goal)
        if a.owner in ("ai", "both") and os.environ.get("HQ_ACTOR") != "agent":  # Djeddi's own queue (not the agent's notes)
            from . import skilltree
            skilltree.queue(con, rid)
    elif a.kind == "idea":
        rid = db.insert(con, "ideas", title=a.text, impact=a.impact, effort_h=a.effort,
                        category=a.category or "general", cost_eur=a.cost)
    elif a.kind == "goal":
        if a.parent and not con.execute("SELECT 1 FROM goals WHERE id = ?", (a.parent,)).fetchone():
            sys.exit(f"goal #{a.parent} does not exist (see ./atlas.sh plan)")
        rid = db.insert(con, "goals", title=a.text, horizon=a.horizon or "goal", parent_id=a.parent)
    elif a.kind == "resource":
        rid = db.insert(con, "resources", kind=a.category or "other", name=a.text, details=a.details or "",
                        value=a.value, unit=a.unit or "")
    else:
        rid = db.insert(con, "memories", kind=a.category or "fact", content=a.text, tags=a.tags or "")
    table = {"task": "tasks", "idea": "ideas", "goal": "goals", "resource": "resources"}.get(a.kind, "memories")
    brain.record(con, "create", table, rid, f"added with `add {a.kind}`")  # undoable like a dump item
    db.log(con, "me", f"add_{a.kind}", a.text)
    con.commit()
    print(f"Added {a.kind} #{rid}")


def cmd_inbox(a):
    """Fast intake for anything: 'todo: ...', 'idea: ...', 'goal: ...', otherwise stored as info."""
    con = _con()
    text = " ".join(a.text).strip()
    prefix, _, rest = text.partition(":")
    p = prefix.lower().strip()
    if rest and p in ("todo", "task"):
        rid, kind, table = db.insert(con, "tasks", title=rest.strip()), "task", "tasks"
    elif rest and p == "idea":
        rid, kind, table = db.insert(con, "ideas", title=rest.strip()), "idea", "ideas"
    elif rest and p == "goal":
        rid, kind, table = db.insert(con, "goals", title=rest.strip()), "goal", "goals"
    else:
        rid, kind, table = db.insert(con, "memories", kind="info", content=text), "memory", "memories"
    brain.record(con, "create", table, rid, "captured with `inbox`")  # undoable like a dump item
    db.log(con, "me", "inbox", text)
    con.commit()
    loop.run_cycle(con)  # new information always triggers a re-rank
    print(f"Captured as {kind} #{rid}; priorities updated.")


def _set_status(table, status, id_, actor="me"):
    con = _con()
    db.update(con, table, id_, status=status)
    db.log(con, actor, f"{table}_{status}", str(id_))
    con.commit()
    loop.run_cycle(con)
    print(f"{table[:-1]} #{id_} → {status}")


def cmd_status(a):
    if a.status == "done":
        return cmd_done(a)
    _set_status("tasks", a.status, a.id)


def _task_action(fn, *args, rerank=True):
    """rerank=False for actions that already re-rank at the source (flow.complete)."""
    con = _con()
    try:
        print(fn(con, *args))
    except (PermissionError, ValueError) as e:
        sys.exit(str(e))
    if rerank:
        loop.run_cycle(con)


def cmd_done(a):
    """The agent marks tasks done after implementing them; Djeddi may close only his own (owner me) tasks."""
    from . import flow
    _task_action(flow.complete, a.id, flow.actor(), rerank=False)  # complete() re-ranks itself


def cmd_run(a):
    from . import flow
    _task_action(flow.run, a.id)


def cmd_move(a):
    from . import edit
    con = _con()
    try:
        print(edit.move(con, a.id, a.direction))
    except ValueError as e:
        sys.exit(str(e))
    loop.run_cycle(con)


def cmd_delete(a):
    from . import flow
    _task_action(flow.discard, a.id)


def cmd_accept(a):
    _set_status("tasks", "todo", a.id)


def cmd_approvals(a):
    con = _con()
    if a.decision:
        from . import gatekeeper
        try:
            gatekeeper.guard_manual(con, a.id, a.decision)
        except ValueError as e:
            sys.exit(str(e))
        con.execute("UPDATE approvals SET status = ?, decided = ? WHERE id = ?", (a.decision, db.now(), a.id))
        db.log(con, "me", f"approval_{a.decision}", str(a.id))
        con.commit()
        print(f"Approval #{a.id} {a.decision}.")
        return
    for r in con.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY created"):
        cost = f" €{r['cost_eur']:g}" if r["cost_eur"] else ""
        print(f"#{r['id']} [{r['category']}{cost}] {r['request']}\n    why: {r['reason']}")


def cmd_recall(a):
    con = _con()
    for r in db.recall(con, " ".join(a.query), a.n):
        print(f"#{r['id']} [{r['kind']}] {r['content']}  ({r['created'][:10]})")


def cmd_resources(a):
    con = _con()
    for r in con.execute("SELECT * FROM resources WHERE COALESCE(status, 'active') = 'active' ORDER BY kind, name"):
        val = f" = {r['value']:g} {r['unit']}" if r["value"] is not None else ""
        print(f"[{r['kind']}] {r['name']}{val}  {r['details']}")


def cmd_ideas(a):
    """Open ideas (not yet tasks); --all also lists promoted ones with the task they became."""
    con = _con()
    rows = con.execute("SELECT * FROM ideas WHERE gate = 'idea' ORDER BY score DESC").fetchall()
    for r in rows:
        print(f"#{r['id']:<3} {r['score']:>5.2f}  [{r['category']}] {r['title']}")
    if not rows:
        print("No open ideas. Say 'maybe ...' in a brain dump, or hq add idea \"...\".")
    else:
        print("\nMake one a task: hq promote <id>  (--ai: for the AI agent)")
    if a.all:
        for r in con.execute("SELECT * FROM ideas WHERE gate = 'promoted' ORDER BY id DESC"):
            print(f"#{r['id']:<3} → task #{r['task_id']}  {r['title']}")


def cmd_flow(a):
    from . import flow
    print(flow.MAP)


def cmd_list(a):
    from . import overview
    status = {x.strip() for x in (a.status or "").split(",") if x.strip()}
    try:
        data = overview.items(_con(), status=status or None, goal=a.goal, due=a.due,
                              kind="tasks" if a.tasks else "ideas" if a.ideas else "all", include_done=a.all)
    except ValueError as e:
        sys.exit(str(e))
    print(overview.text(data))


def cmd_cycle(a):
    con = _con()
    print(loop.run_cycle(con, think=a.think))
    from . import brain, doover
    out = brain.reevaluate_if_changed(con)  # Claude re-evaluates only after real change (RA-063)
    if out:
        print(doover.replan_text(out))
    from . import voiceback
    print(voiceback.retry(con))  # hourly: recordings stored while no Whisper tier was reachable
    from . import devloop
    devloop.sync_merged(con)  # hourly: merged PRs close their tasks, conflicts with main show on the System tab
    from . import atlasplan
    atlasplan.sync(con)  # hourly as well: the repository's RA steps and their status in the plan
    from . import hardware
    hardware.upsert(con, hardware.detect())  # hourly: the Pi itself as a live resource


def cmd_brief(a):
    print(loop.brief(_con()))


def cmd_log(a):
    con = _con()
    for r in con.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (a.n,)):
        print(f"{r['ts']} {r['actor']:<5} {r['action']:<16} {r['detail']}")


def cmd_dump(a):
    from . import dump
    con = _con()
    text = " ".join(a.text) if a.text else sys.stdin.read()
    print(dump.process(con, text))  # re-ranks at the source (dump.sort_thought)


def cmd_radar(a):
    from . import radar
    con = _con()
    if a.action == "scan":
        if getattr(a, "due", False) and not radar.due(con):
            return  # hourly cron: scan only when the interval set under Research → Sources & limits has passed
        print(radar.scan(con, loop.load_constitution(), a.sources or None))
        return
    if a.action in ("star", "dismiss", "idea"):
        radar.set_state(con, a.id, {"star": "starred", "dismiss": "dismissed", "idea": "idea"}[a.action])
        print("ok")
        return
    for r in con.execute("SELECT * FROM radar WHERE state != 'dismissed' ORDER BY found DESC, score DESC LIMIT ?", (a.n,)):
        tags = f"  [{r['matched']}]" if r["matched"] else ""
        print(f"#{r['id']:<4} {r['score']:>5.2f} {r['source']:<6} {r['title'][:70]}{tags}\n       {r['url']}")


def cmd_research(a):
    from . import deepresearch
    con = _con()
    try:
        if a.action == "ask":
            n = deepresearch.ask(con, " ".join(a.words))
            ans = n["answer"]
            print(f"#{n['id']} {n['question']}\n\n{ans.get('summary', '')}")
            for k, label in (("matters", "Why it matters"), ("risks", "Risks")):
                if ans.get(k):
                    print(f"\n{label}: {ans[k]}")
            for step in ans.get("next_steps") or []:
                print(f"  - {step}")
        elif a.action == "idea":
            print(f"Idea #{deepresearch.to_idea(con, int(a.words[0]))} added to Forge → Ideas.")
        else:
            for n in deepresearch.notes(con):
                print(f"#{n['id']} [{n['status']}] {n['question'][:100]}")
    except (ValueError, IndexError) as e:
        raise SystemExit(str(e) or 'Usage: hq research ask "question" | idea <note id>')


def cmd_backup(a):
    """Private, encrypted backup of the data outside the public repository (engine/backup.py)."""
    from . import backup
    if a.action == "restore":
        if not a.file:
            sys.exit("Usage: atlas.sh backup restore <file.db.enc>")
        print(f"Restored to {backup.restore(a.file)}; the live database is untouched.")
    elif a.action == "status":
        print(json.dumps(backup.status(_con()), indent=2))
    else:
        print(backup.run(_con()))


def cmd_arsenal(a):
    from . import arsenal
    con = _con()
    if a.action == "add":
        pid = arsenal.add_project(con, a.arg, a.name)
        print(f"Project #{pid} registered;", arsenal.scan(con, pid))
    elif a.action == "scan":
        print(arsenal.scan(con))
    elif a.action == "find":
        for r in arsenal.find(con, a.arg or "", a.n):
            doc = " — " + " ".join(r["doc"].split())[:90] if r["doc"] else ""
            print(f"[{r['project']}] {r['signature'] or r['name']}  ({r['path']}:{r['line']}){doc}")
    else:
        for r in con.execute("SELECT p.*, COUNT(a.id) n FROM projects p LEFT JOIN assets a ON a.project_id = p.id GROUP BY p.id"):
            print(f"#{r['id']} {r['name']}  {r['n']} items · {r['path']}  {r['description'][:80]}")


def cmd_serve(a):
    from . import dashboard, envfile
    _, created = envfile.ensure_token()  # the dashboard always has a token; created automatically if missing
    if created:
        print("Created a dashboard token in .env. Open this once per browser:\n  " + envfile.dashboard_link(port=a.port))
    dashboard.serve(a.port, a.host)


def cmd_route(a):
    from . import devloop, routing
    con = _con()
    task = con.execute("SELECT * FROM tasks WHERE id = ?", (a.id,)).fetchone() if a.id else devloop.pick_task(con, fallback=True)
    if not task:
        print("No such task." if a.id else "No AI task to route.")
        return
    print(f"#{task['id']} {task['title']}\n→ {routing.text(routing.choose(con, task))}")


def cmd_dev(a):
    from . import devloop
    LEVELS = devloop.LEVELS
    con = _con()
    if a.action == "status":
        print(devloop.status(con))
    elif a.action in LEVELS:
        print(devloop.set_autonomy(con, a.action))
    elif a.action in ("pause", "resume"):
        devloop.set_paused(con, a.action == "pause")
        print(devloop.status(con))
    elif a.action == "stop":
        print(devloop.stop(con))
    elif a.action == "lanes":
        print("\n".join(devloop.lanes_text(con)) or "Nothing in flight: no run and no PR waiting for review.")
    elif a.action == "conflicts":
        print(devloop.scan(con))
        found = devloop.conflicts(con)
        print("\n".join(f"Run {c['id']} · task #{c['task_id']} {c['title'] or ''}: {', '.join(c['files'])}"
                        + (f" (in the stack under task #{c['top']['task_id']}, run {c['top']['run']})" if c["top"] else "")
                        for c in found))
    elif a.action == "resolve":
        if not a.n:
            raise SystemExit("usage: hq dev resolve RUN  (the run id; see hq dev conflicts)")
        result = devloop.resolve(con, int(a.n))
        text = devloop.remember(con, result)
        print(text)
        devloop.notify(text)  # Djeddi pressed Resolve: it always reports back
    elif a.action == "limit":  # hq dev limit [runs|tokens|usd N]
        print(devloop.set_limit(con, a.n, a.value) if a.n and a.value is not None else devloop.limits_text(con))
    elif a.action == "batch":
        print(devloop.set_batch(con, a.n) if a.n else f"Review batch: {devloop.batch_size(con) or 'unlimited'}. "
              + devloop.batch_text(devloop.batch_size(con), capped=devloop.batch_capped(con)))
    else:
        result = devloop.run(con, task_id=a.task, force=a.force)
        text = devloop.remember(con, result)
        if result["status"] == "pr_open" and devloop.batch_full(con):
            n = devloop.open_prs(con)
            text += f"\n\nReview batch reached ({n} open PR(s)): the agent stops until you merge or close some."
        print(text)
        if (devloop.autonomy(con) == "continuous" and not devloop.batch_full(con) and result["status"] in ("pr_open", "no_changes", "failed", "tests_failed")
                and devloop.why_not_now(con) == ""):
            devloop.start_background(force=False)  # continuous: next task right away (daily limit still applies)
        if result["status"] in ("pr_open", "failed", "tests_failed", "no_changes", "stopped") or a.force:
            devloop.notify(text)  # a run you started by hand always reports back, also when it could not start


def cmd_kill(a):
    from . import usage
    print(usage.kill(_con(), "command line"))


def cmd_unkill(a):
    from . import usage
    print(usage.unkill(_con()))


def cmd_usage(a):
    from . import usage
    print(usage.text(_con()))


def cmd_voice(a):
    from . import voicesetup
    con = _con()
    if a.action == "status":
        st = voicesetup.status(con)
        for name, t in st["tiers"].items():
            c = t.get("check")
            where = t.get("url") or ""
            print(f"{t['label']:<11} {'connected' if t['configured'] else 'not set':<10} {where}"
                  + (f"\n            last test: {c['message']} ({c['at'][:16]})" if c else ""))
        print(f"Talk uses: {', '.join(st['active']) or 'browser speech (no Whisper tier)'}")
    elif a.action == "backlog":  # re-transcribe stored recordings that had no tier when they arrived
        from . import voiceback
        print(voiceback.retry(con))
    elif not a.tier or (a.action == "set" and not a.url):
        raise SystemExit("Usage: hq voice set gpu|cloud <url> · test gpu|pi|cloud · remove gpu|cloud")
    elif a.action == "test":
        print(voicesetup.test(con, a.tier)["message"])
    else:  # set / remove; the key is read without echo so it never lands in shell history
        key = ""
        if a.action == "set" and a.tier == "cloud":
            import getpass
            key = getpass.getpass("API key (empty keeps the saved one): ")
        try:
            print(voicesetup.save(con, a.tier, a.url if a.action == "set" else "", key))
        except ValueError as e:
            raise SystemExit(str(e))


def cmd_plan(a):
    from . import plan
    print(plan.text(plan.tree(_con()), tasks_per_goal=a.tasks))


def cmd_link_task(a):
    from . import plan
    try:
        plan.link_task(_con(), a.task, a.goal or None)
    except ValueError as e:
        sys.exit(str(e))
    print(f"task #{a.task} → goal #{a.goal}" if a.goal else f"task #{a.task} unlinked")


def cmd_nest(a):
    from . import plan
    try:
        plan.set_parent(_con(), a.goal, a.parent or None)
    except ValueError as e:
        sys.exit(str(e))
    print(f"goal #{a.goal} → under goal #{a.parent}" if a.parent else f"goal #{a.goal} → top level")


def cmd_update(a):
    from . import updater
    con = _con()
    if a.auto:
        msg = updater.auto(con)
        from . import delivery
        delivery.sync(con, remote=False)  # merged PRs whose commit just got installed → installed, at once
        if msg:
            print(msg)
            from . import devloop
            devloop.notify(msg)
        return
    if a.check:
        info = updater.check()
        print(f"Current: {info['current']['sha']} {info['current']['date']} {info['current']['subject']}")
        print(f"{info['behind']} new change(s) on GitHub" + "".join(f"\n  • {c['subject']}" for c in info["commits"]))
        if info["blocker"]:
            print("Blocked: " + info["blocker"])
        return
    print(updater.apply(con)["message"])
    from . import delivery
    delivery.sync(con, remote=False)


def cmd_delivery(a):
    """Pull requests from ready to merge through installed; 'already done?' suggestions."""
    from . import delivery
    con = _con()
    if a.action == "sync":
        out = delivery.sync(con)
        if not a.quiet or out["moved"] or not out["ok"]:
            print(delivery.text(out))
        return
    if a.action == "decide":
        print(delivery.decide(con, int(a.n), a.answer))
        return
    t = delivery.today(con)
    for key, head in (("review", "Ready to merge"), ("install", "Merged, waiting for install"), ("installed", "Installed recently")):
        for p in t[key]:
            print(f"{head}: PR #{p['number']} {p['title']}" + (f" (tasks {p['task_ids']})" if p["task_ids"] else "") + f"  {p['url']}")
    for c in t["suggestions"]:
        print(f"Already done? [{c['id']}] task #{c['task_id']} {c['title']}: {c['evidence']}  (hq delivery decide {c['id']} done|partial|not_related)")
    if not any(t[k] for k in ("review", "install", "installed", "suggestions")):
        print("Nothing waiting.")


def cmd_revisit(a):
    from . import revisit
    con = _con()
    if a.action == "run":
        out = revisit.run(con, force=not a.due)
        if not (a.due and out["status"] in ("not_due", "off", "nothing")):  # the daily cron stays quiet when nothing happens
            print(revisit.text(out))
    elif a.action == "features":
        feats = revisit.features(con)
        print("\n".join(f"#{f['id']} {f['title']} (built {str(f['built'])[:10]}"
                        + (f", last revisit {f['revisited'][:10]}" if f["revisited"] else "") + ")" for f in feats)
              or "No built feature is old enough to revisit.")
        print(f"Next revisit: {'due now' if revisit.due(con) else 'not due yet'} (every {revisit.cfg()['every_days']} days).")
    elif a.action == "list":
        print(revisit.show(con))
    elif a.action == "placement":  # review where Atlas's functions live now (RA-059)
        print(revisit.placement_text(revisit.placement(con)))
    elif not a.arg:
        sys.exit("Usage: hq revisit undo <run>  (hq revisit list)")
    else:
        print(revisit.undo(con, a.arg))


def cmd_revalidate(a):
    from . import revalidate
    con = _con()
    if a.action == "run":
        out = revalidate.run(con, force=not a.due)
        if not (a.due and out["status"] in ("not_due", "off", "quiet")):  # the daily cron stays quiet when nothing happens
            print(revalidate.text(out))
    elif a.action == "status":
        print(revalidate.status_text(con))
    elif a.action == "news":
        items = revalidate.news(con, limit=revalidate.cfg()["max_news"])
        print("\n".join(f"radar #{n['id']} [{n['component']}] {n['title']}" for n in items)
              or "No news about the tools and models Atlas uses since the last revalidation.")
    elif a.action == "list":
        print(revalidate.show(con))
    elif not a.arg:
        sys.exit("Usage: hq revalidate undo <run>  (hq revalidate list)")
    else:
        print(revalidate.undo(con, a.arg))


def cmd_worker(a):
    from . import workers
    if a.action == "run":  # on a remote worker: its own clone, no access to the hub's database
        from . import worker
        return worker.run(once=a.once)
    con = _con()
    try:
        if a.action == "list":
            print(workers.text(con))
        elif a.action == "add":
            print(workers.register(con, a.name, a.device, a.slots))
        elif a.action == "revoke":
            print(workers.revoke(con, a.name))
        elif a.name.isdigit():  # hq worker slots 2: the Pi
            print(workers.set_slots(con, int(a.name)))
        else:
            print(workers.set_slots(con, a.slots, a.name or workers.PI))
    except ValueError as e:
        sys.exit(str(e))


def cmd_parallel(a):
    from . import parallel
    con = _con()
    try:
        if a.action == "show":
            print(parallel.text(con))
        elif a.action == "accept":
            print(parallel.accept(con, a.ids or None))
        else:
            print(parallel.dismiss(con, a.ids or [t["id"] for t in parallel.suggest(con)["tasks"]]))
    except ValueError as e:
        sys.exit(str(e))


def cmd_overlap(a):
    from . import overlap
    con = _con()
    if a.action == "scan":
        out = overlap.scan(con, use_model=False if a.rule else None)
        if not (a.quiet and not (out["exists"] or out["partial"])):  # the daily cron stays quiet when all is new
            print(overlap.text(out))
    elif a.action == "check":
        if a.kind not in ("task", "idea") or not (a.id or "").isdigit():
            sys.exit("Usage: hq overlap check task|idea <id> [--rule]")
        try:
            print(overlap.line(overlap.check(con, a.kind, int(a.id), use_model=False if a.rule else None)))
        except ValueError as e:
            sys.exit(str(e))
    elif a.action == "decide":
        if not (a.kind or "").isdigit() or a.id not in ("build", "drop", "merge"):
            sys.exit("Usage: hq overlap decide <task id> build|drop|merge")
        try:
            print(overlap.decide(con, int(a.kind), a.id))
        except ValueError as e:
            sys.exit(str(e))
    else:
        rows = overlap.listing(con)
        print("\n".join(f"{c['kind']} #{c['item_id']} {c['title']}\n   {overlap.line(c)}" for c in rows)
              or "No open task or idea overlaps with what Atlas already has (as far as checked: hq overlap scan).")


def cmd_doover(a):
    from . import doover
    con = _con()
    try:
        if a.action == "propose":
            if not a.arg:
                sys.exit("Usage: hq doover propose <file.json>  (see engine/doover.py for the format)")
            data = json.loads(Path(a.arg).read_text())
            aid = doover.propose(con, data["items"], data.get("summary", ""))
            print(doover.show(con, aid))
            print(f"\nWaiting for approval #{aid}; once approved, the next hourly cycle applies it.")
        elif a.action == "replan":
            print(doover.replan_text(doover.replan(con)))
        elif a.action == "list":
            for r in con.execute("SELECT key FROM state WHERE key LIKE 'doover:%' ORDER BY key"):
                print(doover.show(con, int(r[0].split(":", 1)[1])).splitlines()[0])
        elif not a.arg:
            sys.exit(f"Usage: hq doover {a.action} <approval id>  (hq doover list)")
        elif a.action == "show":
            print(doover.show(con, int(a.arg)))
        elif a.action == "apply":
            print(doover.apply(con, int(a.arg)))
        elif a.action == "undo":
            print(doover.undo(con, int(a.arg)))
    except ValueError as e:
        sys.exit(str(e))


def cmd_brain(a):
    from . import brain
    con = _con()
    if a.action == "think":  # daily cron; one path with Forge › Plan (#124): brain re-ranking, then a do-over only on drift
        from . import doover
        print(doover.replan_text(doover.replan(con, force=False)))
        print(brain.text(con))
    elif a.action == "sort":
        print(f"{brain.sort_pending(con)} thought(s) sorted.")
        from . import placement
        r = placement.refresh(con, use_model=True, limit=5)  # hourly: Claude suggests where new ideas fit in the plan
        print(f"{r['ai']} idea(s) placed by Claude." if r["ai"] else "Idea places: rule only" + (f" ({r['error']})" if r["error"] else "") + ".")
    elif a.action == "log":
        for c in brain.changes(con, 30):
            print(f"[{c['id']}] {c['ts'][5:16].replace('T', ' ')} {'(undone) ' if c['undone'] else ''}"
                  f"{brain.describe_change(c)} – {c['reason']}")
    elif a.action == "undo":
        if not a.id:
            sys.exit("Usage: hq brain undo <change number> (see: hq brain log)")
        try:
            print(brain.undo(con, a.id))
        except ValueError as e:
            sys.exit(str(e))
    elif a.action == "thoughts":
        for t in brain.thoughts(con, 30):
            print(f"#{t['id']} {t['created'][5:16].replace('T', ' ')} [{t['status']}] {t['text'][:80]}  → {t['result'][:80]}")
    else:
        print(brain.text(con))


def cmd_promote(a):
    from . import brain
    try:
        tid = brain.promote_idea(_con(), a.idea, "ai" if a.ai else "me")
    except ValueError as e:
        sys.exit(str(e))
    print(f"idea #{a.idea} → task #{tid}" + (" for the AI agent" if a.ai else ""))


def cmd_tree(a):
    from . import skilltree
    con = _con()
    try:
        if a.action == "focus":
            print(skilltree.set_focus(con, a.args[0] if a.args else ""))
        elif a.action == "link":   # hq tree link <task> <capability>
            from . import edit
            print(edit.edit(con, "tasks", int(a.args[0]), {"capability_id": int(a.args[1])}))
        elif a.action == "autolink":  # hq tree autolink [--dry | --undo]: Claude links unlinked tasks to nodes
            from . import autolink
            print(autolink.undo_last(con) if a.undo else autolink.text(autolink.run(con, dry=a.dry)))
        elif a.action == "draft":  # hq tree draft <capability>: Queue this, as a draft task to edit
            tid = skilltree.draft_task(con, int(a.args[0]))
            print(f"Draft task #{tid} for this node. Edit it on the dashboard (Plan → skill tree), then `hq tree queue {tid}`.")
        elif a.action == "research":  # hq tree research <capability>: ways to build it, compared by Claude
            from . import research
            print(research.text(research.load(con, research.research(con, int(a.args[0])))))
        elif a.action == "pick":  # hq tree pick <research> <option number>: that option becomes a draft task
            from . import research
            tid = research.pick(con, int(a.args[0]), int(a.args[1]) - 1)
            print(f"Draft task #{tid}. Edit it on the dashboard (Plan → skill tree), then `hq tree queue {tid}`.")
        elif a.action == "queue":  # hq tree queue <task>: a draft (or any task) into your build queue
            print(skilltree.queue_draft(con, int(a.args[0])))
        elif a.action in ("ok", "no"):
            print(skilltree.decide(con, int(a.args[0]), a.action == "ok"))
        else:
            print(skilltree.text(con))
    except (ValueError, IndexError) as e:
        sys.exit(str(e) or "Usage: hq tree [focus plan|balanced|ideas | link <task> <capability> | autolink [--dry|--undo] | draft <capability> | research <capability> | pick <research> <n> | queue <task> | ok <task> | no <task>]")


def cmd_link(a):
    from . import envfile
    _, created = envfile.ensure_token()
    if created:
        print("(created a new dashboard token in .env; restart the dashboard: stop the running one, then ./atlas.sh serve)")
    print(envfile.dashboard_link(port=a.port))


def cmd_gate(a):
    from . import gatekeeper
    if a.check:
        print(gatekeeper.decide("Bash", {"command": " ".join(a.check)}))
    else:
        gatekeeper.hook_main()


def cmd_ask(a):
    from . import gatekeeper
    opts = a.options.split("|") if a.options else None
    print(gatekeeper.request_approval(_con(), " ".join(a.request), a.reason, a.category, a.cost, wait=a.wait, options=opts))


def cmd_unfinished(a):
    from . import resume
    con = _con()
    try:
        if a.action == "scan":
            r = resume.scan(con)
            print(f"Checked {r['checked']} changed session log(s): {r['opened']} stopped by a usage limit, {r['resolved']} resumed.")
        elif a.action in ("done", "dismiss"):
            print(resume.finish(con, int(a.n), "resolved" if a.action == "done" else "dismissed"))
        else:
            print(resume.text(con))
    except (ValueError, TypeError) as e:
        raise SystemExit(str(e) or "Give the item number, e.g. hq unfinished done 2")


def cmd_sandbox(a):
    from . import sandbox
    con = _con()
    try:
        if a.action == "check":
            r = sandbox.selfcheck(con)
            print(f"Sandbox self-check #{r['id']}: {r['status']} (image {r['image']})")
            for _, ok, why in r["checks"]:
                print(f"  {'ok  ' if ok else 'FAIL'} {why}")
            for p in r["problems"]:
                print(f"  {'refused' if r['status'] == 'refused' else 'WARN'}: {p}")
            if not r["checks"] and r["output"]:
                print(r["output"][-600:])
        elif a.action == "run":
            if len(a.args) < 2:
                raise ValueError("Usage: hq sandbox run <folder> [--task N] [--skill N] [--origin URL] -- <command>")
            r = sandbox.run(con, a.args[0], " ".join(a.args[1:]), task_id=a.task, skill_id=a.skill, origin=a.origin,
                            image=a.image)
            print(sandbox.text_run(r))
        elif a.action == "show":
            print(sandbox.text_run(sandbox._get(con, int(a.args[0]))))
        else:
            print(sandbox.text(con))
    except (ValueError, TypeError, IndexError) as e:
        raise SystemExit(str(e) or "Give the run number, e.g. hq sandbox show 3")


def cmd_cmd(a):
    from . import commands
    con = _con()
    try:
        if a.action == "add":
            if not a.title or not a.command:
                raise ValueError('Usage: hq cmd add "what it does" --command "git push -u origin fix" [--why "…"] [--cwd ~/repo]')
            cid = commands.add(con, " ".join(a.title), a.command, a.why, a.cwd, a.source)
            print(f"Command #{cid} is waiting on Today under Run in your terminal.")
        elif a.action in ("done", "dismiss"):
            print(commands.finish(con, int(a.title[0]), "done" if a.action == "done" else "dismissed"))
        else:
            print(commands.text(con))
    except (ValueError, IndexError) as e:
        raise SystemExit(str(e) or "Give the command number, e.g. hq cmd done 3")


def cmd_needs(a):
    from . import loop, needs
    con = _con()
    if a.action == "list":
        print(needs.render(needs.items(con)))
        return
    if not a.args:
        raise SystemExit("Usage: hq needs answer D12 \"your answer\" (or --approve / --deny for A-items)")
    verdict = "approved" if a.approve else "denied" if a.deny else ""
    try:
        print(needs.answer_key(con, a.args[0], " ".join(a.args[1:]), verdict, "cli"))
    except ValueError as e:
        raise SystemExit(str(e))
    loop.run_cycle(con)


def cmd_progress(a):
    from . import progress
    print(progress.summary(progress.stats(_con())))


def cmd_direction(a):
    from . import direction
    r = direction.rollup(_con())
    print(direction.as_json(r) if a.json else direction.text(r, a.n))


def cmd_review(a):
    from . import progress
    con = _con()
    text = progress.review(con, save=not a.dry_run)
    print(text)
    if a.send and os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        from .interfaces import telegram
        telegram.from_env(con).send(text)


def cmd_intake(a):
    from . import intake
    con = _con()
    if a.retry:
        print(intake.retry(con))
        return
    if a.pending or not a.item:
        rows = intake.pending(con)
        print("\n".join(f"#{r['id']} [{r['kind']}] {r['ref']}  ({r['summary']})" for r in rows) or "Nothing pending.")
        return
    note = a.note or ""
    if a.item.startswith(("http://", "https://")):
        row = intake.add_link(con, a.item, note)
    elif os.path.isfile(a.item):
        row = intake.add_file(con, intake.store_copy(a.item), note)  # a copy, so the original can be moved
    else:
        sys.exit("Give a link (http/https) or the path of an existing file.")
    print(intake.describe(row))
    loop.run_cycle(con)


def cmd_bot(a):
    from .interfaces import telegram
    if a.whoami:
        print(telegram.whoami())
        return
    bot = telegram.from_env(_con())
    if a.send_brief:
        bot.send(bot.cmd_brief())
    elif a.once:
        print(f"{bot.poll_once(timeout=0)} update(s) handled")
    else:
        print(f"Atlas Telegram bot running for chat {bot.chat_id} (Ctrl+C to stop)")
        bot.run()


def main(argv=None):
    p = argparse.ArgumentParser(prog="atlas", description="Atlas — personal management system")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("init", help="create the memory database"); x.add_argument("--seed", action="store_true"); x.set_defaults(f=cmd_init)
    x = s.add_parser("next", help="what to do next"); x.add_argument("-n", type=int, default=5); x.add_argument("--owner", choices=["me", "ai"]); x.set_defaults(f=cmd_next)

    x = s.add_parser("add", help="add task | idea | goal | resource | memory")
    x.add_argument("kind", choices=["task", "idea", "goal", "resource", "memory"]); x.add_argument("text")
    x.add_argument("--impact", type=int, default=3); x.add_argument("--effort", type=float, default=1)
    x.add_argument("--due"); x.add_argument("--owner", choices=["me", "ai", "both"]); x.add_argument("--phase", type=int, default=0)
    x.add_argument("--after", help="comma-separated task ids"); x.add_argument("--goal", type=int)
    x.add_argument("--category", help="idea category / resource kind / memory kind"); x.add_argument("--cost", type=float, default=0)
    x.add_argument("--horizon", help="goals: life | year | quarter | month | week"); x.add_argument("--parent", type=int, help="goals: the bigger goal this one belongs to")
    x.add_argument("--details"); x.add_argument("--value", type=float); x.add_argument("--unit"); x.add_argument("--tags")
    x.set_defaults(f=cmd_add)

    x = s.add_parser("inbox", help="capture anything; 'todo:', 'idea:', 'goal:' prefixes route it"); x.add_argument("text", nargs="+"); x.set_defaults(f=cmd_inbox)
    x = s.add_parser("status", help="set a task's status"); x.add_argument("id", type=int)
    x.add_argument("status", choices=["todo", "doing", "done", "dropped"]); x.set_defaults(f=cmd_status)
    x = s.add_parser("run", help="hand a task to the agent (first in its queue)"); x.add_argument("id", type=int); x.set_defaults(f=cmd_run)
    x = s.add_parser("move", help="move a task up or down in the queue (auto: back to the computed ranking)"); x.add_argument("id", type=int)
    x.add_argument("direction", choices=["up", "down", "auto"]); x.set_defaults(f=cmd_move)
    x = s.add_parser("delete", help="discard a task as a suggestion (set aside, undoable)"); x.add_argument("id", type=int); x.set_defaults(f=cmd_delete)
    x = s.add_parser("done", help="agent only: mark a task done after implementing it"); x.add_argument("id", type=int); x.set_defaults(f=cmd_done)
    x = s.add_parser("accept", help="accept a task Atlas proposed"); x.add_argument("id", type=int); x.set_defaults(f=cmd_accept)

    x = s.add_parser("approvals", help="list, or: approvals <id> approved|denied")
    x.add_argument("id", type=int, nargs="?"); x.add_argument("decision", nargs="?", choices=["approved", "denied"]); x.set_defaults(f=cmd_approvals)

    x = s.add_parser("recall", help="search memory"); x.add_argument("query", nargs="+"); x.add_argument("-n", type=int, default=10); x.set_defaults(f=cmd_recall)
    s.add_parser("resources", help="show resource map").set_defaults(f=cmd_resources)
    x = s.add_parser("ideas", help="open ideas (--all: also those that became tasks)")
    x.add_argument("--all", action="store_true"); x.set_defaults(f=cmd_ideas)
    s.add_parser("flow", help="where things go: thought → task or idea → task").set_defaults(f=cmd_flow)
    x = s.add_parser("list", help="every task and idea with ids and what blocks what; --all includes done")
    x.add_argument("--all", action="store_true", help="include done tasks")
    x.add_argument("--status", help="comma-separated task statuses and/or idea gates, e.g. todo,blocked,done or idea,test")
    x.add_argument("--goal", type=int, help="goal id (sub-goals included); 0 = tasks without a goal")
    x.add_argument("--due", help="overdue | today | week | none | YYYY-MM-DD (due on or before)")
    x.add_argument("--tasks", action="store_true", help="tasks only"); x.add_argument("--ideas", action="store_true", help="ideas only")
    x.set_defaults(f=cmd_list)
    x = s.add_parser("cycle", help="run one loop cycle"); x.add_argument("--think", action="store_true", help="let Claude propose (needs ANTHROPIC_API_KEY)"); x.set_defaults(f=cmd_cycle)
    s.add_parser("brief", help="today's brief").set_defaults(f=cmd_brief)
    x = s.add_parser("log", help="audit trail"); x.add_argument("-n", type=int, default=20); x.set_defaults(f=cmd_log)

    x = s.add_parser("dump", help="brain dump: free text in, plan updates out (or pipe text in)"); x.add_argument("text", nargs="*"); x.set_defaults(f=cmd_dump)
    x = s.add_parser("radar", help="new developments: list | scan | star|dismiss|idea <id>")
    x.add_argument("action", nargs="?", default="list", choices=["list", "scan", "star", "dismiss", "idea"])
    x.add_argument("id", type=int, nargs="?"); x.add_argument("--sources", nargs="*"); x.add_argument("-n", type=int, default=25)
    x.add_argument("--due", action="store_true", help="scan only if the interval under Research → Sources & limits has passed"); x.set_defaults(f=cmd_radar)
    x = s.add_parser("research", help='deep research on demand: ask "question" | list | idea <note id>')
    x.add_argument("action", nargs="?", default="list", choices=["ask", "list", "idea"]); x.add_argument("words", nargs="*"); x.set_defaults(f=cmd_research)
    x = s.add_parser("backup", help="private encrypted backup of the data: run | status | restore <file>")
    x.add_argument("action", nargs="?", default="run", choices=["run", "status", "restore"]); x.add_argument("file", nargs="?"); x.set_defaults(f=cmd_backup)
    x = s.add_parser("arsenal", help="what you've built: list | add <folder> | scan | find <words>")
    x.add_argument("action", nargs="?", default="list", choices=["list", "add", "scan", "find"])
    x.add_argument("arg", nargs="?"); x.add_argument("--name"); x.add_argument("-n", type=int, default=15); x.set_defaults(f=cmd_arsenal)
    x = s.add_parser("serve", help="open the dashboard"); x.add_argument("--port", type=int, default=int(os.environ.get("HQ_PORT") or 8765))
    x.add_argument("--host", default=os.environ.get("HQ_HOST", "127.0.0.1"), help="e.g. the Pi's Tailscale IP (requires HQ_TOKEN); default from HQ_HOST"); x.set_defaults(f=cmd_serve)
    x = s.add_parser("dev", help="dev loop: Claude Code on the Pi builds the top AI task and opens a PR (off until enabled)")
    x.add_argument("action", nargs="?", default="status",
                   choices=["run", "status", "lanes", "pause", "resume", "stop", "batch", "limit", "conflicts", "resolve",
                            "off", "ask", "limits", "continuous"])
    x.add_argument("n", nargs="?", help="with batch: stop for review after this many open PRs (0 or unlimited = never); with resolve: the run id; "
                   "with limit: runs, tokens or usd")
    x.add_argument("value", nargs="?", type=float, help="with limit: the daily limit (tokens/usd: 0 = no limit)")
    x.add_argument("--task", type=int, help="work on this task instead of the top one")
    x.add_argument("--force", action="store_true", help="run now even if off, paused or over the daily limit (not if killed)"); x.set_defaults(f=cmd_dev)
    x = s.add_parser("route", help="which model the dev loop would use for a task, and why (default: the next AI task)")
    x.add_argument("id", nargs="?", type=int); x.set_defaults(f=cmd_route)
    x = s.add_parser("kill", help="KILL SWITCH: stop the agent now, block new runs and paid model calls"); x.set_defaults(f=cmd_kill)
    x = s.add_parser("unkill", help="turn the kill switch off again"); x.set_defaults(f=cmd_unkill)
    x = s.add_parser("usage", help="tokens and estimated cost: agent runs and Atlas's own model calls"); x.set_defaults(f=cmd_usage)
    x = s.add_parser("voice", help="Whisper setup: status | set gpu|cloud <url> | test gpu|pi|cloud | remove gpu|cloud | backlog")
    x.add_argument("action", choices=["status", "set", "test", "remove", "backlog"], nargs="?", default="status")
    x.add_argument("tier", nargs="?", choices=["gpu", "cloud"]); x.add_argument("url", nargs="?", default="")
    x.set_defaults(f=cmd_voice)
    x = s.add_parser("plan", help="the plan as a tree: mission → goals → tasks, with progress")
    x.add_argument("--tasks", type=int, default=4, help="open tasks shown per goal"); x.set_defaults(f=cmd_plan)
    x = s.add_parser("attach", help="link a task to a goal (goal 0 = unlink)")
    x.add_argument("task", type=int); x.add_argument("goal", type=int); x.set_defaults(f=cmd_link_task)
    x = s.add_parser("nest", help="put a goal under a bigger goal (parent 0 = top level)")
    x.add_argument("goal", type=int); x.add_argument("parent", type=int); x.set_defaults(f=cmd_nest)
    x = s.add_parser("update", help="install merged work from GitHub (tests first) and restart the dashboard and bot")
    x.add_argument("--check", action="store_true", help="only show what is new")
    x.add_argument("--auto", action="store_true", help="for cron: quiet, notifies on Telegram when it updates or is blocked"); x.set_defaults(f=cmd_update)
    x = s.add_parser("brain", help="the brain: focus and recommendations, re-evaluate (think), change log, undo")
    x.add_argument("action", nargs="?", default="status", choices=["status", "think", "sort", "log", "undo", "thoughts"])
    x.add_argument("id", nargs="?", type=int, help="change number for undo"); x.set_defaults(f=cmd_brain)
    x = s.add_parser("doover", help="task do-over: a keep/change/done/drop/merge/new diff of the plan, applied after approval; replan: re-evaluate, draft one if the plan drifted")
    x.add_argument("action", choices=["list", "show", "propose", "apply", "undo", "replan"])
    x.add_argument("arg", nargs="?", help="approval id, or the JSON file for propose"); x.set_defaults(f=cmd_doover)
    x = s.add_parser("revisit", help="review features built earlier: keep/improve/merge/remove; changes become draft tasks")
    x.add_argument("action", choices=["run", "features", "list", "placement", "undo"])
    x.add_argument("arg", nargs="?", help="undo: the revisit run (hq revisit list)")
    x.add_argument("--due", action="store_true", help="for cron: only when [revisit] says one is due"); x.set_defaults(f=cmd_revisit)
    x = s.add_parser("revalidate", help="recheck the tools and models Atlas uses against their releases; changes become drafts and approvals")
    x.add_argument("action", choices=["status", "news", "run", "list", "undo"])
    x.add_argument("arg", nargs="?", help="undo: the run (hq revalidate list)")
    x.add_argument("--due", action="store_true", help="for cron: only when [revalidate] says one is due"); x.set_defaults(f=cmd_revalidate)
    x = s.add_parser("worker", help="devices that build: list, add (asks your approval), revoke, slots N [name], run (on the worker)")
    x.add_argument("action", nargs="?", default="list", choices=["list", "add", "revoke", "slots", "run"])
    x.add_argument("name", nargs="?", default="", help="add/revoke/slots: the worker's name, e.g. zenunit")
    x.add_argument("--device", default="", help="add: what it is, e.g. 'ZenUnit PC (WSL2)'")
    x.add_argument("--slots", type=int, default=1, help="add/slots: builds at once")
    x.add_argument("--once", action="store_true", help="run: one claim only"); x.set_defaults(f=cmd_worker)
    x = s.add_parser("parallel", help="AI tasks that can be built side by side (no blockers, no shared files): show, accept or dismiss the batch")
    x.add_argument("action", nargs="?", default="show", choices=["show", "accept", "dismiss"])
    x.add_argument("ids", nargs="*", type=int, help="accept/dismiss: the batch's task ids (default: the batch proposed now)")
    x.set_defaults(f=cmd_parallel)
    x = s.add_parser("overlap", help="does a task or idea already exist in Atlas or the arsenal? exists | partial | new, with why")
    x.add_argument("action", nargs="?", default="list", choices=["list", "scan", "check", "decide"])
    x.add_argument("kind", nargs="?", help="check: task|idea · decide: the task id")
    x.add_argument("id", nargs="?", help="check: the id · decide: build|drop|merge")
    x.add_argument("--rule", action="store_true", help="word-overlap rule only, no model call")
    x.add_argument("--quiet", action="store_true", help="for cron: print only when something overlaps"); x.set_defaults(f=cmd_overlap)
    for name in ("promote", "queue"):  # 'queue' is the older name
        x = s.add_parser(name, help="turn an idea into a task (--ai: for the AI agent)")
        x.add_argument("idea", type=int); x.add_argument("--ai", action="store_true"); x.set_defaults(f=cmd_promote)
    x = s.add_parser("tree", help="skill tree and build order; focus plan|balanced|ideas; link <task> <capability>; autolink [--dry|--undo]; draft <capability>; research <capability>; pick <research> <n>; queue <task>; ok|no <task>")
    x.add_argument("action", nargs="?", default="status", choices=["status", "focus", "link", "autolink", "draft", "research", "pick", "queue", "ok", "no"])
    x.add_argument("args", nargs="*")
    x.add_argument("--dry", action="store_true", help="autolink: show Claude's links without saving them")
    x.add_argument("--undo", action="store_true", help="autolink: undo the last auto-link run"); x.set_defaults(f=cmd_tree)
    x = s.add_parser("link", help="print the dashboard link (creates the token in .env if it is missing)")
    x.add_argument("--port", type=int, help="if the dashboard runs on another port than 8765 (or set HQ_PORT)"); x.set_defaults(f=cmd_link)
    x = s.add_parser("gate", help="Claude Code PreToolUse hook (reads stdin); or --check <command> to test a rule")
    x.add_argument("--check", nargs="+"); x.set_defaults(f=cmd_gate)
    x = s.add_parser("ask", help="request approval from Djeddi's phone, e.g. from a script")
    x.add_argument("request", nargs="+"); x.add_argument("--reason", default=""); x.add_argument("--category", default="action")
    x.add_argument("--cost", type=float, default=0); x.add_argument("--wait", type=int, default=0, help="seconds to wait for the answer")
    x.add_argument("--options", help='a decision: up to 3 choices separated by |, e.g. "Telegram|WhatsApp|Wait"'); x.set_defaults(f=cmd_ask)

    x = s.add_parser("delivery", help="pull requests: list | sync (from GitHub, no model calls) | decide N done|partial|not_related")
    x.add_argument("action", nargs="?", default="list", choices=["list", "sync", "decide"]); x.add_argument("n", nargs="?")
    x.add_argument("answer", nargs="?", default=""); x.add_argument("--quiet", action="store_true"); x.set_defaults(f=cmd_delivery)
    x = s.add_parser("unfinished", help="work a usage limit stopped (Claude Code, Codex, dev loop): list | scan | done N | dismiss N")
    x.add_argument("action", nargs="?", default="list", choices=["list", "scan", "done", "dismiss"]); x.add_argument("n", nargs="?")
    x.set_defaults(f=cmd_unfinished)
    x = s.add_parser("sandbox", help="test outside code in Docker without network or credentials: list | check | run <folder> [--task N] -- <command> | show N")
    x.add_argument("action", nargs="?", default="list", choices=["list", "check", "run", "show"]); x.add_argument("args", nargs="*")
    x.add_argument("--task", type=int); x.add_argument("--skill", type=int); x.add_argument("--origin", default="")
    x.add_argument("--image"); x.set_defaults(f=cmd_sandbox)
    x = s.add_parser("cmd", help="steps left for your terminal: list | add \"what\" --command \"…\" --why \"…\" --cwd DIR | done N | dismiss N")
    x.add_argument("action", nargs="?", default="list", choices=["list", "add", "done", "dismiss"]); x.add_argument("title", nargs="*")
    x.add_argument("--command", default=""); x.add_argument("--why", default=""); x.add_argument("--cwd", default="")
    x.add_argument("--source", default="claude-code"); x.set_defaults(f=cmd_cmd)
    x = s.add_parser("needs", help="what needs your input: decisions, approvals, your tasks the agent waits for; answer D12 \"text\"")
    x.add_argument("action", nargs="?", default="list", choices=["list", "answer"]); x.add_argument("args", nargs="*")
    x.add_argument("--approve", action="store_true"); x.add_argument("--deny", action="store_true"); x.set_defaults(f=cmd_needs)
    x = s.add_parser("progress", help="how the plan is moving: this week, streak, goals, overdue"); x.set_defaults(f=cmd_progress)
    x = s.add_parser("direction", help="where the plan is heading: queue → skill tree nodes → goals, and what to steer")
    x.add_argument("-n", type=int, default=8); x.add_argument("--json", action="store_true"); x.set_defaults(f=cmd_direction)
    x = s.add_parser("review", help="weekly review: done, goals, slipped, focus; saved to memory")
    x.add_argument("--send", action="store_true", help="also send it to Telegram (if configured)")
    x.add_argument("--dry-run", action="store_true", help="print only, do not save"); x.set_defaults(f=cmd_review)

    x = s.add_parser("intake", help="save a link, screenshot or file into memory (content is summarised, never obeyed)")
    x.add_argument("item", nargs="?", help="a link or a file path")
    x.add_argument("--note", help="what it is for, in your words")
    x.add_argument("--pending", action="store_true", help="list items waiting to be read")
    x.add_argument("--retry", action="store_true", help="read images and scanned PDFs that were waiting for a vision model"); x.set_defaults(f=cmd_intake)

    x = s.add_parser("bot", help="Telegram bot (long-polling); needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    x.add_argument("--once", action="store_true", help="handle waiting messages once and exit")
    x.add_argument("--whoami", action="store_true", help="show chat ids that messaged the bot, to fill in TELEGRAM_CHAT_ID")
    x.add_argument("--send-brief", action="store_true", help="send today's brief to Telegram and exit"); x.set_defaults(f=cmd_bot)

    a = p.parse_args(argv)
    from . import models, usage
    models.on_usage = usage.recorder()  # every model call is recorded in model_usage
    a.f(a)


if __name__ == "__main__":
    main()
