"""Gatekeeper: decides on agent actions so Djeddi doesn't have to answer terminal prompts.

Claude Code (running on the Pi) calls this as a PreToolUse hook before every tool call:
    .claude/settings.json → hooks.PreToolUse → "python3 -m engine gate"
The hook receives the tool call as JSON on stdin and answers allow / deny / ask.

Decision order (rules in config/constitution.toml → [gatekeeper]):
  1. deny rules   → refused immediately (never reaches Djeddi)
  2. allow rules  → approved immediately
  3. everything else → by default DEFERRED to Claude Code's own permission mode. In auto mode,
     Claude Code's classifier model reviews it, so routine actions never reach Djeddi's phone.
     With [gatekeeper] unknown = "phone", it instead becomes an approval row + phone push (ntfy) with Approve/Deny buttons;
     the buttons reply via a private ntfy reply topic (checked with a one-time nonce), and the
     hook waits up to `wait_seconds` for the decision from phone, dashboard or CLI.
     No answer in time → `on_timeout` (default: deny).

Conceptual decisions (direction, goals, money, anything external or irreversible) are not tool
calls: agents ask them explicitly with `hq ask "..." --options "A|B|C"`, which arrives on the phone
as one button per option. Every decision is logged. The gatekeeper never widens its own rules.
"""
import json
import os
import re
import secrets
import sys
import time
import urllib.request

from . import db, loop

MANUAL_ONLY = "outside_code"  # = sandbox.MANUAL_ONLY: approvals only Djeddi settles, by hand


def _rules() -> dict:
    return loop.load_constitution().get("gatekeeper", {})


def describe(tool: str, args: dict) -> str:
    if tool == "Bash":
        return args.get("command", "")
    for key in ("file_path", "path", "url", "pattern", "query"):
        if key in args:
            return str(args[key])
    return json.dumps(args)[:300]


def match(rules: list[dict], tool: str, target: str) -> dict | None:
    for r in rules:
        if re.fullmatch(r.get("tool", ".*"), tool) and re.search(r.get("pattern", ".*"), target):
            return r
    return None


def old_names(target: str) -> str:
    """The command spelled with the old names (RA-055), so deny rules written for `hq.sh` / `-m hq` still hold for
    `atlas.sh` / `-m engine`, and rules naming the Pi's old folder `/hq-system/` hold for `~/atlas` (RA-042).
    Only deny rules see this form: the new names never gain an allow rule this way."""
    target = re.sub(r"(?<![\w-])atlas\.sh\b", "hq.sh", target)
    target = re.sub(r"(/home/[^/\s]+|~|\$HOME)/atlas(?=/|\s|$)", r"\1/hq-system", target)
    return re.sub(r"-m(\s+)engine\b", r"-m\1hq", target)


def decide(tool: str, args: dict, cfg: dict | None = None) -> tuple[str, str]:
    """Pure policy check: returns ('allow'|'deny'|'ask', reason)."""
    cfg = cfg if cfg is not None else _rules()
    target = describe(tool, args)
    home = os.path.expanduser(cfg.get("workspace", "~"))
    if tool in ("Edit", "Write", "NotebookEdit") and not os.path.abspath(os.path.expanduser(target)).startswith(home):
        return "deny", f"writes outside {home}"
    if r := match(cfg.get("deny", []), tool, target) or match(cfg.get("deny", []), tool, old_names(target)):
        return "deny", r.get("why", "matches a deny rule")
    if r := match(cfg.get("allow", []), tool, target):
        return "allow", r.get("why", "matches an allow rule")
    return "ask", "not covered by rules"


def _ntfy(cfg: dict) -> tuple[str, str]:
    return cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/"), os.environ.get("HQ_NTFY_TOPIC") or cfg.get("ntfy_topic", "")


def notify(approval_id: int, nonce: str, title: str, body: str, cfg: dict, options: list[str] | None = None) -> bool:
    """Push to phone via ntfy with Approve / Deny buttons.

    The buttons publish the answer to a private reply topic (<topic>-reply) that the Pi polls,
    so they work from anywhere without exposing the Pi. Each answer carries a one-time nonce;
    replies without the right nonce are ignored.
    """
    server, topic = _ntfy(cfg)
    if not topic:
        return False
    reply = f"{server}/{topic}-reply"
    if options:  # a decision: one button per option (ntfy shows at most 3)
        actions = [{"action": "http", "label": opt[:40], "url": reply, "method": "POST",
                    "body": f"choose {approval_id} {nonce} {i}", "clear": True}
                   for i, opt in enumerate(options[:3])]
    else:
        actions = [{"action": "http", "label": label, "url": reply, "method": "POST",
                    "body": f"{op} {approval_id} {nonce}", "clear": True}
                   for label, op in (("Approve", "approved"), ("Deny", "denied"))]
    if cfg.get("dashboard_url") and len(actions) < 3:
        actions.append({"action": "view", "label": "Open", "url": cfg["dashboard_url"]})
    payload = {"topic": topic, "title": title, "message": body[:900], "priority": 4, "tags": ["lock"], "actions": actions}
    try:
        req = urllib.request.Request(server, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False


def poll_replies(con, cfg: dict, since: int) -> int:
    """Apply Approve/Deny taps from the phone. Returns how many approvals were decided."""
    server, topic = _ntfy(cfg)
    if not topic:
        return 0
    try:
        raw = urllib.request.urlopen(f"{server}/{topic}-reply/json?poll=1&since={since}", timeout=10).read().decode()
    except Exception:
        return 0
    n = 0
    for line in raw.splitlines():
        try:
            parts = json.loads(line).get("message", "").split()
            op, aid, nonce = parts[0], int(parts[1]), parts[2]
        except (ValueError, IndexError, json.JSONDecodeError, AttributeError):
            continue
        n += decide_with_nonce(con, aid, op, nonce, "phone", parts[3] if len(parts) > 3 else None)
    con.commit()
    return n


def manual_only(con, approval_id: int) -> bool:
    """An approval only Djeddi's own hand may approve: outside code (category, or a task carrying the mark). #75"""
    row = con.execute("SELECT a.category, t.description FROM approvals a LEFT JOIN tasks t ON t.id = a.task_id "
                      "WHERE a.id = ?", (approval_id,)).fetchone()
    from . import research
    return bool(row) and (row[0] == MANUAL_ONLY or research.OUTSIDE_MARK in (row[1] or ""))


def guard_manual(con, approval_id: int, status: str) -> None:
    """Refuse an agent (HQ_ACTOR=agent) approving outside code; denying stays possible."""
    from . import flow
    if status == "approved" and flow.actor() != "me" and manual_only(con, approval_id):
        raise ValueError(f"#{approval_id} is outside code: only Djeddi can approve it, by hand")


def decide_with_nonce(con, approval_id: int, op: str, nonce: str, via: str, choice: str | None = None) -> bool:
    """Apply an answer only if it carries the approval's one-time nonce and is still pending.

    op is 'approved' / 'denied', or 'choose' with `choice` = index of the option picked for a decision.
    Shared by the ntfy reply topic and the Telegram buttons. Does not commit.
    """
    if not nonce:
        return False
    answer = ""
    if op == "choose":
        row = con.execute("SELECT options FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        opts = (row[0] or "").split("|") if row and row[0] else []
        try:
            answer = opts[int(choice)]
        except (IndexError, ValueError, TypeError):
            return False
        op = "answered"
    elif op not in ("approved", "denied"):
        return False
    cur = con.execute("UPDATE approvals SET status = ?, answer = ?, decided = ? WHERE id = ? AND nonce = ? AND nonce != '' AND status = 'pending'",
                      (op, answer, db.now(), approval_id, nonce))
    if cur.rowcount:
        db.log(con, "me", f"approval_{op}", f"#{approval_id} via {via}{': ' + answer if answer else ''}")
    return bool(cur.rowcount)


def request_approval(con, request: str, reason: str, category: str = "action", cost: float = 0,
                     wait: int | None = None, options: list[str] | None = None, task_id: int | None = None) -> str:
    """Create an approval (or a decision, when options are given), push it to the phone, optionally
    wait for the answer. Returns 'approved' / 'denied' / 'pending', or for decisions the chosen option
    text ('timeout' if nobody answered)."""
    cfg = _rules()
    nonce = secrets.token_urlsafe(9)
    options = [o.strip() for o in (options or []) if o.strip()][:3]
    aid = db.insert(con, "approvals", request=request[:500], reason=reason, category="decision" if options else category,
                    cost_eur=cost, nonce=nonce, options="|".join(options), task_id=task_id)
    con.commit()
    started = int(time.time()) - 5
    title = f"Atlas needs your decision #{aid}" if options else f"Atlas needs approval #{aid}"
    notify(aid, nonce, title, f"{request}\n\nWhy: {reason}" if reason else request, cfg, options)
    wait = cfg.get("wait_seconds", 600) if wait is None else wait
    deadline = time.time() + wait
    while time.time() < deadline:
        poll_replies(con, cfg, started)
        status, answer = con.execute("SELECT status, answer FROM approvals WHERE id = ?", (aid,)).fetchone()
        if status != "pending":
            return answer if status == "answered" else status
        time.sleep(3)
    if wait and options:
        con.execute("UPDATE approvals SET status = 'expired', decided = ? WHERE id = ? AND status = 'pending'", (db.now(), aid))
        db.log(con, "hq", "decision_timeout", f"#{aid}")
        con.commit()
        return "timeout"
    if wait and manual_only(con, aid):  # outside code: no timeout ever settles it; it waits for his tap (#75)
        db.log(con, "hq", "approval_waiting", f"#{aid} outside code: only Djeddi approves it")
        con.commit()
        return "pending"
    if wait:
        status = "approved" if cfg.get("on_timeout") == "allow" else "denied"
        con.execute("UPDATE approvals SET status = ?, decided = ? WHERE id = ? AND status = 'pending'", (status, db.now(), aid))
        db.log(con, "hq", "approval_timeout", f"#{aid} → {status}")
        con.commit()
        return status
    return "pending"


def hook_main() -> None:
    """Entry point for Claude Code's PreToolUse hook. Reads JSON on stdin, prints a decision."""
    event = json.load(sys.stdin)
    tool, args = event.get("tool_name", ""), event.get("tool_input", {}) or {}
    con = db.connect(); db.init(con)
    decision, reason = decide(tool, args)
    if decision == "ask" and _rules().get("unknown", "defer") == "defer":
        db.log(con, "gatekeeper", "defer", f"{tool}: {describe(tool, args)[:150]} (left to Claude Code's permission mode)")
        con.commit()
        return  # no output: Claude Code's own mode decides (auto mode → classifier, not Djeddi)
    if decision == "ask":
        status = request_approval(con, f"{tool}: {describe(tool, args)}", f"Claude Code in {event.get('cwd', '?')}", "action")
        decision, reason = ("allow", "approved by Djeddi") if status == "approved" else ("deny", f"{status} by Djeddi or timeout")
    db.log(con, "gatekeeper", decision, f"{tool}: {describe(tool, args)[:150]} ({reason})")
    con.commit()
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": decision,
                                             "permissionDecisionReason": f"Atlas gatekeeper: {reason}"}}))
