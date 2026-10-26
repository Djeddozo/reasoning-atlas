"""Run in your terminal: steps an agent could not (or may not) do itself, left for Djeddi to run later.

A Claude Code session or the dev loop that ends with "now run this in the shell" registers the step with
`hq cmd add`, so it is not lost when Djeddi is away. Today shows each one at the top as the highest priority,
in two copyable forms:
  shell   one line for the terminal (Termius), ending in a `# Atlas: …` comment that says what it is for
  claude  a prompt for Claude Code with the context, for when it is unclear what the step does: open `claude`
          first, paste it, and Claude runs it and reports or fixes what goes wrong
Commands are shown, never executed by Atlas. Text that looks like a secret is refused (secrets stay in
environment variables, never in the database).
"""
import re

from . import db

SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abp]-|AKIA[0-9A-Z]{16}|"
                    r"(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S{6,})", re.I)


def add(con, title: str, command: str, why: str = "", cwd: str = "", source: str = "claude-code") -> int:
    """Register a step for Djeddi's terminal. The same open command is not added twice (its context is refreshed)."""
    title, command, why, cwd = title.strip(), command.strip(), why.strip(), cwd.strip()
    if not title or not command:
        raise ValueError("A command needs a title and the command itself.")
    if "\n" in command:
        raise ValueError("Use one shell line (join steps with &&), so it pastes and runs as one command.")
    if SECRET.search(command) or SECRET.search(why):
        raise ValueError("This looks like it contains a secret. Keep secrets in environment variables and refer to them instead.")
    same = con.execute("SELECT id FROM shell_commands WHERE status = 'open' AND command = ? AND cwd = ?", (command, cwd)).fetchone()
    if same:
        con.execute("UPDATE shell_commands SET title = ?, why = ?, source = ? WHERE id = ?", (title, why, source, same["id"]))
        con.commit()
        return same["id"]
    cid = con.execute("INSERT INTO shell_commands (title, why, command, cwd, source, created) VALUES (?,?,?,?,?,?)",
                      (title, why, command, cwd, source, db.now())).lastrowid
    db.log(con, "me" if source == "me" else "agent", "command_added", f"#{cid} {title}")
    con.commit()
    return cid


def shell_line(c: dict) -> str:
    """One line for the terminal: cd to the folder, run, and say what it is for (a trailing comment is ignored by the shell)."""
    run = f"cd {c['cwd']} && {c['command']}" if c.get("cwd") else c["command"]
    note = " ".join(c["title"].replace("#", "").split())
    return f"{run}  # Atlas: {note}"


def claude_prompt(c: dict) -> str:
    """The same step as a prompt for Claude Code, with enough context to understand it without the old session."""
    where = f" in {c['cwd']}" if c.get("cwd") else ""
    return (f"Atlas left this step for me to run in the terminal: {c['title']}.\n"
            + (f"Context: {c['why']}\n" if c.get("why") else "")
            + f"Please run this command{where}, show me the result, and if it fails explain why and fix it:\n"
            + c["command"])


def items(con) -> list[dict]:
    """Open commands, oldest first (the first one left behind is usually the first one to run)."""
    out = []
    for r in con.execute("SELECT * FROM shell_commands WHERE status = 'open' ORDER BY created, id"):
        c = dict(r)
        out.append(c | {"shell": shell_line(c), "claude": claude_prompt(c)})
    return out


def finish(con, cid: int, status: str = "done", actor: str = "me") -> str:
    if status not in ("done", "dismissed"):
        raise ValueError("status must be done or dismissed")
    r = con.execute("SELECT title FROM shell_commands WHERE id = ? AND status = 'open'", (cid,)).fetchone()
    if not r:
        raise ValueError(f"No open command #{cid}.")
    con.execute("UPDATE shell_commands SET status = ?, finished = ? WHERE id = ?", (status, db.now(), cid))
    db.log(con, actor, f"command_{status}", f"#{cid} {r['title']}")
    con.commit()
    return f"Command #{cid} marked {status}."


def text(con) -> str:
    rows = items(con)
    if not rows:
        return "No commands waiting for your terminal."
    return "\n\n".join(f"#{c['id']} {c['title']}" + (f"\n  {c['why']}" if c["why"] else "") + f"\n  {c['shell']}" for c in rows)
