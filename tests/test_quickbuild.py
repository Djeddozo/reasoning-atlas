import sqlite3
import unittest
from unittest.mock import patch
from engine import db, quickbuild

class QuickBuildTests(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(':memory:');self.con.row_factory=sqlite3.Row;db.init(self.con)
        self.addCleanup(self.con.close)
        self.auth=patch.object(quickbuild.claudeauth,'check',return_value={'state':'connected'});self.auth.start();self.addCleanup(self.auth.stop)
        self.flow=patch.object(quickbuild.flow,'run');self.flow.start();self.addCleanup(self.flow.stop)
    def test_starts_exact_task_once_on_retry(self):
        with patch.object(quickbuild.devloop,'why_not_now',return_value=''),patch.object(quickbuild.devloop,'start_background',return_value='Started') as launch:
            a=quickbuild.start(self.con,'Build a calendar\nWith week view','request-1234567890')
            b=quickbuild.start(self.con,'Build a calendar\nWith week view','request-1234567890')
        self.assertEqual(a,b);self.assertTrue(a['started']);launch.assert_called_once_with(task_id=a['task_id'])
        row=self.con.execute('SELECT * FROM tasks').fetchone()
        self.assertEqual(row['description'],'Build a calendar\nWith week view')
        self.assertEqual(row['owner'],'ai')
    def test_busy_preserves_task_and_reports_blocker(self):
        with patch.object(quickbuild.devloop,'why_not_now',return_value='A run is already in progress.'),patch.object(quickbuild.devloop,'start_background') as launch:
            a=quickbuild.start(self.con,'Build calendar','request-1234567890')
        self.assertFalse(a['started']);self.assertIn('already in progress',a['message']);launch.assert_not_called()
    def test_disconnected_does_not_lose_prompt_in_new_task(self):
        with patch.object(quickbuild.claudeauth,'check',return_value={'state':'disconnected'}):
            with self.assertRaisesRegex(ValueError,'Reconnect Claude'):
                quickbuild.start(self.con,'Build calendar','request-1234567890')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
    def test_empty_request(self):
        with self.assertRaises(ValueError):quickbuild.start(self.con,' ','request-1234567890')
