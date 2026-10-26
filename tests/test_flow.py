"""Where things go: task vs idea rule, idea → promoted → task, undo both ways, and the dump's per-item reply."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import sqlite3
import unittest
from unittest import mock

from engine import brain, db, dump, flow, models


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.idea = db.insert(self.con, "ideas", title="Flashcard app")
        self.con.commit()

    def idea_row(self):
        return self.con.execute("SELECT gate, task_id FROM ideas WHERE id = ?", (self.idea,)).fetchone()

    def task_status(self, tid):
        return self.con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0]

    def test_promote_links_the_task_and_leaves_the_open_list(self):
        tid = brain.promote_idea(self.con, self.idea)
        self.assertEqual(tuple(self.idea_row()), ("promoted", tid))
        self.assertEqual(flow.where(self.con, "ideas", self.idea)["place"], f"promoted → task #{tid}")
        with self.assertRaisesRegex(ValueError, f"promoted \\(task #{tid}\\)"):
            brain.promote_idea(self.con, self.idea)

    def test_undoing_the_task_reopens_the_idea(self):
        tid = brain.promote_idea(self.con, self.idea)
        create = self.con.execute("SELECT id FROM brain_changes WHERE table_name = 'tasks' AND row_id = ?", (tid,)).fetchone()[0]
        brain.undo(self.con, create)
        self.assertEqual((self.task_status(tid), tuple(self.idea_row())), ("dropped", ("idea", None)))
        self.assertNotEqual(brain.promote_idea(self.con, self.idea), tid)  # can be promoted again

    def test_undoing_the_promotion_drops_the_task(self):
        tid = brain.promote_idea(self.con, self.idea)
        gate = self.con.execute("SELECT id FROM brain_changes WHERE table_name = 'ideas' AND field = 'gate'").fetchone()[0]
        brain.undo(self.con, gate)
        self.assertEqual((self.task_status(tid), tuple(self.idea_row())), ("dropped", ("idea", None)))
        undone = self.con.execute("SELECT COUNT(*) FROM brain_changes WHERE undone = 0").fetchone()[0]
        self.assertEqual(undone, 0)  # both halves of the promotion are marked undone

    def test_old_test_gate_becomes_promoted_with_its_task(self):
        tid = db.insert(self.con, "tasks", title="Flashcard app")
        old = db.insert(self.con, "ideas", title="Old", gate="test")
        brain.record(self.con, "update", "ideas", old, f"became task #{tid}", "gate", "idea", "test")
        db.init(self.con)  # migrations run again on an existing database
        self.assertEqual(tuple(self.con.execute("SELECT gate, task_id FROM ideas WHERE id = ?", (old,)).fetchone()),
                         ("promoted", tid))

    def test_dump_reply_says_where_every_item_went(self):
        self.assertIn(flow.RULE, dump.SYSTEM)
        reply = {"tasks": [{"title": "Book exam"}], "ideas": [{"title": "Study group"}], "summary": "1 task, 1 idea"}
        with mock.patch.object(models, "complete", return_value=json.dumps(reply)):
            out = dump.process(self.con, "need to book the exam; maybe a study group")
        tid = self.con.execute("SELECT id FROM tasks WHERE title = 'Book exam'").fetchone()[0]
        iid = self.con.execute("SELECT id FROM ideas WHERE title = 'Study group'").fetchone()[0]
        self.assertEqual(out, f"1 task, 1 idea\n• task #{tid} Book exam – in Forge › Plan\n"
                              f"• idea #{iid} Study group – in Forge › Ideas")
        brain.promote_idea(self.con, iid, "ai")
        thought = brain.thoughts(self.con, 1)[0]["id"]
        self.assertIn("promoted → task #", flow.lines(flow.placed(self.con, thought)))

    def test_dump_without_model_lists_prefix_items(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("off")):
            out = dump.process(self.con, "todo: buy SSD; idea: NAS")
            self.assertIn("• task #", out); self.assertIn("NAS – in Forge › Ideas", out)
            self.assertNotIn("•", dump.process(self.con, "just a note"))  # nothing filed: plain message

    def test_map_names_the_commands(self):
        for cmd in ("hq promote", "hq ideas", "hq brain undo", "unsure"):
            self.assertIn(cmd, flow.MAP)


if __name__ == "__main__":
    unittest.main()


class ConfirmDoneTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.mine = db.insert(self.con, "tasks", title="Call the bank", owner="me")
        self.ai = db.insert(self.con, "tasks", title="Build X", owner="ai")
        self.con.commit()

    def status(self, tid):
        return self.con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0]

    def test_he_closes_his_own_task_and_can_undo(self):
        flow.confirm_done(self.con, self.mine, "went to the bank")
        self.assertEqual(self.status(self.mine), "done")
        cid = self.con.execute("SELECT id FROM brain_changes WHERE row_id = ? AND field = 'status'", (self.mine,)).fetchone()[0]
        brain.undo(self.con, cid)
        self.assertNotEqual(self.status(self.mine), "done")

    def test_agent_tasks_stay_agent_only(self):
        with self.assertRaises(PermissionError):
            flow.confirm_done(self.con, self.ai)
        with self.assertRaises(PermissionError):
            flow.complete(self.con, self.ai, "me")
        self.assertEqual(flow.complete(self.con, self.mine, "me")[:5], "Done:")

    def test_dump_confirms_only_his_tasks(self):
        out = {"done": [{"task_id": self.mine, "evidence": "I called the bank"}, {"task_id": self.ai}], "summary": "ok"}
        with mock.patch.object(models, "complete", return_value=json.dumps(out)):
            dump.process(self.con, "I called the bank")
        self.assertEqual((self.status(self.mine), self.status(self.ai)), ("done", "todo"))


class RerankAtSourceTest(unittest.TestCase):
    """cli/telegram no longer add a second run_cycle after done or dump: the rank is refreshed at the source."""
    def test_done_reranks_once_without_cycle(self):
        from engine import cli, priority
        con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        db.init(con)
        tid = db.insert(con, "tasks", title="x", owner="ai", status="todo"); con.commit()
        with mock.patch.object(cli, "_con", return_value=con), \
             mock.patch.object(cli.loop, "run_cycle") as cycle, \
             mock.patch.object(priority, "rerank") as rr:
            cli._task_action(flow.complete, tid, "agent", rerank=False)
        cycle.assert_not_called()
        rr.assert_called_once()
