"""Switch the Pi's Atlas install to the Atlas repository (ADR-019), or switch it back.

Run on the Pi, inside the Atlas checkout (the folder with hq.sh). Before the switch the Pi does not have this
file yet; fetch it from Atlas first (see docs/PI_SWITCHOVER.md):

    python3 deploy/switch_to_atlas.py            # check everything, then switch
    python3 deploy/switch_to_atlas.py --check    # only report what would happen
    python3 deploy/switch_to_atlas.py --rollback # return to HQ-APP exactly as before

What switching does, in order (it stops at the first problem and undoes its own steps):
1. Checks: no local code changes, no dev-loop run building or waiting as a pull request,
   and Atlas's main already contains the exact Atlas commit this Pi runs.
2. Pauses the dev loop, remembering its exact setting (it is restored afterwards, never
   switched on if it was off).
3. Backs up the Atlas database consistently (SQLite backup API) and records the old remote and commit.
4. Points the checkout at the Atlas repository.
5. Archives HQ-APP's pull-request records (they are numbered per repository, so Atlas's
   pull requests would otherwise overwrite them) and lets delivery tracking start fresh.
6. Installs Atlas's main through Atlas's own updater: its full test suite must pass on the Pi first.
7. Restores the dev loop setting.
Nothing in the database is deleted: the archived records stay in table prs_hq_app.
"""
import argparse, json, os, shutil, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd() if (Path.cwd() / "hq.sh").exists() else Path(__file__).resolve().parents[1]  # works when downloaded on its own
ATLAS_URL = os.environ.get("ATLAS_REPO_URL", "https://github.com/Djeddozo/Unconventional-Reasoning-Atlas.git")
ATLAS_BRANCH = os.environ.get("ATLAS_BRANCH", "main")  # testing only
DB_PATH = Path(os.environ["HQ_DB"]) if os.environ.get("HQ_DB") else ROOT / "memory" / "hq.db"
STATE_FILE = ROOT / "memory" / "switch-to-atlas.json"


def git(*args, check=True):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=300)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def say(message):
    print(message, flush=True)


def connect():
    return sqlite3.connect(DB_PATH, timeout=30)


def get_state(con, key, default=None):
    row = con.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(con, key, value):
    if value is None:
        con.execute("DELETE FROM state WHERE key = ?", (key,))
    else:
        con.execute("INSERT INTO state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def checks():
    problems = []
    if sys.version_info < (3, 11):
        problems.append(f"Python {sys.version.split()[0]} is too old: the engine needs Python 3.11 or newer (tomllib).")
    if not (ROOT / "hq.sh").exists():
        problems.append("Run this inside the Atlas checkout (the folder with hq.sh).")
    if git("status", "--porcelain", "--untracked-files=no"):
        problems.append("The checkout has local code changes; commit or set them aside first (hq update shows how).")
    if not DB_PATH.exists():
        problems.append(f"Atlas database not found at {DB_PATH}.")
    else:
        with connect() as con:
            busy = con.execute("SELECT id, task_id, status FROM dev_runs WHERE status IN ('running', 'pr_open')").fetchall()
        if busy:
            problems.append("Dev-loop work is still in flight (" + ", ".join(f"run {r[0]} task #{r[1]} {r[2]}" for r in busy)
                            + "). Merge or close those pull requests first, then let an agent merge Atlas into Atlas again.")
    return problems


def switch(dry_run=False, restart=True):
    if git("remote", "get-url", "origin") in (ATLAS_URL, ATLAS_URL.removesuffix(".git")) or STATE_FILE.exists():
        say("This checkout already uses the Atlas repository. Nothing to do.")
        return 0
    problems = checks()
    head = git("rev-parse", "HEAD")
    git("fetch", "-q", ATLAS_URL, f"{ATLAS_BRANCH}:refs/remotes/atlas-candidate/main")
    if subprocess.run(["git", "merge-base", "--is-ancestor", head, "refs/remotes/atlas-candidate/main"], cwd=ROOT).returncode:
        problems.append(f"Atlas's main does not contain this Pi's Atlas commit {head[:7]}. Ask an agent to merge Atlas into Atlas again.")
    if problems:
        say("Not switching:\n- " + "\n- ".join(problems))
        return 1
    behind = git("rev-list", "--count", f"{head}..refs/remotes/atlas-candidate/main")
    say(f"Checks passed. Atlas's main is {behind} commit(s) ahead of this Pi's Atlas {head[:7]}.")
    if dry_run:
        say("Check only: nothing changed.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = DB_PATH.with_name(f"hq-before-atlas-{stamp}.db")
    old_url = git("remote", "get-url", "origin")
    with connect() as con:
        previous = {k: get_state(con, k) for k in ("devloop_paused", "devloop_autonomy", "delivery:imported")}
        set_state(con, "devloop_paused", "1")
        set_state(con, "devloop_autonomy", "off")
    say("Dev loop paused (its previous setting is saved).")
    with connect() as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    record = {"stamp": stamp, "oldUrl": old_url, "oldHead": head, "backup": str(backup), "previous": previous}
    STATE_FILE.write_text(json.dumps(record, indent=2), encoding="utf-8")
    git("branch", "-f", f"hq-app-final-{stamp}", head)
    say(f"Database backed up to {backup.name}; old commit kept as branch hq-app-final-{stamp}.")

    try:
        git("remote", "set-url", "origin", ATLAS_URL)
        git("fetch", "-q", "origin", f"{ATLAS_BRANCH}:refs/remotes/origin/main", "--force")
        with connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS prs_hq_app AS SELECT * FROM prs WHERE 0")
            con.execute("INSERT INTO prs_hq_app SELECT * FROM prs")
            con.execute("DELETE FROM prs")
            set_state(con, "delivery:imported", None)
        say("HQ-APP pull-request records archived in prs_hq_app; delivery tracking starts fresh on Atlas.")
        sys.path.insert(0, str(ROOT))
        from engine import db as hqdb, updater
        con = hqdb.connect(DB_PATH)
        result = updater.apply(con, do_restart=restart)
        con.close()
        say(result["message"])
        if result.get("status") not in ("updated", "up_to_date"):
            raise RuntimeError("the update was not installed")
    except Exception as exc:
        say(f"Switch failed ({exc}); rolling back.")
        rollback(restart=restart)
        return 1
    with connect() as con:
        for key in ("devloop_paused", "devloop_autonomy"):
            set_state(con, key, previous[key])
    say("Dev loop setting restored. The Pi now runs Atlas's main. To undo: python3 deploy/switch_to_atlas.py --rollback")
    return 0


def rollback(restart=True):
    if not STATE_FILE.exists():
        say("No switch record found; nothing to roll back.")
        return 1
    record = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    git("remote", "set-url", "origin", record["oldUrl"])
    git("fetch", "-q", "origin", "main", check=False)
    git("reset", "-q", "--hard", record["oldHead"])
    with connect() as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "prs_hq_app" in tables:
            con.execute("DELETE FROM prs")
            con.execute("INSERT INTO prs SELECT * FROM prs_hq_app")
            con.execute("DROP TABLE prs_hq_app")
        for key, value in record["previous"].items():
            set_state(con, key, value)
    STATE_FILE.rename(STATE_FILE.with_name(f"switch-to-atlas-rolled-back-{record['stamp']}.json"))
    say(f"Back on HQ-APP at {record['oldHead'][:7]}. Pull-request records and dev loop setting restored. "
        f"The database backup {Path(record['backup']).name} is kept in case it is needed.")
    if restart and (ROOT / "deploy" / "restart.sh").exists() and shutil.which("bash"):
        subprocess.Popen(["bash", str(ROOT / "deploy" / "restart.sh")], cwd=ROOT, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report only; change nothing")
    parser.add_argument("--rollback", action="store_true", help="return to HQ-APP exactly as before")
    parser.add_argument("--no-restart", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    sys.exit(rollback(not args.no_restart) if args.rollback else switch(args.check, not args.no_restart))
