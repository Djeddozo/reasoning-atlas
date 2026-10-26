"""Speech-to-text on this computer's own hardware (owner, 2026-10-25).

The screen served by the Pi uses the Pi for most functions, but hardware-heavy work does not belong there: when
Atlas runs on the PC, Quick note's 🎙 sends the recording to this PC's Atlas service, which runs Whisper
(faster-whisper) on the PC itself: on an NVIDIA GPU when one is usable, otherwise on the CPU. Nothing goes to the
Pi or to the internet, and recordings are not kept: the temporary file is removed as soon as it is written out.

On a Raspberry Pi (or with ATLAS_LOCAL_VOICE=off) this refuses, so the Pi never transcribes on its CPU.
faster-whisper is optional and not part of Atlas: Systems › Voice shows whether it is there and installs it on this
PC only when the owner presses Install (his explicit approval for this one package, from PyPI).
Settings (model, language, vocabulary) live in runtime/voice.json on this computer.

NVIDIA support (cuBLAS and cuDNN, about 1 GB) is recommended only when the hardware scan finds an NVIDIA GPU with
CUDA (engine/hardware.py, the same scan that reports this PC under Resources › Connected). When the libraries are
present but no such GPU is, the card offers to remove them. Installing shows progress: packages resolved,
megabytes downloaded against the expected size, then installing; the state survives a restart of Atlas.
"""
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "runtime" / "voice.json"
INSTALL_STATE = ROOT / "runtime" / "voice-install.json"
GPU_PACKAGES = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]
EXPECTED_MB = {False: 150, True: 1300}  # rough download size, for the progress estimate
MAX_AUDIO = 25 * 1024 * 1024
EXT = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav"}
MODELS = ("tiny", "base", "small", "medium", "large-v3-turbo", "large-v3")
DEFAULTS = {"model": "", "language": "", "prompt": "Atlas, Reasoning Atlas, Djeddi, Engine, Forge, Tailscale, Claude"}
_lock, _model = threading.Lock(), {}
_hw = {}


def _install_state() -> dict:
    try:
        st = json.loads(INSTALL_STATE.read_text())
    except (OSError, ValueError):
        return {"state": "", "message": ""}
    if st.get("state") == "installing" and st.get("pid") != os.getpid():  # Atlas restarted while pip ran
        st.update(state="interrupted", message="Atlas restarted during the install; press Install again to finish it.")
    return st


def _set_install(**kw) -> dict:
    st = {**_install_state(), **kw, "pid": os.getpid()}
    INSTALL_STATE.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_STATE.write_text(json.dumps(st))
    return st


def hardware() -> dict:
    """The hardware scan (cached for ten minutes): whether an NVIDIA GPU with CUDA is present."""
    if time.time() - _hw.get("at", 0) > 600:
        try:
            from engine import hardware as hw
            gpus = hw._gpus()
        except Exception:
            gpus = []
        _hw.update(at=time.time(), gpus=gpus)
    nvidia = [g["name"] for g in _hw["gpus"] if g.get("cuda")]
    return {"nvidia": nvidia, "gpus": [g["name"] for g in _hw["gpus"]]}


def gpu_libraries() -> bool:
    import importlib.metadata as md
    for name in ("nvidia-cublas-cu12", "nvidia-cudnn-cu12"):
        try:
            md.distribution(name)
            return True
        except md.PackageNotFoundError:
            continue
    return False


def on_pi() -> bool:
    m = platform.machine().lower()
    return platform.system() == "Linux" and (m.startswith("arm") or m in ("aarch64", "arm64"))


def allowed() -> str:
    """'' when this computer may transcribe, else why not."""
    if os.environ.get("ATLAS_LOCAL_VOICE", "").lower() in ("off", "0", "no"):
        return "Switched off on this computer (ATLAS_LOCAL_VOICE=off)."
    if on_pi():
        return "This is the Pi: it never transcribes on its CPU. Use Atlas on your PC, a Whisper server or the browser."
    return ""


def _user_site() -> None:
    """pip --user installs into the user's site folder. A Python that started before that folder existed does not
    have it on its path, so a fresh install looked missing until Atlas restarted; add it now."""
    import site
    try:
        folder = site.getusersitepackages()
    except Exception:
        return
    if folder and os.path.isdir(folder) and folder not in sys.path:
        site.addsitedir(folder)
        importlib.invalidate_caches()


def installed() -> bool:
    _user_site()
    return importlib.util.find_spec("faster_whisper") is not None


def device() -> str:
    try:
        import ctranslate2  # comes with faster-whisper
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def settings() -> dict:
    try:
        saved = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        saved = {}
    return DEFAULTS | {k: v for k, v in saved.items() if k in DEFAULTS}


def model_name(dev: str | None = None) -> str:
    """The chosen model, or the best default for the hardware: large-v3-turbo on a GPU, small on a CPU."""
    return settings()["model"] or ("large-v3-turbo" if (dev or device()) == "cuda" else "small")


def save_settings(new: dict) -> dict:
    s = settings()
    if "model" in new:
        if new["model"] not in ("",) + MODELS:
            raise ValueError("model must be one of: " + ", ".join(MODELS))
        s["model"] = new["model"]
    if "language" in new:
        lang = "".join(c for c in str(new["language"]) if c.isalpha())[:5].lower()
        s["language"] = lang
    if "prompt" in new:
        s["prompt"] = " ".join(str(new["prompt"]).split())[:300]
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2))
    with _lock:
        _model.clear()  # the next recording loads the new model
    return status()


def status() -> dict:
    why = allowed()
    ok = installed() and not why
    dev = device() if ok else ""
    hw = hardware() if not on_pi() else {"nvidia": [], "gpus": []}
    libs = gpu_libraries()
    return {"available": ok, "why": why or ("" if installed() else "Whisper is not installed on this computer yet."),
            "installed": installed(), "device": dev, "model": model_name(dev) if ok else settings()["model"],
            "settings": settings(), "models": list(MODELS), "install": _install_state(),
            "machine": f"{platform.system()} {platform.machine()}", "hardware": hw,
            "gpu_recommended": bool(hw["nvidia"]), "gpu_libraries": libs,
            "gpu_libraries_unused": libs and not hw["nvidia"]}


def _load():
    from faster_whisper import WhisperModel
    dev = device()
    name = model_name(dev)
    key = (name, dev)
    if _model.get("key") != key:
        _model.clear()
        _model["model"] = WhisperModel(name, device=dev, compute_type="float16" if dev == "cuda" else "int8")
        _model["key"] = key
    return _model["model"]


def _decode(path: str):
    """The recording as 16 kHz mono float32 samples, decoded here with PyAV. faster-whisper's own decoder passes
    `metadata_errors` to av.open, which PyAV 14 removed ("open() got an unexpected keyword argument"), so Atlas does
    not depend on it. Without PyAV the path is passed on unchanged."""
    try:
        import av
        import numpy as np
        from av.audio.resampler import AudioResampler
    except ImportError:
        return path
    chunks = []
    try:
        container = av.open(path)
    except Exception as e:  # av.error.InvalidDataError and friends
        raise ValueError(f"could not read the recording ({e.__class__.__name__})") from None
    with container:
        resampler = AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray() for f in (resampler.resample(frame) or [])]
        chunks += [f.to_ndarray() for f in (resampler.resample(None) or [])]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return (np.concatenate(chunks, axis=1).reshape(-1).astype(np.float32) / 32768.0)


def transcribe(audio: bytes, ctype: str = "", language: str | None = None) -> str:
    why = allowed()
    if why:
        raise PermissionError(why)
    if not installed():
        raise RuntimeError("Whisper is not installed on this computer yet (Systems › Voice › Install).")
    if not audio or len(audio) > MAX_AUDIO:
        raise ValueError("empty or too long recording")
    s = settings()
    lang = s["language"] if language is None else "".join(c for c in language if c.isalpha())[:5].lower()
    f = tempfile.NamedTemporaryFile(suffix=EXT.get((ctype or "").split(";")[0].strip().lower(), ".webm"), delete=False)
    try:
        f.write(audio); f.close()
        with _lock:  # one recording at a time: the model is large and not shared between threads
            segments, _ = _load().transcribe(_decode(f.name), language=lang.split("-")[0] or None,
                                              initial_prompt=s["prompt"] or None, vad_filter=True)
            return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        Path(f.name).unlink(missing_ok=True)  # recordings are never kept on this computer


def _pip(args: list[str], gpu: bool, verb: str) -> None:
    """Run pip, reading its output line by line for the progress shown on the card."""
    expected = EXPECTED_MB[gpu] if verb == "install" else 0
    seen, done_mb, started = set(), 0.0, time.time()
    try:
        proc = subprocess.Popen([sys.executable, "-m", "pip", *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, stdin=subprocess.DEVNULL, bufsize=1)
        tail = []
        for line in proc.stdout:
            line = line.strip()
            tail = (tail + [line])[-6:]
            m = re.match(r"(?:Collecting|Requirement already satisfied:?)\s+([A-Za-z0-9_.\-]+)", line)
            if m:
                seen.add(m.group(1).lower())
            m = re.search(r"Downloading \S+ \(([\d.]+) (kB|MB|GB)\)", line)
            if m:
                done_mb += float(m.group(1)) * {"kB": 1 / 1024, "MB": 1, "GB": 1024}[m.group(2)]
            stage = ("Installing the downloaded packages…" if line.startswith("Installing collected packages")
                     else "Removing…" if verb == "uninstall" else "Downloading…" if "Downloading" in line else "Resolving packages…")
            pct = 95 if stage.startswith("Installing") else min(90, round(100 * done_mb / expected)) if expected else 50
            _set_install(state="installing", stage=stage, percent=pct, packages=len(seen), downloaded_mb=round(done_mb),
                         expected_mb=expected, seconds=round(time.time() - started), message=line[-160:])
        code = proc.wait(timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as e:
        _set_install(state="failed", message=f"pip could not run: {e}"[:300], percent=0)
        return
    importlib.invalidate_caches()
    if code == 0:
        _set_install(state="done", percent=100, stage="", seconds=round(time.time() - started),
                     message=("Installed. The first recording downloads the model once (a few minutes)." if verb == "install"
                              else "NVIDIA libraries removed; Whisper keeps working on the processor."))
        with _lock:
            _model.clear()
    else:
        _set_install(state="failed", percent=0, stage="", message="pip failed: " + " ".join(tail)[-300:])


def install(gpu: bool | None = None) -> dict:
    """pip install faster-whisper for this user, in the background; started only by the owner's click.
    gpu None: as the hardware scan recommends (only with an NVIDIA GPU)."""
    if allowed():
        raise PermissionError(allowed())
    if _install_state().get("state") == "installing":
        return _install_state()
    gpu = hardware()["nvidia"] != [] if gpu is None else gpu
    pkgs = ["faster-whisper"] + (GPU_PACKAGES if gpu else [])
    st = _set_install(state="installing", stage="Starting…", percent=0, packages=0, downloaded_mb=0, expected_mb=EXPECTED_MB[gpu],
                      seconds=0, message="Installing " + ", ".join(pkgs) + " for this user…", gpu=gpu)
    threading.Thread(target=_pip, args=(["install", "--user", "--upgrade", "--progress-bar", "off", *pkgs], gpu, "install"),
                     daemon=True, name="atlas-whisper-install").start()
    return st


def remove_gpu_libraries() -> dict:
    """Uninstall the NVIDIA libraries (about 1 GB) when this PC has no NVIDIA GPU to use them."""
    if allowed():
        raise PermissionError(allowed())
    if _install_state().get("state") == "installing":
        raise ValueError("An install is still running; wait until it is done.")
    st = _set_install(state="installing", stage="Removing…", percent=50, expected_mb=0, seconds=0, gpu=False,
                      message="Removing the NVIDIA libraries…")
    threading.Thread(target=_pip, args=(["uninstall", "-y", *[p.split("==")[0] for p in GPU_PACKAGES]], False, "uninstall"),
                     daemon=True, name="atlas-whisper-uninstall").start()
    return st
