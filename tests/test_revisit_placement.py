"""RA-059: the revisit reviews where Atlas's functions live; proposals are drafts, nothing changes by itself."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import json
import sqlite3
import unittest

from engine import brain, db, revisit


class PlacementReview(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)

    def reply(self, items):
        return lambda job, system, user: (self.assertEqual(job, "revisit"), self.assertIn("engine.brain.write_thought", user),
                                          json.dumps({"summary": "One move.", "items": items}))[-1]

    def test_proposals_become_drafts_and_bad_ones_are_skipped(self):
        out = revisit.placement(self.con, self.reply([
            {"function": "engine.system.usage", "action": "move", "to": "Today", "reason": "the owner checks cost daily"},
            {"function": "engine.brain.write_thought", "action": "move", "to": "Nowhere", "reason": "x"},          # unknown section
            {"function": "engine.made.up", "action": "remove", "reason": "x"},                                    # unknown function
            {"function": "atlas.notes.save", "action": "merge", "with": "engine.brain.write_thought", "reason": "one way to write"}]))
        self.assertEqual((out["status"], len(out["drafts"]), out["skipped"]), ("done", 2, 2))
        rows = self.con.execute("SELECT title, status, owner FROM tasks ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Move engine.system.usage from Systems to Today", "proposed", "ai"),
                                                     ("Merge atlas.notes.save into engine.brain.write_thought", "proposed", "ai")])
        self.assertIn("2 proposal(s)", revisit.placement_text(out))
        self.assertFalse(revisit.placement_due(self.con))  # just reviewed, structure unchanged
        for c in self.con.execute("SELECT id FROM brain_changes ORDER BY id DESC").fetchall():  # undoable
            brain.undo(self.con, c[0])
        self.assertEqual({r[0] for r in self.con.execute("SELECT status FROM tasks")}, {"dropped"})

    def test_due_when_the_structure_changes(self):
        revisit.placement(self.con, self.reply([]))
        self.assertFalse(revisit.placement_due(self.con))
        m = revisit._placement_material()
        m["sections"] = m["sections"] + ["Somewhere new"]
        self.assertTrue(revisit.placement_due(self.con, m))

    def test_no_model_leaves_it_due(self):
        from engine import models
        def offline(*a):
            raise models.ModelUnavailable("offline")
        self.assertEqual(revisit.placement(self.con, offline)["status"], "error")
        self.assertTrue(revisit.placement_due(self.con))
