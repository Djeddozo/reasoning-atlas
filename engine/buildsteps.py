"""Plain steps for the builder's live log (#179).

A run's log (devloop.log_path) holds short technical lines: Claude Code's tool calls ("→ Bash: python3 -m unittest …"),
its words ("· …"), and the builder's own notes ("check: tests failed (66 s)"). Djeddi wants each step as one full,
plain sentence of what the builder is doing; the raw lines stay behind the console's Output button.

plain(line) turns one line into a step or None (lines that carry no step, such as raw stderr). steps(lines) groups
repeats ("Reading 3 files: …") and returns the steps plus the agent's latest words. Pure functions, no I/O.
"""
from __future__ import annotations

import re

_TIME = re.compile(r"^\d\d:\d\d ")


def _base(path: str) -> str:
    parts = [p for p in re.split(r"[\\/]", str(path)) if p]
    return parts[-1] if parts else str(path)


def _step(icon: str, text: str, **extra) -> dict:
    return {"icon": icon, "text": text, "n": 1} | extra


def _bash(c: str) -> dict:
    if re.search(r"unittest|pytest|npm (run )?test|\btest\b", c):
        return _step("🧪", "Running the tests to see whether everything still works.")
    if re.search(r"\btsc\b|build_web|vite build|npm run build", c):
        return _step("⚙", "Building the app and checking it for errors.")
    if re.search(r"git (commit|add)", c):
        return _step("💾", "Saving the work so far as a commit.")
    if re.search(r"git push|gh pr", c):
        return _step("⇪", "Sending the work to GitHub for review.")
    if re.search(r"git (diff|status|log|show)", c):
        return _step("🔎", "Checking what has changed so far.")
    if re.match(r"(ls|cat|head|tail|sed -n|find|grep|rg|wc)\b", c):
        return _step("🔎", "Looking through the code to find the right places.")
    if re.search(r"hq\.sh|atlas\.sh|python3? -m (engine|hq)\b", c):
        return _step("⌘", "Asking Atlas’s Engine about the plan.")
    return _step("⌘", "Running a command.")


def _tool(tool: str, what: str) -> dict:
    if tool == "Read":
        return _step("📖", f"Reading {_base(what)} to understand the code.", verb="Reading", file=_base(what))
    if tool in ("Edit", "MultiEdit"):
        return _step("✎", f"Changing {_base(what)}.", verb="Changing", file=_base(what))
    if tool == "Write":
        return _step("✎", f"Writing {_base(what)}.", verb="Writing", file=_base(what))
    if tool in ("Grep", "Glob"):
        return _step("🔎", "Searching the code" + (f" for “{what[:40]}”." if what else "."))
    if tool in ("WebSearch", "WebFetch"):
        target = re.sub(r"^https?://", "", what)[:50]
        return _step("🌐", f"Looking up {target} online." if target else "Looking something up online.")
    if tool.startswith("Todo"):
        return _step("☑", "Updating its to-do list for this task.")
    if tool in ("Task", "Agent"):
        return _step("⇉", "Handing part of the work to a helper.")
    if tool == "Bash":
        return _bash(what)
    return _step("•", f"Using the {tool} tool.")


_CHECK = {"ok": ("✓", "The check passed: the tests pass{s}."),
          "tests failed": ("⚠", "The check failed: some tests do not pass{s}."),
          "no changes": ("⚠", "The check found no changes to keep{s}."),
          "outside code": ("⚠", "The check found outside code, which needs your approval first{s}."),
          "failed": ("⚠", "The check failed{s}.")}


def plain(line: str) -> dict | None:
    """One log line → {'icon', 'text', 'n', ...} or None. 'say' marks the agent's own words, 'end' the last step."""
    l = str(line).strip()
    if not l or line.startswith("  "):  # raw output from a tool or stderr: only in the Output view
        return None
    l = _TIME.sub("", l)
    if l == "started":
        return _step("▶", "Claude Code has started and is reading the task.")
    if l.startswith("claimed by worker "):
        return _step("⇄", f"The worker {l[18:].strip()} took this task.")
    if l.startswith("model: "):
        model = l[7:].split(" ", 1)[0]
        return _step("◇", f"Chose the model {model} for this task.")
    if (m := re.match(r"■ finished after (\S+) turns( \(error\))?", l)):
        if m.group(2):
            return _step("⚠", "Claude Code stopped with an error.", end=True)
        return _step("✓", f"Claude Code finished after {m.group(1)} steps; the work is checked next.", end=True)
    if l.startswith("· "):
        return _step("💬", l[2:], say=True)
    if (m := re.match(r"→ ([\w.-]+): ?(.*)$", l)):
        return _tool(m.group(1), m.group(2))
    if l.startswith("checking the work"):
        return _step("🧪", "Checking the finished work by running the full test suite.")
    if (m := re.match(r"check: (.+?)(?: \((\d+) s\))?$", l)):
        icon, text = _CHECK.get(m.group(1), ("•", f"The check ended: {m.group(1)}{{s}}."))
        return _step(icon, text.format(s=f" (took {m.group(2)} s)" if m.group(2) else ""))
    if (m := re.match(r"self-repair: (.+?), starting", l)):
        return _step("↻", f"The first attempt did not pass ({m.group(1).replace('_', ' ')}), so it starts one repair run.")
    if (m := re.match(r"nearly at the (.+) limit", l)):
        return _step("⏳", f"Nearly at the {m.group(1)} limit, so it saves a checkpoint to continue from later.")
    if l.startswith("stopped: "):
        return _step("■", f"Stopped by {l[9:]}.", end=True)
    if l.startswith("pushing the branch"):
        return _step("⇪", "Sending the branch to GitHub and opening a pull request.")
    if l.startswith("PR ready"):
        return _step("✓", "The pull request is open and waits for your review.", end=True)
    return None


def steps(lines: list[str], last: int = 7) -> dict:
    """The plain steps of a log, repeats grouped, plus what the agent last said and the current step."""
    out: list[dict] = []
    said = ""
    for line in lines:
        s = plain(line)
        if not s:
            continue
        if s.get("say"):
            said = s["text"]
            continue
        prev = out[-1] if out else None
        verb = s.pop("verb", None)
        f = s.pop("file", None)
        if prev and verb and prev.get("verb") == verb:
            files = prev["files"]
            if f in files:
                prev["n"] += 1
            else:
                files.append(f)
            prev["text"] = (f"{verb} {len(files)} files: {', '.join(files[-3:])}." if len(files) > 1
                            else s["text"])
            continue
        if prev and prev["text"] == s["text"] and "files" not in prev:
            prev["n"] += 1
            continue
        out.append(s | ({"verb": verb, "files": [f]} if verb else {}))
    for s in out:
        s.pop("verb", None)
        s.pop("files", None)
    return {"steps": out[-last:], "total": len(out), "said": said,
            "now": out[-1]["text"] if out else ""}


def now(lines: list[str]) -> str:
    """The current step as one sentence ('' when there is none yet)."""
    for line in reversed(lines):
        s = plain(line)
        if s and not s.get("say"):
            return s["text"]
    return ""
