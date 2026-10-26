"""Atlas dashboard: `hq serve` opens a clean local web page over the memory database.

GET  /            the page (engine/dashboard.html)
GET  /api/data    everything the page shows, as JSON
GET  /api/run?run=12&after=40  – one dev run's live log lines after line 40, for the Build tab's console
GET  /api/overview?status=todo,idea&goal=3&due=week&kind=tasks&all=1  – every task and idea, filtered (hq.overview)
POST /api/action  {"op": ..., "id": ..., "text": ...} – the same actions as the CLI
POST /api/action  {"op": "idea_plan", "id": 4, "text": "<goal id>"} | {"op": "idea_goal", "id": 4} | {"op": "place_ideas"} – Ideas → Plan (hq.placement)
POST /api/action  {"op": "deep_research", "text": "question", "id": radar id?} | {"op": "note_idea", "id": 3} | {"op": "research_settings", "text": "{...}"} – Research
POST /api/action  {"op": "intr_done"|"intr_dismiss", "id": 2} – work stopped by a usage limit (hq.resume)
POST /api/action  {"op": "cmd_done"|"cmd_dismiss", "id": 3} – Run in your terminal (hq.commands)
POST /api/action  {"op": "needs_answer", "text": "{\"key\": \"D12\", \"text\": ..., \"verdict\": ...}"} – Needs your input (hq.needs)
POST /api/action  {"op": "voice_save" | "voice_test", ...} – Whisper setup on the System tab (hq.voicesetup)
POST /api/voice?lang=nl  raw audio recorded by the Talk button → {"text": transcript} (models.transcribe)

Binds to 127.0.0.1 by default. To reach it from your phone, bind to the Pi's Tailscale address
(`hq serve --host 100.x.y.z`) and set HQ_TOKEN: API calls then require the token (open the page once
as /?t=<token>; it is remembered). Never expose it to the open internet.
"""
import errno
import hmac
import json
import os
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import (arsenal, brain, budget, chat, db, delivery, devloop, doover, dump, edit, flow, loop, models, overview, plan, prereq, priority, progress, radar, research,
               needs, skilltree, updater, usage, voicesetup, claudeauth, quickbuild, guides, recovery, taskdetail, commands, placement, resume, deepresearch, overlap)

PAGE = Path(__file__).with_name("dashboard.html")
MAX_VOICE_BYTES = 25 * 1024 * 1024


def rows(con, q, *a):
    return [dict(r) for r in con.execute(q, a).fetchall()]


def _research(con) -> dict:
    return radar.curated(con)


def _ideas(con) -> list[dict]:
    """Ideas for later, each open one with where it fits in the plan (hq.placement; the rule fills gaps instantly)."""
    placement.refresh(con)
    where = placement.places(con)
    checks = {r["item_id"]: overlap.get(con, "idea", r["item_id"]) for r in con.execute(
        "SELECT item_id FROM overlap_checks WHERE kind = 'idea' AND verdict != 'new'")}
    return [dict(i) | {"place": where.get(i["id"]), "overlap": checks.get(i["id"])} for i in con.execute(
        "SELECT * FROM ideas WHERE gate != 'dropped' ORDER BY gate = 'idea' DESC, score DESC LIMIT 40")]


def _constitution(rules: dict) -> dict:
    """What the agent believes it should do and may do: the readable part of config/constitution.toml (no secrets live there)."""
    ident, auto, money = rules.get("identity", {}), rules.get("autonomy", {}), rules.get("money", {})
    return {"owner": ident.get("owner", ""), "mission": ident.get("mission", ""), "phase": ident.get("current_phase", 0),
            "allowed": auto.get("allowed", []), "requires_approval": auto.get("requires_approval", []), "never": auto.get("never", []),
            "money": {k: money.get(k, 0) for k in ("auto_spend_limit_eur", "monthly_budget_eur", "loss_stop_eur")}}


def build_data(con) -> dict:
    rules = loop.load_constitution()
    phase = rules["identity"].get("current_phase", 0)
    priority.rescore(con, rules.get("priority"), current_phase=phase)
    phases = rows(con, """SELECT phase, COUNT(*) total, SUM(status='done') done, MIN(due) start, MAX(due) finish
                          FROM tasks WHERE status != 'dropped' GROUP BY phase ORDER BY phase""")
    return {
        "recovery": recovery.overview(con),
        "management_focus": db.get_state(con, "management_focus", ""),
        "guides": guides.GUIDES,
        "today": date.today().isoformat(),
        "phase": phase,
        "mission": rules["identity"]["mission"],
        "next_me": [dict(r) for r in priority.next_tasks(con, 5, owner="me")],
        "next_ai": [dict(r) for r in priority.next_tasks(con, 4, owner="ai")],
        "approvals": [a | {"detail": _doover_detail(con, a["id"])}
                      for a in rows(con, "SELECT * FROM approvals WHERE status='pending' ORDER BY created")],
        "interruptions": resume.items(con),  # work a usage limit stopped: the very first thing on Today
        "commands": commands.items(con),  # steps left for Djeddi's terminal, shown first on Today
        "needs": [i | ({"detail": _doover_detail(con, i["id"])} if i["kind"] == "approval" else {}) for i in needs.items(con)],
        "proposed": rows(con, "SELECT * FROM tasks WHERE status='proposed' ORDER BY created DESC"),
        "radar": rows(con, """SELECT * FROM radar WHERE state != 'dismissed' AND found >= date('now','-7 day')
                              ORDER BY state='starred' DESC, score DESC LIMIT 60"""),
        "research": _research(con),
        "radar_last": (con.execute("SELECT MAX(ts) FROM log WHERE action='radar_scan'").fetchone()[0] or ""),
        "ideas": _ideas(con),
        "constitution": _constitution(rules),
        "update": updater.local_status(),
        "delivery": delivery.today(con),  # PRs ready to merge → merged → installed, kept current by cron, no scan needed
        "goals": rows(con, "SELECT * FROM goals WHERE status='active'"),
        "phases": phases,
        "tasks": rows(con, "SELECT id,title,phase,owner,status,due FROM tasks WHERE status != 'dropped' ORDER BY phase, due"),
        "resources": rows(con, "SELECT * FROM resources WHERE COALESCE(status, 'active') = 'active' ORDER BY kind, name"),
        "projects": rows(con, """SELECT p.name, p.description, p.scanned, COUNT(a.id) assets
                                 FROM projects p LEFT JOIN assets a ON a.project_id = p.id GROUP BY p.id ORDER BY p.name"""),
        "log": rows(con, "SELECT * FROM log ORDER BY id DESC LIMIT 12"),
        "progress": progress.stats(con),
        "review": progress.last_review(con),
        "plan": plan.tree(con),
        "chat": chat.history(con, 12),
        "brain": brain.focus(con) | {"changes": brain.changes(con, 15),
                                     "unsorted": con.execute("SELECT COUNT(*) FROM thoughts WHERE status = 'unsorted'").fetchone()[0]},
        "version": updater.version(),
        "agent": devloop.overview(con),
        "tree": skilltree.tree(con),
        "usage": usage.summary(con) | {"budgets": budget.status(con)},
        "voice": {"server": models.stt_available(), "language": rules.get("models", {}).get("stt_language", ""),
                  "setup": voicesetup.status(con)},
    }


def voice(con, audio: bytes, ctype: str, lang: str = "") -> str:
    """Store a Talk-button recording in memory/voice/ and return its transcript (the one voice path, talk.transcribe).
    Raises models.ModelUnavailable (the recording is kept and noted, as for Telegram voice notes)."""
    from . import talk
    return talk.transcribe(con, audio, ctype, lang, prefix="dash")


def _doover_detail(con, aid: int) -> str:
    """The full diff of a do-over approval, so it can be read before approving ("" for other approvals)."""
    try:
        return doover.render(con, doover.load(con, aid)["items"])
    except ValueError:
        return ""


def act(con, op: str, id_: int | None = None, text: str = "") -> str:
    if op == "report_browser_error":
        report = json.loads(text)
        line = int(report.get("line", 0))
        if line < 0 or line > 100000:
            raise ValueError("Invalid error location")
        recovery.record(con, "browser", report.get("type", "BrowserError"), f"dashboard.html:{line}")
        return "Error recorded for automatic repair."
    if op == "recovery_toggle":
        db.set_state(con, "recovery_enabled", "1" if text == "on" else "0")
        db.log(con, "me", "recovery_toggle", "on" if text == "on" else "off")
        con.commit()
        return "Automatic repairs " + ("on" if text == "on" else "off")
    if op == "build_now":
        request = json.loads(text)
        return json.dumps(quickbuild.start(con, request.get("prompt", ""), request.get("request_id", "")))
    if op.startswith("claude_auth_"):
        # Return before the generic action logger: codes and URLs must never be logged.
        if not os.environ.get("HQ_TOKEN"):
            raise ValueError("Set HQ_TOKEN before reconnecting Claude through the dashboard.")
        if op == "claude_auth_start":
            result = claudeauth.login.start()
        elif op == "claude_auth_status":
            return json.dumps(claudeauth.login.status())
        elif op == "claude_auth_check":
            return json.dumps(claudeauth.login.refresh())
        elif op == "claude_auth_cancel":
            result = claudeauth.login.cancel()
        elif op == "claude_auth_submit":
            request = json.loads(text)
            result = claudeauth.login.submit(request.get("session", ""), request.get("code", ""))
        else:
            raise ValueError("Unknown Claude connection action.")
        db.log(con, "me", op, "Claude connection")
        con.commit()
        return json.dumps(result)
    if op == "done":      # his own tasks only ("I did it"); the agent's tasks are refused (flow.confirm_done raises)
        return flow.confirm_done(con, id_, "marked done on the dashboard")
    elif op in ("delete", "dropped"):  # Delete: discard as a suggestion (undoable)
        msg = flow.discard(con, id_)
        priority.rerank(con, "task deleted", f"#{id_}")  # local re-rank only: the full cycle also polled Telegram, which made Delete slow
        return msg
    elif op == "run":     # Run: hand it to the agent; start building now if the dev loop is on and idle
        msg = flow.run(con, id_)
        if devloop.cfg()["enabled"] and not devloop.paused(con) and not devloop.why_not_now(con, id_):
            msg += " " + devloop.start_background(task_id=id_)
        priority.rerank(con, "task handed to the agent", f"#{id_}")
        return msg
    elif op in ("move_up", "move_down", "move_auto"):  # reorder the queue by hand
        msg = edit.move(con, id_, op[5:])
        priority.rerank(con, "task moved", f"#{id_}")
        return msg
    elif op in ("doing", "todo"):
        db.update(con, "tasks", id_, status=op)
    elif op == "accept":
        db.update(con, "tasks", id_, status="todo")
    elif op in ("approved", "denied"):
        con.execute("UPDATE approvals SET status=?, decided=? WHERE id=?", (op, db.now(), id_))
    elif op == "choose":  # a decision: text is the chosen option
        con.execute("UPDATE approvals SET status='answered', answer=?, decided=? WHERE id=? AND status='pending'", (text, db.now(), id_))
    elif op in ("intr_done", "intr_dismiss"):  # Stopped by a usage limit: finished after resuming, or not needed any more
        return resume.finish(con, id_, "resolved" if op == "intr_done" else "dismissed")
    elif op in ("cmd_done", "cmd_dismiss"):  # Run in your terminal: he ran it, or it is no longer needed
        return commands.finish(con, id_, "done" if op == "cmd_done" else "dismissed")
    elif op == "needs_answer":  # text: JSON {"key": "D12|A3|T5", "text": typed or spoken input, "verdict": "approved|denied"}
        req = json.loads(text or "{}")
        msg = needs.answer_key(con, str(req.get("key", "")), str(req.get("text", "")), str(req.get("verdict", "")), "dashboard")
        loop.run_cycle(con)
        return msg
    elif op == "tool_idea":  # Research → Tools: Adopt | Adapt | Build this (text) → an idea in Forge with that route
        if text not in radar.APPROACH:
            raise ValueError("Choose adopt, adapt or build.")
        iid = radar.set_state(con, id_, "idea", text)
        return f"{radar.APPROACH[text][0]} → idea #{iid} in Forge → Ideas"
    elif op in ("starred", "dismissed", "idea", "new"):
        radar.set_state(con, id_, op)
    elif op in ("dev_pause", "dev_resume"):
        devloop.set_paused(con, op == "dev_pause")
    elif op == "dev_stop":
        return devloop.stop(con)
    elif op == "dev_run":   # id: build this task now (from the build queue); none: the next one
        reason = devloop.why_not_now(con, id_)
        if reason:
            raise ValueError(reason)
        return devloop.start_background(task_id=id_)
    elif op == "dev_resolve":  # id: the run whose PR conflicts with main
        reason = devloop.why_not_resolve(con, id_)
        if reason:
            raise ValueError(reason)
        return devloop.start_resolve(id_)
    elif op == "dev_conflicts":  # Scan for conflicts: the open PRs, live on GitHub, against the latest main
        return devloop.scan(con)
    elif op == "node_confirm":  # Skill tree: he has this capability (text: the evidence ref, optional)
        return skilltree.confirm(con, id_, text)
    elif op == "node_proof_dismiss":  # a suggested proof that is not proof (text: its ref)
        return skilltree.dismiss(con, id_, text)
    elif op == "node_draft":  # Queue this on a skill tree node: a draft task to edit (its id)
        return str(skilltree.draft_task(con, id_))
    elif op == "node_research":  # Research on a node: ways to build it (JSON of the stored research)
        return json.dumps(research.load(con, research.research(con, id_)))
    elif op == "research_last":  # the latest research on a node, if any (JSON or "null")
        return json.dumps(research.latest(con, id_))
    elif op == "research_pick":  # id: research; text: option index → a draft task to edit (its id)
        return str(research.pick(con, id_, int(text)))
    elif op == "queue_draft":  # text: JSON of edited fields; then into his build queue
        return skilltree.queue_draft(con, id_, json.loads(text or "{}"))
    elif op == "queue_top":  # move one of his queued tasks to the front
        skilltree.queue(con, id_, mine=True, top=True)
        return "Moved to the front of your queue."
    elif op == "voice_save":  # text: JSON {"tier", "url", "key"}; returns before the generic log so the key is never logged
        req = json.loads(text or "{}")
        return voicesetup.save(con, req.get("tier", ""), req.get("url", ""), req.get("key", ""))
    elif op == "voice_test":  # text: the tier
        return json.dumps(voicesetup.test(con, text))
    elif op == "kill":
        return usage.kill(con, "dashboard")
    elif op == "unkill":
        return usage.unkill(con)
    elif op == "update_check":  # Scan for updates: also refresh the PR lifecycle right away
        info = updater.check()
        delivery.sync(con)
        return json.dumps(info)
    elif op == "reconcile":  # an 'already done?' suggestion; text: done | partial | not_related
        return delivery.decide(con, int(id_), text)
    elif op == "update_apply":
        return json.dumps(updater.apply(con))
    elif op == "ask":
        return json.dumps(chat.ask(con, text))
    elif op == "autonomy":
        return devloop.set_autonomy(con, text)
    elif op == "dev_limit":  # text: JSON {"kind": "runs"|"tokens"|"usd", "value": n}
        req = json.loads(text or "{}")
        return devloop.set_limit(con, req.get("kind", ""), req.get("value", 0))
    elif op == "batch":  # text: how many unreviewed PRs before the agent stops
        return devloop.set_batch(con, text)
    elif op == "task_owner":  # queue a task for the AI agent ("ai") or take it back ("me")
        if text not in ("me", "ai"):
            raise ValueError("owner must be me or ai")
        brain.set_field(con, "tasks", id_, "owner", text, "queued for the AI by Djeddi" if text == "ai" else "taken back by Djeddi")
        skilltree.queue(con, id_, mine=text == "ai")  # his own queue: built first, no approval needed
    elif op == "brain_dismiss":  # text: JSON task_ids of a suggestion Djeddi does not want
        ids = json.loads(text)
        f = brain.focus(con)
        f["suggest"] = [s for s in f.get("suggest", []) if s.get("task_ids") != ids]
        db.set_state(con, "brain_focus", json.dumps(f))
    elif op == "deep_research":  # text: the question; id: a feed item to start from (optional)
        n = deepresearch.ask(con, text, id_)
        return f"Research #{n['id']} " + ("is ready." if n["status"] == "done" else "could not reach a model; related items are listed.")
    elif op == "note_idea":  # a research note becomes an idea in Forge
        return f"Idea #{deepresearch.to_idea(con, id_)} added to Forge → Ideas."
    elif op == "research_settings":  # text: JSON {every_hours, per_source, sources, repos}
        return radar.save_settings(con, json.loads(text or "{}"))
    elif op == "idea_plan":   # Ideas → Plan: text = the goal id it goes under
        tid = placement.to_plan(con, id_, int(text))
        return f"Idea is now task #{tid} in your plan. Send it to Build when you want the agent to make it."
    elif op == "idea_goal":   # Ideas → Plan as a new goal; text: optional JSON {"title": ..., "why": ...}
        req = json.loads(text or "{}")
        gid, tid = placement.to_new_goal(con, id_, str(req.get("title", "")), str(req.get("why", "")))
        return f"New goal #{gid} started, with the idea as its first task #{tid}."
    elif op == "place_ideas":  # ask Claude where open ideas fit (the rule answers when no model is reachable)
        r = placement.refresh(con, use_model=True)
        return (f"Claude placed {r['ai']} idea(s)." if r["ai"] else
                f"Claude is not reachable ({r['error'] or 'no answer'}); the suggestions come from word overlap for now.")
    elif op == "idea_task":   # text: "me" or "ai"
        return f"Idea is now task #{brain.promote_idea(con, id_, text if text in ('me', 'ai') else 'me')}"
    elif op == "get":  # text: table; the full row for the edit form
        if text not in edit.FIELDS:
            raise ValueError("unknown table")
        row = con.execute(f"SELECT * FROM {text} WHERE id = ?", (id_,)).fetchone()
        if row is None:
            raise ValueError(f"#{id_} does not exist")
        return json.dumps(dict(row))
    elif op == "edit":    # text: JSON {"table": ..., "fields": {...}}
        req = json.loads(text)
        return edit.edit(con, req["table"], id_, req.get("fields") or {})
    elif op == "create":  # text: JSON {"table": ..., "fields": {...}}
        req = json.loads(text)
        what = {"tasks": "task", "ideas": "idea", "goals": "goal", "capabilities": "capability"}.get(req["table"], "item")
        return f"Added {what} #{edit.create(con, req['table'], req.get('fields') or {})}"
    elif op in ("build_ok", "build_no"):  # Djeddi's answer on a big task, from the Build path
        return skilltree.decide(con, id_, op == "build_ok")
    elif op == "build_focus":
        return skilltree.set_focus(con, text)
    elif op == "brain_undo_many":  # Undo in the toast after an action: its change-log entries, newest first
        ids = sorted((int(x) for x in (text or "").split(",") if x.strip().isdigit()), reverse=True)
        if not ids:
            raise ValueError("Nothing to undo.")
        for cid in ids:
            brain.undo(con, cid)
        return "Undone."
    elif op == "brain_undo":
        return brain.undo(con, id_)
    elif op == "brain_think":
        r = brain.reevaluate(con)
        return r.get("error") or f"Re-evaluated: {r['applied']} change(s)"
    elif op == "replan":  # Today tab: re-evaluate, and propose a do-over when the plan has drifted
        return doover.replan_text(doover.replan(con))
    elif op == "brain_accept":  # text: JSON {"action": "drop|merge", "task_ids": [...], "reason": ""}
        s = json.loads(text)
        return brain.accept_suggestion(con, s["action"], [int(i) for i in s["task_ids"]], s.get("reason", ""))
    elif op == "attach":  # task id → goal id in text ("" = unlink)
        plan.link_task(con, id_, int(text) if str(text).strip() else None)
    elif op == "nest":    # goal id → parent goal id in text ("" = top level)
        plan.set_parent(con, id_, int(text) if str(text).strip() else None)
    elif op == "review":
        return progress.review(con)
    elif op == "scan":
        return json.dumps(radar.scan(con, loop.load_constitution()))
    elif op == "dump":
        result = dump.process(con, text, source="dashboard")
        priority.rerank(con, "brain dump")
        return result
    elif op == "inbox" and text.strip():
        prefix, _, rest = text.partition(":")
        p = prefix.lower().strip()
        if rest and p in ("todo", "task"):
            db.insert(con, "tasks", title=rest.strip())
        elif rest and p == "idea":
            db.insert(con, "ideas", title=rest.strip())
        else:
            db.insert(con, "memories", kind="info", content=text.strip())
    else:
        raise ValueError(f"unknown op {op}")
    db.log(con, "me", f"dash_{op}", str(id_ or text[:80]))
    con.commit()
    # a local re-rank is enough after a click: the hourly cycle (cron) still polls Telegram and applies do-overs
    priority.rerank(con, f"dashboard {op}")
    return "ok"


def overview_query(con, query: str) -> dict:
    """GET /api/overview: the query string maps onto overview.items()."""
    q = {k: v[-1] for k, v in parse_qs(query).items()}
    goal = q.get("goal", "")
    return overview.items(con, status={x for x in q.get("status", "").split(",") if x} or None,
                          goal=int(goal) if goal.isdigit() else None, due=q.get("due") or None,
                          kind=q.get("kind") if q.get("kind") in ("tasks", "ideas") else "all",
                          include_done=q.get("all") == "1", search=q.get("search", ""),
                          sort=q.get("sort", "status"), created_from=q.get("created_from", ""),
                          created_to=q.get("created_to", ""), recommendation=q.get("recommendation", ""),
                          stage=q.get("stage", ""))


def authorised(headers) -> bool:
    token = os.environ.get("HQ_TOKEN", "")
    return not token or hmac.compare_digest(headers.get("X-HQ-Token", ""), token)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        con = db.connect(); db.init(con)
        if self.path.startswith("/api/") and not authorised(self.headers):
            return self._send(401, '{"error": "token required"}')
        if self.path == "/api/data":
            try:
                self._send(200, json.dumps(build_data(con), default=str))
            except Exception as e:
                recovery.capture(e)
                self._send(500, json.dumps({"error": "Atlas encountered an error and recorded it for repair."}))
        elif self.path.split("?")[0] == "/api/task-detail":
            try:
                task_id = int(parse_qs(urlparse(self.path).query).get("id", [""])[0])
                self._send(200, json.dumps(taskdetail.detail(con, task_id)))
            except ValueError as e:
                self._send(400, json.dumps({"error": str(e)}))
        elif self.path.split("?")[0] == "/api/run":
            try:
                q = parse_qs(urlparse(self.path).query)
                self._send(200, json.dumps(devloop.live(con, int(q.get("run", [""])[0]), int(q.get("after", ["0"])[0] or 0))))
            except ValueError as e:
                self._send(400, json.dumps({"error": str(e)}))
        elif self.path.split("?")[0] == "/api/overview":
            try:
                self._send(200, json.dumps(overview_query(con, urlparse(self.path).query), default=str))
            except ValueError as e:
                self._send(400, json.dumps({"error": str(e)}))
        elif self.path.split("?")[0] in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path.split("?")[0] == "/api/voice":
            return self._voice()
        if self.path != "/api/action":
            return self._send(404, "{}")
        if not authorised(self.headers):
            return self._send(401, '{"error": "token required"}')
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        con = db.connect(); db.init(con)
        try:
            mark = con.execute("SELECT COALESCE(MAX(id), 0) FROM brain_changes").fetchone()[0]
            result = act(con, body.get("op"), body.get("id"), body.get("text", ""))
            # the change-log entries this action made: the page's Undo reverses exactly these (optimistic UI)
            made = [r[0] for r in con.execute("SELECT id FROM brain_changes WHERE id > ? AND undone = 0 ORDER BY id", (mark,))]
            self._send(200, json.dumps({"result": result, "changes": made}))
        except Exception as e:
            if not isinstance(e, (ValueError, PermissionError, models.ModelUnavailable)):
                recovery.capture(e)
            self._send(400, json.dumps({"error": str(e)}))

    def _voice(self):
        if not authorised(self.headers):
            return self._send(401, '{"error": "token required"}')
        size = int(self.headers.get("Content-Length", 0))
        if size > MAX_VOICE_BYTES:
            return self._send(413, json.dumps({"error": "recording too long"}))
        audio = self.rfile.read(size)
        lang = parse_qs(urlparse(self.path).query).get("lang", [""])[0]
        con = db.connect(); db.init(con)
        try:
            self._send(200, json.dumps({"text": voice(con, audio, self.headers.get("Content-Type", ""), lang)}))
        except models.ModelUnavailable as e:
            self._send(503, json.dumps({"error": f"Recording saved; speech-to-text is unavailable now ({e})."}))
        except Exception as e:
            if not isinstance(e, (ValueError, PermissionError, models.ModelUnavailable)):
                recovery.capture(e)
            self._send(400, json.dumps({"error": str(e)}))

    def log_message(self, *a):
        pass


class RecoveryServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        import sys
        error = sys.exc_info()[1]
        if error and not isinstance(error, (BrokenPipeError, ConnectionResetError)):
            recovery.capture(error)
        super().handle_error(request, client_address)


def serve(port: int = 8765, host: str = "127.0.0.1"):
    if host not in ("127.0.0.1", "localhost") and not os.environ.get("HQ_TOKEN"):
        raise SystemExit("Refusing to listen beyond localhost without HQ_TOKEN set.")
    print(f"Atlas dashboard on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server = RecoveryServer((host, port), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        # another Atlas server already listens: a start-up clash, not a bug to repair (exits without an incident)
        raise SystemExit(f"Port {port} is already in use, probably by another Atlas dashboard. Not starting a second one.")
    server.serve_forever()
