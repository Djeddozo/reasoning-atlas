import json, sqlite3, sys, tempfile, types, unittest
from pathlib import Path
from unittest import mock

from engine import db, hardware, resources
from services.atlas import localvoice


class Hardware(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close); db.init(self.con)

    def test_detect_reads_this_machine(self):
        d = hardware.detect()
        for key in ("host", "role", "cpu", "cores", "memory_gb", "gpus", "os"):
            self.assertIn(key, d)
        self.assertIn(d["role"], ("pc", "pi"))

    def test_one_live_row_per_machine_read_only_and_separate(self):
        report = {"host": "DESKTOP-1", "role": "pc", "cpu": "Ryzen 7", "cores": 16, "memory_gb": 32.0,
                  "gpus": [{"name": "RTX 4070", "memory_gb": 12.0, "cuda": True}], "disk_free_gb": 400, "disk_total_gb": 1000, "os": "Windows 11"}
        rid = hardware.upsert(self.con, report)
        report["memory_gb"] = 64.0
        self.assertEqual(hardware.upsert(self.con, report), rid)  # refreshed, not duplicated
        resources.add(self.con, {"kind": "time", "name": "Evenings", "value": 10, "unit": "h/week"})
        listing = resources.listing(self.con)
        self.assertEqual([c["name"] for c in listing["connected"]], ["This PC (DESKTOP-1)"])
        self.assertIn("RTX 4070 12 GB (CUDA)", listing["connected"][0]["details"])
        self.assertEqual(listing["connected"][0]["detected"]["memory_gb"], 64.0)
        self.assertEqual([g["kind"] for g in listing["groups"]], ["time"])  # what he writes stays apart
        with self.assertRaises(ValueError):
            resources.edit(self.con, rid, {"details": "by hand"})
        with self.assertRaises(ValueError):
            hardware.upsert(self.con, {"host": ""})


class LocalVoice(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        p = mock.patch.object(localvoice, "SETTINGS", Path(d.name) / "voice.json"); p.start(); self.addCleanup(p.stop)
        localvoice._model.clear()

    def test_never_on_a_pi(self):
        with mock.patch("platform.system", return_value="Linux"), mock.patch("platform.machine", return_value="aarch64"):
            self.assertIn("Pi", localvoice.allowed())
            self.assertFalse(localvoice.status()["available"])
            with self.assertRaises(PermissionError):
                localvoice.transcribe(b"x")
            with self.assertRaises(PermissionError):
                localvoice.install()

    def test_transcribes_on_this_pc_and_keeps_nothing(self):
        seen = {}

        class Model:
            def __init__(self, name, device, compute_type):
                seen["model"] = (name, device, compute_type)

            def transcribe(self, path, language=None, initial_prompt=None, vad_filter=True):
                seen["path"], seen["language"] = path, language
                return [types.SimpleNamespace(text=" Hallo "), types.SimpleNamespace(text="Atlas. ")], None
        fake = types.SimpleNamespace(WhisperModel=Model)
        with mock.patch("platform.system", return_value="Windows"), mock.patch("platform.machine", return_value="AMD64"), \
             mock.patch.object(localvoice, "installed", return_value=True), mock.patch.object(localvoice, "device", return_value="cuda"), \
             mock.patch.dict(sys.modules, {"faster_whisper": fake}), mock.patch.object(localvoice, "_decode", side_effect=lambda p: p):
            localvoice.save_settings({"language": "nl"})
            self.assertEqual(localvoice.transcribe(b"RIFF....", "audio/webm"), "Hallo Atlas.")
            self.assertEqual(seen["model"], ("large-v3-turbo", "cuda", "float16"))
            self.assertEqual(seen["language"], "nl")
            self.assertFalse(Path(seen["path"]).exists())  # the recording is removed at once
            with self.assertRaises(ValueError):
                localvoice.save_settings({"model": "huge"})


class LocalVoiceInstall(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup); self.dir = Path(d.name)
        p = mock.patch.object(localvoice, "INSTALL_STATE", self.dir / "install.json"); p.start(); self.addCleanup(p.stop)

    def test_a_fresh_user_install_is_seen_without_a_restart(self):
        site_dir = self.dir / "user-site"; (site_dir / "faster_whisper").mkdir(parents=True)
        (site_dir / "faster_whisper" / "__init__.py").write_text("")
        with mock.patch("site.getusersitepackages", return_value=str(site_dir)), mock.patch.object(sys, "path", list(sys.path)):
            self.assertTrue(localvoice.installed())

    def test_gpu_support_only_recommended_with_an_nvidia_gpu(self):
        with mock.patch.object(localvoice, "hardware", return_value={"nvidia": [], "gpus": ["Intel Iris Xe"]}), \
             mock.patch.object(localvoice, "gpu_libraries", return_value=True), mock.patch.object(localvoice, "on_pi", return_value=False):
            st = localvoice.status()
        self.assertFalse(st["gpu_recommended"]); self.assertTrue(st["gpu_libraries_unused"])

    def test_progress_is_read_from_pip_and_survives_a_restart(self):
        script = self.dir / "fakepip.py"
        script.write_text("print('Collecting faster-whisper')\nprint('  Downloading faster_whisper-1.0-py3-none-any.whl (75.0 MB)')\n"
                          "print('Collecting av')\nprint('Installing collected packages: av, faster-whisper')\n")
        import subprocess as sp
        real = sp.Popen
        with mock.patch.object(localvoice.subprocess, "Popen", side_effect=lambda args, **kw: real([sys.executable, str(script)], **kw)):
            localvoice._pip(["install", "x"], False, "install")
        st = localvoice._install_state()
        self.assertEqual((st["state"], st["percent"], st["downloaded_mb"]), ("done", 100, 75))
        localvoice._set_install(state="installing", percent=40)
        with mock.patch("os.getpid", return_value=-1):  # Atlas restarted mid-install
            self.assertEqual(localvoice._install_state()["state"], "interrupted")


@unittest.skipUnless(__import__("importlib").util.find_spec("av") and __import__("shutil").which("ffmpeg"), "needs PyAV and ffmpeg")
class Decode(unittest.TestCase):
    def test_webm_becomes_16khz_mono_samples_without_faster_whispers_decoder(self):
        import subprocess as sp
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "tone.webm"
            sp.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ac", "2", "-ar", "48000",
                    "-c:a", "libopus", str(clip)], check=True)
            samples = localvoice._decode(str(clip))
        self.assertEqual((str(samples.dtype), samples.shape), ("float32", (32000,)))


class ServerEndpoints(unittest.TestCase):
    def test_voice_and_hardware_on_this_computer(self):
        import json as _j, threading, urllib.request, urllib.error
        from services.atlas import server
        with tempfile.TemporaryDirectory() as d:
            httpd = server.make_server(0, d + "/t.db"); threading.Thread(target=httpd.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{httpd.server_port}"
            try:
                st = _j.load(urllib.request.urlopen(url + "/api/voice/local"))
                self.assertIn("available", st)
                self.assertIn("cpu", _j.load(urllib.request.urlopen(url + "/api/hardware")))
                with mock.patch.object(localvoice, "allowed", return_value="This is the Pi: it never transcribes on its CPU."):
                    req = urllib.request.Request(url + "/api/voice/local/transcribe", data=b'{"data":"AAAA"}', headers={"Content-Type": "application/json"})
                    with self.assertRaises(urllib.error.HTTPError) as e:
                        urllib.request.urlopen(req)
                    self.assertEqual(e.exception.code, 403)
            finally:
                httpd.shutdown(); httpd.server_close()


if __name__ == "__main__":
    unittest.main()
