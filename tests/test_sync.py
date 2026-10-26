"""RA-013: Windows–Pi offline sync. Disconnect during acknowledgment, restart, conflicting edits, tombstones."""
import contextlib, json, sqlite3, tempfile, threading, unittest, urllib.error, urllib.request, uuid
from pathlib import Path
from unittest import mock
from services.atlas import hub, hubclient, server, sync
from tests.test_hub import TOKEN, engine_database


def opener(path):
    @contextlib.contextmanager
    def connect():
        db = sqlite3.connect(path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()
    return connect


class Authority:
    """The Pi's side in-process; `drop_ack` simulates a connection that breaks after the Pi applied the batch."""

    def __init__(self, path):
        self.connect = opener(path)
        with self.connect() as db:
            sync.migrate_authority(db)
        self.drop_ack = False
        self.offline = False
        self.seen = []

    def push(self, device_id, commands):
        if self.offline:
            raise ConnectionRefusedError("no route to the Pi")
        self.seen.append(json.dumps({"deviceId": device_id, "commands": commands}))
        with self.connect() as db:
            results = sync.apply(db, device_id, commands)
        if self.drop_ack:
            self.drop_ack = False
            raise TimeoutError("connection lost while waiting for the answer")
        return results

    def pull(self, since):
        if self.offline:
            raise ConnectionRefusedError("no route to the Pi")
        with self.connect() as db:
            return sync.changes(db, since)

    def note(self, note_id):
        with self.connect() as db:
            return sync._current(db, note_id)


def client(path):
    connect = opener(path)
    with connect() as db:
        sync.migrate_client(db)
    return connect


class Sync(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.dir = Path(self.folder.name)
        self.pi = Authority(self.dir / "authority.db")
        self.pc = client(self.dir / "pc.db")

    def tearDown(self):
        self.folder.cleanup()

    def edit(self, connect, note_id, body):
        with connect() as db:
            return sync.enqueue(db, note_id, "upsert", body)

    def test_edit_offline_then_sync(self):
        nid = str(uuid.uuid4())
        self.pi.offline = True
        self.edit(self.pc, nid, "first thought")
        result = sync.flush(self.pc, self.pi)
        self.assertEqual(result["pending"] + result["sent"], 1)
        self.assertIn("outbox", result["lastError"])
        self.assertTrue(result["stale"])
        with self.pc() as db:
            self.assertEqual(sync.notes(db)[0]["state"], "pending")  # visible locally while offline
        self.pi.offline = False
        result = sync.flush(self.pc, self.pi)
        self.assertEqual((result["acknowledged"], result["pending"], result["sent"], result["lastError"]), (1, 0, 0, None))
        self.assertFalse(result["stale"])
        self.assertEqual(self.pi.note(nid)["revision"], 1)
        with self.pc() as db:
            self.assertEqual(sync.notes(db)[0]["state"], "synced")

    def test_disconnect_during_acknowledgment_and_restart(self):
        nid = str(uuid.uuid4())
        self.edit(self.pc, nid, "v1")
        self.pi.drop_ack = True
        result = sync.flush(self.pc, self.pi)
        self.assertEqual(result["sent"], 1)  # applied on the Pi, but the PC never heard back
        self.assertEqual(self.pi.note(nid)["revision"], 1)
        pc = client(self.dir / "pc.db")  # restart: a fresh process opens the same file
        self.edit(pc, nid, "v2")  # edits on top of the unacknowledged one still chain correctly
        result = sync.flush(pc, self.pi)
        self.assertEqual((result["acknowledged"], result["conflicts"], result["sent"]), (2, 0, 0))
        note = self.pi.note(nid)
        self.assertEqual((note["revision"], note["body"]), (2, "v2"))  # replay did not apply v1 twice
        with self.pi.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sync_commands").fetchone()[0], 2)
        resent = json.loads(self.pi.seen[-1])["commands"]
        self.assertEqual(len(resent), 2)  # the same command id was sent again
        self.assertEqual(resent[0]["commandId"], json.loads(self.pi.seen[0])["commands"][0]["commandId"])

    def test_concurrent_edits_conflict_visibly(self):
        nid = str(uuid.uuid4())
        laptop = client(self.dir / "laptop.db")
        self.edit(self.pc, nid, "shared")
        sync.flush(self.pc, self.pi)
        sync.flush(laptop, self.pi)
        self.edit(self.pc, nid, "PC version")
        self.edit(laptop, nid, "laptop version")
        sync.flush(self.pc, self.pi)
        result = sync.flush(laptop, self.pi)
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(self.pi.note(nid)["body"], "PC version")  # no silent last-write-wins
        with laptop() as db:
            [c] = sync.conflicts(db)
            self.assertEqual((c["mine"]["body"], c["theirs"]["body"], c["theirs"]["revision"]), ("laptop version", "PC version", 2))
            self.assertEqual(sync.notes(db)[0]["state"], "conflict")
        with self.pi.connect() as db:
            mine, theirs = db.execute("SELECT mine, theirs FROM sync_conflicts").fetchone()
        self.assertEqual((mine, theirs), ("laptop version", "PC version"))  # both kept on the authority too
        with laptop() as db:
            sync.resolve(db, c["commandId"], "mine")
        result = sync.flush(laptop, self.pi)
        self.assertEqual((result["conflicts"], self.pi.note(nid)["body"], self.pi.note(nid)["revision"]), (0, "laptop version", 3))
        sync.flush(self.pc, self.pi)
        with self.pc() as db:
            self.assertEqual(sync.notes(db)[0]["body"], "laptop version")

    def test_keep_theirs_and_held_followups(self):
        nid = str(uuid.uuid4())
        laptop = client(self.dir / "laptop.db")
        self.edit(self.pc, nid, "base")
        sync.flush(self.pc, self.pi); sync.flush(laptop, self.pi)
        self.edit(self.pc, nid, "PC")
        sync.flush(self.pc, self.pi)
        self.edit(laptop, nid, "laptop 1")
        self.edit(laptop, nid, "laptop 2")
        result = sync.flush(laptop, self.pi)
        self.assertEqual(result["conflicts"], 2)  # the second edit is held behind the first
        with laptop() as db:
            for c in sync.conflicts(db):
                sync.resolve(db, c["commandId"], "theirs")
            self.assertEqual(sync.notes(db)[0]["body"], "PC")
            self.assertEqual(sync.conflicts(db), [])
            kept = db.execute("SELECT body FROM sync_outbox WHERE status='resolved_theirs' ORDER BY seq").fetchall()
        self.assertEqual(kept, [("laptop 1",), ("laptop 2",)])  # the other version is never thrown away
        self.assertEqual(self.pi.note(nid)["revision"], 2)

    def test_delete_travels_as_tombstone(self):
        nid = str(uuid.uuid4())
        laptop = client(self.dir / "laptop.db")
        self.edit(self.pc, nid, "to be removed")
        sync.flush(self.pc, self.pi); sync.flush(laptop, self.pi)
        with self.pc() as db:
            sync.enqueue(db, nid, "delete")
            self.assertEqual(sync.notes(db), [])
        sync.flush(self.pc, self.pi)
        note = self.pi.note(nid)
        self.assertEqual((note["deleted"], note["revision"], note["body"]), (True, 2, "to be removed"))
        sync.flush(laptop, self.pi)
        with laptop() as db:
            self.assertEqual(sync.notes(db), [])
            self.assertTrue(sync.notes(db, include_deleted=True)[0]["deleted"])
            with self.assertRaises(KeyError):
                sync.enqueue(db, nid, "delete")

    def test_batch_is_all_or_nothing(self):
        good = {"commandId": str(uuid.uuid4()), "noteId": str(uuid.uuid4()), "op": "upsert", "body": "ok", "baseRevision": None}
        bad = {**good, "commandId": str(uuid.uuid4()), "op": "drop"}
        with self.pi.connect() as db, self.assertRaises(ValueError):
            sync.apply(db, "pc-1", [good, bad])
        self.assertIsNone(self.pi.note(good["noteId"]))

    def test_other_authority_is_refused(self):
        self.edit(self.pc, str(uuid.uuid4()), "x")
        sync.flush(self.pc, self.pi)
        other = Authority(self.dir / "other.db")
        with self.assertRaises(sync.SyncError):
            sync.flush(self.pc, other)

    def test_no_credentials_in_outbox_or_traffic(self):
        self.edit(self.pc, str(uuid.uuid4()), "note")
        sync.flush(self.pc, self.pi)
        with self.pc() as db:
            dump = "\n".join(db.iterdump())
        for text in [dump, *self.pi.seen]:
            self.assertNotIn("token", text.lower())
            self.assertNotIn("Bearer", text)


class SyncOverHub(unittest.TestCase):
    """The real path: Atlas service on the PC → hub on the Pi, with the PC's own hub token only."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.dir = Path(self.folder.name)
        self.patch = mock.patch.object(hubclient, "CONFIG", self.dir / "hub.json")
        self.patch.start()
        hubclient._cache.update(at=0.0, value=None)
        self.hub = hub.make_server("127.0.0.1", 0, TOKEN, engine_database(self.folder.name), self.dir / "authority.db")
        threading.Thread(target=self.hub.serve_forever, daemon=True).start()
        self.hub_url = f"http://127.0.0.1:{self.hub.server_port}"
        self.atlas = server.make_server(0, str(self.dir / "atlas.db"))
        threading.Thread(target=self.atlas.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.atlas.server_port}"

    def tearDown(self):
        self.atlas.shutdown(); self.atlas.server_close()
        self.hub.shutdown(); self.hub.server_close()
        self.patch.stop()
        self.folder.cleanup()

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=15))

    def test_round_trip(self):
        nid = str(uuid.uuid4())
        view = self.post("/api/sync/notes", {"id": nid, "body": "from the PC"})
        self.assertEqual((view["pending"], view["configured"]), (1, False))
        self.assertEqual(self.post("/api/sync/run", {})["pending"], 1)  # no hub yet: waits in the outbox
        hubclient.configure(self.hub_url, TOKEN)
        view = self.post("/api/sync/run", {})
        self.assertEqual((view["acknowledged"], view["pending"], view["lastError"]), (1, 0, None))
        view = json.load(urllib.request.urlopen(self.url + "/api/sync"))
        self.assertEqual(view["notes"][0]["body"], "from the PC")
        self.assertNotIn(TOKEN, json.dumps(view))
        with sqlite3.connect(self.dir / "authority.db") as db:
            self.assertEqual(db.execute("SELECT body, revision FROM sync_notes WHERE id=?", (nid,)).fetchone(), ("from the PC", 1))
        self.assertFalse((self.dir / "hq.db").read_bytes().count(b"from the PC"))  # never in the Engine's database

    def test_hub_refuses_without_token(self):
        req = urllib.request.Request(self.hub_url + "/v1/sync/pull?since=0")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=15)
        self.assertEqual(ctx.exception.code, 401)

    def test_hub_rejects_bad_batch(self):
        req = urllib.request.Request(self.hub_url + "/v1/sync/push", data=json.dumps({"deviceId": "pc", "commands": [{"op": "x"}]}).encode(),
                                     headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=15)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
