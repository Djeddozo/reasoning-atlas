"""Reading PDFs for intake: text first, a picture of the first page for scans.

  extract_text(path)        the text layer of a PDF: `pdftotext` (poppler-utils) when installed, otherwise a small
                            stdlib reader that handles the common cases (Flate-compressed pages, Tj/TJ text,
                            ToUnicode maps). A scan has no text layer and returns ''.
  render_first_page(path)   the first page as a PNG via `pdftoppm`, so a scan can be read by the vision model;
                            None when poppler-utils is not installed or the file cannot be rendered.

The PDF is untrusted data. Both outside programs run without a shell, with a time limit, on a copy inside
memory/intake/; the stdlib reader caps how much it decompresses (no zip bombs). Nothing in a PDF is executed.
"""
import re
import shutil
import subprocess
import threading
import zlib
from pathlib import Path

MAX_PAGES = 30
MAX_CHARS = 60_000
MAX_INFLATE = 20 * 1024 * 1024    # total decompressed bytes the stdlib reader accepts per file
TIMEOUT = 60


def is_pdf(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(1024).lstrip().startswith(b"%PDF")
    except OSError:
        return False


def _tidy(text: str, max_chars: int) -> str:
    text = text.replace("\x00", "").replace("\f", "\n")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out[:max_chars]


def extract_text(path, max_pages: int = MAX_PAGES, max_chars: int = MAX_CHARS) -> str:
    path = Path(path)
    tool = shutil.which("pdftotext")
    if tool:
        out = _bounded_run([tool, "-q", "-enc", "UTF-8", "-l", str(max_pages), str(path), "-"], max_chars * 4)
        if out is not None:
            return _tidy(out.decode("utf-8", errors="replace"), max_chars)
    try:
        return _tidy(read_text(path.read_bytes(), max_pages, max_chars), max_chars)
    except (OSError, ValueError, zlib.error, RecursionError):
        return ""


def _bounded_run(cmd: list[str], max_bytes: int) -> bytes | None:
    """Run cmd and return at most max_bytes of its stdout (it is stopped once it has written more),
    or None when it failed or ran out of time. Untrusted PDFs can expand into far more text than they weigh."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except OSError:
        return None
    timer = threading.Timer(TIMEOUT, proc.kill)  # also ends a read that blocks on a hung process
    timer.start()
    chunks, size = [], 0
    try:
        while size <= max_bytes:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > max_bytes:
            proc.kill()
            proc.wait()
            return b"".join(chunks)[:max_bytes]
        code = proc.wait()
        return b"".join(chunks) if code == 0 else None  # killed by the timer → negative code → None
    except OSError:
        proc.kill()
        proc.wait()
        return None
    finally:
        timer.cancel()
        proc.stdout.close()


def render_first_page(path, dest_dir) -> Path | None:
    """Write <dest_dir>/<pdf name>-p1.png and return it, or None."""
    tool = shutil.which("pdftoppm")
    if not tool:
        return None
    path = Path(path)
    stem = Path(dest_dir) / f"{path.stem}-p1"
    try:
        r = subprocess.run([tool, "-q", "-png", "-r", "110", "-f", "1", "-l", "1", "-singlefile", str(path), str(stem)],
                           capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    out = stem.with_suffix(".png")
    return out if r.returncode == 0 and out.exists() and out.stat().st_size > 0 else None


# ---------------- the stdlib reader ----------------

STREAM = re.compile(rb"<<((?:[^<>]|<[^<]|>[^>]|<<(?:[^<>]|<[^<]|>[^>])*>>)*)>>\s*stream\r?\n", re.S)


def _streams(data: bytes):
    """Yield (dictionary, decoded body) for every stream; skips images and filters other than Flate."""
    budget = MAX_INFLATE
    for m in STREAM.finditer(data):
        head, start = m.group(1), m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            break
        body = data[start:end].rstrip(b"\r\n")
        if re.search(rb"/Subtype\s*/Image|/DCTDecode|/JPXDecode|/CCITTFax|/JBIG2", head):
            continue
        filters = re.findall(rb"/(\w+Decode)", head)
        if filters and filters != [b"FlateDecode"]:
            continue  # ASCII85, LZW etc.: rare for text; pdftotext handles them
        if filters:
            d = zlib.decompressobj()
            try:
                body = d.decompress(body, budget)
            except zlib.error:
                continue
            budget -= len(body)
            if budget <= 0:
                raise ValueError("PDF decompresses to too much data")
        yield head, body


def _literal(data: bytes, i: int) -> tuple[bytes, int]:
    """Parse a (literal string) starting at data[i] == '('; return (bytes, index after it)."""
    out, depth, i = bytearray(), 1, i + 1
    esc = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f",
           ord("("): b"(", ord(")"): b")", ord("\\"): b"\\"}
    while i < len(data):
        c = data[i]
        if c == 0x5C:  # backslash
            i += 1
            if i >= len(data):
                break
            c = data[i]
            if c in esc:
                out += esc[c]
            elif 0x30 <= c <= 0x37:
                j = i
                while j < len(data) and j < i + 3 and 0x30 <= data[j] <= 0x37:
                    j += 1
                out.append(int(data[i:j], 8) & 0xFF)
                i = j - 1
            elif c in (0x0A, 0x0D):  # line continuation
                if c == 0x0D and i + 1 < len(data) and data[i + 1] == 0x0A:
                    i += 1
            else:
                out.append(c)
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out.append(c)
        else:
            out.append(c)
        i += 1
    return bytes(out), i


def _hexstr(s: bytes) -> bytes:
    s = re.sub(rb"\s", b"", s)
    if len(s) % 2:
        s += b"0"
    try:
        return bytes.fromhex(s.decode("ascii"))
    except ValueError:
        return b""


def _cmap(body: bytes) -> dict[bytes, str]:
    """ToUnicode CMap → {code bytes: text}."""
    out: dict[bytes, str] = {}

    def uni(h: bytes) -> str:
        return _hexstr(h).decode("utf-16-be", errors="ignore")

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", body, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]*)>", block):
            out[_hexstr(src)] = uni(dst)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", body, re.S):
        for lo, hi, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f\s]*>|\[[^\]]*\])", block):
            a, b, width = int(lo, 16), int(hi, 16), len(_hexstr(lo))
            if b - a > 65535:
                continue
            if rest.startswith(b"["):
                for k, h in enumerate(re.findall(rb"<([0-9A-Fa-f\s]*)>", rest)):
                    if a + k <= b:
                        out[(a + k).to_bytes(width, "big")] = uni(h)
            else:
                base = _hexstr(rest[1:-1])
                if not base:
                    continue
                start = int.from_bytes(base, "big")
                for k in range(b - a + 1):
                    try:
                        out[(a + k).to_bytes(width, "big")] = (start + k).to_bytes(len(base), "big").decode("utf-16-be", errors="ignore")
                    except OverflowError:
                        break
    return out


def _decode(raw: bytes, cmap: dict[bytes, str], hexed: bool) -> str:
    if cmap:
        widths = sorted({len(k) for k in cmap}, reverse=True)
        out, i = [], 0
        while i < len(raw):
            for w in widths:
                if raw[i:i + w] in cmap:
                    out.append(cmap[raw[i:i + w]])
                    i += w
                    break
            else:
                out.append(chr(raw[i]) if 32 <= raw[i] < 127 else "")
                i += 1
        return "".join(out)
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="ignore")
    if hexed and len(raw) % 2 == 0 and raw[::2].count(0) > len(raw) // 4:
        return raw.decode("utf-16-be", errors="ignore")  # two-byte codes without a map: often plain UCS-2
    return raw.decode("latin-1")


TOKEN = re.compile(rb"\(|<(?!<)[0-9A-Fa-f\s]*>|\[|\]|/[^\s/<>\[\]()]+|[A-Za-z'\"*]+|[-+]?\d*\.?\d+|<<|>>|%[^\r\n]*|\S")


def _content_text(body: bytes, cmap: dict[bytes, str]) -> str:
    """Text shown by one content stream."""
    out, operands, in_array, array = [], [], False, []
    i = 0
    while i < len(body):
        m = TOKEN.match(body, i)
        if not m:
            i += 1
            continue
        tok = m.group(0)
        if tok == b"(":
            s, i = _literal(body, m.start())
            (array if in_array else operands).append(_decode(s, cmap, False))
            continue
        i = m.end()
        if tok.startswith(b"<") and tok.endswith(b">") and tok != b"<<":
            (array if in_array else operands).append(_decode(_hexstr(tok[1:-1]), cmap, True))
        elif tok == b"[":
            in_array, array = True, []
        elif tok == b"]":
            in_array = False
            operands.append(array)
        elif in_array:
            try:
                if float(tok) < -200:  # a wide kerning gap is a space between words
                    array.append(" ")
            except ValueError:
                pass
        elif tok in (b"Tj", b"'", b'"'):
            if tok != b"Tj":
                out.append("\n")
            if operands and isinstance(operands[-1], str):
                out.append(operands[-1])
            operands = []
        elif tok == b"TJ":
            if operands and isinstance(operands[-1], list):
                out.append("".join(operands[-1]))
            operands = []
        elif tok in (b"Td", b"TD", b"T*", b"Tm", b"ET"):
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            operands = []
        elif re.fullmatch(rb"[A-Za-z*]+", tok):
            operands = []
        else:
            operands.append(tok)
    return "".join(out)


def read_text(data: bytes, max_pages: int = MAX_PAGES, max_chars: int = MAX_CHARS) -> str:
    """Best-effort text of a PDF without outside programs. Font maps are merged across the file,
    which is good enough for the usual one- or two-font documents. Pages are approximated by
    content streams (usually one per page): at most max_pages of them are read."""
    streams = list(_streams(data))
    cmap: dict[bytes, str] = {}
    for _, body in streams:
        if b"begincmap" in body:
            for code, text in _cmap(body).items():
                # fonts can disagree about a code (e.g. a symbol font next to the text font): the plain reading
                # (code == character) wins, otherwise the first font seen
                if code not in cmap or text == code.decode("latin-1"):
                    cmap[code] = text
    parts, size = [], 0
    for head, body in streams:
        if len(parts) >= max_pages or size >= max_chars:
            break
        if b"begincmap" in body or b"BT" not in body or re.search(rb"/Type\s*/(XRef|ObjStm|Metadata)|/Length1", head):
            continue
        parts.append(_content_text(body, cmap))
        size += len(parts[-1])
    return "\n".join(p for p in parts if p.strip())
