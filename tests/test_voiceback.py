"""Voice backlog: recordings stored while no Whisper tier was reachable are transcribed later and dumped into the plan."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import db, dump, loop, models, talk, voiceback


class VoiceBacklogTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con); loop.seed(self.con)
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        p = mock.patch.object(talk, "VOICE_DIR", self.dir); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(models, "stt_available", return_value=["local"]); p.start(); self.addCleanup(p.stop)
        self.dump = mock.patch.object(dump, "process", return_value="Added 1 task.").start()
        self.addCleanup(mock.patch.stopall)

    def store(self, name="a.webm", content=b"AUDIO"):
        """A recording as the dashboard and Telegram leave it when transcription fails (file + memory row)."""
        path = self.dir / name
        path.write_bytes(content)
        db.insert(self.con, "memories", kind="info", tags="voice untranscribed", source="me",
                  content=f"Voice note awaiting transcription: {path}")
        self.con.commit()
        return path

    def tags(self):
        return [r[0] for r in self.con.execute("SELECT tags FROM memories WHERE tags LIKE 'voice%' ORDER BY id")]

    def test_pending_lists_stored_recordings_oldest_first(self):
        first = self.store("a.webm"); second = self.store("b.webm", b"B")
        self.assertEqual([p["path"] for p in voiceback.pending(self.con)], [first, second])

    def test_transcribed_recording_goes_into_the_dump_and_is_marked_done(self):
        path = self.store()
        with mock.patch.object(models, "transcribe", return_value="bel de tandarts") as t:
            result = voiceback.retry(self.con)
        self.assertEqual(t.call_args[0][0], path)
        self.assertEqual(result["done"], [{"file": "a.webm", "summary": "Added 1 task."}])
        self.assertIn("bel de tandarts", self.dump.call_args[0][1])
        self.assertEqual(self.dump.call_args[1], {"source": "voice"})
        self.assertEqual(self.tags(), [voiceback.DONE])
        self.assertEqual(voiceback.pending(self.con), [])

    def test_no_reachable_tier_stops_run_and_counts_no_strike(self):
        self.store("a.webm"); self.store("b.webm", b"B")
        with mock.patch.object(models, "transcribe", side_effect=models.ModelUnavailable("local: URLError")) as t:
            result = voiceback.retry(self.con)
        self.assertEqual(t.call_count, 1)  # the first failure ends the run
        self.assertFalse(result["reachable"])
        self.assertEqual(len(voiceback.pending(self.con)), 2)
        self.assertEqual(voiceback._strikes(self.con), {})
        self.dump.assert_not_called()

    def test_no_configured_tier_does_nothing(self):
        self.store()
        with mock.patch.object(models, "stt_available", return_value=[]), \
                mock.patch.object(models, "transcribe") as t:
            result = voiceback.retry(self.con)
        t.assert_not_called()
        self.assertFalse(result["reachable"])

    def test_server_rejecting_a_file_is_struck_then_parked(self):
        self.store()
        err = models.ModelUnavailable("local: HTTPError")
        with mock.patch.object(models, "transcribe", side_effect=err):
            for _ in range(voiceback.STRIKES - 1):
                voiceback.retry(self.con)
                self.assertEqual(self.tags(), [voiceback.UNTRANSCRIBED])
            result = voiceback.retry(self.con)
        self.assertEqual(result["failed"], ["a.webm"])
        self.assertEqual(self.tags(), [voiceback.FAILED])
        self.assertEqual(voiceback.pending(self.con), [])

    def test_rejected_file_does_not_block_the_next_one(self):
        self.store("a.webm"); self.store("b.webm", b"B")

        def fake(path, *a):
            if path.name == "a.webm":
                raise models.ModelUnavailable("local: HTTPError")
            return "tweede opname"
        with mock.patch.object(models, "transcribe", side_effect=fake):
            result = voiceback.retry(self.con)
        self.assertEqual([d["file"] for d in result["done"]], ["b.webm"])
        self.assertEqual(voiceback._strikes(self.con), {str(voiceback.pending(self.con)[0]["id"]): 1})

    def test_deleted_recording_is_marked_missing(self):
        path = self.store()
        path.unlink()
        with mock.patch.object(models, "transcribe") as t:
            result = voiceback.retry(self.con)
        t.assert_not_called()
        self.assertEqual(result["missing"], 1)
        self.assertEqual(self.tags(), [voiceback.MISSING])

    def test_batch_size_comes_from_the_constitution(self):
        for i in range(3):
            self.store(f"r{i}.webm", bytes([i]))
        with mock.patch.object(loop, "load_constitution", return_value={"models": {"stt_backlog_per_run": 2}}), \
                mock.patch.object(models, "transcribe", return_value="x"):
            result = voiceback.retry(self.con)
        self.assertEqual(len(result["done"]), 2)
        self.assertEqual(len(voiceback.pending(self.con)), 1)

    def test_pending_ignores_rows_it_did_not_write(self):
        db.insert(self.con, "memories", kind="info", tags="voice untranscribed", source="me", content="something else")
        self.assertEqual(voiceback.pending(self.con), [])


if __name__ == "__main__":
    unittest.main()
