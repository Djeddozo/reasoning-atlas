"""Delivery: every pull request on Atlas's repo, from 'ready to merge' to 'installed', kept in step with reality.

Atlas used to know only the PRs its own dev loop opened, and noticed merges once an hour; PRs from manual Claude
Code or Codex sessions never closed their tasks, and Today showed an update only after "Scan for updates".
`sync()` now reads all PRs with one `gh pr list` call (no model calls, no tokens) and moves each through:

  draft    opened as a draft: not ready yet
  review   open and ready: "PR ready — open GitHub to review and merge"
  install  merged on GitHub, but its merge commit is not in the code Atlas runs yet (installs within 15 minutes)
  live     its merge commit is in the running code (git ancestry: works for merge, squash and rebase merges)
  closed   closed without merging

Evidence only, never "the description sounds similar":
  run       the dev loop opened it for a task (dev_runs.pr_url); devloop.sync_merged closes that task as before
  closes    "closes/fixes/implements task #N" in the title or body: the task is marked done on merge,
            through the brain change log, so it can be undone
  mentions  "task #N" without such a word: a suggestion on Today (Done | Partly | Not related), never automatic

Uncertain overlap without any reference is left to the overlap check (engine/overlap.py), which now also sees
merged PRs as built features, and whose stale verdicts are redone after a merge.

Runs: every 10 minutes from cron (`hq delivery sync`), after every update install (local check only), and
from Scan for updates. Transitions are keyed by PR number and idempotent: a retry or a duplicate run changes
nothing, and when GitHub cannot be reached nothing is changed at all.
"""
import json
import re
import shutil
import subprocess

from . import brain, db

ROOT = db.ROOT
FIELDS = "number,url,title,headRefName,body,state,isDraft,headRefOid,mergeCommit,mergedAt"
LIMIT = 60          # newest PRs per call; older ones keep their last known stage
RECENT_HOURS = 24   # installed PRs stay on Today this long
STAGE_TEXT = {"draft": "Draft", "review": "PR ready — review and merge on GitHub",
              "install": "Merged — installs within 15 minutes", "live": "Installed", "closed": "Closed without merging"}

CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?|implement(?:s|ed)?|complete[sd]?|finish(?:es|ed)?)"
                    r"[:\s]+(?:hq\s+)?task\s*#?(\d+)", re.I)
MENTION = re.compile(r"\btask\s*#(\d+)", re.I)
DEV_BRANCH = re.compile(r"^hq-dev/(\d+)-")


def _git_ok(*args) -> bool:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=30).returncode == 0


def fetch_prs() -> list[dict] | None:
    """All recent PRs from GitHub, or None when gh is missing or GitHub cannot be reached."""
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "all", "--limit", str(LIMIT), "--json", FIELDS],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def links(con, pr: dict) -> tuple[list[int], str]:
    """Which tasks a PR delivers, and how sure that is: run > closes > mentions."""
    run = [r["task_id"] for r in con.execute("SELECT DISTINCT task_id FROM dev_runs WHERE pr_url = ?", (pr["url"],))]
    m = DEV_BRANCH.match(pr.get("headRefName") or "")
    if m:
        run.append(int(m.group(1)))
    if run:
        return sorted(set(run)), "run"
    text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
    closes = sorted({int(n) for n in CLOSES.findall(text)})
    if closes:
        return closes, "closes"
    return sorted({int(n) for n in MENTION.findall(text)}), "mentions" if MENTION.search(text) else ""


def _in_running_code(sha: str) -> bool:
    return bool(sha) and _git_ok("merge-base", "--is-ancestor", sha, "HEAD")


def stage_of(pr: dict) -> str:
    if pr["state"] == "OPEN":
        return "draft" if pr.get("isDraft") else "review"
    if pr["state"] == "CLOSED":
        return "closed"
    return "live" if _in_running_code((pr.get("mergeCommit") or {}).get("oid", "")) else "install"


def _existing(con, task_id: int):
    return con.execute("SELECT id, status, title FROM tasks WHERE id = ?", (task_id,)).fetchone()


def _on_merge(con, number: int, tasks: list[int], link: str, title: str) -> list[str]:
    """A PR was merged: close the tasks it explicitly closes, suggest the ones it only mentions."""
    notes = []
    for tid in tasks:
        t = _existing(con, tid)
        if not t or t["status"] in ("done", "dropped"):
            continue
        if link == "closes":
            brain.set_field(con, "tasks", tid, "status", "done", f"PR #{number} (merged) closes task #{tid}")
            db.log(con, "hq", "delivery_done", f"task #{tid} by PR #{number}")
            notes.append(f"task #{tid} done (PR #{number})")
        elif link == "mentions":
            con.execute("INSERT OR IGNORE INTO reconcile(task_id, pr_number, evidence, created) VALUES (?,?,?,?)",
                        (tid, number, f"PR #{number} (merged) “{title[:80]}” mentions task #{tid}", db.now()))
            notes.append(f"task #{tid} maybe done (PR #{number})")
    # new built work: overlap verdicts that are not settled and not waiting for an answer are redone at the next scan
    con.execute("UPDATE overlap_checks SET created = '' WHERE verdict != 'exists' AND COALESCE(settled, '') = ''")
    return notes


def sync(con, remote: bool = True) -> dict:
    """Bring the prs table in line with GitHub (remote) and with the running code (always). Returns what moved."""
    moved, notes, merged_now = [], [], False
    prs = fetch_prs() if remote else None
    if remote and prs is None:
        db.set_state(con, "delivery:last", json.dumps({"at": db.now(), "ok": False}))
        con.commit()
        return {"ok": False, "moved": [], "notes": ["GitHub not reachable: nothing changed"]}
    if prs and any(p["state"] == "MERGED" and not _git_ok("cat-file", "-e", f"{(p.get('mergeCommit') or {}).get('oid', '')}^{{commit}}")
                   for p in prs if (p.get("mergeCommit") or {}).get("oid")):
        subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=ROOT, capture_output=True, timeout=180)
    first_run = remote and db.get_state(con, "delivery:imported", "") != "1"
    for p in prs or []:
        old = con.execute("SELECT * FROM prs WHERE number = ?", (p["number"],)).fetchone()
        stage = stage_of(p)
        tasks, link = links(con, p)
        fields = {"url": p["url"], "title": p["title"], "branch": p.get("headRefName") or "", "body": (p.get("body") or "")[:2000],
                  "state": p["state"], "draft": int(bool(p.get("isDraft"))), "head_sha": p.get("headRefOid") or "",
                  "merge_sha": (p.get("mergeCommit") or {}).get("oid", ""), "merged_at": p.get("mergedAt") or "",
                  "task_ids": ",".join(map(str, tasks)), "link": link}
        if old is None:
            con.execute(f"INSERT INTO prs(number, {', '.join(fields)}, stage, first_seen, stage_at, installed_at) "
                        f"VALUES (?, {', '.join('?' * len(fields))}, ?, ?, ?, '')",
                        (p["number"], *fields.values(), stage, db.now(), db.now()))
            if first_run:
                continue  # the first sync imports history; only changes from now on act on tasks
            moved.append((p["number"], "", stage))
        else:
            con.execute(f"UPDATE prs SET {', '.join(f'{k} = ?' for k in fields)} WHERE number = ?", (*fields.values(), p["number"]))
            if old["stage"] != stage:
                moved.append((p["number"], old["stage"], stage))
                con.execute("UPDATE prs SET stage = ?, stage_at = ? WHERE number = ?", (stage, db.now(), p["number"]))
        was_merged = old is not None and old["state"] == "MERGED"
        if p["state"] == "MERGED" and not was_merged:
            merged_now = True
            notes += _on_merge(con, p["number"], tasks, link, p["title"])
    # local: merged PRs whose commit has reached the running code since the last look (after an update install)
    for r in con.execute("SELECT number, merge_sha FROM prs WHERE stage = 'install'").fetchall():
        if _in_running_code(r["merge_sha"]):
            con.execute("UPDATE prs SET stage = 'live', stage_at = ?, installed_at = ? WHERE number = ?", (db.now(), db.now(), r["number"]))
            moved.append((r["number"], "install", "live"))
    for n, a, b in moved:
        if b == "live":
            con.execute("UPDATE prs SET installed_at = COALESCE(NULLIF(installed_at, ''), ?) WHERE number = ?", (db.now(), n))
        db.log(con, "hq", "delivery", f"PR #{n}: {a or 'new'} → {b}")
    if remote:
        db.set_state(con, "delivery:last", json.dumps({"at": db.now(), "ok": True}))
        db.set_state(con, "delivery:imported", "1")
    if first_run and prs:  # merged PRs are new overlap candidates: recheck the open backlog against them once
        con.execute("UPDATE overlap_checks SET created = '' WHERE verdict != 'exists' AND COALESCE(settled, '') = ''")
    con.commit()
    if merged_now:  # dev loop PRs: close their tasks and recheck conflicts with the GitHub state just read
        from . import devloop
        devloop.sync_merged(con, live={p["url"]: {"state": p["state"], "mergeable": "UNKNOWN", "headRefOid": p.get("headRefOid")}
                                       for p in prs or []}, fetch=False)
    return {"ok": True, "moved": moved, "notes": notes}


def today(con) -> dict:
    """What Today shows: PRs to review, merged work waiting for install, what was installed recently, and
    'already done?' suggestions. Database only, plus a git check for merged PRs not yet seen as installed."""
    for r in con.execute("SELECT number, merge_sha FROM prs WHERE stage = 'install'").fetchall():
        if _in_running_code(r["merge_sha"]):
            con.execute("UPDATE prs SET stage = 'live', stage_at = ?, installed_at = ? WHERE number = ?", (db.now(), db.now(), r["number"]))
            con.commit()

    def rows(sql, *args):
        out = []
        for r in con.execute(sql, args):
            d = dict(r)
            d.pop("body", None)
            d["label"] = STAGE_TEXT.get(d["stage"], d["stage"])
            d["tasks"] = [int(x) for x in (d["task_ids"] or "").split(",") if x]
            out.append(d)
        return out
    since = db.now()[:10]  # simple and timezone-proof: today's and yesterday's installs
    return {
        "review": rows("SELECT * FROM prs WHERE stage = 'review' ORDER BY number DESC"),
        "install": rows("SELECT * FROM prs WHERE stage = 'install' ORDER BY number DESC"),
        "installed": rows("SELECT * FROM prs WHERE stage = 'live' AND installed_at != '' "
                          "AND installed_at >= datetime(?, '-1 day') ORDER BY installed_at DESC LIMIT 8", since),
        "suggestions": [dict(r) for r in con.execute(
            """SELECT c.id, c.task_id, c.pr_number, c.evidence, t.title, p.url FROM reconcile c
               JOIN tasks t ON t.id = c.task_id LEFT JOIN prs p ON p.number = c.pr_number
               WHERE c.status = 'open' AND t.status NOT IN ('done', 'dropped') ORDER BY c.id DESC""")],
        "last": json.loads(db.get_state(con, "delivery:last", "{}") or "{}"),
    }


def decide(con, suggestion_id: int, answer: str) -> str:
    """Djeddi's answer to an 'already done?' suggestion: done | partial | not_related. Done goes through the
    brain change log (undoable); partial keeps the task open and notes the PR on it."""
    s = con.execute("SELECT * FROM reconcile WHERE id = ?", (suggestion_id,)).fetchone()
    if s is None or answer not in ("done", "partial", "not_related"):
        raise ValueError("Unknown suggestion or answer.")
    if answer == "done":
        brain.set_field(con, "tasks", s["task_id"], "status", "done", f"confirmed delivered by PR #{s['pr_number']}")
    elif answer == "partial":
        t = con.execute("SELECT description FROM tasks WHERE id = ?", (s["task_id"],)).fetchone()
        note = f"Partly delivered by PR #{s['pr_number']}; build only what is still missing."
        if t and note not in (t["description"] or ""):
            brain.set_field(con, "tasks", s["task_id"], "description", ((t["description"] or "") + "\n\n" + note).strip(),
                            f"partly delivered by PR #{s['pr_number']}")
    con.execute("UPDATE reconcile SET status = ?, decided = ? WHERE id = ?", (answer, db.now(), suggestion_id))
    db.log(con, "me", "reconcile", f"task #{s['task_id']} vs PR #{s['pr_number']}: {answer}")
    con.commit()
    return {"done": f"Task #{s['task_id']} marked done (undo under Memory › What changed).",
            "partial": f"Task #{s['task_id']} stays open, noted as partly delivered.",
            "not_related": "Noted: not related."}[answer]


def built_features(con) -> list[dict]:
    """Merged PRs as built features for the overlap check (engine/overlap.py)."""
    return [{"kind": "feature", "ref": f"PR #{r['number']}", "title": r["title"],
             "text": f"{r['title']} {(r['body'] or '')[:600]}"}
            for r in con.execute("SELECT number, title, body FROM prs WHERE state = 'MERGED' ORDER BY number DESC LIMIT 80")]


def text(out: dict) -> str:
    if not out["ok"]:
        return "; ".join(out["notes"])
    lines = [f"PR #{n}: {a or 'new'} → {b}" for n, a, b in out["moved"]] + out["notes"]
    return "\n".join(lines) or "Nothing changed."
