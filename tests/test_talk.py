import json, sqlite3, unittest
from unittest import mock

from engine import brain, compass, db, flow, models, talk


class Talk(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.vision = compass.create(self.con, "Own products and income")
        self.node = db.insert(self.con, "capabilities", title="Budget & spending", branch="operations", status="locked")
        self.con.commit()

    def test_questions_are_answered_statements_filed(self):
        for q in ("What should I do this week?", "should I learn Rust first", "Two things.\nWhich comes first?"):
            self.assertTrue(talk.is_question(q), q)
        for s in ("Book the exam on Friday.", "todo: what to buy?", "I want to sell the app", "Is done.\nAlso call Sam."):
            self.assertFalse(talk.is_question(s), s)

    def test_a_question_gets_an_answer_from_the_plan(self):
        with mock.patch.object(models, "complete", return_value=json.dumps({"reply": "Finish #1 first.", "thought": ""})):
            out = talk.say(self.con, "What should I focus on?")
        self.assertEqual(out, {"mode": "answer", "reply": "Finish #1 first."})

    def test_the_sender_can_choose_chat_or_dump(self):
        with mock.patch.object(models, "complete", return_value=json.dumps({"reply": "Fine.", "thought": ""})) as c:
            self.assertEqual(talk.say(self.con, "I feel stuck on the budget view", mode="ask")["mode"], "answer")
        self.assertEqual(c.call_args.args[0], "chat")
        with mock.patch.object(models, "complete", return_value=json.dumps({"memories": [], "summary": "Kept."})):
            self.assertEqual(talk.say(self.con, "What if I sold the app?", mode="file")["mode"], "filed")
        with self.assertRaises(ValueError):
            talk.say(self.con, "hi", mode="shout")

    def test_transcribe_keeps_finals_and_drops_previews(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, mock.patch.object(talk, "VOICE_DIR", Path(d)):
            with mock.patch.object(models, "transcribe", return_value=" hallo "):
                self.assertEqual(talk.transcribe(self.con, b"A", "audio/ogg", partial=True), "hallo")
                self.assertEqual(list(Path(d).iterdir()), [])
                self.assertEqual(talk.transcribe(self.con, b"A", "audio/ogg"), "hallo")
            self.assertEqual(len(list(Path(d).iterdir())), 1)
            with mock.patch.object(models, "transcribe", side_effect=models.ModelUnavailable("down")):
                with self.assertRaises(models.ModelUnavailable):
                    talk.transcribe(self.con, b"A", "audio/ogg", partial=True)
            self.assertEqual(len(list(Path(d).iterdir())), 1)
            self.assertIsNone(self.con.execute("SELECT 1 FROM memories WHERE tags = 'voice untranscribed'").fetchone())
            with self.assertRaises(ValueError):
                talk.transcribe(self.con, b"", "audio/ogg")

    def test_a_thought_is_sorted_top_down_and_says_where(self):
        reply = {"goals": [{"title": "First paying customer this year", "horizon": "year", "under": self.vision},
                           {"title": "Learn to paint", "horizon": "life"}],
                 "tasks": [{"title": "Budget view", "owner": "ai", "node": self.node}],
                 "ideas": [{"title": "Sell the budget tool", "node": 999}],
                 "memories": [{"kind": "lesson", "content": "Ship small"}], "summary": "Sorted."}
        with mock.patch.object(models, "complete", return_value=json.dumps(reply)):
            out = talk.say(self.con, "This year I want a first paying customer. Lifetime: learn to paint. Build a budget view; maybe sell it. Lesson: ship small.")
        self.assertEqual(out["mode"], "filed")
        text = out["reply"]
        self.assertIn("goal #", text); self.assertIn("a goal under “Own products and income” in Focus", text)
        self.assertIn("vision #", text); self.assertIn("a new vision in Focus", text)
        self.assertIn("Budget view – in Forge › Plan, for the builder", text)
        self.assertIn("Sell the budget tool – in Forge › Ideas", text)
        self.assertIn("Ship small – in Memory", text)
        row = lambda q: self.con.execute(q).fetchone()
        self.assertEqual(row("SELECT capability_id FROM tasks WHERE title = 'Budget view'")[0], self.node)
        self.assertIsNone(row("SELECT capability_id FROM ideas")[0])  # an unknown node is not linked
        self.assertEqual(row("SELECT kind FROM memories WHERE content = 'Ship small'")[0], "lesson")
        tid = brain.thoughts(self.con, 1)[0]["id"]
        for c in self.con.execute("SELECT id FROM brain_changes WHERE thought_id = ? ORDER BY id DESC", (tid,)).fetchall():
            brain.undo(self.con, c[0])
        self.assertEqual(row("SELECT kind FROM memories WHERE content = 'Ship small'")[0], "forgotten")
        self.assertEqual(flow.placed(self.con, tid), [])  # every item it made is undone

    def test_a_lifetime_aim_under_a_vision_becomes_a_goal_and_dates_only_when_named(self):
        reply = {"goals": [{"title": "Ten customers", "vision": True, "under": self.vision, "due": "2027-06-30"},
                           {"title": "Learn Spanish", "under": self.vision, "due": "soon"}], "summary": "ok"}
        with mock.patch.object(models, "complete", return_value=json.dumps(reply)):
            talk.say(self.con, "ten customers for the vision by the end of June 2027; learn Spanish")
        rows = {r[0]: r[1:] for r in self.con.execute("SELECT title, horizon, parent_id, due FROM goals WHERE parent_id IS NOT NULL")}
        self.assertEqual(rows["Ten customers"], ("goal", self.vision, "2027-06-30"))
        self.assertEqual(rows["Learn Spanish"], ("goal", self.vision, None))

if __name__ == "__main__":
    unittest.main()
