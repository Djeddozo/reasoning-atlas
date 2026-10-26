import argparse
import contextlib
import io
import sqlite3
import unittest
from unittest import mock

from engine import brain, cli, db


class DirectAddsUndoable(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        p = mock.patch.object(cli, "_con", lambda: self.con)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(cli.loop, "run_cycle", lambda con: None)
        p.start(); self.addCleanup(p.stop)

    def run_quiet(self, f, ns):
        with contextlib.redirect_stdout(io.StringIO()):
            f(ns)

    def test_inbox_routes_are_logged_and_undoable(self):
        for text, table in (("todo: write", "tasks"), ("idea: try", "ideas"), ("goal: grow", "goals"), ("just info", "memories")):
            self.run_quiet(cli.cmd_inbox, argparse.Namespace(text=text.split()))
            c = self.con.execute("SELECT * FROM brain_changes WHERE table_name = ? AND action = 'create'", (table,)).fetchone()
            self.assertIsNotNone(c, table)
            brain.undo(self.con, c["id"])
        self.assertEqual(self.con.execute("SELECT status FROM tasks").fetchone()[0], "dropped")
        self.assertEqual(self.con.execute("SELECT gate FROM ideas").fetchone()[0], "dropped")
        self.assertEqual(self.con.execute("SELECT kind FROM memories").fetchone()[0], "forgotten")

    def test_add_task_is_logged(self):
        ns = argparse.Namespace(kind="task", text="Do it", impact=3, effort=1, due=None, owner="me", phase=None,
                                after=None, goal=None)
        self.run_quiet(cli.cmd_add, ns)
        c = self.con.execute("SELECT * FROM brain_changes WHERE table_name = 'tasks'").fetchone()
        self.assertEqual(c["action"], "create")
        brain.undo(self.con, c["id"])
        self.assertEqual(self.con.execute("SELECT status FROM tasks").fetchone()[0], "dropped")


if __name__ == "__main__":
    unittest.main()
