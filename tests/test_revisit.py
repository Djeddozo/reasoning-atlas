"""Scheduled revisit (#79): old built features get keep/improve/merge/remove verdicts; changes become drafts only."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest import mock

from engine import db, models, revisit


def ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


class RevisitTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.cap = db.insert(self.con, "capabilities", title="Voice input", branch="core")
        ins = lambda title, days, **f: db.insert(self.con, "tasks", title=title, owner="ai", status="done",
                                                 done_at=ago(days), **f)
        self.voice = ins("Browser voice button", 90, capability_id=self.cap, description="Web Speech API")
        self.brief = ins("Morning brief", 60)
        self.tabs = ins("Eight dashboard tabs", 45)
        self.fresh = ins("Brand new feature", 2)
        self.mine = db.insert(self.con, "tasks", title="Djeddi's own task", owner="me", status="done", done_at=ago(80))
        self.todo = db.insert(self.con, "tasks", title="Open AI task", owner="ai")
        db.insert(self.con, "memories", kind="lesson", content="Browser voice input fails on iOS; use Whisper")
        self.con.commit()
        self.conf = mock.patch.object(revisit, "cfg", return_value=dict(revisit.DEFAULTS))
        self.conf.start(); self.addCleanup(self.conf.stop)

    def answer(self, items, summary="Voice is outdated."):
        self.prompts = []
        def complete(job, system, user):
            self.prompts.append((job, user))
            return json.dumps({"summary": summary, "items": items})
        return complete

    def task(self, tid):
        return self.con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()

    def test_features_are_old_done_ai_tasks_least_recently_revisited_first(self):
        ids = [f["id"] for f in revisit.features(self.con)]
        self.assertEqual(ids, [self.voice, self.brief, self.tabs])  # not the fresh one, not Djeddi's own, not open work

    def test_prompt_carries_features_lessons_and_open_plan(self):
        revisit.run(self.con, complete=self.answer([{"feature": self.voice, "action": "keep", "reason": "ok"}]))
        job, user = self.prompts[0]
        self.assertEqual(job, "revisit")
        self.assertIn(f"FEATURE #{self.voice}: Browser voice button", user)
        self.assertIn("skill tree node: Voice input", user)
        self.assertIn("fails on iOS", user)
        self.assertIn(f"#{self.todo} Open AI task", user)

    def test_changes_become_drafts_and_keep_is_only_recorded(self):
        out = revisit.run(self.con, complete=self.answer([
            {"feature": self.voice, "action": "improve", "reason": "lesson: iOS fails",
             "task": {"title": "Voice via Whisper everywhere", "description": "swap the engine", "effort_h": 2, "impact": 4}},
            {"feature": self.tabs, "action": "merge", "with": self.brief, "reason": "P3: four tabs", "task": {}},
            {"feature": self.brief, "action": "keep", "reason": "still used daily"},
        ]))
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["counts"]["improve"], 1); self.assertEqual(out["counts"]["keep"], 1)
        self.assertEqual(len(out["drafts"]), 2)
        d = self.task(out["drafts"][0])
        self.assertEqual((d["status"], d["owner"], d["capability_id"], d["impact"]), ("proposed", "ai", self.cap, 4))
        self.assertIn(f"Revisit of #{self.voice}", d["description"])
        m = self.task(out["drafts"][1])
        self.assertIn(f"merge with #{self.brief}", m["description"])
        self.assertIn("never delete data", m["description"])
        self.assertEqual(self.task(self.voice)["status"], "done")  # the feature itself is untouched
        self.assertIn("hq accept", revisit.text(out))
        self.assertIn("still used daily", revisit.show(self.con))

    def test_invalid_verdicts_are_skipped(self):
        out = revisit.run(self.con, complete=self.answer([
            {"feature": self.fresh, "action": "improve", "reason": "not reviewed", "task": {"title": "x"}},
            {"feature": self.voice, "action": "rewrite", "reason": "unknown action"},
            {"feature": self.brief, "action": "keep", "reason": ""},
            {"feature": self.tabs, "action": "merge", "with": self.tabs, "reason": "into itself"},
            {"feature": self.voice, "action": "keep", "reason": "fine"},
            {"feature": self.voice, "action": "remove", "reason": "twice"},
        ]))
        self.assertEqual((out["reviewed"], out["skipped"], out["drafts"]), (1, 5, []))

    def test_schedule_due_and_retry_after_failure(self):
        self.assertTrue(revisit.due(self.con))
        def down(*a):
            raise models.ModelUnavailable("offline")
        self.assertEqual(revisit.run(self.con, force=False, complete=down)["status"], "error")
        self.assertTrue(revisit.due(self.con))  # failed: still due tomorrow
        revisit.run(self.con, force=False, complete=self.answer([{"feature": self.voice, "action": "keep", "reason": "ok"}]))
        self.assertFalse(revisit.due(self.con))
        self.assertEqual(revisit.run(self.con, force=False)["status"], "not_due")
        db.set_state(self.con, "revisit:last", json.dumps({"ts": ago(31)}))
        self.assertTrue(revisit.due(self.con))

    def test_off_in_constitution(self):
        with mock.patch.object(revisit, "cfg", return_value=revisit.DEFAULTS | {"enabled": False}):
            self.assertEqual(revisit.run(self.con, force=False)["status"], "off")

    def test_rotation_and_open_drafts_skip_features(self):
        out = revisit.run(self.con, complete=self.answer([
            {"feature": self.voice, "action": "improve", "reason": "iOS", "task": {"title": "Whisper"}},
            {"feature": self.brief, "action": "keep", "reason": "fine"},
        ]))
        ids = [f["id"] for f in revisit.features(self.con)]
        self.assertNotIn(self.voice, ids)              # its draft is still open
        self.assertEqual(ids[0], self.tabs)            # never revisited goes first
        self.assertEqual(ids[-1], self.brief)          # just revisited goes last
        db.update(self.con, "tasks", out["drafts"][0], status="dropped")
        self.assertIn(self.voice, [f["id"] for f in revisit.features(self.con)])

    def test_undo_sets_drafts_aside(self):
        out = revisit.run(self.con, complete=self.answer([
            {"feature": self.voice, "action": "remove", "reason": "superseded", "task": {"title": "Remove old voice"}}]))
        self.assertIn("1 draft", revisit.undo(self.con, out["run"]))
        self.assertEqual(self.task(out["drafts"][0])["status"], "dropped")

    def test_nothing_old_enough(self):
        with mock.patch.object(revisit, "cfg", return_value=revisit.DEFAULTS | {"min_age_days": 1000}):
            out = revisit.run(self.con, complete=self.answer([]))
        self.assertEqual(out["status"], "nothing")
        self.assertTrue(revisit.due(self.con))  # checked again tomorrow, not in a month

    def test_constitution_routes_the_job(self):
        rules = _loop.load_constitution()
        self.assertIn("revisit", rules["models"]["routing"])
        self.assertGreaterEqual(rules["revisit"]["every_days"], 1)


if __name__ == "__main__":
    unittest.main()
