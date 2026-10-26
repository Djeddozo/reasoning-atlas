import json, threading, tempfile, unittest
from pathlib import Path
from unittest import mock

from engine import db, models, screen
from services.atlas import hub
from tests.test_hub import TOKEN, get, post

VIEWS = ["Today", "Forge › Plan", "Cortex"]


def database():
    con = db.connect(Path(":memory:"))
    db.init(con)
    return con


class Propose(unittest.TestCase):
    def setUp(self):
        self.con = database()
        self.t = db.insert(self.con, "tasks", title="Write the grant report", status="todo", owner="me", score=3)
        self.con.commit()

    def answer(self, out):
        return mock.patch.object(models, "complete", return_value=json.dumps(out))

    def test_proposals_are_cleaned_and_nothing_changes(self):
        out = {"reply": "Done is the obvious step.", "proposals": [
            {"label": f"Mark #{self.t} done", "why": "you said it is finished", "action": "engine.build.task_status",
             "body": {"task": self.t, "status": "done"}, "view": "Forge › Plan", "highlight": "Write the grant report"},
            {"label": "Stop everything", "action": "engine.system.kill_switch", "body": {"on": True}},  # not allowed
            {"label": "Show it on the moon", "action": "", "view": "Moon"},  # nothing to do or show
            {"label": "Show the plan", "action": "", "view": "Forge › Plan"},
            {"label": "Plan only", "action": "engine.build.idea_to_plan", "body": {"idea": 1, "goal": 1}},  # full chat only
            {"label": "Fourth", "action": "engine.brain.write_thought", "body": {"text": "x"}},
            {"label": "Fifth", "action": "engine.brain.write_thought", "body": {"text": "y"}}]}
        with self.answer(out) as call:
            r = screen.propose(self.con, "I finished the grant report", "Today", "Write the grant report  #%d" % self.t, VIEWS)
        self.assertEqual([p["label"] for p in r["proposals"]], [f"Mark #{self.t} done", "Show the plan", "Fourth"])
        self.assertEqual(r["proposals"][0]["view"], "Forge › Plan")
        self.assertIn("Write the grant report", call.call_args[0][2])  # the screen is sent as data
        self.assertIn("engine.build.task_status", call.call_args[0][1])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.t,)).fetchone()[0], "todo")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM chat").fetchone()[0], 0)  # kept only when he asks
        self.assertIn("Kept", screen.keep(self.con, [{"role": "me", "text": "I finished the grant report"}, {"role": "claude", "text": r["reply"]}]))
        self.assertEqual([x[0] for x in self.con.execute("SELECT role FROM chat ORDER BY id")], ["me", "claude"])
        first = self.con.execute("SELECT conv FROM chat").fetchone()[0]
        screen.keep(self.con, [{"role": "me", "text": "another"}])  # a second keep starts its own conversation
        self.assertNotEqual(self.con.execute("SELECT conv FROM chat ORDER BY id DESC").fetchone()[0], first)
        with self.assertRaises(ValueError):
            screen.keep(self.con, [])

    def test_cortex_decides_the_kind(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("no key")):
            r = screen.propose(self.con, "Idea: a garden journal", "Today", "", VIEWS)  # nobody to decide: kept as a note
        self.assertEqual(r["kind"], "note")
        self.assertTrue(self.con.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0])
        with self.answer({"kind": "reminder", "reminder": "Call Sam on Friday"}):
            r = screen.propose(self.con, "remind me to call Sam friday", "Today", "", VIEWS)
        self.assertEqual((r["kind"], r["reminder"]), ("reminder", "Call Sam on Friday"))
        with self.answer({"kind": "find", "query": "grant report"}):
            self.assertEqual(screen.propose(self.con, "where did I put the grant thing", "Today", "", VIEWS)["query"], "grant report")
        with self.answer({"kind": "answer", "reply": "Two tasks are open."}):
            self.assertEqual(screen.propose(self.con, "how much is open?", "Today", "", VIEWS)["kind"], "answer")
        with self.assertRaises(ValueError):
            screen.propose(self.con, "   ")

    def test_choices_when_cortex_needs_an_answer(self):
        with self.answer({"reply": "Which one do you mean?", "choices": ["The bank call", "The grant report", "", "x" * 200],
                          "proposals": []}):
            r = screen.propose(self.con, "move it up", "Today", "", VIEWS)
        self.assertEqual(r["choices"], ["The bank call", "The grant report", "x" * 80])
        self.assertEqual(screen.numbered("Which?", [{"label": "Do it"}], ["A", "B"]), "Which?\n1. A\n2. B\n3. Do it")

    def test_quick_reach_is_small_and_the_chat_reach_is_everything_else(self):
        for action in screen.QUICK:
            self.assertIn(action, hub.ACTIONS)
        for heavy in ("engine.system.kill_switch", "engine.build.dev_controls", "engine.build.idea_to_plan", "engine.build.do_over"):
            self.assertNotIn(heavy, screen.QUICK)
        full = hub.action_catalog()
        self.assertEqual(set(full), set(hub.ACTIONS) - hub.NOT_PROPOSABLE)
        for never in ("engine.build.claude_login", "engine.system.updates", "engine.brain.ingest", "engine.brain.undo_changes"):
            self.assertNotIn(never, full)
        self.assertIn("Body keys: on", full["engine.system.kill_switch"])
        self.assertIn('"drop_idea"', full["engine.build.edit_plan"])


class FullChat(unittest.TestCase):
    def test_full_chat_proposes_anything_offers_a_mockup_and_files(self):
        con = database()
        out = {"reply": "A weekly review would help; and the panel idea deserves a picture.", "thought": "I want a weekly progress panel",
               "choices": [], "mockup": {"title": "Weekly progress panel", "brief": "bars per goal on Today"},
               "proposals": [{"label": "Write this week's review", "action": "engine.direction.weekly_review", "body": {}},
                             {"label": "Log in", "action": "engine.build.claude_login", "body": {}}]}
        acts = hub.action_catalog()
        with mock.patch.object(models, "complete", return_value=json.dumps(out)) as call:
            from engine import talk
            r = talk.say(con, "How about a weekly progress panel on Today?", "atlas", "ask", acts, VIEWS)
        self.assertIn("engine.direction.weekly_review", call.call_args[0][1])
        self.assertEqual([p["action"] for p in r["proposals"]], ["engine.direction.weekly_review", "engine.brain.write_thought"])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0], 0)  # nothing filed unasked in the chat
        self.assertEqual(r["mockup"]["title"], "Weekly progress panel")
        with mock.patch.object(models, "complete", return_value='{"reply": "Fine.", "thought": ""}'):
            plain = talk.say(con, "What next?", "atlas", "ask")
        self.assertNotIn("proposals", plain)  # the quick and old paths keep their plain answer


class ThroughHub(unittest.TestCase):
    def test_speak_then_confirm(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hq.db"
            con = db.connect(path)
            db.init(con)
            tid = db.insert(con, "tasks", title="Call the bank", status="todo", owner="me", score=2)
            con.commit(); con.close()
            server = hub.make_server("127.0.0.1", 0, TOKEN, path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}/v1/actions/"
            try:
                out = {"reply": "Mark it important?", "proposals": [{"label": f"Make #{tid} important", "action": "engine.build.task_status",
                                                                     "body": {"task": tid, "status": "important"}, "view": "Today"}]}
                with mock.patch.object(models, "complete", return_value=json.dumps(out)):
                    r = post(url + "engine.brain.speak_to", {"text": "this one first", "view": "Today", "visible": "Call the bank", "views": VIEWS})
                p = r["message"]["proposals"][0]
                done = post(url + p["action"], p["body"])  # what Atlas does after yes / 1
                self.assertIn("important", done["message"])
                self.assertTrue(done["changes"])
                with mock.patch.object(models, "complete", return_value=json.dumps({**out, "thought": "", "mockup": None})):
                    full = post(url + "engine.brain.talk", {"text": "what now?", "mode": "ask", "full": True, "views": VIEWS})["message"]
                    plain = post(url + "engine.brain.talk", {"text": "what now?", "mode": "ask"})["message"]
                self.assertEqual(full["proposals"][0]["label"], f"Make #{tid} important")
                self.assertIsInstance(plain, str)
            finally:
                server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
