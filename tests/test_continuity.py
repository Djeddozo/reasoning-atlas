"""RA-015 drill: controlled stops before and after a checkpoint; a fresh process resumes from the repository alone.

Each "session" is a separate Python process that sees only the repository folder.
The external effect is an append to a file outside the repository, so a duplicate
would be visible as a second line.
"""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

from packages.contracts import modules
from services.atlas import continuity, handoff, progress

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "resume.py"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Drill(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        (self.repo / "STATE" / "evidence").mkdir(parents=True)
        tasks = {"tasks": [{"id": "RA-900", "title": "Synthetic drill task", "status": "ready", "dependsOn": [],
                            "acceptance": "Resumes", "validation": "Stops", "program": "atlas", "path": "continuity"},
                           {"id": "RA-901", "title": "Finished task", "status": "done", "dependsOn": [],
                            "acceptance": "-", "validation": "-"}]}
        (self.repo / "STATE" / "tasks.json").write_text(json.dumps(tasks))
        (self.repo / "STATE" / "active-task.json").write_text(json.dumps({"schemaVersion": 1, "taskId": None}))
        (self.repo / "work.txt").write_text("base\n")
        git(self.repo, "init", "-q", "-b", "feat/ra-900")
        git(self.repo, "config", "user.email", "drill@example.invalid")
        git(self.repo, "config", "user.name", "drill")
        self.commit("base")
        self.remote = self.tmp / "remote-effects.log"  # outside the repository: stands in for GitHub
        self.remote.write_text("")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, message):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", message)

    def session(self, *args):
        """A fresh session: a new process with nothing but the repository."""
        return subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.repo), *args],
                              capture_output=True, text=True, cwd=self.tmp)

    def show(self):
        out = self.session("show", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def publish(self, key, crash_before_finish=False):
        """Guarded external effect: begin → effect → finish, optionally stopped before finish."""
        begun = self.session("effect", "begin", key, "--kind", "push", "--target", "origin/feat/ra-900")
        if begun.returncode != 0:
            return begun.stdout.strip()
        with self.remote.open("a") as remote:
            remote.write(key + "\n")
        if not crash_before_finish:
            self.assertEqual(self.session("effect", "finish", key, "done").returncode, 0)
        return "ran"

    def first_checkpoint(self):
        self.assertEqual(self.session("claim", "RA-900", "--owner", "Codex", "--session", "codex-1").returncode, 0)
        self.assertEqual(self.session("checkpoint", "--next", "Write the parser").returncode, 0)
        self.commit("RA-900 checkpoint: claimed")
        return git(self.repo, "rev-parse", "--short", "HEAD")

    def test_stop_before_checkpoint_effect_in_flight(self):
        cp = self.first_checkpoint()
        (self.repo / "work.txt").write_text("base\nhalf a parser\n")
        self.assertEqual(self.publish("push:RA-900:1", crash_before_finish=True), "ran")
        # usage cap: the session ends here, nothing committed, the push outcome never recorded

        state = self.show()  # a fresh Claude session
        self.assertEqual(state["taskId"], "RA-900")
        self.assertEqual(state["stopPoint"], "before-checkpoint")
        self.assertEqual(state["checkpointCommit"], cp)
        self.assertIn("work.txt", state["uncommitted"])
        self.assertEqual(state["nextAction"], "Write the parser")
        self.assertEqual([e["key"] for e in state["verifyEffects"]], ["push:RA-900:1"])
        self.assertIn("Verify each unsettled external effect", state["steps"][0])

        # the new session retries the same step; the ledger stops a second push
        self.assertTrue(self.publish("push:RA-900:1").startswith("verify"))
        self.assertEqual(self.remote.read_text().splitlines(), ["push:RA-900:1"])
        # it checks the remote, finds the push, and records it
        self.assertEqual(self.session("effect", "finish", "push:RA-900:1", "done", "--detail", "found on remote").returncode, 0)
        self.assertTrue(self.publish("push:RA-900:1").startswith("skip"))
        self.assertEqual(len(self.remote.read_text().splitlines()), 1)
        self.assertEqual(self.show()["verifyEffects"], [])

    def test_effect_verified_as_not_done_runs_once_more(self):
        self.first_checkpoint()
        begun = self.session("effect", "begin", "pr:RA-900", "--kind", "pull-request", "--target", "feat/ra-900")
        self.assertEqual(begun.returncode, 0)  # stopped before the effect itself happened
        self.assertEqual(self.session("effect", "begin", "pr:RA-900", "--kind", "pull-request", "--target", "feat/ra-900").returncode, 4)
        self.session("effect", "finish", "pr:RA-900", "not_done", "--detail", "no pull request exists")
        self.assertEqual(self.publish("pr:RA-900"), "ran")
        self.assertEqual(self.remote.read_text().splitlines(), ["pr:RA-900"])
        entry = continuity.effects(self.repo)["effects"][0]
        self.assertEqual((entry["state"], entry["attempts"]), ("done", 2))

    def test_stop_after_checkpoint(self):
        self.first_checkpoint()
        (self.repo / "work.txt").write_text("base\nparser\n")
        self.assertEqual(self.publish("push:RA-900:1"), "ran")
        self.assertEqual(self.session("checkpoint", "--next", "Write the evidence file").returncode, 0)
        self.commit("RA-900 checkpoint: parser")
        head = git(self.repo, "rev-parse", "--short", "HEAD")
        # session switch: Claude → Codex

        state = self.show()
        self.assertEqual(state["stopPoint"], "at-checkpoint")
        self.assertEqual(state["checkpointCommit"], head)
        self.assertEqual((state["uncommitted"], state["commitsAfterCheckpoint"], state["verifyEffects"]), ([], [], []))
        self.assertEqual(state["nextAction"], "Write the evidence file")
        self.assertEqual([e["key"] for e in state["doneEffects"]], ["push:RA-900:1"])
        self.assertIn("Do not repeat", state["steps"][-1])
        self.assertTrue(self.publish("push:RA-900:1").startswith("skip"))
        self.assertEqual(len(self.remote.read_text().splitlines()), 1)

    def test_commits_after_checkpoint_count_as_unstamped_work(self):
        self.first_checkpoint()
        (self.repo / "work.txt").write_text("more\n")
        self.commit("RA-900 parser, no checkpoint")
        state = self.show()
        self.assertEqual(state["stopPoint"], "before-checkpoint")
        self.assertEqual(len(state["commitsAfterCheckpoint"]), 1)
        self.assertEqual(state["uncommitted"], [])

    def test_next_action_falls_back_to_evidence(self):
        self.session("claim", "RA-900", "--owner", "Claude", "--session", "c1")
        (self.repo / "STATE/evidence/RA-900.md").write_text("# RA-900\n\n## Exact next action\nRun the drill.\n\n## Other\nx\n")
        self.commit("RA-900 claim")
        self.assertEqual(self.show()["nextAction"], "Run the drill.")
        (self.repo / "STATE/evidence/RA-900.md").write_text("- Exact next action: Rebuild the client.\n")
        self.assertEqual(self.show()["nextAction"], "Rebuild the client.")

    def test_done_task_and_no_claim(self):
        self.assertIn("no task is claimed", self.session("show").stdout)
        self.session("claim", "RA-901", "--owner", "Claude", "--session", "c1")
        self.assertIn("already done", self.show()["steps"][-1])

    def test_rejects_bad_input(self):
        self.assertEqual(self.session("claim", "RA-999", "--owner", "x", "--session", "y").returncode, 2)
        self.assertEqual(self.session("checkpoint", "--next", "x").returncode, 2)
        self.assertEqual(self.session("effect", "finish", "nothing", "done").returncode, 2)

    def test_handoff_packet_carries_resume_state(self):
        self.first_checkpoint()
        (self.repo / "work.txt").write_text("unsaved\n")
        registry = modules.load_registry(ROOT / "modules", ROOT)  # the drill repository has no module manifests
        with mock.patch.object(progress, "STATUS_FILE", self.tmp / "build-status.json"), \
                mock.patch.object(modules, "load_registry", return_value=registry):
            text = handoff.packet("RA-900", "claude-desktop", self.repo)["text"]
        for expected in ("Resume state (before checkpoint): RA-900", "claimed by Codex", "1 uncommitted file(s)",
                         "Continue: Write the parser", "scripts/resume.py show", "effect begin"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
