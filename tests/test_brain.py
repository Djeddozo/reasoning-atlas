"""The brain: thoughts, sorting, re-evaluation with logged/undoable changes, ideas → tasks."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import sqlite3
import unittest
from unittest import mock

from engine import brain, dashboard, db, dump, loop, models


class BrainTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.goal = db.insert(self.con, "goals", title="Pass the exams", horizon="quarter")
        self.task = db.insert(self.con, "tasks", title="Revise maths", impact=2, owner="me")
        self.other = db.insert(self.con, "tasks", title="Revise math", impact=2)
        self.idea = db.insert(self.con, "ideas", title="Flashcard app for revision", description="small web app")
        self.con.commit()

    def model(self, reply):
        return mock.patch.object(models, "complete", return_value=json.dumps(reply))

    def test_dump_becomes_a_thought_and_its_results_are_undoable(self):
        reply = {"tasks": [{"title": "Book exam", "owner": "me", "impact": 4, "goal_id": self.goal}],
                 "ideas": [{"title": "Study group"}], "updates": [{"task_id": self.task, "note": "focus on calculus"}],
                 "summary": "1 task, 1 idea"}
        with self.model(reply):
            reply = dump.process(self.con, "need to book the exam, maybe a study group; calculus is weak", "voice")
        self.assertTrue(reply.startswith("1 task, 1 idea\n"))  # summary, then where every item went
        self.assertIn("– in Forge › Plan under", reply); self.assertIn("– in Forge › Ideas", reply)
        t = brain.thoughts(self.con)[0]
        self.assertEqual((t["source"], t["status"], t["result"]), ("voice", "sorted", "1 task, 1 idea"))
        log = brain.changes(self.con)
        self.assertEqual(sorted(c["action"] + c["table_name"] for c in log), ["createideas", "createtasks", "updatetasks"])
        self.assertTrue(all(c["reason"] == f"from your thought #{t['id']}" for c in log))
        created = next(c for c in log if c["table_name"] == "tasks" and c["action"] == "create")
        brain.undo(self.con, created["id"])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (created["row_id"],)).fetchone()[0], "dropped")
        upd = next(c for c in log if c["action"] == "update")
        brain.undo(self.con, upd["id"])
        self.assertEqual(self.con.execute("SELECT description FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "")
        self.assertEqual(brain.undo(self.con, upd["id"]), "Already undone.")

    def test_unsorted_thought_is_sorted_later(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("off")):
            dump.process(self.con, "maybe learn to solder")
            self.assertEqual(brain.thoughts(self.con)[0]["status"], "unsorted")
            self.assertEqual(brain.sort_pending(self.con), 0)  # still no model: kept, nothing doubled
        with self.model({"ideas": [{"title": "Learn to solder"}], "summary": "1 idea"}):
            self.assertEqual(brain.sort_pending(self.con), 1)
        self.assertEqual(brain.thoughts(self.con)[0]["status"], "sorted")
        self.assertTrue(self.con.execute("SELECT 1 FROM ideas WHERE title = 'Learn to solder'").fetchone())

    def test_reevaluate_applies_allowed_changes_only(self):
        reply = {"changes": [
                    {"table": "tasks", "id": self.task, "field": "impact", "value": 9, "reason": "exam in 5 days"},
                    {"table": "tasks", "id": self.task, "field": "goal_id", "value": self.goal, "reason": "belongs to exams"},
                    {"table": "tasks", "id": self.task, "field": "status", "value": "done", "reason": "not allowed"},
                    {"table": "tasks", "id": self.task, "field": "owner", "value": "robot", "reason": "bad value"},
                    {"table": "tasks", "id": 999, "field": "impact", "value": 5, "reason": "unknown task"}],
                 "promote": [{"idea_id": self.idea, "owner": "ai", "reason": "you mentioned it twice"}],
                 "suggest": [{"action": "merge", "task_ids": [self.task, self.other], "reason": "duplicates"}],
                 "recommended": [{"task_id": self.task, "why": "exam soon"}, {"task_id": 999, "why": "gone"}],
                 "focus": "Exams first."}
        with self.model(reply):
            r = brain.reevaluate(self.con)
        self.assertEqual((r["applied"], r["skipped"]), (3, 3))
        row = self.con.execute("SELECT impact, goal_id, status, owner FROM tasks WHERE id = ?", (self.task,)).fetchone()
        self.assertEqual(tuple(row), (5, self.goal, "todo", "me"))  # impact capped at 5; status/owner untouched
        new = self.con.execute("SELECT id, owner FROM tasks WHERE title = 'Flashcard app for revision'").fetchone()
        self.assertEqual(new["owner"], "ai")                        # promoted idea queued for the agent
        self.assertEqual(self.con.execute("SELECT gate FROM ideas WHERE id = ?", (self.idea,)).fetchone()[0], "promoted")
        f = brain.focus(self.con)
        self.assertEqual(f["focus"], "Exams first.")
        self.assertEqual([x["task_id"] for x in f["recommended"]], [self.task])
        self.assertEqual(f["suggest"][0]["task_ids"], [self.task, self.other])
        brain.accept_suggestion(self.con, "merge", [self.task, self.other], "duplicates")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.other,)).fetchone()[0], "dropped")
        self.assertEqual(brain.focus(self.con)["suggest"], [])
        self.assertIn("Exams first.", brain.text(self.con))

    def test_reevaluate_without_model_keeps_a_plain_ranking(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("off")):
            r = brain.reevaluate(self.con)
        self.assertIn("no model reachable", r["error"])
        f = brain.focus(self.con)
        self.assertTrue(f["recommended"])  # falls back to the plan's own ranking
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM brain_changes").fetchone()[0], 0)

    def test_promote_idea_and_dashboard_controls(self):
        loop.seed(self.con)
        self.assertIn("task #", dashboard.act(self.con, "idea_task", self.idea, "ai"))
        tid = self.con.execute("SELECT id FROM tasks WHERE title = 'Flashcard app for revision'").fetchone()[0]
        data = dashboard.build_data(self.con)
        self.assertIn(tid, [c["row_id"] for c in data["brain"]["changes"] if c["table_name"] == "tasks"])
        change = next(c for c in data["brain"]["changes"] if c["table_name"] == "tasks")
        dashboard.act(self.con, "brain_undo", change["id"])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0], "dropped")
        with self.assertRaises(ValueError):
            brain.promote_idea(self.con, 999)

    def test_codex_review_fixes(self):
        # an older change cannot be undone over a newer one to the same field
        brain.set_field(self.con, "tasks", self.task, "impact", 4, "first")
        brain.set_field(self.con, "tasks", self.task, "impact", 5, "second")
        b, a = [c["id"] for c in brain.changes(self.con, 2)]  # newest first
        with self.assertRaises(ValueError):
            brain.undo(self.con, a)
        brain.undo(self.con, b); brain.undo(self.con, a)
        self.assertEqual(self.con.execute("SELECT impact FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], 2)
        # an idea becomes a task once
        brain.promote_idea(self.con, self.idea)
        with self.assertRaises(ValueError):
            brain.promote_idea(self.con, self.idea)
        # confidence is a probability
        self.assertEqual(brain._valid("tasks", "confidence", 10, self.con), 1.0)
        # mixed thought without a model: the todo is added now, the free text waits and is sorted once
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("off")):
            dump.process(self.con, "todo: buy SSD; maybe build a NAS")
        t = brain.thoughts(self.con)[0]
        self.assertEqual(t["status"], "unsorted")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE title = 'buy SSD'").fetchone()[0], 1)
        seen = []
        with mock.patch.object(models, "complete", side_effect=lambda job, s, u: seen.append(u) or '{"summary": "1 idea"}'):
            self.assertEqual(brain.sort_pending(self.con), 1)
            self.assertEqual(brain.sort_pending(self.con), 0)   # already sorted: not sorted twice
        self.assertEqual(len(seen), 1)
        self.assertIn("maybe build a NAS", seen[0]); self.assertNotIn("buy SSD", seen[0].split("BRAIN DUMP:")[1])
        # a thought claimed by another run is skipped
        tid = brain.add_thought(self.con, "something", "me")
        self.con.execute("UPDATE thoughts SET status = 'sorting', sorted_at = ? WHERE id = ?", (db.now(), tid))
        with mock.patch.object(models, "complete") as m:
            brain.sort_pending(self.con)
            m.assert_not_called()


    def test_telegram_commands(self):
        from engine.interfaces import telegram
        from tests.test_telegram import CHAT, FakeTelegram, msg
        fake = FakeTelegram()
        fake.updates = [msg(1, f"/queue {self.idea} ai"), msg(2, "/brain"), msg(3, "/undo 1"), msg(4, "/queue x")]
        with mock.patch.object(telegram, "api", fake):
            telegram.Bot(self.con, "TOKEN", CHAT, {"poll_seconds": 0}).poll_once()
        texts = [p["text"] for p in fake.sent()]
        self.assertIn("for the AI agent", texts[0])
        self.assertIn("Latest changes by Claude", texts[1])
        self.assertTrue(texts[2].startswith("Undone"))
        self.assertIn("Usage", texts[3])


if __name__ == "__main__":
    unittest.main()
