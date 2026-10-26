"""Whisper setup from the app: pick a speech-to-text tier, connect it, and test it (dashboard System tab, `hq voice`).

Tiers (the routing order stays `[models.routing] transcribe`: local first, then cloud):
  gpu   – a Whisper server on the GPU PC (faster-whisper-server / speaches, OpenAI-compatible) → HQ_WHISPER_URL
  (no Pi CPU tier: too slow; the owner removed it on 2026-10-25. Atlas on the PC transcribes on the PC itself.)
  cloud – an OpenAI-compatible hosted service → HQ_STT_CLOUD_URL + HQ_STT_CLOUD_KEY. Costs money and sends audio
          to that provider, so it is configured only when Djeddi enters the key himself.

`save()` writes the address and key to .env (the only place secrets live) and to this process's environment, so
the dashboard uses them at once; the Telegram bot reads them after its next restart. Keys are never returned,
logged or stored in the database. `test()` runs a short transcription on one tier and records the outcome
(without any transcript text) in state `voice_check`, so the status survives a reload.
"""
import io
import json
import os
import socket
import tempfile
import time
import urllib.error
import wave
from pathlib import Path
from urllib.parse import urlparse

from . import db, envfile, loop, models

TIERS = {
    "gpu": {"label": "GPU PC", "env": ["HQ_WHISPER_URL"]},
    "cloud": {"label": "Hosted API", "env": ["HQ_STT_CLOUD_URL", "HQ_STT_CLOUD_KEY"]},
}
VOICE_DIR = db.ROOT / "memory" / "voice"


def _cfg() -> dict:
    return loop.load_constitution().get("models", {})


def _checks(con) -> dict:
    try:
        return json.loads(db.get_state(con, "voice_check", "{}") or "{}")
    except ValueError:
        return {}


def status(con) -> dict:
    """What is configured per tier, which tiers transcription will use, and the last test of each."""
    cfg, checks = _cfg(), _checks(con)
    gpu_url = os.environ.get("HQ_WHISPER_URL", cfg.get("whisper_url", ""))
    cloud_url = os.environ.get("HQ_STT_CLOUD_URL", "")
    tiers = {
        "gpu": {"configured": bool(gpu_url), "url": gpu_url, "model": cfg.get("whisper_model", "large-v3-turbo")},
        "cloud": {"configured": bool(cloud_url and os.environ.get("HQ_STT_CLOUD_KEY")), "url": cloud_url,
                  "key_set": bool(os.environ.get("HQ_STT_CLOUD_KEY")), "model": cfg.get("cloud_stt_model", "whisper-1")},
    }
    for name, t in tiers.items():
        t["label"] = TIERS[name]["label"]
        t["check"] = checks.get(name)
    return {"tiers": tiers, "order": cfg.get("routing", {}).get("transcribe", ["local", "cloud"]),
            "active": models.stt_available()}


def _clean_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    for suffix in ("/v1/audio/transcriptions", "/v1"):  # people paste the full endpoint; Atlas adds it itself
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc or any(c.isspace() for c in url):
        raise ValueError("Enter an address like http://100.64.0.5:8000 (http or https, no path).")
    return url


def save(con, tier: str, url: str = "", key: str = "", env_path: Path | None = None) -> str:
    """Connect a tier (store its address and key in .env). An empty url removes the tier."""
    env_path = env_path or envfile.ENV_PATH
    if tier not in ("gpu", "cloud"):
        raise ValueError("tier must be gpu or cloud (the Pi does not transcribe on its CPU)")
    values = {}
    if not url.strip():
        values = {k: "" for k in TIERS[tier]["env"]}
        what = "removed"
    else:
        values[TIERS[tier]["env"][0]] = _clean_url(url)
        if tier == "cloud":
            key = key.strip()
            if not key and not os.environ.get("HQ_STT_CLOUD_KEY"):
                raise ValueError("The hosted API needs a key.")
            if key:
                if any(c.isspace() for c in key) or len(key) > 500:
                    raise ValueError("That key looks malformed.")
                values["HQ_STT_CLOUD_KEY"] = key
        what = f"set to {values[TIERS[tier]['env'][0]]}"
    for k, v in values.items():
        envfile.set_value(k, v, env_path)
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    checks = _checks(con)
    checks.pop(tier, None)  # an earlier test no longer says anything about the new address
    db.set_state(con, "voice_check", json.dumps(checks))
    db.log(con, "me", "voice_setup", f"{tier} {what}")  # never the key
    con.commit()
    return f"{TIERS[tier]['label']} {what}." + (" Run a test next." if url.strip() else "")


def sample_clip() -> Path:
    """One second of silence as 16 kHz mono WAV: proves the endpoint accepts audio, without sending speech."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    f = tempfile.NamedTemporaryFile(prefix="hq-stt-test-", suffix=".wav", delete=False)
    f.write(buf.getvalue()); f.close()
    return Path(f.name)


def latest_recording(voice_dir: Path | None = None) -> Path | None:
    d = voice_dir or VOICE_DIR
    files = [p for p in d.glob("*") if p.is_file()] if d.exists() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _explain(tier: str, e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return "the service rejected the key"
        if e.code == 404:
            return "no /v1/audio/transcriptions at this address (is it a Whisper server, not Ollama?)"
        return f"the server answered HTTP {e.code}"
    if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)):
        return "not reachable" + (" (is the GPU PC on and the Whisper server running?)" if tier == "gpu" else "")
    if isinstance(e, models.ModelUnavailable):
        return str(e)
    return f"{e.__class__.__name__}: {str(e)[:120]}"


def test(con, tier: str, voice_dir: Path | None = None) -> dict:
    """Run one test transcription on `tier`. Local tiers use the latest Talk / Telegram recording when there is
    one (real speech, stays on the tailnet); the hosted API always gets the silent clip, so no private audio leaves
    for a test. Returns {"ok", "message", "seconds", "words"}; the transcript itself is not stored."""
    if tier not in TIERS:
        raise ValueError("tier must be gpu or cloud (the Pi does not transcribe on its CPU)")
    rec = latest_recording(voice_dir) if tier != "cloud" else None
    clip = rec or sample_clip()
    cfg = _cfg()
    start = time.monotonic()
    try:
        if tier == "gpu":
            text = models._stt_gpu(cfg, clip, "", timeout=120)
        else:
            text = models.SETUP_TIERS[tier](cfg, clip, "")
        secs = round(time.monotonic() - start, 1)
        words = len(text.split())
        ok = bool(text) or rec is None  # silence may come back empty; that still proves the tier works
        msg = (f"Works: {words} words from your last recording in {secs} s." if rec and text
               else "Reachable, but your last recording came back empty." if rec
               else f"Works: the service accepted a test clip in {secs} s. Use Talk to hear the quality.")
    except Exception as e:
        secs, words, ok = round(time.monotonic() - start, 1), 0, False
        msg = f"Failed: {_explain(tier, e)}."
    finally:
        if rec is None:
            clip.unlink(missing_ok=True)
    result = {"ok": ok, "message": msg, "seconds": secs, "words": words, "at": db.now()}
    checks = _checks(con)
    checks[tier] = result
    db.set_state(con, "voice_check", json.dumps(checks))
    db.log(con, "me", "voice_test", f"{tier}: {msg}")
    con.commit()
    return result
