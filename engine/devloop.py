"""Dev loop: Claude Code on the Pi works through Atlas's plan, one task per run, under Djeddi's control.

It is OFF until Djeddi switches it on himself (`bash deploy/enable-devloop.sh`, which adds the
[devloop] section and extra gatekeeper rules to the constitution and a cron line, after asking).

Each run (cron every 3 hours, or "Run now" on the dashboard / `/dev run` in Telegram):
  1. Notice merged work: a run whose branch is now in origin/main marks its task done.
  2. Pick the top open task owned by 'ai' that has no open PR and has not failed twice.
  3. Create a separate git worktree on a new branch under .devloop/, so the live checkout is untouched.
  4. Run Claude Code headless (`claude -p`, stream output) in that worktree with time and turn limits.
     Every tool call goes through the gatekeeper hook. The agent gets a minimal environment: no API
     keys, bot tokens or other secrets, so it uses the Claude subscription it is logged in with.
     Its steps are written to a live log that the dashboard (System tab) shows while it works.
  5. Atlas, not the agent, checks the result: commits present → full test suite → if green, push the
     branch and open a pull request (`gh` if installed, else a compare link). Never pushes to main,
     never merges.
  6. Self-repair: if the agent crashed, or its tests fail, it gets exactly one repair run in the same
     worktree with the error in its prompt. Djeddi hears of a failure only when the repair fails too.
  7. Record the run in `dev_runs` and tell Djeddi on Telegram when it starts and when it ends.

Review batch: the agent stops starting runs once N of its pull requests wait for review (1 = stop at
every PR, 0 = Unlimited: never stops). Djeddi picks N on Builder settings (or `hq dev batch N|unlimited`), up to
`max_open_prs` in [devloop] when that is set (no ceiling otherwise).
With a batch above 1, a new branch is stacked on the newest unreviewed PR branch (with origin/main merged
in), so each later PR also contains the earlier unmerged work; merging the newest PR merges the whole stack.
If main does not merge cleanly into that branch, the new branch starts from origin/main as before.

Merge conflicts: hourly and after each fetch Atlas checks every open PR against origin/main (`git merge-tree`, nothing
is written) and records the conflicting files. When `gh` reaches GitHub, its live state is used as well: PRs closed
without merging are closed here (the task returns to the queue), merged ones are marked done, and a PR's current
head is checked even when it was pushed from elsewhere. System tab → Scan for conflicts (`hq dev conflicts`) does
this on demand. Each conflicting PR has a Resolve button (`hq dev resolve RUN`): one agent run merges origin/main
into the PR branch, resolves the conflicts, passes the tests, and Atlas pushes the merge commit to the same branch, so
the existing PR updates in place for Djeddi's review. In a stack, resolving the newest PR is enough when it is
merged (it contains the ones below); a PR lower in the stack can be resolved on its own too.

Daily limits: the agent starts no new run once today's runs, tokens (in + out) or estimated spend (USD,
API-equivalent) reach their limit; a run that is already going finishes. Djeddi sets the limits on the System
tab (or `hq dev limit runs|tokens|usd N`, `/dev limit` in Telegram), never above the ceilings in [devloop]
(max_runs_per_day, max_tokens_per_day, max_usd_per_day; missing = no ceiling, so "No limit" is unlimited;
0 = no ceiling for tokens and spend, no runs at all for runs). When a ceiling lowers his choice, the limits text says so. A forced run
("Run now") ignores the daily limits, not the kill switch.

Controls: pause / resume (new runs), run now, stop (ends the current run at once) — from the dashboard,
Telegram (`/dev`, `/dev pause`, `/dev resume`, `/dev run`, `/dev stop`) or the CLI (`hq dev ...`).
"""
import fcntl
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import date, datetime
from pathlib import Path

from . import buildsteps, checkpoint, db, loop, mockup, overlap, prereq, priority, routing, usage, workers

ROOT = db.ROOT
WORK = ROOT / ".devloop"
LOCK = WORK / "lock"
OPEN_STATES = ("running", "pr_open", "merged")
WATCH_SECONDS = 3  # how often the watchdog checks the time limit and the kill switch
EXIT_GRACE = 5  # seconds Claude Code gets to exit by itself after its final result
# max_runs_per_day and max_open_prs have no default on purpose (#178): without them in [devloop] there is no
# ceiling, so "No limit" and "Unlimited" on Builder settings mean exactly that. When set, they cap Djeddi's choice.
DEFAULTS = {"enabled": False, "max_minutes": 40, "max_turns": 60,
            "branch_prefix": "hq-dev/", "claude_command": "claude",
            "approve_over_hours": 4,
            "max_tokens_per_day": 0, "max_usd_per_day": 0,
            "build_next_max": 5}  # places in the Build next lane (#182)  # ceilings for the daily limits (0 = no ceiling)  # bigger tasks (and products, new capabilities, external steps) wait for Djeddi's OK

PROMPT = """You are Atlas's developer, working on Djeddi's Raspberry Pi without supervision.
You are in a git worktree of the Atlas repository on branch {branch}. Work only inside this folder.

Task #{id}: {title}
{description}
{stack}{overlap}{memory}{resume}
How to work:
- Read CLAUDE.md first and follow every rule in it (proposals not actions, stdlib only, tests, secrets only in .env).
- Anything Djeddi sees belongs in Atlas's screen: apps/web/src (React + TypeScript; run `python3 scripts/build_web.py`),
  served by services/atlas, with Engine functions exposed through services/atlas/hub.py and registered in
  modules/engine.json. Place it where docs/STRUCTURE.md says. engine/dashboard.html is the old dashboard and is
  frozen: never add features to it.
- HQ_DB points to a private snapshot of the plan, not the live database. All Atlas commands and
  migrations in this worktree affect that snapshot only. Never open the live repository's memory files.
- Build a solid first slice of this task that can be finished and tested now. Include larger follow-ups
  and lessons in your final summary/PR; local task additions are experiments and are not published.
- Write the code yourself (Python stdlib only; the web app only with packages already in apps/web/package.json).
  Do not add packages, dependencies, copied snippets or repositories from online. If outside code seems
  unavoidable, do not add it: describe it in your summary (what, where from, why no own version works).
  It then goes through the security gate (trust, licence, scan, sandbox) and Djeddi's manual approval.
- Add or update tests in tests/ and run `python3 -m unittest discover tests` until everything passes.
- Commit your work on this branch with a clear message. Do not push, merge, rebase or switch branches:
  Atlas checks your work and opens the pull request itself.
- If the task needs a conceptual decision from Djeddi, stop and describe the question and options
  in your final summary. Do not wait for approvals in the private database.
- If the task cannot be done here (needs hardware, an account, money, or Djeddi himself), make no changes
  and explain why.
Finish with three short lines: what you built, how Djeddi tests it on the Pi, what he needs to provide."""

REPAIR_PROMPT = """You are Atlas's developer, working on Djeddi's Raspberry Pi without supervision.
You are in a git worktree of the Atlas repository on branch {branch}. Work only inside this folder.

A first attempt at task #{id}: {title} did not pass Atlas's checks. This is the one automatic repair attempt.
What went wrong:
{problem}

How to work:
- HQ_DB is the same private snapshot as the first attempt. Never open live memory files;
  report follow-ups and questions in your summary, not through the snapshot's approval queue.
- Read CLAUDE.md and `git log {start}..HEAD` to see what the first attempt did; uncommitted work may be present.
- Find and fix the cause. Change a test only when the test itself is wrong.
- Write the code yourself: no new packages, dependencies or code copied from online (security gate, #75).
- Run `python3 -m unittest discover tests` until everything passes, then commit on this branch.
  Do not merge, rebase or switch branches; Atlas checks the result itself.
- If it cannot be repaired here, make no further changes and explain why.
Finish with three short lines: what you built, how Djeddi tests it on the Pi, what he needs to provide."""

RESOLVE_PROMPT = """You are Atlas's developer, working on Djeddi's Raspberry Pi without supervision.
You are in a git worktree of the Atlas repository on branch {branch}, the pull request for task #{id}: {title}.
Work only inside this folder.

Atlas merged origin/main into this branch and the merge stopped with conflicts in:
{files}

How to work:
- Read CLAUDE.md. HQ_DB is a private snapshot; never open live memory files.
- See what each side meant: `git log --oneline HEAD..MERGE_HEAD` (new on main) and `git diff MERGE_HEAD...HEAD` (this PR).
- Resolve every conflict so both the work on main and this PR's work are kept. Where main already does
  the same thing differently, keep main's version and drop this branch's duplicate.
- Change nothing beyond what the conflicts need.
- Run `python3 -m unittest discover tests` until everything passes, then `git add` the files and conclude the
  merge with `git commit --no-edit`. Do not push, rebase, abort the merge or switch branches.
- If a conflict needs a decision from Djeddi, leave the merge unfinished and explain the choice in your summary.
Finish with three short lines: what you resolved and how, how Djeddi checks it, what he needs to decide."""


# ---------------- settings and controls ----------------

def cfg() -> dict:
    return DEFAULTS | loop.load_constitution().get("devloop", {})


CLAUDE_DIRS = ("~/.local/bin", "~/.claude/local", "/usr/local/bin", "~/.npm-global/bin")


def claude_path(conf: dict | None = None) -> str | None:
    """Find Claude Code, also where its installer puts it (cron, the dashboard and the bot have a short PATH)."""
    name = (conf or cfg())["claude_command"]
    found = shutil.which(name)
    if found or os.sep in name:
        return found
    for d in CLAUDE_DIRS:
        candidate = Path(os.path.expanduser(d)) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


LEVELS = ["off", "ask", "limits", "continuous"]
DONE_STATUSES = ("done", "dropped")  # a stopped task in one of these no longer waits to be resumed
LEVEL_TEXT = {"off": "Off: the agent does not start runs.",
              "ask": "Ask first: before each run you get Approve/Deny on your phone.",
              "limits": "Within limits: runs on schedule (every 3 hours), up to the daily limit.",
              "continuous": "Continuous: starts the next task as soon as one is done, up to the daily limit."}


def autonomy(con) -> str:
    """The agent's autonomy level, set with the slider on Today (or /autonomy). Off = paused."""
    if paused(con):
        return "off"
    level = db.get_state(con, "devloop_autonomy", "limits")
    return level if level in LEVELS and level != "off" else "limits"  # resumed elsewhere: no longer off


def set_autonomy(con, level: str) -> str:
    if level not in LEVELS:
        raise ValueError(f"autonomy must be one of: {', '.join(LEVELS)}")
    db.set_state(con, "devloop_autonomy", level)
    db.set_state(con, "devloop_paused", "1" if level == "off" else "0")
    db.log(con, "me", "devloop_autonomy", level)
    con.commit()
    return LEVEL_TEXT[level]


BATCH_CHOICES = (1, 3, 5, 0)  # 0 = Unlimited: never stops for review
CEILING_FILE = "config/local.toml"  # where enable-devloop.sh puts the Pi's [devloop] ceilings


def batch_ceiling() -> int | None:
    """max_open_prs in [devloop], or None when it is not set (no ceiling)."""
    n = cfg().get("max_open_prs")
    return None if n is None else max(1, int(n))


def batch_choice(con) -> int:
    """Djeddi's own review batch (0 = Unlimited), before any ceiling."""
    try:
        n = int(db.get_state(con, "devloop_batch", "") or 3)
    except ValueError:
        n = 3
    return max(0, n)


def batch_size(con) -> int:
    """How many unreviewed PRs the agent may leave before it stops for review; 0 = no stop (never above the ceiling)."""
    n, ceiling = batch_choice(con), batch_ceiling()
    if ceiling is None:
        return n
    return ceiling if n == 0 else min(n, ceiling)


def set_batch(con, n) -> str:
    n = 0 if str(n).strip().lower() in ("unlimited", "no limit", "none") else int(n)
    ceiling = batch_ceiling()
    if n < 0 or (ceiling is not None and n > ceiling):
        raise ValueError(f"review batch must be 0 (unlimited) or between 1 and {ceiling} (max_open_prs in [devloop])"
                         if ceiling is not None else "review batch must be 0 (unlimited) or a positive number")
    db.set_state(con, "devloop_batch", str(n))
    db.log(con, "me", "devloop_batch", str(n) if n else "unlimited")
    con.commit()
    return batch_text(batch_size(con), capped=batch_capped(con))


def batch_capped(con) -> bool:
    """True when Djeddi's choice is lowered by max_open_prs (e.g. Unlimited chosen, but the ceiling is 5)."""
    return batch_size(con) != batch_choice(con)


def batch_text(n: int, capped: bool = False) -> str:
    text = ("Never stops for review: keeps building while PRs wait." if n == 0 else
            "Stops for review after every PR." if n == 1 else
            f"Keeps building until {n} PRs wait for your review, then stops.")
    if capped:
        text += (f" Capped at {n} by max_open_prs = {n} in [devloop] ({CEILING_FILE}); remove that line for your"
                 " own choice to hold: bash deploy/builder-unlimited.sh")
    return text


def open_prs(con) -> int:
    """The agent's PRs that wait for review: not merged yet, and the task not done or dropped by Djeddi."""
    return con.execute("""SELECT COUNT(DISTINCT r.task_id) FROM dev_runs r JOIN tasks t ON t.id = r.task_id
                          WHERE r.status = 'pr_open' AND t.status NOT IN ('done', 'dropped')""").fetchone()[0]


def batch_full(con) -> bool:
    n = batch_size(con)
    return n > 0 and open_prs(con) >= n


def paused(con) -> bool:
    return db.get_state(con, "devloop_paused", "0") == "1"


def set_paused(con, value: bool) -> None:
    """Pause/resume (System tab, /dev). Kept in step with the autonomy slider: pause = off, resume from off = limits."""
    db.set_state(con, "devloop_paused", "1" if value else "0")
    level = db.get_state(con, "devloop_autonomy", "limits")
    if value:
        db.set_state(con, "devloop_autonomy", "off")
    elif level == "off":
        db.set_state(con, "devloop_autonomy", "limits")
    db.log(con, "me", "devloop_pause" if value else "devloop_resume", "")
    con.commit()


def runs_today(con) -> int:
    return con.execute("SELECT COUNT(*) FROM dev_runs WHERE started >= ?", (date.today().isoformat(),)).fetchone()[0]


LIMITS = {"runs": ("max_runs_per_day", "runs"), "tokens": ("max_tokens_per_day", "tokens"),
          "usd": ("max_usd_per_day", "USD")}


def used_today(con) -> dict:
    """What the agent used today: runs, tokens (in + out) and estimated cost."""
    r = con.execute("""SELECT COUNT(*), COALESCE(SUM(tokens_in), 0) + COALESCE(SUM(tokens_out), 0), COALESCE(SUM(cost_usd), 0)
                       FROM dev_runs WHERE started >= ?""", (date.today().isoformat(),)).fetchone()
    return {"runs": r[0], "tokens": int(r[1]), "usd": round(float(r[2]), 2)}


def _ceiling(kind: str):
    """The ceiling in [devloop] for a daily limit, or None when there is none.
    Tokens and spend: 0 or missing = none. Runs: missing = none, 0 = no runs at all (the builder is held)."""
    raw = cfg().get(LIMITS[kind][0])
    if kind == "runs":
        return None if raw is None else max(0, int(float(raw)))
    n = float(raw or 0)
    n = max(0, int(n)) if kind != "usd" else max(0.0, n)
    return n or None


DEFAULT_RUNS = 3  # runs a day until Djeddi chooses his own limit


def _mine(con) -> dict:
    try:
        return json.loads(db.get_state(con, "devloop_limits", "{}") or "{}")
    except ValueError:
        return {}


def _choice(mine: dict, kind: str):
    """Djeddi's own daily limit before any ceiling (0 = no limit)."""
    n = mine.get(kind)
    if kind == "runs" and n is None:
        n = DEFAULT_RUNS
    return n if n and n > 0 else 0


def limits(con) -> dict:
    """Today's limits: Djeddi's own setting (state), never above a ceiling in [devloop] when one is set.
    0 = no limit. "No limit" is truly unlimited unless [devloop] sets a ceiling (#178)."""
    mine, out = _mine(con), {}
    for kind in LIMITS:
        ceiling, n = _ceiling(kind), _choice(mine, kind)
        if ceiling:
            n = ceiling if not n else min(n, ceiling)
        out[kind] = n
    return out


def capped(con) -> dict:
    """Limits where a ceiling in [devloop] lowers Djeddi's own choice: kind -> the ceiling that holds."""
    mine, lim = _mine(con), limits(con)
    return {k: lim[k] for k in LIMITS if _ceiling(k) and lim[k] != _choice(mine, k)}


def runs_held(con) -> bool:
    """max_runs_per_day = 0 in [devloop]: no scheduled runs at all."""
    return _ceiling("runs") == 0


def set_limit(con, kind: str, value) -> str:
    """Djeddi's daily limit for runs, tokens or usd (0 = up to the ceiling / no limit)."""
    if kind not in LIMITS:
        raise ValueError(f"limit must be one of: {', '.join(LIMITS)}")
    n = float(value)
    n = round(n, 2) if kind == "usd" else int(n)
    ceiling = _ceiling(kind)
    if n < 0 or (ceiling and n > ceiling):
        raise ValueError(f"daily {LIMITS[kind][1]} limit must be between 0 (as high as allowed) and {ceiling} "
                         f"({LIMITS[kind][0]} in [devloop])")
    mine = _mine(con)
    mine[kind] = n
    db.set_state(con, "devloop_limits", json.dumps(mine))
    db.log(con, "me", "devloop_limit", f"{kind}={n}")
    con.commit()
    return limits_text(con)


def override_today(con) -> bool:
    return db.get_state(con, "devloop:limit_override", "") == date.today().isoformat()


def set_override(con, on: bool) -> str:
    db.set_state(con, "devloop:limit_override", date.today().isoformat() if on else "")
    db.log(con, "me", "devloop_limit_override", "on" if on else "off")
    con.commit()
    return ("Daily limits lifted for the rest of today; the kill switch, pause and usage limits still hold." if on
            else "Daily limits apply again.")


def limit_reached(con) -> str:
    """Which daily limit stops new runs ('' = none)."""
    if override_today(con):  # Djeddi pushed through today's limits himself
        return ""
    if runs_held(con):
        return f"daily runs limit reached (max_runs_per_day = 0 in [devloop], {CEILING_FILE})"
    lim, used = limits(con), used_today(con)
    for kind in LIMITS:
        if lim[kind] and used[kind] >= lim[kind]:
            return f"daily {LIMITS[kind][1]} limit reached ({_fmt(kind, used[kind])} of {_fmt(kind, lim[kind])})"
    return ""


def _fmt(kind: str, n) -> str:
    return f"${n:.2f}" if kind == "usd" else f"{n / 1000:.0f}k" if kind == "tokens" and n >= 1000 else str(n)


def limits_text(con) -> str:
    lim, used, cap = limits(con), used_today(con), capped(con)
    unit = {"runs": "runs", "tokens": "tokens", "usd": "spend"}

    def one(k):
        if k == "runs" and runs_held(con):
            return f"{used[k]} runs (none allowed)"
        return f"{_fmt(k, used[k])} {unit[k]} (no limit)" if not lim[k] else f"{_fmt(k, used[k])} of {_fmt(k, lim[k])} {unit[k]}"
    text = ("Limits lifted for today. " if override_today(con) else "") + "Today: " + " · ".join(one(k) for k in LIMITS) + "."
    for k, n in cap.items():  # say plainly when a ceiling, not Djeddi's own choice, sets the number
        text += (f" Your {unit[k]} choice is capped at {_fmt(k, n)} by {LIMITS[k][0]} in [devloop] ({CEILING_FILE});"
                 " remove that line for your own choice to hold: bash deploy/builder-unlimited.sh")
    return text


# Slots (#185): the Pi builds up to workers.slots() tasks at once, one file lock per slot. Slot 1 keeps the old
# lock file, so a run started by an older version still counts. The OS releases a lock when its process dies.

def slot_lock(k: int) -> Path:
    return LOCK if k <= 1 else WORK / f"lock-{k}"


def _held(path: Path) -> bool:
    """True when some process holds this lock now."""
    if not path.exists():
        return False
    with open(path) as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True


def _lock_free() -> bool:
    """True when no slot is held (so any 'running' row on the Pi is left over from a crash or reboot)."""
    return not any(_held(slot_lock(k)) for k in range(1, workers.SLOT_CEILING + 1))


def take_slot(con):
    """(open lock file, slot number) for the first free slot on the Pi, or (None, 0) when every slot is busy."""
    WORK.mkdir(exist_ok=True)
    for k in range(1, workers.slots(con) + 1):
        f = open(slot_lock(k), "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f, k
        except BlockingIOError:
            f.close()
    return None, 0


def busy_slots(con) -> int:
    return sum(_held(slot_lock(k)) for k in range(1, max(workers.slots(con), 1) + 1))


def close_interrupted(con, mine: int | None = None) -> int:
    """Mark runs on the Pi that were cut off (Pi restarted, process killed) as failed, so they block nothing: a run
    whose slot lock nobody holds, or one on the slot this process has just taken. Remote workers' runs end through
    their leases (workers.expire)."""
    workers.expire(con)
    rows = [r for r in con.execute("SELECT id, task_id FROM dev_runs WHERE status = 'running' AND COALESCE(worker, '') IN ('', 'pi')").fetchall()
            if (k := workers.slot_of(con, r["id"])) == mine or not _held(slot_lock(k))]
    for r in rows:
        con.execute("""UPDATE dev_runs SET status = 'failed', finished = ?,
                       summary = trim(summary || char(10) || 'Interrupted: Atlas stopped during this run.') WHERE id = ?""",
                    (db.now(), r["id"]))
        con.execute("UPDATE tasks SET status = 'todo' WHERE id = ? AND status = 'doing'", (r["task_id"],))
        workers.end_lease(con, r["id"], "released", "interrupted")
    con.commit()
    return len(rows)


def current(con):
    """The newest run in progress on any worker (after tidying up runs that were cut off), or None."""
    if con.execute("SELECT 1 FROM dev_runs WHERE status = 'running' LIMIT 1").fetchone():
        close_interrupted(con)
    return con.execute("SELECT * FROM dev_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1").fetchone()


def stop_requested(con, rid: int) -> bool:
    return str(rid) in (db.get_state(con, "devloop_stop", "") or "").split(",")


def stop(con) -> str:
    """End every run in progress now: on the Pi the agent's process group gets SIGTERM; a remote worker is told on
    its next heartbeat."""
    current(con)
    runs = con.execute("SELECT id, task_id, COALESCE(worker, '') AS worker FROM dev_runs WHERE status = 'running'").fetchall()
    if not runs:
        return "Nothing is running."
    db.set_state(con, "devloop_stop", ",".join(str(r["id"]) for r in runs))
    for r in runs:
        db.log(con, "me", "devloop_stop", f"run {r['id']}")
        con.execute("UPDATE build_leases SET stop = 1 WHERE run_id = ? AND status = 'active'", (r["id"],))
        pid = int(db.get_state(con, f"devloop_pid_{r['id']}", "0") or 0) if r["worker"] in ("", "pi") else 0
        if not pid and len(runs) == 1 and r["worker"] in ("", "pi"):
            pid = int(db.get_state(con, "devloop_pid", "0") or 0)  # a run started by an older version
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    con.commit()
    return "Stopping the run on " + ", ".join(f"task #{r['task_id']}" for r in runs) + "."


def why_not_now(con, task_id: int | None = None) -> str:
    """Reason a manual run cannot start right now ('' = it can)."""
    from . import skilltree
    if usage.killed():
        return "The kill switch is on. Turn it off first."
    if not claude_path():
        return "Claude Code was not found on the Pi. Install it and log in once (see README → Dev loop)."
    current(con)
    n = workers.slots(con)
    if busy_slots(con) >= n:
        return "A run is already in progress." if n == 1 else f"All {n} builder slots on the Pi are busy."
    busy = workers.busy_tasks(con)
    if task_id:
        row = con.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row or row[0] in ("done", "dropped"):
            return f"Task #{task_id} is not open."
        if busy and not workers.pick_alongside(con, busy, task_id):
            return (f"Task #{task_id} cannot be built next to " + ", ".join(f"#{b}" for b in busy)
                    + ": only an independent task of the accepted Side by side batch can.")
        return outside_blocked(con, task_id)
    if busy:
        return "" if workers.pick_alongside(con, busy) else workers.why_not_alongside(con, busy)
    if pick_task(con, fallback=True):
        return ""
    return skilltree.why_nothing(skilltree.build_plan(con, cfg()))


def can_start(con) -> bool:
    """A free slot on the Pi and a task for it (continuous mode and the one-minute watcher)."""
    current(con)
    if busy_slots(con) >= workers.slots(con):
        return False
    busy = workers.busy_tasks(con)
    return bool(workers.pick_alongside(con, busy) if busy else pick_task(con))


def start_background(force: bool = True, task_id: int | None = None) -> str:
    """'Run now' from the dashboard or Telegram (force: regardless of schedule and limits), or the next run in
    continuous mode (not forced: the daily limit, pause and kill switch still apply)."""
    from . import launch
    launch.detached(["engine", "dev", "run"] + (["--force"] if force else []) + (["--task", str(task_id)] if task_id else []),
                    WORK / "manual.log", "build")  # its own systemd unit on the Pi: a service restart cannot stop it
    return "Started a run. You get a message when it starts and when it ends."


# ---------------- choosing work ----------------

def outside_blocked(con, task_id: int) -> str:
    """Why a task that uses outside code cannot be built yet ('' = it can, or it uses none). Decision #75."""
    from . import skilltree, sandbox
    row = con.execute("SELECT id, title, description FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row or not skilltree.outside(dict(row)):
        return ""
    status = skilltree._approval(con, task_id)
    if status == "approved":
        return ""
    if status is None:  # the security gate's findings go with the question, so he decides on evidence (#18, #75)
        skilltree.ask_approval(con, dict(row), f"{skilltree.OUTSIDE_GATE}. {sandbox.evidence(con, task_id)}")
    return f"Task #{task_id} {skilltree.OUTSIDE_GATE}; approve it first ({'you said not now' if status == 'denied' else 'waiting for your OK'})."


def pick_task(con, task_id: int | None = None, ask: bool = False, fallback: bool = False):
    """The next task to build (hq.skilltree.build_plan): first the tasks Djeddi queued himself, in his order,
    then Atlas's proposals in build order. Proposals that are big decisions wait for his approval; with ask=True
    (a real run) it is requested once, and the agent takes the next task meanwhile. fallback=True (Run now by
    hand): if nothing is ready, his own queue is built anyway, even a task that waits for another or failed twice."""
    from . import skilltree, atlasplan
    if task_id:
        rules = loop.load_constitution()
        priority.rescore(con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
        if outside_blocked(con, task_id):  # Run by hand does not bypass the outside-code approval
            return None
        task = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task and atlasplan.block_reason(task):
            return None
        if task and ask:  # chosen by hand: no hold, but the verdict still goes into the build prompt
            _overlap_check(con, task)
        return task
    stopped = resume_first(con)  # stopped by a usage limit: finish it before anything else (#158)
    if stopped:
        return stopped
    plan = skilltree.build_plan(con, cfg())
    row = lambda i: con.execute("SELECT * FROM tasks WHERE id = ?", (i["id"],)).fetchone()
    for i in plan.get("lane", []) + plan["mine"] + [p for p in plan["proposed"] if p["owner"] == "ai"]:  # Build next first (#182)
        if i["state"] == "ready":
            if mockup.hold(con, i["id"]):  # visual first: waits until Djeddi approves the mockup (#147)
                continue
            if overlap.hold(con, row(i), check_now=ask):  # already built: Djeddi decides first (#89)
                continue
            return row(i)
        if ask and i["state"] == "needs_ok" and i["approval"] is None:
            skilltree.ask_approval(con, {"id": i["id"], "title": i["title"]}, i["gate"])
    if fallback:
        for i in plan.get("lane", []) + plan["mine"]:
            if i["state"] in ("blocked", "failed") and not outside_blocked(con, i["id"]) and not atlasplan.block_reason(row(i)):
                return row(i)
    return None


def resume_first(con):
    """The task a usage limit stopped (an open dev loop item under Today › Stopped by a usage limit), oldest first,
    so the builder continues it from its checkpoint instead of moving on (#158). When it starts follows the
    builder's autonomy: Continuous within a minute of the reset (atlasplan.tick), otherwise the next run.
    A task that was finished, dropped or waits for review is resolved here and no longer counts."""
    from . import atlasplan, resume
    for r in con.execute("""SELECT i.task_id, t.status, t.owner FROM interruptions i JOIN tasks t ON t.id = i.task_id
                            WHERE i.tool = 'devloop' AND i.status = 'open' ORDER BY i.stopped_at, i.id""").fetchall():
        if r["status"] in DONE_STATUSES or r["owner"] != "ai" or con.execute(
                "SELECT 1 FROM dev_runs WHERE task_id = ? AND status = 'pr_open'", (r["task_id"],)).fetchone():
            resume.resolve_task(con, r["task_id"], "no longer open")
            con.commit()
            continue
        if con.execute("SELECT 1 FROM dev_runs WHERE task_id = ? AND status = 'running'", (r["task_id"],)).fetchone():
            continue
        task = con.execute("SELECT * FROM tasks WHERE id = ?", (r["task_id"],)).fetchone()
        if outside_blocked(con, task["id"]) or atlasplan.block_reason(task) or mockup.hold(con, task["id"]):
            continue  # still held by a gate: the normal order applies until it is cleared
        return task
    return None


def _overlap_check(con, task) -> None:
    """Refresh the overlap verdict of a task picked by hand, so the build prompt carries it."""
    item = {"kind": "task", "id": task["id"], "title": task["title"], "description": task["description"]}
    conf = overlap.cfg()
    if conf["enabled"] and not overlap.fresh(con, item, conf):
        overlap.check_many(con, [item], conf)


def waiting_for_ok(con) -> int:
    """How many AI tasks wait for Djeddi's approval."""
    return con.execute("""SELECT COUNT(DISTINCT task_id) FROM approvals WHERE task_id IS NOT NULL
                          AND status = 'pending'""").fetchone()[0]


# ---------------- git helpers ----------------

def _git(*args, cwd: Path | None = None, timeout: int = 120, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "task"


def _merged(head: str) -> bool:
    """Is this branch's work in origin/main? Works for merge commits, squash merges and rebase merges."""
    if subprocess.run(["git", "merge-base", "--is-ancestor", head, "origin/main"], cwd=ROOT,
                      capture_output=True).returncode == 0:
        return True
    base = _git("merge-base", head, "origin/main", check=False)
    files = _git("diff", "--name-only", base, head, check=False).split() if base else []
    if not files:
        return False
    # squash/rebase: the files the branch changed look in main exactly as the branch left them
    return subprocess.run(["git", "diff", "--quiet", head, "origin/main", "--", *files], cwd=ROOT,
                          capture_output=True).returncode == 0


def sync_merged(con, live: dict | None = None, fetch: bool = True) -> list[int]:
    """Tasks whose dev branch has been merged into origin/main are marked done; PRs closed unmerged are closed here
    too; the rest are checked for conflicts with main. GitHub's live state is used when `gh` can reach it.
    engine/delivery.py passes the PR state it just read (`live`) and has fetched already (`fetch=False`)."""
    rows = con.execute("SELECT id, task_id, head, pr_url FROM dev_runs WHERE status = 'pr_open' AND head != ''").fetchall()
    if not rows:
        return []
    if fetch:
        _git("fetch", "-q", "origin", check=False, timeout=180)
    if live is None:
        live = github_prs()
    state = {r["id"]: (live or {}).get(r["pr_url"], {}).get("state") for r in rows}
    done = []
    merged = [r["id"] for r in rows if state[r["id"]] == "MERGED" or _merged(r["head"])]
    for rid in list(merged):  # merging a stacked PR also merges the unreviewed PRs below it
        merged += [b for b in stack_below(con, rid) if b not in merged]
    for r in con.execute(f"SELECT id, task_id FROM dev_runs WHERE status = 'pr_open' AND id IN ({','.join('?' * len(merged))})",
                         merged).fetchall() if merged else []:
        con.execute("UPDATE dev_runs SET status = 'merged' WHERE id = ?", (r["id"],))
        db.update(con, "tasks", r["task_id"], status="done")
        db.log(con, "hq", "devloop_merged", f"task #{r['task_id']}")
        if r["task_id"] not in done:
            done.append(r["task_id"])
    for r in rows:
        if state[r["id"]] == "CLOSED" and r["id"] not in merged:  # closed without merging: no longer waits for review
            con.execute("UPDATE dev_runs SET status = 'closed', conflict = '' WHERE id = ?", (r["id"],))
            con.execute("UPDATE tasks SET status = 'todo' WHERE id = ? AND status = 'doing'", (r["task_id"],))
            db.log(con, "hq", "devloop_closed", f"task #{r['task_id']}: PR closed without merging")
    con.commit()
    check_conflicts(con, live=live)  # the remaining open PRs against the main just fetched
    if done:
        priority.rerank(con, "task done", ",".join(f"#{t}" for t in done))
    return done


def stack_base(con):
    """The run a new branch stacks on: the newest unreviewed PR whose commit is still here, when the batch is above 1."""
    if batch_size(con) == 1:
        return None
    rows = con.execute("""SELECT r.id, r.task_id, r.branch, r.head, t.title FROM dev_runs r JOIN tasks t ON t.id = r.task_id
                          WHERE r.status = 'pr_open' AND r.head != '' AND t.status NOT IN ('done', 'dropped')
                          ORDER BY r.id DESC""").fetchall()
    for r in rows:
        if subprocess.run(["git", "cat-file", "-e", f"{r['head']}^{{commit}}"], cwd=ROOT, capture_output=True).returncode == 0:
            return r
    return None


def prepare_tree(con, branch: str, tree: Path, stack: bool = True):
    """Create the worktree: stacked on an unreviewed PR (origin/main merged in) or, failing that, on origin/main.
    A build next to another one (stack=False, #185) always starts from origin/main.
    Returns the run it is stacked on (or None) and the commit the agent starts from."""
    _git("fetch", "-q", "origin", "main", timeout=180)
    base = stack_base(con) if stack else None
    if base:
        _git("worktree", "add", "-q", "-b", branch, str(tree), base["head"])
        if subprocess.run(["git", "merge", "-q", "--no-edit", "-m", f"Merge origin/main into {branch}", "origin/main"],
                          cwd=tree, capture_output=True, text=True, timeout=180).returncode == 0:
            return base, _git("rev-parse", "HEAD", cwd=tree)
        _git("merge", "--abort", cwd=tree, check=False)  # conflicts with main: do not stack this time
        _git("worktree", "remove", "--force", str(tree), check=False)
        _git("branch", "-D", branch, check=False)
    _git("worktree", "add", "-q", "-b", branch, str(tree), "origin/main")
    return None, _git("rev-parse", "HEAD", cwd=tree)


def stack_below(con, run_id: int) -> list[int]:
    """The runs a run was stacked on, nearest first."""
    out, seen = [], {run_id}
    rid = run_id
    while True:
        row = con.execute("SELECT base_run FROM dev_runs WHERE id = ?", (rid,)).fetchone()
        rid = row["base_run"] if row else None
        if not rid or rid in seen:
            return out
        seen.add(rid); out.append(rid)


def stack_top(con, run_id: int) -> int:
    """The newest open PR that contains this run's work (the run itself when nothing is stacked on it)."""
    above = [r["id"] for r in con.execute("SELECT id FROM dev_runs WHERE status = 'pr_open' AND id > ? ORDER BY id DESC",
                                          (run_id,)) if run_id in stack_below(con, r["id"])]
    return above[0] if above else run_id


# ---------------- merge conflicts with main ----------------

def conflict_files(head: str, cwd: Path | None = None) -> list[str] | None:
    """Files that conflict when this commit is merged into origin/main: [] merges cleanly, None = could not check.
    `git merge-tree --write-tree` (git 2.38+) only writes objects; no branch or worktree changes."""
    try:
        r = subprocess.run(["git", "merge-tree", "--write-tree", "--name-only", "--no-messages", "origin/main", head],
                           cwd=cwd or ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode == 0:
        return []
    if r.returncode == 1:
        return [f for f in r.stdout.splitlines()[1:] if f.strip()]
    return None


def github_prs() -> dict[str, dict] | None:
    """Live pull request state from GitHub via `gh`, by PR URL: state (OPEN/CLOSED/MERGED), mergeable
    (MERGEABLE/CONFLICTING/UNKNOWN) and head commit. None when gh is missing or GitHub cannot be reached."""
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "all", "--limit", "200", "--json",
                            "url,state,mergeable,headRefOid"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        prs = {p["url"]: p for p in json.loads(r.stdout)} if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None
    for p in (prs or {}).values():  # GitHub computes mergeability on demand: ask again for the open ones it has not yet
        for _ in range(3 if p["state"] == "OPEN" and p["mergeable"] == "UNKNOWN" else 0):
            time.sleep(2)
            try:
                v = subprocess.run(["gh", "pr", "view", p["url"], "--json", "mergeable"], cwd=ROOT,
                                   capture_output=True, text=True, timeout=30)
                p["mergeable"] = json.loads(v.stdout)["mergeable"] if v.returncode == 0 else p["mergeable"]
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
                break
            if p["mergeable"] != "UNKNOWN":
                break
    return prs


def check_conflicts(con, fetch: bool = False, live: dict | None = None) -> list[dict]:
    """Record which open PRs conflict with origin/main. Tells Djeddi once when a PR newly conflicts.
    With GitHub's live state (`live`, from github_prs) the PR's current head is checked, also when it was pushed
    from elsewhere, and a conflict GitHub reports is kept even if the local check finds none."""
    if fetch:
        _git("fetch", "-q", "origin", check=False, timeout=180)
    found = []
    for r in con.execute("""SELECT r.id, r.task_id, r.head, r.pr_url, r.conflict, t.title FROM dev_runs r
                            LEFT JOIN tasks t ON t.id = r.task_id
                            WHERE r.status = 'pr_open' AND r.head != '' ORDER BY r.id""").fetchall():
        pr = (live or {}).get(r["pr_url"]) or {}
        head = pr.get("headRefOid") or r["head"]
        if head != r["head"] and subprocess.run(["git", "cat-file", "-e", f"{head}^{{commit}}"], cwd=ROOT,
                                                capture_output=True).returncode == 0:
            con.execute("UPDATE dev_runs SET head = ? WHERE id = ?", (head, r["id"]))  # the PR moved on GitHub
        else:
            head = r["head"]
        files = conflict_files(head)
        if pr.get("mergeable") == "CONFLICTING" and not files:
            files = ["(see the pull request)"]
        if files is None:
            continue
        con.execute("UPDATE dev_runs SET conflict = ? WHERE id = ?", (",".join(files), r["id"]))
        if not files:
            continue
        found.append({"run": r["id"], "task_id": r["task_id"], "files": files})
        if not r["conflict"] and stack_top(con, r["id"]) == r["id"]:  # new, and not just inherited from a stacked PR
            db.log(con, "hq", "devloop_conflict", f"task #{r['task_id']}: {', '.join(files)}")
            notify(f"Atlas dev loop: the PR for task #{r['task_id']} {r['title'] or ''} now conflicts with main "
                   f"({', '.join(files[:5])}). Resolve it on the dashboard (System tab) or with: hq dev resolve {r['id']}")
    db.set_state(con, "devloop_conflict_scan", json.dumps({"at": db.now(), "github": live is not None}))
    con.commit()
    return found


def scan(con) -> str:
    """Scan for conflicts (System tab, `hq dev conflicts`): fetch, then GitHub's live state of every open PR."""
    before = {r[0] for r in con.execute("SELECT id FROM dev_runs WHERE status = 'pr_open'")}
    merged = sync_merged(con)
    closed = con.execute(f"SELECT COUNT(*) FROM dev_runs WHERE status = 'closed' AND id IN ({','.join('?' * len(before))})",
                         list(before)).fetchone()[0] if before else 0
    n = len(conflicts(con))
    how = "live on GitHub" if json.loads(db.get_state(con, "devloop_conflict_scan", "{}") or "{}").get("github") \
        else "with git (GitHub not reachable)"
    return (f"Checked {how}: " + (f"{n} open PR(s) conflict with main." if n else "no open PR conflicts with main.")
            + (f" {len(merged)} merged." if merged else "") + (f" {closed} closed without merging." if closed else ""))


def why_not_resolve(con, run_id: int) -> str:
    """Reason a conflict resolution cannot start for this run ('' = it can)."""
    if usage.killed():
        return "The kill switch is on. Turn it off first."
    if not claude_path():
        return "Claude Code was not found on the Pi. Install it and log in once (see README → Dev loop)."
    if current(con):
        return "A run is already in progress."
    row = con.execute("SELECT status, conflict, head FROM dev_runs WHERE id = ?", (run_id,)).fetchone()
    if not row or row["status"] != "pr_open" or not row["head"]:
        return f"Run {run_id} has no open pull request."
    if not row["conflict"]:
        return "This pull request has no recorded conflict with main."
    return ""


def start_resolve(run_id: int) -> str:
    """Resolve from the dashboard: a background `hq dev resolve RUN`."""
    from . import launch
    launch.detached(["engine", "dev", "resolve", str(run_id)], WORK / "manual.log", "resolve")
    return "Started resolving the conflicts. You get a message when it is done; then review the updated PR."


def compare_url(branch: str) -> str:
    remote = _git("remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", remote)
    return f"https://github.com/{m.group(1)}/compare/main...{urllib.parse.quote(branch)}?expand=1" if m else ""


def open_pr(branch: str, title: str, body: str, cwd: Path) -> str:
    if shutil.which("gh"):
        r = subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body],
                           cwd=cwd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and "http" in r.stdout:
            return r.stdout.strip().splitlines()[-1]
    return compare_url(branch)


# ---------------- the agent ----------------

def agent_env(agent_db: Path) -> dict:
    """Minimal environment for the agent: no API keys, bot tokens or other secrets from .env."""
    keep = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TZ")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["HQ_DB"] = str(agent_db)  # callers supply an isolated snapshot or test database
    env["HQ_ACTOR"] = "agent"     # tasks it adds are proposals, not Djeddi's own queue
    return env


def log_path(run_id: int) -> Path:
    return WORK / f"run-{run_id}.log"


def note(run_id: int, text: str) -> None:
    """One line in the run's live log, so the steps after Claude Code's result are visible too (#162)."""
    with open(log_path(run_id), "a") as log:
        log.write(f"{datetime.now():%H:%M} {text}\n")


def describe_event(ev: dict) -> list[str]:
    """Turn one stream-json event from Claude Code into short, readable log lines."""
    lines = []
    if ev.get("type") == "assistant":
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                lines.append("· " + " ".join(block["text"].split())[:300])
            elif block.get("type") == "tool_use":
                inp = block.get("input") or {}
                what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or ""
                lines.append(f"→ {block.get('name')}: {' '.join(str(what).split())[:160]}")
    elif ev.get("type") == "result":
        lines.append(f"■ finished after {ev.get('num_turns', '?')} turns" + (" (error)" if ev.get("is_error") else ""))
    return lines


def agent_database(con, run_id: int) -> Path:
    """Snapshot through SQLite's backup API; never hand unmerged code the live DB.

    Keep the same snapshot for the repair attempt. Store it outside the worktree
    so an agent's git add -A cannot accidentally publish the private plan.
    """
    path = WORK / f"run-{run_id}.db"
    if not path.exists():
        WORK.mkdir(parents=True, exist_ok=True)
        con.commit()  # backup must not wait on this connection's write transaction
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            target = sqlite3.connect(path)
            try:
                con.backup(target)
            finally:
                target.close()
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return path


def _end_group(proc, grace: float = 2.0) -> None:
    """Stop the agent's process group (Claude Code and anything it left running in the background):
    SIGTERM, up to `grace` seconds, then SIGKILL. Returns at once when the group is already gone."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.poll()
        return
    until = time.monotonic() + grace
    while time.monotonic() < until:
        proc.poll()  # reap the leader, else its zombie keeps the group alive
        try:
            os.killpg(proc.pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.1)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_agent(con, cmd: list[str], cwd: Path, run_id: int, minutes: int, on_limit=None, tracker=None) -> tuple[str, bool, dict]:
    """Run Claude Code, writing a readable live log. Returns (final summary, failed, usage)."""
    summary, failed, used = "", True, {}
    deadline = time.monotonic() + minutes * 60
    snapshot = agent_database(con, run_id)
    with open(log_path(run_id), "a") as log:
        log.write(f"{datetime.now():%H:%M} started\n"); log.flush()
        proc = subprocess.Popen(cmd, cwd=cwd, env=agent_env(snapshot), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        db.set_state(con, "devloop_pid", str(proc.pid)); db.set_state(con, f"devloop_pid_{run_id}", str(proc.pid)); con.commit()
        stopped: list[str] = []
        ended = False  # the final result arrived; exit codes from stopping leftovers after it do not count

        def watchdog():
            """Enforces the time limit and the kill switch even when the agent prints nothing (e.g. a hung tool)."""
            while proc.poll() is None:
                reason = ("the kill switch" if usage.killed() else
                          f"the time limit ({minutes} min)" if time.monotonic() > deadline else "")
                if reason:
                    stopped.append(reason)
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                        time.sleep(10)
                        if proc.poll() is None:
                            os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    return
                time.sleep(WATCH_SECONDS)
            time.sleep(EXIT_GRACE)  # Claude Code exited; leftovers holding stdout must not keep the reader waiting
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        threading.Thread(target=watchdog, daemon=True).start()
        try:
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    if line.strip():
                        log.write("  " + line.strip()[:300] + "\n"); log.flush()
                    continue
                for text in describe_event(ev):
                    log.write(f"{datetime.now():%H:%M} {text}\n")
                log.flush()
                if tracker and on_limit:
                    tracker.observe(ev)
                    near = tracker.due(time.monotonic() - (deadline - minutes * 60))
                    if near:  # save while the agent can still be reached by us; after the cutoff nothing is possible
                        log.write(f"{datetime.now():%H:%M} nearly at the {near} limit: saving a checkpoint\n"); log.flush()
                        on_limit(near)
                if ev.get("type") == "result":
                    summary, failed = str(ev.get("result") or "").strip(), bool(ev.get("is_error"))
                    u = ev.get("usage") or {}
                    used = {"turns": int(ev.get("num_turns") or 0),
                            "tokens_in": sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                                           "cache_read_input_tokens")),
                            "tokens_out": int(u.get("output_tokens") or 0),
                            "cost_usd": float(ev.get("total_cost_usd") or 0)}
                    ended = True
                    break  # the last event: do not wait for EOF, which a background child holding stdout delays (#162)
            proc.wait(timeout=EXIT_GRACE)
        except subprocess.TimeoutExpired:
            pass
        finally:
            _end_group(proc)  # Claude Code and anything it left running in the background
            proc.stdout.close()  # release the pipe (avoids a file-descriptor leak per run)
            db.set_state(con, "devloop_pid", "0"); db.set_state(con, f"devloop_pid_{run_id}", "0"); con.commit()
        if stopped:
            log.write(f"{datetime.now():%H:%M} stopped: {stopped[0]}\n")
            summary, failed = f"Stopped by {stopped[0]}.", True
    return summary, failed or (not ended and proc.returncode not in (0, None)), used


# ---------------- one run ----------------

def run(con, task_id: int | None = None, force: bool = False) -> dict:
    conf = cfg()
    if usage.killed():
        return {"status": "killed", "detail": "the kill switch is on (/resume or hq unkill to undo)"}
    merged = sync_merged(con)  # merges and conflicts with main are noticed even when no run starts
    if not conf["enabled"] and not force:
        return {"status": "disabled", "detail": "switch it on with: bash deploy/enable-devloop.sh"}
    if paused(con) and not force:
        return {"status": "paused"}
    claude = claude_path(conf)
    if not claude:
        return {"status": "no_claude", "detail": "Claude Code is not installed on this machine (see README → Dev loop)."}
    reached = "" if force else limit_reached(con)
    from . import atlasplan
    if atlasplan.waiting(con):  # A manual click cannot reset the provider's usage window.
        return {"status": "limit", "detail": f"usage limit; resumes after {db.get_state(con, atlasplan.LIMIT_KEY, '')}"}
    if reached:
        return {"status": "limit", "detail": reached}

    lock, slot = take_slot(con)
    if not lock:
        return {"status": "busy", "detail": "another run is in progress" if workers.slots(con) == 1 else "every builder slot is busy"}
    try:
        close_interrupted(con, mine=slot)  # a 'running' row on our slot is left over: we hold its lock
        if batch_full(con) and not force:  # review batch reached: wait until Djeddi merges or closes PRs
            return {"status": "review", "merged": merged,
                    "detail": f"{open_prs(con)} PR(s) wait for your review (batch of {batch_size(con)})."}
        with open(WORK / "pick.lock", "w") as picking:  # choosing and claiming a task is one step across slots
            fcntl.flock(picking, fcntl.LOCK_EX)
            busy = workers.busy_tasks(con)
            if busy:  # a further slot: only an independent task of the accepted Side by side batch (#72, #185)
                task = workers.pick_alongside(con, busy, task_id)
                if not task:
                    return {"status": "idle", "merged": merged, "detail": workers.why_not_alongside(con, busy)}
            else:
                task = pick_task(con, task_id, ask=True, fallback=force)
            if not task:
                from . import skilltree
                return {"status": "idle", "merged": merged, "detail": skilltree.why_nothing(skilltree.build_plan(con, conf))}
            rid = claim_run(con, conf, task, slot)
        already_ok = con.execute("SELECT 1 FROM approvals WHERE task_id = ? AND status = 'approved'",
                                 (task["id"],)).fetchone()  # approved as a big task: do not ask twice
        # Automatic crash repair was explicitly authorised in System. This does not
        # bypass the kill switch, pause, budgets, batch size or outside-code gate.
        repair_authorised = (db.get_state(con, "recovery_enabled", "0") == "1" and
                             con.execute("SELECT 1 FROM incidents WHERE task_id=? AND status='repairing'", (task["id"],)).fetchone())
        if autonomy(con) == "ask" and not force and not already_ok and not repair_authorised:  # ask first: Approve/Deny on the phone
            from . import gatekeeper
            answer = gatekeeper.request_approval(con, f"Agent: work on task #{task['id']} {task['title']}?",
                                                 "autonomy is set to 'ask first'", "agent",
                                                 wait=int(conf.get("ask_wait_seconds", 900)))
            if answer != "approved":
                con.execute("UPDATE dev_runs SET status = 'closed', finished = ?, summary = ? WHERE id = ?",
                            (db.now(), f"Not started: you answered {answer}.", rid))
                db.update(con, "tasks", task["id"], status="todo")
                workers.end_lease(con, rid, "released", "declined")
                con.commit()
                return {"status": "declined", "detail": f"you answered {answer}", "merged": merged}
        return _run_task(con, conf, claude, task, rid, stack=not busy) | {"merged": merged}
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def claim_run(con, conf: dict, task, slot: int = 1) -> int:
    """The run row and the Pi's lease for one slot, written while the pick lock is held."""
    tag = datetime.now().strftime("%m%d%H%M%S")
    branch = f"{conf['branch_prefix']}{task['id']}-{slug(task['title'])}-{tag}"
    rid = db.insert(con, "dev_runs", task_id=task["id"], branch=branch, status="running", started=db.now(), head="", worker=workers.PI)
    workers.lease(con, task["id"], rid, workers.PI, slot, remote=False)
    db.update(con, "tasks", task["id"], status="doing")
    db.log(con, "hq", "devloop_start", f"task #{task['id']} on {branch}" + (f" (slot {slot})" if slot > 1 else ""))
    con.commit()
    return rid


def build_prompt(con, task, branch: str, stack: str = "") -> str:
    """The whole build prompt for one task (also sent to remote workers, #185)."""
    return PROMPT.format(branch=branch, id=task["id"], title=task["title"], description=task["description"] or "",
                         stack=stack, overlap=overlap.prompt_block(con, task["id"]) + prereq.prompt_block(con, task["id"]) + mockup.prompt_block(con, task["id"]), memory=db.memory_block(con, f"{task['title']} {task['description'] or ''}"),
                         resume=checkpoint.resume_block(con, task["id"]))


def _run_task(con, conf: dict, claude: str, task, rid: int | None = None, stack: bool = True) -> dict:
    if rid is None:
        rid = claim_run(con, conf, task)
    branch = con.execute("SELECT branch FROM dev_runs WHERE id = ?", (rid,)).fetchone()[0]
    tree = WORK / f"wt-{task['id']}-{branch.rsplit('-', 1)[-1]}"
    choice = routing.choose(con, task)
    route(con, rid, task["id"], choice)
    notify(f"Atlas dev loop: started task #{task['id']} {task['title']}.\nModel: {routing.line(choice['model'], choice['reason']) or 'default'}"
           "\nWatch it on the dashboard (System tab); /dev stop ends it.")

    started = time.monotonic()
    used: dict = {}
    repaired = False

    def finish(status: str, summary: str = "", pr: str = "", head: str = "") -> dict:
        if stop_requested(con, rid) or usage.killed():
            status = "stopped"
        con.execute("""UPDATE dev_runs SET status = ?, summary = ?, pr_url = ?, head = ?, finished = ?, turns = ?,
                       tokens_in = ?, tokens_out = ?, cost_usd = ?, seconds = ? WHERE id = ?""",
                    (status, summary[:2000], pr, head, db.now(), used.get("turns", 0), used.get("tokens_in", 0),
                     used.get("tokens_out", 0), used.get("cost_usd", 0.0), int(time.monotonic() - started), rid))
        from . import resume
        if status == "pr_open":
            resume.resolve_task(con, task["id"])  # a run stopped by a usage limit earlier has now delivered
        elif resume.is_limit(summary, "\n".join(tail(rid, 30))):
            cp = checkpoint.latest(con, task["id"])
            resume.record_devloop(con, rid, dict(task), summary or "stopped by a usage limit", cp["content"] if cp else "")
            from . import atlasplan
            atlasplan.note_limit(con, summary + "\n" + "\n".join(tail(rid, 30)))
        if status != "pr_open":
            db.update(con, "tasks", task["id"], status="todo")
        workers.end_lease(con, rid, "released", status)
        db.log(con, "hq", f"devloop_{status}", f"task #{task['id']}")
        con.commit()
        model = con.execute("SELECT model, model_reason FROM dev_runs WHERE id = ?", (rid,)).fetchone()
        return {"status": status, "task": task["id"], "title": task["title"], "branch": branch,
                "model": routing.line(model["model"], model["model_reason"]),
                "summary": summary, "pr": pr, "run": rid, "cost_usd": used.get("cost_usd", 0.0),
                "repaired": repaired, "minutes": round((time.monotonic() - started) / 60)}

    try:
        base, start = prepare_tree(con, branch, tree, stack)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return finish("failed", f"could not prepare a worktree: {e}")
    if base:
        con.execute("UPDATE dev_runs SET base_run = ? WHERE id = ?", (base["id"], rid))
        db.log(con, "hq", "devloop_stack", f"task #{task['id']} on #{base['task_id']} ({base['branch']})")
        con.commit()
    note_stack = (f"\nThis branch is stacked on the unmerged work of task #{base['task_id']} {base['title']} "
             f"(branch {base['branch']}), with origin/main merged in. Build on it; do not undo it. "
             f"Your own commits are `git log {start[:12]}..HEAD`.\n" if base else "")

    prompt = build_prompt(con, task, branch, note_stack)

    def cmd(p: str, choice: dict) -> list[str]:
        return ([claude, "-p", p, "--output-format", "stream-json", "--verbose", "--max-turns", str(int(conf["max_turns"]))]
                + (["--model", choice["model"]] if choice["model"] else []))
    tracker = checkpoint.Tracker(int(conf["max_turns"]), int(conf["max_minutes"]), int(conf.get("context_tokens", checkpoint.CONTEXT_TOKENS)))

    def save_checkpoint(why: str, final: str = "") -> None:
        try:
            lines = log_path(rid).read_text().splitlines() if log_path(rid).exists() else []
            checkpoint.save(con, WORK, dict(task), branch, tree, start, why, tracker, lines, final)
        except (sqlite3.Error, OSError, subprocess.SubprocessError):
            pass  # a failed checkpoint must never break the run

    try:
        summary, failed, got = run_agent(con, cmd(prompt, choice), tree, rid, int(conf["max_minutes"]), save_checkpoint, tracker)
        _add_usage(used, got)
        from . import skilltree
        outside_ok = skilltree.outside(dict(task))  # it passed the outside-code approval in pick_task
        outcome, problem = checked(rid, tree, failed, summary, start, outside_ok)
        if problem and may_repair(con, rid, summary):
            repaired = True
            db.log(con, "hq", "devloop_repair", f"task #{task['id']}: {outcome}")
            con.commit()
            with open(log_path(rid), "a") as log:
                log.write(f"{datetime.now():%H:%M} self-repair: {outcome}, starting one repair run\n")
            fix = REPAIR_PROMPT.format(branch=branch, id=task["id"], title=task["title"], problem=problem[-3000:],
                                       start=start[:12])
            stronger = routing.escalate(choice)
            if stronger != choice:
                route(con, rid, task["id"], stronger)
            again, failed, got = run_agent(con, cmd(fix, stronger), tree, rid, int(conf["max_minutes"]))
            _add_usage(used, got)
            summary = f"{again}\n\n(After one automatic repair run; first attempt: {outcome}.)".strip()
            outcome, problem = checked(rid, tree, failed, again, start, outside_ok)
        if failed or problem:
            save_checkpoint("the run ended unfinished", summary)
        if outcome == "no_changes":
            _git("worktree", "remove", "--force", str(tree), check=False)
            _git("branch", "-D", branch, check=False)  # empty branch: no commits of its own, nothing is lost
            return finish("failed" if failed else "no_changes", summary or "no commits")
        if outcome == "tests_failed":
            return finish("tests_failed", summary + "\n\nTests failed:\n" + "\n".join(problem.splitlines()[-8:]))
        if outcome == "outside_code":  # decision #75: no PR with outside code that did not pass the gate
            db.log(con, "hq", "devloop_outside_code", f"task #{task['id']}: {problem[:300]}")
            return finish("failed", summary + "\n\nNot opened as a PR (security gate):\n" + problem)
        head = _git("rev-parse", "HEAD", cwd=tree)
        t0 = time.monotonic()
        note(rid, "pushing the branch and opening the PR")
        _git("push", "-q", "-u", "origin", branch, cwd=tree, timeout=180)
        body = (f"Built by Atlas's dev loop on the Pi for task #{task['id']}: {task['title']}\n\n{summary}\n\n"
                + ("Atlas repaired the first attempt itself (one automatic repair run).\n\n" if repaired else "")
                + (f"Stacked on task #{base['task_id']} ({base['branch']}), which is not merged yet: this PR also "
                   "contains that work. Merging this PR merges it too; Atlas then marks those tasks done.\n\n" if base else "")
                + "Tests pass on the Pi. Review, then merge; Atlas marks the task done once it is in main.")
        checkpoint.clear(con, task["id"])
        url = open_pr(branch, f"Atlas dev: {task['title'][:70]}", body, tree)
        note(rid, f"PR ready ({time.monotonic() - t0:.0f} s)")
        out = finish("pr_open", summary, url, head)
        from . import atlasplan
        if url and atlasplan.is_plan_task(task) and atlasplan.auto_merge(con):  # the owner allowed it (Forge › Plan)
            r = subprocess.run(["gh", "pr", "merge", url, "--merge", "--delete-branch"], cwd=tree, capture_output=True, text=True, timeout=120)
            db.log(con, "agent", "atlasplan_merge", f"{url}: {'merged' if r.returncode == 0 else 'not merged: ' + r.stderr[:160]}")
            con.commit()
        return out
    except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError) as e:
        return finish("failed", f"{summary}\n\n{e}".strip())
    finally:
        _git("worktree", "remove", "--force", str(tree), check=False)  # the branch and its commits are kept
        for suffix in ("", "-wal", "-shm", "-journal"):
            (WORK / f"run-{rid}.db{suffix}").unlink(missing_ok=True)


def resolve(con, run_id: int) -> dict:
    """Djeddi pressed Resolve: one agent run merges origin/main into the PR branch and fixes the conflicts."""
    conf = cfg()
    lock, slot = take_slot(con)
    if not lock:
        return {"status": "busy", "detail": "another run is in progress"}
    try:
        close_interrupted(con, mine=slot)
        check_conflicts(con, fetch=True)
        reason = why_not_resolve(con, run_id)
        if reason:
            return {"status": "not_resolved", "detail": reason}
        target = con.execute("""SELECT r.*, t.title FROM dev_runs r JOIN tasks t ON t.id = r.task_id
                                WHERE r.id = ?""", (run_id,)).fetchone()
        return _resolve(con, conf, claude_path(conf), target)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _resolve(con, conf: dict, claude: str, target) -> dict:
    branch, start = target["branch"], target["head"]
    tree = WORK / f"fix-{target['id']}-{datetime.now():%m%d%H%M%S}"
    rid = db.insert(con, "dev_runs", task_id=target["task_id"], branch=branch, status="running", started=db.now(),
                    head="", resolves=target["id"])
    db.log(con, "me", "devloop_resolve", f"task #{target['task_id']} ({branch}): {target['conflict']}")
    con.commit()
    task = con.execute("SELECT * FROM tasks WHERE id = ?", (target["task_id"],)).fetchone()
    choice = routing.choose(con, task)  # the model that suits the task also suits its conflicts
    route(con, rid, target["task_id"], choice)
    files = target["conflict"].split(",")
    notify(f"Atlas dev loop: resolving the merge conflicts of task #{target['task_id']} {target['title']} ({', '.join(files[:5])}).")
    started = time.monotonic()
    used: dict = {}

    def finish(status: str, summary: str, head: str = "") -> dict:
        if stop_requested(con, rid) or usage.killed():
            status = "stopped"
        con.execute("""UPDATE dev_runs SET status = ?, summary = ?, head = ?, finished = ?, turns = ?, tokens_in = ?,
                       tokens_out = ?, cost_usd = ?, seconds = ? WHERE id = ?""",
                    (status, summary[:2000], head, db.now(), used.get("turns", 0), used.get("tokens_in", 0),
                     used.get("tokens_out", 0), used.get("cost_usd", 0.0), int(time.monotonic() - started), rid))
        if status == "resolved":  # the PR's run now points at the merge commit, so merge detection keeps working
            con.execute("UPDATE dev_runs SET head = ?, conflict = '' WHERE id = ?", (head, target["id"]))
        db.log(con, "hq", f"devloop_resolve_{status}", f"task #{target['task_id']}")
        con.commit()
        return {"status": status, "task": target["task_id"], "title": target["title"], "branch": branch, "run": rid,
                "pr": target["pr_url"], "summary": summary, "cost_usd": used.get("cost_usd", 0.0), "resolve": True,
                "model": routing.line(choice["model"], choice["reason"]), "minutes": round((time.monotonic() - started) / 60)}

    summary = ""
    try:
        _git("worktree", "add", "-q", "-B", branch, str(tree), start)
        step = subprocess.run(["git", "merge", "--no-edit", "-m", f"Merge origin/main into {branch}", "origin/main"],
                              cwd=tree, capture_output=True, text=True, timeout=180)
        if step.returncode == 0:
            summary = "origin/main merged in without conflicts (main changed since the check)."
        else:
            left = _git("diff", "--name-only", "--diff-filter=U", cwd=tree).split()
            if not left:
                return finish("failed", f"git could not merge main in: {(step.stderr or step.stdout).strip()[:300]}")
            prompt = RESOLVE_PROMPT.format(branch=branch, id=target["task_id"], title=target["title"],
                                           files="\n".join(f"- {f}" for f in left))
            cmd = ([claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", str(int(conf["max_turns"]))]
                   + (["--model", choice["model"]] if choice["model"] else []))
            summary, failed, got = run_agent(con, cmd, tree, rid, int(conf["max_minutes"]))
            _add_usage(used, got)
            open_merge = subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=tree,
                                        capture_output=True).returncode == 0
            if open_merge or _git("diff", "--name-only", "--diff-filter=U", cwd=tree):
                return finish("failed", f"{summary}\n\nThe merge was not concluded; the PR is unchanged.".strip())
        markers = [f for f in files if (tree / f).is_file()
                   and re.search(r"^(<{7}|>{7})( |$)", (tree / f).read_text(errors="replace"), re.M)]
        if markers:
            return finish("failed", f"{summary}\n\nConflict markers left in {', '.join(markers)}; the PR is unchanged.")
        outcome, problem = checked(rid, tree, False, summary, start, outside_ok=True)  # main's commits were checked already
        if outcome == "tests_failed":
            return finish("tests_failed", summary + "\n\nTests failed; the PR is unchanged:\n" + "\n".join(problem.splitlines()[-8:]))
        if outcome != "ok" or conflict_files("HEAD", cwd=tree):
            return finish("failed", f"{summary}\n\nThe branch still does not merge cleanly into main; the PR is unchanged.")
        head = _git("rev-parse", "HEAD", cwd=tree)
        _git("push", "-q", "origin", f"HEAD:{branch}", cwd=tree, timeout=180)  # fast-forward: the PR updates in place
        return finish("resolved", summary, head)
    except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError) as e:
        return finish("failed", f"{summary}\n\n{e}".strip())
    finally:
        _git("worktree", "remove", "--force", str(tree), check=False)  # the branch keeps the merge commit
        for suffix in ("", "-wal", "-shm", "-journal"):
            (WORK / f"run-{rid}.db{suffix}").unlink(missing_ok=True)


def route(con, rid: int, task_id: int, choice: dict) -> None:
    """Record the model routing chose for this run: on the run, in the audit log and in the run's log file."""
    con.execute("UPDATE dev_runs SET model = ?, model_reason = ? WHERE id = ?", (choice["model"], choice["reason"], rid))
    db.log(con, "hq", "devloop_model", f"task #{task_id}: {routing.text(choice)}")
    con.commit()
    with open(log_path(rid), "a") as log:
        log.write(f"{datetime.now():%H:%M} model: {routing.text(choice)}\n")


def _add_usage(used: dict, got: dict) -> None:
    for k, v in got.items():
        used[k] = used.get(k, 0) + v


TEST_SHARDS = 3  # parallel test processes after a build (the Pi has 4 cores); the suite alone took ~100 s (#190)


def _test_shards(tree: Path, n: int) -> list[list[str]]:
    """Test modules split into n groups of similar file size (largest first, into the lightest group)."""
    files = sorted((tree / "tests").glob("test_*.py"), key=lambda f: -f.stat().st_size)
    groups: list[tuple[int, list[str]]] = [(0, []) for _ in range(n)]
    for f in files:
        i = min(range(n), key=lambda k: groups[k][0])
        groups[i] = (groups[i][0] + f.stat().st_size, groups[i][1] + [f"tests.{f.stem}"])
    return [g for _, g in groups if g]


def _unittest(tree: Path, args: list[str], db_file: Path):
    return subprocess.Popen([sys.executable, "-m", "unittest", *args], cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, env=agent_env(db_file))


def run_tests(tree: Path, shards: int = TEST_SHARDS) -> tuple[bool, str]:
    """Run the test suite in parallel shards, each with its own database. A failing shard is never trusted on its own:
    the full suite then runs once serially, so a parallel-only clash cannot reject good work."""
    def serial() -> tuple[bool, str]:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=tree, capture_output=True,
                           stdin=subprocess.DEVNULL, text=True, timeout=900, env=agent_env(tree / "memory" / "test.db"))
        return r.returncode == 0, r.stderr or r.stdout
    groups = _test_shards(tree, shards) if shards > 1 else []
    if len(groups) < 2:
        return serial()
    procs = [_unittest(tree, g, tree / "memory" / f"test-{i}.db") for i, g in enumerate(groups)]
    clean = True
    try:
        for p in procs:
            try:
                p.communicate(timeout=900)
            except subprocess.TimeoutExpired:
                clean = False
                p.kill()
                p.communicate()
            clean = clean and p.returncode == 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
    return serial() if not clean else (True, "")


def check_work(tree: Path, failed: bool, summary: str, start: str = "origin/main",
               outside_ok: bool = False) -> tuple[str, str]:
    """Atlas's own check of the agent's work: ('ok' | 'no_changes' | 'tests_failed' | 'outside_code', what went wrong).
    'no_changes' carries a problem only when Claude Code crashed; a clean finish without commits is the agent's own call.
    'outside_code': the commits add a dependency or non-stdlib import, and the task did not pass the security gate."""
    ahead = int(_git("rev-list", "--count", f"{start}..HEAD", cwd=tree) or 0)  # the agent's own commits only
    if not ahead:
        return "no_changes", (f"Claude Code ended with an error and left no commits.\n{summary}".strip() if failed else "")
    ok, output = run_tests(tree)
    if not ok:
        return "tests_failed", "\n".join(output.strip().splitlines()[-40:])
    from . import sandbox
    found = [] if outside_ok else sandbox.new_outside_code(tree, start)
    if found:
        return "outside_code", ("The work adds outside code, which needs the security gate and Djeddi's manual "
                                "approval (decision #75). Remove it and write the code yourself:\n- " + "\n- ".join(found))
    return "ok", ""


def checked(rid: int, tree: Path, failed: bool, summary: str, start: str, outside_ok: bool) -> tuple[str, str]:
    """check_work with its steps in the live log: the test run is the longest step after the agent finishes."""
    t0 = time.monotonic()
    note(rid, "checking the work: running the tests")
    outcome, problem = check_work(tree, failed, summary, start, outside_ok)
    note(rid, f"check: {outcome.replace('_', ' ')} ({time.monotonic() - t0:.0f} s)")
    return outcome, problem


def may_repair(con, rid: int, summary: str) -> bool:
    """No repair run after Stop, the kill switch, the time limit or a usage limit: a repair run would stop the same way."""
    from . import resume
    return not (usage.killed() or stop_requested(con, rid) or summary.startswith("Stopped by")
                or resume.is_limit(summary))


# ---------------- reporting ----------------

def notify(text: str) -> None:
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            from .interfaces import telegram
            telegram.Bot(None, os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], {}).send(text)
        except Exception:
            pass


def remember(con, result: dict) -> str:
    """Store the outcome of the latest run attempt, so the System tab can say why nothing happened."""
    text = report(result)
    db.set_state(con, "devloop_last", json.dumps({"at": db.now(), "status": result.get("status"), "text": text[:600]}))
    con.commit()
    return text


def report(result: dict) -> str:
    s = result.get("status")
    cost = f" ({result.get('minutes', 0)} min, ~${result.get('cost_usd', 0):.2f})" if "task" in result else ""
    model = f"\nModel: {result['model']}" if result.get("model") else ""
    if result.get("resolve"):
        if s == "resolved":
            return (f"Atlas dev loop: resolved the merge conflicts of task #{result['task']} {result['title']}{cost}. "
                    f"The PR is updated; review it, then merge.{model}\n{result['pr']}\n\n{result['summary'][:1200]}")
        return (f"Atlas dev loop: could not resolve the conflicts of task #{result['task']} {result['title']} ({s}){cost}. "
                f"The PR is unchanged.{model}\n{result['summary'][:800]}")
    if s == "pr_open":
        return f"Atlas dev loop: task #{result['task']} {result['title']} is ready for review{cost}.{model}\n{result['pr']}\n\n{result['summary'][:1200]}"
    if s in ("failed", "tests_failed", "stopped"):
        again = ", also not after one automatic repair run" if result.get("repaired") else ""
        return f"Atlas dev loop: task #{result['task']} {result['title']} did not finish ({s}{again}){cost}.{model}\n{result['summary'][:800]}"
    if s == "no_changes":
        return f"Atlas dev loop: no changes for task #{result['task']} {result['title']}.{model}\n{result['summary'][:800]}"
    reasons = {"idle": "nothing to do: there is no open task owned by the AI (blocked tasks wait for the tasks before them)",
               "no_claude": "Claude Code was not found on the Pi", "disabled": "the dev loop is switched off",
               "paused": "the dev loop is paused", "limit": "a daily limit is reached",
               "killed": "the kill switch is on", "busy": "another run is still in progress",
               "declined": "not started (autonomy is 'ask first')",
               "review": "waiting for your review before building more",
               "not_resolved": "conflict resolution not started"}
    return f"Atlas dev loop: {reasons.get(s, s)}. {result.get('detail', '')}".strip()


def tail(run_id: int, n: int = 40) -> list[str]:
    p = log_path(run_id)
    return p.read_text(errors="replace").splitlines()[-n:] if p.exists() else []


def live(con, run_id: int, after: int = 0, limit: int = 400) -> dict:
    """One run's live log for the Build tab's console: only lines after `after`, so polling every few seconds stays cheap."""
    r = con.execute("SELECT r.*, t.title FROM dev_runs r LEFT JOIN tasks t ON t.id = r.task_id WHERE r.id = ?", (run_id,)).fetchone()
    if not r:
        raise ValueError(f"No run #{run_id}.")
    p = log_path(run_id)
    lines = p.read_text(errors="replace").splitlines() if p.exists() else []
    after = max(0, min(int(after), len(lines)))
    new = lines[after:]
    if len(new) > limit:  # first load of a long run: the most recent part is what matters
        after, new = len(lines) - limit, new[-limit:]
    elapsed = int(r["seconds"] or 0)
    if r["status"] == "running" and r["started"]:
        try:
            elapsed = max(0, int((datetime.now() - datetime.fromisoformat(r["started"])).total_seconds()))
        except ValueError:
            pass
    max_s = int(cfg()["max_minutes"]) * 60
    return {"run": r["id"], "task_id": r["task_id"], "title": r["title"] or "", "status": r["status"],
            "kind": "resolve" if r["resolves"] else "build", "started": r["started"] or "", "elapsed": elapsed,
            "progress": min(100, round(100 * elapsed / max_s)) if r["status"] == "running" and max_s else None,
            "max_minutes": max_s // 60, "model": r["model"] or "", "turns": r["turns"] or 0, "pr_url": r["pr_url"] or "",
            "summary": r["summary"] or "" if r["status"] != "running" else "", "from": after, "lines": new, "total": len(lines),
            "plain": buildsteps.steps(lines)}  # #179: one plain sentence per step; the raw lines stay behind Output


def overview(con) -> dict:
    """Everything the dashboard's System tab shows."""
    conf = cfg()
    run_now = current(con)
    from . import skilltree, atlasplan
    plan = skilltree.build_plan(con, conf)
    nxt = pick_task(con)
    runs = [dict(r) for r in con.execute("""SELECT r.*, t.title FROM dev_runs r LEFT JOIN tasks t ON t.id = r.task_id
                                            ORDER BY r.id DESC LIMIT 12""")]
    names = workers.display_names(con)
    for r in runs:  # which device built it, on which slot (#193): History, recent runs and Cortex name the device
        r["worker"] = r.get("worker") or workers.PI
        r["device"] = names.get(r["worker"], r["worker"])
        r["slot"] = workers.slot_of(con, r["id"])
    last = runs[0] if runs else None
    from . import parallel
    batch = parallel.accepted(con)
    return {
        "enabled": bool(conf["enabled"]), "paused": paused(con), "installed": bool(claude_path(conf)), "killed": usage.killed(),
        "autonomy": autonomy(con), "autonomy_text": LEVEL_TEXT[autonomy(con)],
        "provider_reset": atlasplan.reset_status(con),
        "queued": [dict(r) for r in con.execute("""SELECT id, title, status, due FROM tasks WHERE owner = 'ai'
                                                   AND status IN ('todo', 'doing', 'blocked') ORDER BY status = 'doing' DESC,
                                                   status = 'blocked', score DESC LIMIT 40""")],
        "batch": batch_size(con), "batch_choice": batch_choice(con), "batch_ceiling": batch_ceiling(),
        "batch_choices": [n for n in BATCH_CHOICES if batch_ceiling() is None or n <= batch_ceiling()],
        "open_prs": open_prs(con), "batch_text": batch_text(batch_size(con), capped=batch_capped(con)),
        "runs_today": runs_today(con), "max_runs_per_day": limits(con)["runs"],
        "limits": limits(con), "used_today": used_today(con), "limits_text": limits_text(con),
        "auto_merge": atlasplan.auto_merge(con), "ceilings": {k: _ceiling(k) for k in LIMITS},
        "limit_choices": {k: _choice(_mine(con), k) for k in LIMITS}, "capped": capped(con), "limit_reached": limit_reached(con), "override_today": override_today(con),
        "max_minutes": conf["max_minutes"], "max_turns": conf["max_turns"],
        "last": json.loads(db.get_state(con, "devloop_last", "null") or "null"),
        "next": {"id": nxt["id"], "title": nxt["title"],
                 "resumes": bool(con.execute("SELECT 1 FROM interruptions WHERE tool = 'devloop' AND status = 'open' AND task_id = ?",
                                             (nxt["id"],)).fetchone())} if nxt else None,  # continues after a usage limit (#158)
        "plan": plan, "why_idle": "" if nxt else skilltree.why_nothing(plan),
        "current": dict(run_now) | {"title": con.execute("SELECT title FROM tasks WHERE id = ?", (run_now["task_id"],)).fetchone()[0]}
        if run_now else None,
        "log": tail(run_now["id"] if run_now else last["id"]) if (run_now or last) else [],
        "runs": runs,
        "lanes": lanes(con),
        "workers": workers.listing(con), "slots": workers.slots(con), "slot_ceiling": workers.ceiling(),  # #185
        "devices": workers.devices(con, nxt),  # #193: per device its role, slots in use, what it builds or why it is idle
        "alongside": batch["open"] if batch else [],  # the accepted Side by side batch: next free slot on any device
        "conflicts": conflicts(con),
        "conflict_scan": json.loads(db.get_state(con, "devloop_conflict_scan", "null") or "null"),
    }


def lanes(con, log_lines: int = 40) -> list[dict]:
    """One lane per task the agent has in flight: building now, or a PR that waits for review (#57).
    Every 'running' row is a lane, so parallel runs show side by side once they exist. Running lanes come first."""
    current(con)  # closes 'running' rows left over from a crash first
    max_s = int(cfg()["max_minutes"]) * 60
    rows = con.execute("""SELECT r.*, t.title FROM dev_runs r JOIN tasks t ON t.id = r.task_id
                          WHERE r.status = 'running' OR (r.status = 'pr_open' AND t.status NOT IN ('done', 'dropped'))
                          ORDER BY r.status = 'running' DESC, r.id DESC""").fetchall()
    out, seen, names = [], set(), workers.display_names(con)
    for r in rows:
        if r["task_id"] in seen:  # a task's newest run is its lane (e.g. a Resolve run over its open PR)
            continue
        seen.add(r["task_id"])
        running = r["status"] == "running"
        below = [x for x in stack_below(con, r["id"])
                 if con.execute("SELECT status FROM dev_runs WHERE id = ?", (x,)).fetchone()[0] == "pr_open"]
        top = stack_top(con, r["id"]) if not running else r["id"]
        elapsed = 0
        if running and r["started"]:
            try:
                elapsed = max(0, int((datetime.now() - datetime.fromisoformat(r["started"])).total_seconds()))
            except ValueError:
                pass
        log = tail(r["id"], log_lines if running else min(log_lines, 12))
        out.append({
            "run": r["id"], "task_id": r["task_id"], "title": r["title"],
            "state": "running" if running else "conflict" if r["conflict"] else "pr",
            "kind": "resolve" if r["resolves"] else "build",
            "branch": r["branch"], "started": r["started"] or "", "finished": r["finished"] or "",
            "elapsed": elapsed if running else int(r["seconds"] or 0),
            "progress": min(100, round(100 * elapsed / max_s)) if running and max_s else None,  # share of the time limit used
            "model": r["model"] or "", "model_reason": r["model_reason"] or "",
            "worker": r["worker"] or workers.PI, "slot": workers.slot_of(con, r["id"]),
            "device": names.get(r["worker"] or workers.PI, r["worker"] or workers.PI),  # as on its Resources › Connected card
            "pr_url": r["pr_url"] or "", "conflict": [f for f in (r["conflict"] or "").split(",") if f],
            "turns": r["turns"] or 0, "tokens": (r["tokens_in"] or 0) + (r["tokens_out"] or 0), "cost_usd": r["cost_usd"] or 0,
            "summary": r["summary"] or "",
            "stacked_on": [con.execute("SELECT task_id FROM dev_runs WHERE id = ?", (x,)).fetchone()[0] for x in below],
            "in_stack_of": None if top == r["id"] else con.execute("SELECT task_id FROM dev_runs WHERE id = ?", (top,)).fetchone()[0],
            "doing": next((l for l in reversed(log) if l.strip()), "") if running else "",  # raw, for the old dashboard
            "step": buildsteps.now(tail(r["id"], 400)) if running else "",  # #179: the current step as a plain sentence
            "log": log,
        })
    return out


def lanes_text(con) -> list[str]:
    """The lanes as text lines (hq dev lanes, status)."""
    out = []
    for l in lanes(con, 3):
        what = {"running": f"building on {l['device']} since {l['started'][11:16]}" + (f" ({l['progress']}% of the time limit)" if l["progress"] is not None else ""),
                "pr": "PR waits for your review", "conflict": "PR conflicts with main"}[l["state"]]
        out.append(f"[{l['state']}] #{l['task_id']} {l['title']} · {what} · {l['branch']}"
                   + (f" · stacked on #{', #'.join(map(str, l['stacked_on']))}" if l["stacked_on"] else "")
                   + (f" · {l['pr_url']}" if l["pr_url"] else ""))
        if l["step"] or l["doing"]:
            out.append(f"    {l['step'] or l['doing']}")
    return out


def conflicts(con) -> list[dict]:
    """Open PRs that conflict with main, as last checked. 'top' is the stacked PR to resolve instead (or None)."""
    out = []
    for r in con.execute("""SELECT r.id, r.task_id, r.branch, r.pr_url, r.conflict, t.title FROM dev_runs r
                            LEFT JOIN tasks t ON t.id = r.task_id WHERE r.status = 'pr_open' AND r.conflict != ''
                            ORDER BY r.id DESC""").fetchall():
        top = stack_top(con, r["id"])
        out.append(dict(r) | {"files": r["conflict"].split(","), "top": None if top == r["id"] else
                              {"run": top, "task_id": con.execute("SELECT task_id FROM dev_runs WHERE id = ?", (top,)).fetchone()[0]}})
    return out


def status(con, n: int = 5) -> str:
    o = overview(con)
    state = ("STOPPED by the kill switch (/resume to undo)" if o["killed"] else
             "off (switch on: bash deploy/enable-devloop.sh)" if not o["enabled"] else
             "paused" if o["paused"] else f"on · {o['runs_today']}/{o['max_runs_per_day']} runs today")
    state += f" · {o['open_prs']}/{o['batch'] or 'unlimited'} PRs await review"
    if o["limits"]["tokens"] or o["limits"]["usd"]:
        state += " · " + o["limits_text"]
    if o["limit_reached"] and o["enabled"]:
        state += f" · stopped: {o['limit_reached']}"
    lines = [f"Dev loop: {state}" + ("" if o["installed"] else " · Claude Code not installed")]
    if len(o["lanes"]) > 1:
        lines.append(f"In flight ({len(o['lanes'])}, hq dev lanes for details): "
                     + ", ".join(f"#{l['task_id']} {l['state']}" for l in o["lanes"]))
    if o["current"]:
        cur = o["current"]
        lines.append(f"Working on #{cur['task_id']} {cur['title']} since {cur['started'][11:16]}"
                     + (f" · model {routing.line(cur['model'], cur['model_reason'])}" if cur["model"] else ""))
        lines += ["  " + l for l in o["log"][-3:]]
    elif o["next"]:
        lines.append(f"Next: #{o['next']['id']} {o['next']['title']}")
    else:
        lines.append(o["why_idle"])
    for c in o["conflicts"]:
        lines.append(f"! PR of #{c['task_id']} conflicts with main ({', '.join(c['files'][:4])}): "
                     + f"hq dev resolve {c['id']}" + (f" (in the stack under #{c['top']['task_id']})" if c["top"] else ""))
    if o["plan"]["mine"]:
        lines.append("Your queue: " + ", ".join(f"#{i['id']}" for i in o["plan"]["mine"]))
    lines += [f"• {r['started'][5:16].replace('T', ' ')} #{r['task_id']} {r['title'] or ''} → {r['status']}"
              + (f" · {r['model']}" if r["model"] else "") + (f" (~${r['cost_usd']:.2f})" if r["cost_usd"] else "") + (f" {r['pr_url']}" if r["pr_url"] else "")
              for r in o["runs"][:n]]
    return "\n".join(lines)
