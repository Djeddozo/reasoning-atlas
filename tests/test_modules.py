import copy, json, threading, unittest, urllib.error, urllib.request
from pathlib import Path
from packages.contracts.modules import (APPROVALS, EFFECTS, KINDS, LEVELS, STATUSES, build_registry,
                                        load_registry, resolve, validate_manifest)
from services.atlas import server

ROOT = Path(__file__).resolve().parents[1]


def module(**changes):
    base = {"schemaVersion": 1, "id": "demo", "name": "Demo", "kind": "app", "integrationLevel": "L2",
            "summary": "A demo module.",
            "domains": [{"id": "plans", "name": "Plans", "summary": "Plans."}],
            "capabilities": [{"id": "demo.plans.list", "domain": "plans", "summary": "List plans.",
                              "effect": "read", "status": "live", "intent": "plans.read", "evidence": "Demo run"}]}
    base.update(changes)
    return base


def tool(mid, status="live"):
    return {"schemaVersion": 1, "id": mid, "name": mid, "kind": "tool", "summary": "External tool.",
            "domains": [{"id": "workbench", "name": "Workbench", "summary": "Work."}],
            "capabilities": [{"id": f"{mid}.workbench.research", "domain": "workbench", "summary": "Research.",
                              "effect": "read", "status": status, "intent": "web.research", "evidence": "Observed"}]}


class Manifests(unittest.TestCase):
    def test_repository_manifests_are_valid(self):
        registry = load_registry(ROOT / "modules", ROOT)
        self.assertEqual(set(registry["modules"]), {"atlas", "engine", "finance", "claude-desktop", "codex"})
        engine = registry["modules"]["engine"]
        self.assertEqual([d["id"] for d in engine["domains"]],
                         ["brain", "build", "direction", "growth", "assets", "research", "memory", "attention", "system"])
        self.assertGreaterEqual(len(engine["capabilities"]), 80)
        self.assertFalse(engine["callable"])
        live = [c for c in engine["capabilities"] if c["status"] == "live"]
        self.assertTrue(all(c["evidence"].startswith("tests/test_") for c in live))
        self.assertTrue(all(c.get("atlasSection") and c.get("use") for c in engine["capabilities"]))

    def test_schema_document_matches_validator(self):
        schema = json.loads((ROOT / "contracts/module-manifest.schema.json").read_text(encoding="utf-8"))
        props = schema["properties"]
        cap = props["capabilities"]["items"]["properties"]
        self.assertEqual(set(props["kind"]["enum"]), KINDS)
        self.assertEqual(props["integrationLevel"]["enum"], LEVELS + [None])
        self.assertEqual(set(cap["effect"]["enum"]), EFFECTS)
        self.assertEqual(cap["status"]["enum"], STATUSES)
        self.assertEqual(cap["approval"]["enum"], APPROVALS)

    def test_rejections(self):
        cases = {
            "bad id": module(id="Demo App"),
            "unknown kind": module(kind="service"),
            "tool with level": {**tool("t"), "integrationLevel": "L1"},
            "missing level": {k: v for k, v in module().items() if k != "integrationLevel"},
            "duplicate domain": module(domains=[{"id": "plans", "name": "A", "summary": "A"}] * 2),
        }
        cap = module()["capabilities"][0]
        for label, change in {
            "wrong prefix": {"id": "other.plans.list"},
            "unknown domain": {"id": "demo.money.list", "domain": "money"},
            "domain mismatch": {"domain": "money"},
            "unknown effect": {"effect": "delete"},
            "unknown status": {"status": "done"},
            "live without evidence": {"evidence": ""},
            "act without owner approval": {"effect": "act", "approval": "confirm"},
            "propose without confirmation": {"effect": "propose", "approval": "none"},
            "bad intent": {"intent": "research"},
            "bad inputs": {"inputs": {"type": "string"}},
        }.items():
            cases[label] = module(capabilities=[{**cap, **change}])
        cases["duplicate capability"] = module(capabilities=[cap, cap])
        cases["unknown use"] = module(capabilities=[{**cap, "use": ["fax"]}])
        cases["callable not boolean"] = module(callable="yes")
        cases["missing evidence file"] = module(capabilities=[{**cap, "evidence": "STATE/evidence/RA-999.md"}])
        for label, manifest in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                validate_manifest(manifest, ROOT)

    def test_registry_rejects_duplicates_across_modules(self):
        with self.assertRaises(ValueError):
            build_registry([module(), copy.deepcopy(module())])


class Programs(unittest.TestCase):
    def test_programs_nest_under_atlas(self):
        from packages.contracts.modules import children, lineage
        registry = load_registry(ROOT / "modules", ROOT)
        self.assertEqual(lineage(registry, "engine"), ["atlas", "engine"])
        self.assertEqual(children(registry, "atlas"), ["engine", "finance"])
        self.assertIsNone(registry["modules"]["atlas"].get("parent"))

    def test_atlas_can_become_a_sub_program(self):
        from packages.contracts.modules import lineage
        bigger = module(id="bigger", kind="platform", capabilities=[])
        atlas = module(id="atlas", kind="platform", parent="bigger", capabilities=[])
        engine = module(id="engine", parent="atlas", capabilities=[])
        self.assertEqual(lineage(build_registry([bigger, atlas, engine]), "engine"), ["bigger", "atlas", "engine"])

    def test_nesting_errors(self):
        for label, manifests in {
            "unknown parent": [module(parent="nowhere")],
            "tool with parent": [module(id="root", capabilities=[]), {**tool("t"), "parent": "root"}],
            "tool as parent": [tool("t"), module(parent="t")],
            "self parent": [module(parent="demo")],
            "loop": [module(id="a", parent="b", capabilities=[]), module(id="b", parent="a", capabilities=[])],
        }.items():
            with self.subTest(label), self.assertRaises(ValueError):
                build_registry(manifests)


class Routing(unittest.TestCase):
    def test_live_but_not_callable_is_elsewhere(self):
        app_cap = {"id": "demo.plans.research", "domain": "plans", "summary": "Research.", "effect": "read",
                   "status": "live", "intent": "web.research", "evidence": "Run"}
        route = resolve(build_registry([module(capabilities=[app_cap], callable=False), tool("claude")]), "web.research")
        self.assertIsNone(route["here"])
        self.assertEqual([c["id"] for c in route["elsewhere"]], ["demo.plans.research"])
        self.assertEqual([c["module"] for c in route["switchTo"]], ["claude"])

    def test_prefers_platform_then_app_then_switches_to_tool(self):
        app_cap = {"id": "demo.plans.research", "domain": "plans", "summary": "Research.", "effect": "read",
                   "status": "live", "intent": "web.research", "evidence": "Run"}
        registry = build_registry([module(capabilities=[app_cap], callable=True), tool("claude"), tool("codex", "planned")])
        route = resolve(registry, "web.research")
        self.assertEqual(route["here"]["id"], "demo.plans.research")
        self.assertEqual([c["id"] for c in route["alsoAvailableIn"]], ["claude.workbench.research"])
        self.assertEqual(route["switchTo"], [])
        registry = build_registry([module(capabilities=[{**app_cap, "status": "planned"}], callable=True), tool("claude")])
        route = resolve(registry, "web.research")
        self.assertIsNone(route["here"])
        self.assertEqual([c["id"] for c in route["switchTo"]], ["claude.workbench.research"])
        self.assertEqual([c["id"] for c in route["planned"]], ["demo.plans.research"])

    def test_repository_routes(self):
        registry = load_registry(ROOT / "modules", ROOT)
        research = resolve(registry, "web.research")
        # deep research is answered in Atlas through the hub (RA-048); Claude desktop stays an alternative
        self.assertEqual(research["here"]["id"], "engine.research.find_options")
        self.assertEqual([c["module"] for c in research["alsoAvailableIn"]], ["claude-desktop"])
        self.assertEqual(resolve(registry, "notes.read")["here"]["id"], "atlas.notes.list")
        self.assertEqual({c["module"] for c in resolve(registry, "code.edit")["switchTo"]}, {"claude-desktop", "codex"})
        with self.assertRaises(ValueError):
            resolve(registry, "nonsense")

    def test_http(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            httpd = server.make_server(0, folder + "/t.db")
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{httpd.server_port}"
            try:
                body = json.load(urllib.request.urlopen(url + "/api/modules"))
                self.assertEqual({m["id"] for m in body["modules"]}, {"atlas", "engine", "finance", "claude-desktop", "codex"})
                route = json.load(urllib.request.urlopen(url + "/api/route?intent=web.research"))
                self.assertEqual(route["alsoAvailableIn"][0]["module"], "claude-desktop")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(url + "/api/route?intent=x")
                self.assertEqual(ctx.exception.code, 400)
            finally:
                httpd.shutdown(); httpd.server_close()
