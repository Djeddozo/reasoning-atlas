"""Arsenal: everything Djeddi has built, indexed so Atlas can reuse it.

Register project folders once (`hq arsenal add <path>`); `hq arsenal scan` walks them and records
every module, function, class and script with its docstring/comment, signature and location.
Search (`hq arsenal find "currency conversion"`) is full-text over names, docs and paths, with
snake_case and camelCase names split into words so "parse invoice" finds `parseInvoiceLines`.

The brain dump and `hq think` query the arsenal automatically, so new plans point at existing work.
Only reads files; never executes or modifies them.
"""
import ast
import re
from pathlib import Path

from . import db

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next", "target", ".idea", ".vscode"}
CODE_EXT = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".sh", ".ps1", ".sql"}
MAX_BYTES = 500_000

JS_PATTERNS = [
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)()", re.M),
]


def words(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return " ".join(w.lower() for w in re.split(r"[_\W]+", s) if w)


def _comment_above(lines: list[str], idx: int) -> str:
    out = []
    for line in reversed(lines[max(0, idx - 6):idx]):
        s = line.strip()
        if s.startswith(("//", "#", "*", "/*")):
            out.insert(0, s.lstrip("/#* ").strip())
        else:
            break
    return " ".join(x for x in out if x)


def scan_python(src: str):
    tree = ast.parse(src)
    yield "module", "", "", ast.get_docstring(tree) or "", 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            yield "function", node.name, f"{node.name}({args})", ast.get_docstring(node) or "", node.lineno
        elif isinstance(node, ast.ClassDef):
            yield "class", node.name, node.name, ast.get_docstring(node) or "", node.lineno


def scan_js(src: str):
    lines = src.splitlines()
    seen = set()
    for pat in JS_PATTERNS:
        for m in pat.finditer(src):
            name = m.group(1)
            line = src.count("\n", 0, m.start()) + 1
            if (name, line) in seen:
                continue
            seen.add((name, line))
            kind = "class" if "class" in m.group(0) else "function"
            sig = f"{name}({m.group(2).strip()})" if kind == "function" else name
            yield kind, name, sig, _comment_above(lines, line - 1), line


def scan_file(path: Path):
    src = path.read_text(errors="ignore")
    if path.suffix == ".py":
        try:
            yield from scan_python(src)
        except SyntaxError:
            yield "module", "", "", "", 1
    elif path.suffix in (".js", ".mjs", ".ts", ".tsx", ".jsx"):
        yield "module", "", "", _comment_above(["x"] + src.splitlines()[:8], 1) or "", 1
        yield from scan_js(src)
    else:  # scripts
        head = " ".join(l.lstrip("#-/ ").strip() for l in src.splitlines()[:6] if l.strip().startswith(("#", "--", "//")) and not l.startswith("#!"))
        yield "script", "", "", head, 1


def _description(root: Path) -> str:
    for name in ("README.md", "readme.md", "README.txt", "README"):
        p = root / name
        if p.exists():
            paras = [b.strip() for b in p.read_text(errors="ignore").split("\n\n") if b.strip() and not b.strip().startswith(("#", "!", "[", "```"))]
            return paras[0][:400] if paras else ""
    return ""


def add_project(con, path: str, name: str | None = None) -> int:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a folder: {root}")
    pid = db.insert(con, "projects", name=name or root.name, path=str(root), description=_description(root))
    db.log(con, "me", "arsenal_add", str(root))
    con.commit()
    return pid


def scan(con, project_id: int | None = None) -> dict:
    q = "SELECT * FROM projects" + (" WHERE id = ?" if project_id else "")
    report = {}
    for p in con.execute(q, (project_id,) if project_id else ()).fetchall():
        root = Path(p["path"])
        con.execute("DELETE FROM assets WHERE project_id = ?", (p["id"],))
        n = 0
        for f in root.rglob("*"):
            if f.suffix not in CODE_EXT or not f.is_file() or any(part in SKIP_DIRS for part in f.relative_to(root).parts):
                continue
            if f.stat().st_size > MAX_BYTES:
                continue
            rel = str(f.relative_to(root))
            for kind, name, sig, doc, line in scan_file(f):
                label = name or rel
                db.insert(con, "assets", project_id=p["id"], path=rel, kind=kind, name=label, signature=sig,
                          doc=(doc or "")[:500], line=line,
                          tokens=f"{words(label)} {words(Path(rel).stem)} {p['name']}")
                n += 1
        con.execute("UPDATE projects SET description = ?, scanned = ? WHERE id = ?", (_description(root) or p["description"], db.now(), p["id"]))
        report[p["name"]] = n
    db.log(con, "hq", "arsenal_scan", str(report))
    con.commit()
    return report


def find(con, query: str, n: int = 10):
    terms = " OR ".join(w for w in re.findall(r"\w{3,}", query.lower()))
    if not terms:
        return []
    try:
        return con.execute(
            """SELECT a.*, p.name AS project, p.path AS root FROM assets_fts f
               JOIN assets a ON a.id = f.rowid JOIN projects p ON p.id = a.project_id
               WHERE assets_fts MATCH ? ORDER BY rank LIMIT ?""", (terms, n)).fetchall()
    except Exception:
        return []
