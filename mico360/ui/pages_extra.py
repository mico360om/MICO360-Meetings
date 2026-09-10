"""Updates page and Help / About / Terms / Privacy page."""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget,
)

from .. import __app_name__, __version__
from ..config import TMP_DIR
from ..core import updater
from .components import Card, EmptyState, section_title, subtitle, tip
from .context import AppContext
from .workers import UpdateCheckWorker, UpdateDownloadWorker

log = logging.getLogger("mico360.pages_extra")

SUPPORT_EMAIL = "info@mico360.com"


def _scroll(inner: QWidget, max_width: int = 1160) -> QScrollArea:
    sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QScrollArea.NoFrame)
    if max_width:
        inner.setMaximumWidth(max_width)
        sa.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    sa.setWidget(inner)
    return sa


_STATUS_COLORS = {
    updater.AVAILABLE: "#A83326", updater.UP_TO_DATE: "#22C55E",
    updater.DOWNLOADING: "#F59E0B", updater.INSTALLING: "#F59E0B",
    updater.COMPLETED: "#22C55E", updater.FAILED: "#EF4444",
    updater.NOT_CONFIGURED: "#F59E0B", updater.CHECKING: "#A79FA9",
}


class UpdatesPage(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx = ctx
        self.toast = toast
        self._worker = None
        self._dl = None
        self._info: updater.UpdateInfo | None = None

        content = QWidget()
        self.v = QVBoxLayout(content)
        self.v.setContentsMargins(24, 20, 24, 24)
        self.v.setSpacing(14)

        title = QLabel("Application Updates"); title.setObjectName("PageTitle")
        self.v.addWidget(title)
        self.v.addWidget(subtitle(f"{__app_name__} keeps your meetings private. Updates are optional and verified."))

        # current version + actions
        head = Card(); hl = QHBoxLayout(head); hl.setContentsMargins(16, 14, 16, 14)
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(section_title(__app_name__))
        self.cur_lbl = QLabel(f"Current version: v{__version__}")
        self.cur_lbl.setObjectName("Hint")
        col.addWidget(self.cur_lbl)
        hl.addLayout(col); hl.addStretch()
        self.repo_btn = QPushButton("GitHub repo")
        self.repo_btn.clicked.connect(self._open_repo)
        tip(self.repo_btn, "Open the project's GitHub page in your browser — release notes and "
                           "downloads live there")
        self.check_btn = QPushButton("Check for updates")
        self.check_btn.setObjectName("Primary")
        self.check_btn.clicked.connect(self.check)
        tip(self.check_btn, "Compare your version with the latest GitHub release. Requires "
                            "internet; nothing installs without your confirmation")
        hl.addWidget(self.repo_btn); hl.addWidget(self.check_btn)
        self.v.addWidget(head)

        # Git self-update — only when running from a source checkout.
        from ..core import git_update
        if git_update.is_git_checkout():
            self._build_git_card()

        # details card
        self.detail = Card()
        self.dl_layout = QVBoxLayout(self.detail)
        self.dl_layout.setContentsMargins(16, 14, 16, 14)
        self.dl_layout.setSpacing(8)
        self.status_row = QLabel("Click “Check for updates” to see the latest version.")
        self.status_row.setWordWrap(True)
        self.dl_layout.addWidget(self.status_row)
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.body.setVisible(False)
        self.body.setMinimumHeight(220)
        self.dl_layout.addWidget(self.body)

        self.progress = QProgressBar(); self.progress.setVisible(False)
        self.dl_layout.addWidget(self.progress)

        self.action_row = QHBoxLayout()
        self.download_btn = QPushButton("Download update"); self.download_btn.setObjectName("Primary")
        self.download_btn.clicked.connect(self._download); self.download_btn.setVisible(False)
        tip(self.download_btn, "Download the new installer in the background — you can keep "
                               "working while it downloads")
        self.install_btn = QPushButton("Install & restart"); self.install_btn.setObjectName("Primary")
        self.install_btn.clicked.connect(self._install); self.install_btn.setVisible(False)
        tip(self.install_btn, "Close the app, install the update silently and reopen it. "
                              "Unsaved work is autosaved first")
        self.release_btn = QPushButton("Open release page")
        self.release_btn.clicked.connect(self._open_release); self.release_btn.setVisible(False)
        tip(self.release_btn, "Open the release on GitHub to download the installer manually")
        self.retry_btn = QPushButton("Retry"); self.retry_btn.clicked.connect(self.check)
        self.retry_btn.setVisible(False)
        tip(self.retry_btn, "Try the update check again — see the message above for what failed")
        self.action_row.addStretch()
        for b in (self.release_btn, self.retry_btn, self.download_btn, self.install_btn):
            self.action_row.addWidget(b)
        self.dl_layout.addLayout(self.action_row)
        self.v.addWidget(self.detail)
        self.v.addStretch()

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(content))

    # -- git self-update (source checkouts) --------------------------------
    def _build_git_card(self):
        from ..core import git_update
        self._git_worker = None
        card = Card(); gl = QVBoxLayout(card); gl.setContentsMargins(16, 14, 16, 14); gl.setSpacing(8)
        row = QHBoxLayout()
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(section_title("Source update (Git)"))
        self.git_info = QLabel(f"Running from source · commit {git_update.current_commit() or '—'}")
        self.git_info.setObjectName("Hint"); self.git_info.setWordWrap(True)
        col.addWidget(self.git_info)
        row.addLayout(col); row.addStretch()
        self.git_check_btn = QPushButton("Check for updates")
        self.git_check_btn.clicked.connect(self._git_check)
        tip(self.git_check_btn, "Fetch the latest commits from the git remote and show if you're behind")
        self.git_update_btn = QPushButton("Update & restart"); self.git_update_btn.setObjectName("Primary")
        self.git_update_btn.clicked.connect(self._git_pull); self.git_update_btn.setVisible(False)
        tip(self.git_update_btn, "git pull (fast-forward only), then restart the app to load the new code")
        row.addWidget(self.git_check_btn); row.addWidget(self.git_update_btn)
        gl.addLayout(row)
        self.git_auto = QCheckBox("Auto-update from Git on startup")
        self.git_auto.setChecked(self.ctx.settings.get("git_auto_update", False))
        self.git_auto.toggled.connect(lambda on: self.ctx.settings.set("git_auto_update", on))
        tip(self.git_auto, "On startup, fast-forward to the latest source and offer to restart. "
                           "Local changes are never overwritten")
        gl.addWidget(self.git_auto)
        self.git_status = QLabel(""); self.git_status.setObjectName("Hint"); self.git_status.setWordWrap(True)
        gl.addWidget(self.git_status)
        self.v.insertWidget(3, card)      # just under the version card

    def _git_check(self):
        from .workers import GitUpdateWorker
        self.git_check_btn.setEnabled(False); self.git_status.setText("Checking the git remote…")
        self._git_worker = GitUpdateWorker("check")
        self._git_worker.done.connect(self._on_git_check)
        self._git_worker.start()

    def _on_git_check(self, res: dict):
        self.git_check_btn.setEnabled(True)
        if not res.get("ok"):
            self.git_update_btn.setVisible(False)
            self.git_status.setText(f"⚠ {res.get('error', 'Check failed.')}")
            return
        behind = res.get("behind", 0)
        if behind > 0:
            self.git_status.setText(f"{behind} new commit(s) on {res.get('branch', '')} — ready to update.")
            self.git_update_btn.setVisible(True)
        else:
            self.git_status.setText("✓ You're on the latest source.")
            self.git_update_btn.setVisible(False)

    def _git_pull(self):
        from .workers import GitUpdateWorker
        self.git_update_btn.setEnabled(False); self.git_status.setText("Updating from git…")
        self._git_worker = GitUpdateWorker("pull")
        self._git_worker.done.connect(self._on_git_pull)
        self._git_worker.start()

    def _on_git_pull(self, res: dict):
        self.git_update_btn.setEnabled(True)
        if not res.get("ok"):
            self.git_status.setText(f"⚠ {res.get('error', 'Update failed.')}")
            return
        from ..core import git_update
        self.git_info.setText(f"Running from source · commit {git_update.current_commit() or '—'}")
        if res.get("updated"):
            self.git_status.setText("✓ Updated. Restart to apply.")
            self.git_update_btn.setVisible(False)
            self._offer_restart()
        else:
            self.git_status.setText("✓ Already up to date.")
            self.git_update_btn.setVisible(False)

    def _offer_restart(self):
        from PySide6.QtWidgets import QApplication
        if QMessageBox.question(self, "Restart",
                                "The update was pulled. Restart now to apply it?") != QMessageBox.Yes:
            return
        import subprocess
        try:
            subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
        except Exception:
            self.toast.show_message("Couldn't relaunch — please restart the app manually.", "warn")
            return
        QApplication.instance().quit()

    # -- check --------------------------------------------------------------
    def check(self):
        repo = self.ctx.settings.get("github_repo", "")
        self._set_status(updater.CHECKING, "Checking for updates…")
        self.check_btn.setEnabled(False)
        for b in (self.download_btn, self.install_btn, self.release_btn, self.retry_btn):
            b.setVisible(False)
        self._worker = UpdateCheckWorker(repo)
        self._worker.done.connect(self._on_checked)
        self._worker.start()

    def _on_checked(self, info: updater.UpdateInfo):
        self.check_btn.setEnabled(True)
        self._info = info
        self.repo_btn.setVisible(bool(info.repo_url))
        self._render(info)
        if info.update_available and self.toast:
            self.toast.show_message(
                f"Update available: v{info.latest_version}", "info", 6000)

    def _render(self, info: updater.UpdateInfo):
        self._set_status(info.status, self._status_text(info))
        if info.status in (updater.AVAILABLE, updater.UP_TO_DATE, updater.COMPLETED):
            self.body.setVisible(True)
            self.body.setHtml(self._build_html(info))
        else:
            self.body.setVisible(info.status == updater.AVAILABLE)

        self.retry_btn.setVisible(info.status in (updater.FAILED, updater.NOT_CONFIGURED))
        avail = info.status == updater.AVAILABLE
        self.download_btn.setVisible(avail and bool(info.download_url))
        self.release_btn.setVisible(avail and not info.download_url and bool(info.release_url))
        self.install_btn.setVisible(False)

    def _status_text(self, info: updater.UpdateInfo) -> str:
        if info.status == updater.AVAILABLE:
            return (f"<b>Update available:</b> v{info.current_version} &rarr; "
                    f"<b>v{info.latest_version}</b>  &nbsp;•&nbsp; {info.size_human}  "
                    f"&nbsp;•&nbsp; released {info.release_date or 'recently'}")
        if info.status == updater.UP_TO_DATE:
            return f"<b>You’re up to date.</b> v{info.current_version} is the latest version."
        if info.status == updater.COMPLETED:
            return (f"<b>Update completed.</b> Installed v{info.latest_version} at "
                    f"{time.strftime('%H:%M:%S')}.")
        if info.status in (updater.FAILED, updater.NOT_CONFIGURED):
            return f"<b>{info.status}.</b> {info.error}"
        return info.status

    def _build_html(self, info: updater.UpdateInfo) -> str:
        def lst(items):
            return "".join(f"<li>{i}</li>" for i in items) if items else "<li>Not specified</li>"
        restart = "Yes — the app will restart after installing." if info.restart_required else "No"
        return f"""
        <div style='font-family:Segoe UI; font-size:13px;'>
          <table cellpadding='3'>
            <tr><td><b>Application</b></td><td>{info.app_name}</td></tr>
            <tr><td><b>Current version</b></td><td>v{info.current_version}</td></tr>
            <tr><td><b>New version</b></td><td>v{info.latest_version or '—'}</td></tr>
            <tr><td><b>Update size</b></td><td>{info.size_human if info.size_bytes else '—'}</td></tr>
            <tr><td><b>Release date</b></td><td>{info.release_date or '—'}</td></tr>
            <tr><td><b>Restart required</b></td><td>{restart}</td></tr>
          </table>
          <h4>Description</h4>
          <p>{info.description or 'Not specified'}</p>
          <h4>New features</h4><ul>{lst(info.features)}</ul>
          <h4>Bugs fixed</h4><ul>{lst(info.fixes)}</ul>
          <h4>Security improvements</h4><ul>{lst(info.security)}</ul>
          <p><a href='{info.release_url or info.repo_url}'>View full release notes on GitHub</a></p>
        </div>"""

    def _set_status(self, status: str, text: str):
        color = _STATUS_COLORS.get(status, "#A79FA9")
        self.status_row.setText(
            f"<span style='color:{color}; font-weight:700;'>● {status}</span> &nbsp; {text}")

    # -- download / install -------------------------------------------------
    def _download(self):
        if not (self._info and self._info.download_url):
            return
        dest = str(TMP_DIR / Path(self._info.download_url).name)
        self.progress.setVisible(True); self.progress.setValue(0)
        self.download_btn.setEnabled(False)
        self._set_status(updater.DOWNLOADING, "Downloading update…")
        self._dl = UpdateDownloadWorker(self._info, dest)
        self._dl.progress.connect(self._on_dl_progress)
        self._dl.finished_ok.connect(self._on_dl_done)
        self._dl.failed.connect(self._on_dl_failed)
        self._dl.start()

    def _on_dl_progress(self, frac, read, total):
        self.progress.setValue(int(frac * 100))
        self._set_status(updater.DOWNLOADING,
                         f"Downloading… {updater.UpdateInfo(size_bytes=read).size_human}"
                         f" of {updater.UpdateInfo(size_bytes=total).size_human} ({int(frac*100)}%)")

    def _on_dl_done(self, path):
        self._downloaded = path
        self._verify_note = getattr(self._dl, "verify_note", "")
        self._verify_sha = getattr(self._dl, "expected_sha256", "")
        self.download_btn.setEnabled(True)
        self.progress.setValue(100)
        msg = (f"Download verified ({self._verify_note}). Ready to install."
               if self._verify_note else "Download complete. Ready to install.")
        self._set_status(updater.INSTALLING, msg)
        self.download_btn.setVisible(False)
        self.install_btn.setVisible(True)

    def _on_dl_failed(self, msg):
        self.download_btn.setEnabled(True)
        self.progress.setVisible(False)
        self._set_status(updater.FAILED, f"Download failed: {msg}")
        self.retry_btn.setVisible(True)

    def _install(self):
        path = getattr(self, "_downloaded", "")
        if not path or not Path(path).exists():
            return
        from PySide6.QtWidgets import QApplication, QMessageBox
        # Final integrity guard: re-check the hash in case the file was altered on
        # disk between download and this click.
        expected = getattr(self, "_verify_sha", "")
        if expected:
            try:
                if updater.sha256_file(path).lower() != expected.lower():
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    self.install_btn.setVisible(False)
                    self._set_status(updater.FAILED,
                                     "Integrity check failed — the installer changed on disk.")
                    QMessageBox.critical(
                        self, "Update blocked",
                        "The downloaded installer no longer matches the published checksum "
                        "and has been removed. Please download it again.")
                    return
            except Exception:
                log.warning("install-time hash re-check failed", exc_info=True)
        note = getattr(self, "_verify_note", "")
        caution = ""
        if "no published checksum" in note or "unsigned" in note:
            caution = ("\n\nNote: this release could not be fully verified "
                       f"({note}). Only continue if you trust the source.")
        if QMessageBox.question(
                self, "Install update",
                f"{__app_name__} will close, install v"
                f"{self._info.latest_version if self._info else ''}, then reopen.{caution}\n\nContinue?"
        ) != QMessageBox.Yes:
            return
        try:
            import subprocess
            if sys.platform == "win32":
                # Inno Setup silent install that closes + relaunches the app.
                subprocess.Popen(
                    [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                     "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
                    close_fds=True)
            else:
                os.startfile(path)  # noqa: S606
            self._set_status(updater.INSTALLING, "Installing update — the app will reopen…")
            # give the installer a moment to start, then quit so files can be replaced
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, QApplication.instance().quit)
        except Exception as exc:
            self._set_status(updater.FAILED, f"Could not launch installer: {exc}")

    # -- links --------------------------------------------------------------
    def _open_repo(self):
        url = updater.repo_url(self.ctx.settings.get("github_repo", ""))
        if url:
            QDesktopServices.openUrl(QUrl(url))
        else:
            self.toast.show_message("Set the GitHub repo in Settings first.", "warn")

    def _open_release(self):
        if self._info and self._info.release_url:
            QDesktopServices.openUrl(QUrl(self._info.release_url))


# ===========================================================================
# Action Items — tasks across all meetings
# ===========================================================================
_STATUS_COLOR = {"Pending": "#F59E0B", "In Progress": "#A83326",
                 "Done": "#22C55E", "Cancelled": "#9A9AA0"}


class ActionItemsPage(QWidget):
    def __init__(self, ctx: AppContext, toast, on_open_meeting=None):
        super().__init__()
        self.ctx = ctx
        self.toast = toast
        self.on_open_meeting = on_open_meeting
        self._items = []

        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 24); v.setSpacing(12)
        title = QLabel("Action Items"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Every action item from all your meetings in one place. "
                             "Set a task's status with the dropdown; double-click a meeting to open it."))

        bar = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search task, person, meeting or status…")
        self.search.textChanged.connect(self.reload)
        tip(self.search, "Filter as you type — matches task text, responsible person, meeting "
                         "title and status")
        refresh = QPushButton("Refresh"); refresh.clicked.connect(self.reload)
        tip(refresh, "Re-scan all meetings in History for action items")
        self.export_btn = QPushButton("Export CSV…"); self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self._export)
        tip(self.export_btn, "Save the currently filtered list as a CSV file you can open in Excel")
        bar.addWidget(self.search); bar.addWidget(refresh); bar.addWidget(self.export_btn)
        v.addLayout(bar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Task", "Responsible", "Deadline", "Status", "Meeting", "Date"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        tip(self.table, "All action items found in your meetings' minutes. Use the Status dropdown "
                        "to set Pending / In Progress / Done / Cancelled; double-click a Meeting "
                        "cell to open that meeting")
        v.addWidget(self.table, 1)
        self.empty = EmptyState("✔", "No action items yet",
                                "Action items appear here when a meeting's minutes include an "
                                "“Action Items” table. Generate minutes in New Meeting to populate this.")
        self.empty.setVisible(False)
        v.addWidget(self.empty, 1)

        self.summary = QLabel(""); self.summary.setObjectName("Hint")
        v.addWidget(self.summary)

    def reload(self):
        from ..core.tasks import STATUS_CYCLE
        self._items = self.ctx.action_items.all_items(self.search.text())
        self.table.setRowCount(len(self._items))
        for r, it in enumerate(self._items):
            # text columns
            for c, val in ((0, it.task), (1, it.owner or "—"), (2, it.deadline or "—"),
                           (4, it.meeting_title), (5, it.meeting_date)):
                self.table.setItem(r, c, QTableWidgetItem(val))
            # Status: an inline dropdown (obvious, vs the old double-click-to-cycle)
            combo = QComboBox()
            combo.addItems(STATUS_CYCLE)
            if it.status not in STATUS_CYCLE:
                combo.addItem(it.status)
            combo.setCurrentText(it.status)
            combo.setStyleSheet(
                f"color:{_STATUS_COLOR.get(it.status, '#9A9AA0')}; font-weight:600;")
            combo.setToolTip("Change this task's status")
            combo.activated.connect(lambda _i, row=r: self._change_status(row))
            self.table.setCellWidget(r, 3, combo)
        done = sum(1 for i in self._items if i.status == "Done")
        cancelled = sum(1 for i in self._items if i.status == "Cancelled")
        open_ = len(self._items) - done - cancelled     # Cancelled is not "open"
        cancelled_txt = f" · {cancelled} cancelled" if cancelled else ""
        self.summary.setText(f"{len(self._items)} action item(s) · {done} done · "
                             f"{open_} open{cancelled_txt}  ·  set status with the dropdown")
        # empty state: distinguish "none anywhere" from "no search matches"
        if self._items:
            self.table.setVisible(True); self.empty.setVisible(False); self.summary.setVisible(True)
        else:
            if self.search.text().strip():
                self.empty.set(f"No action items match “{self.search.text().strip()}”",
                               "Try a different search, or clear the box to see everything.", "🔍")
            else:
                self.empty.set("No action items yet",
                               "Action items appear here when a meeting's minutes include an "
                               "“Action Items” table. Generate minutes in New Meeting to populate this.", "✔")
            self.table.setVisible(False); self.empty.setVisible(True); self.summary.setVisible(False)

    def _change_status(self, row: int):
        if 0 <= row < len(self._items):
            combo = self.table.cellWidget(row, 3)
            if combo is not None:
                self.ctx.action_items.set_status(self._items[row], combo.currentText())
                self.reload()

    def _on_double_click(self, row, col):
        if not (0 <= row < len(self._items)):
            return
        if col == 4 and self.on_open_meeting:           # Meeting → open it
            m = self.ctx.history.get(self._items[row].meeting_id)
            if m:
                self.on_open_meeting(m)

    def _export(self):
        if not self._items:
            self.toast.show_message("No action items to export.", "warn"); return
        path, _ = QFileDialog.getSaveFileName(self, "Export action items", "action-items.csv",
                                              "CSV (*.csv)")
        if path:
            try:
                self.ctx.action_items.export_csv(path, self._items)
                self.toast.show_message(f"Exported {len(self._items)} items.", "success")
            except Exception as exc:
                QMessageBox.critical(self, "Export failed", str(exc))


# ===========================================================================
# Help / About / Terms / Privacy
# ===========================================================================
class HelpPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 24); v.setSpacing(12)
        title = QLabel("Help & About"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Getting started, contact, and legal information."))

        tabs = QTabWidget()
        tabs.addTab(self._tab(self._about_html()), "About Us")
        tabs.addTab(self._tab(self._help_html()), "Help")
        tabs.addTab(self._tab(self._shortcuts_html()), "Shortcuts")
        tabs.addTab(self._tab(self._terms_html()), "Terms & Conditions")
        tabs.addTab(self._tab(self._privacy_html()), "Privacy Policy")
        v.addWidget(tabs, 1)

    def _tab(self, html: str) -> QWidget:
        from PySide6.QtGui import QColor, QPalette
        b = QTextBrowser(); b.setOpenExternalLinks(True)
        # Brand-red links (readable on both themes) instead of default blue.
        pal = b.palette(); pal.setColor(QPalette.Link, QColor("#C0392B")); b.setPalette(pal)
        b.setHtml(html)
        return b

    def _wrap(self, inner: str) -> str:
        return f"<div style='font-family:Segoe UI; font-size:13.5px; line-height:1.5;'>{inner}</div>"

    def _about_html(self) -> str:
        from PySide6.QtCore import QUrl
        from ..config import resource_path
        repo = updater.repo_url(self.ctx.settings.get("github_repo", "")) or "(provided on release)"
        logo_url = QUrl.fromLocalFile(str(resource_path("assets", "logo.png"))).toString()
        # White chip so the full-colour brand lockup stays legible in either theme.
        logo_html = (f"<table cellpadding='12' style='margin-bottom:4px;'><tr>"
                     f"<td bgcolor='#FFFFFF'><img src='{logo_url}' width='210'></td>"
                     f"</tr></table>")
        return self._wrap(f"""
          {logo_html}
          <h2>{__app_name__}</h2>
          <p><b>Version:</b> v{__version__}</p>
          <p>{__app_name__} turns your meeting recordings and transcripts into
          professional minutes. Audio is transcribed on your computer with Whisper,
          and minutes are written by a <b>local Ollama AI model by default</b>, so
          your meeting data stays on your machine. You can optionally switch the AI
          mode to <b>MICO360 Cloud</b> in Settings — see the Privacy Policy for what
          that sends.</p>
          <h3>Contact</h3>
          <p>Email: <a href='mailto:{SUPPORT_EMAIL}'>{SUPPORT_EMAIL}</a><br>
             Website / Repository: {repo}</p>
          <p style='color:#888'>© {time.strftime('%Y')} MICO360. All rights reserved.</p>""")

    def _shortcuts_html(self) -> str:
        def rows(pairs):
            return "".join(
                f"<tr><td style='padding:3px 20px 3px 0; white-space:nowrap;'>"
                f"<code>{k}</code></td><td style='padding:3px 0;'>{a}</td></tr>"
                for k, a in pairs)
        nav = rows((f"Ctrl+{i + 1}", label) for i, label in enumerate(
            ["New Meeting", "History", "Action Items", "Company Profiles",
             "Prompt Library", "Updates", "Help & About", "Settings"]))
        actions = rows([
            ("Ctrl+N", "Start a new meeting"),
            ("Ctrl+G", "Generate / create the minutes"),
            ("Ctrl+E", "Export the minutes"),
            ("Ctrl+S", "Save to History"),
            ("Ctrl+Shift+C", "Copy the minutes (with formatting)"),
            ("Ctrl+F", "Jump to History and search"),
            ("F1", "Open this Help page"),
        ])
        return self._wrap(f"""
          <h2>Keyboard shortcuts</h2>
          <h3>Navigate</h3>
          <table cellspacing='0'>{nav}</table>
          <h3>Actions</h3>
          <table cellspacing='0'>{actions}</table>
          <p style='color:#888'>Every control also shows its shortcut in its tooltip on hover.</p>""")

    def _help_html(self) -> str:
        return self._wrap(f"""
          <h2>Getting started</h2>
          <ol>
            <li><b>New Meeting → Step 1:</b> drop an audio/video file, record audio
                or your screen, or paste a transcript.</li>
            <li><b>Step 2:</b> review and edit the transcript.</li>
            <li><b>Step 3:</b> choose the Ollama <b>model</b> and the output
                <b>style</b>, optionally tweak the prompt, then <b>Generate</b>.</li>
            <li><b>Step 4:</b> edit, copy, save to history, or export to Word/PDF/TXT.</li>
          </ol>
          <h3>Recording</h3>
          <p>The Record tab supports microphone audio, screen+audio, and camera+audio.
             Live details show status, timer, format, microphone, file size and save
             location, with a red blinking indicator and an audio visualizer.</p>
          <h3>Tips</h3>
          <ul>
            <li>Pick a smaller Whisper model (tiny/base) for speed on low-end PCs.</li>
            <li>Make sure Ollama is running and a model is pulled
                (e.g. <code>ollama pull llama3.1</code>).</li>
            <li>Set a Company Profile to brand your exported PDFs/Word docs.</li>
          </ul>
          <h3>Need help?</h3>
          <p>Email <a href='mailto:{SUPPORT_EMAIL}'>{SUPPORT_EMAIL}</a>. Logs are in
             the app data folder under <code>logs/</code>.</p>""")

    def _terms_html(self) -> str:
        return self._wrap(f"""
          <h2>Terms &amp; Conditions</h2>
          <p>By using {__app_name__} you agree to the following terms.</p>
          <h3>1. Licence</h3>
          <p>{__app_name__} is provided for your business and personal use. The
             software and its branding remain the property of MICO360.</p>
          <h3>2. Acceptable use</h3>
          <p>You are responsible for ensuring you have the right to record and process
             any meeting, and for complying with applicable recording-consent laws in
             your jurisdiction.</p>
          <h3>3. No warranty</h3>
          <p>The software is provided “as is”, without warranty of any kind. AI-generated
             minutes may contain errors and should be reviewed before use.</p>
          <h3>4. Limitation of liability</h3>
          <p>MICO360 is not liable for any loss arising from use of the software,
             including inaccurate transcriptions or minutes.</p>
          <h3>5. Third-party components</h3>
          <p>The app uses open-source components (Whisper, Ollama, PySide6 and others)
             under their respective licences.</p>
          <p>Questions: <a href='mailto:{SUPPORT_EMAIL}'>{SUPPORT_EMAIL}</a></p>""")

    def _privacy_html(self) -> str:
        return self._wrap(f"""
          <h2>Privacy Policy</h2>
          <p><b>Your data stays on your device.</b></p>
          <h3>What we collect</h3>
          <p><b>Nothing, in Local mode.</b> With the AI mode set to <b>Local (Ollama)</b>
             — the default — {__app_name__} does not send your audio, video, transcripts
             or minutes to any server: transcription (Whisper) and minutes generation
             (Ollama) both run on your computer. See “AI mode” below for the one
             exception you can opt into.</p>
          <h3>Local storage</h3>
          <p>Recordings, transcripts, minutes, company profiles and history are stored
             only in your local app-data folder. You can delete them at any time.</p>
          <h3>Network access</h3>
          <p>The only optional network activity is: (1) the one-time download of a
             Whisper or Ollama model you choose, (2) checking GitHub for application
             updates when you click “Check for updates”, (3) the opt-in crash
             reporter — if (and only if) you choose to report a problem, it opens a
             pre-filled GitHub issue or email that you review and send yourself, and
             (4) the <b>Email minutes</b> feature — if you use it, the minutes you
             choose are sent through the SMTP/email server you configure in Settings
             (e.g. Mailjet). Nothing is transmitted automatically.</p>
          <h3>AI mode (local vs cloud)</h3>
          <p>Minutes are written by the <b>AI mode</b> you select in Settings.
             <b>Local (Ollama)</b>, the default, keeps everything on your computer.
             If you switch to <b>MICO360 Cloud</b>, your transcript is sent to the
             MICO360 Connect AI server to generate the minutes, and the result is
             returned to you — choose Local if you need minutes generation to stay
             on-device. <b>Transcription (Whisper) is always local in both modes</b>,
             so your audio and video never leave your computer regardless of AI mode.</p>
          <h3>Contact</h3>
          <p>Privacy questions: <a href='mailto:{SUPPORT_EMAIL}'>{SUPPORT_EMAIL}</a></p>""")
