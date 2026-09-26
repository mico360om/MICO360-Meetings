"""Run a test suite against a THROWAWAY app-data folder.

Import this module and call ``isolate()`` BEFORE anything imports ``mico360``:

    import _isolation
    DATA_ROOT = _isolation.isolate("mico360_qa_")
    ...
    _isolation.cleanup()        # before os._exit(...)

It redirects LOCALAPPDATA / XDG_DATA_HOME to a new temporary folder, so the
suites never touch the developer's real meetings, settings, speakers, logs or
crash reports (%LOCALAPPDATA%\\MICO360Meetings).

Whisper models are the one thing worth sharing: an empty folder would make
faster-whisper download "tiny" (~75 MB) again on every run, and on some
networks (TLS-intercepting proxies) that download fails. Any models already
present in the REAL data folder are therefore COPIED into the temporary one
(copied, not hard-linked: a download tool that rewrote a linked file in place
would corrupt the real model). With no local models (e.g. CI) the suite
downloads them as before.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

APP_SLUG = "MICO360Meetings"          # must match mico360.config.APP_SLUG
_PREFIX_ALL = "mico360_t_"
_state: dict[str, Path] = {}


def real_data_dir() -> Path:
    """The data folder the app would use right now (mirrors config._base_data_dir)."""
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(root) / APP_SLUG


def _copy_real(src: str, dst: str) -> str:
    # HF caches may use symlinks to blobs - copy the target's contents.
    return shutil.copy2(os.path.realpath(src), dst)


def _seed_models(real: Path, data_dir: Path, models) -> list[str]:
    src_root = real / "whisper-models"
    if not src_root.is_dir():
        return []
    dst_root = data_dir / "whisper-models"
    dst_root.mkdir(parents=True, exist_ok=True)
    seeded = []
    for d in src_root.iterdir():
        if not d.is_dir() or not d.name.startswith("models--"):
            continue
        if models and not any(d.name.endswith(f"-{m}") for m in models):
            continue
        try:
            shutil.copytree(d, dst_root / d.name, copy_function=_copy_real,
                            ignore=shutil.ignore_patterns("*.incomplete", "*.lock"),
                            dirs_exist_ok=True)
            seeded.append(d.name)
        except Exception as exc:                       # best effort - may download
            print(f"  [isolation] could not reuse {d.name}: {exc}")
    tag = src_root / "CACHEDIR.TAG"
    if tag.exists():
        try:
            shutil.copy2(tag, dst_root / tag.name)
        except OSError:
            pass
    return seeded


def _purge_stale(max_age_h: float = 24.0) -> None:
    """Remove temp folders left by earlier runs that exited abruptly."""
    base = Path(tempfile.gettempdir())
    cutoff = time.time() - max_age_h * 3600
    try:
        for p in base.glob(_PREFIX_ALL + "*"):
            try:
                if p.is_dir() and p.stat().st_mtime < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


def isolate(prefix: str = "suite_", models=("tiny",)) -> Path:
    """Point the app at a fresh temp data folder; return that folder's root.

    ``models``: which Whisper sizes to reuse from the real data folder
    (``None``/empty = all of them).
    """
    if "mico360.config" in sys.modules:
        raise RuntimeError("isolate() must run before mico360 is imported")
    if "root" in _state:
        return _state["root"]
    # Suites print arrows/dashes; a cp1252 console must not turn that into a
    # test failure when a suite is run directly (the gate sets PYTHONUTF8=1).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    real = real_data_dir()                 # computed BEFORE the env is changed
    _purge_stale()
    root = Path(tempfile.mkdtemp(prefix=_PREFIX_ALL + prefix))
    os.environ["LOCALAPPDATA"] = str(root)
    os.environ["XDG_DATA_HOME"] = str(root)
    data_dir = root / APP_SLUG
    data_dir.mkdir(parents=True, exist_ok=True)
    seeded = _seed_models(real, data_dir, models)
    _state["root"] = root
    _state["real"] = real
    print(f"  [isolation] data folder: {data_dir}"
          + (f"  (reusing {', '.join(s.split('--')[-1] for s in seeded)})" if seeded else ""))
    return root


def assert_isolated() -> None:
    """Fail loudly if the app resolved its data folder outside the temp root."""
    from mico360 import config
    root = _state.get("root")
    if root is None or not str(config.DATA_DIR).startswith(str(root)):
        raise SystemExit(f"NOT ISOLATED: app data folder is {config.DATA_DIR}")


def cleanup() -> None:
    """Best-effort removal of the temp folder (open DB handles may keep a few files)."""
    root = _state.pop("root", None)
    if root is None:
        return
    try:
        import logging
        logging.shutdown()                 # releases logs/app.log
    except Exception:
        pass
    shutil.rmtree(root, ignore_errors=True)
    if not root.exists():
        return
    # Windows cannot delete files this process still has open (the app's SQLite
    # database). Closing them from here is unsafe while Qt threads may still use
    # them, so hand the folder to a small detached process that deletes it once
    # this suite has exited (os._exit releases every handle).
    code = ("import os, shutil, sys, time\n"
            "for _ in range(120):\n"
            "    shutil.rmtree(sys.argv[1], ignore_errors=True)\n"
            "    if not os.path.exists(sys.argv[1]):\n"
            "        break\n"
            "    time.sleep(0.5)\n")
    try:
        import subprocess
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, "-c", code, str(root)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
    except Exception:
        pass                               # _purge_stale() removes it on a later run
