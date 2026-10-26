import io
import json
import os
import sqlite3
import unittest
from unittest.mock import Mock, patch
from engine import claudeauth, dashboard, db


class ClaudeAuthTests(unittest.TestCase):
    def test_status_does_not_expose_account_details(self):
        result = Mock(stdout=json.dumps({'loggedIn': True, 'email': 'private@example.org'}))
        with patch.object(claudeauth.devloop, 'claude_path', return_value='/bin/claude'), patch.object(claudeauth.subprocess, 'run', return_value=result):
            self.assertEqual(claudeauth.check(), {'state':'connected', 'message':'Claude is connected.'})

    def test_login_urls_only_official_and_no_output_leak(self):
        login = claudeauth.Login()
        proc = Mock(stdout=io.StringIO('secret output\nhttps://evil.example/oauth/authorize?secret=1\nhttps://claude.com/cai/oauth/authorize?state=test\n'), stdin=io.StringIO())
        login.process = proc
        def finished():
            self.assertEqual(login.url, 'https://claude.com/cai/oauth/authorize?state=test')
            self.assertNotIn('secret', json.dumps(login.status()))
            return 0
        proc.wait.side_effect = finished
        with patch.object(claudeauth, 'check', return_value={'state':'connected', 'message':'Connected'}):
            login._read(proc)
        self.assertEqual(login.state, 'connected')
        self.assertEqual(login.url, '')

    def test_stale_and_multiline_codes_rejected(self):
        login = claudeauth.Login()
        for session, code in [('old', 'code'), ('', 'a\nb')]:
            with self.assertRaises(ValueError):
                login.submit(session, code)

    def test_submit_delivers_code_only_to_cli(self):
        login = claudeauth.Login()
        login.process = Mock(stdin=io.StringIO())
        login.state, login.session, login.deadline = 'waiting', 'session', float('inf')
        result = login.submit('session', 'code#state')
        self.assertEqual(login.process.stdin.getvalue(), 'code#state\n')
        self.assertNotIn('code#state', json.dumps(result))
        self.assertEqual(result['state'], 'verifying')

    def test_expired_login_stopped(self):
        login = claudeauth.Login()
        login.process = Mock()
        login.process.poll.return_value = None
        proc = login.process
        login.session, login.state = 'session', 'waiting'
        with self.assertRaises(ValueError):
            login.submit('session', 'code')
        proc.terminate.assert_called_once()
        self.assertIsNone(login.process)

    def test_dashboard_does_not_log_code(self):
        con = sqlite3.connect(':memory:'); con.row_factory = sqlite3.Row; db.init(con)
        with patch.dict(os.environ, {'HQ_TOKEN':'test'}), patch.object(claudeauth.login, 'submit', return_value={'state':'verifying'}):
            dashboard.act(con, 'claude_auth_submit', text=json.dumps({'session':'session', 'code':'secret-code'}))
        self.assertNotIn('secret-code', str([tuple(r) for r in con.execute('SELECT * FROM log')]))
        con.close()

    def test_recovery_requires_dashboard_token(self):
        with patch.dict(os.environ, {'HQ_TOKEN':''}):
            with self.assertRaises(ValueError):
                dashboard.act(None, 'claude_auth_start')
