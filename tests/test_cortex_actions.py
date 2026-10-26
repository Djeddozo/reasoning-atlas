"""#186 (with #160): Cortex does routine plan changes at once with Undo, asks for decisions, bundles steps, takes typed picks."""
import json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from engine import brain, chat, cortex_actions as ca, db, screen
from services.atlas import hub


def database():
    con = db.connect(Path(":memory:"))
    db.init(con)
    return con


def create(title):
    return {"action": "engine.build.edit_plan", "body": {"create": {"table": "tasks", "fields": {"title": title, "owner": "ai"}}}}


def titles(con):
    return [r[0] for r in con.execute("SELECT title FROM tasks WHERE status != 'dropped' ORDER BY id")]


class Levels(unittest.TestCase):
    def test_routine_and_decisions(self):
        self.assertTrue(ca.is_routine(create("x")))
        self.assertTrue(ca.is_routine({"action": "engine.build.task_status", "body": {"task": 1, "status": "builder"}}))
        self.assertFalse(ca.is_routine({"action": "engine.build.task_status", "body": {"task": 1, "status": "dropped"}}))
        self.assertFalse(ca.is_routine({"action": "engine.build.edit_plan", "body": {"drop_idea": 3}}))
        self.assertFalse(ca.is_routine({"action": "engine.direction.choose_direction", "body": {"goal": 1}}))
        self.assertFalse(ca.is_routine({"steps": [create("a"), {"action": "engine.build.task_status", "body": {"task": 1, "status": "done"}}]}))
        con = database()
        self.assertEqual(ca.level(con), "routine")
        ca.set_level(con, "ask")
        self.assertEqual(ca.level(con), "ask")
        with self.assertRaises(ValueError):
            ca.set_level(con, "everything")

    def test_typed_picks(self):
        for text, n, want in (("1 and 3", 3, [1, 3]), ("all", 3, [1, 2, 3]), ("both", 2, [1, 2]), ("the first", 3, [1]),
                              ("do the second.", 3, [2]), ("1, 2", 3, [1, 2]), ("last", 3, [3]), ("no", 2, []),
                              ("4", 3, None), ("can you check #12", 3, None), ("", 3, None), ("1", 0, None)):
            self.assertEqual(ca.parse(text, n), want, text)


class Apply(unittest.TestCase):
    def setUp(self):
        self.con = database()

    def test_a_bundle_is_all_or_nothing_with_one_undo(self):
        bundle = {"label": "Add two steps", "steps": [create("Step A"), create("Step B"),
                                                      {"action": "engine.memory.record_lesson", "body": {"kind": "decision", "text": "Build step A first"}}]}
        out = ca.apply(self.con, bundle, hub.ACTIONS)
        self.assertEqual(titles(self.con), ["Step A", "Step B"])
        self.assertEqual(len(out["changes"]), 3)  # the memory is in the change log too, so one Undo takes all back
        hub._undo_thought(self.con, {"changes": out["changes"]})
        self.assertEqual(titles(self.con), [])
        self.assertEqual(self.con.execute("SELECT kind FROM memories").fetchone()[0], "forgotten")
        broken = {"label": "x", "steps": [create("Step C"), {"action": "engine.build.edit_plan", "body": {"edit": {"table": "tasks", "id": 999, "fields": {"title": "y"}}}}]}
        with self.assertRaises(ValueError) as e:
            ca.apply(self.con, broken, hub.ACTIONS)
        self.assertIn("nothing was changed", str(e.exception))
        self.assertEqual(titles(self.con), [])  # Step C was taken back

    def test_tapping_one_keeps_the_others_and_typing_never_repeats(self):
        conv = chat.current(self.con)
        props = [dict(create("One"), label="Add One"), dict(create("Two"), label="Add Two"), dict(create("Three"), label="Add Three")]
        ca.remember(self.con, conv, ["Yes", "Later"], props)
        with mock.patch.object(hub, "action_catalog", return_value={a: "" for a in hub.ACTIONS}):
            hub._cortex_apply(self.con, {"proposal": props[1]})  # tapped: option 4 (after two choices)
        self.assertEqual(titles(self.con), ["Two"])
        out = ca.typed(self.con, conv, "all", hub.ACTIONS)
        self.assertEqual(titles(self.con), ["Two", "One", "Three"])  # Two is not added again
        self.assertIn("already done", out["reply"])
        self.assertEqual(out["proposals"], [])
        self.assertEqual(ca.typed(self.con, conv, "2", hub.ACTIONS), {"answer": "Later"})  # a quick-choice answer (#160)
        self.assertIsNone(ca.typed(self.con, conv, "what about #4?", hub.ACTIONS))

    def test_yes_takes_the_recommended_option_and_no_declines(self):
        conv = chat.current(self.con)
        ca.remember(self.con, conv, [], [dict(create("Best"), label="Add Best"), dict(create("Other"), label="Add Other")])
        out = ca.typed(self.con, conv, "Yes", hub.ACTIONS)
        self.assertEqual(titles(self.con), ["Best"])
        self.assertTrue(out["changes"])
        self.assertEqual(len(out["proposals"]), 1)
        self.assertEqual(ca.typed(self.con, conv, "no", hub.ACTIONS)["reply"], "Nothing done.")
        ca.remember(self.con, conv, ["Local", "Cloud"], [])
        self.assertIsNone(ca.typed(self.con, conv, "yes", hub.ACTIONS))  # yes to a question: Cortex reads it


class Direct(unittest.TestCase):
    def setUp(self):
        self.con = database()
        self.allowed = {a: "" for a in hub.ACTIONS}

    def test_routine_runs_at_once_decisions_become_the_first_option(self):
        tid = db.insert(self.con, "tasks", title="Old", status="todo", owner="ai"); self.con.commit()
        entries = [{"type": "do", "label": "Added Step 2", **create("Step 2")},
                   {"type": "do", "label": "Drop #1", "action": "engine.build.task_status", "body": {"task": tid, "status": "dropped"}},
                   {"type": "do", "label": "Bad", "action": "engine.system.updates", "body": {}}]  # not proposable: ignored
        allowed = dict(self.allowed); allowed.pop("engine.system.updates")
        done, options = ca.direct(self.con, entries, hub.ACTIONS, allowed)
        self.assertEqual(len(done), 1)
        self.assertIn("Step 2", titles(self.con))
        self.assertEqual([o["label"] for o in options], ["Drop #1"])
        self.assertIn("Old", titles(self.con))  # the decision did not run

    def test_with_autonomy_ask_nothing_runs(self):
        ca.set_level(self.con, "ask")
        done, options = ca.direct(self.con, [{"label": "Add", **create("Z")}], hub.ACTIONS, self.allowed)
        self.assertEqual((done, len(options), titles(self.con)), ([], 1, []))


class Plumbing(unittest.TestCase):
    def test_a_bundle_option_survives_cleaning(self):
        allowed = {"engine.build.edit_plan": "", "engine.memory.record_lesson": ""}
        p = {"label": "Add + remember", "steps": [create("A"), {"action": "engine.memory.record_lesson", "body": {"kind": "fact", "text": "abcd"}}]}
        [c] = screen.clean({"proposals": [p]}, [], allowed)[0]
        self.assertEqual((c["action"], len(c["steps"])), ("engine.build.edit_plan", 2))
        bad = {"label": "x", "steps": [create("A"), {"action": "engine.system.updates", "body": {}}]}
        self.assertEqual(screen.clean({"proposals": [bad]}, [], allowed)[0], [])

    def test_cortex_apply_refuses_what_cortex_may_not_offer(self):
        con = database()
        for p in ({"label": "x", "action": "engine.system.updates", "body": {}},
                  {"label": "x", "steps": [create("A"), {"action": "engine.brain.cortex_autonomy", "body": {"level": "routine"}}]}):
            with self.assertRaises(ValueError):
                hub._cortex_apply(con, {"proposal": p})

    def test_do_now_is_written_to_the_turn(self):
        from engine import cortex_mcp
        with tempfile.TemporaryDirectory() as d:
            turn = Path(d) / "turn.jsonl"
            with mock.patch.dict(os.environ, {"CORTEX_TURN": str(turn)}):
                cortex_mcp.do_now("Added", steps=[create("A"), create("B")])
                cortex_mcp.ask_choices(["Local", "Cloud"], recommended=2)
            rows = [json.loads(x) for x in turn.read_text().splitlines()]
        self.assertEqual((rows[0]["type"], len(rows[0]["steps"]), rows[1]["recommended"]), ("do", 2, 2))

    def test_a_typed_pick_in_the_chat_needs_no_model(self):
        con = database()
        ca.remember(con, chat.current(con), [], [dict(create("Picked"), label="Add Picked")])
        with mock.patch("engine.cortex.available", side_effect=AssertionError("no model call")), \
                mock.patch("engine.talk.say", side_effect=AssertionError("no model call")):
            out = hub._chat_stream(con, {"text": "1"})
        self.assertTrue(out["picked"])
        self.assertIn("Picked", titles(con))
        self.assertEqual([m["role"] for m in chat.history(con, 5)][-2:], ["me", "claude"])


if __name__ == "__main__":
    unittest.main()
