"""deploy/schedules.sh: installs Atlas's cron lines (incl. brain think/sort), keeps other lines, idempotent.
Runs against a fake `crontab` command, so the Pi's real crontab is never touched."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE_CRONTAB = """#!/usr/bin/env bash
if [ "$1" = "-l" ]; then [ -f "$CRONTAB_FILE" ] || exit 1; cat "$CRONTAB_FILE"
elif [ "$1" = "-" ]; then cat > "$CRONTAB_FILE"; echo x >> "$CRONTAB_FILE.writes"
else exit 2; fi
"""
DEVLOOP = "17 */3 * * * cd /x && ./hq.sh dev run >> memory/devloop.log 2>&1  # hq-devloop"


class SchedulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp)
        self.hq = self.tmp / "hq"; (self.hq / "deploy").mkdir(parents=True)
        shutil.copy(ROOT / "deploy" / "schedules.sh", self.hq / "deploy")
        shutil.copy(ROOT / "deploy" / "systemd-lib.sh", self.hq / "deploy")
        (self.hq / ".env").write_text("")
        bin_ = self.tmp / "bin"; bin_.mkdir()
        (bin_ / "crontab").write_text(FAKE_CRONTAB); (bin_ / "crontab").chmod(0o755)
        # systemctl decides whether systemd manages the dashboard and bot: not managed unless a test says so
        (bin_ / "systemctl").write_text('#!/usr/bin/env bash\n[ -f "$HOME/managed" ]\n'); (bin_ / "systemctl").chmod(0o755)
        self.cron = self.tmp / "crontab.txt"
        self.env = {"PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(self.tmp), "CRONTAB_FILE": str(self.cron)}

    def run_script(self):
        r = subprocess.run(["bash", str(self.hq / "deploy" / "schedules.sh")], env=self.env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def lines(self):
        return self.cron.read_text().splitlines()

    def writes(self):
        w = Path(f"{self.cron}.writes")
        return len(w.read_text().splitlines()) if w.exists() else 0

    def test_adds_brain_lines_and_keeps_other_lines(self):
        # The Pi's crontab before task #39: an older Atlas set without brain think/sort, plus the dev loop.
        self.cron.write_text(f"{DEVLOOP}\n0 * * * * cd /old && ./hq.sh cycle  # hq\n")
        self.assertEqual(self.run_script(), "Schedules installed")
        lines = self.lines()
        self.assertIn(DEVLOOP, lines)
        self.assertFalse(any("/old" in l for l in lines))  # old Atlas lines replaced, not duplicated
        think = [l for l in lines if "atlas.sh brain think" in l]
        sort = [l for l in lines if "atlas.sh brain sort" in l]
        self.assertEqual(len(think), 1); self.assertTrue(think[0].startswith("35 6 * * * "))
        self.assertEqual(len(sort), 1); self.assertTrue(sort[0].startswith("25 * * * * "))
        revisit = [l for l in lines if "atlas.sh revisit run --due" in l]  # daily check, runs only when due (#79)
        self.assertEqual(len(revisit), 1); self.assertTrue(revisit[0].startswith("40 5 * * * "))
        revalidate = [l for l in lines if "atlas.sh revalidate run --due" in l]  # daily check, runs only when due (#88)
        self.assertEqual(len(revalidate), 1); self.assertTrue(revalidate[0].startswith("45 5 * * * "))
        self.assertTrue(all(l.endswith("# hq") for l in lines if l != DEVLOOP))
        self.assertTrue(all(f"cd {self.hq} " in l or l.startswith("30 3 ") for l in lines if l.endswith("# hq")))

    def test_second_run_changes_nothing(self):
        self.run_script()
        before, writes = self.cron.read_text(), self.writes()
        self.assertEqual(self.run_script(), "Schedules up to date")
        self.assertEqual((self.cron.read_text(), self.writes()), (before, writes))

    def test_telegram_lines_only_with_credentials(self):
        self.run_script()
        self.assertFalse(any("atlas.sh bot" in l for l in self.lines()))
        (self.hq / ".env").write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n")
        self.run_script()
        bot = [l for l in self.lines() if "atlas.sh bot" in l]
        self.assertEqual(len(bot), 2)
        self.assertIn("pgrep -f 'python3 -m (hq|engine) bot$'", bot[0])

    def test_systemd_unit_owns_dashboard_and_bot(self):
        """With hq.service enabled, cron must not start a second dashboard or bot (they crash-looped on the port)."""
        (self.hq / ".env").write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n")
        self.run_script()
        self.assertTrue(any(l.startswith("@reboot") for l in self.lines()))
        (self.tmp / "managed").write_text("")
        self.assertEqual(self.run_script(), "Schedules installed")
        lines = self.lines()
        self.assertFalse(any(l.startswith("@reboot") or "pgrep" in l for l in lines))
        self.assertEqual(len([l for l in lines if "bot --send-brief" in l]), 1)  # the morning brief stays
        self.assertTrue(any("--auto >>" in l for l in lines))  # and the 15-minute self-update

    def test_restart_defers_to_systemd(self):
        """restart.sh asks systemd to restart hq.service instead of starting copies next to it."""
        hq = self.hq; shutil.copy(ROOT / "deploy" / "restart.sh", hq / "deploy"); (hq / "memory").mkdir()
        bin_ = self.tmp / "bin"
        (bin_ / "systemctl").write_text('#!/usr/bin/env bash\necho "$@" >> "$HOME/systemctl.log"\n[ "$2" != is-active ] || [ -f "$HOME/managed" ]\n')
        for name in ("pkill", "sleep", "nohup"):
            (bin_ / name).write_text(f'#!/usr/bin/env bash\necho {name} >> "$HOME/other.log"\n'); (bin_ / name).chmod(0o755)
        (self.tmp / "managed").write_text("")
        run_env = {"PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(self.tmp), "CRONTAB_FILE": str(self.cron)}
        r = subprocess.run(["bash", str(hq / "deploy" / "restart.sh")], env=run_env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--user --no-block restart hq.service", (self.tmp / "systemctl.log").read_text())
        other = (self.tmp / "other.log").read_text() if (self.tmp / "other.log").exists() else ""
        self.assertNotIn("pkill", other); self.assertNotIn("nohup", other)

    def test_unit_file_counts_when_systemctl_cannot_answer(self):
        """From cron, systemctl --user had no bus and failed: the installed unit file still means systemd runs Atlas,
        so no @reboot serve or bot watchdog line comes back (they started stray copies that held the port)."""
        (self.tmp / "bin" / "systemctl").write_text("#!/usr/bin/env bash\nexit 1\n")
        unit = self.tmp / ".config" / "systemd" / "user"; unit.mkdir(parents=True); (unit / "hq.service").write_text("")
        (self.hq / ".env").write_text("TELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=1\n")
        self.run_script()
        text = self.cron.read_text()
        self.assertNotIn("atlas.sh serve", text); self.assertNotIn("pgrep", text)
        self.assertIn("delivery sync", text)

    def test_restart_from_cron_without_bus_defers_to_systemd(self):
        """restart.sh with systemctl unable to answer (cron) but the unit installed: systemd restart, no copies."""
        hq = self.hq; shutil.copy(ROOT / "deploy" / "restart.sh", hq / "deploy"); (hq / "memory").mkdir()
        bin_ = self.tmp / "bin"
        (bin_ / "systemctl").write_text('#!/usr/bin/env bash\necho "$@" >> "$HOME/systemctl.log"\ncase "$*" in *" restart "*) exit 0;; esac\nexit 1\n')
        for name in ("pkill", "sleep", "nohup"):
            (bin_ / name).write_text(f'#!/usr/bin/env bash\necho {name} >> "$HOME/other.log"\n'); (bin_ / name).chmod(0o755)
        (bin_ / "pgrep").write_text("#!/usr/bin/env bash\nexit 1\n"); (bin_ / "pgrep").chmod(0o755)
        unit = self.tmp / ".config" / "systemd" / "user"; unit.mkdir(parents=True); (unit / "hq.service").write_text("")
        r = subprocess.run(["bash", str(hq / "deploy" / "restart.sh")], capture_output=True, text=True, timeout=30,
                           env={"PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(self.tmp), "CRONTAB_FILE": str(self.cron)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restart hq.service", (self.tmp / "systemctl.log").read_text())
        other = (self.tmp / "other.log").read_text() if (self.tmp / "other.log").exists() else ""
        self.assertNotIn("nohup", other)

    def test_setup_and_restart_use_it(self):
        for name in ("setup-pi.sh", "restart.sh"):
            self.assertIn("deploy/schedules.sh", (ROOT / "deploy" / name).read_text())
        self.assertNotIn("| crontab -", (ROOT / "deploy" / "setup-pi.sh").read_text())  # one place for the schedule


if __name__ == "__main__":
    unittest.main()
