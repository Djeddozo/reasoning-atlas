"""Dev loop, kill switch and usage tracking. Uses a real throwaway git repo and a fake `claude`."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from engine import db, devloop, loop, models, routing, usage

FAKE_CLAUDE = textwrap.dedent('''\
    #!{python}
    import json, os, subprocess, sys
    mode = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode")).read().strip()
    assert "ANTHROPIC_API_KEY" not in os.environ and "TELEGRAM_BOT_TOKEN" not in os.environ, "secrets leaked"
    if mode == "hang":
        import time; time.sleep(120)  # silent: no output at all
    if "merge stopped with conflicts" in sys.argv[2]:  # a Resolve run: settle the conflicts, conclude the merge
        open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls"), "a").write("resolve\\n")
        if mode != "giveup":
            for f in subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"], capture_output=True, text=True).stdout.split():
                open(f, "w").write("x = 3\\n")
            subprocess.run(["git", "add", "-A"], check=True)
            subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=agent", "commit", "-q", "--no-edit"], check=True)
        print(json.dumps({{"type": "result", "result": "Resolved.", "is_error": False, "num_turns": 3, "total_cost_usd": 0.1}}))
        sys.exit(0)
    repair = "did not pass Atlas's checks" in sys.argv[2]
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls"), "a").write("repair\\n" if repair else "run\\n")
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"), "a").write(
        (sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "default") + "\\n")
    if mode == "fixable":
        mode = "fix" if repair else "badtest"
    if mode == "crash":
        mode = "ok" if repair else "error"
    print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "Bash", "input": {{"command": "ls -la"}}}}]}}}}))
    if mode in ("ok", "badtest", "fix"):
        body = "import unittest\\nclass T(unittest.TestCase):\\n    def test_x(self):\\n        assert {ok}\\n" if mode != "ok" else "x = 1\\n"
        name = "tests/test_new.py" if mode != "ok" else next(f"feature{{i}}.py" for i in range(1, 99) if not os.path.exists(f"feature{{i}}.py"))
        open("prompt.txt", "w").write(sys.argv[2])
        open(name, "w").write(body.replace("{ok}", "True" if mode == "fix" else "False"))
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=agent", "commit", "-qm", "agent work"], check=True)
    print(json.dumps({{"type": "result", "result": "Built it.\\nTest: run x.\\nNeeds: nothing.", "is_error": mode == "error",
                      "num_turns": 7, "total_cost_usd": 0.42,
                      "usage": {{"input_tokens": 1000, "cache_read_input_tokens": 500, "output_tokens": 300}}}}))
''')


def sh(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class DevLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        remote, repo = self.tmp / "remote.git", self.tmp / "repo"
        sh("git", "init", "-q", "--bare", "-b", "main", str(remote), cwd=self.tmp)
        sh("git", "init", "-q", "-b", "main", str(repo), cwd=self.tmp)
        (repo / "tests").mkdir()
        (repo / "tests" / "__init__.py").write_text("")
        (repo / "tests" / "test_ok.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
        for cmd in (["git", "add", "-A"], ["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "init"],
                    ["git", "remote", "add", "origin", str(remote)], ["git", "push", "-q", "-u", "origin", "main"]):
            sh(*cmd, cwd=repo)
        self.repo, self.remote = repo, remote
        self.github = None  # GitHub's live PR state (github_prs); None = gh not reachable
        bindir = self.tmp / "bin"; bindir.mkdir()
        self.claude = bindir / "claude"
        self.claude.write_text(FAKE_CLAUDE.format(python=sys.executable, ok="{ok}"))
        self.claude.chmod(0o755)
        self.mode("ok")

        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        self.task = db.insert(self.con, "tasks", title="Add a feature flag", owner="ai", description="small")
        db.insert(self.con, "tasks", title="Something for Djeddi", owner="me")
        self.con.commit()
        conf = devloop.DEFAULTS | {"enabled": True, "claude_command": str(self.claude), "max_minutes": 2}
        work = repo / ".devloop"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM", "ANTHROPIC"))}
        env.update(ANTHROPIC_API_KEY="secret", GIT_AUTHOR_NAME="hq", GIT_AUTHOR_EMAIL="hq@x",
                   GIT_COMMITTER_NAME="hq", GIT_COMMITTER_EMAIL="hq@x")
        for patcher in (mock.patch.object(devloop, "ROOT", repo), mock.patch.object(devloop, "WORK", work),
                        mock.patch.object(devloop, "LOCK", work / "lock"), mock.patch.object(devloop, "cfg", lambda: conf),
                        mock.patch.object(usage, "KILL_FILE", self.tmp / "KILL"),
                        mock.patch.object(devloop, "github_prs", lambda: self.github),
                        mock.patch.dict(os.environ, env, clear=True)):
            patcher.start(); self.addCleanup(patcher.stop)
        (repo / ".git" / "info" / "exclude").write_text(".devloop/\n")
        self.conf = conf

    def mode(self, m):
        (self.claude.parent / "mode").write_text(m)

    def run_loop(self, **kw):
        return devloop.run(self.con, **kw)

    def test_off_by_default(self):
        with mock.patch.object(devloop, "cfg", lambda: devloop.DEFAULTS):
            self.assertEqual(self.run_loop()["status"], "disabled")

    def test_full_run_opens_a_pr_and_records_usage(self):
        result = self.run_loop()
        self.assertEqual(result["status"], "pr_open", result)
        self.assertEqual(result["task"], self.task)
        branch = result["branch"]
        self.assertTrue(branch.startswith(f"hq-dev/{self.task}-add-a-feature-flag-"))
        self.assertIn(branch, subprocess.run(["git", "branch", "--list"], cwd=self.remote, capture_output=True, text=True).stdout)
        self.assertEqual(subprocess.run(["git", "rev-parse", "main"], cwd=self.remote, capture_output=True, text=True).stdout,
                         subprocess.run(["git", "rev-parse", "origin/main"], cwd=self.repo, capture_output=True, text=True).stdout)
        row = self.con.execute("SELECT * FROM dev_runs").fetchone()
        self.assertEqual((row["turns"], row["tokens_in"], row["tokens_out"], row["cost_usd"]), (7, 1500, 300, 0.42))
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "doing")
        self.assertIn("→ Bash: ls -la", "\n".join(devloop.tail(row["id"])))
        self.assertFalse(list((self.repo / ".devloop").glob("wt-*")))  # worktree cleaned up, branch kept
        self.assertIn("ready for review", devloop.report(result))
        self.assertIn("~$0.42", devloop.report(result))
        # the same task is not picked again while its PR is open
        self.assertEqual(self.run_loop()["status"], "idle")
        # Djeddi merges the branch → the next run marks the task done
        sh("git", "fetch", "-q", "origin", branch, cwd=self.repo)
        sh("git", "push", "-q", "origin", f"origin/{branch}:refs/heads/main", cwd=self.repo)
        self.assertEqual(self.run_loop()["merged"], [self.task])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "done")

    def test_failing_tests_mean_no_push(self):
        self.mode("badtest")
        result = self.run_loop()
        self.assertEqual(result["status"], "tests_failed", result)
        self.assertNotIn("hq-dev/", subprocess.run(["git", "branch", "--list"], cwd=self.remote, capture_output=True, text=True).stdout)
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "todo")

    def calls(self):
        f = self.claude.parent / "calls"
        return f.read_text().split() if f.exists() else []

    def test_failing_tests_get_one_repair_run(self):
        self.mode("fixable")
        result = self.run_loop()
        self.assertEqual(result["status"], "pr_open", result)
        self.assertTrue(result["repaired"])
        self.assertEqual(self.calls(), ["run", "repair"])
        self.assertIn("automatic repair", result["summary"])
        row = self.con.execute("SELECT turns, cost_usd FROM dev_runs").fetchone()
        self.assertEqual((row["turns"], round(row["cost_usd"], 2)), (14, 0.84))  # both runs counted
        self.assertIn("self-repair", "\n".join(devloop.tail(result["run"])))

    def test_model_is_routed_logged_and_escalated_for_repair(self):
        self.mode("fixable")
        with mock.patch.object(routing, "cfg", lambda: routing.DEFAULTS):
            result = self.run_loop()
        self.assertEqual(result["status"], "pr_open", result)
        models_used = (self.claude.parent / "models").read_text().split()
        self.assertEqual(models_used, ["sonnet", "opus"])  # ordinary task; the repair run gets the stronger model
        row = self.con.execute("SELECT model, model_reason FROM dev_runs").fetchone()
        self.assertEqual(row["model"], "opus")
        self.assertIn("repair", row["model_reason"])
        logged = [r[0] for r in self.con.execute("SELECT detail FROM log WHERE action = 'devloop_model'")]
        self.assertEqual(len(logged), 2)
        self.assertIn("sonnet (standard)", logged[0])
        self.assertIn("model: sonnet", "\n".join(devloop.tail(result["run"])))
        self.assertIn("opus", devloop.status(self.con))

    def test_start_and_finish_messages_name_the_model_and_why(self):
        self.mode("fixable")
        with mock.patch.object(routing, "cfg", lambda: routing.DEFAULTS), \
                mock.patch.object(devloop, "notify") as notify:
            result = self.run_loop()
        started = notify.call_args_list[0].args[0]
        self.assertIn("Model: sonnet: ordinary task", started)  # the first choice, before the agent starts
        self.assertEqual(result["model"], "opus: repair after a failed standard attempt")
        self.assertIn("Model: opus: repair after a failed standard attempt", devloop.report(result))

    def test_routing_off_uses_claude_codes_default(self):
        with mock.patch.object(routing, "cfg", lambda: routing.DEFAULTS | {"enabled": False}):
            self.assertEqual(self.run_loop()["status"], "pr_open")
        self.assertEqual((self.claude.parent / "models").read_text().split(), ["default"])

    def test_crashed_agent_gets_one_repair_run(self):
        self.mode("crash")
        result = self.run_loop()
        self.assertEqual(result["status"], "pr_open", result)
        self.assertEqual(self.calls(), ["run", "repair"])

    def test_failed_repair_is_reported_once(self):
        self.mode("badtest")
        result = self.run_loop()
        self.assertEqual(result["status"], "tests_failed", result)
        self.assertEqual(self.calls(), ["run", "repair"])  # exactly one repair, then stop
        self.assertIn("also not after one automatic repair run", devloop.report(result))

    def test_no_repair_when_the_agent_chose_to_make_no_changes(self):
        self.mode("nothing")
        self.assertEqual(self.run_loop()["status"], "no_changes")
        self.assertEqual(self.calls(), ["run"])

    def test_no_changes_and_two_failures_skip_the_task(self):
        self.conf["max_runs_per_day"] = 10
        self.mode("nothing")
        self.assertEqual(self.run_loop()["status"], "no_changes")
        self.mode("badtest")
        self.assertEqual(self.run_loop()["status"], "tests_failed")
        self.assertEqual(self.run_loop()["status"], "idle")  # two attempts: left for Djeddi

    def test_task_the_agent_cannot_do_is_not_retried_forever(self):
        self.conf["max_runs_per_day"] = 10
        self.mode("nothing")
        self.assertEqual(self.run_loop()["status"], "no_changes")
        self.assertEqual(self.run_loop()["status"], "no_changes")
        self.assertEqual(self.run_loop()["status"], "idle")

    def test_time_limit_holds_even_when_the_agent_is_silent(self):
        self.mode("hang")
        self.conf["max_minutes"] = 0
        with mock.patch.object(devloop, "WATCH_SECONDS", 0.2):
            t0 = __import__("time").monotonic()
            result = self.run_loop()
        self.assertLess(__import__("time").monotonic() - t0, 30)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("time limit", result["summary"])

    def test_kill_switch_stops_a_silent_agent(self):
        import threading, time
        self.mode("hang")
        threading.Timer(1.0, lambda: usage.KILL_FILE.write_text("x")).start()
        with mock.patch.object(devloop, "WATCH_SECONDS", 0.2):
            t0 = time.monotonic()
            result = self.run_loop()
        self.assertLess(time.monotonic() - t0, 30)
        self.assertEqual(result["status"], "stopped", result)

    def second_task(self):
        t = db.insert(self.con, "tasks", title="Second feature", owner="ai", description="next")
        self.con.commit()
        return t

    def on_branch(self, branch, path):
        return subprocess.run(["git", "cat-file", "-e", f"{branch}:{path}"], cwd=self.repo, capture_output=True).returncode == 0

    def test_batch_above_one_stacks_the_next_branch_on_the_open_pr(self):
        second = self.second_task()
        devloop.set_batch(self.con, 3)
        first = self.run_loop(task_id=self.task)
        second_run = self.run_loop(task_id=second)
        self.assertEqual(second_run["status"], "pr_open", second_run)
        self.assertTrue(self.on_branch(second_run["branch"], "feature1.py"))  # the earlier unmerged work is included
        self.assertTrue(self.on_branch(second_run["branch"], "feature2.py"))
        row = self.con.execute("SELECT base_run FROM dev_runs WHERE id = ?", (second_run["run"],)).fetchone()
        self.assertEqual(row["base_run"], first["run"])
        self.assertIn(f"stacked on the unmerged work of task #{self.task}",
                      subprocess.run(["git", "show", f"{second_run['branch']}:prompt.txt"], cwd=self.repo,
                                     capture_output=True, text=True).stdout)
        # Djeddi squash-merges only the newest PR → both tasks are done
        dev = self.tmp / "dev"
        sh("git", "clone", "-q", str(self.remote), str(dev), cwd=self.tmp)
        sh("git", "fetch", "-q", "origin", second_run["branch"], cwd=dev)
        sh("git", "merge", "-q", "--squash", "FETCH_HEAD", cwd=dev)
        sh("git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "squash", cwd=dev)
        sh("git", "push", "-q", "origin", "main", cwd=dev)
        self.assertEqual(sorted(devloop.sync_merged(self.con)), sorted([self.task, second]))
        self.assertEqual(devloop.open_prs(self.con), 0)

    def test_batch_of_one_starts_from_main(self):
        second = self.second_task()
        devloop.set_batch(self.con, 1)
        first = self.run_loop(task_id=self.task)
        result = self.run_loop(task_id=second, force=True)
        self.assertEqual(result["status"], "pr_open", result)
        self.assertNotEqual(subprocess.run(["git", "merge-base", "--is-ancestor", first["branch"], result["branch"]],
                                           cwd=self.repo).returncode, 0)  # not stacked: only its own work
        self.assertIsNone(self.con.execute("SELECT base_run FROM dev_runs WHERE id = ?", (result["run"],)).fetchone()[0])

    def test_conflict_with_main_falls_back_to_main(self):
        second = self.second_task()
        first = self.run_loop(task_id=self.task)
        dev = self.tmp / "dev"  # main gets a different feature1.py meanwhile
        sh("git", "clone", "-q", str(self.remote), str(dev), cwd=self.tmp)
        (dev / "feature1.py").write_text("x = 2\n")
        sh("git", "add", "-A", cwd=dev)
        sh("git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "other", cwd=dev)
        sh("git", "push", "-q", "origin", "main", cwd=dev)
        result = self.run_loop(task_id=second)
        self.assertEqual(result["status"], "pr_open", result)
        self.assertIsNone(self.con.execute("SELECT base_run FROM dev_runs WHERE id = ?", (result["run"],)).fetchone()[0])
        self.assertNotEqual(first["branch"], result["branch"])
        self.assertTrue(self.on_branch(result["branch"], "feature2.py"))

    def main_changes(self, path="feature1.py", text="x = 2\n"):
        dev = self.tmp / "dev"
        if not dev.exists():
            sh("git", "clone", "-q", str(self.remote), str(dev), cwd=self.tmp)
        sh("git", "pull", "-q", "origin", "main", cwd=dev)
        (dev / path).write_text(text)
        sh("git", "add", "-A", cwd=dev)
        sh("git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "main moves on", cwd=dev)
        sh("git", "push", "-q", "origin", "main", cwd=dev)

    def remote_head(self, branch):
        return subprocess.run(["git", "rev-parse", f"refs/heads/{branch}"], cwd=self.remote, capture_output=True, text=True).stdout.strip()

    def test_conflict_is_detected_and_resolved_in_the_same_pr(self):
        first = self.run_loop()
        self.assertEqual(devloop.check_conflicts(self.con, fetch=True), [])
        self.main_changes()
        found = devloop.check_conflicts(self.con, fetch=True)
        self.assertEqual(found, [{"run": first["run"], "task_id": self.task, "files": ["feature1.py"]}])
        self.assertEqual([c["files"] for c in devloop.overview(self.con)["conflicts"]], [["feature1.py"]])
        self.assertIn(f"hq dev resolve {first['run']}", devloop.status(self.con))
        self.assertEqual(devloop.why_not_resolve(self.con, first["run"]), "")
        result = devloop.resolve(self.con, first["run"])
        self.assertEqual(result["status"], "resolved", result)
        self.assertIn("resolved the merge conflicts", devloop.report(result))
        row = self.con.execute("SELECT head, conflict, status FROM dev_runs WHERE id = ?", (first["run"],)).fetchone()
        self.assertEqual((row["conflict"], row["status"]), ("", "pr_open"))  # the same PR, now clean
        self.assertEqual(self.remote_head(first["branch"]), row["head"])     # pushed to the same branch
        self.assertEqual(devloop.conflict_files(row["head"]), [])
        fix = self.con.execute("SELECT status, resolves FROM dev_runs WHERE id = ?", (result["run"],)).fetchone()
        self.assertEqual((fix["status"], fix["resolves"]), ("resolved", first["run"]))
        self.assertEqual(devloop.open_prs(self.con), 1)
        self.assertEqual(devloop.conflicts(self.con), [])

    def test_conflict_is_noticed_when_no_run_may_start(self):
        first = self.run_loop()
        self.main_changes()
        self.conf["max_runs_per_day"] = 0  # today's limit is reached: the check still happens
        self.assertEqual(self.run_loop()["status"], "limit")
        self.assertEqual([c["id"] for c in devloop.conflicts(self.con)], [first["run"]])

    def test_unresolved_conflict_leaves_the_pr_unchanged(self):
        first = self.run_loop()
        self.main_changes()
        devloop.check_conflicts(self.con, fetch=True)
        before = self.remote_head(first["branch"])
        self.mode("giveup")
        result = devloop.resolve(self.con, first["run"])
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("PR is unchanged", devloop.report(result))
        self.assertEqual(self.remote_head(first["branch"]), before)
        self.assertEqual(self.con.execute("SELECT conflict FROM dev_runs WHERE id = ?", (first["run"],)).fetchone()[0], "feature1.py")
        self.assertFalse(list((self.repo / ".devloop").glob("fix-*")))  # worktree cleaned up

    def test_resolve_needs_a_conflict_and_works_inside_a_stack(self):
        second = self.second_task()
        devloop.set_batch(self.con, 3)
        first = self.run_loop(task_id=self.task)
        self.assertIn("no recorded conflict", devloop.why_not_resolve(self.con, first["run"]))
        top = self.run_loop(task_id=second)
        self.main_changes()
        devloop.check_conflicts(self.con, fetch=True)
        by_run = {c["id"]: c for c in devloop.conflicts(self.con)}
        self.assertEqual(by_run[first["run"]]["top"], {"run": top["run"], "task_id": second})
        self.assertIsNone(by_run[top["run"]]["top"])
        self.assertEqual(devloop.why_not_resolve(self.con, first["run"]), "")  # the lower PR has its own Resolve
        self.assertEqual(devloop.resolve(self.con, first["run"])["status"], "resolved")
        self.assertEqual(devloop.resolve(self.con, top["run"])["status"], "resolved")
        self.assertEqual(devloop.conflicts(self.con), [])

    def pr(self, run, **kw):
        row = self.con.execute("SELECT pr_url, head FROM dev_runs WHERE id = ?", (run,)).fetchone()
        return {row["pr_url"]: {"url": row["pr_url"], "state": "OPEN", "mergeable": "MERGEABLE", "headRefOid": row["head"]} | kw}

    def test_scan_closes_a_pr_closed_on_github(self):
        first = self.run_loop()
        self.main_changes()
        devloop.check_conflicts(self.con, fetch=True)
        self.github = self.pr(first["run"], state="CLOSED")
        self.assertIn("1 closed without merging", devloop.scan(self.con))
        self.assertEqual(self.con.execute("SELECT status FROM dev_runs WHERE id = ?", (first["run"],)).fetchone()[0], "closed")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "todo")
        self.assertEqual((devloop.conflicts(self.con), devloop.open_prs(self.con)), ([], 0))

    def test_scan_marks_a_pr_merged_on_github_done(self):
        first = self.run_loop()
        self.github = self.pr(first["run"], state="MERGED")
        self.assertIn("1 merged", devloop.scan(self.con))
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "done")

    def test_scan_uses_githubs_conflict_and_the_prs_current_head(self):
        first = self.run_loop()
        self.github = self.pr(first["run"], mergeable="CONFLICTING")
        self.assertIn("checked live on github: 1 open pr(s) conflict", devloop.scan(self.con).lower())
        self.assertEqual(devloop.conflicts(self.con)[0]["files"], ["(see the pull request)"])
        self.assertTrue(devloop.overview(self.con)["conflict_scan"]["github"])
        self.main_changes()  # now a real conflict; then the branch is fixed and pushed from elsewhere
        devloop.check_conflicts(self.con, fetch=True, live=self.pr(first["run"]))
        self.assertEqual(devloop.conflicts(self.con)[0]["files"], ["feature1.py"])
        before = self.con.execute("SELECT head FROM dev_runs WHERE id = ?", (first["run"],)).fetchone()[0]
        self.assertEqual(devloop.resolve(self.con, first["run"])["status"], "resolved")
        new_head = self.remote_head(first["branch"])
        # as if that fix had been pushed by someone else: Atlas still knows the old head and the conflict
        self.con.execute("UPDATE dev_runs SET head = ?, conflict = 'feature1.py' WHERE id = ?", (before, first["run"]))
        devloop.check_conflicts(self.con, live=self.pr(first["run"], headRefOid=new_head))
        row = self.con.execute("SELECT head, conflict FROM dev_runs WHERE id = ?", (first["run"],)).fetchone()
        self.assertEqual((row["head"], row["conflict"]), (new_head, ""))

    def test_squash_merge_is_recognised(self):
        result = self.run_loop()
        branch = result["branch"]
        dev = self.tmp / "dev"
        sh("git", "clone", "-q", str(self.remote), str(dev), cwd=self.tmp)
        sh("git", "fetch", "-q", "origin", branch, cwd=dev)
        sh("git", "merge", "-q", "--squash", f"origin/{branch}", cwd=dev)
        sh("git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "squash", cwd=dev)
        sh("git", "push", "-q", "origin", "main", cwd=dev)
        self.assertEqual(self.run_loop()["merged"], [self.task])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "done")

    def test_finds_claude_outside_path_and_explains_why_not(self):
        home = self.tmp / "home"; (home / ".local" / "bin").mkdir(parents=True)
        exe = home / ".local" / "bin" / "claude"; exe.write_text("#!/bin/sh\n"); exe.chmod(0o755)
        with mock.patch.dict(os.environ, {"HOME": str(home), "PATH": "/usr/bin:/bin"}):
            self.assertEqual(devloop.claude_path(devloop.DEFAULTS), str(exe))
        self.assertEqual(devloop.why_not_now(self.con), "")
        db.update(self.con, "tasks", self.task, status="done"); self.con.commit()
        self.assertIn("build queue is empty", devloop.why_not_now(self.con))
        text = devloop.remember(self.con, devloop.run(self.con))
        self.assertIn("nothing to do", text)
        self.assertEqual(devloop.overview(self.con)["last"]["status"], "idle")

    def test_limits_pause_and_kill(self):
        self.conf["max_runs_per_day"] = 0
        self.assertEqual(self.run_loop()["status"], "limit")
        self.conf["max_runs_per_day"] = 3
        devloop.set_paused(self.con, True)
        self.assertEqual(self.run_loop()["status"], "paused")
        devloop.set_paused(self.con, False)
        self.assertIn("ON", usage.kill(self.con, "test"))
        self.assertEqual(self.run_loop(force=True)["status"], "killed")  # not even a forced run
        usage.unkill(self.con)
        self.assertEqual(self.run_loop()["status"], "pr_open")

    def test_daily_limits_on_runs_tokens_and_spend(self):
        self.assertEqual(devloop.limits(self.con), {"runs": 3, "tokens": 0, "usd": 0})  # no ceiling: no token/spend limit
        devloop.set_limit(self.con, "runs", 1)
        self.assertEqual(self.run_loop()["status"], "pr_open")
        result = self.run_loop()
        self.assertEqual(result["status"], "limit")
        self.assertIn("runs limit reached (1 of 1)", result["detail"])
        self.assertEqual(self.run_loop(force=True)["status"], "idle")  # Run now ignores the daily limits
        devloop.set_limit(self.con, "runs", 3)
        self.con.execute("UPDATE dev_runs SET tokens_in = 900000, tokens_out = 200000, cost_usd = 4.5")
        devloop.set_limit(self.con, "tokens", 1000000)
        self.assertIn("tokens limit reached", devloop.limit_reached(self.con))
        devloop.set_limit(self.con, "tokens", 0)
        devloop.set_limit(self.con, "usd", 5)
        self.assertEqual(devloop.limit_reached(self.con), "")
        devloop.set_limit(self.con, "usd", 2)
        self.assertIn("USD limit reached ($4.50 of $2.00)", devloop.limit_reached(self.con))
        self.assertIn("stopped: daily USD limit", devloop.status(self.con))
        self.assertIn("of $2.00 spend", devloop.overview(self.con)["limits_text"])

    def test_limits_never_exceed_the_ceilings(self):
        self.conf.update(max_runs_per_day=3, max_tokens_per_day=500000, max_usd_per_day=10)
        self.assertEqual(devloop.limits(self.con), {"runs": 3, "tokens": 500000, "usd": 10})
        for kind, bad in (("runs", 4), ("tokens", 600000), ("usd", 10.5), ("usd", -1), ("days", 1)):
            with self.assertRaises(ValueError):
                devloop.set_limit(self.con, kind, bad)
        devloop.set_limit(self.con, "runs", 0)  # 0 = as many as the ceiling allows (Builder settings: No limit)
        devloop.set_limit(self.con, "tokens", 0)  # 0 = up to the ceiling
        devloop.set_limit(self.con, "usd", 2.5)
        self.assertEqual(devloop.limits(self.con), {"runs": 3, "tokens": 500000, "usd": 2.5})
        self.conf["max_usd_per_day"] = 1  # ceiling lowered later in [devloop]: the lower one holds
        self.assertEqual(devloop.limits(self.con)["usd"], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM log WHERE action = 'devloop_limit'").fetchone()[0], 3)
        devloop.set_override(self.con, True)  # pushed through for today
        self.con.execute("INSERT INTO dev_runs(task_id, status, started) VALUES (1, 'failed', ?)", (db.now(),)); self.con.commit()
        self.assertEqual(devloop.limit_reached(self.con), "")
        self.assertIn("lifted", devloop.limits_text(self.con))

    def test_no_limit_is_truly_unlimited_without_a_ceiling(self):  # #178: 'No limit' used to stop at 3
        self.conf.pop("max_runs_per_day", None)
        devloop.set_limit(self.con, "runs", 0)
        self.assertEqual(devloop.limits(self.con)["runs"], 0)
        for _ in range(150):  # far beyond the old default ceiling of 100 and the Pi's 3
            self.con.execute("INSERT INTO dev_runs(task_id, status, started) VALUES (1, 'failed', ?)", (db.now(),))
        self.con.commit()
        self.assertEqual(devloop.limit_reached(self.con), "")
        self.assertIn("150 runs (no limit)", devloop.limits_text(self.con))
        self.assertEqual(devloop.capped(self.con), {})
        self.assertEqual(devloop.overview(self.con)["limit_choices"]["runs"], 0)

    def test_a_ceiling_over_no_limit_is_named(self):
        self.conf["max_runs_per_day"] = 3  # what an older enable-devloop.sh wrote into config/local.toml
        devloop.set_limit(self.con, "runs", 0)
        self.assertEqual(devloop.limits(self.con)["runs"], 3)
        self.assertEqual(devloop.capped(self.con), {"runs": 3})
        text = devloop.limits_text(self.con)
        self.assertIn("capped at 3 by max_runs_per_day", text)
        self.assertIn("builder-unlimited.sh", text)
        devloop.set_limit(self.con, "runs", 2)  # own choice below the ceiling: nothing to explain
        self.assertEqual(devloop.capped(self.con), {})

    def test_limit_from_the_command_line_and_telegram(self):
        import contextlib, io
        from engine import cli
        from engine.interfaces import telegram
        out = io.StringIO()
        with mock.patch.object(cli, "_con", lambda: self.con), contextlib.redirect_stdout(out):
            cli.main(["dev", "limit", "usd", "3"])
            cli.main(["dev", "limit"])
        self.assertIn("of $3.00 spend", out.getvalue())
        bot = mock.Mock(con=self.con)
        self.assertIn("of 2 runs", telegram.Bot.cmd_dev(bot, "limit runs 2"))
        self.assertEqual(devloop.limits(self.con)["runs"], 2)

    def test_interrupted_run_is_closed_and_does_not_block(self):
        db.insert(self.con, "dev_runs", task_id=self.task, branch="x", status="running", started=db.now(), head="")
        db.update(self.con, "tasks", self.task, status="doing")
        self.con.commit()
        self.assertIsNone(devloop.current(self.con))  # no process holds the lock: left over from a reboot
        self.assertEqual(self.con.execute("SELECT status FROM dev_runs").fetchone()[0], "failed")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "todo")
        self.assertEqual(self.run_loop()["status"], "pr_open")

    def test_status_and_overview(self):
        self.run_loop()
        text = devloop.status(self.con)
        self.assertIn("on · 1/3 runs today", text)
        self.assertIn("pr_open", text)
        o = devloop.overview(self.con)
        self.assertEqual((o["enabled"], o["killed"], len(o["runs"])), (True, False, 1))


class LanesTest(unittest.TestCase):
    """#57: one lane per task in flight (building or PR awaiting review), running first, with stack and conflict info."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con)
        work = self.tmp / ".devloop"
        for patcher in (mock.patch.object(devloop, "WORK", work), mock.patch.object(devloop, "LOCK", work / "lock"),
                        mock.patch.object(devloop, "_held", lambda path: True),  # a run holds every slot lock
                        mock.patch.object(devloop, "cfg", lambda: devloop.DEFAULTS | {"max_minutes": 60})):
            patcher.start(); self.addCleanup(patcher.stop)
        t = lambda title, status="doing": db.insert(self.con, "tasks", title=title, owner="ai", status=status)
        self.a, self.b, self.c, self.d = t("First PR"), t("Second PR"), t("Building now"), t("Merged by hand", "done")
        run = lambda task, status, **kw: db.insert(self.con, "dev_runs", task_id=task, branch=f"hq-dev/{task}", status=status, head="h", **kw)
        self.ra = run(self.a, "pr_open", started="2026-10-14T08:00:00", pr_url="https://x/pr/1", conflict="")
        self.rb = run(self.b, "pr_open", started="2026-10-14T09:00:00", base_run=self.ra, conflict="engine/db.py,engine/cli.py")
        run(self.d, "pr_open", started="2026-10-14T07:00:00")
        started = (datetime.now() - timedelta(minutes=15)).isoformat(timespec="seconds")
        self.rc = run(self.c, "running", started=started, model="sonnet", model_reason="small task")
        (work / "logs").mkdir(parents=True, exist_ok=True)
        devloop.log_path(self.rc).write_text("→ Bash: ls\n· Writing the tests\n")
        self.con.commit()

    def test_lanes(self):
        L = devloop.lanes(self.con)
        self.assertEqual([l["task_id"] for l in L], [self.c, self.b, self.a])  # running first; the done task has no lane
        run, second, first = L
        self.assertEqual((run["state"], run["doing"], run["model"]), ("running", "· Writing the tests", "sonnet"))
        self.assertTrue(20 <= run["progress"] <= 30, run["progress"])  # 15 of 60 minutes
        self.assertEqual(run["step"], "Looking through the code to find the right places.")  # #179: a plain sentence
        self.assertIn("    Looking through the code", "\n".join(devloop.lanes_text(self.con)))
        self.assertEqual((second["state"], second["conflict"], second["stacked_on"]), ("conflict", ["engine/db.py", "engine/cli.py"], [self.a]))
        self.assertEqual((first["state"], first["in_stack_of"], first["progress"]), ("pr", self.b, None))
        o = devloop.overview(self.con)
        self.assertEqual(len(o["lanes"]), 3)
        self.assertIn(f"In flight (3", devloop.status(self.con))
        text = "\n".join(devloop.lanes_text(self.con))
        self.assertIn(f"[conflict] #{self.b} Second PR", text)
        self.assertIn(f"stacked on #{self.a}", text)

    def test_several_running_runs_each_get_a_lane(self):
        e = db.insert(self.con, "tasks", title="Parallel build", owner="ai", status="doing")
        db.insert(self.con, "dev_runs", task_id=e, branch="hq-dev/e", status="running", started=db.now(), head="")
        self.assertEqual([l["state"] for l in devloop.lanes(self.con)], ["running", "running", "conflict", "pr"])

    def test_a_resolve_run_replaces_its_tasks_pr_lane(self):
        db.insert(self.con, "dev_runs", task_id=self.b, branch="hq-dev/b", status="running", started=db.now(), head="",
                  resolves=self.rb)
        L = devloop.lanes(self.con)
        self.assertEqual([(l["task_id"], l["state"], l["kind"]) for l in L if l["task_id"] == self.b], [(self.b, "running", "resolve")])


    def test_live_returns_only_new_lines(self):
        """The Build tab's console polls GET /api/run with the line count it has; only newer lines come back."""
        first = devloop.live(self.con, self.rc)
        self.assertEqual((first["lines"], first["total"], first["from"], first["status"]), (["→ Bash: ls", "· Writing the tests"], 2, 0, "running"))
        self.assertTrue(20 <= first["progress"] <= 30)
        self.assertEqual((first["plain"]["said"], first["plain"]["now"]),
                         ("Writing the tests", "Looking through the code to find the right places."))  # #179
        with open(devloop.log_path(self.rc), "a") as log:
            log.write("■ finished after 2 turns\n")
        nxt = devloop.live(self.con, self.rc, after=first["total"])
        self.assertEqual((nxt["lines"], nxt["total"], nxt["from"]), (["■ finished after 2 turns"], 3, 2))
        self.assertEqual(devloop.live(self.con, self.rc, after=99)["lines"], [])  # a stale count never errors
        tail = devloop.live(self.con, self.rc, limit=1)
        self.assertEqual((tail["lines"], tail["from"]), (["■ finished after 2 turns"], 2))  # a long log starts at its end
        self.assertEqual(devloop.live(self.con, self.ra)["lines"], [])  # no log file yet
        with self.assertRaises(ValueError):
            devloop.live(self.con, 9999)


class KillAndUsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(usage, "KILL_FILE", Path(self.tmp.name) / "KILL"); p.start(); self.addCleanup(p.stop)
        self.db = Path(self.tmp.name) / "hq.db"
        con = db.connect(self.db); db.init(con); con.close()

    def test_kill_switch_blocks_paid_calls_only(self):
        cfg = {"models": {"routing": {"x": ["cloud", "local"]}}}
        con = db.connect(self.db)
        usage.kill(con, "test")
        with mock.patch.object(loop, "load_constitution", return_value=cfg), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), \
                mock.patch.object(models, "_post", return_value={"message": {"content": "local answer"}}) as post:
            self.assertEqual(models.complete("x", "s", "u"), "local answer")
        self.assertNotIn("anthropic", post.call_args[0][0])  # the cloud was never called
        usage.unkill(con)
        self.assertFalse(usage.killed())
        con.close()

    def test_model_calls_are_recorded_with_estimated_cost(self):
        cfg = {"models": {"routing": {"dump": ["cloud"]}, "cloud_usd_per_mtok_in": 3, "cloud_usd_per_mtok_out": 15}}
        reply = {"content": [{"text": "ok"}], "usage": {"input_tokens": 2000, "output_tokens": 1000}}
        with mock.patch.object(loop, "load_constitution", return_value=cfg), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), \
                mock.patch.object(models, "_post", return_value=reply), \
                mock.patch.object(models, "on_usage", usage.recorder(self.db)):
            models.complete("dump", "s", "u")
        con = db.connect(self.db)
        row = con.execute("SELECT job, tier, tokens_in, tokens_out, cost_usd FROM model_usage").fetchone()
        self.assertEqual(tuple(row), ("dump", "cloud", 2000, 1000, 0.021))
        s = usage.summary(con)
        self.assertEqual(s["hq"]["today"]["calls"], 1)
        self.assertIn("Atlas API 1 calls, 3k tokens, ~$0.02", usage.text(con))
        con.close()


if __name__ == "__main__":
    unittest.main()
