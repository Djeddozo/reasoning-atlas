"""Live build progress shared by the service, the build script and agents.

State lives in runtime/build-status.json (ignored by Git, local to this
computer). Writes are atomic so the running app never reads a half-written file,
and they retry briefly because Windows can hold a file open for a moment.
"""
import json, os, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUS_FILE = ROOT / "runtime" / "build-status.json"
STALL_SECONDS = 15 * 60
IDLE = {"schemaVersion": 1, "state": "idle", "taskId": None, "title": None, "steps": [],
        "step": 0, "progress": 0, "message": "", "startedAt": None, "updatedAt": None}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    raise PermissionError(f"Could not update {path}")


def read(path=None, hide_finished=False):
    path = Path(path or STATUS_FILE)
    try:
        data = {**IDLE, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        data = dict(IDLE)
    if hide_finished and data["state"] in ("building", "stalled") and data.get("taskId") and _task_done(data["taskId"]):
        return dict(IDLE)  # the task was finished and checked in elsewhere (another computer, the dev loop)
    if data["state"] == "building" and data["updatedAt"]:
        age = time.time() - datetime.fromisoformat(data["updatedAt"].replace("Z", "+00:00")).timestamp()
        if age > STALL_SECONDS:
            data["state"] = "stalled"
            data["message"] = "The builder stopped reporting progress (for example, a usage limit). Work so far is kept."
    return data


def _task_done(task_id, tasks_file=None):
    try:
        tasks = json.loads(Path(tasks_file or Path(__file__).resolve().parents[2] / "STATE" / "tasks.json").read_text(encoding="utf-8"))["tasks"]
        return any(t["id"] == task_id and t["status"] == "done" for t in tasks)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _percent(step, total):
    return round(100 * step / total) if total else 0


def start(task_id, title, steps, path=None):
    data = {**IDLE, "state": "building", "taskId": task_id, "title": title, "steps": list(steps),
            "step": 0, "progress": 0, "message": steps[0] if steps else "", "startedAt": now(), "updatedAt": now()}
    _write(Path(path or STATUS_FILE), data)
    return data


def step(index, message=None, progress=None, path=None):
    """Mark step `index` (1-based) as the one being worked on."""
    path = Path(path or STATUS_FILE)
    data = read(path)
    total = len(data["steps"])
    index = max(1, min(index, total or index))
    data.update(state="building", step=index, updatedAt=now(),
                message=message or (data["steps"][index - 1] if total else ""),
                progress=progress if progress is not None else _percent(index - 1, total))
    _write(path, data)
    return data


def finish(message="Done", path=None):
    path = Path(path or STATUS_FILE)
    data = read(path)
    data.update(state="done", step=len(data["steps"]), progress=100, message=message, updatedAt=now())
    _write(path, data)
    return data


def fail(message, path=None):
    path = Path(path or STATUS_FILE)
    data = read(path)
    data.update(state="failed", message=message, updatedAt=now())
    _write(path, data)
    return data
