"""Reconnect the existing Claude subscription through the official CLI.

Login URLs/codes stay in memory; Claude alone manages its credential file.
"""
import json
import os
import re
import subprocess
import threading
import time
import secrets
from pathlib import Path
from urllib.parse import urlparse

from . import devloop


def environment():
    env = devloop.agent_env(Path(os.devnull))
    env['BROWSER'] = '/bin/true'
    return env


def check():
    exe = devloop.claude_path()
    if not exe:
        return {'state': 'unavailable', 'message': 'Claude Code is not installed.'}
    try:
        result = subprocess.run([exe, 'auth', 'status'], env=environment(),
                                capture_output=True, text=True, timeout=15)
        info = json.loads(result.stdout)
        connected = bool(info.get('loggedIn'))
        return {'state': 'connected' if connected else 'disconnected',
                'message': 'Claude is connected.' if connected else 'Claude needs you to sign in again.'}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'state': 'error', 'message': 'Could not check Claude. Try again.'}


class Login:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.session = ''
        self.url = ''
        self.state = 'unchecked'
        self.message = 'Check the Claude connection or reconnect below.'
        self.deadline = 0

    def status(self):
        with self.lock:
            return dict(state=self.state, message=self.message, url=self.url, session=self.session)

    def refresh(self):
        with self.lock:
            if self.process is not None:
                return self.status()
            result = check()
            self.state, self.message = result['state'], result['message']
            return self.status()

    def start(self):
        with self.lock:
            if self.process is not None:
                return self.status()
            exe = devloop.claude_path()
            if not exe:
                raise ValueError('Claude Code is not installed.')
            try:
                proc = subprocess.Popen([exe, 'auth', 'login', '--claudeai'], env=environment(),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
            except OSError:
                raise ValueError('Could not start Claude sign-in.') from None
            self.process = proc
            self.session = secrets.token_urlsafe(24)
            self.url = ''
            self.state = 'starting'
            self.message = 'Preparing the Claude sign-in link…'
            self.deadline = time.monotonic() + 600
            threading.Thread(target=self._read, args=(proc,), daemon=True).start()
            timer = threading.Timer(600, self._expire, args=(proc,))
            timer.daemon = True
            timer.start()
            return self.status()

    def _expire(self, proc):
        with self.lock:
            if self.process is proc:
                self._stop('Sign-in timed out. Reconnect to get a new link.')

    def _stop(self, message):
        proc, self.process = self.process, None
        self.url = self.session = ''
        self.state, self.message = 'disconnected', message
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            if proc.stdin:
                proc.stdin.close()

    def cancel(self):
        with self.lock:
            self._stop('Sign-in cancelled. You can reconnect any time.')
            return self.status()

    def _read(self, proc):
        try:
            for line in proc.stdout:
                # Never expose CLI output: it may include credentials or a pasted code.
                for url in re.findall(r'https://[^\s\x1b]+', line):
                    parsed = urlparse(url)
                    if (parsed.hostname, parsed.path) in {('claude.ai', '/oauth/authorize'), ('claude.com', '/cai/oauth/authorize'), ('platform.claude.com', '/oauth/authorize'), ('console.anthropic.com', '/oauth/authorize')}:
                        with self.lock:
                            if self.process is proc:
                                self.url = url
                                self.state, self.message = 'waiting', 'Open Claude sign-in, then paste the returned code here if asked.'
            rc = proc.wait()
            with self.lock:
                if self.process is proc:
                    self.process = None
                    self.url = self.session = ''
                    result = check() if rc == 0 else {'state': 'error', 'message': 'Sign-in failed. Reconnect and try again.'}
                    self.state, self.message = result['state'], result['message']
        finally:
            proc.stdout.close()
            if proc.stdin:
                proc.stdin.close()

    def submit(self, session, code):
        code = code.strip()
        if not re.fullmatch(r'[A-Za-z0-9_\-#.=+/%:]{1,4096}', code):
            raise ValueError('Paste only the sign-in code from Claude.')
        with self.lock:
            if not self.process or self.state != 'waiting' or not secrets.compare_digest(self.session, session):
                raise ValueError('This sign-in has ended. Reconnect to get a new link.')
            if time.monotonic() >= self.deadline:
                self._stop('Sign-in timed out. Reconnect to get a new link.')
                raise ValueError(self.message)
            try:
                self.process.stdin.write(code + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                self._stop('Sign-in ended. Reconnect and try again.')
                raise ValueError(self.message) from None
            self.state, self.message = 'verifying', 'Verifying your sign-in…'
            return self.status()


login = Login()
