"""Unfinished work after a usage limit: never lost, never marked done, always one paste away from resuming.

When Claude Code (or Codex) stops because a usage limit is reached, the agent itself cannot save anything any
more. So Atlas recovers it from the outside, without calling a model:
  * Claude Code and Codex already write every session to disk (~/.claude/projects/*/*.jsonl,
    ~/.codex/sessions/**/rollout-*.jsonl). `scan()` reads the ones that changed recently (cheap: only files whose
    size or time changed are parsed) and finds sessions whose last word was a usage-limit error.
  * Each one becomes ONE open item on Today ("Stopped by a usage limit"): the objective (the requests before it
    stopped), what was already done (files changed, commits), the last thing the agent said, when the limit
    resets, and a resume command (`claude -r <session>` / `codex resume <session>`) that reopens the same session
    with its full context. A second copy holds the context as a prompt for a fresh session, if resuming fails.
  * The same session is never listed twice. Once the session continues after the limit (it was resumed), the item
    is resolved by itself; if it hits a limit again, the same item reopens.
  * Atlas's own dev loop reports an interrupted run here too (`record_devloop`); it also keeps its checkpoint, so the
    next run continues the task, and the item resolves when that task's pull request is opened.
Limits of this approach: detection relies on the error text the CLI writes (matched broadly, see LIMIT);
"remaining steps" are not inferred by a model, so the item shows the agent's own last words instead.
"""
import json
import re
import shlex
from datetime import datetime, timedelta
from pathlib import Path

from . import db

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude" / "projects"
CODEX_DIR = HOME / ".codex" / "sessions"
WINDOW_HOURS = 72          # sessions changed in this window are checked
LIMIT = re.compile(r"usage[ _]limit|limit reached|hit your (?:\w+ )?limit|rate[ _-]?limit|credit balance is too low|"
                   r"out of (?:extra )?usage|limit (?:will )?resets?", re.I)
RESUME_PROMPT = ("You were stopped by a usage limit before finishing. Continue the unfinished work from where you "
                 "stopped. First say in one line what is done and what is left.")
SKIP_USER = ("<command-", "<local-command", "Caveat:", "[Request interrupted", "<environment_context", "<user_instructions",
             "# AGENTS.md", "<permissions", "<turn_aborted")


def is_limit(*texts) -> bool:
    return any(t and LIMIT.search(str(t)) for t in texts)


def resets(text: str) -> str:
    """When the limit lifts, if the message says so ('resets 7pm', 'try again at 7:36 PM', 'limit reached|1760000000')."""
    m = re.search(r"\|(\d{10})\b", text or "")
    if m:
        return datetime.fromtimestamp(int(m.group(1))).strftime("%a %H:%M")
    m = re.search(r"(?:resets?|try again)(?: at)?\s+([0-9]{1,2}(?::[0-9]{2})?\s?(?:[ap]\.?m\.?)?)", text or "", re.I)
    return m.group(1).strip().rstrip(".") if m else ""


def _clean(text: str) -> str:
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text or "", flags=re.S)
    return " ".join(text.split())


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _substantive(t: str) -> bool:
    return len(t) >= 12 and not t.startswith(SKIP_USER)


def _commit(cmd: str) -> str:
    m = re.search(r"git commit[^\n]*?-m\s+([\"'])(.+?)(?:\1|\n)", cmd or "", re.S)
    return m.group(2).splitlines()[0][:120] if m else ""


def parse_claude(path: Path) -> dict | None:
    """One Claude Code session: requests, work done, and whether it stopped at a usage limit."""
    out = {"tool": "claude", "session_id": path.stem, "cwd": "", "requests": [], "said": [], "files": [], "commits": [],
           "limit_at": "", "limit_text": "", "resumed_at": "", "last_at": ""}
    limit_i, i = -1, 0
    with open(path, errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            i += 1
            out["cwd"] = o.get("cwd") or out["cwd"]
            ts = o.get("timestamp") or ""
            out["last_at"] = ts or out["last_at"]
            msg = o.get("message") or {}
            if o.get("type") == "user" and not o.get("isMeta"):
                for b in _blocks(msg.get("content")):
                    if b.get("type") == "text":
                        t = _clean(b.get("text"))
                        if _substantive(t):
                            out["requests"].append((ts, t[:700], i))
            elif o.get("type") == "assistant":
                texts = [_clean(b.get("text")) for b in _blocks(msg.get("content")) if b.get("type") == "text"]
                if o.get("isApiErrorMessage"):
                    if o.get("apiErrorStatus") == 429 or is_limit(o.get("error"), o.get("apiErrorCode"), *texts):
                        limit_i, out["limit_at"], out["limit_text"], out["resumed_at"] = i, ts, " ".join(texts)[:300], ""
                    continue
                if limit_i >= 0 and not out["resumed_at"]:
                    out["resumed_at"] = ts  # the session went on after the limit
                for t in texts:
                    if t:
                        out["said"].append((ts, t[:700], i))
                for b in _blocks(msg.get("content")):
                    if b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        if b.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit") and inp.get("file_path"):
                            out["files"].append((inp["file_path"], i))
                        elif b.get("name") == "Bash" and _commit(inp.get("command", "")):
                            out["commits"].append((_commit(inp["command"]), i))
    return out if limit_i >= 0 else None


def parse_codex(path: Path) -> dict | None:
    """One Codex session (rollout file), same shape as parse_claude."""
    out = {"tool": "codex", "session_id": "", "cwd": "", "requests": [], "said": [], "files": [], "commits": [],
           "limit_at": "", "limit_text": "", "resumed_at": "", "last_at": ""}
    limit_i, i = -1, 0
    with open(path, errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            i += 1
            p, ts = o.get("payload") or {}, o.get("timestamp") or ""
            out["last_at"] = ts or out["last_at"]
            if o.get("type") == "session_meta":
                out["session_id"], out["cwd"] = p.get("id") or p.get("session_id") or "", p.get("cwd") or ""
                continue
            err = p.get("error") if isinstance(p.get("error"), dict) else {}
            if err and is_limit(err.get("codex_error_info"), err.get("message")):
                limit_i, out["limit_at"], out["limit_text"], out["resumed_at"] = i, ts, str(err.get("message") or "")[:300], ""
                continue
            if p.get("type") == "message":
                texts = [_clean(b.get("text")) for b in _blocks(p.get("content")) if b.get("type") in ("input_text", "output_text", "text")]
                if p.get("role") == "user":
                    out["requests"] += [(ts, t[:700], i) for t in texts if _substantive(t)]
                elif p.get("role") == "assistant":
                    if limit_i >= 0 and not out["resumed_at"] and any(texts):
                        out["resumed_at"] = ts
                    out["said"] += [(ts, t[:700], i) for t in texts if t]
            elif p.get("type") in ("function_call", "custom_tool_call"):
                raw = str(p.get("input") or p.get("arguments") or "")
                out["files"] += [(f, i) for f in re.findall(r"\*\*\* (?:Update|Add) File: (\S+)", raw)]
                if _commit(raw.replace('\\"', '"')):
                    out["commits"].append((_commit(raw.replace('\\"', '"')), i))
    return out if limit_i >= 0 and out["session_id"] else None


def _summary(s: dict) -> dict:
    """What Today shows: built from the session as it was when the limit stopped it."""
    stop = max([n for *_, n in s["requests"] + s["said"]] or [0])
    before = lambda rows: [r for r in rows if r[-1] <= stop]
    requests = [t for _, t, _ in before(s["requests"])]
    files = list(dict.fromkeys(f for f, _ in before(s["files"])))
    commits = list(dict.fromkeys(c for c, _ in before(s["commits"])))
    said = [t for _, t, _ in before(s["said"])]
    cwd = s["cwd"] or str(HOME)
    project = Path(cwd).name or "home"
    for f in files:  # the repository the work happened in says more than the folder Claude was started in
        parts = Path(f).parts
        if len(parts) > 3 and str(HOME) in f:
            project = parts[3]
            break
    return {"objective": requests[-1] if requests else "", "requests": requests[-3:], "files": files[-12:],
            "commits": commits[-6:], "last_said": said[-1] if said else "", "cwd": cwd, "project": project}


def _recent_files(window_hours: int) -> list[tuple[str, Path]]:
    since = (datetime.now() - timedelta(hours=window_hours)).timestamp()
    found = []
    if CLAUDE_DIR.exists():
        for d in CLAUDE_DIR.iterdir():
            if d.is_dir() and "--devloop-" not in d.name:  # dev loop runs are reported by the dev loop itself
                found += [("claude", p) for p in d.glob("*.jsonl") if p.stat().st_mtime >= since]
    if CODEX_DIR.exists():
        found += [("codex", p) for p in CODEX_DIR.rglob("rollout-*.jsonl") if p.stat().st_mtime >= since]
    return found


def scan(con, window_hours: int = WINDOW_HOURS) -> dict:
    """Find sessions stopped by a usage limit; open, refresh, reopen or resolve their Today items. No model calls."""
    seen = {r["path"]: (r["size"], r["mtime"]) for r in con.execute("SELECT * FROM scan_files")}
    counts = {"checked": 0, "opened": 0, "resolved": 0}
    for tool, path in _recent_files(window_hours):
        st = path.stat()
        if seen.get(str(path)) == (st.st_size, int(st.st_mtime)):
            continue
        counts["checked"] += 1
        try:
            s = (parse_claude if tool == "claude" else parse_codex)(path)
        except OSError:
            s = None
        con.execute("INSERT INTO scan_files (path, size, mtime) VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime",
                    (str(path), st.st_size, int(st.st_mtime)))
        if s:
            r = _apply(con, s)
            if r:
                counts[r] += 1
    con.commit()
    if counts["opened"] or counts["resolved"]:
        db.log(con, "hq", "unfinished_scan", f"{counts['opened']} stopped by a usage limit, {counts['resolved']} resumed")
        con.commit()
    return counts


def _apply(con, s: dict) -> str:
    row = con.execute("SELECT * FROM interruptions WHERE tool = ? AND session_id = ?", (s["tool"], s["session_id"])).fetchone()
    if s["resumed_at"]:  # it went on after the limit: resolved by resuming
        if row and row["status"] == "open":
            con.execute("UPDATE interruptions SET status = 'resolved', resolved_at = ?, resolution = ? WHERE id = ?",
                        (db.now(), f"resumed in the same session at {s['resumed_at'][:16].replace('T', ' ')}", row["id"]))
            return "resolved"
        return ""
    if row and row["stopped_at"] == s["limit_at"] and row["status"] != "open":
        return ""  # already handled (resolved or dismissed by hand) for this very stop
    info = _summary(s)
    fields = (s["limit_at"], s["limit_text"], resets(s["limit_text"]), info["objective"], json.dumps(info["requests"]),
              json.dumps(info["files"]), json.dumps(info["commits"]), info["last_said"], info["cwd"], info["project"])
    if row:
        con.execute("""UPDATE interruptions SET stopped_at=?, reason=?, resets=?, objective=?, requests=?, files=?, commits=?,
                       last_said=?, cwd=?, project=?, status='open', resolved_at=NULL, resolution='' WHERE id = ?""", fields + (row["id"],))
        return "opened" if row["status"] != "open" else ""
    con.execute("""INSERT INTO interruptions (stopped_at, reason, resets, objective, requests, files, commits, last_said, cwd,
                   project, tool, session_id, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", fields + (s["tool"], s["session_id"], db.now()))
    return "opened"


def record_devloop(con, run_id: int, task: dict, summary: str, done: str = "") -> int:
    """Atlas's dev loop hit a usage limit: one item per run; the checkpoint lets the next run continue the task."""
    sid = f"run-{run_id}"
    row = con.execute("SELECT id FROM interruptions WHERE tool = 'devloop' AND session_id = ?", (sid,)).fetchone()
    if row:
        return row["id"]
    cid = con.execute("""INSERT INTO interruptions (tool, session_id, task_id, stopped_at, reason, resets, objective, requests,
                         files, commits, last_said, cwd, project, created) VALUES ('devloop',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (sid, task["id"], db.now(), summary[:300], resets(summary), f"#{task['id']} {task['title']}", "[]", "[]", "[]",
                       done[:700], str(db.ROOT), "hq-system", db.now())).lastrowid
    db.log(con, "hq", "devloop_usage_limit", f"task #{task['id']} (run {run_id})")
    con.commit()
    return cid


def resolve_task(con, task_id: int, how: str = "finished in a later run") -> None:
    con.execute("UPDATE interruptions SET status = 'resolved', resolved_at = ?, resolution = ? WHERE tool = 'devloop' AND task_id = ? AND status = 'open'",
                (db.now(), how, task_id))


def resume_command(r: dict) -> str:
    cwd = shlex.quote(r["cwd"]) if r["cwd"] else "~"
    if r["tool"] == "claude":
        return f"cd {cwd} && claude -r {r['session_id']} {shlex.quote(RESUME_PROMPT)}"
    if r["tool"] == "codex":
        return f"cd {cwd} && codex resume {r['session_id']}"
    return ""


def context_prompt(r: dict) -> str:
    """For a fresh session when resuming is not possible: everything Atlas knows about the unfinished work."""
    lines = [f"A usage limit stopped an agent ({r['tool']}) before it finished work in {r['cwd']} (project {r['project']}).",
             f"Objective (the last request): {r['objective']}" if r["objective"] else ""]
    if len(r["requests"]) > 1:
        lines.append("Earlier requests: " + " | ".join(r["requests"][:-1]))
    if r["files"]:
        lines.append("Files it changed: " + ", ".join(r["files"]))
    if r["commits"]:
        lines.append("Commits it made: " + "; ".join(r["commits"]))
    if r["last_said"]:
        lines.append(f"The last thing it said: {r['last_said']}")
    if r["tool"] in ("claude", "codex"):
        lines.append(f"Its session id is {r['session_id']} (resume it with {'claude -r' if r['tool'] == 'claude' else 'codex resume'} if it still exists).")
    lines.append("Check the current state (git status, git log, the files above), then finish the remaining work. "
                 "Say first what is done and what is left. Do not redo finished work.")
    return "\n".join(l for l in lines if l)


def items(con) -> list[dict]:
    out = []
    for row in con.execute("SELECT * FROM interruptions WHERE status = 'open' ORDER BY stopped_at DESC"):
        r = dict(row)
        for k in ("requests", "files", "commits"):
            r[k] = json.loads(r[k] or "[]")
        out.append(r | {"resume": resume_command(r), "context": context_prompt(r)})
    return out


def finish(con, iid: int, status: str = "resolved", how: str = "marked finished by Djeddi") -> str:
    if status not in ("resolved", "dismissed"):
        raise ValueError("status must be resolved or dismissed")
    n = con.execute("UPDATE interruptions SET status = ?, resolved_at = ?, resolution = ? WHERE id = ? AND status = 'open'",
                    (status, db.now(), how, iid)).rowcount
    if not n:
        raise ValueError(f"No open item #{iid}.")
    db.log(con, "me", f"unfinished_{status}", f"#{iid}")
    con.commit()
    return f"Item #{iid} {status}."


def text(con) -> str:
    rows = items(con)
    if not rows:
        return "Nothing was stopped by a usage limit."
    return "\n\n".join(f"#{r['id']} [{r['tool']}] {r['project']}: {r['objective'][:120]}\n  stopped {r['stopped_at'][:16]}"
                       + (f", resets {r['resets']}" if r["resets"] else "") + (f"\n  {r['resume']}" if r["resume"] else "") for r in rows)
