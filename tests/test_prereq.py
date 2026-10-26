"""Prerequisites (informs, never blocks) and Research tools vs developments with adopt/adapt/build ideas."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")
import sqlite3
import unittest

from engine import db, prereq, radar


class PrereqTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)
        cap = lambda t, **f: db.insert(self.con, "capabilities", title=t, **f)
        self.memory = cap("Memory database", status="unlocked")
        self.sandbox = cap("Sandbox for outside code", description="Docker, no credentials")
        self.search = cap("Semantic memory search", description="embedding search over memories", requires=f"{self.memory},{self.sandbox}")
        self.dep = db.insert(self.con, "tasks", title="Pick an embedding model", owner="me", status="todo")
        self.task = db.insert(self.con, "tasks", title="Index memories for search", owner="ai", status="todo",
                              capability_id=self.search, depends_on=str(self.dep))
        self.con.commit()

    def test_task_needs_unbuilt_requirement_and_open_dependency(self):
        p = prereq.for_task(self.con, self.task)
        self.assertFalse(p["ready"])
        self.assertIn("Sandbox for outside code", [n["title"] for n in p["needs"]])
        self.assertIn("Memory database", [h["title"] for h in p["have"]])
        self.assertIn(self.dep, [n.get("task_id") for n in p["needs"]])
        self.assertIn("Sandbox for outside code", prereq.prompt_block(self.con, self.task))

    def test_outside_code_points_at_the_sandbox(self):
        p = prereq.check(self.con, "An open-source library for agents https://github.com/x/y")
        self.assertTrue(any("sandbox" in n.lower() for n in p["notes"]))
        self.assertIn("Sandbox for outside code", [n["title"] for n in p["needs"]])

    def test_nothing_missing(self):
        p = prereq.check(self.con, "Sourdough timing")
        self.assertTrue(p["ready"]); self.assertEqual(prereq.line(p), "Nothing missing.")


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)

    def test_kind(self):
        self.assertEqual(radar.kind({"source": "github", "title": "x"}), "tool")
        self.assertEqual(radar.kind({"source": "arxiv", "title": "A new agent benchmark"}), "development")
        self.assertEqual(radar.kind({"source": "hn", "title": "Show HN: a CLI for agents", "url": "https://x.dev"}), "tool")
        self.assertEqual(radar.kind({"source": "hn", "title": "Lab announces model", "url": "https://news.x"}), "development")

    def test_approach_becomes_named_idea(self):
        rid = db.insert(self.con, "radar", source="github", title="leviathan", url="https://github.com/e/leviathan",
                        summary="ranked full-text index", state="new", found=db.now(), external_id="gh:leviathan")
        iid = radar.set_state(self.con, rid, "idea", "adapt")
        idea = self.con.execute("SELECT title, description FROM ideas WHERE id = ?", (iid,)).fetchone()
        self.assertEqual(idea["title"], "Adapt: leviathan")
        self.assertIn("open-source", idea["description"]); self.assertIn("github.com/e/leviathan", idea["description"])
        plain = radar.set_state(self.con, rid, "idea")
        self.assertTrue(self.con.execute("SELECT title FROM ideas WHERE id = ?", (plain,)).fetchone()[0].startswith("Explore: "))


if __name__ == "__main__":
    unittest.main()
