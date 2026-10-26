import os, shutil, sqlite3, subprocess, tempfile, unittest
from datetime import date
from pathlib import Path
from unittest import mock

from engine import backup, db


@unittest.skipUnless(shutil.which("openssl") and shutil.which("git"), "needs openssl and git")
class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src = self.base / "hq.db"
        con = db.connect(self.src); db.init(con)
        db.insert(con, "memories", kind="fact", content="private fact", created=db.now()); con.commit(); con.close()
        self.remote = self.base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        for name, value in (("LOCAL", self.base / "local"), ("CLONE", self.base / "clone")):
            p = mock.patch.object(backup, name, value); p.start(); self.addCleanup(p.stop)

    def test_encrypted_snapshot_reaches_the_private_repository_and_restores(self):
        key = "test-key-not-a-secret"
        plain = backup.snapshot(self.src, backup.LOCAL)
        blob = backup.encrypt(plain, key)
        self.assertNotIn(b"private fact", blob.read_bytes())  # nothing readable leaves the Pi
        backup.push(blob, "owner/private", backup.CLONE, str(self.remote))
        files = subprocess.run(["git", "ls-tree", "--name-only", "main"], cwd=self.remote, capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(files), ["README.md", blob.name])
        out = backup.decrypt(blob, self.base / "restored.db", key)
        with sqlite3.connect(out) as c:
            self.assertEqual(c.execute("SELECT content FROM memories").fetchone()[0], "private fact")
        with self.assertRaises(RuntimeError):
            backup.decrypt(blob, self.base / "wrong.db", "wrong key")
        self.assertEqual(oct(os.stat(blob).st_mode & 0o777), "0o600")

    def test_one_commit_with_seven_days_and_one_per_month(self):
        names = [f"hq-2026-{m:02d}-{d:02d}.db.enc" for m in (9, 10) for d in range(1, 31)]
        keep = backup._keep(names)
        self.assertEqual(len(keep), 7 + 1)  # the last week (October) plus the newest of September
        self.assertIn("hq-2026-09-30.db.enc", keep)

    def test_without_a_key_nothing_leaves_the_pi(self):
        con = db.connect(self.src)
        with mock.patch.dict(os.environ, {"ATLAS_BACKUP_KEY": "", "ATLAS_BACKUP_REPO": "owner/private"}), \
             mock.patch.object(backup, "push") as push:
            msg = backup.run(con, self.src)
        push.assert_not_called()
        self.assertIn("no cloud copy", msg)
        self.assertEqual(backup.status(con)["last"]["action"], "backup_local")
        con.close()


if __name__ == "__main__":
    unittest.main()
