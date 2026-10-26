"""Voice backlog: re-transcribe stored recordings that could not be transcribed when they arrived.

When no Whisper tier was reachable, the Talk button and Telegram keep the audio in memory/voice/ and log a
memory row tagged 'voice untranscribed' (see engine.talk.transcribe and keep_untranscribed).
`retry` runs each of those again through models.transcribe, once a tier answers, and puts the text into the
brain dump (hq.dump), so it lands in the plan like a live recording would have. Runs from the hourly cycle
(`hq cycle`) and from `hq voice backlog`.

Failure rules: a tier that is unreachable stops the run (try again next hour, nothing is counted against the
recording). A tier that answered but rejected the file or returned no text counts as a strike; after
STRIKES strikes the recording is tagged 'voice failed' and kept on disk for Djeddi to listen to.
"""
import json
from pathlib import Path

from . import db, dump, loop, models

UNTRANSCRIBED = "voice untranscribed"
DONE = "voice transcribed"
FAILED = "voice failed"
MISSING = "voice missing"
STRIKES = 3
PREFIX = "Voice note awaiting transcription: "


def pending(con) -> list[dict]:
    """Untranscribed recordings, oldest first: [{"id", "path"}]."""
    rows = con.execute("SELECT id, content FROM memories WHERE tags = ? ORDER BY id", (UNTRANSCRIBED,)).fetchall()
    out = []
    for r in rows:
        if r["content"].startswith(PREFIX):
            out.append({"id": r["id"], "path": Path(r["content"][len(PREFIX):].strip())})
    return out


def _strikes(con) -> dict:
    return json.loads(db.get_state(con, "voice_backlog_strikes", "{}") or "{}")


def _set_tag(con, row_id: int, tags: str, content: str) -> None:
    db.update(con, "memories", row_id, tags=tags, content=content)


def retry(con, limit: int = 5) -> dict:
    """Try the oldest untranscribed recordings (at most `limit`). Returns counts and the dump summaries."""
    cfg = loop.load_constitution().get("models", {})
    limit = int(cfg.get("stt_backlog_per_run", limit))
    result = {"tried": 0, "done": [], "failed": [], "missing": 0, "reachable": True}
    if not models.stt_available():
        result["reachable"] = False
        return result
    strikes = _strikes(con)
    for item in pending(con)[:limit]:
        rid, path = item["id"], item["path"]
        if not path.is_file():
            _set_tag(con, rid, MISSING, f"Voice note missing on disk: {path}")
            db.log(con, "hq", "voice_missing", str(path))
            result["missing"] += 1
            continue
        result["tried"] += 1
        try:
            text = models.transcribe(path)
        except models.ModelUnavailable as e:
            msg = str(e)
            if "HTTPError" not in msg and "empty transcript" not in msg:  # no tier answered: try again later
                result["reachable"] = False
                break
            strikes[str(rid)] = strikes.get(str(rid), 0) + 1
            if strikes[str(rid)] >= STRIKES:
                _set_tag(con, rid, FAILED, f"Voice note could not be transcribed: {path} ({msg[:120]})")
                db.log(con, "hq", "voice_failed", f"{path} ({msg[:120]})")
                result["failed"].append(path.name)
            db.set_state(con, "voice_backlog_strikes", json.dumps(strikes))
            con.commit()
            continue
        _set_tag(con, rid, DONE, f"Voice note transcribed: {path}")
        summary = dump.process(con, f"Voice note {path.name} (re-transcribed): {text}", source="voice")
        db.log(con, "hq", "voice_backlog", f"{path.name}: {len(text)} chars")
        con.commit()
        result["done"].append({"file": path.name, "summary": summary})
    return result
