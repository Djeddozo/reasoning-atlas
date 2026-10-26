import json, os, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path
from services.atlas import versioning

ROOT = Path(__file__).resolve().parents[1]


class Fingerprints(unittest.TestCase):
    def test_service_and_client_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "services").mkdir()
            (root / "apps/web/src").mkdir(parents=True)
            code, screen = root / "services/a.py", root / "apps/web/src/main.tsx"
            code.write_text("x = 1\n"); screen.write_text("one")
            started, built = versioning.service_fingerprint(root), versioning.client_fingerprint(root)
            self.assertEqual(versioning.restart_status(started, built, root), {"needed": False, "reasons": []})
            code.write_text("x = 22\n")
            self.assertEqual(len(versioning.restart_status(started, built, root)["reasons"]), 1)
            screen.write_text("two!")
            self.assertEqual(len(versioning.restart_status(started, built, root)["reasons"]), 2)
            self.assertFalse(versioning.restart_status(versioning.service_fingerprint(root), None, root)["needed"])

    def test_pycache_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "services/__pycache__").mkdir(parents=True)
            before = versioning.service_fingerprint(root)
            (root / "services/__pycache__/a.cpython-312.py").write_text("cache")
            self.assertEqual(versioning.service_fingerprint(root), before)


def start(folder, supervised):
    env = {**os.environ, "ATLAS_SUPERVISED": "1" if supervised else "0"}
    proc = subprocess.Popen([sys.executable, "-m", "services.atlas.server", "--port", "0", "--db", folder + "/t.db"],
                            cwd=ROOT, stdout=subprocess.PIPE, text=True, env=env)
    return proc, proc.stdout.readline().strip().split()[-1]


def post(url):
    return urllib.request.urlopen(urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}))


class Restart(unittest.TestCase):
    def test_unsupervised_service_refuses_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            proc, url = start(folder, False)
            try:
                status = json.load(urllib.request.urlopen(url + "/api/build-status"))
                self.assertFalse(status["restart"]["canRestart"])
                self.assertIn("serverStartedAt", status)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    post(url + "/api/restart")
                self.assertEqual(ctx.exception.code, 409)
                self.assertIsNone(proc.poll())
            finally:
                proc.kill(); proc.wait(); proc.stdout.close()

    def test_supervised_service_exits_for_the_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            proc, url = start(folder, True)
            try:
                self.assertTrue(json.load(urllib.request.urlopen(url + "/api/build-status"))["restart"]["canRestart"])
                self.assertEqual(post(url + "/api/restart").status, 202)
                self.assertEqual(proc.wait(timeout=10), 3)
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.wait()
                proc.stdout.close()
