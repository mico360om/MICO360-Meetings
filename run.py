"""Convenience launcher:  python run.py   (also the PyInstaller entry point)

    MICO360Meetings.exe --self-test --report out.json [--audio speech.wav]
runs the built-in self-test (see mico360/selftest.py) instead of the app.
"""
import sys

if __name__ == "__main__" and "--self-test" in sys.argv:
    # Dispatch BEFORE importing mico360.main: that import loads the config, and the
    # self-test must redirect the data folder first so it never touches user data.
    from mico360.hard_exit import hard_exit
    from mico360.selftest import run_cli
    hard_exit(run_cli(sys.argv[1:]))   # no Python/DLL teardown (PySide6 crashes in it)

from mico360.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
