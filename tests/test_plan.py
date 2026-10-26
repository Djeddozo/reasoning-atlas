"""The plan tree: nested goals, tasks per goal, rolled-up progress, unlinked tasks."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import sqlite3
import unittest

from engine import dashboard, db, loop, plan


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.year = db.insert(self.con, "goals", title="Finish school well", horizon="year")
        self.q = db.insert(self.con, "goals", title="Pass the autumn exams", horizon="quarter", parent_id=self.year)
        self.life = db.insert(self.con, "goals", title="Be independent", horizon="life")
        self.t1 = db.insert(self.con, "tasks", title="Revise maths", goal_id=self.q, status="doing")
        self.t2 = db.insert(self.con, "tasks", title="Book exam", goal_id=self.q)
        db.update(self.con, "tasks", self.t2, status="done")
        self.t3 = db.insert(self.con, "tasks", title="Plan the year", goal_id=self.year)
        self.loose = db.insert(self.con, "tasks", title="Fix bike")
        self.con.commit()

    def test_tree(self):
        t = plan.tree(self.con)
        self.assertEqual([g["title"] for g in t["goals"]], ["Be independent", "Finish school well"])  # life before year
        year = t["goals"][1]
        self.assertEqual(year["children"][0]["title"], "Pass the autumn exams")
        self.assertEqual((year["done"], year["total"], year["pct"]), (1, 3, 33))  # includes the quarter goal's tasks
        q = year["children"][0]
        self.assertEqual([x["title"] for x in q["tasks"]], ["Revise maths"])     # open tasks, doing first
        self.assertEqual((q["done_count"], q["recent_done"], q["depth"]), (1, ["Book exam"], 1))
        self.assertEqual([x["title"] for x in t["loose"]], ["Fix bike"])

    def test_nesting_and_linking(self):
        plan.set_parent(self.con, self.year, self.life)
        self.assertEqual(plan.tree(self.con)["goals"][0]["children"][0]["title"], "Finish school well")
        with self.assertRaises(ValueError):
            plan.set_parent(self.con, self.life, self.q)  # would create a loop
        with self.assertRaises(ValueError):
            plan.set_parent(self.con, self.q, self.q)
        plan.link_task(self.con, self.loose, self.life)
        self.assertEqual(plan.tree(self.con)["loose"], [])
        with self.assertRaises(ValueError):
            plan.link_task(self.con, self.loose, 999)
        with self.assertRaises(ValueError):
            plan.link_task(self.con, 999, self.life)   # unknown task
        with self.assertRaises(ValueError):
            plan.set_parent(self.con, 999, self.life)  # unknown goal

    def test_loop_in_data_still_shows_goals(self):
        self.con.execute("UPDATE goals SET parent_id = ? WHERE id = ?", (self.q, self.year))  # year ↔ quarter loop
        titles = [g["title"] for g in plan.tree(self.con)["goals"]]
        self.assertIn("Finish school well", titles + [c["title"] for g in plan.tree(self.con)["goals"] for c in g["children"]])

    def test_cli_rejects_unknown_parent(self):
        from unittest import mock
        from engine import cli
        with mock.patch.object(cli, "_con", lambda: self.con), self.assertRaises(SystemExit) as e:
            cli.main(["add", "goal", "Typo parent", "--parent", "999"])
        self.assertIn("does not exist", str(e.exception))
        self.assertFalse(self.con.execute("SELECT 1 FROM goals WHERE title = 'Typo parent'").fetchone())

    def test_text(self):
        out = plan.text(plan.tree(self.con))
        self.assertIn("Link one: ./atlas.sh attach <task> <goal>", out)
        self.assertIn("◆ Finish school well  [year] 1/3 (33%)", out)
        self.assertIn("   ◆ Pass the autumn exams  [quarter] 1/2 (50%)", out)
        self.assertIn("      ▶ #%d Revise maths (me)" % self.t1, out)
        self.assertIn("Not linked to a goal (1):", out)

    def test_dashboard(self):
        loop.seed(self.con)
        data = dashboard.build_data(self.con)
        self.assertTrue(data["plan"]["goals"])
        dashboard.act(self.con, "attach", self.loose, str(self.q))
        dashboard.act(self.con, "nest", self.life, str(self.year))
        dashboard.act(self.con, "nest", self.life, "")
        self.assertIsNone(self.con.execute("SELECT parent_id FROM goals WHERE id = ?", (self.life,)).fetchone()[0])
        self.assertEqual(self.con.execute("SELECT goal_id FROM tasks WHERE id = ?", (self.loose,)).fetchone()[0], self.q)

    def test_telegram_plan(self):
        from unittest import mock
        from engine.interfaces import telegram
        from tests.test_telegram import CHAT, FakeTelegram, msg
        fake = FakeTelegram(); fake.updates = [msg(1, "/plan")]
        with mock.patch.object(telegram, "api", fake):
            telegram.Bot(self.con, "TOKEN", CHAT, {"poll_seconds": 0}).poll_once()
        self.assertIn("◆ Pass the autumn exams", fake.sent()[0]["text"])


if __name__ == "__main__":
    unittest.main()
