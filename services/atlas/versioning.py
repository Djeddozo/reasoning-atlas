"""Detect when the running app is older than the code on disk.

Two kinds of change matter:
- service code (Python under services/, packages/, adapters/): only a restart
  loads it;
- client sources (apps/web/src, index.html, package files): need a new build,
  which the launcher makes when it restarts.
Fingerprints use file paths, sizes and modification times, so checking is cheap
enough to run on every status poll.
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIRS = ("services", "packages", "adapters")
CLIENT_PATHS = ("apps/web/src", "apps/web/index.html", "package.json", "package-lock.json", "tsconfig.json")


def _files(root, entries, pattern):
    for entry in entries:
        path = root / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from (p for p in path.rglob(pattern) if p.is_file() and "__pycache__" not in p.parts)


def fingerprint(root, entries, pattern="*"):
    digest = hashlib.sha256()
    for path in sorted(_files(root, entries, pattern)):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(root).as_posix()}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:16]


def service_fingerprint(root=None):
    return fingerprint(Path(root or ROOT), SERVICE_DIRS, "*.py")


def client_fingerprint(root=None):
    return fingerprint(Path(root or ROOT), CLIENT_PATHS)


def restart_status(started_fingerprint, release_source, root=None):
    """Return {"needed": bool, "reasons": [...]} for the header marker."""
    reasons = []
    if service_fingerprint(root) != started_fingerprint:
        reasons.append("The service code changed since the app started.")
    if release_source is not None and client_fingerprint(root) != release_source:
        reasons.append("The screen's code changed and has not been built yet.")
    return {"needed": bool(reasons), "reasons": reasons}
