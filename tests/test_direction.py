"""Where is this heading: the queue rolled up into skill tree nodes and goals, with insights (hq.direction)."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import sqlite3
import unittest

from engine import db, direction, skilltree


class DirectionTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        skilltree.seed(self.con)
        self.cap = {r["title"]: r["id"] for r in self.con.execute("SELECT id, title FROM capabilities")}
        self.goal = db.insert(self.con, "goals", title="Ship a product")
        self.empty = db.insert(self.con, "goals", title="Learn to sail")
        self.con.commit()

    def task(self, title, cap=None, owner="ai", **kw):
        kw.setdefault("goal_id", self.goal)
        tid = db.insert(self.con, "tasks", title=title, owner=owner, capability_id=self.cap.get(cap), **kw)
        self.con.commit()
        return tid

    def queued(self, r, tid):
        return next(t for t in r["queue"] if t["id"] == tid)

    def test_last_task_of_a_node_unlocks_it_and_opens_the_next(self):
        a = self.task("Scaffold a web app", "Prototype builder", score=5)
        r = direction.rollup(self.con)
        t = self.queued(r, a)
        self.assertEqual((t["node"], t["goal"]), ("Prototype builder", "Ship a product"))
        self.assertTrue(t["unlocks"])
        self.assertIn("First digital product", t["opens"])      # needs only the prototype builder
        self.assertNotIn("Product packages", t["opens"])        # also needs the sandbox, still locked
        self.assertIn("Prototype builder", direction.text(r))
        b = self.task("Deploy a preview", "Prototype builder")
        r = direction.rollup(self.con)
        self.assertFalse(self.queued(r, a)["unlocks"] or self.queued(r, b)["unlocks"])  # two left: neither unlocks alone
        node = next(n for n in r["nodes"] if n["title"] == "Prototype builder")
        self.assertEqual((node["left"], node["goals"]), (2, ["Ship a product"]))
        goal = next(g for g in r["goals"] if g["id"] == self.goal)
        self.assertIn("Prototype builder", goal["nodes"])
        self.assertTrue(any("Prototype builder" in h for h in r["insights"]["heading"]))

    def test_insights_steer_yourself_think(self):
        self.task("Unlinked AI work")
        base = self.task("Buy a domain myself", owner="me")
        self.task("Publish the site", "Prototype builder", depends_on=str(base))
        db.insert(self.con, "tasks", title="A draft", status="proposed", goal_id=self.goal)
        self.con.commit()
        i = direction.rollup(self.con)["insights"]
        self.assertTrue(any("build no skill tree node" in s for s in i["steer"]))
        self.assertTrue(any("Learn to sail" in s and "no AI work" in s for s in i["steer"]))
        self.assertEqual(i["yourself"][0], f"#{base} Buy a domain myself (unblocks 1)")
        self.assertTrue(any("proposed task" in s for s in i["think"]))
        self.assertTrue(any("Learn to sail" in s and "no tasks yet" in s for s in i["think"]))

    def test_read_only_and_json(self):
        self.task("Something", "Prototype builder")
        before = self.con.total_changes
        r = direction.rollup(self.con)
        self.assertEqual(self.con.total_changes, before)  # the view never writes
        json.loads(direction.as_json(r))


if __name__ == "__main__":
    unittest.main()
