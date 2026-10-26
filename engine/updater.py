"""Updates: bring merged work from GitHub onto the Pi and restart Atlas, safely.

`check()`  fetches origin/main and lists the new commits.
`apply()`  installs them only when it is safe:
  - the Pi has no hand-made changes to tracked files and no local commits (it never overwrites or
    discards anything; it stops and says what to do instead),
  - the full test suite passes on the new version, run in a separate temporary copy first,
then fast-forwards to origin/main and restarts the dashboard and the Telegram bot
(deploy/restart.sh, detached), so the new code is live within seconds.

Self-repair (cron only, `auto()`): a blocked update is repaired once by saving the hand-made changes and
local commits on a backup branch `hq-backup/<time>` (nothing is lost; restore with `git merge <branch>`)
and going back to the last commit shared with GitHub; files edited in the last QUIET_MINUTES are left
alone, Djeddi may still be working on them. Failing tests on a new version are re-run once (a flaky test,
a busy Pi). Djeddi gets a message only when the repair fails too; a saved backup is noted in his brief.
When the tests still fail after the re-run, the code on GitHub itself is broken: Atlas queues one AI task
"Repair failing tests on main" with the failing output, so the dev loop fixes it in a pull request
(`queue_repair()`; one open repair task at a time, updated with the latest output for a newer version).

Triggers: the Update button on the dashboard, /update in Telegram, `hq update`, and the cron job every
15 minutes (`hq update --auto`), which replaced the plain `git pull`.
"""
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import db

ROOT = db.ROOT
QUIET_MINUTES = 30  # hand-made changes younger than this are not moved aside: Djeddi may be mid-edit
OUTPUT_LINES = 60   # lines of failing test output kept for the repair task (the message shows the last 6)
REPAIR_TITLE = "Repair failing tests on main"


def _git(*args, timeout: int = 120, check: bool = True, env: dict | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                       env=(os.environ | env) if env else None)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout.rstrip("\n")  # not strip(): `git status --porcelain` lines start with a meaningful space


def version() -> dict:
    try:
        sha, date, subject = _git("log", "-1", "--format=%h%x09%cs%x09%s").split("\t", 2)
        return {"sha": sha, "date": date, "subject": subject}
    except (RuntimeError, ValueError, OSError):
        return {"sha": "?", "date": "", "subject": ""}


def check(fetch: bool = True) -> dict:
    """What is new on GitHub, and can it be installed?"""
    if fetch:
        _git("fetch", "-q", "origin", "main", timeout=180)
    new = [line.split("\t", 1) for line in _git("log", "--format=%h%x09%s", "HEAD..origin/main").splitlines() if line]
    ahead = int(_git("rev-list", "--count", "origin/main..HEAD") or 0)
    dirty = _dirty()
    blocker = ""
    if dirty:
        blocker = ("Files were changed by hand on the Pi: " + ", ".join(dirty[:5])
                   + ". Save them first (tell Claude what you changed), or undo with: git checkout -- <file>")
    elif ahead:
        blocker = (f"The Pi has {ahead} commit(s) that are not on GitHub. Keep them with "
                   "`git branch backup-$(date +%s)`, then `git reset --hard origin/main`.")
    return {"current": version(), "behind": len(new), "commits": [{"sha": s, "subject": t} for s, t in new][:20],
            "blocker": blocker}


def local_status() -> dict:
    """Is an update waiting? From the refs the 15-minute job already fetched: no network, cheap enough for every page load."""
    try:
        behind = int(_git("rev-list", "--count", "HEAD..origin/main", timeout=10) or 0)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        behind = 0
    return {"behind": behind}


def _tests_pass_on(ref: str) -> tuple[bool, str]:
    """Run the test suite on `ref` in a temporary worktree, so the live copy is untouched."""
    tmp = Path(tempfile.mkdtemp(prefix="hq-update-"))
    tree = tmp / "tree"
    try:
        _git("worktree", "add", "-q", "--detach", str(tree), ref)
        env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TZ") if k in os.environ}
        env["HQ_DB"] = str(tmp / "test.db")
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=tree, env=env,
                           stdin=subprocess.DEVNULL,  # a test waiting on input must fail fast, never hang on a terminal
                           capture_output=True, text=True, timeout=900)
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-OUTPUT_LINES:])
        return r.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, "tests took longer than 15 minutes"
    finally:
        _git("worktree", "remove", "--force", str(tree), check=False)
        subprocess.run(["rm", "-rf", str(tmp)], check=False)


def restart() -> None:
    """Restart the dashboard and the bot in a detached process (this process may be one of them)."""
    script = ROOT / "deploy" / "restart.sh"
    subprocess.Popen(["bash", str(script)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def apply(con, do_restart: bool = True) -> dict:
    info = check()
    if not info["behind"]:
        return info | {"status": "up_to_date", "message": f"Atlas is up to date ({info['current']['sha']})."}
    if info["blocker"]:
        return info | {"status": "blocked", "message": "Update not installed. " + info["blocker"]}
    sha = _git("rev-parse", "origin/main")  # one fixed commit: the one tested is the one installed
    target = sha[:7]
    ok, tail = _tests_pass_on(sha)
    if not ok:
        db.log(con, "hq", "update_tests_failed", target)
        con.commit()
        short = "\n".join(tail.splitlines()[-6:])
        return info | {"status": "tests_failed", "target": target, "output": tail,
                       "message": f"Update {target} not installed: its tests fail on the Pi.\n{short}"}
    old = info["current"]["sha"]
    _git("merge", "-q", "--ff-only", sha)
    titles = "\n".join(f"• {c['subject']}" for c in info["commits"][:8])  # may list less than sha if main moved meanwhile
    db.log(con, "hq", "update", f"{old} → {target}")
    con.commit()
    if do_restart:
        restart()
    return info | {"status": "updated", "target": target,
                   "message": f"Atlas updated {old} → {target} ({info['behind']} change(s)), restarting now:\n{titles}"}


def _dirty() -> list[str]:
    return [line[3:].split(" -> ")[-1] for line in _git("status", "--porcelain", "--untracked-files=no").splitlines()]


def set_aside(con, quiet_minutes: int = QUIET_MINUTES) -> str:
    """Repair a blocked update without losing anything: commit hand-made changes and keep local commits on
    a new branch hq-backup/<time>, then reset the checkout to the last commit it shares with origin/main.
    Returns the backup branch, or '' when nothing was done (nothing to save, or files edited too recently)."""
    dirty = _dirty()
    ahead = int(_git("rev-list", "--count", "origin/main..HEAD") or 0)
    if not dirty and not ahead:
        return ""
    recent = time.time() - quiet_minutes * 60
    if any((ROOT / f).exists() and (ROOT / f).stat().st_mtime > recent for f in dirty):
        return ""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch, saved = f"hq-backup/{stamp}", _git("rev-parse", "HEAD")
    if dirty:  # a normal commit built in a separate index: the checkout itself is not touched yet
        tmp = Path(tempfile.mkdtemp(prefix="hq-backup-"))
        try:
            env = {"GIT_INDEX_FILE": str(tmp / "index")}
            _git("read-tree", "HEAD", env=env)
            _git("add", "-u", env=env)
            tree = _git("write-tree", env=env)
            saved = _git("-c", "user.name=Atlas", "-c", "user.email=hq@localhost", "commit-tree", tree, "-p", "HEAD",
                         "-m", f"Atlas self-repair: hand-made changes saved before an update ({', '.join(dirty[:5])})")
        finally:
            subprocess.run(["rm", "-rf", str(tmp)], check=False)
    _git("branch", branch, saved)
    _git("reset", "-q", "--hard", _git("merge-base", "HEAD", "origin/main"))  # saved on the branch first: nothing lost
    note = (f"Update self-repair: {len(dirty)} hand-edited file(s) and {ahead} local commit(s) were saved on branch "
            f"{branch} so the update could install. Restore with: git merge {branch}")
    db.insert(con, "memories", kind="info", content=note)
    db.log(con, "hq", "update_set_aside", branch)
    con.commit()
    return branch


def queue_repair(con, target: str, output: str) -> int:
    """Queue (or refresh) the AI task that repairs failing tests on origin/main. Returns its id.
    The dev loop branches from origin/main, so the agent works on exactly the broken version."""
    description = (f"The tests of origin/main ({target}) fail on the Pi, so Atlas cannot install the update. "
                   "Run `python3 -m unittest discover tests`, find the cause and fix it; change a test only when "
                   "the test itself is wrong.\n\nFailing output (last lines):\n" + output.strip())
    row = con.execute("SELECT id FROM tasks WHERE title = ? AND owner = 'ai' AND status NOT IN ('done', 'dropped')"
                      " ORDER BY id LIMIT 1", (REPAIR_TITLE,)).fetchone()
    if row:
        db.update(con, "tasks", row["id"], description=description)
        tid = row["id"]
    else:
        tid = db.insert(con, "tasks", title=REPAIR_TITLE, description=description, owner="ai",
                        impact=5, effort_h=1, confidence=0.8)
    db.log(con, "hq", "update_repair_queued", f"task #{tid} for {target}")
    con.commit()
    return tid


def auto(con) -> str | None:
    """Cron: install quietly; return a message only when something happened worth telling Djeddi.
    A blocked update or failing tests get one repair attempt first (see the module docstring)."""
    saved = ""
    try:
        result = apply(con)
        latest = _git("rev-parse", "--short", "origin/main", check=False)
        if result["status"] == "blocked":
            saved = set_aside(con)
            if not saved:
                return None  # edited recently: try again on a later run, once the files are quiet
            result = apply(con)
        elif result["status"] == "tests_failed" and db.get_state(con, "update_last_warning", "") != f"tests_failed:{latest}":
            db.log(con, "hq", "update_retry", latest)
            result = apply(con)  # one re-run per version: a flaky test or a busy Pi
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return None if "Could not resolve host" in str(e) else f"Atlas update check failed: {e}"
    if saved:
        result["message"] += f"\nHand-made changes on the Pi were saved on branch {saved} (restore: git merge {saved})."
    if result["status"] == "updated":
        return result["message"]
    if result["status"] in ("blocked", "tests_failed"):
        key = f"{result['status']}:{_git('rev-parse', '--short', 'origin/main', check=False)}"
        if db.get_state(con, "update_last_warning", "") != key:  # tell once per new version, not every 15 minutes
            db.set_state(con, "update_last_warning", key)
            con.commit()
            if result["status"] == "tests_failed":
                tid = queue_repair(con, result["target"], result.get("output", ""))
                result["message"] += f"\nHQ queued AI task #{tid} ({REPAIR_TITLE}) with the failing output."
            return result["message"]
    return None
