"""End the process immediately, without running Python or DLL teardown.

`os._exit()` skips Python's own teardown, but on Windows it still runs every
DLL's unload routine. PySide6's touches Python objects that are no longer valid
once the interpreter was never finalised, so the process crashed AFTER its exit
code was set (an access violation in pyside6.abi3.dll — invisible in the exit
code, but logged by Windows and occasionally surfacing as 0xC0000005).
TerminateProcess ends the process without unloading DLLs. Callers must have
saved and closed everything that matters first; this flushes logging and the
standard streams.
"""
from __future__ import annotations

import logging
import os
import sys


def hard_exit(code: int = 0) -> None:
    try:
        logging.shutdown()
    except Exception:
        pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            k32.TerminateProcess(k32.GetCurrentProcess(), int(code) & 0xFFFFFFFF)
        except Exception:
            pass
    os._exit(int(code))
