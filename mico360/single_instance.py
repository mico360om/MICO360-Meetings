"""Prevent multiple instances of the app from running at once — per user session.

Windows: a named kernel mutex in the session-local namespace
(``Local\\MICO360Meetings``). Each logged-on session (a second user on the
same PC via fast user switching or Remote Desktop) has its own namespace, so
another user's copy never blocks this one (M28). The OS releases the mutex
when the process ends, even after a crash, so there is nothing to clean up.

Elsewhere: an exclusive ``QLockFile`` in the per-user data folder.

``acquire()`` returns False ONLY when another instance is really running. Any
other failure (API unavailable, permissions…) is logged distinctly, recorded in
``last_error()``, and the app is allowed to start — blocking a user because the
lock mechanism failed would be worse than a rare double start.
"""
from __future__ import annotations

import logging
import sys

MUTEX_NAME = "Local\\MICO360Meetings"
_ERROR_ALREADY_EXISTS = 183
_ERROR_ACCESS_DENIED = 5          # exists, but created by another security context

_handle = None                    # Windows mutex HANDLE
_lockfile = None                  # QLockFile elsewhere
_last_error = ""

log = logging.getLogger("mico360.single_instance")


def last_error() -> str:
    """Why the lock could not be taken for a reason other than 'already running'
    ("" if none)."""
    return _last_error


def _acquire_windows() -> bool:
    global _handle, _last_error
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    h = k32.CreateMutexW(None, False, MUTEX_NAME)
    err = ctypes.get_last_error()
    if not h:
        if err == _ERROR_ACCESS_DENIED:
            log.warning("another instance is already running (mutex owned by "
                        "another security context)")
            return False
        _last_error = f"CreateMutexW failed (Windows error {err})"
        log.error("single-instance lock unavailable: %s — starting anyway", _last_error)
        return True
    if err == _ERROR_ALREADY_EXISTS:
        k32.CloseHandle(h)
        log.warning("another instance is already running in this session")
        return False
    _handle = h
    return True


def _release_windows() -> None:
    global _handle
    if _handle is None:
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle(_handle)
    finally:
        _handle = None


def _acquire_lockfile() -> bool:
    global _lockfile, _last_error
    from PySide6.QtCore import QLockFile
    from .config import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lf = QLockFile(str(DATA_DIR / "mico360.lock"))
    lf.setStaleLockTime(0)                 # a crashed owner's lock is detected via its PID
    if lf.tryLock(0):
        _lockfile = lf
        return True
    if lf.error() == QLockFile.LockError.LockFailedError:
        log.warning("another instance is already running (lock file held)")
        return False
    _last_error = f"lock file error {lf.error()}"
    log.error("single-instance lock unavailable: %s — starting anyway", _last_error)
    return True


def acquire() -> bool:
    """Return True if this is the only instance (in this user session), False
    only if another instance is already running. Calling it again while this
    process already holds the lock returns True."""
    global _last_error
    if _handle is not None or _lockfile is not None:
        return True
    _last_error = ""
    try:
        if sys.platform == "win32":
            return _acquire_windows()
        return _acquire_lockfile()
    except Exception as exc:               # never block start-up on a lock failure
        _last_error = f"{type(exc).__name__}: {exc}"
        log.exception("single-instance lock failed — starting anyway")
        return True


def release() -> None:
    global _lockfile
    if sys.platform == "win32":
        _release_windows()
    if _lockfile is not None:
        try:
            _lockfile.unlock()
        finally:
            _lockfile = None
