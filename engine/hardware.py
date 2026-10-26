"""What this machine is (owner, 2026-10-25): hardware as a live resource instead of a hand-written line.

`detect()` reads the machine it runs on, standard library and the system's own tools only: operating system, CPU,
cores, memory, NVIDIA GPUs (nvidia-smi) or, on Windows, every video controller, and free disk space. No network.
The Pi reports itself in the hourly cycle; Atlas on the PC reports the PC through the hub. `upsert` keeps one
resource row per machine (source `detected:<key>`), refreshed on every report; the owner's own resources stay
hand-written and editable. A detected row is read-only in Atlas; it is never deleted, only marked retired when
the owner retires it.
"""
import json
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path

from . import db


def _run(args, timeout=8) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _cpu() -> str:
    if platform.system() == "Linux":
        try:
            text = Path("/proc/cpuinfo").read_text()
            m = re.search(r"^(?:model name|Model)\s*:\s*(.+)$", text, re.M)
            if m:
                return m.group(1).strip()
        except OSError:
            pass
    if platform.system() == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
        if out.strip():
            return out.strip().splitlines()[0]
    if platform.system() == "Darwin":
        out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if out.strip():
            return out.strip()
    return platform.processor() or platform.machine()


def _memory_gb() -> float | None:
    try:
        if platform.system() == "Linux":
            m = re.search(r"MemTotal:\s+(\d+) kB", Path("/proc/meminfo").read_text())
            return round(int(m.group(1)) / 1024 / 1024, 1) if m else None
        if platform.system() == "Windows":
            out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
            return round(int(out.strip()) / 1024 ** 3, 1) if out.strip().isdigit() else None
        if platform.system() == "Darwin":
            out = _run(["sysctl", "-n", "hw.memsize"])
            return round(int(out.strip()) / 1024 ** 3, 1) if out.strip().isdigit() else None
    except (OSError, ValueError):
        return None
    return None


def _gpus() -> list[dict]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        name, _, mem = line.partition(",")
        if name.strip():
            gpus.append({"name": name.strip(), "memory_gb": round(float(mem) / 1024, 1) if mem.strip().replace(".", "").isdigit() else None,
                         "cuda": True})
    if not gpus and platform.system() == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"])
        gpus = [{"name": n.strip(), "memory_gb": None, "cuda": False} for n in out.splitlines() if n.strip()]
    return gpus


def detect() -> dict:
    root = Path(os.environ.get("SystemDrive", "C:") + "\\") if platform.system() == "Windows" else Path("/")
    try:
        disk = shutil.disk_usage(root)
        disk_free = round(disk.free / 1024 ** 3), round(disk.total / 1024 ** 3)
    except OSError:
        disk_free = None
    m = platform.machine().lower()
    pi = platform.system() == "Linux" and (m.startswith("arm") or m in ("aarch64", "arm64"))
    model = ""
    if pi:
        try:
            model = Path("/proc/device-tree/model").read_text().strip("\x00 \n")
        except OSError:
            model = "Raspberry Pi"
    return {"host": socket.gethostname(), "role": "pi" if pi else "pc", "model": model,
            "os": f"{platform.system()} {platform.release()}", "arch": platform.machine(), "cpu": _cpu(),
            "cores": os.cpu_count(), "memory_gb": _memory_gb(), "gpus": _gpus(),
            "disk_free_gb": disk_free[0] if disk_free else None, "disk_total_gb": disk_free[1] if disk_free else None}


def describe(d: dict) -> tuple[str, str]:
    """(name, details) for the resource list."""
    name = (d.get("model") or ("This PC" if d.get("role") == "pc" else "Machine")) + f" ({d.get('host', '?')})"
    parts = [d.get("cpu") or "", f"{d['cores']} cores" if d.get("cores") else "",
             f"{d['memory_gb']:g} GB RAM" if d.get("memory_gb") else ""]
    parts += [g["name"] + (f" {g['memory_gb']:g} GB" if g.get("memory_gb") else "") + (" (CUDA)" if g.get("cuda") else "")
              for g in d.get("gpus") or []]
    if d.get("disk_free_gb") is not None:
        parts.append(f"{d['disk_free_gb']} of {d['disk_total_gb']} GB disk free")
    parts.append(d.get("os") or "")
    return name[:120], " · ".join(p for p in parts if p)[:500]


def upsert(con, d: dict) -> int:
    """One resource row per machine, refreshed with every report."""
    key = "detected:" + re.sub(r"[^A-Za-z0-9_.-]", "", str(d.get("host", "")))[:60]
    if key == "detected:":
        raise ValueError("a machine report needs its host name")
    name, details = describe(d)
    row = con.execute("SELECT id FROM resources WHERE source = ?", (key,)).fetchone()
    data = json.dumps({k: d.get(k) for k in ("role", "cpu", "cores", "memory_gb", "gpus", "disk_free_gb", "os", "arch")})
    if row:
        db.update(con, "resources", row[0], name=name, details=details, value=d.get("memory_gb"), unit="GB RAM", detected=data)
        rid = row[0]
    else:
        rid = db.insert(con, "resources", kind="hardware", name=name, details=details, value=d.get("memory_gb"),
                        unit="GB RAM", source=key, status="active", detected=data)
        db.log(con, "hq", "resource_detected", f"#{rid} {name}")
    con.commit()
    return rid
