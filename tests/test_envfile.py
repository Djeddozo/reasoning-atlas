"""Automatic dashboard token in .env."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import db, envfile, loop


class EnvFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / ".env"
        p = mock.patch.dict(os.environ, {}, clear=False); p.start(); self.addCleanup(p.stop)
        os.environ.pop("HQ_TOKEN", None); os.environ.pop("HQ_HOST", None)

    def test_empty_token_line_is_filled_and_rest_kept(self):
        self.env.write_text("HQ_TOKEN=            # dashboard access\nTELEGRAM_BOT_TOKEN=abc\n")
        token, created = envfile.ensure_token(self.env)
        self.assertTrue(created)
        self.assertGreaterEqual(len(token), 30)
        self.assertEqual(self.env.read_text(), f"HQ_TOKEN={token}\nTELEGRAM_BOT_TOKEN=abc\n")
        self.assertEqual(os.environ["HQ_TOKEN"], token)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    def test_missing_line_is_appended(self):
        self.env.write_text("TELEGRAM_CHAT_ID=42")  # no trailing newline, as after a hand-made echo
        token, created = envfile.ensure_token(self.env)
        self.assertTrue(created)
        self.assertEqual(self.env.read_text(), f"TELEGRAM_CHAT_ID=42\nHQ_TOKEN={token}\n")

    def test_missing_file_is_created(self):
        token, created = envfile.ensure_token(self.env)
        self.assertTrue(created)
        self.assertEqual(envfile.read_value("HQ_TOKEN", self.env), token)

    def test_existing_token_is_kept(self):
        self.env.write_text("HQ_TOKEN=keepme\n")
        self.assertEqual(envfile.ensure_token(self.env), ("keepme", False))
        self.assertEqual(self.env.read_text(), "HQ_TOKEN=keepme\n")

    def test_token_from_environment_wins(self):
        os.environ["HQ_TOKEN"] = "fromenv"
        self.assertEqual(envfile.ensure_token(self.env), ("fromenv", False))
        self.assertFalse(self.env.exists())  # nothing written

    def test_link_uses_the_dashboard_port(self):
        self.env.write_text("HQ_TOKEN=tok\n")
        self.assertEqual(envfile.dashboard_link(self.env, port=9000), "http://localhost:9000/?t=tok")
        os.environ["HQ_PORT"] = "9100"
        self.assertEqual(envfile.dashboard_link(self.env), "http://localhost:9100/?t=tok")
        os.environ.pop("HQ_PORT")

    def test_dashboard_link(self):
        self.env.write_text("HQ_TOKEN=tok\nHQ_HOST=100.64.1.2\n")
        self.assertEqual(envfile.dashboard_link(self.env), "http://100.64.1.2:8765/?t=tok")
        self.env.write_text("HQ_TOKEN=tok\nHQ_HOST=\n")
        self.assertEqual(envfile.dashboard_link(self.env), "http://localhost:8765/?t=tok")

    def test_telegram_dashboard_command(self):
        from engine.interfaces import telegram
        from tests.test_telegram import CHAT, FakeTelegram, msg
        self.env.write_text("")
        con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        db.init(con); loop.seed(con)
        fake = FakeTelegram()
        with mock.patch.object(telegram, "api", fake), mock.patch.object(envfile, "ENV_PATH", self.env), \
                mock.patch.object(envfile.dashboard_link, "__defaults__", (self.env, None)), \
                mock.patch.object(envfile.ensure_token, "__defaults__", (self.env,)):
            fake.updates = [msg(1, "/dashboard")]
            telegram.Bot(con, "TOKEN", CHAT, {"poll_seconds": 0}).poll_once()
        token = envfile.read_value("HQ_TOKEN", self.env)
        self.assertTrue(token)
        self.assertIn(f"http://localhost:8765/?t={token}", fake.sent()[0]["text"])


if __name__ == "__main__":
    unittest.main()
