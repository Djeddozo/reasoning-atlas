"""Voice input: batch Whisper with a language hint, an optional cloud tier, and the dashboard's /api/voice upload."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import dashboard, db, loop, models, talk, usage

NO_STT = {"HQ_WHISPER_URL": "", "HQ_STT_CLOUD_URL": "", "HQ_STT_CLOUD_KEY": ""}


def fake_server(seen, text="hallo hq"):
    def urlopen(req, timeout=0):
        seen.append({"url": req.full_url, "body": req.data, "auth": req.headers.get("Authorization")})
        return io.BytesIO(json.dumps({"text": text}).encode())
    return urlopen


class TranscribeTest(unittest.TestCase):
    def test_language_and_vocabulary_prompt_are_sent(self):
        seen = []
        with tempfile.NamedTemporaryFile(suffix=".webm") as f, \
                mock.patch.dict("os.environ", NO_STT | {"HQ_WHISPER_URL": "http://gpu-pc:8000"}), \
                mock.patch("urllib.request.urlopen", fake_server(seen)):
            self.assertEqual(models.transcribe(f.name, "nl-NL"), "hallo hq")
        body = seen[0]["body"]
        self.assertIn(b'name="language"\r\n\r\nnl\r\n', body)
        self.assertIn(b'name="model"\r\n\r\nlarge-v3-turbo\r\n', body)
        self.assertIn(b'name="prompt"', body)

    def test_auto_detect_sends_no_language(self):
        seen = []
        with tempfile.NamedTemporaryFile() as f, \
                mock.patch.dict("os.environ", NO_STT | {"HQ_WHISPER_URL": "http://gpu-pc:8000"}), \
                mock.patch("urllib.request.urlopen", fake_server(seen)):
            models.transcribe(f.name, "")
        self.assertNotIn(b'name="language"', seen[0]["body"])

    def test_cloud_tier_used_when_local_is_missing(self):
        seen = []
        env = NO_STT | {"HQ_STT_CLOUD_URL": "https://stt.example", "HQ_STT_CLOUD_KEY": "k123"}
        with tempfile.NamedTemporaryFile() as f, mock.patch.dict("os.environ", env), \
                mock.patch.object(models, "_stt_local", side_effect=models.ModelUnavailable("off")), \
                mock.patch.object(usage, "killed", return_value=False), \
                mock.patch("urllib.request.urlopen", fake_server(seen, "cloud text")):
            self.assertEqual(models.transcribe(f.name), "cloud text")
        self.assertEqual(seen[0]["url"], "https://stt.example/v1/audio/transcriptions")
        self.assertEqual(seen[0]["auth"], "Bearer k123")

    def test_cloud_tier_respects_kill_switch(self):
        env = NO_STT | {"HQ_STT_CLOUD_URL": "https://stt.example", "HQ_STT_CLOUD_KEY": "k123"}
        with tempfile.NamedTemporaryFile() as f, mock.patch.dict("os.environ", env), \
                mock.patch.object(usage, "killed", return_value=True), \
                mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(models.ModelUnavailable):
                models._stt_cloud({}, Path(f.name))
        urlopen.assert_not_called()

    def test_available_tiers(self):
        with mock.patch.dict("os.environ", NO_STT), mock.patch("importlib.util.find_spec", return_value=None):
            self.assertEqual(models.stt_available(), [])
        with mock.patch.dict("os.environ", NO_STT | {"HQ_WHISPER_URL": "http://gpu-pc:8000",
                                                     "HQ_STT_CLOUD_URL": "https://x", "HQ_STT_CLOUD_KEY": "k"}):
            self.assertEqual(models.stt_available(), ["local", "cloud"])


class DashboardVoiceTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con); loop.seed(self.con)
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        p = mock.patch.object(talk, "VOICE_DIR", self.dir); p.start(); self.addCleanup(p.stop)

    def test_recording_is_stored_and_transcribed_with_language(self):
        with mock.patch.object(models, "transcribe", return_value="bel de tandarts") as t:
            self.assertEqual(dashboard.voice(self.con, b"OPUS", "audio/webm;codecs=opus", "nl-NL"), "bel de tandarts")
        path = t.call_args[0][0]
        self.assertEqual((path.suffix, path.read_bytes(), t.call_args[0][1]), (".webm", b"OPUS", "nl-NL"))

    def test_recording_kept_when_no_transcriber(self):
        with mock.patch.object(models, "transcribe", side_effect=models.ModelUnavailable("asleep")):
            with self.assertRaises(models.ModelUnavailable):
                dashboard.voice(self.con, b"AUDIO", "audio/mp4", "")
        self.assertEqual(len(list(self.dir.iterdir())), 1)
        row = self.con.execute("SELECT content FROM memories WHERE tags='voice untranscribed'").fetchone()
        self.assertIn("awaiting transcription", row[0])

    def test_language_is_sanitised(self):
        with mock.patch.object(models, "transcribe", return_value="x") as t:
            dashboard.voice(self.con, b"A", "audio/ogg", "nl'; DROP")
        self.assertEqual(t.call_args[0][1], "nlDROP")

    def test_empty_recording_rejected(self):
        with self.assertRaises(ValueError):
            dashboard.voice(self.con, b"", "audio/webm", "")

    def test_data_tells_page_which_speech_tiers_exist(self):
        with mock.patch.object(models, "stt_available", return_value=["cloud"]):
            self.assertEqual(dashboard.build_data(self.con)["voice"]["server"], ["cloud"])

    def test_page_records_for_server_and_restarts_browser_speech(self):
        page = dashboard.PAGE.read_text()
        self.assertIn("/api/voice?lang=", page)
        self.assertIn("MediaRecorder", page)
        self.assertIn("if (want) listen()", page)


if __name__ == "__main__":
    unittest.main()
