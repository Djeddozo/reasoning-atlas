import os, tempfile, unittest
from pathlib import Path
from unittest import mock

from engine import launch


class Launch(unittest.TestCase):
    def test_own_systemd_unit_under_systemd(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"INVOCATION_ID": "x"}), \
             mock.patch.object(launch.shutil, "which", return_value="/usr/bin/systemd-run"), \
             mock.patch.object(launch.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
             mock.patch.object(launch.subprocess, "Popen") as popen:
            self.assertEqual(launch.detached(["engine", "dev", "run", "--task", "7"], Path(d) / "x.log", "build"), "unit")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["systemd-run", "--user", "--collect", "--quiet"])
        self.assertIn("-m engine dev run --task 7", argv[-1])
        popen.assert_not_called()

    def test_plain_process_elsewhere(self):
        env = {k: v for k, v in os.environ.items() if k != "INVOCATION_ID"}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(launch.subprocess, "Popen") as popen:
            self.assertEqual(launch.detached(["engine", "dev", "resolve", "3"], Path(d) / "x.log"), "process")
        self.assertEqual(popen.call_args.args[0][-4:], ["engine", "dev", "resolve", "3"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
