import json, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path
from unittest import mock
from services.atlas import handoff, progress, server

ROOT = Path(__file__).resolve().parents[1]


def post(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(request))


class Handoff(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.status = Path(self.folder.name) / "build-status.json"
        self.patch = mock.patch.object(progress, "STATUS_FILE", self.status)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.folder.cleanup()

    def test_packet_carries_task_rules_and_stop_point(self):
        progress.start("RA-021", "Build the Systems view", ["Layout", "Detail", "Tests"])
        progress.step(2)
        text = handoff.packet("RA-021", "claude-desktop", ROOT)["text"]
        for expected in ("RA-021 — Build the Systems view", "Acceptance:", "AGENTS.md", "scripts/progress.py start RA-021",
                         "push every checkpoint", "scripts/remind.py", "step 2 of 3", str(ROOT)):
            self.assertIn(expected, text)
        self.assertEqual(handoff.packet("RA-020", "codex", ROOT)["toolName"], "Codex (ChatGPT app)")

    def test_rejects_unknown_task_or_tool(self):
        with self.assertRaises(ValueError):
            handoff.packet("RA-999", "claude-desktop", ROOT)
        with self.assertRaises(ValueError):
            handoff.packet("RA-021", "engine", ROOT)

    def test_active_task_comes_first(self):
        progress.start("RA-026", "Workbench", ["One"])
        progress.step(1)
        tasks = handoff.candidates(ROOT)
        self.assertEqual(tasks[0]["id"], "RA-026")
        self.assertTrue(tasks[0]["active"])
        self.assertTrue(all(t["status"] in ("in_progress", "review", "ready") for t in tasks[1:]))

    def test_git_head_reads_loose_and_packed_refs(self):
        git = Path(self.folder.name) / "repo/.git"
        (git / "refs/heads").mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/main\n")
        (git / "packed-refs").write_text("# pack-refs\nabcdef1234567890 refs/heads/main\n")
        self.assertEqual(handoff.git_head(git.parent), ("main", "abcdef1"))
        (git / "refs/heads/main").write_text("1234567abcdef\n")
        self.assertEqual(handoff.git_head(git.parent), ("main", "1234567"))
        self.assertEqual(handoff.git_head(Path(self.folder.name) / "none"), (None, None))

    def test_http_logs_switches(self):
        httpd = server.make_server(0, self.folder.name + "/t.db")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{httpd.server_port}"
        try:
            self.assertIn("Continue building", post(url + "/api/handoff", {"taskId": "RA-021", "tool": "codex"})["text"])
            post(url + "/api/handoff", {"taskId": "RA-021", "tool": "codex"})
            data = json.load(urllib.request.urlopen(url + "/api/continue"))
            self.assertEqual(data["handoffs"]["counts"], [{"intent": "code.edit", "tool": "codex", "count": 2}])
            self.assertIsNone(data["route"]["here"])
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(url + "/api/handoff", {"taskId": "RA-021", "tool": "atlas"})
            self.assertEqual(ctx.exception.code, 400)
        finally:
            httpd.shutdown(); httpd.server_close()
