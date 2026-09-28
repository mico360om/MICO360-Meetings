"""Application paths, persistent settings, and logging setup.

All user data lives under a single writable data directory so the app works
when installed to Program Files (which is read-only for normal users).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import __app_name__, __version__

APP_SLUG = "MICO360Meetings"

# Quieten optional HF Hub warnings emitted by faster-whisper on Windows.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _base_data_dir() -> Path:
    """Return the per-user writable data directory for this OS."""
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(root) / APP_SLUG


DATA_DIR = _base_data_dir()
LOG_DIR = DATA_DIR / "logs"
HISTORY_DIR = DATA_DIR / "history"
PROFILES_DIR = DATA_DIR / "profiles"
PROMPTS_DIR = DATA_DIR / "prompts"
EXPORT_DIR = DATA_DIR / "exports"
MODELS_DIR = DATA_DIR / "whisper-models"
TMP_DIR = DATA_DIR / "tmp"
# Finished recordings. Kept until the user deletes them — never auto-purged
# (only the recorder's intermediate files in TMP_DIR are cleaned up).
RECORDINGS_DIR = DATA_DIR / "recordings"

SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "mico360.db"

_ALL_DIRS = [
    DATA_DIR, LOG_DIR, HISTORY_DIR, PROFILES_DIR,
    PROMPTS_DIR, EXPORT_DIR, MODELS_DIR, TMP_DIR, RECORDINGS_DIR,
]


def ensure_dirs() -> None:
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` atomically: write a sibling temp file, fsync it,
    then os.replace() it over the target. A crash or power loss mid-write leaves
    either the old file or the new one — never a truncated, half-written file
    that the next load would treat as corrupt (and the next save would then
    overwrite with defaults)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        # Windows: the target can be briefly locked (AV scanner, Dropbox sync).
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                import time as _t
                _t.sleep(0.05 * (attempt + 1))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def quarantine_bad_file(path: Path) -> Path | None:
    """Move a file that failed to load aside (`<name>.bad`, or `<name>.bad-<ts>`
    if that exists) so the next save can't silently overwrite the only copy of
    the user's data. Returns the new path, or None if nothing was moved."""
    path = Path(path)
    try:
        if not path.exists():
            return None
        dest = path.with_name(path.name + ".bad")
        if dest.exists():
            import time as _t
            dest = path.with_name(f"{path.name}.bad-{_t.strftime('%Y%m%d-%H%M%S')}")
        os.replace(path, dest)
        logging.getLogger("mico360").warning("unreadable %s kept as %s", path.name, dest.name)
        return dest
    except Exception:
        logging.getLogger("mico360").warning("could not quarantine %s", path, exc_info=True)
        return None


def apply_quality_preset(settings, name: str) -> None:
    """Apply a Speed/Quality preset's Whisper settings to the store."""
    preset = QUALITY_PRESETS.get(name)
    if not preset:
        return
    settings.set("quality_preset", name)
    settings.set("whisper_model", preset["whisper_model"])
    settings.set("whisper_compute", preset["whisper_compute"])


# ---------------------------------------------------------------------------
# Resource resolution (works both from source and from a PyInstaller bundle)
# ---------------------------------------------------------------------------
def resource_path(*parts: str) -> Path:
    """Resolve a bundled read-only resource (assets, default data)."""
    if getattr(sys, "frozen", False):  # PyInstaller
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
# Default GitHub repo (templated — change to your real repo when published).
DEFAULT_REPO = "mico360om/MICO360-Meetings"

# ---------------------------------------------------------------------------
# AI provider ("mode") — where MINUTES are generated. Transcription is always
# local (Whisper). Two modes are offered; the user only *selects* one.
# ---------------------------------------------------------------------------
AI_PROVIDERS: dict[str, str] = {
    "local": "Local (Ollama)",
    "cloud": "MICO360 Cloud",
}

# UI text-size presets (accessibility). The factor scales every point-size in the
# theme's stylesheet, so all text grows/shrinks together.
UI_SCALES: list[tuple[str, float]] = [
    ("Small", 0.9), ("Default", 1.0), ("Large", 1.15), ("Larger", 1.3),
]

# MICO360 Connect AI platform — OpenAI-compatible surface. Hard-coded on purpose:
# there is NO settings UI to change the endpoint; the user only picks the mode.
MICO360_CONNECT_BASE_URL = "http://ai.mico360.com:5310/v1"
# Encrypted endpoints tried automatically (certificate-verified). The first one
# that answers over HTTPS is used for all Cloud traffic from then on; the plain
# HTTP URL above is only a fallback until the server offers TLS (BUG_REPORT C5).
MICO360_CONNECT_HTTPS_CANDIDATES = (
    "https://ai.mico360.com/v1",
    "https://ai.mico360.com:5310/v1",
)
# A model the fleet is expected to have (per the API guide). It is only a default —
# the real list comes live from GET /v1/models and the user picks from it.
MICO360_CONNECT_DEFAULT_MODEL = "llama3.1:latest"


def connect_api_key() -> str:
    """The MICO360 Connect API key (a secret).

    Resolution order, so end-users configure nothing and the key never lives in a
    tracked source file (the repository is public):
      1. MICO360_CONNECT_API_KEY environment variable (dev / server / CI), then
      2. a build-time injected ``mico360/_build_key.py`` (gitignored; written by
         the build script from the same env var and bundled into the installer).
    """
    env = (os.environ.get("MICO360_CONNECT_API_KEY", "") or "").strip()
    if env:
        return env
    try:
        from . import _build_key  # generated at build time; gitignored, bundled only
        return (getattr(_build_key, "KEY", "") or "").strip()
    except Exception:
        return ""

# Speed/Quality presets map to a Whisper model + compute. The Ollama model is
# chosen separately, but presets hint a preferred tier (small/large).
QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    "Fast":     {"whisper_model": "tiny",  "whisper_compute": "int8", "ollama_tier": "small"},
    "Balanced": {"whisper_model": "base",  "whisper_compute": "int8", "ollama_tier": "any"},
    "Accurate": {"whisper_model": "small", "whisper_compute": "int8", "ollama_tier": "large"},
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "dark",                    # "dark" | "light"
    "ui_scale": 1.0,                    # text-size factor (see UI_SCALES)
    "ai_provider": "local",             # "local" (Ollama) | "cloud" (MICO360 Connect)
    "ollama_host": "http://127.0.0.1:11434",
    "ollama_model": "",                 # chosen at runtime from available models
    "cloud_model": "",                  # chosen at runtime from GET /v1/models
    "quality_preset": "Balanced",       # Fast | Balanced | Accurate | Custom
    "whisper_model": "base",            # tiny/base/small/medium/large-v3
    "whisper_compute": "int8",          # int8 is best for low-resource CPUs
    "whisper_device": "auto",           # auto/cpu/cuda
    "output_style": "Formal Minutes",
    "active_profile": "",               # company profile id
    "remove_fillers": True,
    "diarize": False,                   # lightweight speaker identification
    "audio_source": "mic",              # mic | system | both
    "chunk_chars": 6000,                # transcript chunk size for map-reduce
    "language": "auto",
    "window_geometry": "",
    "github_repo": DEFAULT_REPO,
    "auto_check_updates": True,
    "git_auto_update": False,           # source checkouts: git pull --ff-only on startup
    "crash_reporter": True,             # show the opt-in crash dialog on errors
    "onboarded": False,                 # first-run guide shown
    "speaker_names": [],                # remembered names for speaker-naming autocomplete
    "live_transcription": False,        # transcribe from the mic while recording (beta)
    "tmp_retention_days": 7,            # purge recorder INTERMEDIATE files older than this (finished recordings are kept)
    "auto_record": False,               # offer to record detected / scheduled meetings
    "auto_record_lead_minutes": 3,      # prompt this many minutes before a calendar start
    "auto_record_ics": "",              # optional .ics calendar file to watch (Outlook is automatic)
    "auto_record_consent_ack": False,   # consent notice shown once
    # Email (SMTP) — credentials are stored ONLY in the local settings file,
    # never in source. Defaults are blank; the user fills them in Settings.
    "smtp_host": "in-v3.mailjet.com",
    "smtp_port": 587,
    "smtp_user": "",                    # Mailjet API key
    "smtp_password": "",                # Mailjet Secret key
    "email_from": "",                   # validated sender, e.g. admin@mico360.com
}



# ---------------------------------------------------------------------------
# Secrets at rest (the SMTP password). On Windows they are encrypted with DPAPI,
# tied to the current Windows user account, so settings.json never holds them in
# plain text. Elsewhere (or if DPAPI is unavailable) they are stored as-is.
# ---------------------------------------------------------------------------
_SECRET_KEYS = frozenset({"smtp_password"})
_SECRET_PREFIX = "dpapi:"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    flags = 0x1                                   # CRYPTPROTECT_UI_FORBIDDEN
    ok = fn(ctypes.byref(src), None, None, None, None, flags, ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.GetLastError(), "DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# Every DPAPI blob starts with this header (version + the DPAPI provider GUID).
_DPAPI_MAGIC = bytes.fromhex("01000000d08c9ddf0115d1118c7a00c04fc297eb")


def _is_blob(value: str) -> bool:
    """True if `value` is an encrypted secret we wrote ("dpapi:" + a DPAPI blob) —
    whether or not THIS Windows account can decrypt it. A password that merely
    starts with "dpapi:" is not a blob."""
    if not isinstance(value, str) or not value.startswith(_SECRET_PREFIX):
        return False
    try:
        import base64
        raw = base64.b64decode(value[len(_SECRET_PREFIX):], validate=True)
    except Exception:
        return False
    return raw.startswith(_DPAPI_MAGIC)


def protect_secret(plain: str) -> str:
    """Encrypt a secret for settings.json (returns it unchanged if impossible)."""
    if not plain or sys.platform != "win32" or _is_blob(str(plain)):
        return plain
    try:
        import base64
        return _SECRET_PREFIX + base64.b64encode(_dpapi(str(plain).encode("utf-8"), True)).decode("ascii")
    except Exception:
        logging.getLogger(__name__).warning("could not encrypt secret; storing as-is", exc_info=True)
        return plain


def unprotect_secret(stored: str) -> str:
    """Decrypt a value written by protect_secret; any other value is returned
    as-is (a plain password — even one that starts with "dpapi:")."""
    if not stored or not _is_blob(str(stored)):
        return stored
    try:
        import base64
        raw = base64.b64decode(str(stored)[len(_SECRET_PREFIX):])
        return _dpapi(raw, False).decode("utf-8")
    except Exception:
        # e.g. settings copied from another Windows account/PC — can't decrypt
        logging.getLogger(__name__).warning("could not decrypt a stored secret", exc_info=True)
        return ""


class Settings:
    """Tiny JSON-backed settings store with attribute-style access."""

    def __init__(self, path: Path = SETTINGS_FILE):
        self._path = path
        self._data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        try:
            if self._path.exists():
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("settings file is not a JSON object")
                # keep defaults for any newly-added keys
                self._data.update({k: v for k, v in loaded.items()})
        except Exception:  # corrupt settings should never crash startup
            logging.getLogger(__name__).warning("settings load failed; using defaults", exc_info=True)
            # keep the unreadable file (SMTP credentials etc.) instead of letting
            # the next save overwrite it with defaults
            quarantine_bad_file(self._path)
        self._migrate()

    def _migrate(self) -> None:
        """Heal settings written by older builds. Persist only if something changed."""
        changed = False
        # Older builds persisted a blank update repo, which silently turned auto-
        # update off. Restore the default repo so update checks work again.
        if not str(self._data.get("github_repo") or "").strip():
            self._data["github_repo"] = DEFAULT_REPO
            changed = True
        # Older builds stored the SMTP password in plain text: encrypt it now.
        for k in _SECRET_KEYS:
            v = self._data.get(k)
            if v and isinstance(v, str) and not _is_blob(v):
                enc = protect_secret(v)
                if enc != v:
                    self._data[k] = enc
                    changed = True
        if changed:
            self.save()

    def save(self) -> None:
        try:
            atomic_write_text(self._path, json.dumps(self._data, indent=2))
        except Exception:
            logging.getLogger(__name__).warning("settings save failed", exc_info=True)

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key, DEFAULT_SETTINGS.get(key, default))
        if key in _SECRET_KEYS and isinstance(value, str):
            return unprotect_secret(value)
        return value

    def set(self, key: str, value: Any) -> None:
        if key in _SECRET_KEYS and isinstance(value, str):
            value = protect_secret(value)
        self._data[key] = value
        self.save()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(level: int = logging.INFO) -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger("mico360")
    if logger.handlers:           # already configured
        return logger
    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    logger.info("=== %s v%s starting (data: %s) ===", __app_name__, __version__, DATA_DIR)
    return logger
