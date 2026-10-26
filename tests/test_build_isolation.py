"""Unmerged branch commands must not migrate or overwrite the running plan."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import db, devloop, usage


class BuildIsolationTest(unittest.TestCase):
    def test_child_migrations_and_task_changes_stay_in_snapshot_including_repair(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            con = db.connect(root / 'live.db')
            self.addCleanup(con.close)
            db.init(con)
            task = db.insert(con, 'tasks', title='Live task', owner='ai')
            con.commit()
            child = '''import os, sqlite3, json
c = sqlite3.connect(os.environ['HQ_DB'])
assert c.execute('SELECT title FROM tasks WHERE id=?', (TASK,)).fetchone()[0] == EXPECTED
c.execute("UPDATE tasks SET title='Branch edit' WHERE id=?", (TASK,))
c.execute('CREATE TABLE IF NOT EXISTS unmerged_schema (id INTEGER)')
c.commit()
print(json.dumps({'type':'result','result':'done','is_error':False}))
'''
            with mock.patch.object(devloop, 'WORK', root / 'runs'), mock.patch.object(usage, 'killed', return_value=False):
                for expected in ('Live task', 'Branch edit'):
                    code = child.replace('TASK', str(task)).replace('EXPECTED', repr(expected))
                    summary, failed, _ = devloop.run_agent(con, [sys.executable, '-c', code], root, 123, 1)
                    self.assertFalse(failed, summary)
                    self.assertEqual(con.execute('SELECT title FROM tasks WHERE id=?', (task,)).fetchone()[0], 'Live task')
                    self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='unmerged_schema'").fetchone())
                snapshot = root / 'runs/run-123.db'
                self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
                self.assertNotEqual(snapshot, root / 'live.db')

    def test_snapshot_includes_committed_wal_data(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            con = db.connect(root / 'live.db')
            self.addCleanup(con.close)
            con.execute('PRAGMA journal_mode=WAL')
            db.init(con)
            db.insert(con, 'tasks', title='Fresh WAL task')
            con.commit()
            with mock.patch.object(devloop, 'WORK', root / 'runs'):
                copy = sqlite3.connect(devloop.agent_database(con, 1))
                try:
                    self.assertEqual(copy.execute('SELECT title FROM tasks').fetchone()[0], 'Fresh WAL task')
                finally:
                    copy.close()
