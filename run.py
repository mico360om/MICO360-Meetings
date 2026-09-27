"""Convenience launcher:  python run.py   (also the PyInstaller entry point)

    MICO360Meetings.exe --self-test --report out.json [--audio speech.wav]
runs the built-in self-test (see mico360/selftest.py) instead of the app.
"""
import sys

if __name__ == "__main__" and "--self-test" in sys.argv:
    # Dispatch BEFORE importing mico360.main: that import loads the config, and the
    # self-test must redirect the data folder first so it never touches user data.
    import os
    from mico360.selftest import run_cli
    rc = run_cli(sys.argv[1:])
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except Exception:
            pass
    os._exit(rc)          # skip interpreter teardown (PySide6 + Python 3.14 quirk)

from mico360.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
