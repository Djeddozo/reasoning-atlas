"""#158: a task stopped by a usage limit stays first in line and the builder continues it, instead of moving on."""
import unittest
from pathlib import Path
from unittest import mock

from engine import db, devloop, priority, resume, skilltree


def database():
    con = db.connect(Path(":memory:"))
    db.init(con)
    return con


class ResumeFirst(unittest.TestCase):
    def setUp(self):
        self.con = database()
        self.top = db.insert(self.con, "tasks", title="Top task", status="todo", owner="ai", impact=5, effort_h=0.5, confidence=1)
        self.low = db.insert(self.con, "tasks", title="Low task", status="todo", owner="ai", impact=1, effort_h=8)
        self.con.commit()
        priority.rescore(self.con)
        for p in (mock.patch.object(devloop.overlap, "hold", return_value=""), mock.patch.object(devloop.mockup, "hold", return_value="")):
            p.start(); self.addCleanup(p.stop)

    def stop(self, task_id, summary="Claude AI usage limit reached|1792349760"):
        rid = db.insert(self.con, "dev_runs", task_id=task_id, status="failed", summary=summary, started=db.now(), head="")
        resume.record_devloop(self.con, rid, {"id": task_id, "title": "x"}, summary)
        return rid

    def test_the_stopped_task_goes_first(self):
        self.assertEqual(devloop.pick_task(self.con)["id"], self.top)
        self.stop(self.low)
        self.assertEqual(devloop.pick_task(self.con)["id"], self.low)
        self.assertTrue(devloop.overview(self.con)["next"]["resumes"])

    def test_delivered_or_dropped_tasks_no_longer_count(self):
        self.stop(self.low)
        db.insert(self.con, "dev_runs", task_id=self.low, status="pr_open", started=db.now(), head="abc")
        self.con.commit()
        self.assertEqual(devloop.pick_task(self.con)["id"], self.top)
        self.assertEqual(resume.items(self.con), [])  # resolved on the way
        other = db.insert(self.con, "tasks", title="Dropped", status="dropped", owner="ai")
        self.stop(other)
        self.assertEqual(devloop.pick_task(self.con)["id"], self.top)

    def test_a_gate_still_holds(self):
        self.stop(self.low)
        with mock.patch.object(devloop, "outside_blocked", return_value=True):
            self.assertIsNone(devloop.resume_first(self.con))

    def test_a_limit_stop_is_not_a_failure_even_when_only_the_log_says_so(self):
        for _ in range(2):  # summary without the limit text: only the log tail showed it
            self.stop(self.low, "")
        item = next(i for i in skilltree.build_plan(self.con)["proposed"] if i["id"] == self.low)
        self.assertNotEqual(item["state"], "failed")
        for _ in range(2):  # real failures still count
            db.insert(self.con, "dev_runs", task_id=self.top, status="tests_failed", summary="Tests failed", started=db.now(), head="")
        self.con.commit()
        plan = skilltree.build_plan(self.con)
        self.assertEqual(next(i for i in plan["proposed"] + plan["waiting"] if i["id"] == self.top)["state"], "failed")


if __name__ == "__main__":
    unittest.main()
