"""Task #162: the builder treats a run as finished right after Claude Code's final result,
not after leftovers (a background shell, a lingering process) release its output pipe."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from engine import db, devloop, usage

RESULT = "print(json.dumps({'type': 'result', 'result': 'done', 'is_error': False, 'num_turns': 3}), flush=True)\n"
LEFTOVER = ("subprocess.Popen([sys.executable, '-c', 'import os, sys, time; open(sys.argv[1], \"w\").write(str(os.getpid())); "
            "time.sleep(60)', %r])\ntime.sleep(0.5)\n")


class FinishTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.con = db.connect(self.root / "live.db")
        self.addCleanup(self.con.close)
        db.init(self.con)
        patches = [mock.patch.object(devloop, "WORK", self.root / "runs"), mock.patch.object(devloop, "EXIT_GRACE", 0.5),
                   mock.patch.object(devloop, "WATCH_SECONDS", 0.2), mock.patch.object(usage, "killed", return_value=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.pidfile = self.root / "leftover.pid"

    def agent(self, body: str):
        code = "import json, subprocess, sys, time\n" + body
        t0 = time.monotonic()
        out = devloop.run_agent(self.con, [sys.executable, "-c", code], self.root, 1, 5)
        return out, time.monotonic() - t0

    def assert_leftover_gone(self):
        pid = int(self.pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            try:  # a zombie still answers kill(0); it is gone once its state is Z or the entry vanished
                if Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0] == "Z":
                    return
            except (OSError, IndexError):
                return
            time.sleep(0.1)
        self.fail(f"leftover process {pid} still runs")

    def test_background_child_holding_stdout_does_not_delay_the_finish(self):
        (summary, failed, used), took = self.agent(LEFTOVER % str(self.pidfile) + RESULT)
        self.assertLess(took, 10)
        self.assertEqual((summary, failed, used["turns"]), ("done", False, 3))
        self.assert_leftover_gone()

    def test_agent_lingering_after_its_result_is_not_waited_for(self):
        (summary, failed, _), took = self.agent(RESULT + "time.sleep(60)\n")
        self.assertLess(took, 10)
        self.assertEqual((summary, failed), ("done", False))  # stopping it after the result is not a failure

    def test_crash_without_result_with_a_leftover_still_ends_quickly(self):
        (summary, failed, _), took = self.agent(LEFTOVER % str(self.pidfile) + "sys.exit(1)\n")
        self.assertLess(took, 10)
        self.assertTrue(failed)
        self.assert_leftover_gone()

    def test_finish_steps_are_logged(self):
        self.agent(RESULT)
        log = devloop.log_path(1).read_text()
        self.assertIn("finished after 3 turns", log)


if __name__ == "__main__":
    unittest.main()
