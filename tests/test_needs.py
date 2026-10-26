"""Needs your input: decisions, approvals and his own tasks the agent waits for, answered in place."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import sqlite3
import unittest
from unittest import mock

from engine import dashboard, db, loop, models, needs
from engine.interfaces import telegram


class NeedsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.mine = db.insert(self.con, "tasks", title="Create the Telegram bot account", owner="me")
        self.loose = db.insert(self.con, "tasks", title="Read a book", owner="me")
        self.ai = db.insert(self.con, "tasks", title="Wire the bot into Atlas", owner="ai", depends_on=str(self.mine))
        self.dec = db.insert(self.con, "approvals", request="Voice: local or cloud?", reason="blocks voice",
                             category="decision", options="Local|Cloud|Later")
        self.appr = db.insert(self.con, "approvals", request="Install whisper.cpp", category="install", cost_eur=0,
                              task_id=self.ai)
        self.con.commit()

    def keys(self):
        return [i["key"] for i in needs.items(self.con)]

    def test_lists_only_what_needs_him(self):
        self.assertEqual(self.keys(), [f"D{self.dec}", f"A{self.appr}", f"T{self.mine}"])
        t = needs.items(self.con)[2]
        self.assertEqual(t["holds"], [{"id": self.ai, "title": "Wire the bot into Atlas"}])
        self.assertIn("1 task paused", t["why"])
        self.assertEqual(needs.items(self.con)[1]["holds"][0]["id"], self.ai)
        # his task without a waiting agent task is not prompted; nor once the agent task is done
        self.assertNotIn(f"T{self.loose}", self.keys())
        db.update(self.con, "tasks", self.ai, status="done")
        self.assertNotIn(f"T{self.mine}", self.keys())

    def test_decision_option_or_own_answer(self):
        self.assertEqual(needs.answer_key(self.con, f"d{self.dec}", "cloud"), f"Decided #{self.dec}: Cloud")
        row = self.con.execute("SELECT status, answer FROM approvals WHERE id = ?", (self.dec,)).fetchone()
        self.assertEqual(tuple(row), ("answered", "Cloud"))
        self.assertTrue(self.con.execute("SELECT 1 FROM memories WHERE kind='decision' AND content LIKE '%→ Cloud'").fetchone())
        with self.assertRaises(ValueError):  # already settled
            needs.answer_key(self.con, f"D{self.dec}", "Local")
        d2 = db.insert(self.con, "approvals", request="Name?", options="A|B")
        self.assertIn("your own answer", needs.answer_key(self.con, f"D{d2}", "Neither, call it Atlas"))
        self.assertEqual(self.con.execute("SELECT answer FROM approvals WHERE id = ?", (d2,)).fetchone()[0], "Neither, call it Atlas")

    def test_approval_needs_a_verdict(self):
        with self.assertRaises(ValueError):
            needs.answer_key(self.con, f"A{self.appr}", "only a note")
        needs.answer_key(self.con, f"A{self.appr}", "ja, but pin the version")
        row = self.con.execute("SELECT status, answer FROM approvals WHERE id = ?", (self.appr,)).fetchone()
        self.assertEqual(tuple(row), ("approved", "but pin the version"))

    def test_task_input_goes_through_the_dump(self):
        reply = json.dumps({"done": [{"task_id": self.mine, "evidence": "made the bot"}], "summary": "closed it"})
        with mock.patch.object(models, "complete", return_value=reply) as m:
            needs.answer_key(self.con, f"T{self.mine}", "I made the bot, token is in .env")
        self.assertIn(f"About my task #{self.mine}", m.call_args[0][2])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.mine,)).fetchone()[0], "done")
        self.assertNotIn(f"T{self.mine}", self.keys())

    def test_bad_keys(self):
        for k in ("12", "X3", "D", ""):
            with self.assertRaises(ValueError):
                needs.answer_key(self.con, k, "x")

    def test_dashboard_and_brief(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("off")):
            data = dashboard.build_data(self.con)
            self.assertEqual([i["key"] for i in data["needs"]], self.keys())
            r = dashboard.act(self.con, "needs_answer", None, json.dumps({"key": f"A{self.appr}", "verdict": "denied"}))
            self.assertTrue(r.startswith("Denied"))
            self.assertIn(f"T{self.mine} Create the Telegram bot account", loop.brief(self.con, write=False))

    def test_telegram_commands(self):
        bot = telegram.Bot(self.con, "t", "1")
        self.assertIn(f"D{self.dec} Voice: local or cloud?", bot.cmd_needs())
        self.assertEqual(bot.cmd_answer(f"D{self.dec} Later"), f"Decided #{self.dec}: Later")
        self.assertIn("Usage", bot.cmd_answer(""))
        self.assertIn("not an item", bot.cmd_answer("zz hello"))


if __name__ == "__main__":
    unittest.main()
