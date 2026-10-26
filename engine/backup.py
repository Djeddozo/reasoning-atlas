"""Private backup of the owner's data, outside the public repository (owner's choice, 2026-10-25).

The repository is public; the data is not in it (databases, .env and runtime files are git-ignored). Once a day this
makes a consistent snapshot of the Engine's database (SQLite's online backup, safe while Atlas runs), encrypts it with
AES-256 (openssl, key in ATLAS_BACKUP_KEY in the Pi's .env, never in Git or the database) and pushes it to a separate
private GitHub repository (ATLAS_BACKUP_REPO, e.g. Djeddozo/atlas-private-data).

The backup repository holds one commit only, replaced each day (force push), with the last 7 daily snapshots and
one per month, so it never grows without bound. Local copies stay in runtime/backups (owner-only permissions).
Without a key nothing leaves the Pi: a local snapshot is still made and Systems says the cloud copy is not set up.
Restore never overwrites the live database: it writes memory/hq.restored.db for the owner to inspect and swap.
"""
import os
import sqlite3
import subprocess
import time
from datetime import date
from pathlib import Path

from . import db

LOCAL = db.ROOT / "runtime" / "backups"
CLONE = db.ROOT / "runtime" / "backup-repo"
KEEP_DAILY = 7


def _key() -> str:
    return os.environ.get("ATLAS_BACKUP_KEY", "")


def _repo() -> str:
    return os.environ.get("ATLAS_BACKUP_REPO", "")


def configured() -> dict:
    return {"key": bool(_key()), "repo": _repo(), "cloud": bool(_key() and _repo())}


def snapshot(source: Path = None, folder: Path = LOCAL) -> Path:
    """A consistent copy of the database, taken while Atlas keeps running."""
    source = Path(source or db.DB_PATH)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    out = folder / f"hq-{date.today().isoformat()}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(out) as dst:
        src.backup(dst)
    os.chmod(out, 0o600)
    return out


def _openssl(args, key):
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", *args, "-pass", "env:ATLAS_BACKUP_KEY"],
                       capture_output=True, text=True, timeout=300, env={**os.environ, "ATLAS_BACKUP_KEY": key})
    if r.returncode:
        raise RuntimeError("openssl failed: " + r.stderr.strip()[:200])


def encrypt(plain: Path, key: str) -> Path:
    out = plain.with_suffix(plain.suffix + ".enc")
    _openssl(["-in", str(plain), "-out", str(out)], key)
    os.chmod(out, 0o600)
    return out


def decrypt(blob: Path, out: Path, key: str) -> Path:
    _openssl(["-d", "-in", str(blob), "-out", str(out)], key)
    os.chmod(out, 0o600)
    return out


def _keep(names: list[str]) -> set[str]:
    """The last KEEP_DAILY dailies and the newest snapshot of every month."""
    dailies = sorted(n for n in names if n.startswith("hq-") and n.endswith(".db.enc"))
    keep = set(dailies[-KEEP_DAILY:])
    months = {}
    for n in dailies:
        months[n[3:10]] = n
    return keep | set(months.values())


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise RuntimeError(f"git {args[0]}: " + (r.stderr or r.stdout).strip()[:200])
    return r.stdout


def push(blob: Path, repo: str, clone: Path = CLONE, remote: str = "") -> str:
    """Replace the backup repository's single commit with the snapshots to keep."""
    remote = remote or f"https://github.com/{repo}.git"
    clone.mkdir(parents=True, exist_ok=True)
    if not (clone / ".git").exists():
        _git(clone, "init", "-q", "-b", "main")
        _git(clone, "remote", "add", "origin", remote)
    (clone / blob.name).write_bytes(blob.read_bytes())
    for f in clone.iterdir():
        if f.name.endswith(".db.enc") and f.name not in _keep([x.name for x in clone.iterdir()]):
            f.unlink()  # only in the clone: the repository keeps one commit with what is kept
    (clone / "README.md").write_text("Encrypted Atlas data snapshots (AES-256, openssl). Restore: ./atlas.sh backup restore FILE\n")
    _git(clone, "checkout", "-q", "--orphan", "snap")
    _git(clone, "add", "-A")
    _git(clone, "-c", "user.name=Atlas", "-c", "user.email=atlas@localhost", "commit", "-qm", f"Snapshot {date.today().isoformat()}")
    _git(clone, "branch", "-q", "-M", "main")
    _git(clone, "push", "-q", "-f", "origin", "main")
    return remote


def run(con=None, source: Path = None) -> str:
    plain = snapshot(source)
    for f in LOCAL.glob("hq-*.db"):  # local plain copies: the last KEEP_DAILY days only
        if f.name not in sorted(x.name for x in LOCAL.glob("hq-*.db"))[-KEEP_DAILY:]:
            f.unlink()
    c = configured()
    if not c["key"]:
        msg = f"Local snapshot {plain.name}; no cloud copy (set ATLAS_BACKUP_KEY and ATLAS_BACKUP_REPO in .env)."
        action = "backup_local"
    else:
        blob = encrypt(plain, _key())
        if c["repo"]:
            try:
                push(blob, c["repo"])
                msg, action = f"Encrypted snapshot {blob.name} pushed to the private repository {c['repo']}.", "backup"
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                msg, action = f"Encrypted snapshot {blob.name} kept on the Pi; push failed: {e}", "backup_failed"
        else:
            msg, action = f"Encrypted snapshot {blob.name} kept on the Pi; set ATLAS_BACKUP_REPO to copy it off the Pi.", "backup_local"
    own = con or db.connect()
    try:
        db.log(own, "hq", action, msg[:300])
        own.commit()
    finally:
        if con is None:
            own.close()
    return msg


def restore(blob: Path, out: Path = None) -> Path:
    """Decrypt a snapshot next to the live database; never replaces it."""
    if not _key():
        raise RuntimeError("Set ATLAS_BACKUP_KEY (the same key that made the snapshot) first.")
    out = out or db.ROOT / "memory" / "hq.restored.db"
    return decrypt(Path(blob), out, _key())


def status(con) -> dict:
    r = con.execute("SELECT ts, action, detail FROM log WHERE action IN ('backup', 'backup_local', 'backup_failed') ORDER BY id DESC LIMIT 1").fetchone()
    return {**configured(), "last": dict(r) if r else None,
            "local": sorted(f.name for f in LOCAL.glob("hq-*.db*"))[-KEEP_DAILY:] if LOCAL.exists() else []}


if __name__ == "__main__":
    print(run())
