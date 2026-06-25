"""Opt-in crash reporter.

On an unhandled exception it:
  1. logs the traceback,
  2. writes a self-contained crash report (traceback + tail of the app log +
     app/system info) to the logs folder,
  3. shows a dialog (only if the user hasn't disabled it) that lets the user
     REVIEW and EDIT the report, then either open a pre-filled GitHub issue in
     their browser, email it, or just save it.

Nothing is ever transmitted automatically — reporting is a deliberate click,
and the GitHub path uses a pre-filled "new issue" URL (no tokens, no secrets):
the user submits it themselves while logged into their own GitHub account.
"""
from __future__ import annotations

import logging
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

from . import __app_name__, __version__
from .config import LOG_DIR

log = logging.getLogger("mico360.crash")

LOG_TAIL_LINES = 120
SUPPORT_EMAIL = "info@mico360.com"
_MAX_URL_BODY = 6000          # keep the GitHub URL under browser/length limits

_settings = None
_installed = False
_in_handler = False           # guard against recursive crashes


# ---------------------------------------------------------------------------
def install(settings) -> None:
    """Route unhandled exceptions through the crash reporter."""
    global _settings, _installed
    _settings = settings
    if _installed:
        return
    sys.excepthook = _excepthook
    # also catch exceptions escaping Qt threads where possible
    try:
        import threading
        threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)
    except Exception:
        pass
    _installed = True


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    global _in_handler
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.critical("UNHANDLED EXCEPTION", exc_info=(exc_type, exc_value, exc_tb))
    if _in_handler:                       # a crash inside the handler — bail out safely
        return
    _in_handler = True
    try:
        report = build_report(exc_type, exc_value, exc_tb)
        path = _write_report(report)
        _maybe_show_dialog(report, path, exc_type, exc_value)
    except Exception:
        log.exception("crash reporter itself failed")
    finally:
        _in_handler = False


# ---------------------------------------------------------------------------
def build_report(exc_type, exc_value, exc_tb) -> str:
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    info = [
        f"{__app_name__} v{__version__} — crash report",
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"OS: {sys.platform}",
        f"Python: {sys.version.split()[0]}",
        "",
        "=== Traceback ===",
        tb.strip(),
        "",
        "=== Recent log (last %d lines) ===" % LOG_TAIL_LINES,
        _tail_log(LOG_TAIL_LINES),
    ]
    return "\n".join(info)


def _tail_log(n: int) -> str:
    f = LOG_DIR / "app.log"
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return "(log unavailable)"


def _write_report(report: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"crash_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        path.write_text(report, encoding="utf-8")
    except Exception:
        log.warning("could not write crash report", exc_info=True)
    return path


def github_issue_url(repo: str, title: str, body: str) -> str:
    repo = (repo or "").strip().strip("/")
    body = body if len(body) <= _MAX_URL_BODY else body[:_MAX_URL_BODY] + "\n…(truncated — full report attached)"
    q = urllib.parse.urlencode({"title": title, "labels": "crash", "body": body})
    return f"https://github.com/{repo}/issues/new?{q}"


def mailto_url(title: str, body: str) -> str:
    body = body if len(body) <= _MAX_URL_BODY else body[:_MAX_URL_BODY] + "\n…(truncated)"
    q = urllib.parse.urlencode({"subject": title, "body": body})
    return f"mailto:{SUPPORT_EMAIL}?{q}"


# ---------------------------------------------------------------------------
def _maybe_show_dialog(report: str, path: Path, exc_type, exc_value) -> None:
    if _settings is not None and not _settings.get("crash_reporter", True):
        return                                  # user opted out of the dialog
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return
    if QApplication.instance() is None:         # no GUI (headless) — report already saved
        return
    repo = _settings.get("github_repo", "") if _settings else ""
    try:
        dlg = CrashDialog(report, path, repo, f"{exc_type.__name__}: {exc_value}")
        dlg.exec()
    except Exception:
        log.exception("could not show crash dialog")


def _build_dialog_class():
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import (
        QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
        QPlainTextEdit, QPushButton, QVBoxLayout,
    )

    class _CrashDialog(QDialog):
        def __init__(self, report, path, repo, summary, parent=None):
            super().__init__(parent)
            self.report = report
            self.path = path
            self.repo = repo
            self.setWindowTitle(f"{__app_name__} — unexpected error")
            self.resize(720, 600)

            v = QVBoxLayout(self)
            head = QLabel(f"<b>{__app_name__} hit an unexpected error.</b>")
            v.addWidget(head)
            v.addWidget(QLabel(summary[:200]))
            note = QLabel(
                "Nothing is sent automatically. You can review and edit the report "
                "below, then choose to report it on GitHub (opens your browser) or "
                "email it. The full report is saved on your computer either way.")
            note.setWordWrap(True); note.setObjectName("Hint")
            v.addWidget(note)

            v.addWidget(QLabel("What were you doing when it happened? (optional)"))
            self.what = QLineEdit()
            self.what.setPlaceholderText("e.g. exporting minutes to PDF…")
            v.addWidget(self.what)

            v.addWidget(QLabel("Report (you can edit / redact before sending):"))
            self.text = QPlainTextEdit(report)
            self.text.setMinimumHeight(280)
            v.addWidget(self.text, 1)

            self.remember = QCheckBox("Don't show this crash dialog again")
            v.addWidget(self.remember)

            row = QHBoxLayout()
            saved = QLabel(f"Saved to: {path.name}")
            saved.setObjectName("Hint")
            row.addWidget(saved)
            row.addStretch()

            open_btn = QPushButton("Open folder"); open_btn.clicked.connect(self._open_folder)
            email_btn = QPushButton("Email report"); email_btn.clicked.connect(self._email)
            self.gh_btn = QPushButton("Report on GitHub"); self.gh_btn.setObjectName("Primary")
            self.gh_btn.clicked.connect(self._github)
            if not (repo and "/" in repo):
                self.gh_btn.setEnabled(False)
                self.gh_btn.setToolTip("Set your GitHub repo in Settings to enable this.")
            close = QPushButton("Close")
            close.clicked.connect(self._close)
            for b in (open_btn, email_btn, close, self.gh_btn):
                row.addWidget(b)
            v.addLayout(row)

        def _full_body(self) -> str:
            what = self.what.text().strip()
            body = self.text.toPlainText()
            if what:
                body = f"**What I was doing:** {what}\n\n```\n{body}\n```"
            else:
                body = f"```\n{body}\n```"
            return body

        def _title(self) -> str:
            return f"[Crash] {__app_name__} v{__version__}"

        def _github(self):
            url = github_issue_url(self.repo, self._title(), self._full_body())
            QDesktopServices.openUrl(QUrl(url))
            self._open_folder()
            QMessageBox.information(
                self, "Report on GitHub",
                "Your browser is opening a pre-filled GitHub issue. Review it and "
                "click 'Submit new issue'. You can drag the saved crash file into "
                "the issue to attach the full log.")
            self._persist_pref(); self.accept()

        def _email(self):
            QDesktopServices.openUrl(QUrl(mailto_url(self._title(), self._full_body())))
            self._persist_pref(); self.accept()

        def _open_folder(self):
            import os, subprocess
            folder = str(self.path.parent)
            try:
                if sys.platform == "win32":
                    os.startfile(folder)  # noqa: S606
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", folder])
                else:
                    subprocess.Popen(["xdg-open", folder])
            except Exception:
                pass

        def _close(self):
            self._persist_pref(); self.reject()

        def _persist_pref(self):
            if self.remember.isChecked() and _settings is not None:
                _settings.set("crash_reporter", False)

    return _CrashDialog


# expose a lazily-built dialog class
def CrashDialog(*args, **kwargs):
    cls = _build_dialog_class()
    return cls(*args, **kwargs)
