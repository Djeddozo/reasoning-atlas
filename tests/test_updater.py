"""Updater (install merged work safely) and Pi-only settings in config/local.toml."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import db, loop, updater

OK_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
BAD_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.fail('broken')\n"
GIT = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]


def sh(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.remote, self.pi, self.dev = self.tmp / "remote.git", self.tmp / "pi", self.tmp / "dev"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.remote), cwd=self.tmp)
        sh("git", "clone", "-q", str(self.remote), str(self.dev), cwd=self.tmp)
        sh("git", "checkout", "-q", "-b", "main", cwd=self.dev)
        self.commit("tests/__init__.py", "", "init")
        self.commit("tests/test_ok.py", OK_TEST, "tests")
        sh("git", "push", "-q", "-u", "origin", "main", cwd=self.dev)
        sh("git", "clone", "-q", str(self.remote), str(self.pi), cwd=self.tmp)
        p = mock.patch.object(updater, "ROOT", self.pi); p.start(); self.addCleanup(p.stop)
        r = mock.patch.object(updater, "restart"); self.restart = r.start(); self.addCleanup(r.stop)
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; self.addCleanup(self.con.close)
        db.init(self.con)

    def commit(self, path, text, msg, push=False):
        f = self.dev / path; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text)
        sh("git", "add", "-A", cwd=self.dev); sh(*GIT, "commit", "-qm", msg, cwd=self.dev)
        if push:
            sh("git", "push", "-q", "origin", "main", cwd=self.dev)

    def head(self):
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.pi, capture_output=True, text=True).stdout.strip()

    def test_up_to_date(self):
        self.assertEqual(updater.apply(self.con)["status"], "up_to_date")
        self.restart.assert_not_called()

    def test_update_installs_and_restarts(self):
        self.commit("hq_feature.py", "x = 1\n", "Plan as a tree", push=True)
        info = updater.check()
        self.assertEqual((info["behind"], info["commits"][0]["subject"], info["blocker"]), (1, "Plan as a tree", ""))
        # A real update runs this suite inside its own hq-update-*/tree.
        # Check only directories created by this test, not the outer checkout.
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def track_temp(**kwargs):
            path = real_mkdtemp(dir=self.tmp, **kwargs)
            created.append(Path(path))
            return path

        outer_tree = self.tmp / "hq-update-outer" / "tree"
        outer_tree.mkdir(parents=True)
        with mock.patch.object(updater.tempfile, "mkdtemp", side_effect=track_temp):
            result = updater.apply(self.con)
        self.assertEqual(result["status"], "updated", result)
        self.assertIn("• Plan as a tree", result["message"])
        self.assertTrue((self.pi / "hq_feature.py").exists())
        self.restart.assert_called_once()
        self.assertTrue(created, "the update must validate in a temporary checkout")
        self.assertTrue(all(not path.exists() for path in created), created)
        self.assertTrue(outer_tree.exists(), "unrelated update checkouts must be preserved")

    def test_installs_exactly_the_commit_that_was_tested(self):
        self.commit("hq_feature.py", "x = 1\n", "tested change", push=True)
        tested = subprocess.run(["git", "rev-parse", "origin/main"], cwd=self.dev, capture_output=True, text=True).stdout.strip()
        real = updater._tests_pass_on

        def tests_while_main_moves(ref):
            result = real(ref)
            self.commit("tests/test_bad.py", BAD_TEST, "untested change", push=True)   # lands during the test run
            sh("git", "fetch", "-q", "origin", cwd=self.pi)                             # e.g. a second updater fetched it
            return result
        with mock.patch.object(updater, "_tests_pass_on", tests_while_main_moves):
            self.assertEqual(updater.apply(self.con)["status"], "updated")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.pi, capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, tested)
        self.assertFalse((self.pi / "tests" / "test_bad.py").exists())

    def test_failing_tests_mean_nothing_is_installed(self):
        before = self.head()
        self.commit("tests/test_bad.py", BAD_TEST, "broken change", push=True)
        result = updater.apply(self.con)
        self.assertEqual(result["status"], "tests_failed")
        self.assertEqual(self.head(), before)
        self.restart.assert_not_called()

    def test_hand_made_changes_block_instead_of_being_overwritten(self):
        (self.pi / "tests" / "test_ok.py").write_text(OK_TEST + "# my local edit\n")
        self.commit("hq_feature.py", "x = 1\n", "new", push=True)
        result = updater.apply(self.con)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("tests/test_ok.py", result["message"])
        self.assertIn("# my local edit", (self.pi / "tests" / "test_ok.py").read_text())  # untouched

    def test_local_commits_block(self):
        (self.pi / "local.txt").write_text("x")
        sh("git", "add", "-A", cwd=self.pi); sh(*GIT, "commit", "-qm", "local", cwd=self.pi)
        self.commit("hq_feature.py", "x = 1\n", "new", push=True)
        self.assertIn("not on GitHub", updater.apply(self.con)["message"])

    def test_auto_tells_once_per_version(self):
        self.assertIsNone(updater.auto(self.con))  # nothing new: silent
        self.commit("tests/test_bad.py", BAD_TEST, "broken", push=True)
        self.assertIn("not installed", updater.auto(self.con))
        self.assertIsNone(updater.auto(self.con))  # same version: not again every 15 minutes
        self.commit("tests/test_bad.py", OK_TEST, "fixed", push=True)
        self.assertIn("Atlas updated", updater.auto(self.con))

    def age(self, path, minutes=60):
        old = __import__("time").time() - minutes * 60
        _os.utime(path, (old, old))

    def branches(self):
        return subprocess.run(["git", "branch", "--list", "hq-backup/*", "--format=%(refname:short)"], cwd=self.pi,
                              capture_output=True, text=True).stdout.split()

    def test_auto_repairs_a_blocked_update_without_losing_changes(self):
        f = self.pi / "tests" / "test_ok.py"
        f.write_text(OK_TEST + "# my local edit\n"); self.age(f)
        (self.pi / "local.txt").write_text("x")
        sh("git", "add", "local.txt", cwd=self.pi); sh(*GIT, "commit", "-qm", "local", cwd=self.pi)
        self.commit("hq_feature.py", "x = 1\n", "new", push=True)
        msg = updater.auto(self.con)
        self.assertIn("Atlas updated", msg)
        self.assertTrue((self.pi / "hq_feature.py").exists())
        [branch] = self.branches()
        self.assertIn(branch, msg)
        saved = subprocess.run(["git", "show", f"{branch}:tests/test_ok.py"], cwd=self.pi, capture_output=True, text=True).stdout
        self.assertIn("# my local edit", saved)  # the hand edit is kept on the backup branch
        self.assertEqual(subprocess.run(["git", "show", f"{branch}:local.txt"], cwd=self.pi, capture_output=True,
                                        text=True).stdout, "x")  # and so is the local commit
        note = self.con.execute("SELECT content FROM memories WHERE kind = 'info'").fetchone()[0]
        self.assertIn(branch, note)

    def test_recent_hand_edits_are_left_alone(self):
        (self.pi / "tests" / "test_ok.py").write_text(OK_TEST + "# editing now\n")
        self.commit("hq_feature.py", "x = 1\n", "new", push=True)
        self.assertIsNone(updater.auto(self.con))  # no message, no move: he may be mid-edit
        self.assertIn("# editing now", (self.pi / "tests" / "test_ok.py").read_text())
        self.assertEqual(self.branches(), [])

    def test_failing_update_tests_are_retried_once(self):
        self.commit("hq_feature.py", "x = 1\n", "new", push=True)
        results = iter([(False, "flaky"), (True, "")])
        with mock.patch.object(updater, "_tests_pass_on", lambda ref: next(results)):
            self.assertIn("Atlas updated", updater.auto(self.con))  # repaired: no failure message

    def repair_tasks(self):
        return self.con.execute("SELECT * FROM tasks WHERE title = ?", (updater.REPAIR_TITLE,)).fetchall()

    def test_failing_tests_on_main_queue_one_ai_repair_task_with_the_output(self):
        self.commit("tests/test_bad.py", BAD_TEST, "broken", push=True)
        msg = updater.auto(self.con)
        [task] = self.repair_tasks()
        self.assertEqual((task["owner"], task["status"]), ("ai", "todo"))
        self.assertIn(f"#{task['id']}", msg)
        self.assertIn("broken", task["description"])          # the failing assertion reaches the agent
        self.assertIn("test_bad", task["description"])
        self.assertIsNone(updater.auto(self.con))             # same version: no second task, no second message
        self.assertEqual(len(self.repair_tasks()), 1)
        self.commit("tests/test_bad2.py", BAD_TEST.replace("test_bad", "test_worse"), "more broken", push=True)
        updater.auto(self.con)                                 # newer broken version: the open task is refreshed
        [task] = self.repair_tasks()
        self.assertIn("test_worse", task["description"])

    def test_a_flaky_failure_queues_no_repair_task(self):
        self.commit("hq_feature.py", "x = 1\n", "new", push=True)
        results = iter([(False, "flaky"), (True, "")])
        with mock.patch.object(updater, "_tests_pass_on", lambda ref: next(results)):
            updater.auto(self.con)
        self.assertEqual(self.repair_tasks(), [])

    def test_a_done_repair_task_is_not_reused(self):
        old = updater.queue_repair(self.con, "abc1234", "old output")
        db.update(self.con, "tasks", old, status="done")
        new = updater.queue_repair(self.con, "def5678", "new output")
        self.assertNotEqual(old, new)


class LocalSettingsTest(unittest.TestCase):
    def test_local_toml_is_merged_over_the_constitution(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "local.toml"
            local.write_text('[devloop]\nenabled = true\nmax_runs_per_day = 5\n'
                             '[[gatekeeper.deny]]\ntool = "Bash"\npattern = "x"\nwhy = "local rule"\n'
                             '[models]\ncloud_usd_per_mtok_in = 3\n')
            base = loop.load_constitution()
            with mock.patch.object(loop, "LOCAL", local):
                merged = loop.load_constitution()
        self.assertEqual(merged["devloop"]["max_runs_per_day"], 5)
        self.assertEqual(len(merged["gatekeeper"]["deny"]), len(base["gatekeeper"]["deny"]) + 1)  # appended
        self.assertEqual(merged["models"]["cloud_usd_per_mtok_in"], 3)
        self.assertEqual(merged["models"]["cloud_model"], base["models"]["cloud_model"])  # rest kept

    def test_tests_ignore_this_pis_local_toml(self):
        """A Pi with enable-devloop.sh's extra deny rules must still pass the suite (they deny e.g. git push)."""
        self.assertFalse(loop.LOCAL.exists())  # set at the top of every test module

    def test_no_environment_variable_can_drop_this_pis_deny_rules(self):
        """The dev loop's cron sources .env; nothing in the environment may switch local.toml's deny rules off."""
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "local.toml"
            local.write_text('[[gatekeeper.deny]]\ntool = "Bash"\npattern = "git push"\nwhy = "pi rule"\n')
            with mock.patch.object(loop, "LOCAL", local), \
                 mock.patch.dict(_os.environ, {"HQ_LOCAL_TOML": "/nonexistent", "HQ_CONSTITUTION": "/nonexistent"}):
                deny = loop.load_constitution()["gatekeeper"]["deny"]
        self.assertTrue(any(r.get("why") == "pi rule" for r in deny))

    def test_off_overrides_an_old_enabled_setting_in_the_constitution(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"; (repo / "config").mkdir(parents=True); (repo / "deploy").mkdir()
            (repo / "deploy" / "enable-devloop.sh").write_text((root / "deploy" / "enable-devloop.sh").read_text())
            c = repo / "config" / "constitution.toml"
            c.write_text((root / "config" / "constitution.toml").read_text() + "\n[devloop]\nenabled = true\n")
            bindir = Path(d) / "bin"; bindir.mkdir()
            (bindir / "crontab").write_text("#!/bin/sh\ncat >/dev/null\n"); (bindir / "crontab").chmod(0o755)
            r = subprocess.run(["bash", "deploy/enable-devloop.sh", "--off"], cwd=repo, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL,  # the fake crontab reads stdin: never wait on the terminal
                               env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": d}, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            with mock.patch.object(loop, "CONSTITUTION", c), mock.patch.object(loop, "LOCAL", repo / "config" / "local.toml"):
                self.assertFalse(loop.load_constitution()["devloop"]["enabled"])

    def test_enable_script_moves_old_settings_out_of_the_constitution(self):
        """An older enable-devloop.sh appended to constitution.toml; the new one moves that to local.toml."""
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            sh("git", "clone", "-q", str(root), str(repo), cwd=d)
            # the working copy as it is now (new modules too), not only what is committed
            for f in ["deploy/enable-devloop.sh", ".gitignore"] + [str(p.relative_to(root)) for p in root.glob("engine/**/*.py")]:
                (repo / f).parent.mkdir(parents=True, exist_ok=True)
                (repo / f).write_text((root / f).read_text())
            for f in ("atlas.sh", "hq.sh"):
                (repo / f).write_text((root / f).read_text()); (repo / f).chmod(0o755)
            c = repo / "config" / "constitution.toml"
            original = c.read_text()
            c.write_text(original.rstrip("\n") + '\n\n[devloop]\nenabled = true\nmax_runs_per_day = 3\n\n'
                         '[[gatekeeper.deny]]\ntool = "Bash"\npattern = "git push"\n'
                         'why = "agents commit only; Atlas pushes branches after tests pass and Djeddi merges"\n')
            bindir = Path(d) / "bin"; bindir.mkdir()
            (bindir / "claude").write_text("#!/bin/sh\n"); (bindir / "claude").chmod(0o755)
            (bindir / "crontab").write_text("#!/bin/sh\ncat >/dev/null\n"); (bindir / "crontab").chmod(0o755)
            (repo / ".env").write_text("")
            env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": d, "HQ_DB": str(Path(d) / "t.db")}
            r = subprocess.run(["bash", "deploy/enable-devloop.sh"], cwd=repo, input="y\n", capture_output=True,
                               text=True, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Moved the dev-loop settings out", r.stdout)
            self.assertEqual(c.read_text(), original)                       # matches GitHub again
            local = (repo / "config" / "local.toml").read_text()
            self.assertIn("[devloop]", local)
            self.assertIn("only Djeddi controls the dev loop, updates", local)
            git_status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo,
                                        capture_output=True, text=True).stdout
            self.assertNotIn("config/constitution.toml", git_status)  # nothing left to block updates


if __name__ == "__main__":
    unittest.main()
