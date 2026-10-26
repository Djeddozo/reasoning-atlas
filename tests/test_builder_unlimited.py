"""deploy/builder-unlimited.sh (#178): removes the Pi's old [devloop] ceilings so Builder settings decide."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL = """# Settings for this Pi only
[devloop]
enabled = true
max_runs_per_day = 3            # each run uses Claude subscription capacity
max_minutes = 40
max_open_prs = 5                # ceiling for the review batch

[[gatekeeper.deny]]
tool = "Bash"
max_runs_per_day = 9
"""


class BuilderUnlimitedTest(unittest.TestCase):
    def run_script(self, d: Path):
        return subprocess.run(["bash", str(d / "deploy" / "builder-unlimited.sh")], capture_output=True, text=True, timeout=30)

    def test_comments_out_only_the_devloop_ceilings(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "deploy").mkdir(); (d / "config").mkdir()
            shutil.copy(ROOT / "deploy" / "builder-unlimited.sh", d / "deploy")
            (d / "config" / "local.toml").write_text(LOCAL)
            r = self.run_script(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("2 ceiling line(s)", r.stdout)
            s = (d / "config" / "local.toml").read_text()
            self.assertIn("# (#178) max_runs_per_day = 3", s)
            self.assertIn("# (#178) max_open_prs = 5", s)
            self.assertIn("max_minutes = 40", s)
            self.assertIn("\nmax_runs_per_day = 9\n", s)  # other sections untouched
            import tomllib
            self.assertNotIn("max_runs_per_day", tomllib.loads(s)["devloop"])
            r = self.run_script(d)  # running it again changes nothing
            self.assertIn("Nothing to change", r.stdout)
            self.assertEqual((d / "config" / "local.toml").read_text(), s)

    def test_new_installs_write_no_ceilings(self):
        script = (ROOT / "deploy" / "enable-devloop.sh").read_text()
        self.assertNotIn("\nmax_runs_per_day =", script)
        self.assertNotIn("\nmax_open_prs =", script)


if __name__ == "__main__":
    unittest.main()
