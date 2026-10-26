from contextlib import closing
import copy, json, re, sqlite3, tempfile, threading, unittest, urllib.request
from pathlib import Path
from adapters.hq import snapshot
from packages.contracts import import_identity
from services.atlas import server

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "fixtures/hq-snapshot-1599d42.json").read_text(encoding="utf-8"))


def fixture():
    return copy.deepcopy(FIXTURE)


def entity(snap, kind, eid):
    return next(e for e in snap["entities"] if e["entityType"] == kind and e["externalId"] == eid)


class Normalization(unittest.TestCase):
    def test_pinned_fixture_normalizes_ids_proof_and_status(self):
        records = {r["id"]: r for r in snapshot.normalize(fixture())["records"]}
        cap = records[import_identity("hq", "capability", "8")]
        self.assertEqual((cap["title"], cap["proofLevel"], cap["availability"]), ("Budget & spending", "partial", "building"))
        self.assertEqual(cap["evidenceIds"], [import_identity("hq", "task", "101")])
        self.assertEqual(cap["requiresIds"], [import_identity("hq", "capability", "6")])
        self.assertEqual(cap["externalRef"]["sourceCommit"], FIXTURE["sourceCommit"])
        task = records[import_identity("hq", "task", "102")]
        self.assertEqual((task["status"], task["externalRef"]["originalStatus"]), ("in_progress", "doing"))
        self.assertEqual(records[import_identity("hq", "task", "103")]["status"], "draft")
        run = records[import_identity("hq", "run", "304")]
        self.assertEqual((run["status"], run["taskId"]), ("interrupted", import_identity("hq", "task", "102")))
        resource = records[import_identity("hq", "resource", "1")]
        self.assertEqual((resource["resourceType"], resource["attributes"]["unit"]), ("hardware", "GB VRAM"))

    def test_unresolved_references_stay_explicit(self):
        edges = [r for r in snapshot.normalize(fixture())["records"] if r["entityType"] == "Edge"]
        unresolved = sorted(e["toId"] for e in edges if e["status"] == "unresolved")
        self.assertEqual(unresolved, ["hq:capability:14", "hq:task:999"])

    def test_dropped_items_are_excluded(self):
        result = snapshot.normalize(fixture())
        titles = {r.get("title") for r in result["records"]}
        self.assertNotIn("Synthetic task: dropped experiment", titles)
        self.assertEqual(result["excluded"], 1)

    def test_missing_fields_are_rejected(self):
        for kind, eid, field in (("capability", "8", "proof"), ("task", "101", "status"), ("run", "301", "task_id"),
                                 ("resource", "1", "kind"), ("goal", "1", "horizon"), ("research", "401", "question")):
            snap = fixture()
            del entity(snap, kind, eid)["data"][field]
            with self.subTest(kind=kind, field=field), self.assertRaisesRegex(ValueError, field):
                snapshot.normalize(snap)
        for field in ("snapshotId", "sourceCommit", "capturedAt", "entities"):
            snap = fixture()
            del snap[field]
            with self.subTest(envelope=field), self.assertRaises(ValueError):
                snapshot.normalize(snap)
        snap = fixture()
        del snap["entities"][0]["externalId"]
        with self.assertRaises(ValueError):
            snapshot.normalize(snap)

    def test_unknown_versions_are_incompatible(self):
        for change in ({"schemaVersion": 2}, {"sourceSystem": "lifedash"}, {"sourceCommit": "0" * 40}):
            with self.subTest(change=change), self.assertRaises(snapshot.IncompatibleSnapshot):
                snapshot.normalize({**fixture(), **change})
        for kind, eid, field, value in (("task", "101", "status", "archived"), ("capability", "1", "proof", "certain"),
                                        ("capability", "1", "branch", "space"), ("run", "301", "status", "queued")):
            snap = fixture()
            entity(snap, kind, eid)["data"][field] = value
            with self.subTest(field=field), self.assertRaises(snapshot.IncompatibleSnapshot):
                snapshot.normalize(snap)
        snap = fixture()
        snap["entities"][0]["entityType"] = "invoice"
        with self.assertRaises(ValueError):
            snapshot.normalize(snap)

    def test_duplicates_and_cycles_are_rejected(self):
        snap = fixture()
        snap["entities"].append(copy.deepcopy(snap["entities"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            snapshot.normalize(snap)
        snap = fixture()
        entity(snap, "capability", "1")["data"]["requires"] = "6"  # 6 requires 5 requires 2 requires 1
        with self.assertRaisesRegex(ValueError, "cycle"):
            snapshot.normalize(snap)


class Import(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)

    def counts(self):
        return snapshot.health(self.db)["counts"]

    def test_repeated_snapshot_does_not_change_counts(self):
        first = snapshot.import_snapshot(self.db, fixture())
        before = self.counts()
        second = snapshot.import_snapshot(self.db, fixture())
        self.assertEqual(first["inserted"], 53)
        self.assertEqual((second["inserted"], second["updated"], second["unchanged"]), (0, 0, 53))
        self.assertEqual(self.counts(), before)
        self.assertEqual(before["capability"], 10)

    def test_newer_snapshot_updates_and_removes(self):
        snapshot.import_snapshot(self.db, fixture())
        snap = fixture()
        snap.update(snapshotId="hq-synthetic-0002", capturedAt="2026-10-19T12:00:00Z")
        entity(snap, "task", "102")["data"]["status"] = "done"
        entity(snap, "task", "102")["revision"] = "2026-10-19T11:00:00Z"
        resource = entity(snap, "resource", "4")
        resource["deleted"] = True
        resource["data"] = {}
        result = snapshot.import_snapshot(self.db, snap)
        self.assertEqual(result["removed"], 1)
        self.assertGreaterEqual(result["updated"], 2)  # the task and its capability's evidence
        self.assertEqual(self.counts()["resource"], 3)
        cap = next(r for r in snapshot.read_model(self.db) if r.get("title") == "Budget & spending")
        self.assertEqual(len(cap["evidenceIds"]), 2)

    def test_invalid_snapshot_writes_nothing(self):
        snapshot.import_snapshot(self.db, fixture())
        before = self.db.execute("SELECT COUNT(*), MAX(importedAt) FROM external_refs").fetchone()
        snap = fixture()
        snap.update(snapshotId="hq-synthetic-0003", capturedAt="2026-10-20T12:00:00Z")
        del entity(snap, "task", "104")["data"]["owner"]
        with self.assertRaises(ValueError):
            snapshot.import_snapshot(self.db, snap)
        self.assertEqual(self.db.execute("SELECT COUNT(*), MAX(importedAt) FROM external_refs").fetchone(), before)
        self.assertIsNone(self.db.execute("SELECT 1 FROM external_imports WHERE snapshotId='hq-synthetic-0003'").fetchone())

    def test_older_snapshot_and_reused_id_are_refused(self):
        snap = fixture()
        snap.update(snapshotId="hq-synthetic-0002", capturedAt="2026-10-19T12:00:00Z")
        snapshot.import_snapshot(self.db, snap)
        with self.assertRaisesRegex(ValueError, "older"):
            snapshot.import_snapshot(self.db, fixture())
        reused = fixture()
        reused.update(snapshotId="hq-synthetic-0002", capturedAt="2026-10-21T12:00:00Z")
        with self.assertRaisesRegex(ValueError, "different content"):
            snapshot.import_snapshot(self.db, reused)

    def test_health_matches_contract(self):
        self.assertEqual(snapshot.health(self.db)["status"], "offline")
        snapshot.import_snapshot(self.db, fixture())
        health = snapshot.health(self.db)
        contract = json.loads((ROOT / "contracts/hq-adapter.openapi.json").read_text(encoding="utf-8"))["components"]["schemas"]["Health"]
        for field in contract["required"]:
            self.assertIn(field, health)
        self.assertEqual(health["capabilities"], {"readSnapshot": True, "writeProposals": False})
        self.assertEqual(health["upstreamCommit"], FIXTURE["sourceCommit"])


class NoLiveAccess(unittest.TestCase):
    def test_adapter_cannot_reach_hq(self):
        forbidden = re.compile(r"\b(urllib|http\.client|socket|requests|subprocess|hq\.db|HQ_DB|import hq\b|from hq\b)")
        for path in (ROOT / "adapters/hq").rglob("*.py"):
            with self.subTest(path=path.name):
                self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")), f"{path.name} must stay offline")

    def test_fixture_is_synthetic(self):
        for e in FIXTURE["entities"]:
            if e["entityType"] in ("goal", "task", "resource", "idea", "research"):
                text = e["data"].get("title") or e["data"].get("name") or e["data"].get("question")
                self.assertTrue(text.startswith("Synthetic"), text)


class Service(unittest.TestCase):
    def test_http_read_model(self):
        with tempfile.TemporaryDirectory() as folder:
            db = folder + "/t.db"
            httpd = server.make_server(0, db)
            with closing(sqlite3.connect(db)) as con:
                snapshot.import_snapshot(con, fixture())
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{httpd.server_port}"
            try:
                status = json.load(urllib.request.urlopen(url + "/api/hq/status"))
                self.assertEqual((status["status"], status["counts"]["task"]), ("ok", 7))
                self.assertEqual(len(json.load(urllib.request.urlopen(url + "/api/hq/entities"))["entities"]), 53)
                adapter = json.load(urllib.request.urlopen(url + "/api/health"))["adapter"]
                self.assertEqual((adapter["mode"], adapter["connected"]), ("snapshot", False))
            finally:
                httpd.shutdown(); httpd.server_close()
