"""Progress tracking tests: completion times, streaks, goal progress, weekly review, Telegram /progress."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import sqlite3
import unittest
from datetime import date, timedelta
from unittest import mock

from engine import dashboard, db, loop, models, progress

TODAY = date(2026, 10, 14)  # a Wednesday; the week starts Monday 12 October


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.row_factory = sqlite3.Row
        db.init(self.con)
        self.goal = db.insert(self.con, "goals", title="Pass the exams")
        p = mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("off"))
        p.start(); self.addCleanup(p.stop)

    def task(self, title="t", done_on: date | None = None, **kw):
        tid = db.insert(self.con, "tasks", title=title, **kw)
        if done_on:
            db.update(self.con, "tasks", tid, status="done", done_at=f"{done_on.isoformat()}T10:00:00")
        return tid

    def test_goal_glance_has_percent_week_and_next(self):
        self.task("old", done_on=TODAY - timedelta(days=9), goal_id=self.goal)
        self.task("new", done_on=TODAY, goal_id=self.goal)
        nxt = self.task("Next step", goal_id=self.goal)
        self.task("Later", goal_id=self.goal, status="proposed")
        g = progress.stats(self.con, TODAY)["goals"][0]
        self.assertEqual((g["done"], g["total"], g["done_week"]), (2, 4, 1))
        self.assertEqual(g["next"]["id"], nxt)
        text = progress.summary(progress.stats(self.con, TODAY))
        self.assertIn("1 this week", text)
        self.assertIn("Next: #%d Next step" % nxt, text)

    def test_done_at_is_recorded_and_cleared(self):
        tid = self.task()
        db.update(self.con, "tasks", tid, status="done")
        self.assertTrue(self.con.execute("SELECT done_at FROM tasks WHERE id = ?", (tid,)).fetchone()[0])
        db.update(self.con, "tasks", tid, status="todo")  # undo
        self.assertIsNone(self.con.execute("SELECT done_at FROM tasks WHERE id = ?", (tid,)).fetchone()[0])

    def test_marking_done_again_keeps_the_original_time(self):
        tid = self.task(done_on=TODAY - timedelta(days=9))
        db.update(self.con, "tasks", tid, status="done")
        self.assertEqual(self.con.execute("SELECT done_at FROM tasks WHERE id = ?", (tid,)).fetchone()[0],
                         f"{(TODAY - timedelta(days=9)).isoformat()}T10:00:00")

    def test_older_done_tasks_are_backfilled(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, status TEXT, updated TEXT)")
        con.execute("INSERT INTO tasks (title, status, updated) VALUES ('old', 'done', '2026-09-01T09:00:00')")
        db.init(con)
        self.assertEqual(con.execute("SELECT done_at FROM tasks").fetchone()[0], "2026-09-01T09:00:00")

    def test_streaks(self):
        days = {TODAY - timedelta(days=i) for i in (1, 2, 3)} | {TODAY - timedelta(days=i) for i in (10, 11, 12, 13, 14)}
        self.assertEqual(progress.streaks(days, TODAY), (3, 5))  # today not done yet: streak still alive
        self.assertEqual(progress.streaks(days | {TODAY}, TODAY), (4, 5))
        self.assertEqual(progress.streaks({TODAY - timedelta(days=2)}, TODAY), (0, 1))
        self.assertEqual(progress.streaks(set(), TODAY), (0, 0))

    def test_stats(self):
        self.task("a", TODAY, goal_id=self.goal)
        self.task("b", TODAY - timedelta(days=1), goal_id=self.goal)          # Tuesday, this week
        self.task("c", TODAY - timedelta(days=5))                             # Friday last week
        self.task("late", goal_id=self.goal, due=(TODAY - timedelta(days=3)).isoformat())
        self.task("soon", due=(TODAY + timedelta(days=2)).isoformat())
        stale = self.task("stuck")
        db.update(self.con, "tasks", stale, status="doing")
        self.con.execute("UPDATE tasks SET updated = '2026-10-01T09:00:00' WHERE id = ?", (stale,))
        s = progress.stats(self.con, TODAY)
        self.assertEqual((s["done_today"], s["done_week"], s["done_last_week"]), (1, 2, 1))
        self.assertEqual(s["streak"], 2)
        self.assertEqual(len(s["daily"]), 28)
        self.assertEqual(s["daily"][-1], {"date": TODAY.isoformat(), "done": 1})
        g = s["goals"][0]
        self.assertEqual((g["done"], g["total"], g["pct"], g["done_week"], g["overdue"]), (2, 3, 67, 2, 1))
        self.assertEqual([(t["title"], t["days_late"]) for t in s["overdue"]], [("late", 3)])
        self.assertEqual([t["title"] for t in s["due_soon"]], ["soon"])
        self.assertEqual([t["title"] for t in s["stalled"]], ["stuck"])
        self.assertEqual(s["recent"][0]["title"], "a")
        text = progress.summary(s)
        self.assertIn("This week: 2 done (+1 vs last week)", text)
        self.assertIn("Pass the exams: 2/3 (67%), 2 this week, 1 overdue", text)

    def test_weekly_review_is_saved(self):
        self.task("Finish essay", TODAY, goal_id=self.goal)
        self.task("Revise chapter 3", due=(TODAY - timedelta(days=1)).isoformat())
        text = progress.review(self.con, TODAY)
        self.assertIn("Completed 1 task(s)", text)
        self.assertIn("• Finish essay", text)
        self.assertIn("Slipped:", text)
        self.assertIn("Focus next week:", text)
        self.assertEqual(progress.last_review(self.con)["content"], text)

    def test_review_adds_model_note_when_available(self):
        with mock.patch.object(models, "complete", return_value="Steady week.") as m:
            text = progress.review(self.con, TODAY, save=False)
        self.assertTrue(text.endswith("Steady week."))
        self.assertEqual(m.call_args[0][0], "review")
        self.assertIsNone(progress.last_review(self.con))

    def test_dashboard_includes_progress(self):
        loop.seed(self.con)
        data = dashboard.build_data(self.con)
        self.assertIn("streak", data["progress"])
        self.assertIsNone(data["review"])
        tid = self.task("via dashboard")
        db.update(self.con, "tasks", tid, status="done"); self.con.commit()  # only the agent completes tasks
        self.assertEqual(dashboard.build_data(self.con)["progress"]["done_today"], 1)


class TelegramProgressTest(unittest.TestCase):
    def setUp(self):
        from engine.interfaces import telegram
        from tests.test_telegram import CHAT, FakeTelegram, msg
        self.msg = msg
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.row_factory = sqlite3.Row
        db.init(self.con)
        loop.seed(self.con)
        self.fake = FakeTelegram()
        p = mock.patch.object(telegram, "api", self.fake); p.start(); self.addCleanup(p.stop)
        self.bot = telegram.Bot(self.con, "TOKEN", CHAT, {"poll_seconds": 0})

    def test_progress_command(self):
        tid = db.insert(self.con, "tasks", title="Book driving lesson")
        db.update(self.con, "tasks", tid, status="done")  # done by the agent; /done is refused (tests/test_task_actions.py)
        self.con.commit()
        self.fake.updates = [self.msg(1, "/progress")]
        self.bot.poll_once()
        self.assertIn("This week: 1 done", self.fake.sent()[0]["text"])


if __name__ == "__main__":
    unittest.main()
