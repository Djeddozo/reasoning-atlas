"""Updates for this computer's copy of Atlas (one Update button for PC and Pi, ADR-022).

`status()` reads what is known without network; `check()` fetches origin/main first. `apply()` installs
only by fast-forward: it never overwrites hand-made changes or local commits, it stops and says why.

Rewritten history (2026-10-25): when GitHub's history is rewritten (for example to publish the repository),
this computer's commits come back with new ids, so it looks hundreds of commits "ahead" and "behind" at once.
A local commit whose subject and author time also exist on GitHub is already there. When that holds for every
local commit, the update realigns: the old state is kept as a branch `backup/before-realign-…`, then the copy
follows GitHub's main. Ignored files (databases, .env, runtime) are never touched. Truly local work still blocks.
After installing, the service restarts through Start Atlas.cmd, which rebuilds the screen first.
"""
import subprocess, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_lock = threading.Lock()
_cache = {"checkedAt": None, "error": ""}


def _git(root, *args, timeout=60):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300] or f"git {args[0]} failed")
    return r.stdout.rstrip()


def _log(root, rng):
    """(short sha, subject, author time) per commit in a range."""
    return [tuple(l.split("\t", 2)) for l in _git(root, "log", "--format=%h%x09%s%x09%at", rng).splitlines() if l]


def status(root=ROOT):
    """No network: from the refs the last check fetched."""
    try:
        sha, subject = _git(root, "log", "-1", "--format=%h%x09%s").split("\t", 1)
        new, ahead = _log(root, "HEAD..origin/main"), _log(root, "origin/main..HEAD")
        dirty = [l[3:] for l in _git(root, "status", "--porcelain", "--untracked-files=no").splitlines() if l.strip()]
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"current": None, "behind": 0, "commits": [], "blocker": f"Git is not available here: {exc}",
                "checkedAt": _cache["checkedAt"], "error": _cache["error"]}
    mine = {(s, t) for _, s, t in ahead}
    realign = bool(ahead) and bool(new) and mine <= {(s, t) for _, s, t in new}
    if realign:  # the same work under new ids: only what is really new on GitHub is waiting
        new = [c for c in new if (c[1], c[2]) not in mine]
    blocker = ""
    if dirty:
        blocker = "Files changed by hand on this computer: " + ", ".join(dirty[:5]) + ". Save or undo them first."
    elif ahead and not realign:
        blocker = f"This computer has {len(ahead)} commit(s) that are not on GitHub; push them first."
    return {"current": {"sha": sha, "subject": subject}, "behind": len(new) or (1 if realign else 0), "realign": realign,
            "commits": [{"sha": s, "subject": t} for s, t, _ in new][:20], "blocker": blocker,
            "note": "GitHub's history was rewritten; Update realigns this computer and keeps the old state as a branch."
            if realign else "", "checkedAt": _cache["checkedAt"], "error": _cache["error"]}


def check(root=ROOT):
    with _lock:
        try:
            _git(root, "fetch", "-q", "origin", "main", timeout=120)
            _cache.update(checkedAt=time.time(), error="")
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            _cache.update(error=f"Could not reach GitHub: {exc}")
    return status(root)


def apply(root=ROOT):
    """Fast-forward to origin/main, or realign after a history rewrite. Returns (installed: bool, message)."""
    info = check(root)
    if info["error"]:
        return False, info["error"]
    if not info["behind"]:
        return False, f"This computer is up to date ({info['current']['sha']})."
    if info["blocker"]:
        return False, "Not installed. " + info["blocker"]
    with _lock:
        old = info["current"]["sha"]
        if info.get("realign"):
            backup = "backup/before-realign-" + time.strftime("%Y%m%d-%H%M")
            _git(root, "branch", backup, "HEAD")
            _git(root, "reset", "-q", "--hard", "origin/main")
            new = _git(root, "log", "-1", "--format=%h")
            return True, (f"GitHub's history was rewritten; this computer now follows it ({old} → {new}). "
                          f"The old state is kept as branch {backup}; data files were not touched.")
        _git(root, "merge", "-q", "--ff-only", "origin/main")
        new = _git(root, "log", "-1", "--format=%h")
    return True, f"Updated this computer {old} → {new} ({info['behind']} change(s))."


def watch(every=600, root=ROOT):
    """Background check every `every` seconds, so the screen can show "update available" without a click."""
    def loop():
        while True:
            check(root)
            time.sleep(every)
    threading.Thread(target=loop, daemon=True, name="atlas-update-check").start()
