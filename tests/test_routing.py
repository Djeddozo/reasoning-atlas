"""Model routing for the dev loop: which model a task gets, and why."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import sqlite3
import unittest
from datetime import date

from engine import db, routing

CONF = routing.DEFAULTS


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)

    def task(self, title, description="", effort_h=2, impact=3):
        tid = db.insert(self.con, "tasks", title=title, description=description, effort_h=effort_h, impact=impact, owner="ai")
        return self.con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()

    def choose(self, task, conf=CONF):
        return routing.choose(self.con, task, conf, today=date(2026, 10, 12))

    def test_small_wording_task_is_light(self):
        c = self.choose(self.task("Fix a typo in the brief", effort_h=0.5))
        self.assertEqual((c["level"], c["model"]), ("light", "haiku"))
        self.assertIn("typo", c["reason"])

    def test_light_words_on_a_big_task_are_not_light(self):
        self.assertEqual(self.choose(self.task("Rewrite the docs", effort_h=3))["level"], "standard")

    def test_ordinary_task_is_standard(self):
        c = self.choose(self.task("Add a filter to the task list"))
        self.assertEqual((c["level"], c["model"]), ("standard", "sonnet"))

    def test_risky_type_large_or_high_impact_is_strong(self):
        self.assertEqual(self.choose(self.task("Gatekeeper: block new commands", effort_h=1))["model"], "opus")
        self.assertEqual(self.choose(self.task("Weekly review page", effort_h=6))["level"], "strong")
        self.assertEqual(self.choose(self.task("Weekly review page", effort_h=2, impact=5))["level"], "strong")

    def test_words_match_whole_words_only(self):
        # 'speech-to-text' is not the light word 'text'; 'authority' is not 'auth'
        self.assertEqual(self.choose(self.task("speech-to-text authority", effort_h=0.5))["level"], "standard")

    def test_budget_steps_down_one_level(self):
        db.insert(self.con, "dev_runs", task_id=1, status="pr_open", cost_usd=8.5, started="2026-10-03T10:00:00")
        db.insert(self.con, "dev_runs", task_id=1, status="pr_open", cost_usd=50, started="2026-09-20T10:00:00")  # last month
        conf = CONF | {"monthly_budget_usd": 10}
        c = self.choose(self.task("Refactor the router", effort_h=3), conf)
        self.assertEqual(c["level"], "standard")
        self.assertIn("$8.50 of $10 budget", c["reason"])
        self.assertEqual(self.choose(self.task("Add a filter"), conf | {"monthly_budget_usd": 20})["level"], "standard")

    def test_escalate_for_repair(self):
        c = self.choose(self.task("Fix a typo", effort_h=0.5))
        up = routing.escalate(c, CONF)
        self.assertEqual(up["model"], "sonnet")
        self.assertIn("repair", up["reason"])
        strong = self.choose(self.task("Security fix"))
        self.assertIs(routing.escalate(strong, CONF), strong)

    def test_off_means_default_model(self):
        c = self.choose(self.task("Anything"), CONF | {"enabled": False})
        self.assertEqual(c["model"], "")
        self.assertEqual(routing.escalate(c, CONF), c)
        self.assertIn("default", routing.text(c))

    def test_constitution_section_is_read(self):
        conf = routing.cfg()
        self.assertEqual({conf["light"], conf["standard"], conf["strong"]} - {""}, {"haiku", "sonnet", "opus"})


if __name__ == "__main__":
    unittest.main()


class ChosenModelTest(unittest.TestCase):
    """#70: the model and reason are shown on tasks being built and tasks already built."""
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.doing = db.insert(self.con, "tasks", title="Being built", owner="ai", status="doing")
        self.done = db.insert(self.con, "tasks", title="Built", owner="ai", status="done", done_at="2026-10-12T10:00:00")
        self.mine = db.insert(self.con, "tasks", title="Never built", owner="me", status="done", done_at="2026-10-12T09:00:00")
        self.add_run(self.done, "haiku", "small typo task (0.5 h)", "pr_open")
        self.add_run(self.done, "opus", "repair after a failed standard attempt", "merged")  # the latest run counts
        self.add_run(self.doing, "sonnet", "ordinary task (2 h)", "running")
        self.add_run(self.mine, "", "", "failed")  # routing off: nothing to show

    def add_run(self, task_id, model, reason, status):
        db.insert(self.con, "dev_runs", task_id=task_id, branch="b", status=status, started="2026-10-12T08:00:00",
                  model=model, model_reason=reason)

    def test_latest_routed_run_per_task(self):
        got = routing.chosen(self.con)
        self.assertEqual(got[self.done], {"model": "opus", "reason": "repair after a failed standard attempt"})
        self.assertEqual(got[self.doing]["model"], "sonnet")
        self.assertNotIn(self.mine, got)
        self.assertEqual(set(routing.chosen(self.con, [self.doing])), {self.doing})

    def test_line(self):
        self.assertEqual(routing.line("sonnet", "ordinary task (2 h)"), "sonnet: ordinary task (2 h)")
        self.assertEqual(routing.line("sonnet", ""), "sonnet")
        self.assertEqual(routing.line("", ""), "")

    def test_progress_recent_and_summary(self):
        from engine import progress
        s = progress.stats(self.con, date(2026, 10, 12))
        recent = {t["id"]: t for t in s["recent"]}
        self.assertEqual(recent[self.done]["model"], "opus")
        self.assertIn("repair", recent[self.done]["model_reason"])
        self.assertEqual(recent[self.mine]["model"], "")
        self.assertIn("#%d Built (opus: repair after a failed standard attempt)" % self.done, progress.summary(s))

    def test_overview_shows_model_on_doing_and_done_only(self):
        from engine import overview
        db.insert(self.con, "dev_runs", task_id=db.insert(self.con, "tasks", title="Back to todo", owner="ai"),
                  branch="b", status="failed", started="2026-10-12T08:00:00", model="sonnet", model_reason="x")
        tasks = {t["title"]: t for t in overview.items(self.con, include_done=True)["tasks"]}
        self.assertEqual(tasks["Being built"]["model"], "sonnet")
        self.assertEqual(tasks["Built"]["model_reason"], "repair after a failed standard attempt")
        self.assertEqual(tasks["Back to todo"]["model"], "")  # an old attempt on an open task says nothing about the next
        self.assertIn("model sonnet: ordinary task (2 h)", overview.text(overview.items(self.con)))
