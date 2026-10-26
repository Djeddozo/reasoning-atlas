from contextlib import closing
import json, sqlite3, tempfile, threading, unittest, urllib.error, urllib.parse, urllib.request
from unittest import mock
from pathlib import Path
from engine import db as hqdb, models
from packages.contracts.modules import load_registry
from services.atlas import hub

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-0123456789abcdef"


def engine_database(folder):
    path = Path(folder) / "hq.db"
    con = hqdb.connect(path)
    hqdb.init(con)
    hqdb.insert(con, "approvals", request="Spend 5 euro on a domain", reason="Synthetic", status="pending", created=hqdb.now())
    hqdb.insert(con, "shell_commands", title="Restart the bot", why="Synthetic", command="./hq.sh bot", status="open", created=hqdb.now())
    hqdb.insert(con, "interruptions", tool="claude", session_id="s1", stopped_at=hqdb.now(), objective="Synthetic objective",
                status="open", created=hqdb.now())
    for i, (title, status, score) in enumerate([("First step", "doing", 9), ("Second step", "todo", 5), ("Done step", "done", 1)]):
        hqdb.insert(con, "tasks", title=title, status=status, score=score, created=hqdb.now())
    hqdb.insert(con, "memories", kind="lesson", content="Synthetic lesson about hubs and tokens", created=hqdb.now())
    hqdb.insert(con, "radar", source="github", external_id="r1", title="agent-kit: an open-source agent framework", url="https://github.com/x/agent-kit",
                summary="A self-hosted agent workflow library", score=3, found=hqdb.now())
    hqdb.insert(con, "radar", source="labs", external_id="r2", title="New model release with better tool use", url="https://example.org/news",
                summary="Function calling and long context", score=2, found=hqdb.now())
    hqdb.insert(con, "radar", source="hn", external_id="r3", title="Gardening tips", url="https://example.org/garden", summary="", found=hqdb.now())
    hqdb.insert(con, "resources", kind="hardware", name="Raspberry Pi 5", details="runs the Engine", value=1, unit="pc")
    hqdb.insert(con, "resources", kind="time", name="Evenings", details="for building", value=10, unit="h/week")
    pid = hqdb.insert(con, "projects", name="finance-app", path="/synthetic/finance-app", description="Budget tracker")
    hqdb.insert(con, "assets", project_id=pid, path="app/budget.py", kind="function", name="monthly_budget",
                signature="def monthly_budget(rows)", doc="Sum spending per category per month", line=12, tokens="monthly budget finance-app")
    hqdb.insert(con, "research_notes", question="Could a local model sort my notes?", status="done", finished=hqdb.now(),
                answer=json.dumps({"summary": "Yes, a small one can.", "matters": "Cheaper sorting.", "next_steps": ["Try it on the GPU PC"], "idea": "Local sorting"}))
    con.commit()
    con.close()
    return path


def post(url, body, token=TOKEN):
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    return json.load(urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers), timeout=60))


def get(url, token=TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10))


class Hub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.db = engine_database(cls.folder.name)
        cls.server = hub.make_server("127.0.0.1", 0, TOKEN, cls.db)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.folder.cleanup()

    def test_token_is_required(self):
        for token in (None, "wrong-token-0123456789abcdef"):
            with self.subTest(token=token), self.assertRaises(urllib.error.HTTPError) as ctx:
                get(self.url + "/v1/health", token)
            self.assertEqual(ctx.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(self.url + "/v1/actions/engine.attention.answer_needs", {"key": "A1", "verdict": "approved"}, token=None)
        self.assertEqual(ctx.exception.code, 401)

    def test_health(self):
        health = get(self.url + "/v1/health")
        self.assertEqual((health["status"], health["engineDatabase"]), ("ok", True))
        self.assertEqual(set(health["functions"]), set(hub.FUNCTIONS))

    def test_engine_functions(self):
        call = lambda fid, q="": get(f"{self.url}/v1/functions/{fid}{q}")["result"]
        self.assertEqual(call("engine.attention.needs")[0]["title"], "Spend 5 euro on a domain")
        self.assertEqual(call("engine.attention.terminal_steps")[0]["command"], "./hq.sh bot")
        self.assertEqual(call("engine.attention.interrupted_work")[0]["objective"], "Synthetic objective")
        steps = call("engine.direction.ranked_next_steps")
        self.assertEqual([t["title"] for t in steps], ["First step", "Second step"])
        self.assertTrue(steps[0]["why"].startswith("impact "))
        focus = call("engine.direction.current_focus")
        self.assertEqual((focus["focus"], focus["next"]["title"], focus["destination"]), ("balanced", "First step", None))
        self.assertIn("plan", focus["options"])
        self.assertEqual(set(call("engine.direction.zoom_out")), {"insights", "goals", "nodes"})
        self.assertIn("agent", call("engine.system.usage"))
        self.assertEqual(len(call("engine.memory.recall", "?q=hubs")), 1)
        self.assertEqual(call("engine.brain.recent_thoughts"), [])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            get(self.url + "/v1/functions/engine.research.find_options")
        self.assertEqual(ctx.exception.code, 404)

    def test_reads_never_write(self):
        before = self.db.read_bytes()
        for fid in hub.FUNCTIONS:
            try:
                get(f"{self.url}/v1/functions/{fid}?q=lesson")
            except urllib.error.HTTPError as exc:  # a read that needs another parameter refuses; it still must not write
                self.assertEqual(exc.code, 400, fid)
        self.assertEqual(self.db.read_bytes(), before)
        with closing(hub.read_only(self.db)) as con, self.assertRaises(sqlite3.OperationalError):
            con.execute("DELETE FROM tasks")

    def test_functions_match_registry(self):
        registry = load_registry(ROOT / "modules", ROOT)
        caps = registry["capabilities"]
        via_hub = {cid for cid, c in caps.items() if c.get("viaHub")}
        self.assertEqual(via_hub, set(hub.FUNCTIONS) | set(hub.ACTIONS))
        self.assertTrue(all(caps[f]["effect"] == "read" for f in hub.FUNCTIONS))
        self.assertTrue(all(caps[a]["effect"] != "read" for a in hub.ACTIONS))

    def test_refuses_unsafe_start(self):
        with self.assertRaises(SystemExit):
            hub.make_server("127.0.0.1", 0, "")
        with self.assertRaises(SystemExit):
            hub.make_server("127.0.0.1", 0, "short")
        with self.assertRaises(SystemExit):
            hub.make_server("0.0.0.0", 0, TOKEN)

    def test_missing_database(self):
        server = hub.make_server("127.0.0.1", 0, TOKEN, Path(self.folder.name) / "absent.db")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                get(f"http://127.0.0.1:{server.server_port}/v1/functions/engine.attention.needs")
            self.assertEqual(ctx.exception.code, 503)
        finally:
            server.shutdown(); server.server_close()


class HubActions(unittest.TestCase):
    """Writes go through the Engine's own functions, as Atlas's dashboard does for the same buttons."""
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = engine_database(self.folder.name)
        con = hqdb.connect(self.db)
        hqdb.insert(con, "approvals", request="Which voice model?", options="Local|Cloud", status="pending", created=hqdb.now())
        con.commit(); con.close()
        self.server = hub.make_server("127.0.0.1", 0, TOKEN, self.db)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/actions/"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        self.folder.cleanup()

    def row(self, sql, *args):
        with closing(sqlite3.connect(self.db)) as con:
            return con.execute(sql, args).fetchone()

    def test_approve_and_decide(self):
        self.assertIn("Approved #1", post(self.url + "engine.attention.answer_needs", {"key": "A1", "verdict": "approved"})["message"])
        self.assertEqual(self.row("SELECT status FROM approvals WHERE id = 1")[0], "approved")
        self.assertEqual(self.row("SELECT actor, detail FROM log WHERE action = 'approval_approved'"), ("me", "#1 via atlas"))
        post(self.url + "engine.attention.answer_needs", {"key": "D2", "text": "local"})
        self.assertEqual(self.row("SELECT status, answer FROM approvals WHERE id = 2"), ("answered", "Local"))
        self.assertIn("Which voice model? → Local", self.row("SELECT content FROM memories WHERE kind = 'decision'")[0])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(self.url + "engine.attention.answer_needs", {"key": "A1", "verdict": "denied"})
        self.assertEqual(ctx.exception.code, 400)  # already settled: the Engine refuses, nothing changes
        self.assertEqual(self.row("SELECT status FROM approvals WHERE id = 1")[0], "approved")

    def test_terminal_step_and_interrupted_work(self):
        post(self.url + "engine.attention.finish_terminal_step", {"id": 1, "status": "done"})
        self.assertEqual(self.row("SELECT status FROM shell_commands WHERE id = 1")[0], "done")
        post(self.url + "engine.attention.finish_interrupted", {"id": 1, "status": "dismissed"})
        self.assertEqual(self.row("SELECT status, resolution FROM interruptions WHERE id = 1"), ("dismissed", "not needed (Atlas)"))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(self.url + "engine.attention.finish_terminal_step", {"id": 1, "status": "done"})
        self.assertEqual(ctx.exception.code, 400)

    def test_write_thought(self):
        reply = {"tasks": [{"title": "Book the exam", "owner": "me", "impact": 4}], "ideas": [{"title": "Study group"}], "summary": "1 task, 1 idea"}
        with mock.patch.object(models, "complete", return_value=json.dumps(reply)):
            message = post(self.url + "engine.brain.write_thought", {"text": "book the exam; maybe a study group"})["message"]
        self.assertTrue(message.startswith("1 task, 1 idea"))
        self.assertIn("in Forge › Plan", message)
        self.assertEqual(self.row("SELECT source, status FROM thoughts"), ("atlas", "sorted"))
        self.assertEqual(self.row("SELECT reason FROM brain_changes WHERE table_name = 'tasks'")[0], "from your thought #1")
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("offline")):
            message = post(self.url + "engine.brain.write_thought", {"text": "idea: voice notes on the watch\nand something vague"})["message"]
        self.assertIn("no model reachable", message)
        self.assertEqual(self.row("SELECT status FROM thoughts WHERE id = 2")[0], "unsorted")  # sorted later by the Engine
        self.assertEqual(self.row("SELECT COUNT(*) FROM ideas WHERE title = 'voice notes on the watch'")[0], 1)
        thoughts = get(self.url.replace("/v1/actions/", "/v1/functions/") + "engine.brain.recent_thoughts")["result"]
        self.assertEqual([t["id"] for t in thoughts], [2, 1])
        self.assertEqual(sorted((c["table_name"], c["title"]) for c in thoughts[1]["changes"]),
                         [("ideas", "Study group"), ("tasks", "Book the exam")])
        self.assertIn("2 change(s)", post(self.url + "engine.brain.undo_changes", {"thought": 1})["message"])
        self.assertEqual(self.row("SELECT status FROM tasks WHERE title = 'Book the exam'")[0], "dropped")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(self.url + "engine.brain.undo_changes", {"thought": 1})
        self.assertEqual(ctx.exception.code, 400)
        for body in ({"text": "  "}, {"text": "x" * 8001}, {}):
            with self.subTest(body=str(body)[:20]), self.assertRaises(urllib.error.HTTPError) as ctx:
                post(self.url + "engine.brain.write_thought", body)
            self.assertEqual(ctx.exception.code, 400)

    def test_set_focus(self):
        self.assertEqual(post(self.url + "engine.attention.answer_needs", {"key": "A1", "verdict": "denied"})["action"], "engine.attention.answer_needs")
        self.assertIn("Plan first", post(self.url + "engine.direction.set_focus", {"focus": "plan"})["message"])
        self.assertEqual(self.row("SELECT value FROM state WHERE key = 'build_focus'")[0], "plan")
        self.assertEqual(self.row("SELECT actor FROM log WHERE action = 'build_focus'")[0], "me")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(self.url + "engine.direction.set_focus", {"focus": "everything"})
        self.assertEqual(ctx.exception.code, 400)

    def test_ideas(self):
        con = hqdb.connect(self.db)
        goal = hqdb.insert(con, "goals", title="Voice input everywhere", horizon="quarter", status="active")
        a = hqdb.insert(con, "ideas", title="Voice notes from the watch", description="voice input", gate="idea")
        b = hqdb.insert(con, "ideas", title="Garden planner", gate="idea")
        c = hqdb.insert(con, "ideas", title="Old idea", gate="idea")
        con.commit(); con.close()
        before = self.db.read_bytes()
        view = get(self.url.replace("/v1/actions/", "/v1/functions/") + "engine.build.ideas")["result"]
        self.assertEqual(self.db.read_bytes(), before)  # placing by rule happened on a copy
        self.assertEqual({i["title"] for i in view["ideas"]}, {"Voice notes from the watch", "Garden planner", "Old idea"})
        self.assertEqual(view["goals"][0]["title"], "Voice input everywhere")
        self.assertIn("place", view["ideas"][0])
        self.assertIn("task #", post(self.url + "engine.build.idea_to_plan", {"idea": a, "goal": goal})["message"])
        self.assertEqual(self.row("SELECT gate FROM ideas WHERE id = ?", a)[0], "promoted")
        self.assertEqual(self.row("SELECT goal_id, owner FROM tasks WHERE title = 'Voice notes from the watch'"), (goal, "me"))
        self.assertIn("New goal", post(self.url + "engine.build.idea_to_plan", {"idea": b, "new_goal": {"title": "A garden"}})["message"])
        self.assertEqual(self.row("SELECT COUNT(*) FROM goals WHERE title = 'A garden'")[0], 1)
        self.assertIn("set aside", post(self.url + "engine.build.edit_plan", {"drop_idea": c})["message"])
        self.assertEqual(self.row("SELECT gate FROM ideas WHERE id = ?", c)[0], "dropped")
        for body in ({"idea": a, "goal": goal}, {"drop_idea": c}, {"idea": 99, "goal": goal}):
            with self.subTest(body=body), self.assertRaises(urllib.error.HTTPError) as ctx:
                post(self.url + ("engine.build.edit_plan" if "drop_idea" in body else "engine.build.idea_to_plan"), body)
            self.assertEqual(ctx.exception.code, 400)
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("offline")):
            self.assertIn("not reachable", post(self.url + "engine.build.place_ideas", {})["message"])

    def test_directions(self):
        made = post(self.url + "engine.direction.shape_directions", {"create": {"title": "Advance the intelligence of Atlas"}})["message"]
        gid = int(made.split("#")[1].split()[0])
        obj = post(self.url + "engine.direction.shape_directions", {"objective": {"vision": gid, "title": "Engine in Atlas"}})["message"]
        oid = int(obj.split("#")[1].split()[0])
        con = hqdb.connect(self.db)
        con.execute("UPDATE tasks SET goal_id = ? WHERE title = 'Second step'", (oid,))
        loose = hqdb.insert(con, "goals", title="Pass the exam", horizon="quarter", status="active")
        con.commit(); con.close()
        view = get(self.url.replace("/v1/actions/", "/v1/functions/") + "engine.direction.directions")["result"]
        self.assertEqual([(v["title"], [o["title"] for o in v["objectives"]]) for v in view["visions"]],
                         [("Advance the intelligence of Atlas", ["Engine in Atlas"])])
        self.assertEqual([g["title"] for g in view["loose"]], ["Pass the exam"])
        post(self.url + "engine.direction.shape_directions", {"goal": loose, "vision": gid})
        self.assertIn("Advance the intelligence", post(self.url + "engine.direction.choose_direction", {"goal": gid})["message"])
        steps = get(self.url.replace("/v1/actions/", "/v1/functions/") + "engine.direction.ranked_next_steps")["result"]
        self.assertEqual([t["inFocus"] for t in steps], [False, True])
        self.assertIn("in your focus", steps[1]["why"])
        self.assertIn("No focus", post(self.url + "engine.direction.choose_direction", {"goal": None})["message"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(self.url + "engine.direction.choose_direction", {"goal": oid})
        self.assertEqual(ctx.exception.code, 400)

    def test_build_controls(self):
        from engine import devloop, quickbuild, skilltree
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        before = self.db.read_bytes()
        status = get(fn + "engine.build.run_status")["result"]
        self.assertEqual(self.db.read_bytes(), before)  # read on a copy
        for key in ("enabled", "paused", "autonomy", "limits", "lanes", "plan", "delivery", "runs"):
            self.assertIn(key, status)
        self.assertIn("paused", post(self.url + "engine.build.dev_controls", {"pause": True})["message"])
        self.assertEqual(self.row("SELECT value FROM state WHERE key = 'devloop_paused'")[0], "1")
        post(self.url + "engine.build.dev_controls", {"pause": False})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(self.url + "engine.build.dev_controls", {"autonomy": "reckless"})
        self.assertEqual(ctx.exception.code, 400)
        with mock.patch.object(devloop, "why_not_now", return_value=""), \
             mock.patch.object(devloop, "start_background", return_value="Started a run.") as start:
            self.assertEqual(post(self.url + "engine.build.dev_loop", {"task": 2})["message"], "Started a run.")
        self.assertEqual(start.call_args.kwargs, {"task_id": 2})
        with mock.patch.object(devloop, "why_not_now", return_value="A run is already in progress."), \
             self.assertRaises(urllib.error.HTTPError) as ctx:
            post(self.url + "engine.build.dev_loop", {"task": None})
        self.assertEqual(ctx.exception.code, 400)
        with mock.patch.object(skilltree, "queue") as queue:
            self.assertIn("queued for the builder", post(self.url + "engine.build.build_queue", {"owner": {"task": 2, "to": "ai"}})["message"])
        self.assertEqual(self.row("SELECT owner FROM tasks WHERE id = 2")[0], "ai")
        self.assertTrue(queue.called)
        with mock.patch.object(devloop, "scan", return_value="No conflicts."):
            self.assertEqual(post(self.url + "engine.build.pr_conflicts", {"scan": True})["message"], "No conflicts.")
        with mock.patch.object(quickbuild, "start", return_value={"message": "Task #9: Started a run."}):
            self.assertIn("Task #9", post(self.url + "engine.build.quick_build", {"prompt": "x", "request_id": "r" * 20})["message"])

    def test_live_console(self):
        from engine import devloop
        con = hqdb.connect(self.db)
        run = hqdb.insert(con, "dev_runs", task_id=1, status="running", started="2026-10-20T10:00:00", branch="dev/x")
        con.commit(); con.close()
        fn = self.url.replace("/v1/actions/", "/v1/functions/") + "engine.build.run_status"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(devloop, "WORK", Path(d)):
            devloop.log_path(run).write_text("one\ntwo\nthree")
            first = get(f"{fn}?run={run}&after=0")["result"]
            self.assertEqual((first["lines"], first["total"]), (["one", "two", "three"], 3))
            devloop.log_path(run).write_text("one\ntwo\nthree\nfour")
            self.assertEqual(get(f"{fn}?run={run}&after=3")["result"]["lines"], ["four"])  # only what is new

    def test_plan(self):
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        before = self.db.read_bytes()
        tree = get(fn + "engine.build.plan_tree")["result"]
        self.assertEqual(self.db.read_bytes(), before)  # tree() re-scores on a copy
        self.assertIn("goals", tree)
        made = post(self.url + "engine.build.edit_plan", {"create": {"table": "goals", "fields": {"title": "Engine in Atlas", "horizon": "quarter"}}})
        gid = int(made["message"].split("#")[1].rstrip("."))
        self.assertTrue(made["changes"])  # the change-log entries, for Undo
        sub = post(self.url + "engine.build.edit_plan", {"create": {"table": "goals", "fields": {"title": "Plan stage", "horizon": "month", "parent_id": gid}}})
        self.assertEqual(self.row("SELECT parent_id FROM goals WHERE title = 'Plan stage'")[0], gid)
        edited = post(self.url + "engine.build.edit_plan", {"edit": {"table": "tasks", "id": 2, "fields": {"goal_id": gid, "owner": "me"}}})
        self.assertEqual(self.row("SELECT goal_id FROM tasks WHERE id = 2")[0], gid)
        post(self.url + "engine.brain.undo_changes", {"changes": edited["changes"]})
        self.assertIsNone(self.row("SELECT goal_id FROM tasks WHERE id = 2")[0])  # Undo restored it
        detail = get(fn + "engine.build.task_detail?id=2")["result"]
        self.assertEqual(detail["task"]["title"], "Second step")
        self.assertIn("impact", detail["priority_factors"])
        overview = get(fn + "engine.build.overview?kind=tasks")["result"]
        self.assertTrue(any(t["title"] == "Second step" for t in overview["tasks"]))
        post(self.url + "engine.build.edit_plan", {"edit": {"table": "tasks", "id": 2, "fields": {"owner": "me"}}})
        self.assertIn("Done", post(self.url + "engine.build.task_status", {"task": 2, "status": "done"})["message"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:  # the builder's tasks close on evidence only
            con = hqdb.connect(self.db); con.execute("UPDATE tasks SET owner = 'ai' WHERE id = 1"); con.commit(); con.close()
            post(self.url + "engine.build.task_status", {"task": 1, "status": "done"})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("deleted", post(self.url + "engine.build.task_status", {"task": 1, "status": "dropped"})["message"])
        self.assertEqual(self.row("SELECT status FROM tasks WHERE id = 1")[0], "dropped")
        with self.assertRaises(urllib.error.HTTPError):
            post(self.url + "engine.build.reorder_tasks", {"task": 1, "direction": "up"})  # not open any more

    def test_placement_review(self):
        from engine import revisit
        with mock.patch.object(models, "complete", return_value=json.dumps({"summary": "Fine.", "items": [
                {"function": "engine.system.usage", "action": "move", "to": "Today", "reason": "checked daily"}]})):
            message = post(self.url + "engine.system.revisit", {"placement": True})["message"]
        self.assertIn("1 proposal(s)", message)
        self.assertEqual(self.row("SELECT status FROM tasks WHERE title LIKE 'Move engine.system.usage%'")[0], "proposed")

    def test_today_glance_and_weekly_review(self):
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        con = hqdb.connect(self.db); con.execute("UPDATE tasks SET owner = 'me'"); con.commit(); con.close()
        before = self.db.read_bytes()
        g = get(fn + "engine.direction.progress")["result"]
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual([t["title"] for t in g["mine"]], ["First step", "Second step"])
        for key in ("done_week", "streak", "overdue", "due_soon", "stalled", "proposals", "review"):
            self.assertIn(key, g)
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("offline")):
            text = post(self.url + "engine.direction.weekly_review", {})["message"]
        self.assertTrue(text.startswith("Weekly review"))
        self.assertTrue(get(fn + "engine.direction.progress")["result"]["review"]["content"].startswith("Weekly review"))

    def test_mark_important(self):
        from engine import skilltree
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        con = hqdb.connect(self.db); con.execute("UPDATE tasks SET owner = 'ai' WHERE id = 2"); con.commit(); con.close()
        with mock.patch.object(skilltree, "queue") as queue:
            out = post(self.url + "engine.build.task_status", {"task": 2, "status": "important"})
        self.assertIn("builder takes it next", out["message"])
        self.assertEqual(queue.call_args.kwargs, {"mine": True, "top": True})
        steps = get(fn + "engine.direction.ranked_next_steps")["result"]
        self.assertEqual(steps[0]["title"], "First step")  # work in progress stays first
        self.assertEqual(steps[1]["title"], "Second step")
        self.assertEqual(self.row("SELECT impact FROM tasks WHERE id = 2")[0], 5)
        self.assertGreater(self.row("SELECT score FROM tasks WHERE id = 2")[0], self.row("SELECT score FROM tasks WHERE id = 1")[0])
        post(self.url + "engine.brain.undo_changes", {"changes": out["changes"]})  # Undo works
        self.assertIsNone(self.row("SELECT pinned_score FROM tasks WHERE id = 2")[0])
        post(self.url + "engine.build.task_status", {"task": 2, "status": "important"})
        post(self.url + "engine.build.task_status", {"task": 2, "status": "normal"})
        self.assertEqual(self.row("SELECT impact, pinned_score FROM tasks WHERE id = 2"), (3, None))

    def test_explore_and_take_an_idea(self):
        con = hqdb.connect(self.db)
        goal = hqdb.insert(con, "goals", title="First digital product", horizon="year", status="active")
        idea = hqdb.insert(con, "ideas", title="Flashcard app", description="spaced repetition", gate="idea")
        con.commit(); con.close()
        reply = {"summary": "A small first product.", "fits": [], "take_as": "task", "under": goal, "title": "Prototype", "why": "fits"}
        with mock.patch.object(models, "complete", return_value=json.dumps(reply)):
            self.assertEqual(post(self.url + "engine.build.explore_idea", {"idea": idea})["message"], "A small first product.")
        view = get(self.url.replace("/v1/actions/", "/v1/functions/") + "engine.build.ideas")["result"]
        self.assertEqual(next(i for i in view["ideas"] if i["id"] == idea)["explored"]["take_as"], "task")
        self.assertIn("under the goal it serves", post(self.url + "engine.build.idea_to_plan", {"idea": idea, "take": True})["message"])

    def test_brain_links_files_and_ask(self):
        import base64
        from engine import intake
        with tempfile.TemporaryDirectory() as d, mock.patch.object(intake, "FILES_DIR", Path(d)), \
             mock.patch.object(intake, "add_link", return_value={"status": "done", "kind": "link", "title": "A page", "summary": "about hubs", "ref": "x"}) as link, \
             mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("offline")):
            msg = post(self.url + "engine.brain.write_thought", {"text": "read this https://example.com/a later"})["message"]
            self.assertEqual(link.call_args.args[1], "https://example.com/a")
            self.assertIn("Saved link: A page", msg)
            png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
            msg = post(self.url + "engine.brain.ingest", {"name": "../screen shot.png", "data": png, "note": "the error"})["message"]
            self.assertIn("image", msg)
            self.assertEqual([p.name.split("-", 2)[-1] for p in Path(d).iterdir()], ["screen-shot.png"])  # no path tricks
            reply = post(self.url + "engine.brain.talk", {"text": "what should I do first?", "mode": "ask"})["message"]
            self.assertIn("can't reach Claude", reply)
        convo = get(self.url.replace("/v1/actions/", "/v1/functions/") + "engine.brain.conversation")["result"]
        self.assertEqual(convo[0]["text"], "what should I do first?")

    def test_skill_tree(self):
        from engine import research
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        before = self.db.read_bytes()
        tree = get(fn + "engine.growth.skill_tree")["result"]
        self.assertEqual(self.db.read_bytes(), before)  # the tree plants its seed on a copy
        self.assertTrue(tree["nodes"] and tree["branches"])
        locked = next(n for n in tree["nodes"] if n["state"] != "unlocked")
        node = get(fn + f"engine.growth.skill_tree?node={locked['id']}")["result"]["node"]
        self.assertEqual((node["id"], node["research"]), (locked["id"], None))
        self.assertIn("Draft task", post(self.url + "engine.growth.draft_node_task", {"node": locked["id"]})["message"])
        self.assertEqual(self.row("SELECT status, capability_id FROM tasks ORDER BY id DESC LIMIT 1"), ("proposed", locked["id"]))
        self.assertIn("built", post(self.url + "engine.growth.confirm_proof", {"node": locked["id"], "ref": "PR #3"})["message"].lower())
        with mock.patch.object(research, "research", return_value=7), \
             mock.patch.object(research, "load", return_value={"summary": "Three ways.", "options": [1, 2, 3]}):
            self.assertEqual(post(self.url + "engine.growth.node_research", {"node": locked["id"]})["message"], "Three ways.")

    def test_research(self):
        from engine import deepresearch, launch
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        before = self.db.read_bytes()
        r = get(fn + "engine.research.research_feed")["result"]
        self.assertEqual(self.db.read_bytes(), before)
        titles = {i["title"]: i for i in r["feed"]}
        self.assertNotIn("Gardening tips", titles)  # touches nothing of Atlas or his interests
        tool = next(i for i in r["feed"] if i["kind"] == "tool")
        self.assertTrue(tool["prereq"]["line"] and tool["why"])
        self.assertEqual(r["notes"][0]["answer"]["idea"], "Local sorting")
        self.assertIn("github", r["settings"]["sources"])
        note = get(fn + f"engine.research.sourced_summary?id={r['notes'][0]['id']}")["result"]
        self.assertEqual(note["question"], "Could a local model sort my notes?")
        self.assertIn("Saved", post(self.url + "engine.research.mark_finding", {"item": tool["id"], "state": "starred"})["message"])
        self.assertEqual(self.row("SELECT state FROM radar WHERE id = ?", tool["id"]), ("starred",))
        self.assertIn("Adapt", post(self.url + "engine.research.research_to_idea", {"item": tool["id"], "approach": "adapt"})["message"])
        self.assertEqual(self.row("SELECT title FROM ideas ORDER BY id DESC LIMIT 1")[0][:6], "Adapt:")
        self.assertIn("Forge", post(self.url + "engine.research.research_to_idea", {"note": note["id"]})["message"])
        self.assertEqual(self.row("SELECT title FROM ideas ORDER BY id DESC LIMIT 1"), ("Local sorting",))
        self.assertIn("6 h", post(self.url + "engine.research.research_sources", {"settings": {"every_hours": 6, "per_source": 10}})["message"])
        with mock.patch.object(launch, "detached") as d:
            post(self.url + "engine.research.radar_scan", {})
        self.assertEqual(d.call_args[0][0], ["engine", "radar", "scan"])
        with mock.patch.object(deepresearch, "ask", return_value={"id": 9, "status": "done"}):
            self.assertIn("Research #9", post(self.url + "engine.research.find_options", {"question": "What is MCP?"})["message"])

    def test_resources_and_arsenal(self):
        from engine import launch
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        r = post(self.url + "engine.assets.edit_resource", {"fields": {"kind": "hardware", "name": "GPU PC", "details": "RTX card", "value": 1, "unit": "pc"}})
        self.assertTrue(r["changes"])
        rid = self.row("SELECT id FROM resources WHERE name = 'GPU PC'")[0]
        post(self.url + "engine.assets.edit_resource", {"id": rid, "fields": {"details": "RTX 4070"}})
        groups = get(fn + "engine.assets.resources")["result"]["groups"]
        self.assertEqual([g["items"][0]["details"] for g in groups if g["kind"] == "hardware"], ["RTX 4070"])
        out = post(self.url + "engine.assets.edit_resource", {"retire": rid})
        self.assertEqual(get(fn + "engine.assets.resources")["result"]["count"], 2)
        post(self.url + "engine.brain.undo_changes", {"changes": out["changes"]})
        self.assertEqual(self.row("SELECT status FROM resources WHERE id = ?", rid), ("active",))  # undo brings it back
        for bad in ({"name": "Bank", "details": "IBAN NL00 0000"}, {"name": "x", "kind": "spaceship"}, {"name": "x", "value": "lots"}):
            with self.subTest(bad=bad), self.assertRaises(urllib.error.HTTPError) as ctx:
                post(self.url + "engine.assets.edit_resource", {"fields": bad})
            self.assertEqual(ctx.exception.code, 400)
        a = get(fn + "engine.assets.arsenal_find?q=budget")["result"]
        self.assertEqual(([p["assets"] for p in a["projects"]], [h["name"] for h in a["hits"]]), ([1], ["monthly_budget"]))
        with mock.patch.object(launch, "detached") as d:
            post(self.url + "engine.assets.arsenal_scan", {})
        self.assertEqual(d.call_args[0][0], ["engine", "arsenal", "scan"])

    def test_memory_and_change_log(self):
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        self.assertIn("lesson", post(self.url + "engine.memory.record_lesson", {"kind": "lesson", "text": "Run the detached-worktree suite before pushing"})["message"])
        m = get(fn + "engine.memory.memories")["result"]
        self.assertEqual(m["counts"]["lesson"], 2)
        self.assertEqual(m["items"][0]["content"], "Run the detached-worktree suite before pushing")
        found = get(fn + "engine.memory.memories?q=" + urllib.parse.quote("tokens AND (hubs"))["result"]["items"]  # odd input is only words
        self.assertEqual([x["content"] for x in found], ["Synthetic lesson about hubs and tokens"])
        self.assertEqual(get(fn + "engine.memory.memories?kind=decision")["result"]["items"], [])
        with self.assertRaises(urllib.error.HTTPError):
            post(self.url + "engine.memory.record_lesson", {"kind": "secret", "text": "nope nope"})
        post(self.url + "engine.assets.edit_resource", {"fields": {"kind": "time", "name": "Weekends"}})
        log = get(fn + "engine.memory.change_log?n=5")["result"]
        self.assertIn("resource", log[0]["text"])
        self.assertIn("Weekends", log[0]["text"])

    def test_systems(self):
        from engine import claudeauth, devloop, usage, voicesetup
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        with mock.patch.object(usage, "KILL_FILE", Path(self.folder.name) / "KILL"), \
             mock.patch.object(devloop, "stop", return_value="Nothing running."):
            h = get(fn + "engine.system.health")["result"]
            self.assertFalse(h["killed"])
            self.assertIn("reevaluate", {b["job"] for b in h["budgets"]})
            self.assertIn("[budgets]", h["constitution"])
            self.assertEqual(h["upkeep"][0]["label"], "Plan re-evaluation")
            self.assertNotIn("key", h["voice"]["tiers"]["cloud"])
            self.assertIn("Kill switch ON", post(self.url + "engine.system.kill_switch", {"on": True})["message"])
            self.assertTrue(get(fn + "engine.system.health")["result"]["killed"])
            self.assertIn("OFF", post(self.url + "engine.system.kill_switch", {"on": False})["message"])
        self.assertIn("on", post(self.url + "engine.system.repairs", {"enabled": True})["message"])
        self.assertTrue(get(fn + "engine.system.health")["result"]["repairs"]["enabled"])
        post(self.url + "engine.system.repairs", {"enabled": False})
        with mock.patch.object(claudeauth, "check", return_value={"state": "connected", "message": "Claude is connected."}):
            self.assertEqual(post(self.url + "engine.build.claude_login", {"step": "check"})["message"]["state"], "connected")
        with self.assertRaises(urllib.error.HTTPError) as ctx:  # a key never comes through Atlas
            post(self.url + "engine.system.voice_setup", {"tier": "cloud", "url": "https://stt.example", "key": "sk-123"})
        self.assertEqual(ctx.exception.code, 400)
        with mock.patch.object(voicesetup, "test", return_value={"ok": True, "message": "Works: test clip."}):
            self.assertEqual(post(self.url + "engine.system.voice_setup", {"test": "gpu"})["message"], "Works: test clip.")
        with mock.patch.object(voicesetup, "save", return_value="GPU PC set.") as save:
            post(self.url + "engine.system.voice_setup", {"tier": "gpu", "url": "http://gpu.local:9000"})
        self.assertEqual(save.call_args[0][1:], ("gpu", "http://gpu.local:9000"))

    def test_talk(self):
        def fake(job, system, user):
            if job == "chat":
                return json.dumps({"reply": "Start with the second step.", "thought": ""})
            return json.dumps({"tasks": [{"title": "Plan the week", "owner": "me"}], "summary": "1 task."})
        with mock.patch.object(models, "complete", side_effect=fake):
            asked = post(self.url + "engine.brain.talk", {"text": "What comes next?"})
            told = post(self.url + "engine.brain.talk", {"text": "I will plan the week on Sunday."})
        self.assertEqual((asked["message"], asked["changes"]), ("Start with the second step.", []))
        self.assertIn("Plan the week – in Forge › Plan", told["message"])
        post(self.url + "engine.brain.undo_changes", {"changes": told["changes"]})
        self.assertEqual(self.row("SELECT status FROM tasks WHERE title = 'Plan the week'"), ("dropped",))

    def test_voice_input_is_one_path_with_live_partials(self):
        import base64
        from engine import talk
        audio = base64.b64encode(b"OPUS" * 50).decode()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(talk, "VOICE_DIR", Path(d)):
            with mock.patch.object(models, "transcribe", return_value=" call the dentist ") as t:
                live = post(self.url + "engine.brain.voice_input", {"data": audio, "type": "audio/webm;codecs=opus", "partial": True})
                self.assertEqual((live["message"], list(Path(d).iterdir())), ("call the dentist", []))  # a preview is not kept
                done = post(self.url + "engine.brain.voice_input", {"data": audio, "type": "audio/webm;codecs=opus"})
            self.assertEqual((done["message"], [p.suffix for p in Path(d).iterdir()]), ("call the dentist", [".webm"]))
            self.assertIsNone(t.call_args.args[1])  # the constitution's stt_language decides
            with mock.patch.object(models, "transcribe", side_effect=models.ModelUnavailable("asleep")):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    post(self.url + "engine.brain.voice_input", {"data": audio, "type": "audio/mp4"})
                self.assertIn("Recording saved", json.load(ctx.exception)["error"])
                with self.assertRaises(urllib.error.HTTPError):
                    post(self.url + "engine.brain.voice_input", {"data": audio, "type": "audio/mp4", "partial": True})
            self.assertEqual(sorted(p.suffix for p in Path(d).iterdir()), [".m4a", ".webm"])  # the failed final is kept, the preview not
        self.assertEqual(self.row("SELECT COUNT(*) FROM memories WHERE tags = 'voice untranscribed'"), (1,))
        with mock.patch.object(models, "complete", return_value=json.dumps({"reply": "Noted as a question.", "thought": ""})):
            asked = post(self.url + "engine.brain.talk", {"text": "Call the dentist on Friday.", "mode": "ask"})
        self.assertEqual(asked["message"], "Noted as a question.")  # Ask goes to Brain chat even without a question mark

    def test_already_done_suggestions(self):
        from contextlib import closing as _c
        with _c(sqlite3.connect(self.db)) as con:
            tid = con.execute("INSERT INTO tasks(title, status) VALUES ('Fix the search', 'todo')").lastrowid
            con.execute("INSERT INTO prs(number, title, state, stage, url) VALUES (7, 'Search fixed', 'MERGED', 'live', 'https://example.org/pr/7')")
            sid = con.execute("INSERT INTO reconcile(task_id, pr_number, evidence, status) VALUES (?, 7, 'mentions search', 'open')", (tid,)).lastrowid
            con.commit()
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        item = next(n for n in get(fn + "engine.attention.needs")["result"] if n["kind"] == "already_done")
        self.assertEqual((item["key"], item["url"]), (f"S{sid}", "https://example.org/pr/7"))
        with self.assertRaises(urllib.error.HTTPError):
            post(self.url + "engine.attention.answer_needs", {"key": f"S{sid}", "text": "maybe"})
        out = post(self.url + "engine.attention.answer_needs", {"key": f"S{sid}", "text": "done"})
        self.assertIn("marked done", out["message"])
        self.assertTrue(out["changes"])
        self.assertEqual(self.row("SELECT status FROM tasks WHERE id = ?", tid), ("done",))

    def test_report_hardware(self):
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        report = {"host": "DESKTOP-9", "role": "pc", "cpu": "i7", "cores": 8, "memory_gb": 16.0, "gpus": [{"name": "RTX 3060", "memory_gb": 12.0, "cuda": True}]}
        self.assertIn("This PC (DESKTOP-9)", post(self.url + "engine.assets.report_hardware", {"report": report})["message"])
        connected = get(fn + "engine.assets.resources")["result"]["connected"]
        self.assertEqual([c["name"] for c in connected], ["This PC (DESKTOP-9)"])

    def test_build_queue_order_and_place(self):
        from contextlib import closing as _c
        with _c(sqlite3.connect(self.db)) as con:
            ids = [con.execute("INSERT INTO tasks(title, status, owner) VALUES (?, 'todo', 'ai')", (f"Q{n}",)).lastrowid for n in range(6)]
            waiting = con.execute("INSERT INTO tasks(title, status, owner, depends_on) VALUES ('After Q0', 'todo', 'me', ?)", (str(ids[0]),)).lastrowid
            pick = con.execute("INSERT INTO tasks(title, status, owner) VALUES ('Picked', 'todo', 'me')").lastrowid
            con.commit()
        post(self.url + "engine.build.build_order", {"order": ids[:5]})
        self.assertIn("number 5", post(self.url + "engine.build.task_status", {"task": pick, "status": "builder"})["message"])
        order = [r[0] for r in sqlite3.connect(self.db).execute(
            "SELECT id FROM tasks WHERE queued_by = 'me' AND status NOT IN ('done','dropped') ORDER BY queued_at, id")]
        self.assertEqual(order[3:6], [ids[3], pick, ids[4]])  # the old fifth moved to sixth
        with self.assertRaises(urllib.error.HTTPError):  # waits for another task: refused with the reason
            post(self.url + "engine.build.task_status", {"task": waiting, "status": "builder"})
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        tree = get(fn + "engine.build.plan_tree")["result"]
        flat = {t["id"]: t for t in tree["loose"]}
        self.assertEqual(flat[pick]["queued"], order.index(pick) + 1)
        self.assertIn(f"#{ids[0]}", flat[waiting]["blocked"])

    def test_cortex_name(self):
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        self.assertEqual(get(fn + "engine.brain.cortex")["result"], {"name": "Cortex", "default": "Cortex", "named": False})
        self.assertIn("Nova", post(self.url + "engine.brain.name_cortex", {"name": "  Nova "})["message"])
        self.assertEqual(get(fn + "engine.brain.cortex")["result"]["name"], "Nova")
        seen = {}
        with mock.patch.object(models, "complete", side_effect=lambda job, system, user: seen.setdefault("s", system) and '{"reply": "Hi.", "thought": ""}'):
            post(self.url + "engine.brain.talk", {"text": "Who are you?"})
        self.assertIn("You are Nova (Cortex)", seen["s"])
        with self.assertRaises(urllib.error.HTTPError):
            post(self.url + "engine.brain.name_cortex", {"name": "<script>"})
        post(self.url + "engine.brain.name_cortex", {"name": ""})  # back to Cortex, still counted as chosen
        self.assertEqual(get(fn + "engine.brain.cortex")["result"], {"name": "Cortex", "default": "Cortex", "named": True})

    def test_new_conversation(self):
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        with mock.patch.object(models, "complete", return_value='{"reply": "Noted.", "thought": ""}'):
            post(self.url + "engine.brain.talk", {"text": "First topic", "mode": "ask"})
        self.assertEqual([m["text"] for m in get(fn + "engine.brain.conversation")["result"]][-2:], ["First topic", "Noted."])
        self.assertIn("New conversation", post(self.url + "engine.brain.new_conversation", {})["message"])
        self.assertEqual(get(fn + "engine.brain.conversation")["result"], [])
        self.assertIn("already empty", post(self.url + "engine.brain.new_conversation", {})["message"])
        seen = {}
        with mock.patch.object(models, "complete", side_effect=lambda job, system, user: seen.setdefault("u", user) and '{"reply": "Sure.", "thought": ""}'):
            post(self.url + "engine.brain.talk", {"text": "Second topic", "mode": "ask"})
        self.assertNotIn("First topic", seen["u"].split("CONVERSATION")[1])
        self.assertEqual(len(get(fn + "engine.brain.conversation")["result"]), 2)
        self.assertEqual(len(get(fn + "engine.brain.conversation?earlier=1")["result"]), 4)
        convs = get(fn + "engine.brain.conversations")["result"]
        self.assertEqual([c["title"] for c in convs["conversations"]][:2], ["Second topic", "First topic"])
        first = convs["conversations"][1]["conv"]
        self.assertIn("Back", post(self.url + "engine.brain.new_conversation", {"open": first})["message"])
        self.assertEqual([m["text"] for m in get(fn + "engine.brain.conversation")["result"]], ["First topic", "Noted."])
        with self.assertRaises(urllib.error.HTTPError):
            post(self.url + "engine.brain.new_conversation", {"open": 99})

    def test_just_added(self):
        fn = self.url.replace("/v1/actions/", "/v1/functions/")
        con = hqdb.connect(self.db)
        old = hqdb.insert(con, "tasks", title="Old fresh-check task", status="todo", owner="me", created="2020-01-01T09:00:00")
        new = hqdb.insert(con, "tasks", title="New fresh-check task", status="todo", owner="me")
        con.commit(); con.close()
        tree = get(fn + "engine.build.plan_tree")["result"]
        flat = {t["id"]: t for t in tree["loose"]}
        self.assertTrue(flat[new]["new"]); self.assertFalse(flat[old]["new"])
        self.assertEqual(tree["fresh_minutes"], 60)
        j = get(fn + "engine.direction.progress")["result"]["just_added"]
        titles = [x["title"] for x in j["items"]]
        self.assertIn("New fresh-check task", titles); self.assertNotIn("Old fresh-check task", titles)
        self.assertIn("2 h", post(self.url + "engine.direction.fresh_window", {"minutes": 120})["message"])
        self.assertEqual(get(fn + "engine.direction.progress")["result"]["just_added"]["minutes"], 120)
        with self.assertRaises(urllib.error.HTTPError):
            post(self.url + "engine.direction.fresh_window", {"minutes": 0})
        post(self.url + "engine.direction.fresh_window", {"minutes": 60})
        con = hqdb.connect(self.db); con.execute("UPDATE tasks SET status = 'dropped' WHERE id IN (?, ?)", (old, new)); con.commit(); con.close()

    def test_bad_requests(self):
        for path, body, code in (("engine.attention.answer_needs", {"key": "X9"}, 400),
                                 ("engine.attention.answer_needs", {"key": "A1"}, 400),
                                 ("engine.attention.finish_terminal_step", {}, 400),
                                 ("engine.research.find_options", {"q": "x"}, 400),
                                 ("engine.no.such_action", {}, 404),
                                 ("engine.attention.needs", {}, 404)):
            with self.subTest(path=path, body=body), self.assertRaises(urllib.error.HTTPError) as ctx:
                post(self.url + path, body)
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(self.row("SELECT COUNT(*) FROM approvals WHERE status = 'pending'")[0], 2)

