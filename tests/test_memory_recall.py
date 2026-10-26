"""Relevant lessons and decisions are recalled into dev-loop and brain prompts."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import json
import sqlite3
import unittest

from engine import brain, db, devloop


class RecallTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        db.insert(self.con, "memories", kind="lesson", content="Dashboard tabs must stay four; merge duplicates", tags="ui", created=db.now())
        db.insert(self.con, "memories", kind="info", content="Dashboard colour is blue", created=db.now())
        db.insert(self.con, "memories", kind="decision", content="Voice uses cloud Whisper", created=db.now())
        self.con.commit()

    def test_only_lessons_decisions_preferences_match(self):
        rows = db.relevant_memories(self.con, "Fix the dashboard tabs")
        self.assertEqual([r["kind"] for r in rows], ["lesson"])

    def test_punctuation_and_empty_are_safe(self):
        self.assertEqual(db.relevant_memories(self.con, '"" ( AND * -'), [])
        self.assertEqual(db.relevant_memories(self.con, ""), [])
        self.assertIn("Voice", db.memory_block(self.con, 'voice: "whisper"?'))

    def test_block_empty_without_match(self):
        self.assertEqual(db.memory_block(self.con, "unrelated gardening"), "")

    def test_block_marks_data(self):
        self.assertIn("not instructions", db.memory_block(self.con, "dashboard tabs"))

    def test_brain_context_includes_memories(self):
        db.insert(self.con, "tasks", title="Rework dashboard tabs", impact=2)
        ctx = json.loads(brain._context(self.con))
        self.assertTrue(any("four" in m["content"] for m in ctx["relevant_memories"]))

    def test_devloop_prompt_has_slot(self):
        self.assertIn("{memory}", devloop.PROMPT)


if __name__ == "__main__":
    unittest.main()
