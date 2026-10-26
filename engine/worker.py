"""A remote builder (#185): claims work from the hub, builds it here, reports back. `python3 -m engine worker run`.

It runs in its own clone of the repository (the PC through WSL2, #187), never touches the hub's database, and keeps
a private one of its own (.devloop/worker.db) only for the live log and Claude Code's process. Each job:
  1. claim from the hub (POST /v1/worker/claim, with this worker's name and token): the task, the branch name, the
     whole build prompt and the limits; nothing to do → wait and ask again;
  2. a fresh worktree from origin/main, Claude Code headless in it, under the repository's gatekeeper hook;
  3. the same check the Pi makes (devloop.checked: commits, the full test suite, the outside-code rule);
  4. push the branch, open the PR, report status, summary and tokens (POST /v1/worker/report).
Meanwhile a heartbeat every minute renews the lease and sends the new log lines to Build › Live; when the hub says
stop (Djeddi pressed Stop, or the kill switch), the build is ended. Settings come from the environment:
ATLAS_HUB_URL (e.g. http://pi:8766 over Tailscale), ATLAS_WORKER_NAME and the worker's token (ATLAS_WORKER_TOKEN).
"""
import json
import os
import shutil
import signal
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import db, devloop

LOCAL_DB = devloop.WORK / "worker.db"


class Hub:
    def __init__(self, url: str, name: str, secret: str):
        if not (url and name and secret):
            raise SystemExit("Set ATLAS_HUB_URL, ATLAS_WORKER_NAME and ATLAS_WORKER_TOKEN (see docs/WORKERS.md)")
        self.url, self.name, self.secret = url.rstrip("/"), name, secret

    def post(self, what: str, body: dict, timeout: int = 60) -> tuple[int, dict]:
        req = urllib.request.Request(f"{self.url}/v1/worker/{what}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Atlas-Worker": self.name,
                                              "Authorization": f"Bearer {self.secret}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"error": str(e)}


def from_env() -> Hub:
    return Hub(os.environ.get("ATLAS_HUB_URL", ""), os.environ.get("ATLAS_WORKER_NAME", ""), os.environ.get("ATLAS_WORKER_TOKEN", ""))


def low_disk() -> str:
    """Why this device should not claim work now ('' = enough free disk; ATLAS_WORKER_MIN_FREE_GB, default 5)."""
    need = float(os.environ.get("ATLAS_WORKER_MIN_FREE_GB") or 5)
    free = shutil.disk_usage(devloop.ROOT).free / 1e9
    return f"Only {free:.1f} GB free here (at least {need:g} GB needed): not claiming work." if free < need else ""


def _local():
    devloop.WORK.mkdir(exist_ok=True)
    con = db.connect(LOCAL_DB)
    db.init(con)
    return con


class Beat:
    """The heartbeat thread: renews the lease, forwards new log lines, and ends the build when the hub says stop."""

    def __init__(self, hub: Hub, job: dict, con_path: Path, rid: int):
        self.hub, self.job, self.rid, self.sent, self.stopped, self.lost = hub, job, rid, 0, threading.Event(), ""
        self.con_path, self.done = con_path, threading.Event()

    def lines(self) -> list[str]:
        p = devloop.log_path(self.rid)
        if not p.exists():
            return []
        all_ = p.read_text(errors="replace").splitlines()
        new, self.sent = all_[self.sent:], len(all_)
        return new

    def tick(self) -> None:
        code, out = self.hub.post("heartbeat", {"lease": self.job["lease"], "lines": self.lines()}, timeout=30)
        if code == 200 and out.get("stop"):
            self.end("the hub asked to stop")
        elif code in (403, 409):  # the lease ran out or the worker was revoked: someone else may take the task
            self.lost = out.get("error", "the lease is no longer ours")
            self.end(self.lost)

    def end(self, why: str) -> None:
        self.stopped.set()
        con = db.connect(self.con_path)
        try:
            pid = int(db.get_state(con, f"devloop_pid_{self.rid}", "0") or 0)
        finally:
            con.close()
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self) -> None:
        while not self.done.wait(int(self.job.get("heartbeat_seconds", 60))):
            try:
                self.tick()
            except (OSError, ValueError):
                pass  # the hub is briefly unreachable: the lease outlasts a few missed beats


def build(hub: Hub, job: dict, claude: str | None = None) -> dict:
    """One claimed job, start to finish; returns what was reported."""
    con = _local()
    claude = claude or devloop.claude_path() or "claude"
    rid = db.insert(con, "dev_runs", task_id=job["task"], branch=job["branch"], status="running", started=db.now(), head="")
    con.commit()
    tree = devloop.WORK / f"wt-{job['task']}-{job['branch'].rsplit('-', 1)[-1]}"
    beat = Beat(hub, job, LOCAL_DB, rid)
    threading.Thread(target=beat.run, daemon=True).start()
    started, result = time.monotonic(), {}
    try:
        devloop._git("fetch", "-q", "origin", "main", timeout=180)
        devloop._git("worktree", "add", "-q", "-b", job["branch"], str(tree), job.get("base") or "origin/main")
        start = devloop._git("rev-parse", "HEAD", cwd=tree)
        cmd = ([claude, "-p", job["prompt"], "--output-format", "stream-json", "--verbose", "--max-turns", str(int(job["max_turns"]))]
               + (["--model", job["model"]] if job.get("model") else []))
        summary, failed, used = devloop.run_agent(con, cmd, tree, rid, int(job["max_minutes"]))
        result = {k: used.get(k, 0) for k in ("turns", "tokens_in", "tokens_out", "cost_usd")}
        if beat.stopped.is_set():
            result |= {"status": "stopped", "summary": f"Stopped: {beat.lost or 'the hub asked to stop'}.\n{summary}".strip()}
        else:
            outcome, problem = devloop.checked(rid, tree, failed, summary, start, bool(job.get("outside_ok")))
            if outcome == "ok":
                devloop.note(rid, "pushing the branch and opening the PR")
                devloop._git("push", "-q", "-u", "origin", job["branch"], cwd=tree, timeout=180)
                body = (f"Built by Atlas's builder on worker {hub.name} for task #{job['task']}: {job['title']}\n\n{summary}\n\n"
                        "Started from origin/main. Tests pass on the worker. Review, then merge; Atlas marks the task done once it is in main.")
                url = devloop.open_pr(job["branch"], f"Atlas dev: {job['title'][:70]}", body, tree)
                result |= {"status": "pr_open", "summary": summary, "pr_url": url, "head": devloop._git("rev-parse", "HEAD", cwd=tree)}
            elif outcome == "no_changes":
                result |= {"status": "failed" if failed else "no_changes", "summary": summary or "no commits"}
            elif outcome == "tests_failed":
                result |= {"status": "tests_failed", "summary": summary + "\n\nTests failed:\n" + "\n".join(problem.splitlines()[-8:])}
            else:
                result |= {"status": "failed", "summary": summary + "\n\nNot opened as a PR (security gate):\n" + problem}
    except Exception as e:  # anything here ends this job, never the worker
        result |= {"status": "failed", "summary": f"The worker could not finish: {e}"[:2000]}
    finally:
        beat.done.set()
        devloop._git("worktree", "remove", "--force", str(tree), check=False)
        con.close()
    result["seconds"] = int(time.monotonic() - started)
    if not beat.lost:
        try:
            beat.tick()  # the last log lines
        except (OSError, ValueError):
            pass
        hub.post("report", {"lease": job["lease"], "result": result})
    return result


def run(hub: Hub | None = None, once: bool = False) -> None:
    """Claim, build, report, forever (or once); waits as long as the hub says when there is nothing to do."""
    hub = hub or from_env()
    while True:
        low = low_disk()
        if low:  # a build needs room for a worktree and its test database: do not claim what cannot finish
            print(low, flush=True)
            try:  # the hub only notes why, so Forge › Build can say it (#193)
                hub.post("claim", {"idle": low})
            except OSError:
                pass
            if once:
                return
            time.sleep(600)
            continue
        try:
            code, job = hub.post("claim", {})
        except OSError as e:
            code, job = 0, {"why": f"the hub is not reachable ({e})", "retry_seconds": 120}
        if code == 200 and job.get("task"):
            print(f"Building #{job['task']} {job['title']}", flush=True)
            out = build(hub, job)
            print(f"#{job['task']}: {out.get('status')}", flush=True)
        else:
            print(f"Nothing to build: {job.get('why') or job.get('error') or code}", flush=True)
            if code in (401, 403):
                raise SystemExit(job.get("error") or "the hub refused this worker")
            if once:
                return
            time.sleep(int(job.get("retry_seconds") or 300))
        if once:
            return
