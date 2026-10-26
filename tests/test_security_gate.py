"""Security gate for outside code (task #75): trust and licence checks, no automatic approval, and the dev loop's
check for new dependencies. Builds on engine.sandbox (task #18)."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import db, devloop, gatekeeper, needs, sandbox, skilltree
from tests.test_sandbox import CONF, FakeDocker

CONF_GATE = {"trusted_owners": sandbox.TRUSTED_OWNERS}


class GateTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-test-", dir=Path.home()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "tool"; self.src.mkdir()
        (self.src / "main.py").write_text("print('hi')\n")
        stage = mock.patch.object(sandbox, "STAGE", self.tmp / "stage"); stage.start(); self.addCleanup(stage.stop)
        kill = mock.patch("engine.usage.killed", return_value=False); kill.start(); self.addCleanup(kill.stop)
        env = mock.patch.dict(_os.environ, {"HQ_ACTOR": ""}); env.start(); self.addCleanup(env.stop)

    def outside_task(self, source="https://github.com/someone/tool") -> int:
        tid = db.insert(self.con, "tasks", title="Adopt a tool", owner="ai", description=f"Outside code: {source}. Review it.")
        self.con.commit()
        return tid

    # ---- licence ----
    def test_licence_check_classifies_files_and_metadata(self):
        (self.src / "LICENSE").write_text("MIT License\n\nPermission is hereby granted, free of charge, ...")
        self.assertEqual(sandbox.license_check(self.src)["kind"], "permissive")
        (self.src / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3")
        self.assertEqual(sandbox.license_check(self.src)["name"], "GPL")
        (self.src / "LICENSE").write_text("GNU LESSER GENERAL PUBLIC LICENSE")
        self.assertEqual(sandbox.license_check(self.src)["kind"], "weak-copyleft")
        (self.src / "LICENSE").unlink()
        self.assertEqual(sandbox.license_check(self.src)["kind"], "none")
        (self.src / "package.json").write_text(json.dumps({"name": "x", "license": "Apache-2.0"}))
        lic = sandbox.license_check(self.src)
        self.assertEqual((lic["name"], lic["file"]), ("Apache-2.0", "package.json"))
        (self.src / "package.json").write_text(json.dumps({"name": "x", "license": "Weird-1.0"}))
        self.assertEqual(sandbox.license_check(self.src)["name"], "unrecognised")

    # ---- trust ----
    def test_trust_is_high_only_for_trusted_publishers(self):
        self.assertEqual(sandbox.trust_check(self.con, "", CONF_GATE)["level"], "low")
        self.assertEqual(sandbox.trust_check(self.con, "https://evil.example/x.tar.gz", CONF_GATE)["level"], "low")
        self.assertEqual(sandbox.trust_check(self.con, "https://github.com/python/cpython", CONF_GATE)["level"], "high")
        self.assertEqual(sandbox.trust_check(self.con, "https://pypi.org/project/someone-pkg/", CONF_GATE)["level"], "low")
        self.assertEqual(sandbox.trust_check(self.con, "https://github.com/someone/tool", CONF_GATE)["level"], "low")
        db.insert(self.con, "node_research", sources=json.dumps({"repos": [
            {"name": "someone/tool", "url": "https://github.com/someone/tool", "stars": 5000, "updated": "2026-09-01"}]}))
        t = sandbox.trust_check(self.con, "Outside code: https://github.com/someone/tool.", CONF_GATE, today="2026-10-25")
        self.assertEqual(t["level"], "medium")  # popular, but popularity never makes code highly trusted
        self.assertIn("5000 stars", t["why"])
        self.assertEqual(sandbox.trust_check(self.con, "https://github.com/someone/tool", CONF_GATE,
                                             today="2028-01-01")["level"], "low")  # no longer maintained
        extra = sandbox.gate_cfg({"security_gate": {"trusted_owners": ["Someone"]}})
        self.assertEqual(sandbox.trust_check(self.con, "https://github.com/someone/tool", extra)["level"], "high")

    # ---- the report on the approval ----
    def test_approval_shows_source_trust_licence_scan_and_run(self):
        tid = self.outside_task()
        (self.src / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE")
        line = sandbox.evidence(self.con, tid)
        self.assertIn("Code from https://github.com/someone/tool", line)
        self.assertIn("Trust: low", line); self.assertIn("Licence: not checked yet", line)
        self.assertIn("Only your own tap approves outside code", line)
        sandbox.run(self.con, self.src, "python3 main.py", task_id=tid, origin="https://github.com/someone/tool",
                    conf=CONF, runner=FakeDocker())
        self.assertEqual(sandbox.runs(self.con, tid)[0]["license"]["name"], "AGPL-3.0")
        g = sandbox.gate_report(self.con, tid)
        self.assertTrue(any("AGPL" in a for a in g["advice"]))
        self.assertIn("Licence: AGPL-3.0", sandbox.evidence(self.con, tid))
        with mock.patch("engine.gatekeeper.notify"):
            self.assertTrue(devloop.outside_blocked(self.con, tid))
        a = self.con.execute("SELECT * FROM approvals WHERE task_id = ?", (tid,)).fetchone()
        self.assertEqual(a["category"], "outside_code")
        self.assertIn("Trust: low", a["reason"]); self.assertIn("Sandbox run #", a["reason"])
        self.assertEqual(a["reason"].count("Trust:"), 1)

    # ---- no automatic approval ----
    def test_a_timeout_never_approves_outside_code(self):
        tid = self.outside_task()
        rules = {"on_timeout": "allow", "wait_seconds": 1}
        with mock.patch.object(gatekeeper, "_rules", return_value=rules), mock.patch.object(gatekeeper, "notify"), \
                mock.patch.object(gatekeeper.time, "sleep"):
            self.assertEqual(gatekeeper.request_approval(self.con, "Use it?", "", "outside_code", wait=1), "pending")
            self.assertEqual(gatekeeper.request_approval(self.con, "Build?", "", "action", wait=1, task_id=tid), "pending")
            self.assertEqual(gatekeeper.request_approval(self.con, "Other?", "", "action", wait=1), "approved")
        self.assertEqual([r[0] for r in self.con.execute("SELECT status FROM approvals ORDER BY id")],
                         ["pending", "pending", "approved"])

    def test_an_agent_cannot_approve_outside_code(self):
        tid = self.outside_task()
        plain = db.insert(self.con, "tasks", title="Own code", owner="ai", description="stdlib")
        with mock.patch.dict(_os.environ, {"HQ_ACTOR": "agent"}):
            with self.assertRaises(ValueError):
                skilltree.decide(self.con, tid, True)
            skilltree.decide(self.con, tid, False)  # saying no is always possible
            skilltree.decide(self.con, plain, True)
            with mock.patch("engine.gatekeeper.notify"):
                gatekeeper.request_approval(self.con, "Build it?", "", "outside_code", wait=0, task_id=tid)
            aid = self.con.execute("SELECT MAX(id) FROM approvals").fetchone()[0]
            with self.assertRaises(ValueError):
                needs.answer(self.con, "approval", aid, verdict="approved")
            with self.assertRaises(ValueError):
                gatekeeper.guard_manual(self.con, aid, "approved")
        self.assertIn("Approved", needs.answer(self.con, "approval", aid, verdict="approved"))  # Djeddi himself
        self.assertEqual(skilltree._approval(self.con, tid), "approved")
        self.assertEqual(self.con.execute("SELECT category FROM approvals WHERE task_id = ? ORDER BY id LIMIT 1",
                                          (tid,)).fetchone()[0], "outside_code")

    # ---- the dev loop ----
    def test_prompts_carry_the_self_written_default(self):
        self.assertIn("Write the code yourself", devloop.PROMPT)
        self.assertIn("security gate", devloop.PROMPT)
        self.assertIn("Write the code yourself", devloop.REPAIR_PROMPT)

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True,
                       env=dict(_os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                                GIT_COMMITTER_EMAIL="t@t"))

    def test_new_dependencies_in_the_agents_commits_are_found(self):
        self.repo = self.tmp / "repo"; (self.repo / "engine").mkdir(parents=True)
        (self.repo / "engine" / "__init__.py").write_text("")
        (self.repo / "engine" / "a.py").write_text("import json\n")
        (self.repo / "package.json").write_text(json.dumps({"dependencies": {"react": "18"}}))
        self.git("init", "-q"); self.git("add", "."); self.git("commit", "-qm", "base")
        start = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        self.assertEqual(sandbox.new_outside_code(self.repo, start), [])
        (self.repo / "engine" / "b.py").write_text("import os\nfrom engine import a\nfrom . import a\nimport yaml\n"
                                                   "from requests.adapters import HTTPAdapter\n")
        (self.repo / "requirements.txt").write_text("# deps\nrequests>=2\n")
        (self.repo / "package.json").write_text(json.dumps({"dependencies": {"react": "18", "left-pad": "1"}}))
        self.git("add", "."); self.git("commit", "-qm", "work")
        found = sandbox.new_outside_code(self.repo, start)
        self.assertIn("requirements.txt: new dependency requests", found)
        self.assertIn("package.json: new dependency left-pad", found)
        self.assertTrue(any("import of yaml" in f for f in found))
        self.assertTrue(any("import of requests" in f for f in found))
        self.assertFalse(any("react" in f or "of os" in f or "of engine" in f for f in found))

    def test_check_work_refuses_outside_code_unless_the_task_passed_the_gate(self):
        ok = subprocess.CompletedProcess([], 0, "OK", "")
        with mock.patch.object(devloop, "_git", return_value="1"), \
                mock.patch("engine.devloop.subprocess.run", return_value=ok), \
                mock.patch.object(sandbox, "new_outside_code", return_value=["Python import of yaml"]):
            outcome, problem = devloop.check_work(self.tmp, False, "")
            self.assertEqual(outcome, "outside_code")
            self.assertIn("write the code yourself", problem); self.assertIn("yaml", problem)
            self.assertEqual(devloop.check_work(self.tmp, False, "", outside_ok=True), ("ok", ""))


if __name__ == "__main__":
    unittest.main()
