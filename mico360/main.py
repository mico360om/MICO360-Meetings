"""Application entry point."""
from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __app_name__
from .config import ensure_dirs, resource_path, setup_logging
from . import single_instance


def _set_windows_appid() -> None:
    """Make Windows show our icon (not python's) on the taskbar + group correctly."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MICO360.Meetings.1")
        except Exception:
            pass


def main() -> int:
    ensure_dirs()
    log = setup_logging()
    _set_windows_appid()

    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setOrganizationName("MICO360")
    icon_path = resource_path("assets", "app.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Single-instance guard
    if not single_instance.acquire():
        QMessageBox.information(
            None, __app_name__,
            f"{__app_name__} is already running.\nPlease use the existing window.")
        return 0

    try:
        from .ui.context import AppContext
        from . import crash_reporter
        ctx = AppContext()
        crash_reporter.install(ctx.settings)        # opt-in crash reporting
        from .ui.main_window import MainWindow
        win = MainWindow(ctx)
        win.show()
        app.aboutToQuit.connect(ctx.close)
        rc = app.exec()
        single_instance.release()
        return rc
    except Exception as exc:
        log.exception("fatal error during startup")
        try:
            from . import crash_reporter
            report = crash_reporter.build_report(type(exc), exc, exc.__traceback__)
            path = crash_reporter._write_report(report)
            crash_reporter._maybe_show_dialog(report, path, type(exc), exc)
        except Exception:
            QMessageBox.critical(None, __app_name__,
                                 "A fatal error occurred during startup.\n"
                                 "See the log file for details.")
        single_instance.release()
        return 1


if __name__ == "__main__":
    sys.exit(main())
