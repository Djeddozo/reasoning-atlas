"""Talk to Atlas the way you talk to Claude (RA-058): one way in, from the app and from Telegram.

A question gets an answer grounded in the plan (hq.chat; anything new in it is filed as a thought). Everything else
is a thought: saved first, then sorted by Claude into visions, objectives, goals, tasks, ideas, skill-tree nodes or
memories (hq.dump), with a short reply saying what changed and where. Everything it changes is in the brain's change
log, so it can be undone. Links go to intake (read and saved), the words around them are sorted as usual.

Voice is one path too (task #143, with #12 one-tap voice and #14 Whisper on the GPU PC): every recording, from Atlas's
Quick note, the old dashboard or Telegram, is transcribed by `transcribe` through the Whisper tiers in models
(GPU PC → Pi → cloud). Atlas sends the growing recording while you talk (`partial`) so the transcript appears live;
partials are neither kept nor logged. The final recording is kept in memory/voice/; when no tier answers it is
noted 'voice untranscribed' and voiceback files it later. The text is shown for editing; nothing is filed until sent.
"""
import re
import secrets
from datetime import datetime

from . import db, models

VOICE_DIR = db.ROOT / "memory" / "voice"
MAX_AUDIO = 25 * 1024 * 1024
AUDIO_EXT = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav"}
MODES = ("auto", "ask", "file")

QUESTION_WORD = re.compile(r"^(what|why|how|when|where|which|who|whom|should|could|can|do|does|did|is|are|am|was|"
                           r"were|will|would|shall|have|has)\b", re.I)


def is_question(text: str) -> bool:
    """A question is answered; a statement is filed. Judged on the words, as a person would: a message ending in a
    question mark, or one line that opens like a question and does not end like a statement."""
    lines = [x.strip() for x in (text or "").strip().splitlines() if x.strip()]
    if not lines or re.match(r"^(todo|task|idea)\s*:", lines[0], re.I):
        return False
    if lines[-1].endswith("?"):
        return True
    return len(lines) == 1 and bool(QUESTION_WORD.match(lines[0])) and not lines[0].endswith((".", "!"))


def say(con, text: str, source: str = "atlas", mode: str = "auto", actions: dict | None = None,
        views: list[str] | None = None) -> dict:
    """Answer or file. `mode` "ask" always answers (Brain chat), "file" always files (brain dump), "auto" judges the
    words. Returns {"mode": "answer"|"filed", "reply": "..."}."""
    from . import chat, dump, intake
    text = (text or "").strip()
    if not text or len(text) > 8000:
        raise ValueError("Say something of 1–8000 characters")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if mode == "ask" or (mode == "auto" and is_question(text)):
        out = chat.ask(con, text, actions, views)
        said = {"mode": "answer", "reply": out["reply"] + (f"\n\nFiled: {out['added']}" if out.get("added") else "")}
        return {**said, "proposals": out["proposals"], "choices": out["choices"], "mockup": out["mockup"]} if actions else said
    urls, rest = intake.find_urls(text)
    rest = rest if urls else text  # keep line breaks (todo:/idea: lines) when there is no link
    said = [intake.describe(intake.add_link(con, u, note=rest[:300])) for u in urls[:5]]
    if rest:
        said.append(dump.process(con, rest, source=source))
    return {"mode": "filed", "reply": "\n".join(said) or "Nothing to add."}


def keep_untranscribed(con, path, error) -> None:
    """Note a kept recording that no tier could transcribe; voiceback retries it and files the text."""
    db.insert(con, "memories", kind="info", tags="voice untranscribed", source="me",
              content=f"Voice note awaiting transcription: {path}")
    db.log(con, "hq", "voice_untranscribed", f"{path} ({str(error)[:120]})")


def transcribe(con, audio: bytes, ctype: str = "", lang: str | None = None, partial: bool = False,
               prefix: str = "talk") -> str:
    """The transcript of one recording. A partial (the recording so far, while still talking) goes to a temporary
    file that is removed afterwards and is never noted. A final recording is kept in memory/voice/; if no tier
    answers it is noted for voiceback and models.ModelUnavailable is raised. `lang` "" auto-detects, None uses
    `stt_language` from the constitution."""
    if not audio:
        raise ValueError("empty recording")
    if len(audio) > MAX_AUDIO:
        raise ValueError("recording too long")
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    ext = AUDIO_EXT.get((ctype or "").split(";")[0].strip().lower(), ".bin")
    kind = "partial" if partial else prefix
    path = VOICE_DIR / f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}{ext}"
    path.write_bytes(audio)
    if lang is not None:
        lang = "".join(c for c in lang if c.isalpha() or c == "-")[:10]
    try:
        text = models.transcribe(path, lang)
    except models.ModelUnavailable as e:
        if partial:
            path.unlink(missing_ok=True)
        else:
            keep_untranscribed(con, path, e)
        raise
    if partial:
        path.unlink(missing_ok=True)
    else:
        db.log(con, "me", "voice", f"{path.name}: {len(text)} chars")
    return text.strip()
