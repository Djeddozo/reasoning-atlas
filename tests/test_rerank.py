"""Re-rank right after a thought, a finished task or new radar items."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")
import sqlite3
import unittest
from datetime import date
from unittest import mock

from engine import db, dump, flow, models, priority, radar


class RerankTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        db.init(self.con)

    def reranks(self):
        return [r[0] for r in self.con.execute("SELECT detail FROM log WHERE action = 'rerank' ORDER BY id")]

    def test_finished_task_unblocks_and_reranks(self):
        a = db.insert(self.con, "tasks", title="foundation", owner="ai", impact=2)
        b = db.insert(self.con, "tasks", title="big win", owner="ai", impact=5, depends_on=str(a))
        priority.rescore(self.con)
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (b,)).fetchone()[0], "blocked")
        flow.complete(self.con, a, "agent")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (b,)).fetchone()[0], "todo")
        log = self.reranks()
        self.assertEqual(len(log), 1)
        self.assertIn(f"task done #{a}", log[0])
        self.assertIn(f"→ #{b}", log[0])

    def test_thought_reranks_even_without_model(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("offline")):
            dump.process(self.con, "todo: renew passport")
        log = self.reranks()
        self.assertTrue(log and log[-1].startswith("thought #"))
        self.assertIn("renew passport is on top", log[-1])
        self.assertGreater(self.con.execute("SELECT score FROM tasks WHERE title = 'renew passport'").fetchone()[0], 0)

    def test_radar_reranks_only_on_new_items(self):
        item = dict(source="github", external_id="a/b", title="a/b", url="u", summary="agent", popularity=1, extra="")
        fake = {"github": lambda: iter([item])}
        radar.scan(self.con, {"radar": {"interests": ["agent"]}}, ["github"], fake)
        radar.scan(self.con, {"radar": {"interests": ["agent"]}}, ["github"], fake)
        log = self.reranks()
        self.assertEqual(len(log), 1)
        self.assertTrue(log[0].startswith("radar 1 new items: top unchanged"))

    def test_explain_names_the_factors(self):
        g = db.insert(self.con, "goals", title="Health")
        a = db.insert(self.con, "tasks", title="book dentist", impact=4, effort_h=0.5, due="2026-10-15", goal_id=g)
        db.insert(self.con, "tasks", title="follow-up", depends_on=str(a))
        t = self.con.execute("SELECT * FROM tasks WHERE id = ?", (a,)).fetchone()
        s = priority.explain(self.con, t, today=date(2026, 10, 12))
        for part in ("impact 4/5", "due in 3 days", "unlocks 1 waiting task", "serves goal 'Health'", "quick win"):
            self.assertIn(part, s)
        self.assertTrue(s.endswith(".") and "\n" not in s)

    def test_explain_full_marks_missing_fields(self):
        g = db.insert(self.con, "goals", title="Health", why="Stay well")
        a = db.insert(self.con, "tasks", title="book dentist", goal_id=g, status="proposed", impact=4, effort_h=0.5,
                      description="Teeth hurt.\nDone when: appointment is in the calendar")
        b = db.insert(self.con, "tasks", title="bare", depends_on=str(a))
        full = priority.explain_full(self.con, self.con.execute("SELECT * FROM tasks WHERE id = ?", (a,)).fetchone())
        self.assertEqual(full["problem"]["text"], "Teeth hurt.")
        self.assertEqual(full["success_criterion"]["text"], "appointment is in the calendar")
        self.assertEqual(full["goal"]["text"], "Health")
        self.assertIn("impact 4/5", full["why_now"]["text"])
        self.assertTrue(full["dependencies"]["none"])
        bare = priority.explain_full(self.con, self.con.execute("SELECT * FROM tasks WHERE id = ?", (b,)).fetchone())
        self.assertTrue(bare["problem"]["missing"] and bare["success_criterion"]["missing"] and bare["goal"]["missing"])
        self.assertIn(f"#{a} book dentist", bare["dependencies"]["text"])

    def test_why_first_is_one_source(self):
        self.assertEqual(priority.why_first(self.con)["task"], None)
        db.insert(self.con, "tasks", title="call bank", owner="me")
        priority.rerank(self.con, "thought")
        w = priority.why_first(self.con)
        self.assertIn("call bank is on top", w["why"])
        self.assertEqual(w["last_rerank"]["detail"].split(":")[0], "thought")

    def test_brief_says_why_first(self):
        db.insert(self.con, "tasks", title="call bank", owner="me")
        self.assertIn("Why first: #1 call bank is on top", _loop.brief(self.con, write=False))


if __name__ == "__main__":
    unittest.main()
