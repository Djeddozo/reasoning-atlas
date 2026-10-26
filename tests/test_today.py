"""Today as an action dashboard: chat with Claude, autonomy levels for the agent, queueing for the AI."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import chat, dashboard, db, devloop, envfile, loop, models


class TodayTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con); loop.seed(self.con)
        self.task = db.insert(self.con, "tasks", title="Write the Atlas help page", owner="me")
        self.con.commit()

    def test_chat_answers_and_files_new_thoughts(self):
        replies = iter([json.dumps({"reply": "Focus on #3 first.", "thought": "I want to finish the help page this week"}),
                        json.dumps({"tasks": [{"title": "Finish help page"}], "summary": "1 task"})])
        with mock.patch.object(models, "complete", side_effect=lambda *a: next(replies)):
            r = chat.ask(self.con, "what first? and I want to finish the help page this week")
        self.assertEqual((r["reply"], r["added"].split("\n")[0]), ("Focus on #3 first.", "1 task"))
        h = chat.history(self.con)
        self.assertEqual([m["role"] for m in h], ["me", "claude"])
        self.assertIn("Filed: 1 task\n• task #", h[1]["text"])
        self.assertEqual(self.con.execute("SELECT source FROM thoughts").fetchone()[0], "chat")

    def test_chat_without_model_keeps_the_message(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("off")):
            r = chat.ask(self.con, "remember: dentist on friday")
        self.assertIn("can't reach Claude", r["reply"])
        self.assertEqual(self.con.execute("SELECT text FROM thoughts").fetchone()[0], "remember: dentist on friday")

    def test_autonomy_levels(self):
        self.assertEqual(devloop.autonomy(self.con), "limits")
        devloop.set_autonomy(self.con, "off")
        self.assertTrue(devloop.paused(self.con))
        self.assertEqual(devloop.autonomy(self.con), "off")
        devloop.set_autonomy(self.con, "continuous")
        self.assertFalse(devloop.paused(self.con))
        self.assertEqual(devloop.overview(self.con)["autonomy"], "continuous")
        with self.assertRaises(ValueError):
            devloop.set_autonomy(self.con, "maximum")

    def test_ask_first_level_needs_approval(self):
        devloop.set_autonomy(self.con, "ask")
        db.update(self.con, "tasks", self.task, owner="ai"); self.con.commit()
        conf = devloop.DEFAULTS | {"enabled": True}
        with mock.patch.object(devloop, "cfg", lambda: conf), mock.patch.object(devloop, "claude_path", return_value="/bin/true"), \
                mock.patch.object(devloop, "sync_merged", return_value=[]), \
                mock.patch("engine.gatekeeper.request_approval", return_value="denied") as ask, \
                mock.patch.object(devloop, "_run_task") as run_task, tempfile.TemporaryDirectory() as d, \
                mock.patch.object(devloop, "WORK", Path(d)), mock.patch.object(devloop, "LOCK", Path(d) / "lock"):
            self.assertEqual(devloop.run(self.con)["status"], "declined")
            self.assertIn("Write the Atlas help page", ask.call_args[0][1])
            run_task.assert_not_called()

    def test_continuous_starts_the_next_run(self):
        from engine import cli
        devloop.set_autonomy(self.con, "continuous")
        with mock.patch.object(cli, "_con", lambda: self.con), \
                mock.patch.object(devloop, "run", return_value={"status": "pr_open", "task": 1, "title": "x", "summary": "", "pr": ""}), \
                mock.patch.object(devloop, "why_not_now", return_value=""), \
                mock.patch.object(devloop, "notify"), mock.patch.object(devloop, "start_background") as nxt:
            cli.main(["dev", "run"])
        nxt.assert_called_once_with(force=False)

    def test_dashboard_controls(self):
        dashboard.act(self.con, "task_owner", self.task, "ai")
        self.assertEqual(self.con.execute("SELECT owner FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "ai")
        data = dashboard.build_data(self.con)
        self.assertIn(self.task, [t["id"] for t in data["agent"]["queued"]])
        self.assertEqual(data["brain"]["changes"][0]["reason"], "queued for the AI by Djeddi")  # logged, undoable
        self.assertIn("Continuous", dashboard.act(self.con, "autonomy", None, "continuous"))
        with self.assertRaises(ValueError):
            dashboard.act(self.con, "task_owner", self.task, "robot")
        with mock.patch.object(models, "complete", return_value=json.dumps({"reply": "Hi", "thought": ""})):
            self.assertEqual(json.loads(dashboard.act(self.con, "ask", None, "hello"))["reply"], "Hi")
        self.assertEqual(len(dashboard.build_data(self.con)["chat"]), 2)

    def test_telegram_ask_and_autonomy(self):
        from engine.interfaces import telegram
        from tests.test_telegram import CHAT, FakeTelegram, msg
        fake = FakeTelegram()
        fake.updates = [msg(1, "/autonomy ask"), msg(2, "/autonomy"), msg(3, "/ask what now?"), msg(4, "/autonomy fast")]
        with mock.patch.object(telegram, "api", fake), \
                mock.patch.object(models, "complete", return_value=json.dumps({"reply": "Do #3.", "thought": ""})):
            telegram.Bot(self.con, "TOKEN", CHAT, {"poll_seconds": 0}).poll_once()
        texts = [p["text"] for p in fake.sent()]
        self.assertTrue(texts[0].startswith("Ask first"))
        self.assertIn("Agent autonomy: ask", texts[1])
        self.assertIn("Do #3.", texts)                      # reply sent by the worker
        self.assertTrue(any("must be one of" in t for t in texts))

    def test_https_dashboard_link(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"; env.write_text("HQ_TOKEN=tok\nHQ_URL=https://pi.tail1234.ts.net\n")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("HQ_TOKEN", None); os.environ.pop("HQ_URL", None)
                self.assertEqual(envfile.dashboard_link(env), "https://pi.tail1234.ts.net/?t=tok")



class AutonomyResumeTest(unittest.TestCase):
    def test_pause_and_resume_keep_the_slider_in_step(self):
        import sqlite3
        from engine import db, devloop
        con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row; db.init(con)
        devloop.set_autonomy(con, "off")
        devloop.set_paused(con, False)           # Resume on the Agent tab or /dev resume
        self.assertFalse(devloop.paused(con))
        self.assertEqual(devloop.autonomy(con), "limits")
        self.assertEqual(db.get_state(con, "devloop_autonomy", ""), "limits")
        devloop.set_paused(con, True)
        self.assertEqual(devloop.autonomy(con), "off")
        devloop.set_autonomy(con, "continuous"); devloop.set_paused(con, False)
        self.assertEqual(devloop.autonomy(con), "continuous")
        db.set_state(con, "devloop_autonomy", "off"); db.set_state(con, "devloop_paused", "0")  # an older state mix
        self.assertEqual(devloop.autonomy(con), "limits")


class HttpsScriptTest(unittest.TestCase):
    """deploy/https-dashboard.sh with a fake Tailscale: ports from .env, URL with the port when not 443."""

    def run_script(self, env_lines: str):
        import shutil, subprocess
        root = Path(__file__).resolve().parent.parent
        d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d, True)
        (d / "deploy").mkdir(); shutil.copy(root / "deploy" / "https-dashboard.sh", d / "deploy")
        shutil.copytree(root / "engine", d / "engine")
        (d / "deploy" / "restart.sh").write_text("true\n")
        (d / "atlas.sh").write_text("#!/bin/sh\necho link\n"); (d / "atlas.sh").chmod(0o755)
        (d / ".env").write_text(env_lines)
        bindir = d / "bin"; bindir.mkdir()
        (bindir / "tailscale").write_text('#!/bin/sh\nif [ "$1" = status ]; then echo \'{"Self": {"DNSName": "pi.tail1.ts.net."}}\'; '
                                          'else echo "$@" >> "%s/serve.log"; fi\n' % d)
        (bindir / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
        (bindir / "sleep").write_text("#!/bin/sh\n")
        for f in ("tailscale", "sudo", "sleep"):
            (bindir / f).chmod(0o755)
        r = subprocess.run(["bash", "deploy/https-dashboard.sh"], cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(d)}, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return (d / "serve.log").read_text().strip(), envfile.read_value("HQ_URL", d / ".env")

    def test_default_and_custom_ports(self):
        self.assertEqual(self.run_script(""), ("serve --bg --https=443 8765", "https://pi.tail1.ts.net"))
        self.assertEqual(self.run_script("HQ_PORT=8770\nHQ_HTTPS_PORT=8443\n"),
                         ("serve --bg --https=8443 8770", "https://pi.tail1.ts.net:8443"))

if __name__ == "__main__":
    unittest.main()
