"""Build the web client without disturbing a running app.

Each build goes into a new folder, apps/web/dist/releases/<release-id>. Only
after it succeeds is apps/web/dist/current.json switched to it, in one atomic
step. The service reads that pointer on every request, so an open browser keeps
working during the build and picks up the new release by itself. Files that an
open app may be serving are never overwritten. The newest three releases are kept.

  python scripts/build_web.py
"""
import hashlib, json, os, shutil, subprocess, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.atlas import progress, versioning  # noqa: E402

DIST = ROOT / "apps" / "web" / "dist"
RELEASES = DIST / "releases"
KEEP = 3


def tool(name):
    found = shutil.which(name) or shutil.which(name + ".cmd")
    if not found:
        sys.exit(f"{name} not found. Install Node.js 22.12+ and run npm ci.")
    return found


def note(message):
    status = progress.read()
    if status["state"] == "building":
        progress.step(status["step"] or 1, message=message, progress=status["progress"])


def sync_dependencies():
    """Run npm ci only when package-lock.json differs from what was installed."""
    lock = ROOT / "package-lock.json"
    marker = ROOT / "node_modules" / ".atlas-lock-sha256"
    wanted = hashlib.sha256(lock.read_bytes()).hexdigest()
    try:
        if marker.read_text(encoding="utf-8").strip() == wanted:
            return
    except OSError:
        pass
    note("Installing locked dependencies")
    subprocess.run([tool("npm"), "ci", "--no-audit", "--no-fund"], cwd=ROOT, check=True)
    marker.write_text(wanted, encoding="utf-8")


def main():
    sync_dependencies()
    release = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    source = versioning.client_fingerprint(ROOT)  # taken before building, so edits during the build count as new
    target = RELEASES / release
    note("Type-checking the client")
    subprocess.run([tool("npx"), "tsc", "--noEmit"], cwd=ROOT, check=True)
    note("Building a new client release")
    subprocess.run([tool("npx"), "vite", "build", "apps/web", "--outDir", str(target), "--emptyOutDir"],
                   cwd=ROOT, check=True)
    pointer = {"release": release, "source": source, "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    progress._write(DIST / "current.json", pointer)
    note("New release live; open windows update automatically")
    old = sorted(p for p in RELEASES.iterdir() if p.is_dir() and p.name != release)
    for stale in old[:-(KEEP - 1)] if len(old) >= KEEP else []:
        shutil.rmtree(stale, ignore_errors=True)  # a file still open on Windows is retried next build
    print(f"Release {release} is live.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        status = progress.read()
        if status["state"] == "building":
            progress.fail("Client build failed; the previous release is still running.")
        sys.exit(exc.returncode)
