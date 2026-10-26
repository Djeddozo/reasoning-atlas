"""Review batch (#55): the agent keeps building until N of its PRs wait for review, then stops."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import dashboard, db, devloop, loop


class ReviewBatchTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con); loop.seed(self.con)

    def pr(self, status="todo"):
        t = db.insert(self.con, "tasks", title=f"task {status}", owner="ai", status=status)
        db.insert(self.con, "dev_runs", task_id=t, branch=f"b{t}", status="pr_open", started=db.now(), head="abc")
        self.con.commit()
        return t

    def test_default_and_limits(self):
        self.assertEqual(devloop.batch_size(self.con), 3)
        self.assertIn("every PR", devloop.set_batch(self.con, 1))
        self.assertEqual(devloop.batch_size(self.con), 1)
        with self.assertRaises(ValueError):
            devloop.set_batch(self.con, -1)
        with mock.patch.object(devloop, "cfg", lambda: devloop.DEFAULTS | {"max_open_prs": 5}):
            with self.assertRaises(ValueError):
                devloop.set_batch(self.con, 6)  # above max_open_prs
        with mock.patch.object(devloop, "cfg", lambda: devloop.DEFAULTS | {"max_open_prs": 2}):
            db.set_state(self.con, "devloop_batch", "5")
            self.assertEqual(devloop.batch_size(self.con), 2)  # a lower constitution wins
            self.assertEqual(devloop.overview(self.con)["batch_choices"], [1, 0])

    def test_unlimited_batch_never_stops_for_review(self):  # #178, quick thoughts #47 and #48
        self.assertEqual(devloop.overview(self.con)["batch_choices"], [1, 3, 5, 0])  # no max_open_prs: no ceiling
        self.assertIn("Never stops for review", devloop.set_batch(self.con, "unlimited"))
        self.assertEqual(devloop.batch_size(self.con), 0)
        for _ in range(8):
            self.pr()
        self.assertFalse(devloop.batch_full(self.con))
        import subprocess
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=devloop.ROOT, capture_output=True, text=True).stdout.strip()
        self.con.execute("UPDATE dev_runs SET head = ?", (head,))
        self.assertIsNotNone(devloop.stack_base(self.con))  # new branches still stack on the newest PR
        self.assertIn("8/unlimited PRs await review", devloop.status(self.con))
        devloop.set_batch(self.con, 0)
        o = devloop.overview(self.con)
        self.assertEqual((o["batch"], o["batch_choice"], o["batch_ceiling"]), (0, 0, None))

    def test_a_ceiling_caps_unlimited_and_says_so(self):
        devloop.set_batch(self.con, 0)
        with mock.patch.object(devloop, "cfg", lambda: devloop.DEFAULTS | {"max_open_prs": 5}):
            self.assertEqual(devloop.batch_size(self.con), 5)
            o = devloop.overview(self.con)
            self.assertEqual(o["batch_choice"], 0)  # the Unlimited chip stays selected
            self.assertIn("max_open_prs = 5", o["batch_text"])
            self.assertIn("builder-unlimited.sh", o["batch_text"])
            for _ in range(5):
                self.pr()
            self.assertTrue(devloop.batch_full(self.con))

    def test_open_prs_ignore_done_and_dropped_tasks(self):
        self.pr(); self.pr("doing"); self.pr("done"); self.pr("dropped")
        self.assertEqual(devloop.open_prs(self.con), 2)
        devloop.set_batch(self.con, 3)
        self.assertFalse(devloop.batch_full(self.con))
        self.pr()
        self.assertTrue(devloop.batch_full(self.con))

    def run_dev(self, force=False):
        conf = devloop.DEFAULTS | {"enabled": True}
        with mock.patch.object(devloop, "cfg", lambda: conf), mock.patch.object(devloop, "claude_path", return_value="/bin/true"), \
                mock.patch.object(devloop, "sync_merged", return_value=[]), \
                mock.patch.object(devloop, "_run_task", return_value={"status": "pr_open"}) as run_task, \
                tempfile.TemporaryDirectory() as d, \
                mock.patch.object(devloop, "WORK", Path(d)), mock.patch.object(devloop, "LOCK", Path(d) / "lock"):
            return devloop.run(self.con, force=force), run_task

    def test_run_stops_when_batch_is_full(self):
        db.insert(self.con, "tasks", title="next thing", owner="ai"); self.con.commit()
        devloop.set_batch(self.con, 1)
        self.pr()
        result, run_task = self.run_dev()
        self.assertEqual(result["status"], "review")
        run_task.assert_not_called()
        self.assertIn("waiting for your review", devloop.report(result))
        result, run_task = self.run_dev(force=True)  # Run now by hand still works
        run_task.assert_called_once()

    def test_continuous_chain_stops_at_the_batch(self):
        from engine import cli
        devloop.set_autonomy(self.con, "continuous"); devloop.set_batch(self.con, 1)
        self.pr()
        with mock.patch.object(cli, "_con", lambda: self.con), \
                mock.patch.object(devloop, "run", return_value={"status": "pr_open", "task": 1, "title": "x", "summary": "", "pr": ""}), \
                mock.patch.object(devloop, "why_not_now", return_value=""), \
                mock.patch.object(devloop, "notify") as note, mock.patch.object(devloop, "start_background") as nxt:
            cli.main(["dev", "run"])
        nxt.assert_not_called()
        self.assertIn("Review batch reached", note.call_args[0][0])

    def test_dashboard_and_cli(self):
        from engine import cli
        self.assertIn("5 PRs", dashboard.act(self.con, "batch", None, "5"))
        self.assertEqual(dashboard.build_data(self.con)["agent"]["batch"], 5)
        with self.assertRaises(ValueError):
            dashboard.act(self.con, "batch", None, "lots")
        with mock.patch.object(cli, "_con", lambda: self.con):
            cli.main(["dev", "batch", "1"])
        self.assertEqual(devloop.batch_size(self.con), 1)
        self.assertIn("0/1 PRs await review", devloop.status(self.con))


if __name__ == "__main__":
    unittest.main()
