"""The .env file: create missing secrets automatically, so setup never needs hand-made tokens.

`ensure_token()` makes sure HQ_TOKEN (dashboard access) exists: if .env has no value for it, a random
one is generated and written to .env (permissions 600). It is also put into this process's environment.
`dashboard_link()` returns the address to open once per browser; the page then remembers the token.
Secrets stay in .env only (never in the database or logs).
"""
import os
import re
import secrets
from pathlib import Path

from . import db

ENV_PATH = db.ROOT / ".env"
PORT = 8765


def read_value(key: str, path: Path = ENV_PATH) -> str:
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].split("#", 1)[0].strip()
    return ""


def set_value(key: str, value: str, path: Path = ENV_PATH) -> None:
    """Set key=value in .env, replacing an existing (possibly empty) line or appending one."""
    text = path.read_text() if path.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{re.escape(key)}=.*$", text, re.M):
        text = re.sub(rf"^{re.escape(key)}=.*$", lambda _: line, text, count=1, flags=re.M)
    else:
        text = text + ("" if not text or text.endswith("\n") else "\n") + line + "\n"
    path.write_text(text)
    path.chmod(0o600)


def ensure_token(path: Path = ENV_PATH) -> tuple[str, bool]:
    """Return (token, created). A token already in the environment wins (as the dashboard uses it);
    otherwise the one in .env; otherwise a new one is created in .env."""
    if os.environ.get("HQ_TOKEN"):
        return os.environ["HQ_TOKEN"], False
    token = read_value("HQ_TOKEN", path)
    created = False
    if not token:
        token = secrets.token_urlsafe(24)
        set_value("HQ_TOKEN", token, path)
        created = True
    os.environ["HQ_TOKEN"] = token
    return token, created


def dashboard_link(path: Path = ENV_PATH, port: int | None = None) -> str:
    token, _ = ensure_token(path)
    url = os.environ.get("HQ_URL") or read_value("HQ_URL", path)  # e.g. https://pi.tailnet.ts.net (Tailscale HTTPS)
    if url:
        return f"{url.rstrip('/')}/?t={token}"
    host = os.environ.get("HQ_HOST") or read_value("HQ_HOST", path) or "localhost"
    port = port or int(os.environ.get("HQ_PORT") or read_value("HQ_PORT", path) or PORT)
    return f"http://{host}:{port}/?t={token}"
