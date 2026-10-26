"""Noticing parallel work (#72): which AI tasks can be built side by side, proposed as one batch.

Two tasks are independent when neither waits for the other (depends_on, directly or through other open tasks)
and they are not expected to change the same files. Which files a task will change is predicted by rule, without
a model call, from its title and description:
  - paths it names (engine/devloop.py, apps/web/src/main.tsx, …) and hub functions it names (engine.brain.x);
  - Atlas modules it names by file name ("mockup", "overlap", "cortex_mcp") or by a known alias ("dev loop",
    "builder" → engine/devloop.py), and the module of the department table in CLAUDE.md its words match best,
    when that match is clear (the overlap check's word score);
  - the web app's main.tsx and style.css when it has a visual side (mockup.is_visual): the screen is one file;
  - STATE/ for a Reasoning Atlas step (RA-nnn), whose state files every step updates.
Predicting too many files only makes a batch smaller; predicting too few risks a merge conflict, so the rules lean
towards more. A task whose files cannot be predicted is never called independent. Files nearly every task touches
with small additions in different places (CLAUDE.md, engine/db.py, engine/cli.py, engine/guides.py,
services/atlas/hub.py, tests/) do not count as a clash; Forge › Build › Queue's Resolve handles the rare conflict.
Tasks in flight (building, or a PR waiting for review) count with the files their branch really changed when git
knows it, otherwise with their prediction.

The batch: the ready tasks in the builder's order (skilltree.build_plan: Build next, his queue, proposals), taken
greedily. A task joins when it is independent of every task in the batch and of every task in flight, up to
`[parallel] batch_max` (default 3, ceiling 6). It is a proposal, worked out afresh whenever it is shown: Djeddi
accepts it (it is stored and the builder's extra slots may take only these tasks, #185) or dismisses it (that set
is not offered again); both can be undone. Nothing is built or started here.
"""
import json
import re
import subprocess
from pathlib import Path

from . import brain, db, mockup, overlap

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {"batch_max": 3, "module_match": 0.2, "module_margin": 0.04}
CEILING = 6
OPEN = ("proposed", "todo", "doing", "blocked")
COMMON = ("CLAUDE.md", "engine/db.py", "engine/cli.py", "engine/guides.py", "services/atlas/hub.py")
SCREEN = ("apps/web/src/main.tsx", "apps/web/src/style.css")
PATH = re.compile(r"\b((?:engine|services|apps|adapters|packages|scripts|deploy|config|docs|STATE|modules|tests)/[\w./-]+\.\w+)")
HUB_FN = re.compile(r"\b(?:engine|atlas)\.[a-z]+\.[a-z_]+\b")
# words that name a module without its file name; matched as whole words, case-insensitive
ALIASES = {
    "engine/devloop.py": r"dev ?loop|builder|build lanes?|workers?|slots?|leases?|stack(?:ed|s)?|pull requests?|PRs?",
    "engine/cortex.py": r"cortex",
    "engine/skilltree.py": r"skill ?tree|nodes?",
    "engine/doover.py": r"do-?over|re-?evaluat\w*|replan",
    "engine/dump.py": r"brain ?dump",
    "engine/priority.py": r"priorit\w+|rank(?:ing)?|scores?",
    "engine/interfaces/telegram.py": r"telegram",
    "engine/models.py": r"model router|routing polic\w+",
    "engine/updater.py": r"updater|self-?updates?|update button|install(?:er|ing) (?:the )?updates?",
    "engine/delivery.py": r"delivery",
    "engine/radar.py": r"radar|feeds?",
    "engine/voicesetup.py": r"voice|whisper|dictation",
}
# file names that are ordinary words: a task saying "flow" or "edit" rarely means flow.py or edit.py
PLAIN = {"agent", "agents", "brain", "chat", "edit", "explore", "export", "flow", "fresh", "launch", "loop", "needs",
         "plan", "progress", "screen", "talk", "usage", "direction", "research", "resources", "commands", "models"}


def cfg() -> dict:
    from . import loop
    conf = DEFAULTS | loop.load_constitution().get("parallel", {})
    conf["batch_max"] = max(2, min(int(conf.get("batch_max") or 3), CEILING))
    return conf


def _stems() -> dict[str, str]:
    return {f.stem: f"engine/{f.name}" for f in (ROOT / "engine").glob("*.py") if not f.stem.startswith("_")}


def predict(task: dict, conf: dict | None = None, mods: list[dict] | None = None) -> list[str]:
    """The files a task is expected to change, by rule; [] when nothing points anywhere."""
    conf = conf or DEFAULTS
    text = f"{task.get('title', '')}\n{task.get('description') or ''}"
    files = set(PATH.findall(text))
    if HUB_FN.search(text):
        files.add("services/atlas/hub.py")
    for stem, path in _stems().items():
        if stem in PLAIN:
            if re.search(rf"\b{re.escape(stem)}\.py\b|engine/{re.escape(stem)}\b", text):
                files.add(path)
        elif re.search(rf"\b{re.escape(stem)}(?:\.py)?\b", text, re.I) or (
                "_" in stem and re.search(r"\b" + re.escape(stem).replace("_", "[ _-]?") + r"\b", text, re.I)):
            files.add(path)
    for path, words in ALIASES.items():
        if re.search(rf"\b(?:{words})\b", text, re.I):
            files.add(path)
    if mockup.is_visual(task.get("title", ""), task.get("description") or ""):
        files.update(SCREEN)
    if re.match(r"\s*RA-\d+", task.get("title", "")):
        files.add("STATE/")
    words = overlap._words(text)
    scored = sorted(((overlap._score(words, overlap._words(m["text"])), m["ref"]) for m in (mods if mods is not None else overlap.modules())),
                    reverse=True)
    if scored and scored[0][0] >= conf["module_match"] and (len(scored) < 2 or scored[0][0] - scored[1][0] >= conf["module_margin"]):
        files.add(scored[0][1])
    return sorted(files)


def _clash(a: list[str], b: list[str]) -> list[str]:
    """Files both change, ignoring the common ones; a folder ('STATE/') clashes with every file inside it."""
    out = set()
    for x in a:
        for y in b:
            if x in COMMON or y in COMMON or x.startswith("tests/") or y.startswith("tests/"):
                continue
            if x == y or (x.endswith("/") and y.startswith(x)) or (y.endswith("/") and x.startswith(y)):
                out.add(min(x, y, key=len))
    return sorted(out)


def _waits(con) -> dict[int, set[int]]:
    """For every open task, the open tasks it waits for, directly or through others."""
    deps = {r["id"]: {int(d) for d in re.findall(r"\d+", r["depends_on"] or "")}
            for r in con.execute(f"SELECT id, depends_on FROM tasks WHERE status IN ({','.join('?' * len(OPEN))})", OPEN)}
    out = {}
    for tid in deps:
        seen, todo = set(), list(deps[tid])
        while todo:
            d = todo.pop()
            if d in deps and d not in seen:
                seen.add(d)
                todo.extend(deps[d])
        out[tid] = seen
    return out


def _branch_files(branch: str) -> list[str] | None:
    """What a branch really changed against origin/main, when git knows it (None otherwise)."""
    if not branch:
        return None
    try:
        out = subprocess.run(["git", "diff", "--name-only", f"origin/main...{branch}"], cwd=ROOT, capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return [l for l in out.stdout.splitlines() if l.strip()] if out.returncode == 0 else None


def why_not(a: dict, b: dict, waits: dict[int, set[int]]) -> str:
    """Why two tasks cannot be built side by side ('' = they can)."""
    if a["id"] in waits.get(b["id"], set()):
        return f"#{b['id']} waits for #{a['id']}"
    if b["id"] in waits.get(a["id"], set()):
        return f"#{a['id']} waits for #{b['id']}"
    if not a["files"] or not b["files"]:
        return f"cannot tell which files #{(a if not a['files'] else b)['id']} changes"
    shared = _clash(a["files"], b["files"])
    return f"both change {', '.join(shared[:3])}" if shared else ""


def _task(con, tid: int, conf: dict, mods: list[dict], files: list[str] | None = None) -> dict:
    t = dict(con.execute("SELECT id, title, description, depends_on FROM tasks WHERE id = ?", (tid,)).fetchone())
    t["files"] = files if files is not None else predict(t, conf, mods)
    t.pop("description", None)
    return t


def in_flight(con, conf: dict | None = None, mods: list[dict] | None = None) -> list[dict]:
    """Tasks being built or waiting for review, with the files their branch changed (or the prediction)."""
    conf, mods = conf or cfg(), mods if mods is not None else overlap.modules()
    out, seen = [], set()
    for r in con.execute("""SELECT task_id, branch, status FROM dev_runs WHERE status IN ('running', 'pr_open')
                            AND task_id IS NOT NULL ORDER BY id DESC"""):
        if r["task_id"] in seen or not con.execute("SELECT 1 FROM tasks WHERE id = ?", (r["task_id"],)).fetchone():
            continue
        seen.add(r["task_id"])
        real = _branch_files(r["branch"]) if r["status"] == "pr_open" else None
        t = _task(con, r["task_id"], conf, mods, real)
        out.append({**t, "state": "building" if r["status"] == "running" else "PR open", "known": real is not None})
    return out


def ready(con) -> list[int]:
    """The tasks the builder could take now, in its order (Build next, his queue, proposals)."""
    from . import devloop, skilltree
    plan = skilltree.build_plan(con, devloop.cfg())
    out = []
    for i in plan.get("lane", []) + plan["mine"] + [p for p in plan["proposed"] if p["owner"] == "ai"]:
        if i["state"] != "ready" or i["id"] in out or mockup.hold(con, i["id"]):
            continue
        row = con.execute("SELECT * FROM tasks WHERE id = ?", (i["id"],)).fetchone()
        if row and row["owner"] == "ai" and not overlap.hold(con, row, check_now=False):
            out.append(i["id"])
    return out


def _sig(ids) -> str:
    return ",".join(str(i) for i in sorted(ids))


def _dismissed(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT tasks FROM parallel_batches WHERE status = 'dismissed'")}


def suggest(con, conf: dict | None = None, order: list[int] | None = None) -> dict:
    """The batch to propose now: {"tasks": [...], "left_out": [{"id", "why"}], "in_flight": [...]}."""
    conf = conf or cfg()
    mods = overlap.modules()
    waits = _waits(con)
    flying = in_flight(con, conf, mods)
    batch, left = [], []
    for tid in (order if order is not None else ready(con)):
        t = _task(con, tid, conf, mods)
        why = next((f"{w} (#{o['id']} is {o['state']})" for o in flying if (w := why_not(t, o, waits))), "")
        if why:
            left.append({"id": tid, "title": t["title"], "why": why})
            continue
        if len(batch) >= conf["batch_max"]:
            break
        why = next((w for o in batch if (w := why_not(t, o, waits))), "")
        if why:
            left.append({"id": tid, "title": t["title"], "why": why})
        else:
            batch.append(t)
    if len(batch) < 2 or _sig(t["id"] for t in batch) in _dismissed(con):
        batch = []
    return {"tasks": batch, "left_out": left[:8], "in_flight": flying}


def accepted(con) -> dict | None:
    """The batch Djeddi accepted that still has open tasks (None when there is none)."""
    for r in con.execute("SELECT * FROM parallel_batches WHERE status = 'accepted' ORDER BY id DESC"):
        ids = [int(i) for i in r["tasks"].split(",") if i]
        open_ = [i for i in ids if con.execute(f"SELECT 1 FROM tasks WHERE id = ? AND status IN ({','.join('?' * len(OPEN))})",
                                               (i, *OPEN)).fetchone()]
        if open_:  # one whose tasks are all finished is simply over; reading never writes
            return {**dict(r), "ids": ids, "open": open_, "files": json.loads(r["files"] or "{}")}
    return None


def may_run_alongside(con, task_id: int) -> bool:
    """For the builder's extra slots (#185): only a task in the accepted batch may be built next to others."""
    b = accepted(con)
    return bool(b and task_id in b["open"])


def accept(con, ids: list[int] | None = None) -> str:
    """Accept the batch proposed now (or the given tasks, when they still form a valid batch)."""
    s = suggest(con)
    want = [t["id"] for t in s["tasks"]]
    if ids and _sig(ids) != _sig(want):
        raise ValueError("The plan changed since this batch was shown; look at the new proposal")
    if len(want) < 2:
        raise ValueError("There is no batch of independent tasks to accept right now")
    for b in con.execute("SELECT id FROM parallel_batches WHERE status = 'accepted'").fetchall():
        brain.set_field(con, "parallel_batches", b["id"], "status", "replaced", "a new parallel batch was accepted")
    bid = db.insert(con, "parallel_batches", tasks=_sig(want), status="proposed", created=db.now(),
                    files=json.dumps({str(t["id"]): t["files"] for t in s["tasks"]}),
                    reason="; ".join(f"#{t['id']}: {', '.join(t['files'][:4])}" for t in s["tasks"])[:600])
    brain.set_field(con, "parallel_batches", bid, "status", "accepted", "Djeddi accepted the parallel batch")
    db.update(con, "parallel_batches", bid, decided=db.now())
    db.log(con, "me", "parallel_accept", f"batch #{bid}: " + ", ".join(f"#{i}" for i in want))
    con.commit()
    return (f"Batch #{bid} accepted: " + ", ".join(f"#{i}" for i in want)
            + " may be built side by side once the builder has more than one slot. Undo under Memory › What changed.")


def dismiss(con, ids: list[int]) -> str:
    """Do not offer this set again (it stays possible to accept the next, different proposal)."""
    ids = sorted({int(i) for i in ids})
    if len(ids) < 2:
        raise ValueError("Name the tasks of the batch to dismiss")
    bid = db.insert(con, "parallel_batches", tasks=_sig(ids), status="proposed", created=db.now(), files="{}", reason="")
    brain.set_field(con, "parallel_batches", bid, "status", "dismissed", "Djeddi dismissed the parallel batch")
    db.update(con, "parallel_batches", bid, decided=db.now())
    db.log(con, "me", "parallel_dismiss", f"batch {_sig(ids)}")
    con.commit()
    return "Dismissed: this set is not proposed again. Undo under Memory › What changed."


def view(con) -> dict:
    """What Forge › Build › Queue shows: the proposal now, and the accepted batch."""
    s = suggest(con)
    a = accepted(con)
    if a:
        a["titles"] = {i: (con.execute("SELECT title FROM tasks WHERE id = ?", (i,)).fetchone() or [""])[0] for i in a["ids"]}
    return {"proposal": s["tasks"], "left_out": s["left_out"], "in_flight": s["in_flight"], "accepted": a,
            "same": bool(a and s["tasks"] and _sig(t["id"] for t in s["tasks"]) == a["tasks"])}


def text(con) -> str:
    v = view(con)
    lines = []
    if v["accepted"]:
        lines.append(f"Accepted batch #{v['accepted']['id']}: " + ", ".join(f"#{i}" for i in v["accepted"]["open"]) + " (open)")
    if v["proposal"] and not v["same"]:
        lines.append("Can be built side by side (accept with `hq parallel accept`):")
        lines += [f"  #{t['id']} {t['title'][:70]}  ·  {', '.join(t['files'][:4])}" for t in v["proposal"]]
    elif not v["accepted"]:
        lines.append("No two ready AI tasks can be built side by side right now.")
    for x in v["left_out"][:5]:
        lines.append(f"  not with them: #{x['id']} {x['title'][:50]} ({x['why']})")
    for f in v["in_flight"]:
        lines.append(f"  in flight: #{f['id']} ({f['state']}) · {', '.join(f['files'][:4]) or 'files unknown'}")
    return "\n".join(lines)
