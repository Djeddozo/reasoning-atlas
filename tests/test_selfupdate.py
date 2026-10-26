import json, subprocess, tempfile, threading, unittest, urllib.request
from pathlib import Path
from unittest import mock
from services.atlas import hub, selfupdate, server
from engine import updater
from tests.test_hub import TOKEN, engine_database, get, post


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class SelfUpdate(unittest.TestCase):
    """This computer's copy: fast-forward only, never over hand-made changes."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.origin, self.pc = base / "origin.git", base / "pc"
        git(base, "init", "-q", "--bare", "-b", "main", str(self.origin))
        seed = base / "seed"
        git(base, "clone", "-q", str(self.origin), str(seed))
        for k, v in (("user.email", "t@example.com"), ("user.name", "Test")):
            git(seed, "config", k, v)
        (seed / "a.txt").write_text("one\n")
        git(seed, "add", "."); git(seed, "commit", "-qm", "first"); git(seed, "push", "-q", "origin", "HEAD:main")
        git(base, "clone", "-q", str(self.origin), str(self.pc))
        (seed / "a.txt").write_text("two\n")
        git(seed, "commit", "-qam", "second"); git(seed, "push", "-q", "origin", "HEAD:main")

    def tearDown(self):
        self.tmp.cleanup()

    def test_realigns_after_github_history_was_rewritten(self):
        """GitHub's history rewritten (e.g. to publish it): the same commits under new ids are not 'waiting'."""
        base = Path(self.tmp.name)
        selfupdate.apply(self.pc)  # the PC has first + second
        (self.pc / "data.db").write_text("private")  # ignored data must survive
        (self.pc / ".gitignore").write_text("*.db\n"); git(self.pc, "add", ".gitignore")
        for k, v in (("user.email", "t@example.com"), ("user.name", "Test")):
            git(self.pc, "config", k, v)
        git(self.pc, "commit", "-qm", "ignore data"); git(self.pc, "push", "-q", "origin", "HEAD:main")
        # rewrite every commit id while keeping subjects and author times, as a history-scrubbing tool does
        rewrite2 = base / "rewrite2"
        git(base, "clone", "-q", str(self.origin), str(rewrite2))
        git(rewrite2, "-c", "user.email=t@example.com", "-c", "user.name=Test", "filter-branch", "-f", "--msg-filter", "cat",
            "--env-filter", "export GIT_COMMITTER_NAME=Rewriter", "HEAD")
        (rewrite2 / "b.txt").write_text("new on GitHub\n"); git(rewrite2, "add", "b.txt")
        git(rewrite2, "-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-qm", "really new")
        git(rewrite2, "push", "-q", "-f", "origin", "HEAD:main")
        info = selfupdate.check(self.pc)
        self.assertEqual((info["realign"], info["blocker"], [c["subject"] for c in info["commits"]]), (True, "", ["really new"]))
        installed, message = selfupdate.apply(self.pc)
        self.assertTrue(installed, message); self.assertIn("backup/before-realign-", message)
        self.assertEqual((self.pc / "b.txt").read_text(), "new on GitHub\n")
        self.assertEqual((self.pc / "data.db").read_text(), "private")
        self.assertFalse(selfupdate.check(self.pc)["behind"])

    def test_truly_local_commits_still_block(self):
        selfupdate.apply(self.pc)
        for k, v in (("user.email", "t@example.com"), ("user.name", "Test")):
            git(self.pc, "config", k, v)
        (self.pc / "a.txt").write_text("mine\n"); git(self.pc, "commit", "-qam", "my own work")
        info = selfupdate.check(self.pc)
        self.assertFalse(info["realign"])
        self.assertEqual(info["behind"], 0)

    def test_check_and_apply(self):
        self.assertEqual(selfupdate.status(self.pc)["behind"], 0)  # nothing fetched yet
        info = selfupdate.check(self.pc)
        self.assertEqual((info["behind"], info["commits"][0]["subject"], info["blocker"]), (1, "second", ""))
        installed, message = selfupdate.apply(self.pc)
        self.assertTrue(installed, message)
        self.assertEqual((self.pc / "a.txt").read_text(), "two\n")
        self.assertEqual(selfupdate.apply(self.pc)[0], False)

    def test_hand_made_changes_block(self):
        (self.pc / "a.txt").write_text("mine\n")
        installed, message = selfupdate.apply(self.pc)
        self.assertFalse(installed)
        self.assertIn("changed by hand on this computer: a.txt.", message)
        self.assertEqual((self.pc / "a.txt").read_text(), "mine\n")


class PiUpdateThroughHub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = engine_database(self.tmp.name)
        self.state = mock.patch.object(hub, "UPDATE_STATE", Path(self.tmp.name) / "hub-update.json")
        self.state.start()
        self.server = hub.make_server("127.0.0.1", 0, TOKEN, self.db)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        self.state.stop()
        self.tmp.cleanup()

    def test_status_check_and_update(self):
        with mock.patch.object(updater, "local_status", return_value={"behind": 2}):
            status = get(self.url + "functions/engine.system.update_status")["result"]
        self.assertEqual((status["behind"], status["running"]), (2, False))
        with mock.patch.object(updater, "check", return_value={"behind": 2, "blocker": ""}):
            self.assertIn("2 new change", post(self.url + "actions/engine.system.updates", {"check": True})["message"])
        started = []
        with mock.patch.object(hub, "_start_update_runner", side_effect=lambda: started.append(1)):
            self.assertIn("tests run first", post(self.url + "actions/engine.system.updates", {})["message"])
            self.assertIn("already running", post(self.url + "actions/engine.system.updates", {})["message"])
        self.assertEqual(started, [1])
        # the runner itself: Atlas's updater, result reported for the screen
        restarts = []
        with mock.patch.object(hub, "engine_db", return_value=self.db), \
             mock.patch.object(updater, "apply", return_value={"status": "updated", "message": "Atlas updated a → b", "target": "b"}) as apply, \
             mock.patch.object(hub, "restart_and_wait", side_effect=lambda: restarts.append(1)):
            hub.run_update()
        self.assertEqual((apply.call_args.kwargs, restarts), ({"do_restart": False}, [1]))  # restart waited for, not detached
        # merged PRs now in the running code leave 'Update installs it' at once (local check, no GitHub call)
        from engine import delivery
        with mock.patch.object(hub, "engine_db", return_value=self.db), \
             mock.patch.object(updater, "apply", return_value={"status": "updated", "message": "m", "target": "b"}), \
             mock.patch.object(delivery, "sync") as sync, mock.patch.object(hub, "restart_and_wait"):
            hub.run_update()
        self.assertEqual(sync.call_args.kwargs, {"remote": False})
        last = json.loads(Path(hub.UPDATE_STATE).read_text())
        self.assertEqual((last["state"], last["target"]), ("updated", "b"))
        with mock.patch.object(hub, "engine_db", return_value=self.db), \
             mock.patch.object(updater, "apply", side_effect=RuntimeError("git broke")):
            hub.run_update()
        last = json.loads(Path(hub.UPDATE_STATE).read_text())
        self.assertEqual(last["state"], "failed")
        self.assertIn("nothing was installed", last["message"])
        # installed, then a later step failed: the restart still happens
        heads = iter([("main", "old"), ("main", "new")])
        restarted = []
        with mock.patch.object(hub, "engine_db", return_value=self.db), mock.patch.object(hub, "git_version", side_effect=lambda: next(heads)), \
             mock.patch.object(updater, "apply", side_effect=__import__("sqlite3").OperationalError("database is locked")), \
             mock.patch.object(hub, "restart_and_wait", side_effect=lambda: restarted.append(1)):
            hub.run_update()
        last = json.loads(Path(hub.UPDATE_STATE).read_text())
        self.assertEqual((last["state"], restarted), ("updated", [1]))
        self.assertIn("installed old → new and restarted", last["message"])


class AtlasUpdateEndpoints(unittest.TestCase):
    def test_status_without_hub(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(server.hubclient, "status", return_value={"reachable": False}), \
             mock.patch.object(server.selfupdate, "status", return_value={"behind": 1, "blocker": ""}), \
             mock.patch.object(server.selfupdate, "apply", return_value=(True, "Updated this computer a → b (1 change(s)).")):
            atlas = server.make_server(0, d + "/atlas.db")
            threading.Thread(target=atlas.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{atlas.server_port}"
            try:
                body = json.load(urllib.request.urlopen(url + "/api/update/status"))
                self.assertEqual((body["pc"]["behind"], body["pi"]), (1, None))
                req = urllib.request.Request(url + "/api/update/apply", data=b"{}", headers={"Content-Type": "application/json"})
                out = json.load(urllib.request.urlopen(req))
                self.assertFalse(out["restarting"])  # not started by Start Atlas.cmd
                self.assertIn("Start Atlas.cmd", out["messages"][-1])
                self.assertIn("Pi: not reachable", out["messages"][0])
            finally:
                atlas.shutdown(); atlas.server_close()


class StaleBuildBar(unittest.TestCase):
    def test_finished_task_is_not_shown_as_paused(self):
        from services.atlas import progress
        with tempfile.TemporaryDirectory() as d:
            status = Path(d) / "s.json"
            status.write_text(json.dumps({"state": "building", "taskId": "RA-035", "updatedAt": "2026-10-20T08:00:00Z"}))
            self.assertEqual(progress.read(status, hide_finished=True)["state"], "idle")  # RA-035 is done in STATE/tasks.json
            status.write_text(json.dumps({"state": "building", "taskId": "RA-999", "updatedAt": "2026-10-20T08:00:00Z"}))
            self.assertEqual(progress.read(status, hide_finished=True)["state"], "stalled")


class HubFollowsUpdates(unittest.TestCase):
    def test_stops_when_disk_is_newer(self):
        server = hub.make_server("127.0.0.1", 0, TOKEN, Path(tempfile.gettempdir()) / "absent.db")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        commits = iter(["aaa", "bbb"])
        hub.follow_updates(server, "aaa", every=0.05, head=lambda: next(commits, "bbb"))
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())  # stopped: systemd starts it again on the new code
        server.server_close()

    def test_health_reports_running_commit(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(hub, "git_version", return_value=("main", "older")):  # the commit this process started on
                server = hub.make_server("127.0.0.1", 0, TOKEN, engine_database(d))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with mock.patch.object(hub, "git_version", return_value=("main", "newer")):
                    health = get(f"http://127.0.0.1:{server.server_port}/v1/health")
                self.assertEqual((health["commit"], health["restartNeeded"]), ("newer", True))
            finally:
                server.shutdown(); server.server_close()
