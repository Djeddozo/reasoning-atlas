"""Needs your input: the few things only Djeddi can settle, in one list, each answerable in place.

Three kinds, and nothing else (the rest of the plan keeps moving without him):
  D<id>  a decision Atlas asked (`hq ask --options`): pick an option or type a free answer
  A<id>  an approval (spend, install, send, a big build): approve or deny, with an optional note
  T<id>  a task of his own (owner 'me') that an open task of the agent waits for: the work path is paused
         until he does it or says how to go on

His own tasks that hold nothing up stay under "Recommended for you"; they are not prompted here.
A typed or spoken answer on a task goes through the brain dump with the task as context, so Claude can
close it (flow.confirm_done), add a note, or create follow-up work, all logged and undoable.
"""
from . import db, dump, priority

YES = {"yes", "y", "ja", "j", "ok", "approve", "approved", "akkoord"}
NO = {"no", "n", "nee", "deny", "denied", "nope"}


def _blocking(con) -> dict[int, list[dict]]:
    """Djeddi's open tasks → the open agent tasks that wait for them."""
    mine = {r["id"]: r for r in con.execute(
        "SELECT id, title FROM tasks WHERE owner = 'me' AND status IN ('todo', 'doing', 'blocked')")}
    held: dict[int, list[dict]] = {}
    for r in con.execute("""SELECT id, title, depends_on FROM tasks WHERE owner IN ('ai', 'both')
                            AND status IN ('todo', 'doing', 'blocked') AND depends_on != '' ORDER BY score DESC"""):
        for d in priority._deps(r):
            if d in mine:
                held.setdefault(d, []).append({"id": r["id"], "title": r["title"]})
    return held


def items(con) -> list[dict]:
    """Everything waiting for Djeddi, decisions first. Each item: key, kind, id, title, why, options, holds."""
    titles = {r["id"]: r["title"] for r in con.execute("SELECT id, title FROM tasks")}
    out = []
    for a in con.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY created, id"):
        options = [o for o in (a["options"] or "").split("|") if o]
        kind = "decision" if options else "approval"
        why = a["reason"] or ""
        if a["cost_eur"]:
            why = (why + " · " if why else "") + f"€{a['cost_eur']:g}"
        holds = [{"id": a["task_id"], "title": titles.get(a["task_id"], "")}] if a["task_id"] else []
        out.append({"key": f"{kind[0].upper()}{a['id']}", "kind": kind, "id": a["id"], "title": a["request"],
                    "why": why, "options": options, "holds": holds, "category": a["category"]})
    held = _blocking(con)
    if held:
        marks = ",".join("?" * len(held))
        for t in con.execute(f"SELECT id, title, description, due FROM tasks WHERE id IN ({marks}) ORDER BY score DESC",
                             list(held)):
            n = len(held[t["id"]])
            out.append({"key": f"T{t['id']}", "kind": "task", "id": t["id"], "title": t["title"],
                        "why": f"the agent waits for this: {n} task{'s' if n != 1 else ''} paused"
                               + (f" · due {t['due']}" if t["due"] else ""),
                        "options": [], "holds": held[t["id"]], "description": (t["description"] or "")[:300]})
    out.sort(key=lambda i: ("decision", "approval", "task").index(i["kind"]))
    return out


def parse_key(key: str) -> tuple[str, int]:
    k = key.strip().upper().lstrip("#")
    kinds = {"D": "decision", "A": "approval", "T": "task"}
    if len(k) < 2 or k[0] not in kinds or not k[1:].isdigit():
        raise ValueError(f"'{key}' is not an item; use the key shown in the list, e.g. D12, A3 or T5")
    return kinds[k[0]], int(k[1:])


def _pending(con, aid: int):
    row = con.execute("SELECT * FROM approvals WHERE id = ?", (aid,)).fetchone()
    if row is None:
        raise ValueError(f"#{aid} does not exist")
    if row["status"] != "pending":
        raise ValueError(f"#{aid} was already settled ({row['status']}{': ' + row['answer'] if row['answer'] else ''})")
    return row


def answer(con, kind: str, id_: int, text: str = "", verdict: str = "", source: str = "dashboard") -> str:
    """Settle one item. Decisions: text is an option (matched loosely) or a free answer. Approvals: verdict
    'approved'/'denied' (or yes/no words in text), text kept as a note. Tasks: text goes to the brain dump."""
    text = (text or "").strip()
    if kind == "decision":
        row = _pending(con, id_)
        if not text:
            raise ValueError("Pick an option or type your answer.")
        options = [o for o in (row["options"] or "").split("|") if o]
        chosen = next((o for o in options if o.lower() == text.lower()), text)
        con.execute("UPDATE approvals SET status = 'answered', answer = ?, decided = ? WHERE id = ? AND status = 'pending'",
                    (chosen[:500], db.now(), id_))
        db.insert(con, "memories", kind="decision", content=f"{row['request']} → {chosen}"[:1000],
                  tags="needs decision", source="me")
        db.log(con, "me", "approval_answered", f"#{id_} via {source}: {chosen[:120]}")
        con.commit()
        return f"Decided #{id_}: {chosen}" + ("" if chosen in options else " (your own answer)")
    if kind == "approval":
        row = _pending(con, id_)
        word = text.lower().split()[0].strip(".,!") if text else ""
        verdict = verdict or ("approved" if word in YES else "denied" if word in NO else "")
        if verdict not in ("approved", "denied"):
            raise ValueError("Approve or deny it; a note alone does not settle an approval.")
        from . import gatekeeper
        gatekeeper.guard_manual(con, id_, verdict)  # decision #75: outside code only by Djeddi's own hand
        note = text if word not in YES | NO else text.split(None, 1)[1] if " " in text else ""
        con.execute("UPDATE approvals SET status = ?, answer = ?, decided = ? WHERE id = ? AND status = 'pending'",
                    (verdict, note[:500], db.now(), id_))
        db.log(con, "me", f"approval_{verdict}", f"#{id_} via {source}{': ' + note[:120] if note else ''}")
        con.commit()
        return f"{verdict.capitalize()} #{id_}: {row['request'][:80]}" + (f" (note: {note})" if note else "")
    if kind == "task":
        t = con.execute("SELECT id, title, owner, status FROM tasks WHERE id = ?", (id_,)).fetchone()
        if t is None:
            raise ValueError(f"Task #{id_} does not exist")
        if t["status"] in ("done", "dropped"):
            raise ValueError(f"Task #{id_} is already {t['status']}")
        if not text:
            raise ValueError("Type or say what you did or how to go on.")
        db.log(con, "me", "needs_input", f"T{id_} via {source}: {text[:120]}")
        return dump.process(con, f"About my task #{id_} \"{t['title']}\": {text}", source=source)
    raise ValueError(f"unknown kind {kind}")


def answer_key(con, key: str, text: str = "", verdict: str = "", source: str = "cli") -> str:
    kind, id_ = parse_key(key)
    return answer(con, kind, id_, text, verdict, source)


def render(its: list[dict]) -> str:
    """Plain text for the CLI and Telegram."""
    if not its:
        return "Nothing needs your input. Atlas keeps working on the rest."
    lines = [f"Needs your input ({len(its)}):"]
    for i in its:
        lines.append(f"{i['key']} {i['title']}")
        if i["why"]:
            lines.append(f"   {i['why']}")
        if i["options"]:
            lines.append("   options: " + " | ".join(i["options"]) + " (or your own answer)")
        if i["holds"] and i["kind"] != "task":
            lines.append("   holds up: " + ", ".join(f"#{h['id']} {h['title']}" for h in i["holds"]))
        elif i["kind"] == "task":
            lines.append("   paused: " + ", ".join(f"#{h['id']} {h['title']}" for h in i["holds"]))
    return "\n".join(lines)
