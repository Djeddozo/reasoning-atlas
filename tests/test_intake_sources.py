"""RA-005: local intake with source provenance (synthetic fixtures only)."""
import base64, contextlib, hashlib, json, sqlite3, subprocess, sys, tempfile, unittest, urllib.error, urllib.request, uuid
from pathlib import Path
from unittest import mock
from services.atlas import intake
from services.atlas.server import Store


@contextlib.contextmanager
def raw(path):
    db = sqlite3.connect(path)
    try:
        with db: yield db
    finally: db.close()

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
TEXT = "Synthetic source: fasting and focus.\nSecond line — with UTF-8.\n".encode("utf-8")


class IntakeStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = self.tmp.name + "/atlas.db"
        self.store = Store(self.db)

    def test_duplicate_import_keeps_one_copy(self):
        first = self.store.sources.add(TEXT, "text/plain", "notes.txt")
        second = self.store.sources.add(TEXT, "text/plain", "renamed.txt")
        self.assertFalse(first["duplicate"]); self.assertTrue(second["duplicate"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["contentHash"], hashlib.sha256(TEXT).hexdigest())
        [row] = self.store.sources.list()
        self.assertEqual(row["importCount"], 2)
        self.assertEqual(row["originalName"], "notes.txt")  # first provenance is kept
        blobs = [p for p in (Path(self.tmp.name) / "sources").rglob("*") if p.is_file()]
        self.assertEqual(len(blobs), 1)

    def test_original_retained_byte_for_byte_and_text_extracted(self):
        item = self.store.sources.add(TEXT, "text/markdown", "a.md")
        data, media = self.store.sources.original(item["id"])
        self.assertEqual(data, TEXT); self.assertEqual(media, "text/markdown")
        full = self.store.sources.get(item["id"])
        self.assertEqual(full["status"], "done"); self.assertEqual(full["extractedText"], TEXT.decode())
        png = self.store.sources.add(PNG, "image/png", "shot.png")
        self.assertEqual(png["status"], "done"); self.assertEqual(self.store.sources.original(png["id"])[0], PNG)

    def test_invalid_sources_are_refused_and_nothing_is_stored(self):
        cases = [(b"", "text/plain", "empty.txt"), (b"x" * (intake.MAX_BYTES + 1), "text/plain", "big.txt"),
                 (b"hello", "application/x-msdownload", "a.exe"), (b"not a pdf", "application/pdf", "fake.pdf"),
                 (b"GIF", "image/png", "fake.png"), (b"hello", "text/plain", "\x00bad"), (b"hello", "text/plain", "   ")]
        for data, media, name in cases:
            with self.subTest(name=name, media=media), self.assertRaises(ValueError):
                self.store.sources.add(data, media, name)
        self.assertEqual(self.store.sources.list(), [])
        for payload in ({"data": "%%%", "mediaType": "text/plain", "name": "a"}, [1], {"data": "aGk=", "mediaType": "text/plain", "name": "a", "uri": 5}):
            with self.assertRaises(ValueError): self.store.sources.add_base64(payload)

    def test_path_in_name_keeps_only_the_file_name(self):
        item = self.store.sources.add(TEXT, "text/plain", "../../etc/passwd")
        self.assertEqual(item["originalName"], "passwd")
        self.assertTrue(all(p.resolve().is_relative_to(Path(self.tmp.name)) for p in Path(self.tmp.name).rglob("*")))

    def test_unreadable_content_is_kept_as_invalid_not_retried(self):
        item = self.store.sources.add(b"\xff\xfe broken", "text/plain", "latin.txt")
        self.assertEqual(item["status"], "invalid"); self.assertIn("text/plain", item["error"])
        self.assertEqual(self.store.sources.original(item["id"])[0], b"\xff\xfe broken")
        self.assertEqual(self.store.sources.resume(), {})
        bad_json = self.store.sources.add(b"{not json", "application/json", "a.json")
        self.assertEqual(bad_json["status"], "invalid")

    def test_failed_processing_resumes_after_restart(self):
        with mock.patch.object(intake, "extract", side_effect=OSError("disk busy")):
            item = self.store.sources.add(TEXT, "text/plain", "a.txt")
        self.assertEqual(item["status"], "failed"); self.assertEqual(item["attempts"], 1)
        restarted = Store(self.db)
        self.assertEqual(restarted.sources.resume(), {item["id"]: "done"})
        done = restarted.sources.get(item["id"])
        self.assertEqual((done["status"], done["attempts"], done["error"]), ("done", 2, None))
        self.assertEqual(done["extractedText"], TEXT.decode())

    def test_crash_mid_processing_is_resumed(self):
        item = self.store.sources.add(TEXT, "text/plain", "a.txt")
        with raw(self.db) as db:  # simulate a process killed between claim and result
            db.execute("UPDATE sources SET status='processing', extractedText=NULL, processedAt=NULL WHERE id=?", (item["id"],))
        self.assertEqual(Store(self.db).sources.resume(), {item["id"]: "done"})

    def test_automatic_retries_stop_at_the_limit_owner_retry_still_works(self):
        with mock.patch.object(intake, "extract", side_effect=OSError("down")):
            item = self.store.sources.add(TEXT, "text/plain", "a.txt")
            for _ in range(intake.MAX_ATTEMPTS + 2): self.store.sources.resume()
        self.assertEqual(self.store.sources.get(item["id"])["attempts"], intake.MAX_ATTEMPTS)
        self.assertEqual(self.store.sources.retry(item["id"]), "done")

    def test_missing_original_fails_instead_of_inventing_content(self):
        item = self.store.sources.add(TEXT, "text/plain", "a.txt")
        intake.blob_path(Path(self.tmp.name) / "sources", item["contentHash"]).write_bytes(b"tampered")
        with raw(self.db) as db: db.execute("UPDATE sources SET status='pending' WHERE id=?", (item["id"],))
        self.assertEqual(self.store.sources.resume(), {item["id"]: "failed"})
        self.assertIn("hash", self.store.sources.get(item["id"])["error"])

    def test_notes_get_source_provenance_with_hash_dedupe(self):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        self.store.save("Same thought", a); self.store.save("Same thought", a)  # retry of one note
        self.store.save("Same thought", b)  # same text written again
        notes = self.store.list()
        self.assertEqual(len(notes), 2)
        self.assertEqual(notes[0]["sourceId"], notes[1]["sourceId"])
        self.assertEqual(notes[0]["contentHash"], hashlib.sha256(b"Same thought").hexdigest())
        source = self.store.sources.get(notes[0]["sourceId"])
        self.assertEqual((source["sourceType"], source["importCount"], source["status"]), ("note", 2, "done"))
        self.assertEqual(sorted(source["notes"]), sorted([a, b]))
        self.assertEqual(self.store.sources.original(source["id"])[0], b"Same thought")


class IntakeService(unittest.TestCase):
    def test_http_import_duplicate_invalid_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            def start():
                p = subprocess.Popen([sys.executable, "-m", "services.atlas.server", "--port", "0", "--db", folder + "/atlas.db"], stdout=subprocess.PIPE, text=True)
                return p, p.stdout.readline().strip().split()[-1]
            def post(url, payload):
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req) as r: return r.status, json.load(r)
                except urllib.error.HTTPError as e: return e.code, json.load(e)
            payload = {"name": "fixture.txt", "mediaType": "text/plain", "data": base64.b64encode(TEXT).decode()}
            p, url = start()
            try:
                code, first = post(url + "/api/sources", payload); self.assertEqual(code, 201)
                code, again = post(url + "/api/sources", payload); self.assertEqual(code, 200); self.assertTrue(again["duplicate"])
                code, bad = post(url + "/api/sources", {**payload, "mediaType": "application/pdf"}); self.assertEqual(code, 400)
                self.assertIn("not a valid", bad["error"])
                with raw(folder + "/atlas.db") as db:
                    db.execute("UPDATE sources SET status='processing', extractedText=NULL WHERE id=?", (first["id"],))
            finally: p.kill(); p.wait(); p.stdout.close()
            p, url = start()  # the restart resumes the interrupted source
            try:
                item = json.load(urllib.request.urlopen(f"{url}/api/sources/{first['id']}"))
                self.assertEqual((item["status"], item["extractedText"]), ("done", TEXT.decode()))
                self.assertEqual(urllib.request.urlopen(f"{url}/api/sources/{first['id']}/original").read(), TEXT)
                self.assertEqual(len(json.load(urllib.request.urlopen(url + "/api/sources"))), 1)
                self.assertEqual(post(url + "/api/sources/nope/retry", {})[0], 404)
            finally: p.kill(); p.wait(); p.stdout.close()


if __name__ == "__main__":
    unittest.main()
