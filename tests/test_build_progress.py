import json, tempfile, threading, unittest, urllib.request
from pathlib import Path
from unittest import mock
from services.atlas import progress, server


class Progress(unittest.TestCase):
    def test_steps_done_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            self.assertEqual(progress.read(path)["state"], "idle")
            progress.start("RA-019", "Live progress", ["One", "Two", "Three", "Four"], path)
            data = progress.step(3, path=path)
            self.assertEqual((data["state"], data["step"], data["progress"], data["message"]), ("building", 3, 50, "Three"))
            self.assertEqual(progress.finish("Saved", path)["progress"], 100)
            self.assertEqual(progress.fail("Broken", path)["state"], "failed")

    def test_silent_builder_is_stalled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            data = progress.start("RA-019", "Live progress", ["One"], path)
            data["updatedAt"] = "2020-01-01T00:00:00Z"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(progress.read(path)["state"], "stalled")

    def test_corrupt_file_reads_as_idle(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(progress.read(path)["state"], "idle")


class Releases(unittest.TestCase):
    def test_server_switches_release_without_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dist = root / "apps/web/dist"
            for name in ("old", "new"):
                (dist / "releases" / name).mkdir(parents=True)
                (dist / "releases" / name / "index.html").write_text(f"<p>{name}</p>", encoding="utf-8")
            (dist / "current.json").write_text(json.dumps({"release": "old"}), encoding="utf-8")
            status = root / "runtime/build-status.json"
            with mock.patch.object(server, "ROOT", root), mock.patch.object(progress, "STATUS_FILE", status):
                httpd = server.make_server(0, str(root / "runtime/test.db"))
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                url = f"http://127.0.0.1:{httpd.server_port}"
                try:
                    self.assertIn("old", urllib.request.urlopen(url + "/").read().decode())
                    self.assertEqual(json.load(urllib.request.urlopen(url + "/api/build-status"))["release"], "old")
                    (dist / "current.json").write_text(json.dumps({"release": "new"}), encoding="utf-8")
                    self.assertIn("new", urllib.request.urlopen(url + "/").read().decode())
                    body = json.load(urllib.request.urlopen(url + "/api/build-status"))
                    self.assertEqual((body["release"], body["state"]), ("new", "idle"))
                finally:
                    httpd.shutdown(); httpd.server_close()

    def test_missing_release_falls_back_to_dist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "apps/web/dist").mkdir(parents=True)
            (root / "apps/web/dist/current.json").write_text(json.dumps({"release": "gone"}), encoding="utf-8")
            with mock.patch.object(server, "ROOT", root):
                self.assertEqual(server.current_release(), (None, root / "apps/web/dist"))
