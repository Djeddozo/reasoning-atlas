"""Sandbox for outside code (task #18): run code found online in a throwaway Docker container before Djeddi
decides on it by hand (decisions #69, #71, #75).

What the container gets, and what it never gets:
  - no network at all (`--network none`): it cannot reach the internet, the Pi, the hub, the PC or Tailscale
  - no credentials: no environment from the host, no .env, no home folder, no Docker socket, no SSH keys
  - the code read-only at /src, copied into a size-limited scratch folder /work that disappears afterwards
  - a read-only root filesystem, no Linux capabilities, no privilege escalation, an unprivileged user
  - limits on time, memory, CPU and processes (`[sandbox]` in the constitution, never above CEILINGS)
  - only images already on the Pi (`--pull never`): pulling a new image is installing outside code itself

Before a run, a preflight refuses folders that hold secrets or reach into the Pi's own files, and a rule-based
scan lists what the code tries to do (network, processes, credentials, install hooks, obfuscation) so the
review has something concrete to read. A run shows behaviour, not safety: it never approves anything. The
result is attached to the task's outside-code approval as evidence.

The security gate (task #75) adds two checks next to the scan: where the code comes from and how trusted that
source is (trust: high only for publishers Djeddi listed or a small vetted default set; popularity alone is
never enough), and its licence (permissive, copyleft, restricted or none). Policy: Atlas writes its own code by
default; outside code or a new dependency is proposed only when highly trusted, and is always approved by Djeddi
by hand. MANUAL_ONLY approvals never time out into 'approved' and cannot be approved by an agent
(hq.gatekeeper, hq.skilltree.decide, hq.needs). After a build, new_outside_code() lists new dependencies the
agent added; the dev loop refuses to open a PR with them unless the task went through the gate (hq.devloop).

No model calls, stdlib only. `hq sandbox check` proves the isolation on this machine.
"""
import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from . import db

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {"image": "python:3.13-slim", "timeout_s": 120, "memory_mb": 512, "cpus": 1.0, "pids": 128,
            "scratch_mb": 256}
CEILINGS = {"timeout_s": 900, "memory_mb": 2048, "cpus": 2.0, "pids": 512, "scratch_mb": 1024}
MAX_FILES, MAX_BYTES = 20000, 200 * 1024 * 1024  # what a source folder may hold
OUTPUT_TAIL = 8000
NOBODY = "65534:65534"
STAGE = Path.home() / ".cache" / "atlas-sandbox"


# Files that hold credentials: a folder containing one is never mounted.
SECRET_NAMES = re.compile(r"^(\.env(\..*)?|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|\.netrc|\.git-credentials|\.npmrc|\.pypirc|"
                          r"credentials(\.json)?|.*\.(pem|key|p12|pfx|kdbx|keystore|jks))$", re.I)

# Rule-based scan: (category, pattern, why). Reads only; never executes anything.
SCAN_RULES = [
    ("network", r"\b(socket\.|requests\.|urllib|http\.client|httpx|aiohttp|fetch\(|XMLHttpRequest|axios|net\.connect)",
     "opens network connections"),
    ("network", r"\b(curl|wget|nc|ncat)\s", "calls a download or network tool"),
    ("process", r"\b(subprocess|os\.system|os\.popen|child_process|Runtime\.getRuntime|exec\.Command)\b", "starts other programs"),
    ("dynamic", r"\b(eval|exec)\s*\(|new Function\(|__import__\(", "runs code built at run time"),
    ("obfuscation", r"(base64\.b64decode|atob\(|fromCharCode|(\\x[0-9a-f]{2}){8,}|[A-Za-z0-9+/]{200,}={0,2})",
     "decodes or hides code"),
    ("credentials", r"(\.ssh|\.aws|\.gnupg|\.config/gcloud|os\.environ|process\.env|getenv\(|keyring|/etc/passwd|/etc/shadow)",
     "reads credentials or environment"),
    ("install-hook", r"(\"(pre|post)install\"\s*:|cmdclass\s*=|setup_requires)", "runs code during installation"),
    ("persistence", r"(crontab|systemctl|/etc/init\.d|\.bashrc|\.profile|authorized_keys)", "tries to stay on the machine"),
    ("pipe-to-shell", r"(curl|wget)[^\n|]*\|\s*(ba|z)?sh\b", "pipes a download into a shell"),
]
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".rb", ".pl", ".php", ".go", ".rs", ".java",
                 ".json", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".txt", ".md", ".ps1", ".bat", ""}
MAX_FLAGS = 200


def cfg(rules: dict | None = None) -> dict:
    """Sandbox limits: `[sandbox]` in the constitution over the defaults, never above CEILINGS."""
    if rules is None:
        from . import loop
        rules = loop.load_constitution()
    conf = dict(DEFAULTS)
    for k, v in (rules.get("sandbox") or {}).items():
        if k in DEFAULTS:
            conf[k] = v
    for k, top in CEILINGS.items():
        try:
            conf[k] = max(min(type(top)(conf[k]), top), type(top)(1) if k != "cpus" else 0.1)
        except (TypeError, ValueError):
            conf[k] = DEFAULTS[k]
    conf["image"] = str(conf["image"])
    return conf


# ---------------- before a run ----------------

def _protected() -> list[Path]:
    home = Path.home().resolve()
    return [Path("/").resolve(), home, ROOT, home / ".ssh", home / ".config", home / ".claude"]


def preflight(src) -> dict:
    """Is this folder safe to mount? Refuses secrets, the Pi's own folders and oversized trees."""
    path = Path(src).expanduser()
    problems, files, size, digest = [], 0, 0, None
    if not path.is_dir():
        return {"path": str(path), "problems": [f"{path} is not a folder"], "files": 0, "bytes": 0, "digest": ""}
    path = path.resolve()
    for p in _protected():
        if path == p or p.is_relative_to(path):
            problems.append(f"{path} is or contains {p}: copy only the outside code into its own folder")
        elif path.is_relative_to(ROOT):
            problems.append(f"{path} is inside Atlas's own repository: copy the outside code elsewhere")
            break
    if problems:
        return {"path": str(path), "problems": problems, "files": 0, "bytes": 0, "digest": ""}
    import hashlib
    digest = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        rel = f.relative_to(path)
        if f.is_symlink():
            target = os.readlink(f)
            if os.path.isabs(target) or ".." in Path(target).parts:
                problems.append(f"{rel} is a link pointing outside the folder ({target})")
            continue
        if SECRET_NAMES.match(f.name):
            problems.append(f"{rel} looks like a credential file; remove it before testing")
        if not f.is_file():
            continue
        files += 1
        size += f.stat().st_size
        if files > MAX_FILES or size > MAX_BYTES:
            problems.append(f"more than {MAX_FILES} files or {MAX_BYTES // 2**20} MB; test a smaller part")
            break
        digest.update(str(rel).encode() + b"\0")
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                digest.update(chunk)
    return {"path": str(path), "problems": problems[:20], "files": files, "bytes": size, "digest": digest.hexdigest()}


def scan(src) -> list[dict]:
    """What the code tries to do, by rule: [{category, file, line, why, text}]. Untrusted text stays data."""
    path, flags = Path(src).expanduser().resolve(), []
    rules = [(c, re.compile(p, re.I), w) for c, p, w in SCAN_RULES]
    for f in sorted(path.rglob("*")):
        if f.is_symlink() or not f.is_file() or f.suffix.lower() not in TEXT_SUFFIXES or f.stat().st_size > 1 << 20:
            continue
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            for cat, rx, why in rules:
                if rx.search(line):
                    flags.append({"category": cat, "file": str(f.relative_to(path)), "line": n, "why": why,
                                  "text": line.strip()[:160]})
                    if len(flags) >= MAX_FLAGS:
                        return flags
    return flags


def summarize_flags(flags: list[dict]) -> str:
    counts = {}
    for fl in flags:
        counts[fl["category"]] = counts.get(fl["category"], 0) + 1
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "nothing flagged"


# ---------------- security gate: trust and licence (task #75) ----------------

MANUAL_ONLY = "outside_code"  # approval category: only Djeddi's own tap settles it, never a timeout or an agent
KNOWN_HOSTS = {"github.com", "gitlab.com", "codeberg.org", "pypi.org", "files.pythonhosted.org", "www.npmjs.com",
               "npmjs.com", "registry.npmjs.org", "crates.io", "huggingface.co"}
TRUSTED_OWNERS = {"python", "pypa", "psf", "anthropics"}  # vetted defaults; more via [security_gate] trusted_owners
STALE_DAYS = 365

LICENSES = [  # (name, kind, pattern over a licence file or package metadata); first match wins
    ("AGPL-3.0", "copyleft", r"GNU AFFERO GENERAL PUBLIC LICENSE|\bAGPL"),
    ("LGPL", "weak-copyleft", r"GNU LESSER GENERAL PUBLIC LICENSE|GNU LIBRARY GENERAL PUBLIC|\bLGPL"),
    ("GPL", "copyleft", r"GNU GENERAL PUBLIC LICENSE|\bGPL-?[23]|\bGPL\b"),
    ("MPL-2.0", "weak-copyleft", r"Mozilla Public License|\bMPL-?2"),
    ("SSPL / BSL / non-commercial", "restricted", r"Server Side Public License|\bSSPL|Business Source License|\bBUSL|"
                                                  r"non-?commercial|Commons Clause|Elastic License"),
    ("Apache-2.0", "permissive", r"Apache License|\bApache-2\.0\b|\bApache\b"),
    ("MIT", "permissive", r"\bMIT License\b|Permission is hereby granted, free of charge|\bMIT\b"),
    ("BSD", "permissive", r"\bBSD\b|Redistribution and use in source and binary forms"),
    ("ISC", "permissive", r"\bISC\b"),
    ("PSF", "permissive", r"Python Software Foundation License|\bPSF\b"),
    ("Unlicense / CC0", "permissive", r"unlicense|\bCC0\b|public domain"),
    ("Zlib", "permissive", r"\bzlib\b"),
]
LICENSE_FILES = re.compile(r"^(licen[cs]e|copying|notice)(\.(md|txt|rst))?$|^licen[cs]e[-_.]", re.I)
LICENSE_ADVICE = {"permissive": "can be used with attribution",
                  "weak-copyleft": "changes to it must stay under its licence: check how it would be used",
                  "copyleft": "would put Atlas under the same licence: needs your explicit review",
                  "restricted": "limits use (commercial or hosting): do not use without your review",
                  "none": "no licence found: no right to use it, write it ourselves"}


def gate_cfg(rules: dict | None = None) -> dict:
    """`[security_gate]` in the constitution: extra trusted publishers. It can only add trust, never skip a check."""
    if rules is None:
        from . import loop
        rules = loop.load_constitution()
    own = (rules.get("security_gate") or {}).get("trusted_owners") or []
    return {"trusted_owners": TRUSTED_OWNERS | {str(o).strip().lower() for o in own if str(o).strip()}}


def license_check(src) -> dict:
    """Which licence the folder carries: {name, kind, file, advice}. Reads text only."""
    path = Path(src).expanduser()
    found = []
    if path.is_dir():
        for f in sorted(path.iterdir()):
            if f.is_file() and not f.is_symlink() and LICENSE_FILES.match(f.name):
                found.append((f.name, f.read_text(errors="replace")[:20000]))
        for meta in ("package.json", "pyproject.toml", "setup.cfg", "setup.py", "Cargo.toml"):
            f = path / meta
            if f.is_file() and not f.is_symlink():
                m = re.search(r"""(?i)(?<![\w-])["']?licen[cs]e["']?\s*[:=]\s*(\{[^}\n]*\}|["'][^"'\n]+["'])""", f.read_text(errors="replace")[:50000])
                if m:
                    found.append((meta, m.group(1)))
    for name, body in found:
        for lic, kind, rx in LICENSES:
            if re.search(rx, body, re.I):
                return {"name": lic, "kind": kind, "file": name, "advice": LICENSE_ADVICE[kind]}
    if found:
        return {"name": "unrecognised", "kind": "restricted", "file": found[0][0],
                "advice": "a licence Atlas does not recognise: read it before any use"}
    return {"name": "none", "kind": "none", "file": "", "advice": LICENSE_ADVICE["none"]}


def _repo_meta(con, url: str) -> dict:
    """Stars and last update of a GitHub repository as fetched earlier by node research (untrusted data)."""
    key = url.rstrip("/").lower()
    try:
        rows = con.execute("SELECT sources FROM node_research ORDER BY id DESC LIMIT 200").fetchall()
    except sqlite3.OperationalError:
        return {}
    for (raw,) in rows:
        try:
            repos = json.loads(raw or "{}").get("repos") or []
        except (json.JSONDecodeError, AttributeError):
            continue
        for r in repos:
            if isinstance(r, dict) and str(r.get("url", "")).rstrip("/").lower() == key:
                return r
    return {}


def trust_check(con, origin: str, conf: dict | None = None, today: str | None = None) -> dict:
    """How trusted the source is: {level high|medium|low, source, why}. Only 'high' counts as highly trusted:
    a publisher on the trusted list. Popularity raises an unknown publisher to 'medium' at most."""
    from urllib.parse import urlparse
    conf = conf or gate_cfg()
    origin = (origin or "").strip()
    m = re.search(r"https?://[^\s)>\]\"']+", origin)
    url = m.group(0).rstrip(".,;") if m else ""
    if not url:
        return {"level": "low", "source": origin or "unknown", "why": "no link to where the code comes from"}
    u = urlparse(url)
    host, parts = (u.hostname or "").lower(), [x for x in u.path.split("/") if x]
    if host not in KNOWN_HOSTS:
        return {"level": "low", "source": url, "why": f"{host or 'the host'} is not a known code host"}
    if host == "pypi.org" and len(parts) >= 2 and parts[0] == "project":
        owner = parts[1].lower()
    elif host in ("www.npmjs.com", "npmjs.com") and len(parts) >= 2 and parts[0] == "package":
        owner = parts[1].lower().lstrip("@")
    else:
        owner = parts[0].lower() if parts else ""
    if owner in conf["trusted_owners"]:
        return {"level": "high", "source": url, "why": f"{owner} is a trusted publisher"}
    meta = _repo_meta(con, url) if host == "github.com" else {}
    if not meta:
        return {"level": "low", "source": url, "why": f"{owner or 'the publisher'} is not on the trusted list and "
                                                       "there is no reputation data"}
    stars, updated = int(meta.get("stars") or 0), str(meta.get("updated") or "")
    from datetime import date
    try:
        age = (date.fromisoformat(today or date.today().isoformat()) - date.fromisoformat(updated[:10])).days
    except ValueError:
        age = None
    facts = f"{stars} stars, last update {updated[:10] or 'unknown'}"
    if stars >= 1000 and age is not None and age <= STALE_DAYS:
        return {"level": "medium", "source": url, "why": f"popular and maintained ({facts}), but {owner} is not a "
                                                          "trusted publisher"}
    return {"level": "low", "source": url, "why": f"little reputation ({facts})"}


def origin_of(con, task_id: int) -> str:
    """Where a task's outside code comes from: the latest sandbox run's origin, else the task's 'Outside code:' line."""
    from . import research
    found = runs(con, task_id, limit=1)
    if found and found[0]["origin"]:
        return found[0]["origin"]
    row = con.execute("SELECT description FROM tasks WHERE id = ?", (task_id,)).fetchone()
    m = re.search(re.escape(research.OUTSIDE_MARK) + r"\s*([^\n]+)", (row[0] or "") if row else "")
    return m.group(1).split(". ")[0].strip() if m else ""


def gate_report(con, task_id: int) -> dict:
    """Everything Djeddi needs to decide on a task's outside code: where it is from, trust, licence, scan, run."""
    origin = origin_of(con, task_id)
    trust = trust_check(con, origin)
    found = runs(con, task_id, limit=1)
    r = found[0] if found else None
    lic = (r or {}).get("license") or {}
    if r and not lic and r["status"] != "refused" and Path(r["source"]).is_dir():
        lic = license_check(r["source"])
    advice = []
    if trust["level"] != "high":
        advice.append("not highly trusted: Atlas advises writing it ourselves")
    if not lic:
        advice.append("licence not checked yet (it is checked during the sandbox run)")
    elif lic["kind"] != "permissive":
        advice.append(f"licence {lic['name']}: {lic['advice']}")
    if not r or r["status"] == "refused":
        advice.append("not tested in the sandbox")
    elif r["flags"]:
        advice.append("read the scan findings before deciding")
    return {"task_id": task_id, "origin": origin, "trust": trust, "license": lic,
            "run": {k: r[k] for k in ("id", "status", "exit_code", "source", "digest")} if r else None,
            "scan": summarize_flags(r["flags"]) if r else "", "advice": advice,
            "verdict": "may be considered" if not advice else "write it ourselves unless you see a reason",
            "approval": "only by your own tap; Atlas never approves outside code itself"}


# ---------------- after a build: new outside code in the agent's work ----------------

MANIFESTS = re.compile(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile(\.lock)?|"
                       r"package\.json|go\.mod|Cargo\.toml|Gemfile|composer\.json)$")
PY_IMPORT = re.compile(r"^\+\s*(?:import\s+([A-Za-z_]\w*)|from\s+([A-Za-z_]\w*)[\w.]*\s+import\b)")


def _deps(name: str, text: str) -> set[str]:
    """The dependency names a manifest declares ('' text = the file does not exist)."""
    if not text:
        return set()
    base = name.rsplit("/", 1)[-1]
    try:
        if base == "package.json":
            data = json.loads(text)
            return {f"npm:{k}" for sec in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
                    for k in (data.get(sec) or {})}
        if base == "pyproject.toml":
            import tomllib
            data = tomllib.loads(text)
            proj = data.get("project") or {}
            reqs = list(proj.get("dependencies") or []) + list((data.get("build-system") or {}).get("requires") or [])
            reqs += [r for extra in (proj.get("optional-dependencies") or {}).values() for r in extra]
            return {"py:" + re.split(r"[\s<>=!~;\[(]", r.strip(), maxsplit=1)[0].lower() for r in reqs if r.strip()}
    except (ValueError, TypeError, AttributeError):
        return {f"file:{name}"}
    if base.startswith("requirements") and base.endswith(".txt"):
        return {"py:" + re.split(r"[\s<>=!~;\[(]", ln.strip(), maxsplit=1)[0].lower()
                for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("#", "-"))}
    return {f"{base}:{ln.strip()}" for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")}


def _local_modules(tree: Path) -> set[str]:
    names = set()
    for f in tree.rglob("*.py"):
        if "node_modules" in f.parts or ".git" in f.parts:
            continue
        names.add(f.stem)
        names.update(p for p in f.relative_to(tree).parts[:-1])
    return names


def new_outside_code(tree, start: str, runner=subprocess.run) -> list[str]:
    """New dependencies the agent's commits (start..HEAD) add: manifest entries and non-stdlib Python imports."""
    import sys
    tree = Path(tree)

    def git(*args) -> str:
        r = runner(["git", *args], cwd=tree, capture_output=True, text=True, timeout=120)
        return r.stdout if r.returncode == 0 else ""

    found = []
    for name in git("diff", "--name-only", "--diff-filter=AMR", f"{start}..HEAD").splitlines():
        if MANIFESTS.search(name) and "node_modules/" not in name:
            for dep in sorted(_deps(name, git("show", f"HEAD:{name}")) - _deps(name, git("show", f"{start}:{name}"))):
                found.append(f"{name}: new dependency {dep.split(':', 1)[1]}")
    local = _local_modules(tree)
    seen = set()
    for line in git("diff", "-U0", f"{start}..HEAD", "--", "*.py").splitlines():
        m = PY_IMPORT.match(line)
        mod = m and (m.group(1) or m.group(2))
        if mod and mod not in sys.stdlib_module_names and mod not in local and mod != "__future__" and mod not in seen:
            seen.add(mod)
            found.append(f"Python import of {mod}, which is not in the standard library")
    return found


# ---------------- the container ----------------

def docker_args(name: str, src: str | None, command: str, conf: dict) -> list[str]:
    """The full `docker run` line. Every isolation flag lives here, so tests can hold it to the promise."""
    args = ["docker", "run", "--rm", "--name", name, "--label", "atlas.sandbox=1", "--pull", "never",
            "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", NOBODY, "--ipc", "private", "--hostname", "sandbox",
            "--pids-limit", str(conf["pids"]), "--memory", f"{conf['memory_mb']}m",
            "--memory-swap", f"{conf['memory_mb']}m", "--cpus", str(conf["cpus"]),
            "--tmpfs", f"/work:rw,exec,nosuid,nodev,size={conf['scratch_mb']}m,uid=65534,gid=65534,mode=700",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--env", "HOME=/work", "--workdir", "/work"]
    if src:
        args += ["--mount", f"type=bind,source={src},target=/src,readonly"]
        script = 'cp -R /src/. /work/ && cd /work && exec sh -c "$0"'
    else:
        script = 'exec sh -c "$0"'
    return args + [conf["image"], "sh", "-c", script, command]


def image_present(image: str, runner=subprocess.run) -> bool:
    try:
        r = runner(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _execute(name: str, args: list[str], timeout: int, runner) -> tuple[str, int | None, float, str]:
    start = time.monotonic()
    try:
        r = runner(args, capture_output=True, text=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired as e:
        try:
            runner(["docker", "kill", name], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
        out = ((e.stdout or "") if isinstance(e.stdout, str) else "") + ((e.stderr or "") if isinstance(e.stderr, str) else "")
        return "timeout", None, time.monotonic() - start, out[-OUTPUT_TAIL:] + f"\n[stopped after {timeout} s]"
    except OSError as e:
        return "error", None, time.monotonic() - start, f"could not start docker: {e}"
    out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    if r.returncode == 125:  # docker itself refused (bad image, daemon down)
        return "error", 125, time.monotonic() - start, out[-OUTPUT_TAIL:]
    return ("passed" if r.returncode == 0 else "failed"), r.returncode, time.monotonic() - start, out[-OUTPUT_TAIL:]


@contextlib.contextmanager
def staged(src: str):
    """A temporary copy the container's unprivileged user can read; the original folder stays untouched.
    Removed afterwards: it is a scratch copy, not data."""
    STAGE.mkdir(parents=True, exist_ok=True)  # not /tmp: a service with PrivateTmp would hide it from Docker
    tmp = Path(tempfile.mkdtemp(prefix="run-", dir=STAGE))
    try:
        copy = tmp / "src"
        shutil.copytree(src, copy, symlinks=True)
        for p in [copy, *copy.rglob("*")]:
            if not p.is_symlink():
                p.chmod(0o755 if p.is_dir() or p.stat().st_mode & 0o100 else 0o644)
        tmp.chmod(0o755)
        yield str(copy)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _record(con, **fields) -> int:
    fields.setdefault("created", db.now())
    for k in ("flags", "problems", "license"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    return db.insert(con, "sandbox_runs", **fields)


def run(con, src, command: str, task_id: int | None = None, skill_id: int | None = None, origin: str = "",
        image: str | None = None, actor: str = "me", conf: dict | None = None, runner=subprocess.run) -> dict:
    """Test outside code in the sandbox and record the result. Never approves anything."""
    from . import usage
    conf = dict(conf or cfg())
    if image:
        conf["image"] = image
    command = (command or "").strip()
    base = {"source": str(src), "origin": origin, "image": conf["image"], "command": command,
            "task_id": task_id, "skill_id": skill_id, "actor": actor}
    if task_id and not con.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
        raise ValueError(f"task #{task_id} does not exist")
    if skill_id and not con.execute("SELECT 1 FROM skills WHERE id = ?", (skill_id,)).fetchone():
        raise ValueError(f"skill #{skill_id} does not exist")
    problems = [] if command else ["give the command to run, e.g. python3 main.py --help"]
    if usage.killed():
        problems.append("the kill switch is on")
    pre = preflight(src)
    problems += pre["problems"]
    readable = not pre["problems"] and Path(pre["path"]).is_dir()
    flags = scan(pre["path"]) if readable else []
    base.update(source=pre["path"], digest=pre["digest"], flags=flags, license=license_check(pre["path"]) if readable else {})
    if not problems and not image_present(conf["image"], runner):
        problems.append(f"image {conf['image']} is not on this machine; pulling one is installing outside code "
                        "and needs your approval first")
    if problems:
        rid = _record(con, **base, status="refused", problems=problems)
        db.log(con, actor, "sandbox_refused", f"run #{rid}: {'; '.join(problems)[:300]}")
        con.commit()
        return _get(con, rid)
    rid = _record(con, **base, status="running")
    con.commit()
    name = f"atlas-sandbox-{rid}"
    with staged(pre["path"]) as copy:
        status, code, secs, out = _execute(name, docker_args(name, copy, command, conf), conf["timeout_s"], runner)
    db.update(con, "sandbox_runs", rid, status=status, exit_code=code, seconds=round(secs, 1), output=out,
              problems=json.dumps(warnings(out)))
    if skill_id and status in ("passed", "failed", "timeout"):
        db.update(con, "skills", skill_id, status="sandboxed", notes=f"sandbox run #{rid}: {status}")
    db.log(con, actor, "sandbox_run", f"run #{rid} {status} (exit {code}) {pre['path']}: {command[:120]}"
           + (f" task #{task_id}" if task_id else "") + f"; scan: {summarize_flags(flags)}")
    con.commit()
    return _get(con, rid)


# The probe runs inside the container and reports what it can reach. Python, because the default image has it.
PROBE = r"""
import json, os, socket
def reach(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close(); return True
    except OSError:
        return False
def writable(p):
    try:
        open(os.path.join(p, '.atlas-probe'), 'w').close(); os.remove(os.path.join(p, '.atlas-probe')); return True
    except OSError:
        return False
try:
    socket.getaddrinfo('github.com', 443); dns = True
except OSError:
    dns = False
ifaces = [i for i in os.listdir('/sys/class/net') if i != 'lo'] if os.path.isdir('/sys/class/net') else []
secret = [k for k in os.environ if any(w in k.upper() for w in ('TOKEN', 'KEY', 'SECRET', 'PASS', 'AUTH'))]
print(json.dumps({"uid": os.getuid(), "dns": dns, "internet": reach('1.1.1.1', 443),
  "host": any(reach(h, p) for h in ('172.17.0.1', '192.168.1.1', '100.100.100.100') for p in (22, 80, 8765)),
  "interfaces": ifaces, "secret_env": secret, "root_writable": writable('/'), "work_writable": writable('/work'),
  "docker_socket": os.path.exists('/var/run/docker.sock'), "home_mounted": os.path.exists('/home')
    and bool(os.listdir('/home'))}))
"""

EXPECT = {"uid": lambda v: v != 0, "dns": lambda v: v is False, "internet": lambda v: v is False,
          "host": lambda v: v is False, "interfaces": lambda v: v == [], "secret_env": lambda v: v == [],
          "root_writable": lambda v: v is False, "work_writable": lambda v: v is True,
          "docker_socket": lambda v: v is False, "home_mounted": lambda v: v is False}
EXPLAIN = {"uid": "runs as an unprivileged user", "dns": "cannot look up names", "internet": "cannot reach the internet",
           "host": "cannot reach the Pi, router or Tailscale", "interfaces": "has no network interface",
           "secret_env": "sees no secret environment variables", "root_writable": "cannot change its own system",
           "work_writable": "has a scratch folder to work in", "docker_socket": "cannot control Docker",
           "home_mounted": "sees no home folder"}


NO_MEMORY_LIMIT = "does not support memory limit"  # Docker's warning when the kernel lacks the memory cgroup
MEMORY_FIX = ("the memory limit is not enforced: the kernel has no memory cgroup. On a Raspberry Pi add "
              "'cgroup_enable=memory' to /boot/firmware/cmdline.txt and restart the Pi")


def check_result(report: dict, image_env: set[str] = frozenset()) -> list[tuple[str, bool, str]]:
    """Each isolation promise, kept or not. Variables the image itself declares are not from the host."""
    report = dict(report)
    report["secret_env"] = [k for k in report.get("secret_env", []) if k not in image_env]
    return [(k, k in report and ok(report[k]), EXPLAIN[k]) for k, ok in EXPECT.items()]


def image_env(image: str, runner=subprocess.run) -> set[str]:
    try:
        r = runner(["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image],
                   capture_output=True, text=True, timeout=30)
        return {e.split("=", 1)[0] for e in json.loads(r.stdout or "[]") or []}
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return set()


def warnings(output: str) -> list[str]:
    """Resource limits Docker had to drop on this machine: not an escape, but the Pi is less protected."""
    return [MEMORY_FIX] if NO_MEMORY_LIMIT in (output or "") else []


def selfcheck(con, conf: dict | None = None, actor: str = "me", runner=subprocess.run) -> dict:
    """Prove the isolation on this machine: a probe inside the sandbox tries to escape and reports."""
    conf = dict(conf or cfg())
    base = {"source": "selfcheck", "image": conf["image"], "command": "isolation probe", "actor": actor}
    if not image_present(conf["image"], runner):
        rid = _record(con, **base, status="refused", problems=[f"image {conf['image']} is not on this machine"])
        con.commit()
        return _get(con, rid) | {"checks": [], "warnings": []}
    rid = _record(con, **base, status="running")
    con.commit()
    name = f"atlas-sandbox-{rid}"
    command = "python3 -c " + _shquote(PROBE)
    status, code, secs, out = _execute(name, docker_args(name, None, command, conf), 60, runner)
    checks = []
    try:
        report = json.loads(out.strip().splitlines()[0]) if status == "passed" else None
    except (ValueError, IndexError):
        report = None
    if report is not None:
        checks = check_result(report, image_env(conf["image"], runner))
        status = "passed" if all(ok for _, ok, _ in checks) else "failed"
    elif status == "passed":
        status = "error"
    warns = warnings(out)
    db.update(con, "sandbox_runs", rid, status=status, exit_code=code, seconds=round(secs, 1), output=out,
              flags=json.dumps([{"category": k, "ok": ok, "why": why} for k, ok, why in checks]),
              problems=json.dumps(warns))
    db.log(con, actor, "sandbox_selfcheck", f"run #{rid} {status}" + (f"; {len(warns)} warning(s)" if warns else ""))
    con.commit()
    return _get(con, rid) | {"checks": checks, "warnings": warns}


def _shquote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ---------------- reading ----------------

def _get(con, rid: int) -> dict:
    r = dict(con.execute("SELECT * FROM sandbox_runs WHERE id = ?", (rid,)).fetchone())
    r["flags"], r["problems"] = json.loads(r["flags"] or "[]"), json.loads(r["problems"] or "[]")
    r["license"] = json.loads(r.get("license") or "{}")
    return r


def runs(con, task_id: int | None = None, limit: int = 20) -> list[dict]:
    sql, args = "SELECT id FROM sandbox_runs", ()
    if task_id:
        sql, args = sql + " WHERE task_id = ?", (task_id,)
    try:
        ids = [r[0] for r in con.execute(sql + " ORDER BY id DESC LIMIT ?", args + (limit,))]
    except sqlite3.OperationalError:  # a read-only copy made before this table existed
        return []
    return [_get(con, i) for i in ids]


def evidence(con, task_id: int) -> str:
    """The security gate's findings for the outside-code approval: source, trust, licence, scan and sandbox run."""
    g = gate_report(con, task_id)
    lic = g["license"]
    parts = [f"Code from {g['trust']['source']}.",
             f"Trust: {g['trust']['level']} ({g['trust']['why']}).",
             f"Licence: {lic['name']} ({lic['advice']})." if lic else "Licence: not checked yet."]
    r = runs(con, task_id, limit=1)
    if not r:
        parts.append(f"Not tested in the sandbox yet: hq sandbox run <folder> --task {task_id} -- <command>.")
    elif r[0]["status"] == "refused":
        parts.append(f"Sandbox run #{r[0]['id']} was refused: {'; '.join(r[0]['problems'])[:200]}.")
    else:
        r = r[0]
        parts.append(f"Sandbox run #{r['id']}: {r['status']}" + (f" (exit {r['exit_code']})" if r["exit_code"] is not None else "")
                     + f"; scan: {summarize_flags(r['flags'])}. It shows behaviour, not safety.")
    if g["advice"] and g["advice"][0].startswith("not highly trusted"):
        parts.append("Atlas advises writing it ourselves.")
    parts.append("Only your own tap approves outside code.")
    return " ".join(parts)


def text_run(r: dict) -> str:
    lines = [f"Sandbox run #{r['id']}: {r['status']}" + (f", exit {r['exit_code']}" if r["exit_code"] is not None else "")
             + (f", {r['seconds']:g} s" if r["seconds"] is not None else ""),
             f"  source {r['source']}" + (f" ({r['origin']})" if r["origin"] else "") + f", image {r['image']}"]
    if r["digest"]:
        lines.append(f"  digest {r['digest'][:16]}")
    lines += [f"  {'refused' if r['status'] == 'refused' else 'warning'}: {p}" for p in r["problems"]]
    if r.get("license"):
        lines.append(f"  licence: {r['license']['name']} ({r['license']['advice']})")
    if r["source"] != "selfcheck" and r["flags"]:
        lines.append(f"  scan: {summarize_flags(r['flags'])}")
        lines += [f"    {f['category']}: {f['file']}:{f['line']} {f['why']}" for f in r["flags"][:15]]
    if r["output"]:
        lines += ["  output (last lines, untrusted):"] + ["    " + ln for ln in r["output"].strip().splitlines()[-20:]]
    return "\n".join(lines)


def text(con) -> str:
    found = runs(con)
    if not found:
        return ("No sandbox runs yet. Prove the isolation: hq sandbox check\n"
                "Test outside code: hq sandbox run <folder> [--task N] -- <command>")
    return "\n".join(f"#{r['id']} {r['created'][:16]} {r['status']:8} "
                     + (f"task #{r['task_id']} " if r["task_id"] else "") + f"{r['source']}: {r['command'][:60]}"
                     for r in found)
