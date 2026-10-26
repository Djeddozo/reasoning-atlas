"""deploy/move_to_atlas_folder.py (RA-042): moves ~/hq-system to ~/atlas on a temporary home.
Fake `systemctl` and `crontab` commands stand in for the Pi's, so no real unit or crontab is touched."""
import contextlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "move_to_atlas_folder.py"
FAKE_CRONTAB = """#!/usr/bin/env bash
if [ "$1" = "-l" ]; then [ -f "$HOME/crontab.txt" ] || exit 1; cat "$HOME/crontab.txt"
elif [ "$1" = "-" ]; then cat > "$HOME/crontab.txt"
else exit 2; fi
"""
# --user <verb> <unit>: is-active answers from $HOME/active; units listed in $HOME/broken never start
FAKE_SYSTEMCTL = """#!/usr/bin/env bash
shift; echo "$*" >> "$HOME/systemctl.log"
verb="$1"; [ "$verb" = "is-active" ] && shift; unit="$2"
touch "$HOME/active"
case "$verb" in
  is-active) grep -qx "$unit" "$HOME/active"; exit $? ;;
  stop) grep -vx "$unit" "$HOME/active" > "$HOME/active.tmp"; mv "$HOME/active.tmp" "$HOME/active" ;;
  start) grep -qx "$unit" "$HOME/broken" 2>/dev/null || echo "$unit" >> "$HOME/active" ;;
esac
exit 0
"""
DEVLOOP = "17 */3 * * * cd {old} && ./hq.sh dev run >> memory/devloop.log 2>&1  # hq-devloop"
OTHER = "0 1 * * * cd ~/hq-system-old && ./backup.sh"


class MoveFolderTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve(); self.addCleanup(shutil.rmtree, self.home)
        self.old, self.new = self.home / "hq-system", self.home / "atlas"
        self.env = {"HOME": str(self.home), "PATH": f"{self.home / 'bin'}:{os.environ['PATH']}", "ATLAS_MOVE_SETTLE": "0",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        bin_ = self.home / "bin"; bin_.mkdir()
        for name, text in (("crontab", FAKE_CRONTAB), ("systemctl", FAKE_SYSTEMCTL)):
            (bin_ / name).write_text(text); (bin_ / name).chmod(0o755)
        self.old.mkdir()
        (self.old / "atlas.sh").write_text("#!/bin/sh\n")
        (self.old / ".gitignore").write_text("memory/\n.env\n.devloop/\n")
        self.git("init", "-q", "-b", "main"); self.git("add", "."); self.git("commit", "-qm", "init")
        (self.old / "memory").mkdir()
        (self.old / "memory" / "serve.log").write_text("log\n")
        (self.old / ".env").write_text("HQ_TOKEN=secret\n")
        with contextlib.closing(sqlite3.connect(self.old / "memory" / "hq.db")) as con, con:
            con.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
            con.execute("CREATE TABLE dev_runs (id INTEGER PRIMARY KEY, task_id INTEGER, status TEXT, worktree TEXT)")
            con.execute("INSERT INTO dev_runs(task_id, status, worktree) VALUES (5, 'merged', ?)", (f"{self.old}/.devloop/wt-5",))
            con.execute("INSERT INTO state VALUES ('devloop_autonomy', 'continuous')")
        self.units = self.home / ".config" / "systemd" / "user"; self.units.mkdir(parents=True)
        self.unit_text = {
            "hq.service": "[Service]\nWorkingDirectory=%h/hq-system\nExecStart=%h/hq-system/atlas.sh serve\n",
            "atlas-hub.service": f"[Service]\nWorkingDirectory={self.old}\nExecStart=/bin/bash -c 'exec python3 -m services.atlas.hub'\n",
            "hq-recovery.timer": "[Timer]\nOnBootSec=2min\nUnit=hq-recovery.service\n",
            "other.service": "[Service]\nWorkingDirectory=%h/hq-system-old\n",
        }
        for name, text in self.unit_text.items():
            (self.units / name).write_text(text)
        (self.units / "default.target.wants").mkdir()
        (self.units / "default.target.wants" / "hq.service").symlink_to(self.units / "hq.service")
        self.cron = "\n".join([f"0 * * * * cd {self.old} && ./atlas.sh cycle  # hq", DEVLOOP.format(old=self.old), OTHER]) + "\n"
        (self.home / "crontab.txt").write_text(self.cron)
        (self.home / ".claude").mkdir()
        self.settings = '{"hooks": {"command": "cd ~/hq-system && python3 -m engine gate"}}\n'
        (self.home / ".claude" / "settings.json").write_text(self.settings)
        (self.home / "active").write_text("hq.service\natlas-hub.service\nhq-recovery.timer\n")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.old, env={**os.environ, **self.env},
                              capture_output=True, text=True, check=True).stdout.strip()

    def run_script(self, *args, code=0):
        r = subprocess.run(["python3", str(SCRIPT), *args], env=self.env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60, cwd=self.home)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout

    def state(self, root, key):
        with contextlib.closing(sqlite3.connect(root / "memory" / "hq.db")) as con:
            row = con.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def test_dry_run_lists_every_unit_cron_line_and_path_and_changes_nothing(self):
        out = self.run_script("--dry-run")
        self.assertIn(f"Folder: {self.old} -> {self.new}", out)
        self.assertIn(f"unit: {self.units / 'hq.service'}", out)
        self.assertIn("+ WorkingDirectory=%h/atlas", out)
        self.assertIn(f"unit: {self.units / 'atlas-hub.service'}", out)
        self.assertIn(f"+ WorkingDirectory={self.new}", out)
        self.assertIn(f"+ 0 * * * * cd {self.new} && ./atlas.sh cycle  # hq", out)
        self.assertIn(f"+ {DEVLOOP.format(old=self.new)}", out)
        self.assertIn(f"settings: {self.home / '.claude' / 'settings.json'}", out)
        self.assertIn("cd ~/atlas && python3 -m engine gate", out)
        self.assertIn("dev_runs 1", out)   # database rows naming the old folder: reported, kept
        self.assertIn("hq.service", out.split("Units stopped")[1].splitlines()[0])
        self.assertNotIn("other.service", out); self.assertNotIn("hq-system-old && ./backup", out.replace(OTHER, ""))
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink()); self.assertFalse(self.new.exists())
        self.assertEqual((self.home / "crontab.txt").read_text(), self.cron)
        self.assertEqual((self.units / "hq.service").read_text(), self.unit_text["hq.service"])
        self.assertFalse((self.home / "systemctl.log").read_text().count("stop"))

    def test_move_then_rollback(self):
        self.git("worktree", "add", "-q", "-b", "hq-dev/7", str(self.old / ".devloop" / "wt-7"))
        self.run_script()
        wt = self.new / ".devloop" / "wt-7"
        self.assertEqual((wt / ".git").read_text().strip(), f"gitdir: {self.new}/.git/worktrees/wt-7")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt), "hq-dev/7")
        self.assertTrue(self.old.is_symlink()); self.assertEqual(Path(os.readlink(self.old)), self.new)
        self.assertEqual((self.new / ".env").read_text(), "HQ_TOKEN=secret\n")
        self.assertEqual((self.new / "memory" / "serve.log").read_text(), "log\n")
        self.assertTrue(list((self.new / "memory").glob("hq-before-folder-move-*.db")))
        self.assertEqual((self.units / "hq.service").read_text(),
                         "[Service]\nWorkingDirectory=%h/atlas\nExecStart=%h/atlas/atlas.sh serve\n")
        self.assertIn(f"WorkingDirectory={self.new}\n", (self.units / "atlas-hub.service").read_text())
        self.assertEqual((self.units / "other.service").read_text(), self.unit_text["other.service"])
        self.assertTrue((self.units / "default.target.wants" / "hq.service").is_symlink())
        cron = (self.home / "crontab.txt").read_text()
        self.assertIn(DEVLOOP.format(old=self.new), cron); self.assertIn(OTHER, cron)
        self.assertNotIn(f"cd {self.old} ", cron)
        self.assertIn("cd ~/atlas &&", (self.home / ".claude" / "settings.json").read_text())
        log = (self.home / "systemctl.log").read_text()
        self.assertLess(log.index("stop hq-recovery.timer"), log.index("stop hq.service"))   # timers first
        self.assertIn("daemon-reload", log)
        self.assertEqual(set((self.home / "active").read_text().split()), {"hq.service", "atlas-hub.service", "hq-recovery.timer"})
        self.assertIsNone(self.state(self.new, "devloop_paused"))   # the dev loop setting is as before
        self.assertEqual(self.state(self.new, "devloop_autonomy"), "continuous")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.new), "main")
        self.assertIn("Nothing in units", self.run_script("--status"))
        self.run_script(code=1)   # a second move is refused

        out = self.run_script("--rollback")
        self.assertIn(f"Back in {self.old}", out)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink()); self.assertFalse(self.new.exists())
        for name, text in self.unit_text.items():
            self.assertEqual((self.units / name).read_text(), text)
        self.assertEqual((self.home / "crontab.txt").read_text(), self.cron)
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(), self.settings)
        self.assertEqual((self.old / ".env").read_text(), "HQ_TOKEN=secret\n")
        self.assertFalse((self.home / ".atlas-folder-move.json").exists())
        self.assertEqual((self.old / ".devloop" / "wt-7" / ".git").read_text().strip(), f"gitdir: {self.old}/.git/worktrees/wt-7")
        self.run_script("--rollback", code=1)

    def test_switches_to_main_when_main_contains_the_branch_and_rolls_back_to_it(self):
        self.git("switch", "-qc", "hq-dev/old")
        out = self.run_script()
        self.assertIn("Switched from hq-dev/old to main.", out)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.new), "main")
        self.run_script("--rollback")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "hq-dev/old")

    def test_refuses_a_branch_with_commits_main_lacks(self):
        self.git("switch", "-qc", "side"); (self.old / "x").write_text("x"); self.git("add", "x"); self.git("commit", "-qm", "x")
        self.assertIn("commits main does not have", self.run_script(code=1))
        self.assertFalse(self.new.exists())

    def test_refuses_while_a_build_runs_local_changes_or_an_existing_target(self):
        with contextlib.closing(sqlite3.connect(self.old / "memory" / "hq.db")) as con, con:
            con.execute("INSERT INTO dev_runs(task_id, status) VALUES (9, 'running')")
        (self.old / "atlas.sh").write_text("changed\n")
        self.new.mkdir()
        out = self.run_script("--dry-run", code=1)
        self.assertIn("task #9", out); self.assertIn("local code changes", out); self.assertIn("already exists", out)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())

    def test_a_unit_that_does_not_start_again_rolls_everything_back(self):
        (self.home / "broken").write_text("hq.service\n")
        out = self.run_script(code=1)
        self.assertIn("did not start again: hq.service", out); self.assertIn("rolling back", out)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink()); self.assertFalse(self.new.exists())
        self.assertEqual((self.units / "hq.service").read_text(), self.unit_text["hq.service"])
        self.assertEqual((self.home / "crontab.txt").read_text(), self.cron)
        self.assertIsNone(self.state(self.old, "devloop_paused"))

    def test_link_stays_while_something_uses_the_old_path(self):
        (self.old / ".env").write_text(f"HQ_TOKEN=secret\nHQ_DB={self.old}/memory/hq.db\n")
        out = self.run_script()
        self.assertIn(".env names the old folder on 1 line", out)
        self.assertEqual((self.new / ".env").read_text(), f"HQ_TOKEN=secret\nHQ_DB={self.old}/memory/hq.db\n")   # never edited
        self.assertIn(".env: 1 line", self.run_script("--drop-link", code=1))
        self.assertTrue(self.old.is_symlink())
        (self.new / ".env").write_text("HQ_TOKEN=secret\n")
        self.assertIn("Removed the link", self.run_script("--drop-link"))
        self.assertFalse(self.old.exists()); self.assertTrue((self.new / "memory" / "hq.db").exists())


class RewriteTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("move_to_atlas_folder", SCRIPT)
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        self.m.OLD, self.m.NEW = Path("/home/u/hq-system"), Path("/home/u/atlas")

    def test_every_spelling_of_the_folder_and_only_the_folder(self):
        r = self.m.rewrite
        self.assertEqual(r("cd /home/u/hq-system && x"), "cd /home/u/atlas && x")
        self.assertEqual(r("WorkingDirectory=%h/hq-system\n"), "WorkingDirectory=%h/atlas\n")
        self.assertEqual(r("cd ~/hq-system/memory; $HOME/hq-system"), "cd ~/atlas/memory; $HOME/atlas")
        for kept in ("~/hq-system-old", "/home/u/hq-system.bak", "/srv/x~/hq-systems", "/home/u/hq-system2"):
            self.assertEqual(r(kept), kept); self.assertFalse(self.m.mentions(kept))


class GatekeeperFolderTest(unittest.TestCase):
    def test_deny_rules_for_the_old_folder_hold_in_the_new_one_and_allow_rules_do_not_widen(self):
        from engine import gatekeeper
        cfg = {"workspace": "/home/pi",
               "deny": [{"tool": "Edit|Write|Bash", "pattern": "/hq-system/(config|memory)/", "why": "live data"}],
               "allow": [{"tool": "Edit|Write", "pattern": "/hq-system/", "why": "project"}]}
        d = lambda tool, **a: gatekeeper.decide(tool, a, cfg)[0]
        self.assertEqual(d("Edit", file_path="/home/pi/atlas/config/constitution.toml"), "deny")
        self.assertEqual(d("Bash", command="echo x > ~/atlas/memory/hq.db"), "deny")
        self.assertEqual(d("Edit", file_path="/home/pi/hq-system/memory/x"), "deny")
        self.assertEqual(d("Edit", file_path="/home/pi/atlas/engine/db.py"), "ask")   # not allowed by the old allow rule
        self.assertEqual(d("Edit", file_path="/home/pi/atlas/.devloop/wt-1/config/x.toml"), "ask")
        self.assertEqual(gatekeeper.old_names("cat /home/pi/atlasx/config/a"), "cat /home/pi/atlasx/config/a")


if __name__ == "__main__":
    unittest.main()
