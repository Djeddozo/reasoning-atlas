"""Telegram bot tests. All network calls are mocked. Run: python3 -m unittest discover tests"""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import db, dump, loop, models
from engine.interfaces import telegram

CHAT = "4242"


class FakeTelegram:
    """Stands in for the Bot API: records calls and serves queued updates."""

    def __init__(self):
        self.calls, self.updates, self.next_id = [], [], 100

    def __call__(self, token, method, params=None, timeout=30):
        params = params or {}
        self.calls.append((method, params))
        if method == "getUpdates":
            return [u for u in self.updates if u["update_id"] >= params.get("offset", 0)]
        if method == "sendMessage":
            self.next_id += 1
            return {"message_id": self.next_id, "chat": {"id": int(params["chat_id"])}, "text": params["text"]}
        if method == "getFile":
            return {"file_path": "voice/file_1.oga", "file_size": 10}
        return True

    def sent(self):
        return [p for m, p in self.calls if m == "sendMessage"]


def msg(update_id, text=None, chat=CHAT, sender=None, **extra):
    m = {"message_id": update_id, "chat": {"id": int(chat), "type": "private"},
         "from": {"id": int(sender or chat)}, **extra}
    if text is not None:
        m["text"] = text
    return {"update_id": update_id, "message": m}


class TelegramTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.row_factory = sqlite3.Row
        db.init(self.con)
        loop.seed(self.con)
        self.fake = FakeTelegram()
        p = mock.patch.object(telegram, "api", self.fake)
        p.start(); self.addCleanup(p.stop)
        b = mock.patch.object(loop, "brief", lambda con, write=True: "# Atlas brief\n**Do next**")  # no briefs/ files
        b.start(); self.addCleanup(b.stop)
        self.bot = telegram.Bot(self.con, "TOKEN", CHAT, {"poll_seconds": 0, "max_voice_mb": 1})

    def test_text_goes_to_dump_and_offset_advances(self):
        self.fake.updates = [msg(7, "todo: buy SSD for the pi")]
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("offline")):
            self.assertEqual(self.bot.poll_once(), 1)
        self.assertTrue(self.con.execute("SELECT 1 FROM tasks WHERE title = 'buy SSD for the pi'").fetchone())
        self.assertIn("1 tasks", self.fake.sent()[0]["text"])
        self.assertEqual(db.get_state(self.con, "telegram_offset"), "8")
        self.assertEqual(self.bot.poll_once(), 0)  # not processed twice

    def test_other_chats_are_ignored(self):
        self.fake.updates = [msg(1, "todo: something sneaky", chat="999")]
        with mock.patch.object(dump, "process") as proc:
            self.bot.poll_once()
        proc.assert_not_called()
        self.assertEqual(self.fake.sent(), [])
        self.assertTrue(self.con.execute("SELECT 1 FROM log WHERE action = 'telegram_ignored'").fetchone())

    def test_commands(self):
        self.fake.updates = [msg(1, "/next"), msg(2, "/brief"), msg(3, "/radar"), msg(4, "/approvals"),
                             msg(5, "/bogus"), msg(6, "/help@hq_bot")]
        self.bot.poll_once()
        texts = [p["text"] for p in self.fake.sent()]
        self.assertTrue(texts[0].startswith("#"))          # ranked tasks
        self.assertNotIn("**", texts[1])                    # brief as plain text
        self.assertIn("Radar is empty", texts[2])
        self.assertEqual(texts[3], "No pending approvals.")
        self.assertIn("Unknown command /bogus", texts[4])
        self.assertTrue(texts[5].startswith("Atlas bot."))

    def test_approvals_are_pushed_once_with_buttons(self):
        aid = db.insert(self.con, "approvals", request="Buy 1TB SSD", reason="Pi storage", cost_eur=60, nonce="n0nce")
        agent_aid = db.insert(self.con, "approvals", request="proposed by agent")  # no nonce yet
        self.bot.poll_once()
        self.bot.poll_once()
        sent = self.fake.sent()
        self.assertEqual(len(sent), 2)
        buttons = sent[0]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in buttons], ["Approve", "Deny"])
        self.assertEqual(buttons[0]["callback_data"], f"approved {aid} n0nce")
        self.assertIn("€60", sent[0]["text"])
        nonce = self.con.execute("SELECT nonce FROM approvals WHERE id = ?", (agent_aid,)).fetchone()[0]
        self.assertTrue(nonce)
        self.assertLessEqual(len(sent[1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"].encode()), 64)

    def test_button_needs_right_nonce_and_chat(self):
        aid = db.insert(self.con, "approvals", request="pip install x", nonce="right")
        self.con.commit()
        base = {"message": {"message_id": 5, "chat": {"id": int(CHAT)}, "text": "Atlas needs approval"},
                "from": {"id": int(CHAT)}}
        self.fake.updates = [
            {"update_id": 1, "callback_query": {"id": "a", "data": f"approved {aid} wrong", **base}},
            {"update_id": 2, "callback_query": {"id": "b", "data": "garbage", **base}},
            {"update_id": 3, "callback_query": {"id": "c", "data": f"approved {aid} right", "from": {"id": 999},
                                                "message": {"message_id": 5, "chat": {"id": 999}}}},
            # a group member: right chat id (a group configured by mistake) and right nonce, but not Djeddi
            {"update_id": 4, "callback_query": {"id": "e", "data": f"approved {aid} right", **base,
                                                "from": {"id": 777}}},
        ]
        self.bot.poll_once()
        status = lambda: self.con.execute("SELECT status FROM approvals WHERE id = ?", (aid,)).fetchone()[0]
        self.assertEqual(status(), "pending")
        self.fake.updates = [{"update_id": 5, "callback_query": {"id": "d", "data": f"denied {aid} right", **base}}]
        self.bot.poll_once()
        self.assertEqual(status(), "denied")
        edits = [p for m, p in self.fake.calls if m == "editMessageText"]
        self.assertEqual(len(edits), 1)
        self.assertTrue(edits[0]["text"].endswith("→ denied"))
        self.assertTrue(self.con.execute("SELECT 1 FROM log WHERE detail = ?", (f"#{aid} via telegram",)).fetchone())

    def test_decision_shows_one_button_per_option(self):
        aid = db.insert(self.con, "approvals", request="Which first?", category="decision",
                        options="Telegram bot|Tailscale|Wait", nonce="nn")
        self.bot.poll_once()
        rows = self.fake.sent()[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual([r[0]["text"] for r in rows], ["Telegram bot", "Tailscale", "Wait"])
        self.assertEqual(rows[1][0]["callback_data"], f"choose {aid} nn 1")
        tap = {"id": "x", "data": f"choose {aid} nn 1", "from": {"id": int(CHAT)},
               "message": {"message_id": 9, "chat": {"id": int(CHAT)}, "text": "Atlas needs your decision"}}
        self.fake.updates = [{"update_id": 1, "callback_query": {**tap, "data": f"choose {aid} nn 7"}},  # no such option
                             {"update_id": 2, "callback_query": tap}]
        self.bot.poll_once()
        status, answer = self.con.execute("SELECT status, answer FROM approvals WHERE id = ?", (aid,)).fetchone()
        self.assertEqual((status, answer), ("answered", "Tailscale"))
        edits = [p for m, p in self.fake.calls if m == "editMessageText"]
        self.assertTrue(edits[0]["text"].endswith("→ chose: Tailscale"))

    def test_group_member_message_is_ignored(self):
        self.fake.updates = [msg(1, "todo: from a group member", sender="777")]
        with mock.patch.object(dump, "process") as proc:
            self.bot.poll_once()
        proc.assert_not_called()

    def test_model_work_does_not_block_approvals(self):
        """A slow dump runs on the worker; the poll loop still pushes approvals and returns at once."""
        import threading
        release, started = threading.Event(), threading.Event()

        def slow_process(con, text, **kw):
            db.insert(con, "memories", content=text); con.commit()  # what dump.process does before the model call
            started.set()
            release.wait(5)
            return "done"

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hq.db"

            def connect():
                c = sqlite3.connect(path, check_same_thread=False)
                c.row_factory = sqlite3.Row
                db.init(c)
                return c

            main = connect()
            loop.seed(main)
            bot = telegram.Bot(main, "TOKEN", CHAT, {"poll_seconds": 0})
            worker = bot.start_worker(connect)
            db.insert(main, "approvals", request="pip install x", nonce="n")
            main.commit()
            self.fake.updates = [msg(1, "a long brain dump")]
            with mock.patch.object(dump, "process", slow_process):
                t0 = __import__("time").time()
                bot.poll_once()
                self.assertLess(__import__("time").time() - t0, 2)
                self.assertTrue(started.wait(2))
                self.assertIn("reply_markup", self.fake.sent()[0])  # approval sent while the dump is running
                self.fake.updates.append(msg(2, "/next"))
                bot.poll_once()                                     # main thread can still write (offset)
                self.assertEqual(db.get_state(main, "telegram_offset"), "3")
                release.set()
                bot.jobs.join()
            bot.jobs.put(None); worker.join(2)
            self.assertEqual(self.fake.sent()[-1]["text"], "done")
            main.close()

    def test_worker_survives_a_job_whose_error_handler_fails(self):
        """If even logging the error fails (e.g. database locked), the worker keeps serving later jobs."""
        import threading
        bot = telegram.Bot(self.con, "TOKEN", CHAT, {})
        con = mock.MagicMock()
        con.commit.side_effect = sqlite3.OperationalError("database is locked")
        worker = bot.start_worker(lambda: con)
        done = threading.Event()

        def boom(c):
            raise RuntimeError("model crashed")

        bot.submit(boom)
        bot.submit(lambda c: done.set())
        self.assertTrue(done.wait(2))
        con.rollback.assert_called()  # the failed job's half-done write is not left holding a lock
        bot.jobs.put(None); worker.join(2)

    def test_confirmation_edit_stays_within_telegram_limit(self):
        aid = db.insert(self.con, "approvals", request="x", nonce="n")
        self.con.commit()
        tap = {"id": "a", "data": f"approved {aid} n", "from": {"id": int(CHAT)},
               "message": {"message_id": 5, "chat": {"id": int(CHAT)}, "text": "y" * 4090}}
        self.fake.updates = [{"update_id": 1, "callback_query": tap}]
        self.bot.poll_once()
        edit = [p for m, p in self.fake.calls if m == "editMessageText"][0]
        self.assertLessEqual(len(edit["text"]), telegram.MAX_TEXT)
        self.assertTrue(edit["text"].endswith("→ approved"))

    def test_run_keeps_going_after_a_database_error(self):
        calls = []

        def poll_once(timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            raise KeyboardInterrupt  # stop the loop

        with mock.patch.object(self.bot, "poll_once", poll_once), mock.patch.object(telegram.time, "sleep"), \
                mock.patch.object(self.bot, "start_worker"):
            with self.assertRaises(KeyboardInterrupt):
                self.bot.run()
        self.assertEqual(len(calls), 2)
        self.assertTrue(self.con.execute("SELECT 1 FROM log WHERE action = 'telegram_poll_error'").fetchone())

    def _voice_update(self):
        return [msg(1, voice={"file_id": "F1", "file_unique_id": "../evil", "duration": 3})]

    def test_voice_without_transcriber_is_kept_as_memory(self):
        self.fake.updates = self._voice_update()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(telegram, "VOICE_DIR", Path(d)), \
                mock.patch("urllib.request.urlopen", lambda *a, **k: io.BytesIO(b"OggS-audio")), \
                mock.patch.object(models, "transcribe", side_effect=models.ModelUnavailable("no whisper")):
            self.bot.poll_once()
            files = list(Path(d).iterdir())
            self.assertEqual(len(files), 1)
            self.assertNotIn("..", files[0].name)
            mem = self.con.execute("SELECT content FROM memories WHERE tags LIKE 'voice%'").fetchone()[0]
            self.assertIn(str(files[0]), mem)
        self.assertIn("needs the GPU PC", self.fake.sent()[0]["text"])

    def test_voice_with_transcriber_goes_to_dump(self):
        self.fake.updates = self._voice_update()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(telegram, "VOICE_DIR", Path(d)), \
                mock.patch("urllib.request.urlopen", lambda *a, **k: io.BytesIO(b"OggS-audio")), \
                mock.patch.object(models, "transcribe", return_value="todo: call the dentist"), \
                mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("offline")):
            self.bot.poll_once()
        self.assertTrue(self.con.execute("SELECT 1 FROM tasks WHERE title = 'call the dentist'").fetchone())
        self.assertTrue(self.fake.sent()[0]["text"].startswith("Heard: todo: call the dentist"))

    def test_handler_crash_is_logged_not_retried(self):
        self.fake.updates = [msg(1, "hello")]
        with mock.patch.object(dump, "process", side_effect=KeyError("boom")):
            self.bot.poll_once()
        self.assertEqual(db.get_state(self.con, "telegram_offset"), "2")
        self.assertTrue(self.con.execute("SELECT 1 FROM log WHERE action = 'telegram_error'").fetchone())
        self.assertIn("went wrong", self.fake.sent()[-1]["text"])

    def test_long_replies_are_split(self):
        parts = telegram.chunks("line\n" * 2000)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= telegram.MAX_TEXT for p in parts))


class TranscribeTest(unittest.TestCase):
    def test_whisper_endpoint_is_used_when_set(self):
        seen = {}

        def fake_urlopen(req, timeout=0):
            seen["url"], seen["ctype"], seen["body"] = req.full_url, req.headers["Content-type"], req.data
            return io.BytesIO(json.dumps({"text": " hello hq "}).encode())

        with tempfile.NamedTemporaryFile(suffix=".oga") as f, \
                mock.patch.dict("os.environ", {"HQ_WHISPER_URL": "http://gpu-pc:8000/"}), \
                mock.patch("urllib.request.urlopen", fake_urlopen):
            f.write(b"AUDIO"); f.flush()
            self.assertEqual(models.transcribe(f.name), "hello hq")
        self.assertEqual(seen["url"], "http://gpu-pc:8000/v1/audio/transcriptions")
        self.assertTrue(seen["ctype"].startswith("multipart/form-data; boundary="))
        self.assertIn(b"AUDIO", seen["body"])

    def test_unavailable_without_endpoint(self):
        import builtins
        real_import = builtins.__import__

        def no_whisper(name, *a, **k):
            if name == "faster_whisper":
                raise ImportError(name)
            return real_import(name, *a, **k)

        with tempfile.NamedTemporaryFile() as f, mock.patch.dict("os.environ", {"HQ_WHISPER_URL": ""}), \
                mock.patch("builtins.__import__", no_whisper):
            with self.assertRaises(models.ModelUnavailable):
                models.transcribe(f.name)


if __name__ == "__main__":
    unittest.main()
