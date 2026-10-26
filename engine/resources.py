"""Resources: what the owner has to work with (money, hardware, accounts, time, skills, contacts, services) (RA-049).

Atlas's Resources page lists them and lets him add, correct and retire them; the agent and the re-evaluation read them
as context. Every change goes through the brain's change log, so it is undoable; nothing is deleted, a resource that
is gone is retired. No secrets belong here: an account is named, never its password or number.
"""
import json

from . import brain, db

KINDS = ("money", "hardware", "account", "time", "skill", "contact", "service", "other")
FIELDS = {"kind", "name", "details", "value", "unit"}
SECRET_HINTS = ("password", "passwd", "pin ", "iban", "card number", "cvv", "api key", "token", "secret")


def listing(con, retired: bool = False) -> dict:
    rows = [dict(r) for r in con.execute("SELECT * FROM resources WHERE (status IS NULL OR status = 'active') OR ? ORDER BY kind, name",
                                         (1 if retired else 0,))]
    groups: dict[str, list] = {}
    connected = []
    for r in rows:
        if str(r.get("source") or "").startswith("detected:"):  # live: reported by the machine itself (engine/hardware.py)
            try:
                r["detected"] = json.loads(r.get("detected") or "{}")
            except ValueError:
                r["detected"] = {}
            connected.append(r)
            continue
        groups.setdefault(r["kind"] if r["kind"] in KINDS else "other", []).append(r)
    return {"kinds": list(KINDS), "groups": [{"kind": k, "items": groups[k]} for k in KINDS if k in groups],
            "connected": connected, "count": len(rows)}


def _clean(fields: dict) -> dict:
    out = {k: v for k, v in fields.items() if k in FIELDS}
    for k in ("name", "details", "unit"):
        if k in out:
            out[k] = " ".join(str(out[k] or "").split())[:{"name": 120, "details": 500, "unit": 20}[k]]
    if "kind" in out and out["kind"] not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    if "value" in out and out["value"] not in (None, ""):
        try:
            out["value"] = float(out["value"])
        except (TypeError, ValueError):
            raise ValueError("value must be a number") from None
    elif "value" in out:
        out["value"] = None
    text = f"{out.get('name', '')} {out.get('details', '')}".lower()
    if any(h in text for h in SECRET_HINTS):
        raise ValueError("Resources never hold passwords, card or account numbers, keys or tokens; name the account only.")
    return out


def add(con, fields: dict) -> int:
    f = _clean(fields)
    if not f.get("name"):
        raise ValueError("A resource needs a name")
    rid = db.insert(con, "resources", kind=f.get("kind", "other"), name=f["name"], details=f.get("details", ""),
                    value=f.get("value"), unit=f.get("unit", ""), status="active")
    brain.record(con, "create", "resources", rid, "added in Atlas › Resources")
    db.log(con, "me", "resource_add", f"#{rid} {f['name']}")
    con.commit()
    return rid


def edit(con, rid: int, fields: dict) -> list[int]:
    """Change fields; returns the change-log ids (for Undo)."""
    row = con.execute("SELECT source FROM resources WHERE id = ?", (rid,)).fetchone()
    if row is None:
        raise ValueError(f"resource #{rid} does not exist")
    if str(row[0] or "").startswith("detected:"):
        raise ValueError("This one is detected from the machine itself and updates by itself; it cannot be edited by hand.")
    f = _clean(fields)
    if "name" in f and not f["name"]:
        raise ValueError("A resource needs a name")
    before = con.execute("SELECT COALESCE(MAX(id), 0) FROM brain_changes").fetchone()[0]
    for k, v in f.items():
        brain.set_field(con, "resources", rid, k, v, "corrected in Atlas › Resources")
    db.log(con, "me", "resource_edit", f"#{rid} {sorted(f)}")
    con.commit()
    return [r[0] for r in con.execute("SELECT id FROM brain_changes WHERE id > ? AND table_name = 'resources'", (before,))]


def retire(con, rid: int, back: bool = False) -> int | None:
    before = con.execute("SELECT COALESCE(MAX(id), 0) FROM brain_changes").fetchone()[0]
    if con.execute("SELECT 1 FROM resources WHERE id = ?", (rid,)).fetchone() is None:
        raise ValueError(f"resource #{rid} does not exist")
    brain.set_field(con, "resources", rid, "status", "active" if back else "retired", "back in use" if back else "no longer available")
    db.log(con, "me", "resource_" + ("restore" if back else "retire"), f"#{rid}")
    con.commit()
    row = con.execute("SELECT id FROM brain_changes WHERE id > ? ORDER BY id DESC LIMIT 1", (before,)).fetchone()
    return row[0] if row else None
