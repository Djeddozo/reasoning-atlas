"""Model router: send each job to the cheapest model that does it well.

Tiers (configured in [models] of the constitution):
  claude_code – Claude Code on the Pi, logged in with Djeddi's Claude subscription (`claude -p`, no tools,
                one turn). No API key needed; uses subscription capacity.
  local  – Ollama on the GPU PC (free, private; bulk work: summaries, sorting, tagging)
  cloud  – Claude via the Anthropic API (best reasoning; planning, extraction, proposals)

`complete(job, system, user)` tries the tiers listed for that job in order and falls back
to the next one if a tier is unreachable (e.g. the GPU PC is asleep or no API key is set).
Raises ModelUnavailable when no tier works, so callers can degrade gracefully.

`transcribe(path)` is the speech-to-text counterpart (job "transcribe"), routed the same way:
  local  – a Whisper HTTP endpoint on a PC (HQ_WHISPER_URL, OpenAI-compatible /v1/audio/transcriptions,
           e.g. faster-whisper-server). The Pi never transcribes on its own CPU (owner, 2026-10-25); when
           Atlas runs on the PC, the PC's screen transcribes on the PC itself (services/atlas/localvoice.py).
  cloud  – any OpenAI-compatible speech-to-text service (HQ_STT_CLOUD_URL + HQ_STT_CLOUD_KEY in .env,
           model `cloud_stt_model`); off until Djeddi sets both, so no voice leaves the tailnet by default
Both send the whole recording at once with a language hint (`stt_language` or the caller's) and a short
vocabulary prompt (`stt_prompt`). Batch transcription of the full recording by a large model is how the
Claude app works, and is why it beats in-browser streaming recognition (see docs/VOICE.md).

`see(job, system, prompt, image_path)` reads an image (screenshot, photo of a page), routed like
`complete`: local = a vision model in Ollama (`local_vision_model`), cloud = Claude with the image.
"""
import base64
import json
import os
import secrets
import urllib.request
from pathlib import Path

from . import loop


class ModelUnavailable(RuntimeError):
    pass


class Truncated(ModelUnavailable):
    """The model answered, but the answer was cut off by its output limit (#188)."""


# Room to answer per job (#188): the whole-plan jobs return long JSON; the default was 2500 and cut them off.
# Order: [models.jobs.<job>] max_tokens, then [models] max_tokens, then these. A cut-off answer is retried once with
# double the room, never above the ceiling.
JOB_MAX_TOKENS = {"reevaluate": 12000, "doover": 12000}
DEFAULT_MAX_TOKENS = 6000
MAX_TOKENS_CEILING = 32000
# Seconds per cloud call: [models.jobs.<job>] timeout (or [models] timeout), never above the ceiling (#188).
DEFAULT_TIMEOUT, TIMEOUT_CEILING = 300, 900
# Jobs whose answer must be one JSON object: an unparseable answer from Claude Code counts as cut off.
JSON_JOBS = {"reevaluate", "doover", "revisit", "overlap", "triage", "link", "dump", "research", "revalidate"}


def max_tokens(cfg: dict, job: str) -> int:
    value = cfg.get("max_tokens") or JOB_MAX_TOKENS.get(job, DEFAULT_MAX_TOKENS)
    return max(256, min(int(value), MAX_TOKENS_CEILING))


def timeout(cfg: dict) -> int:
    return max(10, min(int(cfg.get("timeout") or DEFAULT_TIMEOUT), TIMEOUT_CEILING))


def is_json(text: str) -> bool:
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start == -1 or end < start:
        return False
    try:
        json.loads(text[start:end + 1])
        return True
    except ValueError:
        return False


def why_failed(e: Exception) -> str:
    """For a job's error line: a cut-off answer is not the same as no model being reachable."""
    if isinstance(e, Truncated):
        return f"answer was cut off ({str(e)[:120]})"
    return f"no model reachable ({str(e)[:120]})"


# Usage tracking: entry points (CLI, bot, dashboard) set this to usage.recorder(); tests leave it None.
# Called as on_usage(job, tier, model, tokens_in, tokens_out, cost_usd).
on_usage = None
_job = "?"  # the job of the call in progress, for the usage record


def _record(tier: str, model: str, tokens_in: int, tokens_out: int, cfg: dict) -> None:
    if on_usage:
        from . import usage
        cost = usage.estimate(cfg, tokens_in, tokens_out) if tier == "cloud" else 0.0
        on_usage(_job, tier, model, tokens_in, tokens_out, cost)


def _check_kill() -> None:
    from . import usage
    if usage.killed():
        raise ModelUnavailable("kill switch is on: paid model calls are blocked")


def _post(url: str, body: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# Used for the monthly cap when no prices are set in the constitution, so the cap always works.
# Roughly Claude Sonnet list prices (USD per million tokens); set your own in [models] for exact figures.
CAP_FALLBACK_USD_PER_MTOK = (3.0, 15.0)


def _over_cap(cfg: dict) -> bool:
    """True when this month's API spend reached [models] monthly_cap_usd (0 = no cap). Only counts when tracked.

    Spend is the larger of the recorded cost and the month's tokens priced at the configured rates
    (or CAP_FALLBACK_USD_PER_MTOK when none are set), so a zero price never disables the cap."""
    cap = float(cfg.get("monthly_cap_usd", 0) or 0)
    if not cap or on_usage is None:
        return False
    from datetime import date
    from . import db
    try:
        con = db.connect(db.DB_PATH)
        cost, t_in, t_out = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM model_usage WHERE tier = 'cloud' AND ts >= ?", (date.today().replace(day=1).isoformat(),)).fetchone()
        con.close()
    except Exception:
        return False
    rate_in = float(cfg.get("cloud_usd_per_mtok_in", 0) or 0)
    rate_out = float(cfg.get("cloud_usd_per_mtok_out", 0) or 0)
    if not rate_in and not rate_out:
        rate_in, rate_out = CAP_FALLBACK_USD_PER_MTOK
    return max(cost, (t_in * rate_in + t_out * rate_out) / 1e6) >= cap


def _cloud(cfg: dict, system: str, user: str) -> str:
    _check_kill()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ModelUnavailable("no ANTHROPIC_API_KEY")
    if _over_cap(cfg):
        raise ModelUnavailable("monthly API cap reached")
    model = cfg.get("model") or cfg.get("cloud_model", "claude-sonnet-5-5")
    think = int(cfg.get("thinking_tokens", 0) or 0)
    body = {"model": model, "max_tokens": max(int(cfg.get("max_tokens", DEFAULT_MAX_TOKENS) or DEFAULT_MAX_TOKENS), think + 2000 if think else 0),
            "system": system, "messages": [{"role": "user", "content": user}]}
    if think:
        body["thinking"] = {"type": "enabled", "budget_tokens": max(1024, think)}  # time to think before answering
    data = _post("https://api.anthropic.com/v1/messages", body,
                 {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout(cfg))
    u = data.get("usage") or {}
    _record("cloud", model, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), cfg)
    if data.get("stop_reason") == "max_tokens":
        raise Truncated(f"stopped at max_tokens {body['max_tokens']}")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type", "text") == "text")


def _local(cfg: dict, system: str, user: str) -> str:
    url = os.environ.get("HQ_OLLAMA_URL", cfg.get("local_url", "http://localhost:11434"))
    data = _post(f"{url}/api/chat",
                 {"model": cfg.get("local_model", "qwen3:14b"), "stream": False,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                 {}, 300)
    _record("local", cfg.get("local_model", ""), int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0), cfg)
    if data.get("done_reason") == "length":
        raise Truncated("stopped at the local model's output limit")
    return data["message"]["content"]


def _claude_code(cfg: dict, system: str, user: str) -> str:
    """Claude Code on this machine, on Djeddi's subscription: one turn, no tools, no project context."""
    _check_kill()
    if os.environ.get("HQ_NO_CLAUDE_CODE"):  # set by the test suite: never call the real Claude in tests
        raise ModelUnavailable("Claude Code disabled (HQ_NO_CLAUDE_CODE)")
    import subprocess
    import tempfile
    from . import devloop
    exe = devloop.claude_path()
    if not exe:
        raise ModelUnavailable("Claude Code is not installed")
    quiet = Path(tempfile.gettempdir()) / "hq-claude"   # an empty folder, so no CLAUDE.md or files are read
    quiet.mkdir(exist_ok=True)
    env = devloop.agent_env(Path(os.devnull))            # no API keys or tokens: uses the subscription login
    if int(cfg.get("thinking_tokens", 0) or 0):          # thinking time on the subscription route too (Claude Code reads this)
        env["MAX_THINKING_TOKENS"] = str(int(cfg["thinking_tokens"]))
    if cfg.get("max_tokens"):                            # room to answer, as on the cloud tier (#188)
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(cfg["max_tokens"]))
    try:
        # --tools "" and --strict-mcp-config: no built-in or MCP tools, so text in the prompt can never run anything
        pick = ["--model", str(cfg["claude_code_model"])] if cfg.get("claude_code_model") else []  # e.g. "opus"
        r = subprocess.run([exe, "-p", "--output-format", "json", "--max-turns", "1", "--tools", "",
                            "--strict-mcp-config", *pick, "--system-prompt", system],
                           input=user, cwd=quiet, env=env, capture_output=True, text=True,
                           timeout=int(cfg.get("claude_code_timeout", 300)))
    except subprocess.TimeoutExpired:
        raise ModelUnavailable("Claude Code timed out")
    try:
        out = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        raise ModelUnavailable(f"Claude Code: {(r.stderr or r.stdout).strip()[:200]}")
    if r.returncode != 0 or out.get("is_error"):
        raise ModelUnavailable(f"Claude Code: {str(out.get('result') or r.stderr)[:200]}")
    u = out.get("usage") or {}
    if on_usage:
        tokens_in = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        on_usage(_job, "claude_code", "claude-code", tokens_in, int(u.get("output_tokens") or 0),
                 float(out.get("total_cost_usd") or 0))
    result = str(out.get("result") or "")
    if out.get("stop_reason") == "max_tokens" or (cfg.get("expect_json") and not is_json(result)):
        raise Truncated("stopped at the output limit" if out.get("stop_reason") == "max_tokens" else "the JSON answer is incomplete")
    return result


TIERS = {"cloud": _cloud, "claude_code": _claude_code, "local": _local}


def complete(job: str, system: str, user: str) -> str:
    global _job
    _job = job
    cfg = loop.load_constitution().get("models", {})
    order = cfg.get("routing", {}).get(job, ["cloud", "local"])
    # per-job settings from [models.jobs.<job>] (owner, 2026-10-26): e.g. the chat on Opus with thinking time
    cfg = {**cfg, **(cfg.get("jobs", {}).get(job) or {})}
    cfg["max_tokens"], cfg["expect_json"] = max_tokens(cfg, job), job in JSON_JOBS
    from . import budget
    paid_ok = any(t in budget.PAID for t in order) and budget.allowed(job)  # automatic jobs: daily paid-call budget
    errors, held, cut = [], False, False
    for tier in order:
        if tier in budget.PAID and not paid_ok:
            errors.append(f"{tier}: daily budget for '{job}' reached")
            held = True
            continue
        try:
            try:
                return TIERS[tier](cfg, system, user)
            except Truncated:  # cut off: once more with double the room (#188)
                return TIERS[tier]({**cfg, "max_tokens": min(cfg["max_tokens"] * 2, MAX_TOKENS_CEILING)}, system, user)
        except Truncated:
            errors.append(f"{tier}: answer was cut off, also with {min(cfg['max_tokens'] * 2, MAX_TOKENS_CEILING)} tokens")
            cut = True
        except Exception as e:
            errors.append(f"{tier}: {e.__class__.__name__}")
    if held:
        budget.note_deferred(job)
    raise (Truncated if cut else ModelUnavailable)("; ".join(errors) or "no tiers configured")


def parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start != -1 else {}


# ---------------- speech-to-text ----------------

def _multipart(fields: dict, file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = "hq" + secrets.token_hex(12)
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _stt_fields(cfg: dict, model: str, language: str) -> dict:
    fields = {"model": model, "response_format": "json"}
    if language:
        fields["language"] = language
    if cfg.get("stt_prompt"):
        fields["prompt"] = cfg["stt_prompt"]
    return fields


def _stt_post(url: str, fields: dict, path: Path, headers: dict, timeout: int) -> str:
    body, ctype = _multipart(fields, "file", path)
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/audio/transcriptions", data=body,
                                 headers={"Content-Type": ctype, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("text", "").strip()


def _stt_gpu(cfg: dict, path: Path, language: str = "", timeout: int = 300) -> str:
    """Whisper over HTTP, usually on the GPU PC (HQ_WHISPER_URL)."""
    url = os.environ.get("HQ_WHISPER_URL", cfg.get("whisper_url", ""))
    if not url:
        raise ModelUnavailable("no HQ_WHISPER_URL set")
    return _stt_post(url, _stt_fields(cfg, cfg.get("whisper_model", "large-v3-turbo"), language), path, {}, timeout)


def _stt_local(cfg: dict, path: Path, language: str = "") -> str:
    """Whisper on a PC (HQ_WHISPER_URL). The Pi never transcribes on its own CPU: too slow (owner, 2026-10-25)."""
    if os.environ.get("HQ_WHISPER_URL") or cfg.get("whisper_url"):
        return _stt_gpu(cfg, path, language)
    raise ModelUnavailable("no Whisper server set (HQ_WHISPER_URL); the Pi does not transcribe on its CPU")


def _stt_cloud(cfg: dict, path: Path, language: str = "") -> str:
    url, key = os.environ.get("HQ_STT_CLOUD_URL", ""), os.environ.get("HQ_STT_CLOUD_KEY", "")
    if not (url and key):
        raise ModelUnavailable("no HQ_STT_CLOUD_URL / HQ_STT_CLOUD_KEY set")
    _check_kill()
    model = cfg.get("cloud_stt_model", "whisper-1")
    text = _stt_post(url, _stt_fields(cfg, model, language), path, {"Authorization": f"Bearer {key}"}, 120)
    _record("cloud", model, 0, 0, cfg)
    return text


STT_TIERS = {"local": _stt_local, "cloud": _stt_cloud}
SETUP_TIERS = {"gpu": _stt_gpu, "cloud": _stt_cloud}  # one concrete tier each, for engine.voicesetup


def stt_available() -> list[str]:
    """The speech-to-text tiers configured on this machine (not whether they are reachable right now)."""
    tiers = []
    if os.environ.get("HQ_WHISPER_URL") or loop.load_constitution().get("models", {}).get("whisper_url"):
        tiers.append("local")
    if os.environ.get("HQ_STT_CLOUD_URL") and os.environ.get("HQ_STT_CLOUD_KEY"):
        tiers.append("cloud")
    return tiers


def transcribe(path, language: str | None = None) -> str:
    """Speech-to-text for a local audio file. Raises ModelUnavailable when no tier works.
    `language` is a code like "nl" or "en-GB"; None uses `stt_language` from the constitution, "" auto-detects."""
    global _job
    _job = "transcribe"
    cfg = loop.load_constitution().get("models", {})
    lang = (cfg.get("stt_language", "") if language is None else language).split("-")[0].lower()
    order = cfg.get("routing", {}).get("transcribe", ["local", "cloud"])
    errors = []
    for tier in order:
        try:
            text = STT_TIERS[tier](cfg, Path(path), lang)
            if text:
                return text
            errors.append(f"{tier}: empty transcript")
        except Exception as e:
            errors.append(f"{tier}: {e.__class__.__name__}")
    raise ModelUnavailable("; ".join(errors) or "no tiers configured")


# ---------------- vision ----------------

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
               ".gif": "image/gif"}


def _see_cloud(cfg: dict, system: str, prompt: str, path: Path) -> str:
    _check_kill()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ModelUnavailable("no ANTHROPIC_API_KEY")
    if _over_cap(cfg):
        raise ModelUnavailable("monthly API cap reached")
    img = {"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES.get(path.suffix.lower(), "image/jpeg"),
                                       "data": base64.b64encode(path.read_bytes()).decode()}}
    data = _post("https://api.anthropic.com/v1/messages",
                 {"model": cfg.get("cloud_model", "claude-sonnet-5-5"), "max_tokens": 1500, "system": system,
                  "messages": [{"role": "user", "content": [img, {"type": "text", "text": prompt}]}]},
                 {"x-api-key": key, "anthropic-version": "2023-06-01"}, 120)
    u = data.get("usage") or {}
    _record("cloud", cfg.get("cloud_model", ""), int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), cfg)
    return "".join(b.get("text", "") for b in data.get("content", []))


def _see_local(cfg: dict, system: str, prompt: str, path: Path) -> str:
    model = cfg.get("local_vision_model", "")
    if not model:
        raise ModelUnavailable("no local_vision_model configured")
    url = os.environ.get("HQ_OLLAMA_URL", cfg.get("local_url", "http://localhost:11434"))
    data = _post(f"{url}/api/chat",
                 {"model": model, "stream": False,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": prompt, "images": [base64.b64encode(path.read_bytes()).decode()]}]},
                 {}, 300)
    return data["message"]["content"]


SEE_TIERS = {"cloud": _see_cloud, "local": _see_local}


def see(job: str, system: str, prompt: str, image_path) -> str:
    """Describe or read an image. Raises ModelUnavailable when no tier works."""
    global _job
    _job = job
    cfg = loop.load_constitution().get("models", {})
    order = cfg.get("routing", {}).get(job, ["local", "cloud"])
    errors = []
    for tier in order:
        try:
            text = SEE_TIERS[tier](cfg, system, prompt, Path(image_path))
            if text.strip():
                return text
            errors.append(f"{tier}: empty reply")
        except Exception as e:
            errors.append(f"{tier}: {e.__class__.__name__}")
    raise ModelUnavailable("; ".join(errors) or "no tiers configured")
