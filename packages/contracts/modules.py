"""Module manifests and the capability registry (RA-020, docs/PLATFORM.md).

A manifest describes one program or tool: Atlas itself, a sub-program kept whole
(the Engine, formerly HQ; Finance), or an external tool the owner can switch to
(Claude desktop, Codex). Programs nest through `parent` (docs/MODEL.md): Atlas is
the root today and can become a sub-program of something bigger later.
Capabilities are the functions a module offers to the rest of the platform.
`resolve` decides, for an intent such as "web.research", whether Atlas can do
it here or should hand the work to another app.
"""
import json, re
from pathlib import Path

KINDS = {"platform", "app", "tool"}
LEVELS = ["L0", "L1", "L2", "L3", "L4"]
EFFECTS = {"read", "propose", "act"}
STATUSES = ["planned", "built", "tested", "live"]
APPROVALS = ["none", "confirm", "owner"]
MIN_APPROVAL = {"read": "none", "propose": "confirm", "act": "owner"}
USES = {"hq-dashboard", "telegram", "cli", "automatic", "atlas"}
SLUG = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
NAME = re.compile(r"^[a-z][a-z0-9_]{0,60}$")
INTENT = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$")


def _text(value, field, limit=600):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be text of 1–{limit} characters")


def _schema(value, field):
    if value is None:
        return
    if not isinstance(value, dict) or value.get("type") != "object":
        raise ValueError(f"{field} must be a JSON schema object with type 'object'")


def validate_manifest(manifest, root=None):
    """Validate one manifest on its own. Cross-module rules live in build_registry."""
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be an object")
    if manifest.get("schemaVersion") != 1:
        raise ValueError("Unsupported manifest schemaVersion")
    mid = manifest.get("id")
    if not isinstance(mid, str) or not SLUG.match(mid):
        raise ValueError("Module id must be a lowercase slug")
    _text(manifest.get("name"), "name", 80)
    _text(manifest.get("summary"), "summary")
    kind = manifest.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{mid}: unknown kind {kind!r}")
    level = manifest.get("integrationLevel")
    if kind == "tool":
        if level is not None:
            raise ValueError(f"{mid}: tools are outside the platform and have no integration level")
    elif level not in LEVELS:
        raise ValueError(f"{mid}: integrationLevel must be one of {LEVELS}")
    if not isinstance(manifest.get("callable", kind == "platform"), bool):
        raise ValueError(f"{mid}: callable must be true or false")
    parent = manifest.get("parent")
    if kind == "tool":
        if parent is not None:
            raise ValueError(f"{mid}: tools are outside the program tree and have no parent")
    elif parent is not None and (not isinstance(parent, str) or not SLUG.match(parent) or parent == mid):
        raise ValueError(f"{mid}: parent must be another program's id")
    domains = manifest.get("domains")
    if not isinstance(domains, list) or not domains:
        raise ValueError(f"{mid}: at least one domain is required")
    domain_ids = set()
    for domain in domains:
        did = domain.get("id") if isinstance(domain, dict) else None
        if not isinstance(did, str) or not SLUG.match(did):
            raise ValueError(f"{mid}: domain id must be a lowercase slug")
        if did in domain_ids:
            raise ValueError(f"{mid}: duplicate domain {did}")
        domain_ids.add(did)
        _text(domain.get("name"), f"{mid}.{did} name", 80)
        _text(domain.get("summary"), f"{mid}.{did} summary")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list):
        raise ValueError(f"{mid}: capabilities must be a list")
    seen = set()
    for cap in capabilities:
        if not isinstance(cap, dict):
            raise ValueError(f"{mid}: capability must be an object")
        cid = cap.get("id")
        parts = cid.split(".") if isinstance(cid, str) else []
        if len(parts) != 3 or parts[0] != mid or not NAME.match(parts[2]):
            raise ValueError(f"{mid}: capability id {cid!r} must be '{mid}.<domain>.<name>'")
        if parts[1] not in domain_ids or cap.get("domain") != parts[1]:
            raise ValueError(f"{cid}: domain must be one of this module's domains and match the id")
        if cid in seen:
            raise ValueError(f"{cid}: duplicate capability")
        seen.add(cid)
        _text(cap.get("summary"), f"{cid} summary")
        effect = cap.get("effect")
        if effect not in EFFECTS:
            raise ValueError(f"{cid}: unknown effect {effect!r}")
        status = cap.get("status")
        if status not in STATUSES:
            raise ValueError(f"{cid}: unknown status {status!r}")
        approval = cap.get("approval", MIN_APPROVAL[effect])
        if approval not in APPROVALS or APPROVALS.index(approval) < APPROVALS.index(MIN_APPROVAL[effect]):
            raise ValueError(f"{cid}: {effect} capabilities need at least '{MIN_APPROVAL[effect]}' approval")
        if status in ("tested", "live"):
            evidence = cap.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError(f"{cid}: {status} requires evidence")
            if root and evidence.startswith(("STATE/", "tests/")) and not (Path(root) / evidence.split("#")[0]).exists():
                raise ValueError(f"{cid}: evidence {evidence} does not exist")
        use = cap.get("use", [])
        if not isinstance(use, list) or not set(use) <= USES:
            raise ValueError(f"{cid}: use must list where it works today: {sorted(USES)}")
        if "viaHub" in cap and not isinstance(cap["viaHub"], bool):
            raise ValueError(f"{cid}: viaHub is true or false")
        if cap.get("atlasSection") is not None and not isinstance(cap.get("atlasSection"), str):
            raise ValueError(f"{cid}: atlasSection must be text")
        intent = cap.get("intent")
        if intent is not None and (not isinstance(intent, str) or not INTENT.match(intent)):
            raise ValueError(f"{cid}: intent must look like 'area.action'")
        requires = cap.get("requires", [])
        if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires) or cid in requires:
            raise ValueError(f"{cid}: requires must be a list of other capability ids")
        _schema(cap.get("inputs"), f"{cid} inputs")
        _schema(cap.get("outputs"), f"{cid} outputs")
    return manifest


def build_registry(manifests, root=None):
    registry = {"modules": {}, "capabilities": {}}
    for manifest in manifests:
        validate_manifest(manifest, root)
        if manifest["id"] in registry["modules"]:
            raise ValueError(f"Duplicate module {manifest['id']}")
        registry["modules"][manifest["id"]] = manifest
    for manifest in manifests:
        parent = manifest.get("parent")
        if parent is not None:
            if parent not in registry["modules"] or registry["modules"][parent]["kind"] == "tool":
                raise ValueError(f"{manifest['id']}: unknown parent program {parent!r}")
        seen, node = set(), manifest["id"]
        while node is not None:
            if node in seen:
                raise ValueError(f"Program nesting loops through {node}")
            seen.add(node)
            node = registry["modules"][node].get("parent")
    for manifest in manifests:
        for cap in manifest["capabilities"]:
            if cap["id"] in registry["capabilities"]:
                raise ValueError(f"Duplicate capability {cap['id']}")
            registry["capabilities"][cap["id"]] = {**cap, "module": manifest["id"], "moduleName": manifest["name"], "moduleKind": manifest["kind"],
                                                   "callable": manifest.get("callable", manifest["kind"] == "platform") or bool(cap.get("viaHub"))}
    for cid, cap in registry["capabilities"].items():
        for req in cap.get("requires", []):
            if req not in registry["capabilities"]:
                raise ValueError(f"{cid}: requires unknown capability {req}")
    state = {}

    def visit(cid):
        if state.get(cid) == 1:
            raise ValueError(f"Capability dependency cycle through {cid}")
        if state.get(cid) == 2:
            return
        state[cid] = 1
        for req in registry["capabilities"][cid].get("requires", []):
            visit(req)
        state[cid] = 2
    for cid in registry["capabilities"]:
        visit(cid)
    return registry


def lineage(registry, module_id):
    """The chain of programs from the root down to this one, e.g. ["atlas", "engine"]."""
    chain, node = [], module_id
    while node is not None:
        chain.append(node)
        node = registry["modules"][node].get("parent")
    return list(reversed(chain))


def children(registry, module_id):
    return sorted(m["id"] for m in registry["modules"].values() if m.get("parent") == module_id)


def check_placement(registry, sections):
    """Every platform and app function has exactly one home: a section from STATE/sections.json (ADR-025);
    and no two of them do the same thing (the same intent), so one function never lives in two places."""
    known, problems, intents = set(sections), [], {}
    for cid, cap in sorted(registry["capabilities"].items()):
        if cap["moduleKind"] == "tool":
            continue  # outside apps: no section in Atlas
        home = cap.get("atlasSection")
        if home not in known:
            problems.append(f"{cid}: section {home!r} is not one of Atlas's sections")
        if cap.get("intent"):
            intents.setdefault(cap["intent"], []).append(cid)
    problems += [f"intent {i!r} lives in more than one function: {', '.join(ids)}" for i, ids in sorted(intents.items()) if len(ids) > 1]
    if problems:
        raise ValueError("Placement: " + "; ".join(problems))
    return registry


def load_registry(folder, root=None):
    manifests = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("*.json"))]
    registry = build_registry(manifests, root)
    sections = Path(root) / "STATE" / "sections.json" if root else None
    if sections and sections.exists():
        check_placement(registry, json.loads(sections.read_text(encoding="utf-8"))["sections"])
    return registry


def resolve(registry, intent):
    """Decide where work with this intent can happen.

    Returns {"intent", "here": capability or None, "switchTo": [tool capabilities],
    "alsoAvailableIn": [...], "elsewhere": [live in a program Atlas cannot call yet],
    "planned": [not built yet]}. Platform and callable app modules are preferred over
    external tools; only live capabilities are usable.
    """
    if not isinstance(intent, str) or not INTENT.match(intent):
        raise ValueError("Unknown intent format")
    matches = [c for c in registry["capabilities"].values() if c.get("intent") == intent]
    live = [c for c in matches if c["status"] == "live"]
    inside = [c for c in live if c["moduleKind"] in ("platform", "app") and c["callable"]]
    elsewhere = sorted((c for c in live if c["moduleKind"] in ("platform", "app") and not c["callable"]), key=lambda c: c["id"])
    order = {"platform": 0, "app": 1}
    inside.sort(key=lambda c: (order[c["moduleKind"]], c["id"]))
    tools = sorted((c for c in live if c["moduleKind"] == "tool"), key=lambda c: c["id"])
    planned = sorted((c for c in matches if c["status"] != "live"), key=lambda c: c["id"])
    return {"intent": intent, "here": inside[0] if inside else None, "switchTo": [] if inside else tools,
            "alsoAvailableIn": tools if inside else [], "elsewhere": elsewhere, "planned": planned}
