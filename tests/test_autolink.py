"""Automatic node linking: Claude links unlinked open tasks to skill tree nodes; validated, undoable, never overrides."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import contextlib
import io
import json
import sqlite3
import unittest
from unittest import mock

from engine import autolink, brain, cli, db, direction, edit, loop, models, skilltree


class AutolinkTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con); loop.seed(self.con); skilltree.seed(self.con)
        # start from a clean queue: only the tasks each test adds
        self.con.execute("UPDATE tasks SET status = 'done'"); self.con.commit()
        self.cap = {r["title"]: r["id"] for r in self.con.execute("SELECT id, title FROM capabilities")}
        self.calls = []

    def task(self, title, cap=None, status="todo"):
        tid = db.insert(self.con, "tasks", title=title, owner="ai", status=status, capability_id=self.cap.get(cap))
        self.con.commit()
        return tid

    def cap_of(self, tid):
        return self.con.execute("SELECT capability_id FROM tasks WHERE id = ?", (tid,)).fetchone()[0]

    def reply(self, links):
        def fake(job, system, user):
            self.calls.append((job, json.loads(user)))
            return "Here you go:\n" + json.dumps({"links": links})
        return mock.patch.object(models, "complete", side_effect=fake)

    def test_links_valid_suggestions_and_skips_bad_ones(self):
        pdf = self.task("Read scanned PDFs with OCR")
        card = self.task("Virtual card with a monthly cap")
        vague = self.task("Tidy things up")
        done = self.task("Old PDF work", status="done")
        linked = self.task("Already placed", "Radar")
        links = [{"task_id": pdf, "capability_id": self.cap["Reading PDFs & scans"], "reason": "PDF reading"},
                 {"task_id": pdf, "capability_id": self.cap["Radar"], "reason": "second link for one task"},
                 {"task_id": card, "capability_id": self.cap["Budget & spending"], "reason": "spending limits"},
                 {"task_id": vague, "capability_id": None, "reason": "no node fits"},
                 {"task_id": done, "capability_id": self.cap["Radar"]},       # not a candidate
                 {"task_id": linked, "capability_id": self.cap["Memory"]},    # already linked
                 {"task_id": 99999, "capability_id": self.cap["Memory"]},     # unknown task
                 {"task_id": card + 1000, "capability_id": 424242}, "junk"]
        with self.reply(links):
            r = autolink.run(self.con)
        self.assertEqual(self.calls[0][0], "link")
        sent = {t["id"] for t in self.calls[0][1]["tasks"]}
        self.assertEqual(sent, {pdf, card, vague})                     # only open, unlinked tasks go to Claude
        self.assertIn("Reading PDFs & scans", {n["title"] for n in self.calls[0][1]["nodes"]})
        self.assertEqual(r["applied"], 2); self.assertEqual(r["unlinked"], [vague])
        self.assertEqual(self.cap_of(pdf), self.cap["Reading PDFs & scans"])
        self.assertEqual(self.cap_of(card), self.cap["Budget & spending"])
        self.assertIsNone(self.cap_of(vague))
        self.assertEqual(self.cap_of(linked), self.cap["Radar"])
        ch = [c for c in brain.changes(self.con, 10) if c["field"] == "capability_id"]
        self.assertEqual(len(ch), 2)
        self.assertTrue(all(c["batch"] == r["batch"] and c["reason"].startswith("auto-link:") for c in ch))
        self.assertIn("Linked 2 task(s)", autolink.text(r))

    def test_dry_run_writes_nothing(self):
        tid = self.task("Read scanned PDFs")
        with self.reply([{"task_id": tid, "capability_id": self.cap["Reading PDFs & scans"], "reason": "pdf"}]):
            r = autolink.run(self.con, dry=True)
        self.assertEqual((r["applied"], len(r["links"])), (0, 1))
        self.assertIsNone(self.cap_of(tid))
        self.assertIn("Would link 1", autolink.text(r))

    def test_undo_restores_and_the_task_is_never_relinked(self):
        a, b = self.task("Read scanned PDFs"), self.task("Virtual card")
        links = [{"task_id": a, "capability_id": self.cap["Reading PDFs & scans"]},
                 {"task_id": b, "capability_id": self.cap["Budget & spending"]}]
        with self.reply(links):
            autolink.run(self.con)
        edit.edit(self.con, "tasks", b, {"capability_id": self.cap["Radar"]})   # Djeddi moves one himself
        msg = autolink.undo_last(self.con)
        self.assertIn("Undid 1", msg); self.assertIn("1 kept", msg)
        self.assertIsNone(self.cap_of(a)); self.assertEqual(self.cap_of(b), self.cap["Radar"])
        self.assertEqual(autolink.candidates(self.con), [])                     # an undone link is not redone
        self.assertIn("Nothing left to undo", autolink.undo_last(self.con))
        self.calls.clear()
        with self.reply(links):
            r = autolink.run(self.con)
        self.assertEqual(self.calls, [])                                        # nothing to ask Claude about
        self.assertIn("already builds a node", autolink.text(r))

    def test_link_set_while_claude_thinks_is_not_overridden(self):
        tid = self.task("Read scanned PDFs")
        def fake(job, system, user):  # Djeddi links the task himself while the model call runs
            edit.edit(self.con, "tasks", tid, {"capability_id": self.cap["Radar"]})
            return json.dumps({"links": [{"task_id": tid, "capability_id": self.cap["Reading PDFs & scans"]}]})
        with mock.patch.object(models, "complete", side_effect=fake):
            r = autolink.run(self.con)
        self.assertEqual(r["applied"], 0); self.assertEqual(self.cap_of(tid), self.cap["Radar"])
        msg = autolink.text(r)
        self.assertIn("Linked 0 task(s)", msg); self.assertIn("1 skipped", msg); self.assertNotIn("Undo all", msg)

    def test_json_booleans_are_not_ids(self):
        tid = self.task("Read scanned PDFs")
        with self.reply([{"task_id": tid, "capability_id": True}]):
            r = autolink.run(self.con)
        self.assertEqual((r["applied"], r["unlinked"]), (0, [tid])); self.assertIsNone(self.cap_of(tid))

    def test_undo_targets_only_the_latest_run(self):
        old, new = self.task("Old task"), self.task("New task")
        with self.reply([{"task_id": old, "capability_id": self.cap["Radar"]}]):
            autolink.run(self.con, limit=1)  # highest-ranked first: only 'old' (same score, lower id)
        with self.reply([{"task_id": new, "capability_id": self.cap["Memory"]}]):
            autolink.run(self.con)
        edit.edit(self.con, "tasks", new, {"capability_id": self.cap["Arsenal"]})  # the latest run is fully changed
        self.assertIn("Nothing left to undo", autolink.undo_last(self.con))
        self.assertEqual(self.cap_of(old), self.cap["Radar"])                   # the older run is untouched
        self.assertEqual(self.cap_of(new), self.cap["Arsenal"])

    def test_real_run_without_links_does_not_read_as_a_preview(self):
        tid = self.task("Tidy things up")
        with self.reply([{"task_id": tid, "capability_id": None, "reason": "no node fits"}]):
            r = autolink.run(self.con)
        self.assertEqual(autolink.text(r).splitlines()[0], "Linked 0 task(s); 1 fit no node.")

    def test_no_nodes_means_no_model_call(self):
        self.task("Read scanned PDFs")
        with mock.patch.object(skilltree, "nodes", return_value={}), self.reply([]):
            r = autolink.run(self.con)
        self.assertEqual(self.calls, []); self.assertEqual(r["applied"], 0)
        self.assertIn("no nodes yet", autolink.text(r))           # not "fit no node": Claude was never asked
        self.assertNotIn("fit no node", autolink.text(r))

    def test_hand_unlinked_task_is_left_alone(self):
        tid = self.task("Something", "Radar")
        edit.edit(self.con, "tasks", tid, {"capability_id": None})
        self.assertEqual(autolink.candidates(self.con), [])

    def test_no_model_changes_nothing(self):
        tid = self.task("Read scanned PDFs")
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("cloud: URLError")):
            r = autolink.run(self.con)
        self.assertEqual((r["applied"], r["unlinked"]), (0, [tid]))
        self.assertIn("no model reachable", autolink.text(r))
        self.assertIsNone(self.cap_of(tid))

    def test_limit_and_drafts_last(self):
        draft = self.task("A draft", status="proposed")
        ids = [self.task(f"Task {i}") for i in range(3)]
        got = [t["id"] for t in autolink.candidates(self.con, limit=3)]
        self.assertEqual(len(got), 3); self.assertNotIn(draft, got); self.assertEqual(set(got), set(ids))

    def test_direction_steer_points_to_autolink(self):
        self.task("Unlinked work")
        steer = direction.rollup(self.con)["insights"]["steer"]
        self.assertTrue(any("hq tree autolink" in s for s in steer))

    def test_cli_autolink_and_undo(self):
        tid = self.task("Read scanned PDFs")
        out = io.StringIO()
        with mock.patch.object(cli, "_con", return_value=self.con), contextlib.redirect_stdout(out), \
                self.reply([{"task_id": tid, "capability_id": self.cap["Reading PDFs & scans"], "reason": "pdf"}]):
            cli.main(["tree", "autolink"])
            cli.main(["tree", "autolink", "--undo"])
        self.assertIn("Linked 1 task(s)", out.getvalue()); self.assertIn("Undid 1", out.getvalue())
        self.assertIsNone(self.cap_of(tid))

    def test_link_job_is_routed_in_the_constitution(self):
        self.assertIn("link", loop.load_constitution()["models"]["routing"])


if __name__ == "__main__":
    unittest.main()
