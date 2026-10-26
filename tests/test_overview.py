"""The full overview: every task and idea, filters, and what blocks what."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import sqlite3
import unittest
from datetime import date

from engine import dashboard, db, overview

TODAY = date(2026, 10, 12)


class OverviewTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.year = db.insert(self.con, "goals", title="Finish school", horizon="year")
        self.q = db.insert(self.con, "goals", title="Autumn exams", horizon="quarter", parent_id=self.year)
        self.base = db.insert(self.con, "tasks", title="Set up Pi", goal_id=self.year, due="2026-10-01")
        self.dep = db.insert(self.con, "tasks", title="Run Atlas on Pi", goal_id=self.q, depends_on=str(self.base),
                             due="2026-10-15")
        self.done = db.insert(self.con, "tasks", title="Buy Pi")
        db.update(self.con, "tasks", self.done, status="done")
        self.dropped = db.insert(self.con, "tasks", title="Old plan")
        db.update(self.con, "tasks", self.dropped, status="dropped")
        self.idea = db.insert(self.con, "ideas", title="Sell prints")
        self.promoted = db.insert(self.con, "ideas", title="Tutoring", gate="promoted", task_id=self.done)
        self.con.commit()

    def ids(self, data):
        return [t["id"] for t in data["tasks"]], [i["id"] for i in data["ideas"]]

    def test_default_open_work_and_blocking(self):
        data = overview.items(self.con, today=TODAY)
        tasks, ideas = self.ids(data)
        self.assertEqual(tasks, [self.base, self.dep])                # todo before blocked; done and dropped hidden
        self.assertEqual(ideas, [self.idea])                          # promoted ideas are tasks now
        base, dep = data["tasks"]
        self.assertEqual(dep["status"], "blocked")
        self.assertEqual((dep["blocked_by"], base["blocks"]), ([self.base], [self.dep]))
        self.assertTrue(base["overdue"]); self.assertFalse(dep["overdue"])
        self.assertEqual(data["counts"]["done"], 1); self.assertEqual(data["counts"]["ideas"], 1)

    def test_all_includes_done(self):
        tasks, ideas = self.ids(overview.items(self.con, include_done=True, today=TODAY))
        self.assertIn(self.done, tasks); self.assertNotIn(self.dropped, tasks)
        self.assertEqual(ideas, [self.idea, self.promoted])

    def test_status_filter_spans_tasks_and_ideas(self):
        tasks, ideas = self.ids(overview.items(self.con, status={"dropped", "promoted"}, today=TODAY))
        self.assertEqual((tasks, ideas), ([self.dropped], [self.promoted]))

    def test_goal_filter_includes_subgoals_and_hides_ideas(self):
        tasks, ideas = self.ids(overview.items(self.con, goal=self.year, today=TODAY))
        self.assertEqual((tasks, ideas), ([self.base, self.dep], []))
        self.assertEqual(self.ids(overview.items(self.con, goal=self.q, today=TODAY))[0], [self.dep])
        self.assertEqual(self.ids(overview.items(self.con, goal=0, include_done=True, today=TODAY))[0], [self.done])

    def test_due_filter(self):
        f = lambda d: self.ids(overview.items(self.con, due=d, include_done=True, today=TODAY))[0]
        self.assertEqual(f("overdue"), [self.base])
        self.assertEqual(f("week"), [self.base, self.dep])
        self.assertEqual(f("2026-10-05"), [self.base])
        self.assertEqual(f("none"), [self.done])
        with self.assertRaises(ValueError):
            overview.items(self.con, due="soon")

    def test_text_and_dashboard_query(self):
        out = overview.text(overview.items(self.con, today=TODAY))
        self.assertIn(f"blocks #{self.dep}", out); self.assertIn(f"waits on #{self.base}", out)
        self.assertIn("IDEAS (1)", out); self.assertIn("(overdue)", out)
        data = dashboard.overview_query(self.con, "status=done,idea&goal=&kind=tasks")
        self.assertEqual(self.ids(data), ([self.done], []))
        self.assertEqual(overview.text(overview.items(self.con, status={"doing"})), "Nothing matches these filters.")


if __name__ == "__main__":
    unittest.main()

class HistoryFiltersTest(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(':memory:');self.con.row_factory=sqlite3.Row;db.init(self.con);self.addCleanup(self.con.close)
        self.old=db.insert(self.con,'tasks',title='Calendar',description='week view',created='2026-09-01T12:00:00',impact=5)
        self.new=db.insert(self.con,'tasks',title='Search',created='2026-10-17T12:00:00',impact=1)
        self.unknown=db.insert(self.con,'tasks',title='Legacy',created=None)
        db.insert(self.con,'ideas',title='Calendar someday',created='2026-10-01T12:00:00')
    def test_search_matches_description_and_id(self):
        self.assertEqual([t['id'] for t in overview.items(self.con,search='WEEK VIEW')['tasks']],[self.old])
        self.assertEqual([t['id'] for t in overview.items(self.con,search=f'#{self.new}')['tasks']],[self.new])
    def test_sort_dates_unknown_last(self):
        self.assertEqual([t['id'] for t in overview.items(self.con,sort='newest')['tasks']],[self.new,self.old,self.unknown])
        self.assertEqual([t['id'] for t in overview.items(self.con,sort='oldest')['tasks']],[self.old,self.new,self.unknown])
    def test_date_filter_applies_to_ideas_too(self):
        data=overview.items(self.con,created_from='2026-10-10',created_to='2026-10-18')
        self.assertEqual([t['id'] for t in data['tasks']],[self.new]);self.assertEqual(data['ideas'],[])
        with self.assertRaises(ValueError):overview.items(self.con,created_from='invalid')
    def test_recommendation_filter(self):
        from unittest.mock import patch
        with patch('engine.overview.brain.focus',return_value={'recommended':[{'task_id':self.old,'why':'Useful now'}]}):
            data=overview.items(self.con,recommendation='recommended')
        self.assertEqual([t['id'] for t in data['tasks']],[self.old]);self.assertEqual(data['tasks'][0]['recommendation_reason'],'Useful now')
        self.assertEqual(data['ideas'],[])


class DeliveryStagesTest(unittest.TestCase):
    """Installed & live, merged-not-installed, waiting for review, building and to do, from dev runs plus git."""
    def setUp(self):
        from unittest.mock import patch
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)
        def task(title, status, run=None, head=""):
            tid = db.insert(self.con, "tasks", title=title)
            db.update(self.con, "tasks", tid, status=status)
            if run:
                self.con.execute("INSERT INTO dev_runs (task_id, status, head, started) VALUES (?, ?, ?, ?)",
                                 (tid, run, head, db.now()))
            return tid
        self.manual = task("Bought a Pi", "done")
        self.live = task("Installed", "done", "merged", "a" * 40)
        self.waiting = task("Merged on GitHub", "done", "merged", "b" * 40)
        self.unsynced = task("Merged, not synced yet", "doing", "pr_open", "c" * 40)
        self.review = task("Open PR", "doing", "pr_open", "d" * 40)
        self.building = task("Running", "doing", "running")
        self.todo = task("Later", "todo")
        self.con.commit()
        commits = patch.object(overview, "_commits", return_value=({"a" * 40, "x"}, {"a" * 40, "b" * 40, "c" * 40}))
        commits.start(); self.addCleanup(commits.stop)

    def test_stages(self):
        got = {t["id"]: t["stage"] for t in overview.items(self.con, include_done=True)["tasks"]}
        self.assertEqual(got, {self.manual: "live", self.live: "live", self.waiting: "install", self.unsynced: "install",
                               self.review: "review", self.building: "building", self.todo: "todo"})

    def test_stage_filter_and_counts(self):
        data = overview.items(self.con, stage="install")
        self.assertEqual(sorted(t["id"] for t in data["tasks"]), sorted([self.waiting, self.unsynced]))
        self.assertEqual(data["ideas"], [])
        counts = {d["key"]: d["count"] for d in data["delivery"]}
        self.assertEqual((counts["live"], counts["install"], counts["review"], counts["todo"]), (2, 2, 1, 1))
        with self.assertRaises(ValueError):
            overview.items(self.con, stage="nonsense")
        self.assertEqual(len(dashboard.overview_query(self.con, "stage=review")["tasks"]), 1)
