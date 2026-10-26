"""Local intake with source provenance (RA-005).

Every note and every local file the owner brings in becomes a Source:
- The original bytes are kept unchanged in a content-addressed folder next to the
  database (sources/ab/<sha256>), outside Git. The database row records hash,
  media type, size, original name, when it arrived and how often it was imported.
- The SHA-256 of the bytes is the identity: importing the same content again
  returns the existing source (duplicate) instead of storing a second copy.
- Processing (reading text out of the original) runs in its own transactions:
  claim → work → result. A crash leaves the row "processing"; the next start puts
  it back to "pending" and resumes it. A failure that may pass (I/O, a missing
  or damaged original) is "failed" and retried up to MAX_ATTEMPTS; content that
  can never be read the way it claims (e.g. not UTF-8) is "invalid" and kept as is.
Imported content is data, never an instruction.
"""
import base64, hashlib, json, os, sqlite3, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 10_000_000
MAX_TEXT = 200_000
MAX_ATTEMPTS = 3
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}
BINARY_TYPES = {  # media type → required leading bytes (None: no fixed signature)
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": None,
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, contentHash TEXT NOT NULL UNIQUE, sourceType TEXT NOT NULL,
  mediaType TEXT NOT NULL, sizeBytes INTEGER NOT NULL, originalName TEXT, uri TEXT,
  localPath TEXT NOT NULL, retrievedAt TEXT NOT NULL, lastImportedAt TEXT NOT NULL,
  importCount INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0, error TEXT, extractedText TEXT, processedAt TEXT);
CREATE TABLE IF NOT EXISTS note_sources (noteId TEXT PRIMARY KEY, sourceId TEXT NOT NULL REFERENCES sources(id));
"""
LIST_FIELDS = "id,contentHash,sourceType,mediaType,sizeBytes,originalName,uri,retrievedAt,lastImportedAt,importCount,status,attempts,error,processedAt"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def migrate(db):
    db.executescript(SCHEMA)
    if db.execute("PRAGMA user_version").fetchone()[0] < 4:
        db.execute("PRAGMA user_version=4")


def blob_path(folder, digest):
    return Path(folder) / digest[:2] / digest


def clean_name(name):
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("File name must be text")
    name = Path(name.replace("\\", "/")).name.strip()
    if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
        raise ValueError("File name must be 1–200 printable characters")
    return name


def validate(data, media_type):
    """Refuse what is not a usable source; nothing is stored for a refused import."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("The source is empty")
    if len(data) > MAX_BYTES:
        raise ValueError(f"The source is larger than {MAX_BYTES // 1_000_000} MB")
    if media_type in TEXT_TYPES:
        return
    if media_type not in BINARY_TYPES:
        raise ValueError(f"Unsupported media type: {media_type!r}")
    signatures = BINARY_TYPES[media_type]
    if media_type == "image/webp":
        ok = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    else:
        ok = any(bytes(data).startswith(s) for s in signatures)
    if not ok:
        raise ValueError(f"The content is not a valid {media_type} file")


def store_blob(folder, data):
    """Write the original once, atomically; returns its SHA-256."""
    digest = hashlib.sha256(data).hexdigest()
    path = blob_path(folder, digest)
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".part-")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(tmp, path)
    except BaseException:
        _discard(tmp)
        raise
    return digest


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def record(db, folder, digest, size, media_type, source_type, name=None, uri=None):
    """Insert the source row or count a repeat import. Returns (id, duplicate)."""
    row = db.execute("SELECT id FROM sources WHERE contentHash=?", (digest,)).fetchone()
    stamp = now()
    if row:
        db.execute("UPDATE sources SET importCount=importCount+1, lastImportedAt=? WHERE id=?", (stamp, row[0]))
        return row[0], True
    identifier = str(uuid.uuid4())
    local = str(blob_path(folder, digest).relative_to(folder))
    db.execute("INSERT INTO sources (id,contentHash,sourceType,mediaType,sizeBytes,originalName,uri,localPath,retrievedAt,lastImportedAt)"
               " VALUES (?,?,?,?,?,?,?,?,?,?)", (identifier, digest, source_type, media_type, size, name, uri, local, stamp, stamp))
    return identifier, False


def extract(data, media_type):
    """Read text out of an original. Raises UnicodeDecodeError/ValueError for unreadable content."""
    if media_type not in TEXT_TYPES:
        return None  # images and PDFs are kept as originals; reading them is a later step
    text = bytes(data).decode("utf-8")
    if media_type == "application/json":
        json.loads(text)
    return text[:MAX_TEXT]


class Intake:
    def __init__(self, connect, folder):
        self.connect, self.folder = connect, Path(folder)

    def add(self, data, media_type, name=None, uri=None, source_type="file"):
        name = clean_name(name)
        validate(data, media_type)
        digest = store_blob(self.folder, bytes(data))
        with self.connect() as db:
            identifier, duplicate = record(db, self.folder, digest, len(data), media_type, source_type, name, uri)
        if not duplicate:
            self.process(identifier)
        return {**self.get(identifier, text=False), "duplicate": duplicate}

    def add_base64(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Send a JSON object")
        try:
            data = base64.b64decode(payload.get("data") or "", validate=True)
        except (ValueError, TypeError):
            raise ValueError("data must be base64")
        uri = payload.get("uri")
        if uri is not None and (not isinstance(uri, str) or len(uri) > 2000):
            raise ValueError("uri must be text up to 2,000 characters")
        return self.add(data, payload.get("mediaType"), payload.get("name"), uri)

    def process(self, identifier):
        """Claim, read and store the result in separate transactions so a crash can be resumed."""
        with self.connect() as db:
            row = db.execute("SELECT contentHash,mediaType,status FROM sources WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            if row[2] == "done":
                return "done"
            db.execute("UPDATE sources SET status='processing', attempts=attempts+1, error=NULL WHERE id=?", (identifier,))
        digest, media_type = row[0], row[1]
        status, text, error = "done", None, None
        try:
            data = blob_path(self.folder, digest).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise OSError("The kept original no longer matches its hash")
            text = extract(data, media_type)
        except (UnicodeDecodeError, ValueError) as exc:
            status, error = "invalid", f"Content cannot be read as {media_type}: {exc.__class__.__name__}"
        except OSError as exc:
            status, error = "failed", f"{exc.__class__.__name__}: {exc}"[:500]
        with self.connect() as db:
            db.execute("UPDATE sources SET status=?, error=?, extractedText=?, processedAt=? WHERE id=?",
                       (status, error, text, now() if status == "done" else None, identifier))
        return status

    def resume(self):
        """After a restart: put interrupted rows back and process everything that may still succeed."""
        with self.connect() as db:
            db.execute("UPDATE sources SET status='pending' WHERE status='processing'")
            ids = [r[0] for r in db.execute(
                "SELECT id FROM sources WHERE status='pending' OR (status='failed' AND attempts<?) ORDER BY retrievedAt", (MAX_ATTEMPTS,))]
        return {i: self.process(i) for i in ids}

    def retry(self, identifier):
        """Owner-triggered retry of one source, also past the automatic attempt limit."""
        return self.process(identifier)

    def note(self, db, note_id, body):
        """Link a saved note to the source holding its exact text (inside the note's transaction)."""
        data = body.encode("utf-8")
        digest = store_blob(self.folder, data)
        if db.execute("SELECT 1 FROM note_sources WHERE noteId=?", (note_id,)).fetchone():
            return None
        identifier, duplicate = record(db, self.folder, digest, len(data), "text/plain", "note")
        db.execute("INSERT INTO note_sources VALUES (?,?)", (note_id, identifier))
        if not duplicate:
            db.execute("UPDATE sources SET status='done', extractedText=?, processedAt=?, attempts=1 WHERE id=?",
                       (body[:MAX_TEXT], now(), identifier))
        return identifier

    def list(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(f"SELECT {LIST_FIELDS} FROM sources ORDER BY lastImportedAt DESC, retrievedAt DESC")]

    def get(self, identifier, text=True):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(f"SELECT {LIST_FIELDS}{',extractedText' if text else ''} FROM sources WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            out = dict(row)
            out["notes"] = [r[0] for r in db.execute("SELECT noteId FROM note_sources WHERE sourceId=?", (identifier,))]
            return out

    def original(self, identifier):
        """The original bytes exactly as imported."""
        item = self.get(identifier, text=False)
        return blob_path(self.folder, item["contentHash"]).read_bytes(), item["mediaType"]
