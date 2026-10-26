"""Overlap check (#89): tasks and ideas compared with Atlas, the arsenal and the backlog: exists | partial | new."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import arsenal, db, devloop, gatekeeper, models, needs, overlap, taskdetail

CONF = dict(overlap.DEFAULTS)
REAL_MODULES = overlap.modules


class OverlapTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)
        ins = lambda title, **f: db.insert(self.con, "tasks", title=title, owner="ai", **f)
        self.built = ins("Weekly progress report per goal with streaks", status="done",
                         description="Streaks, goals and a weekly review in engine/progress.py")
        self.dup = ins("Telegram voice notes into the brain dump")
        self.again = ins("Telegram voice notes into the brain dump", description="same idea, queued twice")
        self.more = ins("Progress report per goal: add a weekly chart of streaks and goals")
        self.fresh = ins("Calendar sync for school timetable")
        self.idea = db.insert(self.con, "ideas", title="Sourdough baking schedule")
        self.con.commit()
        for p in (mock.patch.object(overlap, "cfg", return_value=dict(CONF)),
                  mock.patch.object(overlap, "modules", return_value=[]),
                  mock.patch.object(gatekeeper, "notify", return_value=False)):
            p.start(); self.addCleanup(p.stop)

    def check(self, tid, **kw):
        return overlap.check(self.con, "task", tid, use_model=kw.pop("use_model", False))

    def test_rule_three_verdicts(self):
        self.assertEqual(self.check(self.fresh)["verdict"], "new")
        same = self.check(self.again)
        self.assertEqual((same["verdict"], same["match_kind"], same["match_ref"]), ("exists", "task", f"#{self.dup}"))
        part = self.check(self.more)
        self.assertEqual((part["verdict"], part["match_ref"], part["by"]), ("partial", f"#{self.built}", "rule"))
        self.assertIn("progress", part["why"])

    def test_rule_partial_never_points_at_an_open_task(self):
        db.update(self.con, "tasks", self.built, status="todo")
        self.assertEqual(self.check(self.more)["verdict"], "new")

    def test_modules_and_arsenal_are_candidates(self):
        overlap.modules.return_value = [{"kind": "module", "ref": "engine/radar.py", "title": "Research",
                                         "text": "Research feeds GitHub arXiv radar scan sources"}]
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "calendar_sync.py").write_text('"""Sync a school timetable into a calendar."""\n'
                                                      'def sync_timetable(calendar):\n    """Sync the school timetable."""\n')
            arsenal.scan(self.con, arsenal.add_project(self.con, d, "school-tools"))
            refs = [c["ref"] for c in overlap.candidates(self.con, {"kind": "task", "id": self.fresh,
                                                                     "title": "Calendar sync for school timetable"})]
        self.assertTrue(any(r.startswith("school-tools:calendar_sync.py") for r in refs))
        r = overlap.candidates(self.con, {"kind": "idea", "id": 0, "title": "Radar scan of arXiv research feeds"})
        self.assertEqual(r[0]["ref"], "engine/radar.py")

    def test_real_modules_come_from_the_claude_md_table(self):
        real = REAL_MODULES()
        refs = {m["ref"] for m in real}
        self.assertTrue({"engine/arsenal.py", "engine/overlap.py", "engine/radar.py"} <= refs)
        self.assertTrue(all(m["text"] for m in real))
        titles = {m["ref"]: m["title"] for m in real}
        self.assertEqual(titles["engine/overlap.py"], "Overlap check")  # escaped pipes in a cell do not shift the columns

    def test_model_judges_close_items_and_bad_answers_fall_back(self):
        answer = json.dumps({"checks": [
            {"item": f"task:{self.more}", "verdict": "partial", "match": f"#{self.built}", "why": "The report exists; the chart does not.",
             "rewrite": {"title": "Add a weekly chart to the progress report", "description": "Extend engine/progress.py."}},
            {"item": f"task:{self.again}", "verdict": "exists", "match": "#999", "why": "made up"}]})
        items = [overlap._item(self.con, "task", t) for t in (self.more, self.again, self.fresh)]
        with mock.patch.object(models, "complete", return_value=answer) as call:
            out = overlap.check_many(self.con, items, use_model=True)
        self.assertEqual(call.call_args.args[0], "overlap")
        sent = json.loads(call.call_args.args[2])["items"]
        self.assertNotIn(f"task:{self.fresh}", [i["item"] for i in sent])  # nothing close: no model needed
        self.assertEqual((out[0]["by"], out[0]["rewrite"]["title"]), ("ai", "Add a weekly chart to the progress report"))
        self.assertEqual((out[1]["by"], out[1]["verdict"]), ("rule", "exists"))  # unknown match → the rule stands
        with mock.patch.object(models, "complete", side_effect=RuntimeError("offline")):
            self.assertEqual(overlap.check(self.con, "task", self.more, use_model=True)["by"], "rule")

    def test_scan_covers_open_ai_tasks_and_ideas_and_skips_fresh_verdicts(self):
        out = overlap.scan(self.con, use_model=False)
        self.assertEqual(out["checked"], 5)  # four open AI tasks + one idea; the done one is not backlog
        self.assertEqual((out["exists"], out["partial"]), (2, 1))  # the duplicate pair both point at each other
        self.assertEqual(overlap.scan(self.con, use_model=False)["checked"], 0)
        self.con.execute("UPDATE tasks SET description = 'changed' WHERE id = ?", (self.fresh,))
        self.assertEqual(overlap.scan(self.con, use_model=False)["checked"], 1)  # changed text: checked again
        listed = [(c["kind"], c["item_id"]) for c in overlap.listing(self.con)]
        self.assertEqual({x[1] for x in listed[:2]}, {self.again, self.dup})  # already built first
        self.assertIn(("task", self.more), listed)

    def test_gate_holds_already_built_work_and_applies_the_answer(self):
        task = self.con.execute("SELECT * FROM tasks WHERE id = ?", (self.again,)).fetchone()
        why = overlap.hold(self.con, task, conf=dict(CONF, use_model=False))
        self.assertIn("looks already built", why)
        item = next(i for i in needs.items(self.con) if i["kind"] == "decision")
        self.assertEqual(item["options"], ["Drop it", f"Merge into #{self.dup}", "Build anyway"])
        self.assertIn("waiting for your answer", overlap.hold(self.con, task, conf=dict(CONF, use_model=False)))  # asked once
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 1)
        needs.answer(self.con, "decision", item["id"], f"Merge into #{self.dup}")
        self.assertIn("merged", overlap.hold(self.con, task, conf=dict(CONF, use_model=False)))
        status = lambda t: self.con.execute("SELECT status, description FROM tasks WHERE id = ?", (t,)).fetchone()
        self.assertEqual(status(self.again)["status"], "dropped")
        self.assertIn(f"Merged from #{self.again}", status(self.dup)["description"])
        undo = self.con.execute("SELECT COUNT(*) FROM brain_changes WHERE table_name = 'tasks'").fetchone()[0]
        self.assertEqual(undo, 2)  # both changes are in the change log, so undoable

    def test_build_anyway_releases_the_task(self):
        task = self.con.execute("SELECT * FROM tasks WHERE id = ?", (self.again,)).fetchone()
        overlap.hold(self.con, task, conf=dict(CONF, use_model=False))
        self.assertEqual(overlap.decide(self.con, self.again, "build"), f"Task #{self.again}: build.")
        self.assertEqual(overlap.hold(self.con, task, conf=dict(CONF, use_model=False)), "")
        self.assertEqual(self.con.execute("SELECT status FROM approvals").fetchone()[0], "answered")
        self.assertIn("anyway", overlap.prompt_block(self.con, self.again))
        with self.assertRaises(ValueError):
            overlap.decide(self.con, self.fresh, "drop")  # never checked

    def test_partial_and_new_do_not_hold_and_partial_reaches_the_prompt(self):
        for tid in (self.more, self.fresh):
            task = self.con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
            self.assertEqual(overlap.hold(self.con, task, conf=dict(CONF, use_model=False)), "")
        block = overlap.prompt_block(self.con, self.more)
        self.assertIn(f"#{self.built}", block)
        self.assertIn("extend it", block)
        self.assertEqual(overlap.prompt_block(self.con, self.fresh), "")
        self.assertIn("Partly built", taskdetail.detail(self.con, self.more)["overlap"]["label"])

    def test_disabled_never_holds(self):
        task = self.con.execute("SELECT * FROM tasks WHERE id = ?", (self.again,)).fetchone()
        self.assertEqual(overlap.hold(self.con, task, conf=dict(CONF, enabled=False)), "")
        self.assertIsNone(overlap.get(self.con, "task", self.again))

    def test_pick_task_skips_held_work(self):
        plan = {"mine": [], "proposed": [{"id": self.again, "title": "x", "owner": "ai", "state": "ready"},
                                         {"id": self.fresh, "title": "y", "owner": "ai", "state": "ready"}]}
        with mock.patch("engine.skilltree.build_plan", return_value=plan), \
                mock.patch.object(overlap, "cfg", return_value=dict(CONF, use_model=False)):
            self.assertEqual(devloop.pick_task(self.con, ask=True)["id"], self.fresh)
            self.assertEqual(devloop.pick_task(self.con)["id"], self.fresh)  # display only: the stored hold counts


if __name__ == "__main__":
    unittest.main()
