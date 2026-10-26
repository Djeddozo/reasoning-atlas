"""Whisper setup from the app (#71): connect a tier, keep keys out of logs and the database, test transcription."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from engine import dashboard, db, envfile, loop, models, usage, voicesetup

NO_STT = {"HQ_WHISPER_URL": "", "HQ_STT_CLOUD_URL": "", "HQ_STT_CLOUD_KEY": ""}


class VoiceSetupTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con); loop.seed(self.con)
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.env = self.dir / "env"
        for target, value in ((envfile, "ENV_PATH"), (voicesetup, "VOICE_DIR")):
            p = mock.patch.object(target, value, self.env if value == "ENV_PATH" else self.dir / "voice")
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, NO_STT); p.start(); self.addCleanup(p.stop)

    def logs(self):
        return " ".join(f"{r['action']} {r['detail']}" for r in self.con.execute("SELECT * FROM log"))

    def test_status_when_nothing_is_set(self):
        st = voicesetup.status(self.con)
        self.assertEqual(st["active"], [])
        self.assertFalse(any(t["configured"] for t in st["tiers"].values()))
        self.assertNotIn("pi", st["tiers"])  # the Pi never transcribes on its own CPU (owner, 2026-10-25)

    def test_save_gpu_writes_env_and_cleans_the_address(self):
        msg = voicesetup.save(self.con, "gpu", " http://100.64.0.5:8000/v1/audio/transcriptions ")
        self.assertIn("http://100.64.0.5:8000", msg)
        self.assertEqual(envfile.read_value("HQ_WHISPER_URL", self.env), "http://100.64.0.5:8000")
        self.assertEqual(os.environ["HQ_WHISPER_URL"], "http://100.64.0.5:8000")
        self.assertEqual(voicesetup.status(self.con)["active"], ["local"])
        self.assertIn("voice_setup", self.logs())

    def test_bad_address_is_refused(self):
        for bad in ("gpu-pc:8000", "ftp://x", "http://", "http://a b"):
            with self.assertRaises(ValueError):
                voicesetup.save(self.con, "gpu", bad)
        self.assertFalse(self.env.exists())

    def test_cloud_key_goes_to_env_only(self):
        voicesetup.save(self.con, "cloud", "https://api.example.com", "sk-secret123")
        self.assertEqual(envfile.read_value("HQ_STT_CLOUD_KEY", self.env), "sk-secret123")
        st = voicesetup.status(self.con)
        self.assertTrue(st["tiers"]["cloud"]["key_set"])
        self.assertNotIn("sk-secret123", json.dumps(st))
        dump = "\n".join(self.con.iterdump())
        self.assertNotIn("sk-secret123", dump)  # never in the database, logs included

    def test_cloud_needs_a_key_but_keeps_a_saved_one(self):
        with self.assertRaises(ValueError):
            voicesetup.save(self.con, "cloud", "https://api.example.com")
        voicesetup.save(self.con, "cloud", "https://api.example.com", "k1")
        voicesetup.save(self.con, "cloud", "https://other.example.com")  # empty key keeps k1
        self.assertEqual(envfile.read_value("HQ_STT_CLOUD_KEY", self.env), "k1")
        self.assertEqual(envfile.read_value("HQ_STT_CLOUD_URL", self.env), "https://other.example.com")

    def test_remove_clears_the_tier(self):
        voicesetup.save(self.con, "cloud", "https://api.example.com", "k1")
        voicesetup.save(self.con, "cloud", "")
        self.assertEqual(envfile.read_value("HQ_STT_CLOUD_KEY", self.env), "")
        self.assertNotIn("HQ_STT_CLOUD_KEY", os.environ)
        self.assertEqual(voicesetup.status(self.con)["active"], [])

    def test_there_is_no_pi_cpu_tier(self):
        with mock.patch("subprocess.run") as run, self.assertRaises(ValueError) as e:
            voicesetup.save(self.con, "pi")
        self.assertIn("does not transcribe on its CPU", str(e.exception))
        run.assert_not_called()
        with self.assertRaises(ValueError):
            voicesetup.test(self.con, "pi")

    def test_gpu_test_uses_latest_recording_and_is_remembered(self):
        voicesetup.save(self.con, "gpu", "http://gpu:8000")
        (self.dir / "voice").mkdir()
        (self.dir / "voice" / "dash-1.webm").write_bytes(b"SPEECH")
        seen = []

        def urlopen(req, timeout=0):
            seen.append(req.data)
            return io.BytesIO(json.dumps({"text": "bel de tandarts morgen"}).encode())
        with mock.patch("urllib.request.urlopen", urlopen):
            r = voicesetup.test(self.con, "gpu")
        self.assertTrue(r["ok"]); self.assertEqual(r["words"], 4)
        self.assertIn(b"SPEECH", seen[0])
        check = voicesetup.status(self.con)["tiers"]["gpu"]["check"]
        self.assertTrue(check["ok"])
        self.assertNotIn("tandarts", db.get_state(self.con, "voice_check"))  # no transcript stored

    def test_unreachable_gpu_is_explained(self):
        voicesetup.save(self.con, "gpu", "http://gpu:8000")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            r = voicesetup.test(self.con, "gpu")
        self.assertFalse(r["ok"]); self.assertIn("not reachable", r["message"])
        # a new address forgets the old test
        voicesetup.save(self.con, "gpu", "http://gpu2:8000")
        self.assertIsNone(voicesetup.status(self.con)["tiers"]["gpu"]["check"])

    def test_cloud_test_sends_silence_not_recordings(self):
        voicesetup.save(self.con, "cloud", "https://api.example.com", "k1")
        (self.dir / "voice").mkdir()
        (self.dir / "voice" / "private.webm").write_bytes(b"PRIVATE")
        seen = []

        def urlopen(req, timeout=0):
            seen.append(req.data)
            return io.BytesIO(json.dumps({"text": ""}).encode())
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch.object(usage, "killed", return_value=False):
            r = voicesetup.test(self.con, "cloud")
        self.assertTrue(r["ok"])
        self.assertNotIn(b"PRIVATE", seen[0]); self.assertIn(b"RIFF", seen[0])

    def test_rejected_key_and_kill_switch(self):
        voicesetup.save(self.con, "cloud", "https://api.example.com", "bad")
        err = urllib.error.HTTPError("u", 401, "no", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err), mock.patch.object(usage, "killed", return_value=False):
            self.assertIn("rejected the key", voicesetup.test(self.con, "cloud")["message"])
        with mock.patch("urllib.request.urlopen") as urlopen, mock.patch.object(usage, "killed", return_value=True):
            self.assertIn("kill switch", voicesetup.test(self.con, "cloud")["message"])
        urlopen.assert_not_called()

    def test_dashboard_actions_never_log_the_key(self):
        dashboard.act(self.con, "voice_save", None, json.dumps({"tier": "cloud", "url": "https://x.example", "key": "sk-zzz"}))
        self.assertNotIn("sk-zzz", self.logs())
        self.assertIn("setup", dashboard.build_data(self.con)["voice"])
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")), \
                mock.patch.object(usage, "killed", return_value=False):
            self.assertFalse(json.loads(dashboard.act(self.con, "voice_test", None, "cloud"))["ok"])

    def test_page_has_the_setup_card_on_system(self):
        page = dashboard.PAGE.read_text()
        self.assertLess(page.index('id="system"'), page.index('id="voicePanel"'))
        self.assertIn("act(\"voice_save\"", page)


class SttTierSplitTest(unittest.TestCase):
    def test_local_is_the_whisper_server_never_the_pi_cpu(self):
        with tempfile.NamedTemporaryFile() as f, mock.patch.dict(os.environ, NO_STT):
            with self.assertRaises(models.ModelUnavailable) as e:
                models._stt_local({}, Path(f.name))
            self.assertIn("does not transcribe on its CPU", str(e.exception))
            self.assertFalse(hasattr(models, "_stt_pi"))
            with mock.patch.dict(os.environ, {"HQ_WHISPER_URL": "http://gpu:8000"}), \
                    mock.patch.object(models, "_stt_gpu", return_value="gpu text"):
                self.assertEqual(models._stt_local({}, Path(f.name)), "gpu text")

if __name__ == "__main__":
    unittest.main()
