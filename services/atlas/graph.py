"""The function graph behind the map (RA-033): programs, areas, functions and focuses.

Everything comes from the capability registry (modules/*.json) and STATE/focuses.json.
A focus selects functions by program, area (program.domain) or id; a function can be
in several focuses.
"""
import json
from pathlib import Path

from packages.contracts import modules

ROOT = Path(__file__).resolve().parents[2]


def _label(cid):
    return cid.rsplit(".", 1)[-1].replace("_", " ").capitalize()


def resolve_focus(focus, registry):
    members = set()
    for cid, cap in registry["capabilities"].items():
        if cap["module"] in focus.get("programs", []) or f"{cap['module']}.{cap['domain']}" in focus.get("domains", []):
            members.add(cid)
    for cid in focus.get("functions", []):
        if cid not in registry["capabilities"]:
            raise ValueError(f"Focus {focus['id']}: unknown function {cid}")
        members.add(cid)
    for key in focus.get("domains", []):
        prog, _, dom = key.partition(".")
        if prog not in registry["modules"] or dom not in {d["id"] for d in registry["modules"][prog]["domains"]}:
            raise ValueError(f"Focus {focus['id']}: unknown area {key}")
    for prog in focus.get("programs", []):
        if prog not in registry["modules"]:
            raise ValueError(f"Focus {focus['id']}: unknown program {prog}")
    return sorted(members)


def build(root=ROOT):
    root = Path(root)
    registry = modules.load_registry(root / "modules", root)
    focuses = json.loads((root / "STATE" / "focuses.json").read_text(encoding="utf-8"))["focuses"]
    ids = [f["id"] for f in focuses]
    if len(ids) != len(set(ids)) or sum(bool(f.get("current")) for f in focuses) > 1:
        raise ValueError("Focus ids must be unique and at most one focus is current")
    resolved = [{**{k: f[k] for k in ("id", "name", "summary")}, "current": bool(f.get("current")), "horizon": bool(f.get("horizon")),
                 "concepts": f.get("concepts"), "members": resolve_focus(f, registry)} for f in focuses]
    membership = {}
    for f in resolved:
        for cid in f["members"]:
            membership.setdefault(cid, []).append(f["id"])
    programs = [{"id": m["id"], "name": m["name"], "kind": m["kind"], "parent": m.get("parent"),
                 "level": m.get("integrationLevel"), "summary": m["summary"]} for m in registry["modules"].values()]
    domains = [{"id": f"{m['id']}.{d['id']}", "program": m["id"], "name": d["name"], "summary": d["summary"]}
               for m in registry["modules"].values() for d in m["domains"]]
    functions = [{"id": cid, "label": _label(cid), "summary": c["summary"], "program": c["module"],
                  "programName": c["moduleName"], "domain": f"{c['module']}.{c['domain']}", "status": c["status"],
                  "effect": c["effect"], "intent": c.get("intent"), "requires": c.get("requires", []),
                  "evidence": c.get("evidence"), "focuses": membership.get(cid, []), "use": c.get("use", []),
                  "atlasSection": c.get("atlasSection"), "reachable": c["callable"] or c["moduleKind"] == "tool", "note": c.get("note")}
                 for cid, c in sorted(registry["capabilities"].items())]
    return {"programs": programs, "domains": domains, "functions": functions, "focuses": resolved}
