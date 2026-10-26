"""Start a long job that must outlive the process that asked for it (a build, a conflict resolution, an update).

Under systemd (the Pi), a background child stays in the cgroup of the service that started it, so restarting that
service (the dashboard, the bot, the hub after an update) would stop the job too. There the job runs as its own
transient unit (`systemd-run --user --collect`); elsewhere as a plain detached process. Output goes to `log`.
"""
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .db import ROOT


def detached(args: list[str], log: Path, name: str = "job") -> str:
    """Run `python -m <args>` from the repository root, detached; returns how it was started ('unit' or 'process')."""
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", *args]
    if os.environ.get("INVOCATION_ID") and shutil.which("systemd-run"):
        env = [f"--setenv={k}={os.environ[k]}" for k in ("HQ_DB", "HQ_ACTOR", "PATH", "HOME") if os.environ.get(k)]
        shell = f"exec {' '.join(shlex.quote(c) for c in cmd)} >> {shlex.quote(str(log))} 2>&1"
        r = subprocess.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit=atlas-{name}-{int(time.time() * 1000)}",
                            f"--working-directory={ROOT}", *env, "/bin/bash", "-c", shell],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        if r.returncode == 0:
            return "unit"
    with open(log, "ab") as out:
        subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    return "process"
