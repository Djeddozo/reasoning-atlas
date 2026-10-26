"""One name, stage 3 (RA-055): the Engine's code is `engine/` and its command `atlas.sh`; `hq` and `hq.sh` are thin aliases."""
import importlib
import os
import pkgutil
import re
import subprocess
import sys
import unittest
from pathlib import Path

import engine

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "HQ_DB": "/nonexistent/never-opened.db"}


def run(*args):
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, timeout=60, env=ENV,
                          stdin=subprocess.DEVNULL)


class AliasPackage(unittest.TestCase):
    def test_every_module_is_the_same_object_under_both_names(self):
        import hq
        names = [m.name for m in pkgutil.walk_packages(engine.__path__, "engine.") if not m.name.endswith("__main__")]
        self.assertIn("engine.devloop", names)
        self.assertIn("engine.interfaces.telegram", names)
        for name in names:
            new = importlib.import_module(name)
            old = importlib.import_module("hq" + name[len("engine"):])
            self.assertIs(old, new, name)
            self.assertEqual(new.__spec__.name, name)  # the alias does not rename the real module
        from hq import db
        self.assertIs(db, engine.db)
        self.assertIs(hq.priority, engine.priority)

    def test_patching_the_old_name_patches_the_engine(self):
        from unittest import mock
        with mock.patch("hq.usage.killed", return_value=True):
            self.assertTrue(engine.usage.killed())

    def test_alias_holds_no_code_of_its_own(self):
        self.assertEqual(sorted(p.name for p in (ROOT / "hq").glob("*.py")), ["__init__.py", "__main__.py"])
        self.assertLess(len((ROOT / "hq.sh").read_text().splitlines()), 5)
        self.assertIn("atlas.sh", (ROOT / "hq.sh").read_text())


class Commands(unittest.TestCase):
    def test_new_and_old_commands_give_the_same_cli(self):
        new = run("./atlas.sh", "--help")
        self.assertEqual(new.returncode, 0, new.stderr)
        self.assertTrue(new.stdout.startswith("usage: atlas"))
        for old in (run("./hq.sh", "--help"), run(sys.executable, "-m", "hq", "--help"), run(sys.executable, "-m", "engine", "--help")):
            self.assertEqual((old.returncode, old.stdout), (0, new.stdout), old.stderr)

    def test_old_command_passes_arguments_and_exit_codes(self):
        r = run("./hq.sh", "no-such-command")
        self.assertEqual(r.returncode, run("./atlas.sh", "no-such-command").returncode)
        self.assertNotEqual(r.returncode, 0)


class NothingInTheRepositoryUsesTheOldNames(unittest.TestCase):
    """The aliases exist for what lives outside git (the Pi's crontab, systemd units, habits) until RA-042."""

    def tracked(self, *patterns):
        out = subprocess.run(["git", "ls-files", *patterns], cwd=ROOT, capture_output=True, text=True).stdout.split()
        return [ROOT / f for f in out if not f.startswith("hq/") and (ROOT / f).exists()]

    def test_python_imports_the_engine(self):
        old = re.compile(r"^\s*(from hq(\.|\s+import)|import hq\b)", re.M)
        bad = [str(p.relative_to(ROOT)) for p in self.tracked("*.py") if p.name != Path(__file__).name and old.search(p.read_text())]
        self.assertEqual(bad, [])

    def test_engine_jobs_start_under_the_new_name(self):
        bad = []
        for p in self.tracked("*.py"):
            if p.name == Path(__file__).name:
                continue
            if re.search(r"launch\.detached\(\[\"hq\"", p.read_text()):
                bad.append(str(p.relative_to(ROOT)))
        self.assertEqual(bad, [])

    def test_deploy_scripts_call_the_new_command(self):
        bad = []
        for p in self.tracked("deploy/*.sh", "deploy/*.service", "deploy/*.json"):
            for n, line in enumerate(p.read_text().splitlines(), 1):
                if re.search(r"\./hq\.sh|/hq\.sh\b|from hq import|-m hq\b", line):
                    bad.append(f"{p.relative_to(ROOT)}:{n}")
        self.assertEqual(bad, [])

    def test_process_checks_find_both_names(self):
        """Services started before the update run as `python3 -m hq …`; after it as `python3 -m engine …`."""
        for p in self.tracked("deploy/*.sh"):
            for line in p.read_text().splitlines():
                if re.search(r"\b(pgrep|pkill) -f", line) and "python3 -m" in line:
                    self.assertIn("python3 -m (hq|engine)", line, f"{p.name}: {line.strip()}")


class Gatekeeper(unittest.TestCase):
    """Rules on the Pi (config/local.toml, the constitution) were written for the old names; the new ones must not slip past."""
    CFG = {"deny": [{"tool": "Bash", "pattern": r"(hq\.sh|-m\s+hq)\s+(dev|kill|unkill|update)\b", "why": "owner only"}],
           "allow": [{"tool": "Bash", "pattern": r"^\s*(\./hq\.sh|python3 -m hq)\b[^;&|`$]*$", "why": "Atlas commands"}]}

    def decide(self, command):
        from engine import gatekeeper
        return gatekeeper.decide("Bash", {"command": command}, self.CFG)[0]

    def test_old_deny_rules_hold_for_the_new_names(self):
        for c in ("./hq.sh dev run", "./atlas.sh dev run", "cd x && ./atlas.sh kill", "python3 -m engine update", "python3 -m hq unkill"):
            self.assertEqual(self.decide(c), "deny", c)

    def test_new_names_gain_no_allow_rule(self):
        self.assertEqual(self.decide("./hq.sh brief"), "allow")
        self.assertEqual(self.decide("./atlas.sh brief"), "ask")
        self.assertEqual(self.decide("python3 -m engine brief"), "ask")

    def test_other_words_are_not_rewritten(self):
        from engine.gatekeeper import old_names
        self.assertEqual(old_names("cat myatlas.sh; python3 -m engines"), "cat myatlas.sh; python3 -m engines")
        self.assertEqual(old_names("./atlas.sh dev && python3 -m  engine kill"), "./hq.sh dev && python3 -m  hq kill")

    def test_the_enable_script_writes_a_rule_for_both_names(self):
        text = (ROOT / "deploy" / "enable-devloop.sh").read_text()
        self.assertIn(r"(hq\\.sh|atlas\\.sh|-m\\s+(hq|engine))", text)


if __name__ == "__main__":
    unittest.main()
