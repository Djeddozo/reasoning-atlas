"""Move the Pi's checkout from ~/hq-system to ~/atlas (RA-042, one name stage 4), or move it back.

Run on the Pi, as Djeddi, from anywhere:

    python3 ~/hq-system/deploy/move_to_atlas_folder.py --dry-run   # list every unit, cron line and path it would change
    python3 ~/hq-system/deploy/move_to_atlas_folder.py             # move
    python3 ~/atlas/deploy/move_to_atlas_folder.py --rollback      # back to ~/hq-system exactly as before
    python3 ~/atlas/deploy/move_to_atlas_folder.py --status        # what still uses the old path
    python3 ~/atlas/deploy/move_to_atlas_folder.py --drop-link     # remove the ~/hq-system link once nothing uses it

What moving does, in order (it stops at the first problem and undoes its own steps):
1. Checks: the checkout has no local code changes, no builder run is in progress, branch main can be used.
2. Pauses the dev loop (its exact setting is restored afterwards), backs up the database with SQLite's backup API
   and records every file in memory/ and a checksum of .env.
3. Stops the Engine's systemd user units that are running (timers first).
4. Switches to branch main if the checkout is on another branch that main already contains.
5. Renames the folder (one rename on the same disk: memory/, .env, runtime/ and .devloop/ move with it) and leaves
   ~/hq-system as a link to ~/atlas, so anything still using the old path keeps working.
6. Points the systemd user units, the crontab and Claude Code's gatekeeper hook at ~/atlas; repairs git worktrees.
7. Confirms nothing in memory/ or .env was lost, starts the units that were running and restores the dev loop.
The record of the move (with the previous unit files, crontab and settings) is kept in ~/.atlas-folder-move.json.
"""
import argparse, contextlib, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("HOME") or Path.home())
OLD = HOME / "hq-system"
NEW = HOME / "atlas"
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "systemd" / "user"
CLAUDE_SETTINGS = HOME / ".claude" / "settings.json"
RECORD = HOME / ".atlas-folder-move.json"
# Units the Engine runs; stopped during the move (timers first, so they cannot start a job half-way) and started again.
UNITS = ["hq-recovery.timer", "atlas-builder-watch.timer", "atlas-web.service", "atlas-hub.service", "hq.service"]
SETTLE_SECONDS = float(os.environ.get("ATLAS_MOVE_SETTLE", "4"))


def say(message=""):
    print(message, flush=True)


def run(*args, cwd=None, check=True, stdin=None):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, timeout=300, input=stdin)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r


def git(root, *args, check=True):
    return run("git", *args, cwd=root, check=check).stdout.strip()


def systemctl(*args, check=False):
    if not shutil.which("systemctl"):
        return subprocess.CompletedProcess(args, 1, "", "no systemctl")
    return run("systemctl", "--user", *args, check=check)


# --- path rewriting ------------------------------------------------------------------------------------------------

def _forms(old: Path, new: Path):
    """The spellings of the folder in units, cron lines and settings: absolute, %h (systemd), ~ and $HOME."""
    yield str(old), str(new)
    for prefix in ("%h/", "~/", "$HOME/", "${HOME}/"):
        yield prefix + old.name, prefix + new.name


def _pattern(form: str):
    return re.compile(r"(?<![\w.~%$/-])" + re.escape(form) + r"(?![\w.-])")   # whole path only: not ~/hq-system-old


def rewrite(text: str, old: Path = None, new: Path = None) -> str:
    for a, b in _forms(old or OLD, new or NEW):
        text = _pattern(a).sub(lambda m: b, text)
    return text


def mentions(text: str) -> bool:
    return any(_pattern(a).search(text) for a, _ in _forms(OLD, NEW))


# --- what the move changes -----------------------------------------------------------------------------------------

def crontab_text():
    if not shutil.which("crontab"):
        return None
    r = run("crontab", "-l", check=False)
    return r.stdout if r.returncode == 0 else ""


def unit_files():
    if not UNIT_DIR.is_dir():
        return []
    return sorted(p for p in UNIT_DIR.iterdir() if p.is_file() and not p.is_symlink()
                  and p.suffix in (".service", ".timer", ".path", ".socket"))


def plan():
    """Every change, as (kind, name, before, after) with before/after the full text; nothing is written."""
    changes = []
    for p in unit_files():
        text = p.read_text(encoding="utf-8")
        if mentions(text):
            changes.append(("unit", str(p), text, rewrite(text)))
    cron = crontab_text()
    if cron and mentions(cron):
        changes.append(("crontab", "crontab", cron, rewrite(cron)))
    if CLAUDE_SETTINGS.is_file():
        text = CLAUDE_SETTINGS.read_text(encoding="utf-8")
        if mentions(text):
            changes.append(("settings", str(CLAUDE_SETTINGS), text, rewrite(text)))
    return changes


def changed_lines(before, after):
    b, a = before.splitlines(), after.splitlines()
    if len(a) != len(b):
        return [("", l) for l in a if l not in b]
    return [(x, y) for x, y in zip(b, a) if x != y]


def env_mentions(root):
    env = root / ".env"
    return sum(1 for l in env.read_text(encoding="utf-8").splitlines() if mentions(l)) if env.is_file() else 0


def db_mentions(db_path):
    """Rows in the database that name the old folder (history, arsenal folders, run worktrees). Reported, not rewritten:
    the link keeps them valid."""
    if not db_path.exists():
        return {}
    found = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")') if (r[2] or "").upper() in ("TEXT", "")]
            if not cols:
                continue
            where = " OR ".join(f'"{c}" LIKE ?' for c in cols)
            n = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}', [f"%{OLD}%"] * len(cols)).fetchone()[0]
            if n:
                found[table] = n
    finally:
        con.close()
    return found


def branch_step(root):
    """(current branch, whether to switch to main, problem or None)."""
    current = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if current == "main":
        return current, False, None
    if git(root, "rev-parse", "--verify", "-q", "refs/heads/main", check=False) == "":
        return current, False, "There is no local branch main. Run: git fetch origin && git branch main origin/main"
    if subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "main"], cwd=root).returncode:
        return current, False, f"The checkout is on {current}, which has commits main does not have. Merge them first."
    return current, True, None


# --- checks --------------------------------------------------------------------------------------------------------

def checks():
    problems = []
    if OLD.is_symlink():
        return [f"{OLD} is already a link to {os.readlink(OLD)}: the folder was moved. See --status."]
    if not (OLD / "atlas.sh").exists():
        return [f"{OLD} is not the Atlas checkout (no atlas.sh there)."]
    if NEW.exists() or NEW.is_symlink():
        problems.append(f"{NEW} already exists. Move it aside first; this script never overwrites a folder.")
    if os.stat(OLD).st_dev != os.stat(HOME).st_dev:
        problems.append(f"{OLD} is on another disk than {HOME}; the move must be a single rename.")
    if git(OLD, "status", "--porcelain", "--untracked-files=no"):
        problems.append("The checkout has local code changes; commit or set them aside first (hq update shows how).")
    db_path = OLD / "memory" / "hq.db"
    if not db_path.exists():
        problems.append(f"Atlas database not found at {db_path}.")
    else:
        con = sqlite3.connect(db_path, timeout=30)
        try:
            busy = con.execute("SELECT id, task_id FROM dev_runs WHERE status = 'running'").fetchall()
        except sqlite3.OperationalError:
            busy = []
        finally:
            con.close()
        if busy:
            problems.append("A builder run is in progress (" + ", ".join(f"run {r[0]} task #{r[1]}" for r in busy)
                            + "). Wait until it has finished.")
    problems += [p for p in [branch_step(OLD)[2]] if p]
    return problems


def show_plan(changes, current, to_main):
    say(f"Folder: {OLD} -> {NEW}; {OLD} stays as a link to {NEW} until nothing uses it.")
    say(f"Branch: {current} -> main" if to_main else "Branch: main (unchanged)")
    active = [u for u in UNITS if systemctl("is-active", "--quiet", u).returncode == 0]
    say("Units stopped during the move and started again: " + (", ".join(active) or "none running"))
    if not changes:
        say("No unit, cron line or setting names the old folder.")
    for kind, name, before, after in changes:
        say(f"{kind}: {name}")
        for b, a in changed_lines(before, after):
            if b:
                say(f"  - {b.strip()}")
            say(f"  + {a.strip()}")
    n = env_mentions(OLD if not OLD.is_symlink() else NEW)
    if n:
        say(f"Note: .env names the old folder on {n} line(s); it is not changed (secrets file). It keeps working through"
            f" the link; edit those lines by hand before --drop-link.")
    refs = db_mentions((NEW if OLD.is_symlink() else OLD) / "memory" / "hq.db")
    if refs:
        say("Database rows naming the old folder (kept; the link keeps them valid): "
            + ", ".join(f"{t} {n}" for t, n in sorted(refs.items())))
    return active


# --- record of memory/ and .env ------------------------------------------------------------------------------------

def manifest(root):
    files = {str(p.relative_to(root)): p.stat().st_size for p in (root / "memory").rglob("*") if p.is_file()}
    env = root / ".env"
    return {"memory": files, "env": hashlib.sha256(env.read_bytes()).hexdigest() if env.is_file() else None}


def lost(before, root):
    """Files in memory/ that were there before and are gone now, and whether .env changed."""
    now = manifest(root)
    gone = sorted(set(before["memory"]) - set(now["memory"]))
    if before["env"] != now["env"]:
        gone.append(".env (changed)")
    return gone


@contextlib.contextmanager
def connect(path):
    """A committed and closed connection: none may stay open while the folder is renamed."""
    con = sqlite3.connect(path, timeout=30)
    try:
        with con:
            yield con
    finally:
        con.close()


def get_state(con, key):
    row = con.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(con, key, value):
    if value is None:
        con.execute("DELETE FROM state WHERE key = ?", (key,))
    else:
        con.execute("INSERT INTO state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value))


def write_crontab(text):
    run("crontab", "-", stdin=text if text.endswith("\n") or not text else text + "\n")


# --- move ----------------------------------------------------------------------------------------------------------

def move(dry_run=False):
    problems = checks()
    if problems:
        say("Not moving:\n- " + "\n- ".join(problems))
        return 1
    current, to_main = branch_step(OLD)[:2]
    changes = plan()
    active = show_plan(changes, current, to_main)
    if dry_run:
        say("Dry run: nothing changed.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    db_path = OLD / "memory" / "hq.db"
    backup = db_path.with_name(f"hq-before-folder-move-{stamp}.db")
    with connect(db_path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
        previous = {k: get_state(con, k) for k in ("devloop_paused", "devloop_autonomy")}
        set_state(con, "devloop_paused", "1")
    with connect(db_path) as src, connect(backup) as dst:
        src.backup(dst)
    backup.chmod(0o600)
    record = {"stamp": stamp, "old": str(OLD), "new": str(NEW), "branch": current, "toMain": to_main,
              "active": active, "previous": previous, "backup": str(backup.relative_to(OLD)),
              "files": [{"kind": k, "name": n, "before": b} for k, n, b, _ in changes],
              "manifest": manifest(OLD), "done": []}
    save(record)
    say(f"Dev loop paused; database backed up to memory/{backup.name}.")
    try:
        for unit in active:
            systemctl("stop", unit)
            record["done"].append(f"stopped {unit}")
        if active:
            say("Stopped: " + ", ".join(active))
        if to_main:
            git(OLD, "switch", "-q", "main")
            record["done"].append("branch")
            say(f"Switched from {current} to main.")
        os.rename(OLD, NEW)
        record["done"].append("renamed")
        save(record)
        os.symlink(NEW, OLD, target_is_directory=True)
        record["done"].append("linked")
        save(record)
        say(f"Moved {OLD} to {NEW}; {OLD} now links to it.")
        repair_worktrees(NEW, OLD, NEW)
        for kind, name, _, after in changes:
            if kind == "crontab":
                write_crontab(after)
            else:
                Path(name).write_text(after, encoding="utf-8")
        record["done"].append("rewritten")
        save(record)
        if any(k == "unit" for k, *_ in changes):
            systemctl("daemon-reload")
        say(f"Pointed {len(changes)} file(s) at {NEW}: " + ", ".join(Path(n).name for _, n, _, _ in changes) if changes
            else "No unit, cron line or setting needed changing.")
        gone = lost(record["manifest"], NEW)
        if gone:
            raise RuntimeError("files missing after the move: " + ", ".join(gone[:10]))
        say(f"Checked: all {len(record['manifest']['memory'])} file(s) in memory/ and .env are in place.")
        for unit in reversed(active):
            systemctl("start", unit)
        if active:
            time.sleep(SETTLE_SECONDS)
            down = [u for u in active if systemctl("is-active", "--quiet", u).returncode]
            if down:
                raise RuntimeError("did not start again: " + ", ".join(down))
            say("Started again: " + ", ".join(active))
    except Exception as exc:
        say(f"Move failed ({exc}); rolling back.")
        rollback()
        return 1
    with connect(NEW / "memory" / "hq.db") as con:
        set_state(con, "devloop_paused", previous["devloop_paused"])
    record["done"].append("finished")
    save(record)
    say(f"Done. Atlas now lives in {NEW} on branch main. Check: cd {NEW} && ./hq.sh update && ./hq.sh link")
    say(f"Undo: python3 {NEW}/deploy/move_to_atlas_folder.py --rollback")
    return 0


def repair_worktrees(root, src, dst):
    """Point git's worktree records (the builder's .devloop/ copies) from `src` at `dst`, in both directions.
    `git worktree repair` leaves them alone, because the old path still resolves through the link."""
    records = root / ".git" / "worktrees"
    for record in sorted(records.iterdir()) if records.is_dir() else []:
        pointer = record / "gitdir"   # <worktree>/.git
        if not pointer.is_file():
            continue
        pointer.write_text(rewrite(pointer.read_text(encoding="utf-8"), src, dst), encoding="utf-8")
        dotgit = Path(pointer.read_text(encoding="utf-8").strip())
        if dotgit.is_file():
            dotgit.write_text(rewrite(dotgit.read_text(encoding="utf-8"), src, dst), encoding="utf-8")


def save(record):
    RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")
    RECORD.chmod(0o600)


def rollback():
    if not RECORD.exists():
        say("No move record found; nothing to roll back.")
        return 1
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    old, new, done = Path(record["old"]), Path(record["new"]), record["done"]
    active = record["active"]
    for unit in active:
        systemctl("stop", unit)
    for f in record["files"]:
        if f["kind"] == "crontab":
            write_crontab(f["before"])
        else:
            Path(f["name"]).write_text(f["before"], encoding="utf-8")
    if any(f["kind"] == "unit" for f in record["files"]):
        systemctl("daemon-reload")
    if old.is_symlink() and Path(os.readlink(old)) == new:
        old.unlink()   # only the link; the data is in `new` and moves back
    if new.is_dir() and not old.exists():
        os.rename(new, old)
    root = old
    repair_worktrees(root, new, old)
    if record["toMain"] and "branch" in done and not git(root, "status", "--porcelain", "--untracked-files=no"):
        git(root, "switch", "-q", record["branch"], check=False)
    db_path = root / "memory" / "hq.db"
    if db_path.exists():
        with connect(db_path) as con:
            for key, value in record["previous"].items():
                set_state(con, key, value)
    for unit in reversed(active):
        systemctl("start", unit)
    RECORD.rename(RECORD.with_name(f".atlas-folder-move-rolled-back-{record['stamp']}.json"))
    say(f"Back in {old} on branch {git(root, 'rev-parse', '--abbrev-ref', 'HEAD')}. Units, crontab, Claude settings and"
        f" the dev loop setting are as before. The database backup {record['backup']} is kept.")
    return 0


# --- after the move ------------------------------------------------------------------------------------------------

def users_of_old_path():
    users = [f"{k}: {n}" for k, n, _, _ in plan()]
    root = NEW if NEW.is_dir() else OLD
    n = env_mentions(root)
    if n:
        users.append(f".env: {n} line(s)")
    for wt in (root / ".devloop").glob("*/.git") if (root / ".devloop").is_dir() else []:
        if wt.is_file() and mentions(wt.read_text(encoding="utf-8")):
            users.append(f"worktree: {wt.parent.name}")
    return users


def status():
    if not OLD.is_symlink():
        say(f"{OLD} is " + ("still the real folder: not moved yet." if OLD.is_dir() else "gone.")
            + (f" {NEW} exists." if NEW.exists() else ""))
        return 0
    say(f"{OLD} links to {os.readlink(OLD)}.")
    users = users_of_old_path()
    say("Still using the old path:\n- " + "\n- ".join(users) if users else "Nothing in units, cron, settings, .env or"
        " worktrees uses the old path any more; --drop-link may remove the link.")
    refs = db_mentions(NEW / "memory" / "hq.db")
    if refs:
        say("Database rows naming the old folder (history; not a reason to keep the link): "
            + ", ".join(f"{t} {n}" for t, n in sorted(refs.items())))
    return 0


def drop_link():
    if not OLD.is_symlink():
        say(f"{OLD} is not a link; nothing to remove.")
        return 1
    users = users_of_old_path()
    if users:
        say("Keeping the link; still in use:\n- " + "\n- ".join(users))
        return 1
    OLD.unlink()   # removes only the link, never the folder it points to
    say(f"Removed the link {OLD}. Atlas lives in {NEW}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="list every change; change nothing")
    group.add_argument("--rollback", action="store_true", help="move back to ~/hq-system exactly as before")
    group.add_argument("--status", action="store_true", help="what still uses the old path")
    group.add_argument("--drop-link", action="store_true", help="remove the old-path link once nothing uses it")
    args = parser.parse_args()
    if args.rollback:
        sys.exit(rollback())
    if args.status:
        sys.exit(status())
    if args.drop_link:
        sys.exit(drop_link())
    sys.exit(move(args.dry_run))
