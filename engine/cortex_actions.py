"""What Cortex may do by itself, and how his picks are applied (#186, with #160's quick choices).

Three levels of action, from the change it makes:
  routine   reversible plan changes he clearly asked for (create or edit a task, idea or goal, order, dependencies,
            the Build next lane, a memory, a thought): with Cortex autonomy "routine" (the default, System › Settings)
            they run at once through the change log and the answer shows "Done: … · Undo".
  decision  everything else Cortex may propose (dropping, marking done, direction, priorities between goals,
            unclear requests): a numbered option, the recommended one first; nothing runs until he picks it.
  approval  money, external steps, accounts, new systems, outside code, the constitution: not hub actions at all,
            so they stay approvals under the constitution, unchanged.

An option can bundle several steps ("add step 2 + link it to #185 + save the decision"): applied in order, all or
nothing, one Undo. He picks by tap, by number, or by typing ("all", "1 and 3", "yes", "the first"). Picking one
option keeps the others open. The options of the latest answer are kept per conversation (state
cortex_pending_<conv>), so a typed pick and a tap never apply the same option twice.
"""
import json
import re

from . import brain, db

LEVELS = ("routine", "ask")
KEY = "cortex_autonomy"
TEXT = {"routine": "Routine plan changes you ask for are made at once, with Undo; decisions are asked.",
        "ask": "Every change is offered as an option first; nothing runs until you pick it."}
MAX_STEPS = 5
_ROUTINE_STATUS = {"todo", "doing", "builder", "important", "normal"}
ROUTINE = {  # action -> is this body routine?
    "engine.build.edit_plan": lambda b: isinstance(b.get("edit"), dict) or isinstance(b.get("create"), dict),
    "engine.build.reorder_tasks": lambda b: True,
    "engine.build.build_next": lambda b: True,
    "engine.build.build_order": lambda b: True,
    "engine.memory.record_lesson": lambda b: True,
    "engine.brain.write_thought": lambda b: True,
    "engine.build.task_status": lambda b: str(b.get("status", "")) in _ROUTINE_STATUS,
}


def level(con) -> str:
    value = db.get_state(con, KEY, "routine")
    return value if value in LEVELS else "routine"


def set_level(con, value: str) -> str:
    if value not in LEVELS:
        raise ValueError(f"Cortex autonomy must be one of: {', '.join(LEVELS)}")
    db.set_state(con, KEY, value)
    db.log(con, "me", "cortex_autonomy", value)
    con.commit()
    return TEXT[value]


def steps(p: dict) -> list[dict]:
    """The steps of an option: its bundle, or its one action."""
    if isinstance(p.get("steps"), list) and p["steps"]:
        return [s for s in p["steps"] if isinstance(s, dict)]
    return [{"action": p.get("action", ""), "body": p.get("body") or {}}] if p.get("action") else []


def is_routine(p: dict) -> bool:
    s = steps(p)
    return bool(s) and all(st.get("action") in ROUTINE and ROUTINE[st["action"]](st.get("body") or {}) for st in s)


def clean_steps(raw, allowed) -> list[dict] | None:
    """A bundle from the model, checked: known, proposable actions with object bodies, at most MAX_STEPS."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_STEPS:
        return None
    out = []
    for s in raw:
        if not isinstance(s, dict) or str(s.get("action") or "") not in allowed:
            return None
        body = s.get("body") if isinstance(s.get("body"), dict) else {}
        if len(json.dumps(body)) > 4000:
            return None
        out.append({"action": str(s["action"]), "body": body})
    return out


def _last_change(con) -> int:
    return con.execute("SELECT COALESCE(MAX(id), 0) FROM brain_changes").fetchone()[0]


def apply(con, p: dict, actions: dict) -> dict:
    """Run an option's steps in order, all or nothing: a failing step undoes the steps before it.
    Returns {"message", "changes"}; the changes are what one Undo reverses."""
    todo = steps(p)
    if not todo:
        raise ValueError("Nothing to do")
    before, said = _last_change(con), []
    for n, st in enumerate(todo, 1):
        fn = actions.get(st["action"])
        try:
            if fn is None:
                raise ValueError(f"{st['action']} is not available")
            msg = fn(con, dict(st.get("body") or {}))
            said.append(msg if isinstance(msg, str) else "done")
        except (ValueError, KeyError, TypeError, PermissionError) as e:
            for cid in [r[0] for r in con.execute("SELECT id FROM brain_changes WHERE id > ? AND undone = 0 ORDER BY id DESC", (before,))]:
                brain.undo(con, cid)
            raise ValueError(f"step {n} of {len(todo)} failed ({e}); nothing was changed" if len(todo) > 1 else str(e))
    con.commit()
    changes = [r[0] for r in con.execute("SELECT id FROM brain_changes WHERE id > ? AND undone = 0 ORDER BY id", (before,))]
    return {"message": " ".join(m.rstrip(".") + "." for m in said if m), "changes": changes}


# ---------------- the options of the latest answer, and typed picks ----------------

def remember(con, conv: int, choices: list, proposals: list) -> None:
    db.set_state(con, f"cortex_pending_{conv}", json.dumps(
        {"choices": list(choices or []), "proposals": [dict(p, state="open") for p in proposals or []]}))


def pending(con, conv: int) -> dict:
    try:
        v = json.loads(db.get_state(con, f"cortex_pending_{conv}", "") or "{}")
    except ValueError:
        v = {}
    return {"choices": v.get("choices") or [], "proposals": v.get("proposals") or []}


ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1,
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
ALL = {"all", "everything", "both", "all of them", "all three", "all options", "do all", "do them all", "do both"}
YES = {"yes", "y", "ok", "okay", "sure", "go", "do it", "go ahead", "yes please", "please do", "the recommended one", "recommended"}
NO = {"no", "none", "neither", "no thanks", "not now", "skip"}
FILLER = {"and", "the", "option", "options", "number", "numbers", "please", "do", "pick", "take", "plus", "&", "+", "en", "ja"}


def parse(text: str, n: int) -> list[int] | None:
    """A typed pick among n numbered options (1-based): [1, 3], [] for no, None when the text is something else.
    'yes' means the recommended first option."""
    t = " ".join(re.sub(r"[.!,;:]+", " ", (text or "").lower()).split())
    if not t or not n or len(t) > 60:
        return None
    if t in ALL:
        return list(range(1, n + 1))
    if t in YES:
        return [1]
    if t in NO:
        return []
    picked = []
    for w in t.split():
        if w in FILLER:
            continue
        v = int(w) if w.isdigit() else ORDINALS.get(w)
        if v is None:
            return None  # a word that is not a pick: this is an ordinary message
        v = n if v == -1 else v
        if not 1 <= v <= n:
            return None
        if v not in picked:
            picked.append(v)
    return picked or None


def typed(con, conv: int, text: str, actions: dict) -> dict | None:
    """His message picks from the latest options: apply the picked proposals (or decline them), or turn a picked
    choice into his answer. Returns None when the message is not a pick."""
    p = pending(con, conv)
    choices, props = p["choices"], p["proposals"]
    said = " ".join(re.sub(r"[.!,;:]+", " ", (text or "").lower()).split())
    if said in YES:  # yes = the recommended (first) open option; to a question it is an ordinary answer
        first = next((i for i, q in enumerate(props) if q["state"] == "open"), None)
        if choices or first is None:
            return None
        nums = [len(choices) + first + 1]
    else:
        nums = parse(text, len(choices) + len(props))
    if nums is None:
        return None
    if len(nums) == 1 and nums[0] <= len(choices):
        return {"answer": choices[nums[0] - 1]}  # a quick-choice answer (#160): sent to Cortex as his words
    if not props:
        return None
    lines, changes = [], []
    if not nums:
        for q in props:
            q["state"] = "declined" if q["state"] == "open" else q["state"]
        lines.append("Nothing done.")
    for i in nums:
        k = i - len(choices) - 1
        if not 0 <= k < len(props):
            continue
        q = props[k]
        if q["state"] != "open":
            lines.append(f"{i}. {q['label']}: already {q['state']}.")
            continue
        try:
            r = apply(con, q, actions)
            q["state"] = "done"
            lines.append(f"Done: {i}. {q['label']}. {r['message']}".strip())
            changes += r["changes"]
        except ValueError as e:
            lines.append(f"Not done: {i}. {q['label']} ({e}).")
    db.set_state(con, f"cortex_pending_{conv}", json.dumps(p))
    con.commit()
    return {"reply": "\n".join(lines) or "Nothing to do.", "changes": changes,
            "proposals": [q for q in props if q["state"] == "open"]}


def mark(con, conv: int, proposal: dict) -> None:
    """A tapped option is done: the same option typed later is not applied again."""
    p = pending(con, conv)
    for q in p["proposals"]:
        if q["state"] == "open" and q.get("label") == proposal.get("label") and steps(q) == steps(proposal):
            q["state"] = "done"
            db.set_state(con, f"cortex_pending_{conv}", json.dumps(p))
            return


def direct(con, entries: list, actions: dict, allowed) -> tuple[list[str], list[dict]]:
    """Cortex's do_now entries: routine ones run at once (autonomy 'routine'); the rest become options, first in line.
    Returns (done lines, options)."""
    done, options = [], []
    for e in entries:
        label = " ".join(str(e.get("label") or "").split())[:120]
        st = clean_steps(e.get("steps"), allowed) if e.get("steps") else clean_steps([{"action": e.get("action"), "body": e.get("body")}], allowed)
        if not label or not st:
            continue
        opt = {"label": label, "why": " ".join(str(e.get("why") or "").split())[:240], "action": st[0]["action"],
               "body": st[0]["body"], "view": "", "highlight": ""} | ({"steps": st} if len(st) > 1 else {})
        if level(con) == "routine" and is_routine(opt):
            try:
                done.append(f"{label}: {apply(con, opt, actions)['message']}")
                continue
            except ValueError as err:
                opt["why"] = f"Could not do it at once ({str(err)[:120]}); try it yourself."
        options.append(opt)
    return done, options
