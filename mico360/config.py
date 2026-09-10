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

SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "mico360.db"

_ALL_DIRS = [
    DATA_DIR, LOG_DIR, HISTORY_DIR, PROFILES_DIR,
    PROMPTS_DIR, EXPORT_DIR, MODELS_DIR, TMP_DIR,
]


def ensure_dirs() -> None:
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


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

# MICO360 Connect AI platform — OpenAI-compatible surface. Hard-coded on purpose:
# there is NO settings UI to change the endpoint; the user only picks the mode.
MICO360_CONNECT_BASE_URL = "http://ai.mico360.com:5310/v1"
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
    "crash_reporter": True,             # show the opt-in crash dialog on errors
    "onboarded": False,                 # first-run guide shown
    # Email (SMTP) — credentials are stored ONLY in the local settings file,
    # never in source. Defaults are blank; the user fills them in Settings.
    "smtp_host": "in-v3.mailjet.com",
    "smtp_port": 587,
    "smtp_user": "",                    # Mailjet API key
    "smtp_password": "",                # Mailjet Secret key
    "email_from": "",                   # validated sender, e.g. admin@mico360.com
}


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
                # keep defaults for any newly-added keys
                self._data.update({k: v for k, v in loaded.items()})
        except Exception:  # corrupt settings should never crash startup
            logging.getLogger(__name__).warning("settings load failed; using defaults", exc_info=True)

    def save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception:
            logging.getLogger(__name__).warning("settings save failed", exc_info=True)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULT_SETTINGS.get(key, default))

    def set(self, key: str, value: Any) -> None:
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
