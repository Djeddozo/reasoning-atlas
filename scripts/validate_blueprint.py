"""Validate the delivered blueprint; no external dependencies or runtime calls."""
from pathlib import Path
import hashlib
import json
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
errors = []
required = ["README.md", "VISION.md", "VISION_QUESTIONS.md", "AGENTS.md",
            "CLAUDE.md", "ROADMAP.md", "TASKS.md", "DECISIONS.md", "HANDOFF.md",
            "STATE/CURRENT_PROGRESS.md", "STATE/tasks.json", "STATE/active-task.json",
            "docs/PRODUCT.md", "docs/DESIGN.md", "docs/INTERACTIONS.md",
            "docs/DATA_MODEL.md", "docs/ARCHITECTURE.md", "docs/INTEGRATION_ENGINE.md",
            "docs/DEVELOPMENT.md", "docs/TESTING.md", "docs/SOURCES.md",
            "design/tokens.json", "assets/reference/reference-manifest.json",
            "contracts/hq-adapter.openapi.json", "docs/COVERAGE.md",
            "assets/reference/measurements.json"]
for name in required:
    if not (ROOT / name).is_file():
        errors.append("Missing file: " + name)
SKIP = {"node_modules", "dist", ".git", ".venv", "runtime", "__pycache__"}
def files(pattern):
    return [p for p in ROOT.rglob(pattern) if not SKIP.intersection(p.relative_to(ROOT).parts)]
for path in files("*.json"):
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        errors.append(f"Invalid JSON {path.relative_to(ROOT)}: {exc}")
for path in files("*.md"):
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
            continue
        target = target.split("#", 1)[0]
        if target and not (path.parent / target).exists():
            errors.append(f"Broken link {path.relative_to(ROOT)} -> {target}")

tasks = json.loads((ROOT / "STATE/tasks.json").read_text(encoding="utf-8"))["tasks"]
by_id = {t["id"]: t for t in tasks}
if len(by_id) != len(tasks):
    errors.append("Duplicate task ID")
allowed = {"todo", "ready", "in_progress", "blocked", "review", "done"}
for t in tasks:
    if t["status"] not in allowed:
        errors.append("Invalid status: " + t["id"])
    path_ids = {p["id"] for p in json.loads((ROOT / "STATE/tasks.json").read_text(encoding="utf-8")).get("paths", [])}
    if t.get("program") is None or t.get("path") not in path_ids:
        errors.append(f"{t['id']} needs a program and a known path (docs/MODEL.md)")
    for field in ["title", "acceptance", "validation", "evidence"]:
        if not t.get(field):
            errors.append(f"{t['id']} missing {field}")
    for dep in t["dependsOn"]:
        if dep not in by_id:
            errors.append(f"{t['id']} unknown dependency {dep}")
        elif t["status"] in {"done", "ready"} and by_id[dep]["status"] != "done":
            errors.append(f"{t['id']} cannot be {t['status']} before {dep}")
def visit(task_id, active, complete):
    if task_id in active:
        errors.append("Dependency cycle: " + task_id)
        return
    if task_id in complete or task_id not in by_id:
        return
    for dep in by_id[task_id]["dependsOn"]:
        visit(dep, active | {task_id}, complete)
    complete.add(task_id)
completed = set()
for task_id in by_id:
    visit(task_id, set(), completed)

manifest = json.loads((ROOT / "assets/reference/reference-manifest.json").read_text(encoding="utf-8"))
image_path = ROOT / manifest["expectedPath"]
if manifest["status"] == "missing":
    print("OPEN BLOCKER: original reference PNG unavailable; exact design acceptance not complete.")
    if image_path.exists():
        errors.append("Reference exists but manifest still says missing")
    if "--require-reference" in sys.argv:
        errors.append("Original reference is required for visual acceptance")
elif manifest["status"] == "verified":
    if not image_path.is_file():
        errors.append("Verified reference file is missing")
    else:
        data = image_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
            errors.append("Reference SHA-256 mismatch")
        if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
            errors.append("Reference is not a PNG")
        else:
            width, height = struct.unpack(">II", data[16:24])
            if [width, height] != [manifest.get("width"), manifest.get("height")]:
                errors.append("Reference dimensions mismatch")
        if manifest.get("inspected") is not True:
            errors.append("Reference has not been visually inspected")
else:
    errors.append("Unsupported reference status")
if errors:
    print("\n".join("ERROR: " + e for e in errors))
    sys.exit(1)
print(f"PASS: {len(required)} required files, {len(tasks)} tasks, JSON and local links.")
