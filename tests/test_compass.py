"""Compass: lifetime visions, year objectives, the recommendation, and the focus bonus in the ranking."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import sqlite3
import unittest
from datetime import date

from engine import brain, compass, db, priority

TODAY = date(2026, 10, 20)


class CompassTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        ins = lambda t, **k: db.insert(self.con, t, **k)
        self.mind = ins("goals", title="Advance the intelligence of Atlas", horizon="life", created="2026-01-01T10:00:00")
        self.money = ins("goals", title="Financial stability", horizon="life", created="2026-10-01T10:00:00")
        self.obj = ins("goals", title="Atlas finds opportunities itself", horizon="goal", parent_id=self.mind, created="2026-04-20T10:00:00",
                       due="2027-04-20")  # a deadline the owner set: the only timeline (Vision → Goal → Task)
        self.loose = ins("goals", title="Pass the driving exam", horizon="quarter", created="2026-09-01T10:00:00")
        ins("tasks", title="Done thing", goal_id=self.obj, status="done", done_at="2026-10-15T10:00:00", updated="2026-10-15T10:00:00")
        for n in range(3):
            ins("tasks", title=f"Atlas step {n}", goal_id=self.obj, status="todo", impact=3, updated="2026-10-15T10:00:00")
        self.exam = ins("tasks", title="Book the exam", goal_id=self.loose, status="todo", impact=3)
        self.con.commit()

    def test_visions_objectives_and_recommendation(self):
        r = compass.directions(self.con, TODAY)
        self.assertEqual({v["title"] for v in r["visions"]}, {"Advance the intelligence of Atlas", "Financial stability"})
        self.assertEqual([g["title"] for g in r["loose"]], ["Pass the driving exam"])
        mind = next(v for v in r["visions"] if v["id"] == self.mind)
        obj = mind["objectives"][0]
        self.assertEqual((obj["title"], obj["done"], obj["total"]), ("Atlas finds opportunities itself", 1, 4))
        self.assertAlmostEqual(obj["behind"], 0.25, delta=0.01)  # 183 of 365 days used, a quarter of the work done
        self.assertIn("is 25% done after 50% of the time to its deadline", mind["reasons"][0])
        self.assertEqual(r["recommended"]["id"], self.mind)  # an objective behind outweighs a vision without objectives
        money = next(v for v in r["visions"] if v["id"] == self.money)
        self.assertIn("no goals yet", money["reasons"][0])

    def test_only_a_deadline_raises_priority(self):
        priority.rescore(self.con, today=TODAY)
        score = lambda: self.con.execute("SELECT score FROM tasks WHERE id = ?", (self.exam,)).fetchone()[0]
        before = score()
        self.con.execute("UPDATE goals SET horizon = 'week' WHERE id = ?", (self.loose,))  # a time layer alone does nothing
        priority.rescore(self.con, today=TODAY); self.assertEqual(score(), before)
        self.assertIn("Deadline", compass.set_due(self.con, self.loose, "2026-10-23"))
        priority.rescore(self.con, today=TODAY); self.assertGreater(score(), before)
        t = self.con.execute("SELECT * FROM tasks WHERE id = ?", (self.exam,)).fetchone()
        self.assertIn("its goal's deadline", priority.explain(self.con, t, today=TODAY))
        with self.assertRaises(ValueError):
            compass.set_due(self.con, self.loose, "next week")
        compass.set_due(self.con, self.loose, None)
        priority.rescore(self.con, today=TODAY); self.assertEqual(score(), before)

    def test_focus_ranks_its_work_and_explains_it(self):
        priority.rescore(self.con, today=TODAY)
        before = self.con.execute("SELECT score FROM tasks WHERE title = 'Atlas step 0'").fetchone()[0]
        self.assertIn("Advance the intelligence", compass.set_focus(self.con, self.mind))
        priority.rescore(self.con, today=TODAY)
        self.assertAlmostEqual(self.con.execute("SELECT score FROM tasks WHERE title = 'Atlas step 0'").fetchone()[0], 2 * before, places=2)
        top = priority.next_tasks(self.con, 1)[0]
        self.assertIn("in your focus", priority.explain(self.con, top, today=TODAY))
        compass.set_focus(self.con, None)
        self.assertEqual(compass.focused_goals(self.con), set())

    def test_focus_is_a_vision(self):
        for bad in (self.obj, self.loose, 999):
            with self.subTest(goal=bad), self.assertRaises(ValueError):
                compass.set_focus(self.con, bad)

    def test_create_objective_place_and_undo(self):
        gid = compass.create(self.con, "Own products and income", "an income of my own")
        oid = compass.add_objective(self.con, gid, "First digital product", "")
        compass.place(self.con, self.loose, self.mind)
        r = compass.directions(self.con, TODAY)
        self.assertEqual(r["loose"], [])
        self.assertEqual([o["title"] for v in r["visions"] if v["id"] == gid for o in v["objectives"]], ["First digital product"])
        with self.assertRaises(ValueError):
            compass.add_objective(self.con, oid, "Not under an objective")
        with self.assertRaises(ValueError):
            compass.create(self.con, "")
        change = self.con.execute("SELECT id FROM brain_changes WHERE row_id = ? AND table_name = 'goals'", (gid,)).fetchone()[0]
        brain.undo(self.con, change)
        self.assertEqual(self.con.execute("SELECT status FROM goals WHERE id = ?", (gid,)).fetchone()[0], "dropped")


if __name__ == "__main__":
    unittest.main()


class SmartOverlayer(unittest.TestCase):
    """RA-063: Claude re-evaluates the plan only after real change, at most every few hours."""
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)

    def test_reevaluates_only_after_enough_new_input(self):
        import json as _json
        from unittest import mock
        from datetime import datetime, timedelta
        from engine import doover
        at = (datetime.now() - timedelta(hours=4)).isoformat(timespec="seconds")
        db.set_state(self.con, "brain_focus", _json.dumps({"at": at}))
        db.set_state(self.con, doover.STRUCTURE_KEY, doover.structure_signature()); self.con.commit()  # the plan was checked against today's structure
        with mock.patch.object(doover, "replan", return_value={"ok": 1}) as replan:
            self.assertIsNone(brain.reevaluate_if_changed(self.con))          # nothing new
            for n in range(5):
                db.insert(self.con, "thoughts", text=f"thought {n}", created=db.now())
            self.con.commit()
            self.assertEqual(brain.reevaluate_if_changed(self.con), {"ok": 1})  # five new thoughts
            db.set_state(self.con, "brain_focus", _json.dumps({"at": (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")}))
            for n in range(6):
                db.insert(self.con, "ideas", title=f"idea {n}", created=db.now())
            self.con.commit()
            self.assertIsNone(brain.reevaluate_if_changed(self.con))          # but not twice within three hours
        self.assertEqual(replan.call_count, 1)
        db.set_state(self.con, "brain_focus", _json.dumps({"at": at})); db.set_state(self.con, doover.STRUCTURE_KEY, "older")
        with mock.patch.object(doover, "replan", return_value={"ok": 2}):
            self.assertEqual(brain.reevaluate_if_changed(self.con), {"ok": 2})  # a new structure is real change (RA-057)
