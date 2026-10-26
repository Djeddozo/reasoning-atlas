from contextlib import closing
import json, sqlite3, tempfile, threading, unittest, urllib.error, urllib.request, uuid
from pathlib import Path
from unittest import mock
from services.atlas import reminders, server

AGENT = {"schemaVersion": 1, "reminders": [
    {"id": "dissect-example", "title": "Dissect Example", "detail": "Questions", "link": "RA-023", "group": "Apps to dissect"}]}


def post(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(request))


class Reminders(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.agent_file = root / "owner-reminders.json"
        self.agent_file.write_text(json.dumps(AGENT), encoding="utf-8")
        self.patch = mock.patch.object(reminders, "AGENT_FILE", self.agent_file)
        self.patch.start()
        self.db = str(root / "atlas.db")

    def tearDown(self):
        self.patch.stop()
        self.folder.cleanup()

    def test_upgrade_keeps_existing_notes(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL, createdAt TEXT NOT NULL)")
            db.execute("INSERT INTO notes VALUES ('a','Existing note','2026-10-19T00:00:00Z')")
            db.execute("PRAGMA user_version=1")
        store = server.Store(self.db)
        self.assertEqual(store.list()[0]["body"], "Existing note")
        self.assertIsNone(store.list()[0]["sourceId"])  # provenance of older notes stays unknown
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)  # notes 1, reminders 2, handoffs 3, sources 4, owner actions 5

    def test_owner_and_agent_reminders_with_done_state(self):
        store = server.Store(self.db)
        identifier = str(uuid.uuid4())
        rid = store.add_reminder(identifier, "Call the bank", "Before Friday")
        self.assertEqual(store.add_reminder(identifier, "Call the bank", "Before Friday"), rid)
        with self.assertRaises(ValueError):
            store.add_reminder(identifier, "Something else")
        items = {r["id"]: r for r in store.reminders()}
        self.assertEqual(items["dissect-example"]["source"], "agent")
        self.assertEqual(items[rid]["source"], "owner")
        store.set_reminder_done("dissect-example", True)
        first = {r["id"]: r for r in store.reminders()}["dissect-example"]["doneAt"]
        store.set_reminder_done("dissect-example", True)
        self.assertEqual({r["id"]: r for r in store.reminders()}["dissect-example"]["doneAt"], first)
        store.set_reminder_done("dissect-example", False)
        self.assertIsNone({r["id"]: r for r in store.reminders()}["dissect-example"]["doneAt"])
        with self.assertRaises(KeyError):
            store.set_reminder_done("unknown", True)
        self.assertIn("dissect-example", self.agent_file.read_text(encoding="utf-8"))

    def test_missing_agent_file_is_empty(self):
        self.agent_file.unlink()
        self.assertEqual(server.Store(self.db).reminders(), [])

    def test_http_endpoints(self):
        httpd = server.make_server(0, self.db)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{httpd.server_port}"
        try:
            rid = post(url + "/api/reminders", {"id": str(uuid.uuid4()), "title": "Dissect my apps"})["id"]
            self.assertEqual(post(url + f"/api/reminders/{rid}/done", {"done": True}), {"id": rid, "done": True})
            listed = {r["id"]: r for r in json.load(urllib.request.urlopen(url + "/api/reminders"))}
            self.assertIsNotNone(listed[rid]["doneAt"])
            for path, body, code in (("/api/reminders/nope/done", {"done": True}, 404),
                                     (f"/api/reminders/{rid}/done", {}, 400),
                                     (f"/api/reminders/{rid}/done", {"done": "yes"}, 400),
                                     ("/api/reminders", {"id": "not-a-uuid", "title": "x"}, 400)):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    post(url + path, body)
                self.assertEqual(ctx.exception.code, code)
        finally:
            httpd.shutdown(); httpd.server_close()
