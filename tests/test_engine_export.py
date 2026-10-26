"""RA-009: the Engine's read-only snapshot export, the hub function and Atlas's connected import."""
from contextlib import closing
import hashlib, json, sqlite3, tempfile, threading, unittest, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from adapters.hq import snapshot as hq_snapshot
from engine import db as hqdb, export
from services.atlas import enginesync as connect, hub, server

TOKEN = "test-token-0123456789abcdef"


def engine_database(folder):
    """A synthetic test instance of the Engine: real schema, invented rows."""
    path = Path(folder) / "hq.db"
    con = hqdb.connect(path)
    hqdb.init(con)
    g = hqdb.insert(con, "goals", title="Synthetic goal", horizon="year", why="Synthetic", status="active", created=hqdb.now())
    hqdb.insert(con, "goals", title="Dropped goal", horizon="month", status="dropped", created=hqdb.now())
    t1 = hqdb.insert(con, "tasks", title="Synthetic done task", status="done", owner="ai", goal_id=g, capability_id=1, created=hqdb.now())
    t2 = hqdb.insert(con, "tasks", title="Synthetic open task", status="todo", owner="me", goal_id=g, depends_on=str(t1), created=hqdb.now())
    hqdb.insert(con, "tasks", title="Synthetic dropped task", status="dropped", owner="ai", created=hqdb.now())
    hqdb.insert(con, "tasks", title="Synthetic draft", status="proposed", owner="ai", depends_on="9999", created=hqdb.now())
    hqdb.insert(con, "resources", kind="hardware", name="Synthetic Pi", details="runs the Engine", value=1, unit="pc", updated=hqdb.now())
    hqdb.insert(con, "resources", kind="time", name="Retired hours", value=2, unit="h", status="retired")
    hqdb.insert(con, "ideas", title="Synthetic idea", gate="idea", created=hqdb.now())
    hqdb.insert(con, "dev_runs", task_id=t2, branch="hq-dev/x", status="pr_open", started=hqdb.now(), head="abc1234")
    hqdb.insert(con, "dev_runs", task_id=None, branch="hq-dev/y", status="failed", started=hqdb.now())
    hqdb.insert(con, "research_notes", question="Synthetic question?", status="done", answer=json.dumps({"summary": "Synthetic."}),
                created=hqdb.now())
    con.commit()
    con.close()
    return path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Export(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = engine_database(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def export(self, **kw):
        with closing(hub.read_only(self.db)) as con:
            return export.snapshot(con, commit="testcommit", **kw)

    def test_export_imports_into_atlas(self):
        before = digest(self.db)
        snap = self.export()
        self.assertEqual(digest(self.db), before, "exporting must not change the Engine's database")
        self.assertTrue(snap["readOnly"])
        with closing(sqlite3.connect(":memory:")) as atlas:
            result = hq_snapshot.import_snapshot(atlas, snap)
            self.assertGreater(result["inserted"], 0)
            again = hq_snapshot.import_snapshot(atlas, self.export(captured=snap["capturedAt"]))
            self.assertEqual((again["inserted"], again["updated"]), (0, 0), "an unchanged Engine re-imports as unchanged")

    def test_audit_matches_engine_database(self):
        """Test-instance audit: what Atlas holds equals what the Engine's tables hold."""
        snap = self.export()
        with closing(hub.read_only(self.db)) as con:
            rows = export.counts(con)
            seeded = sqlite3.connect(":memory:")
            con.backup(seeded)
            seeded.row_factory = sqlite3.Row
            from engine import skilltree
            nodes = len(skilltree.nodes(seeded))
            seeded.close()
        exported = {}
        for e in snap["entities"]:
            exported[e["entityType"]] = exported.get(e["entityType"], 0) + 1
        self.assertEqual({k: v for k, v in exported.items() if k not in ("capability", "run")},
                         {k: v for k, v in rows.items() if k not in ("capability", "run")})
        self.assertEqual(exported["run"], rows["run"] - 1, "the run without a task is left out")
        self.assertEqual(exported["capability"], nodes)
        with closing(sqlite3.connect(":memory:")) as atlas:
            result = hq_snapshot.import_snapshot(atlas, snap)
            model = hq_snapshot.read_model(atlas)
        tasks = {r["externalRef"]["externalId"]: r for r in model if r["entityType"] == "Task"}
        with closing(hub.read_only(self.db)) as con:
            live = {str(r[0]): r[1] for r in con.execute("SELECT id, status FROM tasks WHERE status != 'dropped'")}
        self.assertEqual({k: t["externalRef"]["originalStatus"] for k, t in tasks.items()}, live)
        self.assertEqual(result["excluded"], 1, "the dropped task")
        self.assertTrue(any(r.get("status") == "unresolved" and r["toId"] == "hq:task:9999" for r in model if r["entityType"] == "Edge"))

    def test_timestamps_are_utc(self):
        for e in self.export()["entities"]:
            for field in ("created", "updated", "started", "finished"):
                value = e["data"].get(field)
                if value:
                    self.assertTrue(value.endswith("Z"), (e["entityType"], field, value))

    def test_schema_mismatch_is_reported(self):
        con = sqlite3.connect(self.db)
        con.execute("ALTER TABLE tasks RENAME COLUMN owner TO owned_by")  # an Engine upgrade Atlas was not built for
        con.commit()
        con.close()
        with self.assertRaises(export.SchemaMismatch) as ctx:
            self.export()
        self.assertIn("tasks lacks owner", str(ctx.exception))

    def test_unknown_export_version_is_refused(self):
        snap = self.export()
        snap["exportVersion"] = 2
        with self.assertRaises(hq_snapshot.IncompatibleSnapshot):
            hq_snapshot.normalize(snap)

    def test_export_source_cannot_write(self):
        source = Path(export.__file__).read_text()
        for forbidden in ("rescore", "INSERT", "UPDATE ", "DELETE", "commit(", "urlopen", "subprocess"):
            self.assertNotIn(forbidden, source)


class Boundary(unittest.TestCase):
    """The PC side against a real hub on a synthetic Engine: success, unauthorized, offline, upgrade."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.db = engine_database(cls.folder.name)
        cls.server = hub.make_server("127.0.0.1", 0, TOKEN, cls.db)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.folder.cleanup()

    def setUp(self):
        self.atlas = sqlite3.connect(":memory:")

    def tearDown(self):
        self.atlas.close()

    def test_connected_read_changes_nothing_in_the_engine(self):
        before = digest(self.db)
        with mock.patch.object(hub, "git_version", return_value=("main", "c0ffee")):
            state = connect.sync(self.atlas, {"url": self.url, "token": TOKEN})
        self.assertEqual(digest(self.db), before)
        self.assertEqual(state["state"], "connected", state["message"])
        self.assertEqual((state["readOnly"], state["writes"]), (True, False))
        self.assertEqual(state["sourceCommit"], "c0ffee")
        self.assertGreater(state["counts"]["task"], 0)

    def test_unauthorized(self):
        state = connect.sync(self.atlas, {"url": self.url, "token": "wrong-token-0123456789abcdef"})
        self.assertEqual(state["state"], "unauthorized")
        self.assertIsNone(state["snapshotId"])

    def test_offline_keeps_last_data_and_says_so(self):
        connect.sync(self.atlas, {"url": self.url, "token": TOKEN})
        state = connect.sync(self.atlas, {"url": "http://127.0.0.1:1", "token": TOKEN})
        self.assertEqual(state["state"], "offline")
        self.assertIsNotNone(state["snapshotId"], "the cached read model stays")
        self.assertEqual([a["outcome"] for a in state["attempts"][:2]], ["offline", "ok"])

    def test_not_configured(self):
        self.assertEqual(connect.sync(self.atlas, {})["state"], "not_configured")

    def test_older_engine_without_export(self):
        with mock.patch.dict(hub.FUNCTIONS, {}, clear=True):
            state = connect.sync(self.atlas, {"url": self.url, "token": TOKEN})
        self.assertEqual(state["state"], "schema_mismatch")
        self.assertIn("older Engine", state["message"])

    def test_engine_schema_mismatch_over_http(self):
        with mock.patch.object(export, "check_schema", return_value={"tasks": ["owner"]}):
            state = connect.sync(self.atlas, {"url": self.url, "token": TOKEN})
        self.assertEqual(state["state"], "schema_mismatch")
        self.assertIn("tasks lacks owner", state["message"])

    def test_incompatible_snapshot_from_newer_engine(self):
        def newer(config):
            with closing(hub.read_only(self.db)) as con:
                snap = export.snapshot(con, commit="x")
            snap["exportVersion"] = 99
            return "ok", snap, ""
        state = connect.sync(self.atlas, {"url": self.url, "token": TOKEN}, fetcher=newer)
        self.assertEqual(state["state"], "schema_mismatch")
        self.assertIsNone(state["snapshotId"], "nothing was imported")

    def test_stale(self):
        connect.sync(self.atlas, {"url": self.url, "token": TOKEN})
        later = datetime.now(timezone.utc) + timedelta(minutes=connect.STALE_AFTER_MINUTES + 5)
        state = connect.connection(self.atlas, now=later)
        self.assertEqual(state["state"], "stale")
        self.assertTrue(state["dataStale"])

    def test_rescoring_read_is_not_exposed_as_a_pure_read(self):
        """GET /api/data in the old dashboard rescores; the hub's reads run on a read-only connection,
        so a function that tried to rescore would fail instead of silently writing."""
        with closing(hub.read_only(self.db)) as con, self.assertRaises(sqlite3.OperationalError):
            from engine import dashboard
            dashboard.build_data(con)
        self.assertNotIn("dashboard.build_data", Path(hub.__file__).read_text())


class Service(unittest.TestCase):
    def test_sync_and_connection_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            httpd = server.make_server(0, folder + "/atlas.db")
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{httpd.server_port}"
            try:
                with mock.patch("services.atlas.hubclient.load", return_value=None):
                    first = json.load(urllib.request.urlopen(url + "/api/hq/connection"))
                    self.assertEqual(first["state"], "never")
                    req = urllib.request.Request(url + "/api/hq/sync", data=b"{}", method="POST",
                                                 headers={"Content-Type": "application/json"})
                    self.assertEqual(json.load(urllib.request.urlopen(req))["state"], "not_configured")
            finally:
                httpd.shutdown(); httpd.server_close()


if __name__ == "__main__":
    unittest.main()
