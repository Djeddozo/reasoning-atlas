"""Task do-over: a keep/change/done/drop/merge/new diff, applied only after approval, undoable as one batch."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import sqlite3
import unittest

from engine import db, doover, loop

ROOT = _pl.Path(__file__).resolve().parent.parent


class DooverTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.goal = db.insert(self.con, "goals", title="Atlas runs on its own")
        ins = lambda title, **f: db.insert(self.con, "tasks", title=title, **f)
        self.pi = ins("Pi host", due="2026-10-25")
        self.think = ins("Enable think", depends_on=str(self.pi))
        self.radar = ins("GitHub radar", depends_on=f"{self.think},{self.pi}", due="2026-12-20")
        self.page = ins("One-page dashboard")
        self.keep = ins("Constitution check")
        self.done = ins("Already finished", status="done")
        self.con.commit()
        self.items = [
            {"kind": "keep", "task": self.keep, "reason": "fine"},
            {"kind": "done", "task": self.pi, "reason": "already running"},
            {"kind": "drop", "task": self.think, "reason": "superseded by the brain"},
            {"kind": "change", "task": self.radar, "fields": {"due": None, "impact": 2}, "reason": "stage 2"},
            {"kind": "merge", "task": self.page, "into": "N1", "reason": "four tabs replace it"},
            {"kind": "new", "key": "N1", "fields": {"title": "Four tabs", "owner": "ai", "goal_id": self.goal}, "reason": "P3"},
            {"kind": "new", "key": "N2", "fields": {"title": "Colours", "owner": "ai"}, "after": ["N1"], "reason": "P3"},
        ]

    def task(self, tid):
        return self.con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()

    def approve(self, aid):
        self.con.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (aid,)); self.con.commit()

    def test_proposal_changes_nothing_until_approved(self):
        before = [tuple(r) for r in self.con.execute("SELECT * FROM tasks")]
        aid = doover.propose(self.con, self.items, "test")
        row = self.con.execute("SELECT * FROM approvals WHERE id = ?", (aid,)).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertIn("Plan re-evaluation: 1 changed, 1 closed as built, 1 dropped, 1 merged, 2 new (1 kept)", row["request"])
        self.assertEqual(doover.changes(self.con, aid)["groups"]["drop"][0]["title"], self.con.execute("SELECT title FROM tasks WHERE id = ?", (self.items[2]["task"],)).fetchone()[0])
        self.assertIn(f"hq doover show {aid}", row["request"])
        text = doover.show(self.con, aid)
        self.assertIn("DROP (1)", text); self.assertIn("due: '2026-12-20' → None", text); self.assertIn("+ N2 Colours", text)
        self.assertEqual(before, [tuple(r) for r in self.con.execute("SELECT * FROM tasks")])
        with self.assertRaises(ValueError):
            doover.apply(self.con, aid)
        self.assertEqual(loop.run_cycle(self.con)["doovers_applied"], [])

    def test_approved_doover_is_applied_by_the_cycle_and_undone_as_a_batch(self):
        aid = doover.propose(self.con, self.items)
        self.approve(aid)
        self.assertEqual(loop.run_cycle(self.con)["doovers_applied"], [aid])
        self.assertEqual(loop.run_cycle(self.con)["doovers_applied"], [])  # applied once
        n1, n2 = [r["id"] for r in self.con.execute("SELECT id FROM tasks WHERE title IN ('Four tabs', 'Colours') ORDER BY id")]
        self.assertEqual(self.task(self.pi)["status"], "done")
        self.assertEqual(self.task(self.think)["status"], "dropped")
        self.assertEqual(self.task(self.page)["status"], "dropped")
        self.assertIn(f"(merged: #{self.page} One-page dashboard)", self.task(n1)["description"])
        self.assertEqual(self.task(n2)["depends_on"], str(n1))
        radar = self.task(self.radar)
        self.assertEqual((radar["due"], radar["impact"], radar["depends_on"]), (None, 2, str(self.pi)))
        self.assertEqual(radar["status"], "todo")  # no longer blocked behind the dropped task
        self.assertIn("already applied", doover.apply(self.con, aid))

        self.assertIn("Undid", doover.undo(self.con, aid))
        self.assertEqual([self.task(t)["status"] for t in (self.pi, self.think, self.page, n1, n2)],
                         ["todo", "blocked", "todo", "dropped", "dropped"])
        radar = self.task(self.radar)
        self.assertEqual((radar["due"], radar["impact"], radar["depends_on"]),
                         ("2026-12-20", 3, f"{self.think},{self.pi}"))

    def test_invalid_items_are_rejected(self):
        bad = [
            [{"kind": "keep", "task": self.keep}],                                         # no reason
            [{"kind": "keep", "task": 999, "reason": "x"}],                                # unknown task
            [{"kind": "drop", "task": self.done, "reason": "x"}],                          # already done
            [{"kind": "keep", "task": self.keep, "reason": "x"}] * 2,                      # twice
            [{"kind": "change", "task": self.keep, "fields": {"status": "done"}, "reason": "x"}],
            [{"kind": "change", "task": self.keep, "fields": {"impact": 9}, "reason": "x"}],
            [{"kind": "merge", "task": self.keep, "into": "N9", "reason": "x"}],
            [{"kind": "new", "key": "N1", "fields": {"owner": "ai"}, "reason": "x"}],      # no title
            [{"kind": "new", "key": "N1", "fields": {"title": "t"}, "after": [999], "reason": "x"}],
            [{"kind": "wipe", "task": self.keep, "reason": "x"}],
        ]
        for items in bad:
            with self.subTest(items=items), self.assertRaises(ValueError):
                doover.propose(self.con, items)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_plan_changed_after_proposal_is_not_applied(self):
        aid = doover.propose(self.con, self.items)
        db.update(self.con, "tasks", self.think, status="done"); self.approve(aid)
        self.assertEqual(doover.apply_approved(self.con), [])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE title = 'Four tabs'").fetchone()[0], 0)

    def test_committed_proposal_file_is_well_formed(self):
        data = json.loads((ROOT / "docs" / "reviews" / "2026-10-12-doover.json").read_text())
        kinds = {it["kind"] for it in data["items"]}
        self.assertLessEqual(kinds, set(doover.KINDS))
        self.assertTrue(all(it.get("reason") for it in data["items"]))
        tasks = [it["task"] for it in data["items"] if "task" in it]
        self.assertEqual(len(tasks), len(set(tasks)))


if __name__ == "__main__":
    unittest.main()


class ReplanTest(unittest.TestCase):
    """The Plan tab's "Re-evaluate tasks": brain re-evaluation, then a do-over proposal only when the plan drifted."""
    def setUp(self):
        from unittest import mock
        self.mock = mock
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.a = db.insert(self.con, "tasks", title="Four tabs", owner="ai")
        self.b = db.insert(self.con, "tasks", title="Old dashboard page")
        db.set_state(self.con, doover.STRUCTURE_KEY, doover.structure_signature())
        self.con.commit()

    def model(self, doover_reply, reeval=None):
        replies = {"reevaluate": json.dumps(reeval or {"changes": [], "focus": "fine"}), "doover": json.dumps(doover_reply)}
        self.jobs = []
        return self.mock.patch.object(doover.models, "complete",
                                      side_effect=lambda job, s, u: self.jobs.append(job) or replies[job])

    def drift_it(self):
        for n in range(doover.OVERDUE_MAX):
            db.insert(self.con, "tasks", title=f"late {n}", due="2020-01-01")
        self.con.commit()

    def test_no_drift_no_doover(self):
        with self.model({}):
            out = doover.replan(self.con)
        self.assertEqual(out["drift"], []); self.assertIsNone(out["approval"])
        self.assertEqual(self.jobs, ["reevaluate"])
        self.assertIn("not drifted", doover.replan_text(out))

    def test_structure_change_rechecks_tasks_and_ideas(self):
        """RA-057: after a new structure, the whole plan and the open ideas are judged again; ideas can be tidied."""
        old = db.insert(self.con, "ideas", title="Separate HQ dashboard theme")
        keep = db.insert(self.con, "ideas", title="Weekly reflection prompts")
        db.set_state(self.con, doover.STRUCTURE_KEY, "before-the-redesign"); self.con.commit()
        reply = {"summary": "The old dashboard is retired.", "items": [
            {"kind": "drop", "task": self.b, "reason": "the old dashboard is retired"},
            {"kind": "idea_drop", "idea": old, "reason": "outdated: the HQ dashboard is retired"},
            {"kind": "idea_change", "idea": keep, "fields": {"title": "Weekly reflection in Focus"}, "reason": "fits the Focus page"}]}
        with self.model(reply):
            out = doover.replan(self.con)
        self.assertIn("structure", " ".join(out["drift"]))
        self.assertIsNotNone(out["approval"])
        self.assertFalse(doover.structure_changed(self.con))  # judged once against this structure
        self.assertIn("2 ideas tidied", self.con.execute("SELECT request FROM approvals WHERE id = ?", (out["approval"],)).fetchone()[0])
        ch = doover.changes(self.con, out["approval"])
        self.assertEqual(ch["groups"]["idea_drop"][0]["title"], "Idea: Separate HQ dashboard theme")
        self.con.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (out["approval"],))
        doover.apply(self.con, out["approval"])
        gate = lambda i: self.con.execute("SELECT gate, title FROM ideas WHERE id = ?", (i,)).fetchone()
        self.assertEqual(tuple(gate(old)), ("dropped", "Separate HQ dashboard theme"))
        self.assertEqual(tuple(gate(keep)), ("idea", "Weekly reflection in Focus"))
        doover.undo(self.con, out["approval"])
        self.assertEqual(tuple(gate(old)), ("idea", "Separate HQ dashboard theme"))
        self.assertEqual(tuple(gate(keep)), ("idea", "Weekly reflection prompts"))

    def test_idea_items_are_checked(self):
        with self.assertRaises(ValueError):
            doover.validate(self.con, [{"kind": "idea_drop", "idea": 999, "reason": "x"}])
        i = db.insert(self.con, "ideas", title="An idea")
        with self.assertRaises(ValueError):
            doover.validate(self.con, [{"kind": "idea_change", "idea": i, "fields": {"gate": "dropped"}, "reason": "x"}])

    def test_drift_reasons(self):
        self.drift_it()
        dropped = db.insert(self.con, "tasks", title="gone", status="dropped")
        db.update(self.con, "tasks", self.a, depends_on=str(dropped))
        old = "2020-01-01T00:00:00"
        for n in range(doover.STALE_MAX):
            db.insert(self.con, "tasks", title=f"stale {n}", created=old, updated=old)
        reasons = " | ".join(doover.drift(self.con, {"applied": doover.CHANGES_MAX}))
        for part in ("overdue", "untouched", f"#{self.a}", "changed 6"):
            self.assertIn(part, reasons)

    def test_drift_drafts_a_pending_doover_and_changes_no_task(self):
        self.drift_it()
        before = [tuple(r) for r in self.con.execute("SELECT id, title, status, owner, impact, due, depends_on FROM tasks")]
        reply = {"summary": "Merge the old page.", "items": [
            {"kind": "keep", "task": self.a, "reason": "P3"},
            {"kind": "merge", "task": str(self.b), "into": self.a, "reason": "four tabs replace it"},
            {"kind": "drop", "task": 999, "reason": "does not exist"},       # invalid: skipped, not fatal
            {"kind": "new", "key": "N1", "fields": {"title": "Colours"}, "reason": "P3"}]}
        with self.model(reply):
            out = doover.replan(self.con)
        aid = out["approval"]
        self.assertEqual(self.jobs, ["reevaluate", "doover"]); self.assertEqual(out["dropped"], 1)
        row = self.con.execute("SELECT * FROM approvals WHERE id = ?", (aid,)).fetchone()
        self.assertEqual(row["status"], "pending"); self.assertEqual(row["reason"], "Merge the old page.")  # the short summary only; why it drifted is in the log
        self.assertEqual(before, [tuple(r) for r in self.con.execute("SELECT id, title, status, owner, impact, due, depends_on FROM tasks")])  # only scores are recomputed
        self.assertIn("waits for your approval", doover.replan_text(out))
        # a second press does not stack a second proposal
        with self.model(reply):
            again = doover.replan(self.con)
        self.assertEqual(again["approval"], aid); self.assertEqual(self.jobs, ["reevaluate"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 1)

    def test_model_off_or_nothing_to_change(self):
        self.drift_it()
        with self.model({"items": [{"kind": "keep", "task": self.a, "reason": "ok"}]}):
            self.assertIsNone(doover.replan(self.con)["approval"])
        with self.mock.patch.object(doover.models, "complete", side_effect=doover.models.ModelUnavailable("off")):
            out = doover.replan(self.con)
        self.assertIsNone(out["approval"]); self.assertIn("no model reachable", doover.replan_text(out))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_dashboard_op_and_diff_in_approvals(self):
        from engine import dashboard
        self.drift_it()
        reply = {"items": [{"kind": "drop", "task": self.b, "reason": "superseded"}]}
        with self.model(reply):
            msg = dashboard.act(self.con, "replan")
        self.assertIn("waits for your approval", msg)
        data = dashboard.build_data(self.con)
        detail = [a["detail"] for a in data["approvals"]]
        self.assertTrue(any("DROP (1)" in d for d in detail))


class GoalsTopDown(unittest.TestCase):
    """The do-over reshapes goals too (RA-064): place under a vision, drop, merge, change; visions stay Djeddi's."""
    def setUp(self):
        import sqlite3
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        ins = lambda t, **k: db.insert(self.con, t, **k)
        self.vision = ins("goals", title="Advance the intelligence of Atlas", horizon="life", status="active")
        self.old = ins("goals", title="HQ dashboard polish", horizon="quarter", status="active")
        self.loose = ins("goals", title="Self-steering builds", horizon="quarter", status="active")
        self.dup = ins("goals", title="Builds steer themselves", horizon="month", status="active")
        self.t1 = ins("tasks", title="Restyle the HQ dashboard tabs", goal_id=self.old, status="todo")
        self.t2 = ins("tasks", title="Pick the next node", goal_id=self.dup, status="todo")
        self.con.commit()

    def test_reshape_goals_and_undo(self):
        items = [{"kind": "goal_drop", "goal": self.old, "reason": "the old dashboard is retired"},
                 {"kind": "drop", "task": self.t1, "reason": "serves a dropped goal"},
                 {"kind": "goal_place", "goal": self.loose, "under": self.vision, "reason": "serves the vision"},
                 {"kind": "goal_merge", "goal": self.dup, "into": self.loose, "reason": "same aim"},
                 {"kind": "goal_change", "goal": self.loose, "fields": {"why": "fewer coordination steps"}, "reason": "x"}]
        with self.assertRaises(ValueError):  # each goal at most once
            doover.validate(self.con, items)
        items = items[:4]
        with self.assertRaises(ValueError):  # a vision is never touched
            doover.validate(self.con, [{"kind": "goal_drop", "goal": self.vision, "reason": "x"}])
        aid = doover.propose(self.con, items, "Old HQ work goes; builds that steer themselves serve the vision.")
        self.assertIn("goals reshaped", self.con.execute("SELECT request FROM approvals WHERE id = ?", (aid,)).fetchone()[0])
        self.con.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (aid,)); self.con.commit()
        doover.apply(self.con, aid)
        g = lambda i: tuple(self.con.execute("SELECT status, parent_id FROM goals WHERE id = ?", (i,)).fetchone())
        self.assertEqual((g(self.old), g(self.loose), g(self.dup)), (("dropped", None), ("active", self.vision), ("dropped", None)))
        self.assertEqual(self.con.execute("SELECT goal_id FROM tasks WHERE id = ?", (self.t2,)).fetchone()[0], self.loose)
        doover.undo(self.con, aid)
        self.assertEqual((g(self.old), g(self.loose), g(self.dup)), (("active", None), ("active", None), ("active", None)))
