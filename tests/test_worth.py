"""RA-063: just before a build, 'still worth building now?' is judged in the overlap check's own model call."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import json
import sqlite3
import unittest
from unittest import mock

from engine import compass, db, models, overlap


class Worth(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        v = compass.create(self.con, "Financial stability")
        compass.set_focus(self.con, v)
        self.task = db.insert(self.con, "tasks", title="Animated logo for the dashboard", description="spin it", owner="ai", status="todo")
        self.con.commit()
        self.row = dict(self.con.execute("SELECT * FROM tasks WHERE id = ?", (self.task,)).fetchone())

    def model(self, worth, why="The focus is on money; a logo can wait."):
        def reply(job, system, user):
            data = json.loads(user)
            self.assertEqual((job, data["FOCUS"]["vision"]), ("overlap", "Financial stability"))  # judged against the focus
            return json.dumps({"checks": [{"item": f"task:{self.task}", "verdict": "new", "match": None, "why": "nothing like it",
                                           "worth": worth, "worth_why": why}]})
        return mock.patch.object(models, "complete", side_effect=reply)

    def test_build_goes_ahead(self):
        with self.model("build", "fits"):
            self.assertEqual(overlap.hold(self.con, self.row), "")

    def test_hold_asks_once_and_the_answer_settles_it(self):
        with self.model("hold"), mock.patch("engine.gatekeeper.notify", create=True):
            msg = overlap.hold(self.con, self.row)
        self.assertIn("still worth building", msg)
        ap = self.con.execute("SELECT * FROM approvals ORDER BY id DESC").fetchone()
        self.assertIn("Claude suggests to wait", ap["request"])
        self.assertEqual(ap["options"], "Build anyway|Not now|Drop it")
        self.assertIn("asked you", overlap.hold(self.con, self.row))  # no second question while it is pending
        self.con.execute("UPDATE approvals SET status = 'answered', answer = 'Not now' WHERE id = ?", (ap["id"],)); self.con.commit()
        self.assertIn("you said not now", overlap.hold(self.con, self.row) + overlap.hold(self.con, self.row))

    def test_drop_answer_drops_the_task(self):
        with self.model("drop", "superseded by the budget work"), mock.patch("engine.gatekeeper.notify", create=True):
            overlap.hold(self.con, self.row)
        ap = self.con.execute("SELECT id FROM approvals ORDER BY id DESC").fetchone()[0]
        self.con.execute("UPDATE approvals SET status = 'answered', answer = 'Drop it' WHERE id = ?", (ap,)); self.con.commit()
        overlap.hold(self.con, self.row)
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "dropped")
