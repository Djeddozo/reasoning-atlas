"""The PC side of the hub connection (RA-030).

The hub's address and token live in runtime/hub.json on this computer (ignored by Git, never sent to
the browser). Atlas's local service calls the hub with the token and passes results to the screen.
"""
import json, os, time, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "runtime" / "hub.json"
TIMEOUT = 4
_cache = {"at": 0.0, "value": None}


def load(path=None):
    try:
        data = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
        return data if data.get("url") and data.get("token") else None
    except (OSError, ValueError):
        return None


def _save(data, path=None):
    path = Path(path or CONFIG)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _request(config, path, timeout=TIMEOUT):
    req = urllib.request.Request(config["url"].rstrip("/") + path, headers={"Authorization": f"Bearer {config['token']}"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def check(config):
    """Ask the hub for its health. Never raises; returns a status for the screen (no token in it)."""
    started = time.monotonic()
    try:
        health = _request(config, "/v1/health")
        return {"configured": True, "url": config["url"], "reachable": True,
                "latencyMs": round((time.monotonic() - started) * 1000), "commit": health.get("commit"), "version": health.get("version"),
                "engineDatabase": health.get("engineDatabase"), "functions": health.get("functions", []), "actions": health.get("actions", []),
                "runningCommit": health.get("runningCommit"), "restartNeeded": bool(health.get("restartNeeded"))}
    except urllib.error.HTTPError as exc:
        reason = "The hub refused the token" if exc.code == 401 else f"The hub answered {exc.code}"
        return {"configured": True, "url": config["url"], "reachable": False, "error": reason}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"configured": True, "url": config["url"], "reachable": False,
                "error": "The hub cannot be reached (is the Pi on and Tailscale connected on both devices?)"}


def status(path=None, max_age=10):
    config = load(path)
    if not config:
        return {"configured": False, "reachable": False}
    if _cache["value"] and _cache["value"].get("url") == config["url"] and time.monotonic() - _cache["at"] < max_age:
        return _cache["value"]
    value = check(config)
    _cache.update(at=time.monotonic(), value=value)
    return value


def configure(url, token, path=None):
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The hub address must look like http://<pi-address>:4320")
    if not isinstance(token, str) or len(token.strip()) < 20:
        raise ValueError("Paste the full hub token printed on the Pi")
    config = {"url": f"{parts.scheme}://{parts.netloc}", "token": token.strip()}
    result = check(config)
    if result["reachable"]:
        _save(config, path)
        _cache.update(at=time.monotonic(), value=result)
    return result


def forget(path=None):
    try:
        Path(path or CONFIG).unlink()
    except FileNotFoundError:
        pass
    _cache.update(at=0.0, value=None)


def call(function_id, query="", path=None):
    """Run an Engine function through the hub. Returns (http status, body)."""
    config = load(path)
    if not config:
        return 409, {"error": "No hub connected yet. Connect one under Systems."}
    try:
        return 200, _request(config, f"/v1/functions/{function_id}" + (f"?{query}" if query else ""), timeout=8)
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
        except ValueError:
            body = {"error": f"The hub answered {exc.code}"}
        return (502 if exc.code == 401 else exc.code), body
    except (urllib.error.URLError, OSError, ValueError):
        _cache.update(at=0.0, value=None)
        return 504, {"error": "The hub cannot be reached right now"}


def act(action_id, body, path=None):
    """Run an Engine action through the hub (the Engine's own function for that button). Returns (status, body)."""
    config = load(path)
    if not config:
        return 409, {"error": "No hub connected yet. Connect one under Systems."}
    req = urllib.request.Request(config["url"].rstrip("/") + f"/v1/actions/{action_id}", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {config['token']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as response:  # the Engine re-plans after an answer
            return 200, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
        except ValueError:
            body = {"error": f"The hub answered {exc.code}"}
        return (502 if exc.code == 401 else exc.code), body
    except (urllib.error.URLError, OSError, ValueError):
        _cache.update(at=0.0, value=None)
        return 504, {"error": "The hub cannot be reached right now; nothing was changed"}


def stream(action_id, body, path=None, timeout=600):
    """A streamed Engine action (the Cortex chat): yields each event line (bytes) as the hub sends it."""
    config = load(path)
    if not config:
        yield (json.dumps({"t": "error", "d": "No hub connected yet. Connect one under Systems."}) + "\n").encode()
        return
    req = urllib.request.Request(config["url"].rstrip("/") + f"/v1/stream/{action_id}", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {config['token']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for line in response:
                yield line
    except urllib.error.HTTPError as exc:
        try:
            msg = json.load(exc).get("error")
        except ValueError:
            msg = f"The hub answered {exc.code}"
        yield (json.dumps({"t": "error", "d": msg}) + "\n").encode()
    except (urllib.error.URLError, OSError, ValueError):
        _cache.update(at=0.0, value=None)
        yield (json.dumps({"t": "error", "d": "The hub cannot be reached right now"}) + "\n").encode()
