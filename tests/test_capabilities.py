"""RA-022: capabilities called through the registry (read, propose with preview, act disabled, offline)."""
import contextlib, json, sqlite3, tempfile, threading, unittest, urllib.error, urllib.request
from functools import partial
from pathlib import Path
from unittest import mock

from adapters.hq import snapshot as hq_snapshot
from engine import models
from packages.contracts.modules import load_registry
from services.atlas import capabilities, hub, hubclient, server
from tests.test_hub import TOKEN, engine_database

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "modules", ROOT)


class Client:
    """hubclient bound to one config file, without its shared status cache."""
    def __init__(self, config_path):
        self.path = config_path

    def status(self):
        config = hubclient.load(self.path)
        return hubclient.check(config) if config else {"configured": False, "reachable": False}

    def call(self, fid, query=""):
        return hubclient.call(fid, query, self.path)

    def act(self, aid, body):
        return hubclient.act(aid, body, self.path)


class Spy:
    """A module adapter that records what reaches it."""
    via = "test adapter"

    def __init__(self, state=("ok", "", "abc1234")):
        self.reads, self.proposals, self._state = [], [], state

    def state(self):
        return self._state

    def read(self, cid, inputs):
        self.reads.append((cid, inputs))
        return 200, {"function": cid, "result": [{"id": 1}]}

    def propose(self, cid, inputs):
        self.proposals.append((cid, inputs))
        return 200, {"action": cid, "message": f"Kept: {inputs.get('text', '')}"}


def store(folder):
    path = Path(folder) / "atlas.db"

    @contextlib.contextmanager
    def connect():
        db = sqlite3.connect(path)
        try:
            with db:
                yield db
        finally:
            db.close()
    return connect


class ThroughTheEngineHub(unittest.TestCase):
    """Read and propose against a real hub serving a synthetic Engine database."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.engine_db = engine_database(cls.folder.name)
        cls.hub = hub.make_server("127.0.0.1", 0, TOKEN, cls.engine_db)
        threading.Thread(target=cls.hub.serve_forever, daemon=True).start()
        config = Path(cls.folder.name) / "hub.json"
        config.write_text(json.dumps({"url": f"http://127.0.0.1:{cls.hub.server_port}", "token": TOKEN}))
        cls.connect = staticmethod(store(cls.folder.name))
        cls.gateway = capabilities.Gateway(REGISTRY, cls.connect, {"engine": capabilities.HubAdapter(Client(config))})

    @classmethod
    def tearDownClass(cls):
        cls.hub.shutdown()
        cls.hub.server_close()
        cls.folder.cleanup()

    def test_read_carries_provenance(self):
        code, body = self.gateway.call("engine.attention.needs")
        self.assertEqual((code, body["ok"], body["outcome"]), (200, True, "ok"))
        self.assertTrue(any("Spend 5 euro" in (n.get("title") or "") for n in body["result"]))
        p = body["provenance"]
        self.assertEqual((p["capabilityId"], p["module"], p["effect"], p["via"]), ("engine.attention.needs", "engine", "read", "engine hub"))
        self.assertEqual(p["version"], hub.git_version()[1] or f"hub protocol v{hub.VERSION} (commit unknown)")
        self.assertTrue(p["at"].endswith("Z") and p["callId"])

    def test_read_inputs_reach_the_function(self):
        code, body = self.gateway.call("engine.direction.ranked_next_steps", {"n": 1})
        self.assertEqual(code, 200)
        self.assertEqual([t["title"] for t in body["result"]], ["First step"])

    def test_synthetic_snapshot_imports_through_the_adapter(self):
        code, body = self.gateway.call("engine.system.snapshot_export")
        self.assertEqual(code, 200)
        with self.connect() as db:
            hq_snapshot.migrate(db)
            result = hq_snapshot.import_snapshot(db, body["result"])
        self.assertEqual(result["snapshotId"], body["result"]["snapshotId"])
        self.assertGreater(result["inserted"], 0)

    def test_duplicate_propose_writes_once(self):
        def thoughts():
            with contextlib.closing(sqlite3.connect(self.engine_db)) as con:
                return con.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
        before = thoughts()
        inputs = {"text": "synthetic: one thought for the registry test"}
        _, first = self.gateway.preview("engine.brain.write_thought", inputs)
        _, second = self.gateway.preview("engine.brain.write_thought", inputs)
        self.assertEqual(first["preview"]["callId"], second["preview"]["callId"])
        self.assertTrue(second["preview"]["reused"])
        self.assertEqual(thoughts(), before)  # a preview sends nothing
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("offline")):
            code, done = self.gateway.confirm(first["preview"]["callId"])
            self.assertEqual((code, done["ok"]), (200, True), done)
            code, again = self.gateway.confirm(first["preview"]["callId"])
        self.assertTrue(again["replayed"])
        self.assertEqual(thoughts(), before + 1)
        _, third = self.gateway.preview("engine.brain.write_thought", inputs)
        self.assertEqual(third["preview"]["duplicateOf"]["callId"], first["preview"]["callId"])
        code, refused = self.gateway.confirm(third["preview"]["callId"])
        self.assertEqual((code, refused["outcome"]), (409, "duplicate"))
        self.assertEqual(thoughts(), before + 1)

    def test_act_is_disabled(self):
        code, body = self.gateway.call("engine.system.kill_switch")
        self.assertEqual((code, body["outcome"]), (403, "disabled"))
        code, body = self.gateway.preview("engine.system.kill_switch", {"on": True})
        self.assertEqual((code, body["outcome"]), (403, "disabled"))
        with contextlib.closing(sqlite3.connect(self.engine_db)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM state WHERE key='kill_switch'").fetchone()[0], 0)

    def test_provenance_audit(self):
        self.gateway.call("engine.memory.recall", {"q": "hubs"})
        self.gateway.call("engine.system.kill_switch")
        with self.connect() as db:
            report = capabilities.audit(db)
        self.assertGreater(report["checked"], 1)
        self.assertEqual(report["missing"], [])


class Offline(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.connect = store(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_unreachable_hub_fails_visibly(self):
        config = Path(self.folder.name) / "hub.json"
        config.write_text(json.dumps({"url": "http://127.0.0.1:9", "token": TOKEN}))  # nothing listens on port 9
        gateway = capabilities.Gateway(REGISTRY, self.connect, {"engine": capabilities.HubAdapter(Client(config))})
        code, body = gateway.call("engine.attention.needs")
        self.assertEqual((code, body["ok"], body["outcome"], body["result"]), (503, False, "offline", None))
        self.assertIn("cannot be reached", body["message"])
        self.assertEqual(body["provenance"]["capabilityId"], "engine.attention.needs")
        code, body = gateway.preview("engine.brain.write_thought", {"text": "x"})
        self.assertEqual((code, body["outcome"]), (503, "offline"))
        self.assertNotIn("preview", body)
        self.assertEqual([c["status"] for c in gateway.calls()], ["refused", "failed"])

    def test_no_hub_connected(self):
        gateway = capabilities.Gateway(REGISTRY, self.connect,
                                       {"engine": capabilities.HubAdapter(Client(Path(self.folder.name) / "none.json"))})
        code, body = gateway.call("engine.attention.needs")
        self.assertEqual((code, body["outcome"]), (503, "not_configured"))

    def test_module_goes_offline_after_preview(self):
        spy = Spy()
        gateway = capabilities.Gateway(REGISTRY, self.connect, {"engine": spy})
        _, preview = gateway.preview("engine.brain.write_thought", {"text": "x"})
        spy._state = ("offline", "The hub cannot be reached", None)
        code, body = gateway.confirm(preview["preview"]["callId"])
        self.assertEqual((code, body["outcome"]), (503, "offline"))
        self.assertEqual(spy.proposals, [])


class Rules(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.connect = store(self.folder.name)
        self.spy = Spy()
        self.gateway = capabilities.Gateway(REGISTRY, self.connect, {"engine": self.spy})

    def tearDown(self):
        self.folder.cleanup()

    def test_refusals(self):
        cases = [("engine.nothing.here", "unknown", 404), ("engine.research.tools_radar", "unavailable", 409),
                 ("atlas.notes.list", "unreachable", 409), ("engine.brain.write_thought", "invalid", 400)]
        for cid, outcome, code in cases:
            with self.subTest(cid=cid):
                got, body = self.gateway.call(cid)
                self.assertEqual((got, body["outcome"]), (code, outcome))
        self.assertEqual(self.gateway.call("engine.attention.needs", {"q": ["a"]})[1]["outcome"], "invalid")
        self.assertEqual(self.gateway.preview("engine.attention.needs", {})[1]["outcome"], "invalid")
        self.assertEqual((self.spy.reads, self.spy.proposals), ([], []))

    def test_expired_preview_is_not_sent(self):
        _, preview = self.gateway.preview("engine.brain.write_thought", {"text": "late"})
        with self.connect() as db:
            db.execute("UPDATE capability_calls SET expiresAt='2000-01-01T00:00:00Z'")
        code, body = self.gateway.confirm(preview["preview"]["callId"])
        self.assertEqual((code, body["outcome"]), (410, "expired"))
        self.assertEqual(self.spy.proposals, [])

    def test_interrupted_proposal_is_never_resent(self):
        _, preview = self.gateway.preview("engine.brain.write_thought", {"text": "crash"})
        with self.connect() as db:  # as if Atlas stopped after claiming the call, before the answer came back
            db.execute("UPDATE capability_calls SET status='running'")
        code, body = self.gateway.confirm(preview["preview"]["callId"])
        self.assertEqual((code, body["outcome"]), (409, "in_progress"))
        self.assertEqual(self.spy.proposals, [])

    def test_repeat_sends_again_on_purpose(self):
        inputs = {"text": "twice on purpose"}
        self.gateway.confirm(self.gateway.preview("engine.brain.write_thought", inputs)[1]["preview"]["callId"])
        again = self.gateway.preview("engine.brain.write_thought", inputs)[1]["preview"]["callId"]
        code, body = self.gateway.confirm(again, repeat=True)
        self.assertEqual(code, 200)
        self.assertEqual(len(self.spy.proposals), 2)

    def test_audit_finds_missing_provenance(self):
        self.gateway.call("engine.attention.needs")
        with self.connect() as db:
            db.execute("UPDATE capability_calls SET provenance=?", (json.dumps({"capabilityId": "engine.attention.needs"}),))
            report = capabilities.audit(db)
        self.assertEqual(len(report["missing"]), 1)
        self.assertIn("module", report["missing"][0]["missing"])


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.spy = Spy()
        self.server = server.make_server(0, str(Path(self.folder.name) / "atlas.db"), capability_adapters={"engine": self.spy})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.folder.cleanup()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_read_preview_confirm_log_audit(self):
        code, body = self.request("/api/capabilities/call/engine.direction.ranked_next_steps?n=3")
        self.assertEqual((code, body["provenance"]["capabilityId"]), (200, "engine.direction.ranked_next_steps"))
        self.assertEqual(self.spy.reads, [("engine.direction.ranked_next_steps", {"n": "3"})])
        code, body = self.request("/api/capabilities/preview", {"id": "engine.brain.write_thought", "inputs": {"text": "hi"}})
        self.assertEqual(code, 200)
        self.assertEqual(self.spy.proposals, [])
        code, body = self.request("/api/capabilities/confirm", {"callId": body["preview"]["callId"]})
        self.assertEqual((code, body["message"]), (200, "Kept: hi"))
        code, body = self.request("/api/capabilities/preview", {"id": "engine.system.kill_switch", "inputs": {}})
        self.assertEqual(code, 403)
        calls = self.request("/api/capabilities/calls")[1]["calls"]
        self.assertEqual(sorted(c["status"] for c in calls), ["done", "done", "refused"])
        self.assertEqual(self.request("/api/capabilities/audit")[1]["missing"], [])


if __name__ == "__main__":
    unittest.main()
