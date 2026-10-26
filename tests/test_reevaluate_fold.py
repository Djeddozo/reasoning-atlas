"""#124: engine.brain.reevaluate_plan folded into engine.build.do_over: one registry entry, one path (doover.replan)."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import argparse
import contextlib
import io
import json
import pathlib
import sqlite3
import unittest
from unittest import mock

from engine import cli, db, doover

ROOT = pathlib.Path(__file__).resolve().parent.parent


class RegistryFold(unittest.TestCase):
    def setUp(self):
        self.caps = {c["id"]: c for c in json.loads((ROOT / "modules/engine.json").read_text())["capabilities"]}

    def test_one_entry_in_forge_plan(self):
        self.assertNotIn("engine.brain.reevaluate_plan", self.caps)
        do_over = self.caps["engine.build.do_over"]
        self.assertEqual(do_over["atlasSection"], "Forge › Plan")
        self.assertIn("engine.brain.reevaluate_plan", do_over["note"])
        self.assertIn("engine.build.do_over", self.caps["engine.brain.review_suggestions"]["requires"])

    def test_no_capability_requires_the_removed_one(self):
        for c in self.caps.values():
            self.assertNotIn("engine.brain.reevaluate_plan", c.get("requires", []), c["id"])


class OnePath(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.calls = []
        p = mock.patch.object(doover, "replan", lambda con, force=False: self.calls.append(force) or
                              {"reevaluate": {"applied": 1}, "drift": [], "approval": None})
        p.start(); self.addCleanup(p.stop)

    def test_cli_brain_think_is_the_drift_based_do_over(self):
        with mock.patch.object(cli, "_con", lambda: self.con), contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_brain(argparse.Namespace(action="think", id=None))
        self.assertEqual(self.calls, [False])  # the daily cron: a do-over is drafted only when the plan drifted
        self.assertIn("Re-evaluated: 1 change(s).", out.getvalue())

    def test_telegram_think_is_the_full_do_over(self):
        from engine.interfaces import telegram
        sent = []
        bot = telegram.Bot.__new__(telegram.Bot)
        bot.send = sent.append
        bot.think_job(self.con)
        self.assertEqual(self.calls, [True])  # the owner asked, as with the Forge › Plan button
        self.assertIn("not drifted", sent[0])


if __name__ == "__main__":
    unittest.main()
