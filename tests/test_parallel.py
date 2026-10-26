import tempfile, threading, unittest
from pathlib import Path
from unittest import mock

from engine import brain, db, parallel, priority
from services.atlas import hub
from tests.test_hub import TOKEN, get, post


def database():
    con = db.connect(Path(":memory:"))
    db.init(con)
    return con


MODS = []  # no department-table match in unit tests: the explicit cues decide


class Predict(unittest.TestCase):
    def files(self, title, description=""):
        return parallel.predict({"title": title, "description": description}, parallel.DEFAULTS, MODS)

    def test_explicit_paths_modules_and_aliases(self):
        self.assertEqual(self.files("Fix engine/radar.py feed dedupe"), ["engine/radar.py"])
        self.assertIn("engine/devloop.py", self.files("Builder: lease expiry for workers"))
        self.assertIn("engine/overlap.py", self.files("Overlap check also for ideas"))
        self.assertIn("engine/cortex_mcp.py", self.files("A new cortex mcp tool"))
        self.assertIn("services/atlas/hub.py", self.files("Merge engine.brain.old_chat into engine.brain.talk"))
        self.assertIn("STATE/", self.files("RA-031 · Local screens talk to the hub"))

    def test_visual_tasks_share_the_screen_and_plain_words_do_not_count(self):
        self.assertTrue(set(parallel.SCREEN) <= set(self.files("A new card on Today")))
        self.assertEqual(self.files("Make the flow of the plan easier to edit"), [])  # flow.py / edit.py / plan.py: ordinary words
        self.assertEqual(self.files("Think about holidays"), [])

    def test_clash_ignores_common_files_and_folders_cover_their_files(self):
        self.assertEqual(parallel._clash(["engine/db.py", "CLAUDE.md", "tests/test_x.py"], ["engine/db.py", "CLAUDE.md", "tests/test_x.py"]), [])
        self.assertEqual(parallel._clash(["STATE/"], ["STATE/tasks.json"]), ["STATE/"])
        self.assertEqual(parallel._clash(["engine/radar.py"], ["engine/radar.py", "engine/db.py"]), ["engine/radar.py"])


class Batch(unittest.TestCase):
    def setUp(self):
        self.con = database()
        add = lambda title, **kw: db.insert(self.con, "tasks", title=title, owner="ai", status="todo", impact=3, effort_h=1, **kw)
        self.radar = add("Radar: follow more feeds")
        self.mock = add("Mockup rounds can be compared")
        self.radar2 = add("Radar: explain why an item matters")       # same file as self.radar
        self.unknown = add("Think about holidays")                       # files cannot be told
        self.after = add("Sandbox: limits per run", depends_on=str(self.mock))
        self.backup = add("Backup: keep seven days")
        self.con.commit()
        self.order = [self.radar, self.mock, self.radar2, self.unknown, self.after, self.backup]
        patch = mock.patch.object(parallel.overlap, "modules", return_value=MODS)
        patch.start()
        self.addCleanup(patch.stop)

    def suggest(self, **kw):
        return parallel.suggest(self.con, parallel.DEFAULTS, order=kw.get("order", self.order))

    def test_greedy_batch_with_reasons_for_what_is_left_out(self):
        s = self.suggest()
        self.assertEqual([t["id"] for t in s["tasks"]], [self.radar, self.mock, self.backup])
        why = {x["id"]: x["why"] for x in s["left_out"]}
        self.assertIn("both change engine/radar.py", why[self.radar2])
        self.assertIn("cannot tell which files", why[self.unknown])
        self.assertEqual(why[self.after], f"#{self.after} waits for #{self.mock}")
        extra = db.insert(self.con, "tasks", title="Telegram: a /parallel command", owner="ai", status="todo")
        s = self.suggest(order=self.order + [extra])
        self.assertNotIn(extra, [t["id"] for t in s["tasks"]])  # the batch is full at batch_max = 3

    def test_dependencies_through_other_tasks(self):
        middle = db.insert(self.con, "tasks", title="Mockup step two", owner="ai", status="todo", depends_on=str(self.mock))
        db.update(self.con, "tasks", self.after, depends_on=str(middle))
        waits = parallel._waits(self.con)
        self.assertEqual(waits[self.after], {middle, self.mock})
        a = {"id": self.mock, "files": ["engine/mockup.py"]}
        b = {"id": self.after, "files": ["engine/sandbox.py"]}
        self.assertEqual(parallel.why_not(a, b, waits), f"#{self.after} waits for #{self.mock}")
        db.update(self.con, "tasks", self.mock, status="done")
        self.assertEqual(parallel.why_not(a, b, parallel._waits(self.con)), "")

    def test_tasks_in_flight_count_with_their_files(self):
        db.insert(self.con, "dev_runs", task_id=self.backup, branch="hq-dev/x", status="pr_open")
        busy = db.insert(self.con, "tasks", title="Radar: a new source", owner="ai", status="doing")
        db.insert(self.con, "dev_runs", task_id=busy, branch="hq-dev/y", status="running")
        with mock.patch.object(parallel, "_branch_files", return_value=["engine/mockup.py", "engine/db.py"]):
            s = self.suggest(order=[self.radar, self.mock, self.after])
        self.assertEqual({f["id"]: f["state"] for f in s["in_flight"]}, {self.backup: "PR open", busy: "building"})
        why = {x["id"]: x["why"] for x in s["left_out"]}
        self.assertIn(f"#{busy} is building", why[self.radar])
        self.assertIn(f"#{self.backup} is PR open", why[self.mock])  # its branch really changed mockup.py
        self.assertEqual(s["tasks"], [])  # one task left: no batch

    def test_accept_dismiss_and_undo(self):
        with mock.patch.object(parallel, "ready", return_value=self.order):
            ids = [t["id"] for t in parallel.suggest(self.con)["tasks"]]
            with self.assertRaises(ValueError):
                parallel.accept(self.con, [self.radar, self.backup])  # not what is proposed now
            self.assertIn("accepted", parallel.accept(self.con, ids))
            b = parallel.accepted(self.con)
            self.assertEqual(b["open"], ids)
            self.assertIn("engine/radar.py", b["files"][str(self.radar)])
            self.assertTrue(parallel.may_run_alongside(self.con, self.mock))
            self.assertFalse(parallel.may_run_alongside(self.con, self.radar2))
            self.assertTrue(parallel.view(self.con)["same"])
            for t in ids:
                db.update(self.con, "tasks", t, status="done")
            self.assertIsNone(parallel.accepted(self.con))  # finished: over, without a write
            for t in ids:
                db.update(self.con, "tasks", t, status="todo")
            change = self.con.execute("SELECT id FROM brain_changes WHERE table_name = 'parallel_batches' ORDER BY id DESC").fetchone()[0]
            brain.undo(self.con, change)
            self.assertIsNone(parallel.accepted(self.con))
            parallel.dismiss(self.con, ids)
            self.assertEqual(parallel.suggest(self.con)["tasks"], [])  # the same set is not offered again
            with self.assertRaises(ValueError):
                parallel.accept(self.con)

    def test_through_the_real_build_order(self):
        priority.rescore(self.con)
        ready = parallel.ready(self.con)
        self.assertIn(self.radar, ready)
        self.assertNotIn(self.after, ready)  # waits for #mock
        self.assertEqual(len(parallel.suggest(self.con, parallel.DEFAULTS)["tasks"]), 3)
        self.assertIn("side by side", parallel.text(self.con))


class ThroughHub(unittest.TestCase):
    def test_read_and_accept(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hq.db"
            con = db.connect(path)
            db.init(con)
            a = db.insert(con, "tasks", title="Radar: follow more feeds", owner="ai", status="todo", impact=3, effort_h=1)
            b = db.insert(con, "tasks", title="Backup: keep seven days", owner="ai", status="todo", impact=3, effort_h=1)
            con.commit()
            con.close()
            server = hub.make_server("127.0.0.1", 0, TOKEN, path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with mock.patch.object(parallel.overlap, "modules", return_value=MODS):
                    v = get(url + "/v1/functions/engine.build.parallel")["result"]
                    self.assertEqual(sorted(t["id"] for t in v["proposal"]), [a, b])
                    r = post(url + "/v1/actions/engine.build.parallel_batch", {"accept": [t["id"] for t in v["proposal"]]})
                    self.assertIn("accepted", r["message"])
                    self.assertEqual(get(url + "/v1/functions/engine.build.parallel")["result"]["accepted"]["open"], sorted([a, b]))
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
