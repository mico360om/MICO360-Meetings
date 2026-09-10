"""Updates page and Help / About / Terms / Privacy page."""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget,
)

from .. import __app_name__, __version__
from ..config import TMP_DIR
from ..core import updater
from . import metrics as M
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
        self.v.setContentsMargins(*M.PAGE_MARGINS)
        self.v.setSpacing(M.PAGE_GAP)

        title = QLabel("Application Updates"); title.setObjectName("PageTitle")
        self.v.addWidget(title)
        self.v.addWidget(subtitle(f"{__app_name__} keeps your meetings private. Updates are optional and verified."))

        # current version + actions
        head = Card(); hl = QHBoxLayout(head); hl.setContentsMargins(M.LG, M.LG, M.LG, M.LG)
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
        self.dl_layout.setContentsMargins(M.LG, M.LG, M.LG, M.LG)
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
        card = Card(); gl = QVBoxLayout(card); gl.setContentsMargins(M.LG, M.LG, M.LG, M.LG); gl.setSpacing(8)
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
_STATUS_COLOR = {"Pending": "#B8760F", "In Progress": "#A83326",
                 "Completed": "#16A34A", "Cancelled": "#9A9AA0",
                 "Overdue": "#DC2626"}
# subtle row tint for overdue tasks (light / dark)
_OVERDUE_TINT = "#FCEBEA"


class ActionItemsPage(QWidget):
    (_COL_TASK, _COL_OWNER, _COL_DUE, _COL_PRIO, _COL_STATUS,
     _COL_MEETING, _COL_DATE) = range(7)
    _DEADLINE_FILTERS = ["Any deadline", "Overdue", "Due today", "Due this week",
                         "Has a date", "No date"]
    _PRIO_COLOR = {"High": "#DC2626", "Medium": "#B8760F", "Low": "#6C6269"}

    def __init__(self, ctx: AppContext, toast, on_open_meeting=None):
        super().__init__()
        self.ctx = ctx
        self.toast = toast
        self.on_open_meeting = on_open_meeting
        self._items = []            # currently displayed (filtered) items
        self._all = []              # all items for the current search (for filters)
        self._reloading = False     # guard against filter-repopulation recursion

        v = QVBoxLayout(self); v.setContentsMargins(*M.PAGE_MARGINS); v.setSpacing(M.PAGE_GAP)
        title = QLabel("Action Items"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Every action item from all your meetings in one place. Set a "
                             "status with the dropdown, filter the list, and spot overdue tasks."))

        # -- search + export -------------------------------------------------
        bar = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search task, person or meeting…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search_changed)
        tip(self.search, "Filter as you type — matches task text, responsible person and meeting title")
        refresh = QPushButton("↻  Refresh"); refresh.setObjectName("Ghost"); refresh.clicked.connect(self.reload)
        tip(refresh, "Re-scan all meetings in History for action items")
        self.export_btn = QPushButton("Export CSV…"); self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self._export)
        tip(self.export_btn, "Save the currently filtered list as a CSV file you can open in Excel")
        bar.addWidget(self.search, 1); bar.addWidget(refresh); bar.addWidget(self.export_btn)
        v.addLayout(bar)

        # -- filters: person · status · deadline · meeting -------------------
        self._search_timer = QTimer(self); self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220); self._search_timer.timeout.connect(self.reload)

        fbar = QHBoxLayout()
        self.person_filter = QComboBox(); tip(self.person_filter, "Show only tasks for one responsible person")
        self.status_filter = QComboBox(); tip(self.status_filter, "Show only tasks with a given status (incl. Overdue)")
        self.deadline_filter = QComboBox(); self.deadline_filter.addItems(self._DEADLINE_FILTERS)
        tip(self.deadline_filter, "Filter by deadline: overdue, due today/this week, dated or undated")
        self.meeting_filter = QComboBox(); tip(self.meeting_filter, "Show only tasks from one meeting")
        for lbl, cb in (("Person", self.person_filter), ("Status", self.status_filter),
                        ("Deadline", self.deadline_filter), ("Meeting", self.meeting_filter)):
            cap = QLabel(lbl); cap.setObjectName("Hint")
            fbar.addWidget(cap); fbar.addWidget(cb)
            cb.currentIndexChanged.connect(self._apply_filters)
        self.clear_btn = QPushButton("Clear"); self.clear_btn.setObjectName("Ghost")
        self.clear_btn.clicked.connect(self._clear_filters)
        tip(self.clear_btn, "Reset all filters and the search box")
        fbar.addStretch(); fbar.addWidget(self.clear_btn)
        v.addLayout(fbar)

        # -- table / empty / loading ----------------------------------------
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Task", "Responsible", "Deadline", "Priority", "Status", "Meeting", "Date"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(self._COL_TASK, QHeaderView.Stretch)
        for c in (self._COL_OWNER, self._COL_DUE, self._COL_PRIO, self._COL_STATUS,
                  self._COL_MEETING, self._COL_DATE):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        tip(self.table, "Action items from your meetings. Set priority/status with the dropdowns; "
                        "double-click a row to edit it; right-click for quick actions. Overdue "
                        "tasks are highlighted; double-click a Meeting cell to open it.")
        self.empty = EmptyState("✔", "No action items yet",
                                "Action items appear here when a meeting's minutes include an "
                                "“Action Items” table. Generate minutes in New Meeting to populate this.")
        self.loading = EmptyState("⏳", "Scanning meetings…", "")
        self.stack = QStackedWidget()
        for w in (self.table, self.empty, self.loading):
            self.stack.addWidget(w)
        v.addWidget(self.stack, 1)

        self.summary = QLabel(""); self.summary.setObjectName("Hint")
        v.addWidget(self.summary)

    # -- search / reload ----------------------------------------------------
    def _on_search_changed(self, _text=None):
        self.stack.setCurrentWidget(self.loading)
        self._search_timer.start()

    def reload(self):
        """Re-scan history for action items, then rebuild filters + table."""
        self._all = self.ctx.action_items.all_items(self.search.text())
        self._rebuild_filter_options()
        self._apply_filters()

    def _rebuild_filter_options(self):
        from ..core.tasks import STATUS_CYCLE, OVERDUE
        self._reloading = True
        try:
            people = sorted({(i.owner or "").strip() for i in self._all if (i.owner or "").strip()})
            meetings = sorted({i.meeting_title for i in self._all if i.meeting_title})
            self._fill_combo(self.person_filter, ["All people"] + people)
            self._fill_combo(self.status_filter, ["All statuses"] + STATUS_CYCLE + [OVERDUE])
            self._fill_combo(self.meeting_filter, ["All meetings"] + meetings)
        finally:
            self._reloading = False

    @staticmethod
    def _fill_combo(combo: QComboBox, options: list[str]):
        prev = combo.currentText()
        combo.blockSignals(True)
        combo.clear(); combo.addItems(options)
        i = combo.findText(prev)
        combo.setCurrentIndex(i if i >= 0 else 0)      # keep selection if still present
        combo.blockSignals(False)

    def _clear_filters(self):
        for cb in (self.person_filter, self.status_filter, self.deadline_filter, self.meeting_filter):
            cb.blockSignals(True); cb.setCurrentIndex(0); cb.blockSignals(False)
        self.search.clear()          # triggers reload via _on_search_changed
        self.reload()

    def _apply_filters(self):
        if self._reloading:
            return
        from ..core import tasks as T
        from datetime import date, timedelta
        person = self.person_filter.currentText()
        status = self.status_filter.currentText()
        dl = self.deadline_filter.currentText()
        meeting = self.meeting_filter.currentText()
        today = date.today()

        def keep(it) -> bool:
            if person not in ("", "All people") and (it.owner or "").strip() != person:
                return False
            if meeting not in ("", "All meetings") and it.meeting_title != meeting:
                return False
            if status not in ("", "All statuses") and T.effective_status(it, today) != status:
                return False
            if dl != self._DEADLINE_FILTERS[0]:
                d = T.parse_deadline(it.deadline)
                if dl == "Overdue" and not T.is_overdue(it, today):
                    return False
                if dl == "Due today" and d != today:
                    return False
                if dl == "Due this week" and not (d and today <= d <= today + timedelta(days=7)):
                    return False
                if dl == "Has a date" and d is None:
                    return False
                if dl == "No date" and d is not None:
                    return False
            return True

        self._items = [it for it in self._all if keep(it)]
        self._populate()

    # -- table --------------------------------------------------------------
    def _populate(self):
        from ..core import tasks as T
        from ..core.tasks import STATUS_CYCLE, PRIORITIES
        from datetime import date
        today = date.today()
        self.table.setRowCount(len(self._items))
        overdue_n = 0
        for r, it in enumerate(self._items):
            overdue = T.is_overdue(it, today)
            overdue_n += int(overdue)
            due_txt = ("⚠ " + it.deadline) if overdue else (it.deadline or "—")
            task_txt = ("📝 " + it.task) if (it.notes or "").strip() else it.task
            for c, val in ((self._COL_TASK, task_txt), (self._COL_OWNER, it.owner or "—"),
                           (self._COL_DUE, due_txt), (self._COL_MEETING, it.meeting_title),
                           (self._COL_DATE, it.meeting_date)):
                item = QTableWidgetItem(val)
                if c == self._COL_TASK and (it.notes or "").strip():
                    item.setToolTip("Notes: " + it.notes)
                if overdue:
                    item.setBackground(QColor(_OVERDUE_TINT))
                    if c == self._COL_DUE:
                        item.setForeground(QColor(_STATUS_COLOR["Overdue"]))
                self.table.setItem(r, c, item)
            # Priority dropdown (— / High / Medium / Low), colour-coded.
            pcombo = QComboBox(); pcombo.addItems(["—"] + PRIORITIES)
            pcombo.setCurrentText(it.priority or "—")
            pcombo.setStyleSheet(
                f"color:{self._PRIO_COLOR.get(it.priority, '#6C6269')}; font-weight:600;")
            pcombo.setToolTip("Set this task's priority")
            pcombo.activated.connect(lambda _i, row=r: self._change_priority(row))
            self.table.setCellWidget(r, self._COL_PRIO, pcombo)
            # Status dropdown (settable statuses only; Overdue is derived).
            combo = QComboBox()
            combo.addItems(STATUS_CYCLE)
            if it.status not in STATUS_CYCLE:
                combo.addItem(it.status)
            combo.setCurrentText(it.status)
            shown = "Overdue" if overdue else it.status
            combo.setStyleSheet(
                f"color:{_STATUS_COLOR.get(shown, '#9A9AA0')}; font-weight:600;")
            combo.setToolTip("Overdue — past its deadline. Change this task's status here."
                             if overdue else "Change this task's status")
            combo.activated.connect(lambda _i, row=r: self._change_status(row))
            self.table.setCellWidget(r, self._COL_STATUS, combo)

        # summary
        n = len(self._items)
        done = sum(1 for i in self._items if i.status == "Completed")
        cancelled = sum(1 for i in self._items if i.status == "Cancelled")
        open_ = n - done - cancelled
        parts = [f"{n} shown", f"{done} completed", f"{open_} open"]
        if overdue_n:
            parts.append(f"{overdue_n} overdue")
        total = len(self._all)
        suffix = f" (of {total})" if n != total else ""
        self.summary.setText("  ·  ".join(parts) + suffix)

        if self._items:
            self.stack.setCurrentWidget(self.table)
        elif self._all:
            self.empty.set("No matching action items",
                           "No tasks match the current filters. Adjust or clear them "
                           "to see more.", "🔍")
            self.stack.setCurrentWidget(self.empty)
        else:
            self.empty.set("No action items yet",
                           "Action items appear here when a meeting's minutes include an "
                           "“Action Items” table. Generate minutes in New Meeting to populate this.", "✔")
            self.stack.setCurrentWidget(self.empty)

    def _change_status(self, row: int):
        if 0 <= row < len(self._items):
            combo = self.table.cellWidget(row, self._COL_STATUS)
            if combo is not None:
                self.ctx.action_items.set_status(self._items[row], combo.currentText())
                self.reload()

    def _change_priority(self, row: int):
        if 0 <= row < len(self._items):
            combo = self.table.cellWidget(row, self._COL_PRIO)
            if combo is not None:
                val = combo.currentText()
                self.ctx.action_items.set_priority(self._items[row], "" if val == "—" else val)
                self.reload()

    def _edit_item(self, row: int):
        if not (0 <= row < len(self._items)):
            return
        from .dialogs import ActionItemDialog
        it = self._items[row]
        dlg = ActionItemDialog(it, self)
        if dlg.exec():
            self.ctx.action_items.update_item(it, **dlg.values())
            self.reload()
            self.toast.show_message("Action item updated.", "success")

    def _open_meeting(self, row: int):
        if 0 <= row < len(self._items) and self.on_open_meeting:
            m = self.ctx.history.get(self._items[row].meeting_id)
            if m:
                self.on_open_meeting(m)

    def _on_double_click(self, row, col):
        if not (0 <= row < len(self._items)):
            return
        if col == self._COL_MEETING:          # Meeting cell → open the meeting
            self._open_meeting(row)
        elif col not in (self._COL_PRIO, self._COL_STATUS):   # dropdowns handle themselves
            self._edit_item(row)              # any other cell → edit the item

    def _context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        row = self.table.rowAt(pos.y())
        if not (0 <= row < len(self._items)):
            return
        self.table.selectRow(row)
        it = self._items[row]
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self._edit_item(row))
        menu.addSeparator()
        menu.addAction("Mark complete", lambda: self._quick_status(row, "Completed"))
        menu.addAction("Mark in progress", lambda: self._quick_status(row, "In Progress"))
        pm = menu.addMenu("Set priority")
        for p in ("High", "Medium", "Low"):
            pm.addAction(p, lambda _=False, pr=p: self._quick_priority(row, pr))
        pm.addAction("Clear", lambda: self._quick_priority(row, ""))
        menu.addSeparator()
        if self.on_open_meeting:
            menu.addAction("Open meeting", lambda: self._open_meeting(row))
        menu.addAction("Reset to original", lambda: self._reset_item(row))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _quick_status(self, row: int, status: str):
        if 0 <= row < len(self._items):
            self.ctx.action_items.set_status(self._items[row], status)
            self.reload(); self.toast.show_message(f"Marked {status.lower()}.", "success")

    def _quick_priority(self, row: int, priority: str):
        if 0 <= row < len(self._items):
            self.ctx.action_items.set_priority(self._items[row], priority)
            self.reload()

    def _reset_item(self, row: int):
        if 0 <= row < len(self._items):
            self.ctx.action_items.reset_item(self._items[row])
            self.reload(); self.toast.show_message("Reverted to the meeting's original values.", "success")

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
        v = QVBoxLayout(self); v.setContentsMargins(*M.PAGE_MARGINS); v.setSpacing(M.PAGE_GAP)
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
