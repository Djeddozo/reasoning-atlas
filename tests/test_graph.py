import copy, json, tempfile, threading, unittest, urllib.request
from pathlib import Path
from packages.contracts.modules import build_registry
from services.atlas import graph, server

ROOT = Path(__file__).resolve().parents[1]


def manifest(mid, caps, **extra):
    return {"schemaVersion": 1, "id": mid, "name": mid.title(), "kind": "app", "integrationLevel": "L2", "summary": "Test.",
            "domains": [{"id": "core", "name": "Core", "summary": "Core."}], "capabilities": caps, **extra}


def cap(mid, name, requires=()):
    return {"id": f"{mid}.core.{name}", "domain": "core", "summary": "Test.", "effect": "read", "status": "planned",
            "requires": list(requires)}


class Graph(unittest.TestCase):
    def test_repository_graph(self):
        g = graph.build(ROOT)
        self.assertEqual({p["id"] for p in g["programs"]}, {"atlas", "engine", "finance", "claude-desktop", "codex"})
        self.assertEqual(sum(f["current"] for f in g["focuses"]), 1)
        shared = [f for f in g["functions"] if len(f["focuses"]) > 1]
        self.assertTrue(shared, "some functions belong to several focuses")
        engine = next(f for f in g["focuses"] if f["id"] == "engine-into-atlas")
        self.assertEqual(len(engine["members"]), len([f for f in g["functions"] if f["program"] == "engine"]))
        finance = next(f for f in g["focuses"] if f["id"] == "finance")
        self.assertEqual(finance["members"], [])
        fn = next(f for f in g["functions"] if f["id"] == "engine.build.delivery_evidence")
        self.assertEqual((fn["label"], fn["requires"]), ("Delivery evidence", ["engine.build.run_status"]))

    def test_focus_selectors(self):
        reg = build_registry([manifest("a", [cap("a", "one"), cap("a", "two")]), manifest("b", [cap("b", "three")])])
        self.assertEqual(graph.resolve_focus({"id": "f", "programs": ["a"]}, reg), ["a.core.one", "a.core.two"])
        self.assertEqual(graph.resolve_focus({"id": "f", "domains": ["b.core"], "functions": ["a.core.one"]}, reg),
                         ["a.core.one", "b.core.three"])
        for bad in ({"id": "f", "functions": ["a.core.nope"]}, {"id": "f", "domains": ["a.money"]}, {"id": "f", "programs": ["zzz"]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                graph.resolve_focus(bad, reg)

    def test_requires_are_checked(self):
        with self.assertRaisesRegex(ValueError, "unknown capability"):
            build_registry([manifest("a", [cap("a", "one", ["a.core.missing"])])])
        with self.assertRaisesRegex(ValueError, "cycle"):
            build_registry([manifest("a", [cap("a", "one", ["a.core.two"]), cap("a", "two", ["a.core.one"])])])
        with self.assertRaises(ValueError):
            build_registry([manifest("a", [cap("a", "one", ["a.core.one"])])])

    def test_focus_file_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "modules").mkdir(); (root / "STATE").mkdir()
            (root / "modules/a.json").write_text(json.dumps(manifest("a", [cap("a", "one")])))
            for focuses in ([{"id": "x", "name": "X", "summary": "", "current": True}, {"id": "y", "name": "Y", "summary": "", "current": True}],
                            [{"id": "x", "name": "X", "summary": ""}, {"id": "x", "name": "X again", "summary": ""}]):
                (root / "STATE/focuses.json").write_text(json.dumps({"focuses": focuses}))
                with self.subTest(focuses=focuses), self.assertRaises(ValueError):
                    graph.build(root)

    def test_http(self):
        with tempfile.TemporaryDirectory() as folder:
            httpd = server.make_server(0, folder + "/t.db")
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                body = json.load(urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/graph"))
                self.assertEqual(len(body["functions"]), len(graph.build(ROOT)["functions"]))
            finally:
                httpd.shutdown(); httpd.server_close()
