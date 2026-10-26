"""Resume a task after a session switch or usage cap from the repository alone (RA-015).

A builder can stop at any moment: a usage limit, a closed app, a switch from
Claude to Codex. The next session has no conversation, only this folder. This
module rebuilds where work stopped from what the repository holds:

- STATE/active-task.json: the claim (task, owner, session) and the exact next action;
- the checkpoint: the last commit that changed STATE/active-task.json;
- work after the checkpoint: later commits and uncommitted changes;
- STATE/effects.json: a write-ahead ledger of external effects (push, pull
  request, message). An effect is recorded as "intended" before it happens and
  "done" after. A new session skips done effects and verifies intended ones
  instead of repeating them, so a stop never causes a duplicate.

  python scripts/resume.py show            # where work stopped, what to do next
  python scripts/resume.py claim RA-015 --owner Claude --session s1
  python scripts/resume.py checkpoint --next "Write the evidence file"
  python scripts/resume.py effect begin push:RA-015 --kind push --target origin/feat/ra-015
  python scripts/resume.py effect finish push:RA-015 done
"""
import json, os, subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = "STATE/active-task.json"
EFFECTS = "STATE/effects.json"
OUTCOMES = ("done", "failed", "not_done")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # atomic: a stop mid-write leaves the old file, never half a file


def _git(root, *args, strip=True):
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() if strip else out.stdout.rstrip("\n")


# Claim and checkpoint

def claim(task_id, owner, session, root=ROOT, branch=None):
    """Record who works on which task before implementation (AGENTS.md)."""
    root = Path(root)
    tasks = {t["id"] for t in _read(root / "STATE/tasks.json", {"tasks": []})["tasks"]}
    if task_id not in tasks:
        raise ValueError(f"Unknown task {task_id}")
    active = _read(root / ACTIVE, {"schemaVersion": 1})
    active.update({"taskId": task_id, "owner": owner, "sessionId": session, "status": "in_progress",
                   "branch": branch or _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
                   "claimedAt": now(), "nextAction": active.get("nextAction") if active.get("taskId") == task_id else None})
    _write(root / ACTIVE, active)
    return active


def checkpoint(next_action, root=ROOT, note=None, status=None):
    """Stamp the claim with the exact next action; the commit that carries it is the checkpoint."""
    root = Path(root)
    active = _read(root / ACTIVE, None)
    if not active or not active.get("taskId"):
        raise ValueError("No task is claimed; run claim first")
    active.update({"nextAction": next_action, "checkpointAt": now(),
                   "lastCheckpointCommit": _git(root, "rev-parse", "--short", "HEAD") or active.get("lastCheckpointCommit")})
    if note:
        active["note"] = note
    if status:
        active["status"] = status
    _write(root / ACTIVE, active)
    return active


# External effects

def effects(root=ROOT):
    return _read(Path(root) / EFFECTS, {"schemaVersion": 1, "effects": []})


def begin(key, task_id, kind, target, root=ROOT):
    """Ask before an external effect. Returns "run", "skip" (already done) or "verify" (outcome unknown).

    "run" records the intent first, so a stop between the effect and finish()
    leaves a trace the next session must verify instead of repeating blindly.
    """
    ledger = effects(root)
    entry = next((e for e in ledger["effects"] if e["key"] == key), None)
    if entry and entry["state"] == "done":
        return "skip", entry
    if entry and entry["state"] == "intended":
        return "verify", entry
    entry = entry or {"key": key}
    if entry not in ledger["effects"]:
        ledger["effects"].append(entry)
    entry.update({"taskId": task_id, "kind": kind, "target": target, "state": "intended", "intendedAt": now(),
                  "attempts": entry.get("attempts", 0) + 1})
    _write(Path(root) / EFFECTS, ledger)
    return "run", entry


def finish(key, outcome, root=ROOT, detail=""):
    """Record the outcome: done, failed (may be retried), or not_done (verified it never happened)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"Outcome must be one of {', '.join(OUTCOMES)}")
    ledger = effects(root)
    entry = next((e for e in ledger["effects"] if e["key"] == key), None)
    if not entry:
        raise ValueError(f"No effect {key}; call begin first")
    entry.update({"state": "done" if outcome == "done" else "failed", "finishedAt": now(), "detail": detail})
    _write(Path(root) / EFFECTS, ledger)
    return entry


# Resume

def _next_action_from_evidence(path):
    """The '## Exact next action' section or '- Exact next action:' line of an evidence file."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for i, line in enumerate(lines):
        low = line.strip().lower()
        if low.startswith("- exact next action:"):
            return line.split(":", 1)[1].strip() or None
        if low.startswith("#") and "exact next action" in low:
            body = []
            for rest in lines[i + 1:]:
                if rest.startswith("#"):
                    break
                if rest.strip():
                    body.append(rest.strip())
            return " ".join(body) or None
    return None


def resume(root=ROOT):
    """Where work stopped and what the next session does first, from the repository alone."""
    root = Path(root)
    active = _read(root / ACTIVE, {}) or {}
    task_id = active.get("taskId")
    tasks = {t["id"]: t for t in _read(root / "STATE/tasks.json", {"tasks": []})["tasks"]}
    task = tasks.get(task_id)
    checkpoint_commit = _git(root, "log", "-1", "--format=%h", "--", ACTIVE)
    later = _git(root, "log", "--format=%h %s", f"{checkpoint_commit}..HEAD") if checkpoint_commit else None
    status = _git(root, "status", "--porcelain", "--untracked-files=all", strip=False)
    dirty = [line[3:] for line in (status or "").splitlines() if line[3:] and not line[3:].startswith("runtime/")]
    commits = [line for line in (later or "").splitlines() if line]
    ledger = effects(root)["effects"]
    verify = [e for e in ledger if e["state"] == "intended"]
    done = [e for e in ledger if e["state"] == "done" and e.get("taskId") == task_id]
    evidence = root / "STATE" / "evidence" / f"{task_id}.md" if task_id else None
    next_action = active.get("nextAction") or (_next_action_from_evidence(evidence) if evidence else None)
    beyond = bool(dirty or commits)
    steps = []
    if verify:
        steps.append("Verify each unsettled external effect before anything else, then record it with "
                     "`python scripts/resume.py effect finish KEY done|not_done`: " + "; ".join(f"{e['key']} ({e['kind']} → {e['target']})" for e in verify) + ".")
    if dirty:
        steps.append(f"Inspect the {len(dirty)} uncommitted file(s) with git diff; keep what matches the next action, then save a checkpoint.")
    if commits:
        steps.append(f"Review the {len(commits)} commit(s) after the checkpoint; they are kept but not yet stamped with a next action.")
    if task and task.get("status") == "done":
        steps.append(f"{task_id} is already done; choose the next ready task instead of repeating it.")
    elif next_action:
        steps.append(f"Continue: {next_action}")
    elif task_id:
        steps.append(f"No next action was recorded; read STATE/evidence/{task_id}.md and the task's acceptance.")
    else:
        steps.append("No task is claimed; choose one ready task from TASKS.md and claim it.")
    if done:
        steps.append("Do not repeat these finished external effects: " + ", ".join(e["key"] for e in done) + ".")
    return {
        "taskId": task_id, "title": task["title"] if task else None, "taskStatus": task["status"] if task else None,
        "claim": {k: active.get(k) for k in ("owner", "sessionId", "branch", "status", "claimedAt", "checkpointAt")},
        "checkpointCommit": checkpoint_commit, "head": _git(root, "rev-parse", "--short", "HEAD"),
        "stopPoint": "before-checkpoint" if beyond else "at-checkpoint",
        "commitsAfterCheckpoint": commits, "uncommitted": dirty,
        "nextAction": next_action, "verifyEffects": verify, "doneEffects": done,
        "evidence": f"STATE/evidence/{task_id}.md" if evidence and evidence.exists() else None,
        "steps": steps,
    }


def text(state):
    """The resume state as a few lines for a handoff packet or a terminal."""
    if not state["taskId"]:
        return "Resume state: no task is claimed in STATE/active-task.json."
    claim = state["claim"]
    lines = [f"Resume state ({state['stopPoint'].replace('-', ' ')}): {state['taskId']}"
             + (f" — {state['title']}" if state["title"] else "")
             + (f", claimed by {claim['owner']}" if claim.get("owner") else "")
             + (f" in session {claim['sessionId']}" if claim.get("sessionId") else "") + "."]
    lines.append(f"Checkpoint commit {state['checkpointCommit'] or 'unknown'}, head {state['head'] or 'unknown'}"
                 + (f", {len(state['commitsAfterCheckpoint'])} commit(s) after it" if state["commitsAfterCheckpoint"] else "")
                 + (f", {len(state['uncommitted'])} uncommitted file(s)" if state["uncommitted"] else "") + ".")
    lines += [f"{i}. {step}" for i, step in enumerate(state["steps"], 1)]
    return "\n".join(lines)
