from contextlib import closing, redirect_stdout
import io, json, sqlite3, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path
from unittest import mock
from services.atlas import actions, server

AGENT = {"schemaVersion": 1, "actions": [
    {"id": "publish", "title": "Publish the repos", "context": "Prepared by an agent.",
     "steps": [{"id": "check", "text": "Dry run", "command": "bash publish.sh --check"}, {"id": "go", "text": "Publish"}],
     "answered": [{"q": "Where do I run it?", "a": "In a terminal on the Pi."}], "ask": "Anything unclear?"},
    {"id": "broken"}]}


def post(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(request))


class Actions(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.agent_file = root / "owner-actions.json"
        self.agent_file.write_text(json.dumps(AGENT), encoding="utf-8")
        self.patch = mock.patch.object(actions, "AGENT_FILE", self.agent_file)
        self.patch.start()
        self.db = str(root / "atlas.db")

    def tearDown(self):
        self.patch.stop()
        self.folder.cleanup()

    def test_items_without_title_are_ignored(self):
        self.assertEqual([i["id"] for i in actions.agent_items()], ["publish"])

    def test_ticks_input_and_done_are_local(self):
        store = server.Store(self.db)
        before = self.agent_file.read_text(encoding="utf-8")
        store.update_action("publish", "step", {"step": "check", "done": True})
        store.update_action("publish", "input", {"input": "Ran it, all green"})
        store.update_action("publish", "done", {"done": True})
        item = store.actions()[0]
        self.assertTrue(item["steps"][0]["doneAt"])
        self.assertIsNone(item["steps"][1]["doneAt"])
        self.assertEqual(item["input"], "Ran it, all green")
        self.assertTrue(item["doneAt"])
        self.assertEqual(self.agent_file.read_text(encoding="utf-8"), before)  # the repository file never changes
        store.update_action("publish", "step", {"step": "check", "done": False})
        store.update_action("publish", "done", {"done": False})
        item = store.actions()[0]
        self.assertIsNone(item["steps"][0]["doneAt"])
        self.assertIsNone(item["doneAt"])
        self.assertEqual(item["input"], "Ran it, all green")  # reopening keeps the input

    def test_unknown_items_and_steps_are_refused(self):
        store = server.Store(self.db)
        with self.assertRaises(KeyError):
            store.update_action("nope", "input", {"input": "x"})
        with self.assertRaises(KeyError):
            store.update_action("publish", "step", {"step": "nope", "done": True})
        with self.assertRaises(ValueError):
            store.update_action("publish", "done", {"done": "yes"})

    def test_prompt_carries_context_progress_answers_and_input(self):
        store = server.Store(self.db)
        store.update_action("publish", "step", {"step": "check", "done": True})
        store.update_action("publish", "input", {"input": "My note"})
        text = store.actions()[0]["prompt"]
        for part in ("`publish`", "Prepared by an agent.", "1/2 steps done", "- [x] Dry run", "- [ ] Publish",
                     "$ bash publish.sh --check", "Q: Where do I run it?", "Anything unclear?", "My note"):
            self.assertIn(part, text)

    def test_schema_version(self):
        server.Store(self.db)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)

    def test_cli_reads_the_app_database_read_only(self):
        store = server.Store(self.db)
        store.update_action("publish", "input", {"input": "From the screen"})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(actions.main(["--db", self.db, "list"]), 0)
        self.assertIn("publish", out.getvalue())
        self.assertIn("0/2", out.getvalue())
        self.assertIn("[has input]", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(actions.main(["--db", self.db, "prompt", "publish"]), 0)
        self.assertIn("From the screen", out.getvalue())
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(actions.main(["--db", self.db, "show", "nope"]), 1)

    def test_cli_without_database(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(actions.main(["--db", str(Path(self.folder.name) / "missing.db"), "show", "publish"]), 0)
        self.assertIn("(none yet)", out.getvalue())

    def test_http_endpoints(self):
        httpd = server.make_server(0, self.db)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            post(f"{base}/api/actions/publish/step", {"step": "go", "done": True})
            post(f"{base}/api/actions/publish/input", {"input": "via http"})
            items = json.load(urllib.request.urlopen(f"{base}/api/actions"))
            self.assertEqual(items[0]["input"], "via http")
            self.assertTrue(items[0]["steps"][1]["doneAt"])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                post(f"{base}/api/actions/nope/done", {"done": True})
            self.assertEqual(caught.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                post(f"{base}/api/actions/publish/delete", {"done": True})
            self.assertEqual(caught.exception.code, 404)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
