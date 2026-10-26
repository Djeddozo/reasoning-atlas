"""RA-064: explore an idea, then take it into the plan the way the AI judges best."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import json
import sqlite3
import unittest

from engine import compass, db, explore


class Explore(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.vision = compass.create(self.con, "Own products and income")
        self.goal = compass.add_objective(self.con, self.vision, "First digital product")
        self.idea = db.insert(self.con, "ideas", title="Flashcard app for language learning", description="spaced repetition", gate="idea")
        self.con.commit()

    def answer(self, **kw):
        out = {"summary": "A small product that also trains Dutch.", "fits": [{"id": self.goal, "title": "First digital product", "how": "a first sellable app"}],
               "value": "useful daily", "new_start": "a product line", "overlaps": [], "first_step": "a prototype",
               "risks": "competition", "take_as": "task", "under": self.goal, "title": "Prototype the flashcard app", "why": "fits the objective"} | kw
        return lambda job, system, user: (self.assertEqual(job, "research"), self.assertIn("Own products and income", user), json.dumps(out))[-1]

    def test_explore_then_take_as_task(self):
        e = explore.explore(self.con, self.idea, self.answer())
        self.assertEqual((e["take_as"], e["under"], e["fits"][0]["how"]), ("task", self.goal, "a first sellable app"))
        self.assertEqual(explore.get(self.con, self.idea)["summary"], "A small product that also trains Dutch.")
        msg = explore.take(self.con, self.idea)
        self.assertIn("under the goal it serves", msg)
        self.assertEqual(self.con.execute("SELECT goal_id FROM tasks WHERE title LIKE 'Flashcard%'").fetchone()[0], self.goal)

    def test_take_as_goal_under_the_vision(self):
        explore.explore(self.con, self.idea, self.answer(take_as="goal", under=self.vision, title="Language-learning products"))
        msg = explore.take(self.con, self.idea)
        g = self.con.execute("SELECT id, parent_id FROM goals WHERE title = 'Language-learning products'").fetchone()
        self.assertEqual(g["parent_id"], self.vision)
        self.assertIn("under its vision", msg)

    def test_later_keeps_the_idea_and_text_change_resets(self):
        explore.explore(self.con, self.idea, self.answer(take_as="later", why="the focus is elsewhere this year"))
        self.assertIn("Not now", explore.take(self.con, self.idea))
        self.assertEqual(self.con.execute("SELECT gate FROM ideas WHERE id = ?", (self.idea,)).fetchone()[0], "idea")
        db.update(self.con, "ideas", self.idea, description="now with a shop"); self.con.commit()
        self.assertIsNone(explore.get(self.con, self.idea))  # the exploration described the old text

    def test_take_without_exploration_uses_the_placement(self):
        self.assertIn("first task", explore.take(self.con, self.idea))
