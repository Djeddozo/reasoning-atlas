"""Ideas → Plan (hq.placement): every open idea gets a suggested goal or a new goal, with a reason; nothing loose."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")
import json
import sqlite3
import unittest
from unittest import mock

from engine import dashboard, db, models, placement


class PlacementTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)
        self.voice = db.insert(self.con, "goals", title="Run Atlas from my phone by voice", why="no typing")
        self.school = db.insert(self.con, "goals", title="Finish school", why="diploma this year")
        db.insert(self.con, "tasks", title="One-tap voice from the phone home screen", goal_id=self.voice)
        self.fits = db.insert(self.con, "ideas", title="Voice shortcut on the phone lock screen")
        self.alone = db.insert(self.con, "ideas", title="Learn to bake sourdough bread")
        self.con.commit()

    def test_rule_places_every_open_idea(self):
        r = placement.refresh(self.con)
        self.assertEqual((r["rule"], r["ai"]), (2, 0))
        p = placement.places(self.con)
        self.assertEqual((p[self.fits]["goal_id"], p[self.fits]["goal"], p[self.fits]["by"]), (self.voice, "Run Atlas from my phone by voice", "rule"))
        self.assertIn("phone", p[self.fits]["why"])
        self.assertEqual((p[self.alone]["goal_id"], p[self.alone]["new_goal"]), (None, "Learn to bake sourdough bread"))
        self.assertEqual(placement.refresh(self.con)["rule"], 0)  # nothing new: nothing redone

    def test_claude_places_and_bad_goal_ids_are_ignored(self):
        answer = json.dumps({"places": [{"idea_id": self.alone, "goal_id": None, "new_goal": "Bake at home", "why": "A hobby, unrelated to Atlas."},
                                        {"idea_id": self.fits, "goal_id": 999, "why": "made up"}]})
        with mock.patch.object(models, "complete", return_value=answer) as call:
            r = placement.refresh(self.con, use_model=True)
        self.assertEqual(call.call_args.args[0], "triage")
        self.assertEqual(r["ai"], 1)
        p = placement.places(self.con)
        self.assertEqual((p[self.alone]["new_goal"], p[self.alone]["by"]), ("Bake at home", "ai"))
        self.assertEqual(p[self.fits]["by"], "rule")  # an unknown goal id from the model is not trusted

    def test_no_model_falls_back_to_the_rule(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("cloud: down")):
            r = placement.refresh(self.con, use_model=True)
        self.assertEqual((r["ai"], r["error"]), (0, "cloud: down"))
        self.assertEqual(len(placement.places(self.con)), 2)

    def test_to_plan_and_new_goal(self):
        tid = placement.to_plan(self.con, self.fits, self.voice)
        task = self.con.execute("SELECT goal_id, owner, status FROM tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual(tuple(task), (self.voice, "me", "todo"))  # in the plan, not yet in the agent's queue
        self.assertEqual(self.con.execute("SELECT gate FROM ideas WHERE id = ?", (self.fits,)).fetchone()[0], "promoted")
        gid, tid2 = placement.to_new_goal(self.con, self.alone, title="Bake at home")
        self.assertEqual(self.con.execute("SELECT title FROM goals WHERE id = ?", (gid,)).fetchone()[0], "Bake at home")
        self.assertEqual(self.con.execute("SELECT goal_id FROM tasks WHERE id = ?", (tid2,)).fetchone()[0], gid)
        with self.assertRaises(ValueError):
            placement.to_plan(self.con, self.fits, self.voice)  # already promoted

    def test_dashboard_actions_and_data(self):
        data = dashboard._ideas(self.con)
        self.assertEqual({i["id"]: bool(i["place"]) for i in data}, {self.fits: True, self.alone: True})
        self.assertIn("in your plan", dashboard.act(self.con, "idea_plan", self.fits, str(self.school)))
        self.assertIn("New goal", dashboard.act(self.con, "idea_goal", self.alone, json.dumps({"title": "Bread"})))
        c = dashboard._constitution({"identity": {"owner": "D", "mission": "m"}, "autonomy": {"never": ["x"]}})
        self.assertEqual((c["mission"], c["never"], c["money"]["monthly_budget_eur"]), ("m", ["x"], 0))


if __name__ == "__main__":
    unittest.main()
