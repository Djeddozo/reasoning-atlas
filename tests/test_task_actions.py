"""Task actions: Djeddi can Run or Delete a task; only the agent marks it done (CLI, dashboard, Telegram, edit form)."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import sqlite3
import unittest
from unittest import mock

from engine import brain, dashboard, db, devloop, edit, flow, priority


class TaskActionsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.tid = db.insert(self.con, "tasks", title="Write the exporter", owner="me")
        self.con.commit()

    def row(self):
        return self.con.execute("SELECT status, owner, queued_by FROM tasks WHERE id = ?", (self.tid,)).fetchone()

    def test_djeddi_cannot_mark_done(self):
        db.update(self.con, "tasks", self.tid, owner="ai")  # only the agent's tasks; his own can be confirmed
        with self.assertRaisesRegex(PermissionError, "Run"):
            flow.complete(self.con, self.tid, "me")
        self.assertEqual(self.row()["status"], "todo")

    def test_agent_marks_done(self):
        self.assertIn("Done: #", flow.complete(self.con, self.tid, "agent"))
        self.assertEqual(self.row()["status"], "done")
        with self.assertRaisesRegex(ValueError, "already done"):
            flow.complete(self.con, self.tid, "agent")

    def test_actor_comes_from_hq_actor(self):
        with mock.patch.dict(_os.environ, {"HQ_ACTOR": "agent"}):
            self.assertEqual(flow.actor(), "agent")
        with mock.patch.dict(_os.environ, {"HQ_ACTOR": ""}):
            self.assertEqual(flow.actor(), "me")

    def test_run_hands_the_task_to_the_agent_first(self):
        other = db.insert(self.con, "tasks", title="Earlier queued", owner="ai"); self.con.commit()
        from engine import skilltree
        skilltree.queue(self.con, other)
        flow.run(self.con, self.tid)
        r = self.row()
        self.assertEqual((r["status"], r["owner"], r["queued_by"]), ("todo", "ai", "me"))
        first = self.con.execute("SELECT id FROM tasks WHERE queued_by = 'me' ORDER BY queued_at LIMIT 1").fetchone()[0]
        self.assertEqual(first, self.tid)

    def test_run_accepts_a_proposed_task(self):
        db.update(self.con, "tasks", self.tid, status="proposed"); self.con.commit()
        flow.run(self.con, self.tid)
        self.assertEqual(self.row()["status"], "todo")

    def test_delete_is_undoable(self):
        flow.run(self.con, self.tid)
        flow.discard(self.con, self.tid)
        self.assertEqual((self.row()["status"], self.row()["queued_by"]), ("dropped", ""))
        change = self.con.execute("SELECT id FROM brain_changes WHERE field = 'status' ORDER BY id DESC").fetchone()[0]
        brain.undo(self.con, change)
        self.assertEqual(self.row()["status"], "todo")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            flow.discard(self.con, 9999)

    def test_dashboard_refuses_done_and_offers_run_and_delete(self):
        db.update(self.con, "tasks", self.tid, owner="ai")
        with self.assertRaises(PermissionError):
            dashboard.act(self.con, "done", self.tid)
        with mock.patch.object(devloop, "cfg", return_value={"enabled": False}), \
                mock.patch.object(devloop, "start_background") as start:
            self.assertIn("handed to the agent", dashboard.act(self.con, "run", self.tid))
            start.assert_not_called()
        self.assertEqual(self.row()["owner"], "ai")
        self.assertIn("deleted", dashboard.act(self.con, "delete", self.tid))
        self.assertEqual(self.row()["status"], "dropped")

    def test_dashboard_run_starts_the_loop_when_on_and_idle(self):
        with mock.patch.object(devloop, "cfg", return_value={"enabled": True}), \
                mock.patch.object(devloop, "why_not_now", return_value=""), \
                mock.patch.object(devloop, "start_background", return_value="Started.") as start:
            self.assertIn("Started.", dashboard.act(self.con, "run", self.tid))
            start.assert_called_once_with(task_id=self.tid)

    def test_edit_form_cannot_set_done(self):
        with self.assertRaisesRegex(ValueError, "status must be one of"):
            edit.edit(self.con, "tasks", self.tid, {"status": "done"})
        self.assertEqual(self.row()["status"], "todo")


class TelegramTaskActionsTest(unittest.TestCase):
    def setUp(self):
        from engine.interfaces import telegram
        from tests.test_telegram import CHAT, FakeTelegram, msg
        self.msg = msg
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.fake = FakeTelegram()
        p = mock.patch.object(telegram, "api", self.fake); p.start(); self.addCleanup(p.stop)
        self.bot = telegram.Bot(self.con, "TOKEN", CHAT, {"poll_seconds": 0})

    def test_done_is_refused_run_and_delete_work(self):
        tid = db.insert(self.con, "tasks", title="Book driving lesson"); self.con.commit()
        self.fake.updates = [self.msg(1, f"/done {tid}"), self.msg(2, f"/run #{tid}"), self.msg(3, f"/delete {tid}"),
                             self.msg(4, "/run 9999"), self.msg(5, "/delete")]
        self.bot.poll_once()
        texts = [p["text"] for p in self.fake.sent()]
        self.assertIn("marked done by the agent", texts[0])
        self.assertIn("handed to the agent", texts[1])
        self.assertIn("deleted", texts[2])
        self.assertIn("does not exist", texts[3])
        self.assertIn("Usage", texts[4])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0], "dropped")


if __name__ == "__main__":
    unittest.main()


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.ids = [db.insert(self.con, "tasks", title=f"T{i}", owner="me", impact=5 - i) for i in range(3)]
        self.con.commit()
        priority.rescore(self.con)

    def order(self):
        return [r["id"] for r in priority.next_tasks(self.con, 10)]

    def test_move_down_and_up_survive_rescore(self):
        a, b, c = self.ids
        self.assertEqual(self.order(), [a, b, c])
        edit.move(self.con, a, "down")
        priority.rescore(self.con)
        self.assertEqual(self.order(), [b, a, c])
        edit.move(self.con, c, "up")
        priority.rescore(self.con)
        self.assertEqual(self.order(), [b, c, a])

    def test_edges_and_auto(self):
        a, b, c = self.ids
        self.assertIn("already first", edit.move(self.con, a, "up"))
        edit.move(self.con, a, "down")
        edit.move(self.con, a, "auto"); edit.move(self.con, b, "auto")
        priority.rescore(self.con)
        self.assertEqual(self.order(), [a, b, c])
        with self.assertRaises(ValueError):
            edit.move(self.con, a, "sideways")

    def test_own_queue_swaps_queued_order(self):
        a, b, c = self.ids
        for i in (a, b):
            db.update(self.con, "tasks", i, owner="ai")
            flow.run(self.con, i)  # b ends up first in his queue
        edit.move(self.con, b, "down")
        first = self.con.execute("SELECT id FROM tasks WHERE queued_by = 'me' ORDER BY queued_at").fetchone()[0]
        self.assertEqual(first, a)
