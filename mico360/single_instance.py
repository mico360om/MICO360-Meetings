"""Prevent multiple instances of the app from running at once.

Uses an exclusive bind on a fixed loopback port. The socket is held for the
process lifetime; if a second instance cannot bind, one is already running.
This is cross-platform and needs no external files to clean up.
"""
from __future__ import annotations

import logging
import socket

_LOCK_PORT = 47917  # arbitrary, app-specific
_sock: socket.socket | None = None

log = logging.getLogger("mico360.single_instance")


def acquire() -> bool:
    """Return True if this is the only instance, False if one already runs."""
    global _sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # SO_REUSEADDR is intentionally NOT set so a second bind fails fast.
        s.bind(("127.0.0.1", _LOCK_PORT))
        s.listen(1)
        _sock = s
        return True
    except OSError:
        s.close()
        log.warning("another instance is already running (port %s busy)", _LOCK_PORT)
        return False


def release() -> None:
    global _sock
    if _sock is not None:
        try:
            _sock.close()
        finally:
            _sock = None
