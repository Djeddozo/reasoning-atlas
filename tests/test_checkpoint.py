import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("HQ_DB", tempfile.mktemp(suffix=".db"))
from engine import checkpoint, db


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        git(self.tree, "init", "-q")
        git(self.tree, "config", "user.email", "a@b.c")
        git(self.tree, "config", "user.name", "t")
        (self.tree / "a.txt").write_text("one\n")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-qm", "base")
        self.start = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tree, capture_output=True, text=True).stdout.strip()
        self.con = db.connect(self.tmp / "t.db"); db.init(self.con)
        self.task = {"id": 7, "title": "Demo task"}

    def test_tracker_thresholds(self):
        t = checkpoint.Tracker(max_turns=10, max_minutes=10, context_tokens=1000)
        self.assertEqual(t.due(0), "")
        for _ in range(8):
            t.observe({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        self.assertEqual(t.due(0), "")
        t.observe({"type": "assistant", "message": {"usage": {"input_tokens": 900}, "content": []}})
        self.assertEqual(t.due(0), "turns")
        t.saved = True
        self.assertEqual(t.due(0), "")

    def test_time_and_context(self):
        t = checkpoint.Tracker(100, 10, 1000)
        self.assertEqual(t.due(560), "time")
        t.observe({"type": "assistant", "message": {"usage": {"cache_read_input_tokens": 950}, "content": []}})
        self.assertEqual(t.due(0), "context")

    def test_save_and_resume(self):
        (self.tree / "a.txt").write_text("one\ntwo\n")
        (self.tree / "new.txt").write_text("x")
        tr = checkpoint.Tracker(10, 10)
        tr.last_text = "halfway through the parser"
        mid = checkpoint.save(self.con, self.tmp, self.task, "hq-dev/7-demo", self.tree, self.start, "turns", tr, ["12:00 → Edit: a.txt"])
        row = checkpoint.latest(self.con, 7)
        self.assertEqual(row["id"], mid)
        for needle in ("halfway through the parser", "new.txt", "Resume prompt", "Next steps", "hq-dev/7-demo"):
            self.assertIn(needle, row["content"])
        patch = checkpoint.patch_path(self.tmp, 7)
        self.assertIn("new.txt", patch.read_text())
        self.assertTrue(tr.saved)
        # working tree is left as it was (index reset only)
        self.assertEqual((self.tree / "new.txt").read_text(), "x")
        block = checkpoint.resume_block(self.con, 7)
        self.assertIn("interrupted run", block)
        self.assertEqual(checkpoint.resume_block(self.con, 8), "")

    def test_save_refreshes_and_clear(self):
        a = checkpoint.save(self.con, self.tmp, self.task, "b", self.tree, self.start, "turns")
        b = checkpoint.save(self.con, self.tmp, self.task, "b", self.tree, self.start, "time", final="later note")
        self.assertEqual(a, b)
        self.assertIn("later note", checkpoint.latest(self.con, 7)["content"])
        checkpoint.clear(self.con, 7)
        self.assertIsNone(checkpoint.latest(self.con, 7))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM memories WHERE tags LIKE 'checkpoint-done%'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
