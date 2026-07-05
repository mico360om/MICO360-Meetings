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
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import __app_name__, __version__
from ..config import TMP_DIR
from ..core import updater
from .components import Card, section_title, subtitle
from .context import AppContext
from .workers import UpdateCheckWorker, UpdateDownloadWorker

log = logging.getLogger("mico360.pages_extra")

SUPPORT_EMAIL = "info@mico360.com"


def _scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QScrollArea.NoFrame)
    sa.setWidget(inner)
    return sa


_STATUS_COLORS = {
    updater.AVAILABLE: "#3B82F6", updater.UP_TO_DATE: "#22C55E",
    updater.DOWNLOADING: "#3B82F6", updater.INSTALLING: "#F59E0B",
    updater.COMPLETED: "#22C55E", updater.FAILED: "#EF4444",
    updater.NOT_CONFIGURED: "#F59E0B", updater.CHECKING: "#8FA0BD",
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
        self.check_btn = QPushButton("Check for updates")
        self.check_btn.setObjectName("Primary")
        self.check_btn.clicked.connect(self.check)
        hl.addWidget(self.repo_btn); hl.addWidget(self.check_btn)
        self.v.addWidget(head)

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
        self.install_btn = QPushButton("Install & restart"); self.install_btn.setObjectName("Primary")
        self.install_btn.clicked.connect(self._install); self.install_btn.setVisible(False)
        self.release_btn = QPushButton("Open release page")
        self.release_btn.clicked.connect(self._open_release); self.release_btn.setVisible(False)
        self.retry_btn = QPushButton("Retry"); self.retry_btn.clicked.connect(self.check)
        self.retry_btn.setVisible(False)
        self.action_row.addStretch()
        for b in (self.release_btn, self.retry_btn, self.download_btn, self.install_btn):
            self.action_row.addWidget(b)
        self.dl_layout.addLayout(self.action_row)
        self.v.addWidget(self.detail)
        self.v.addStretch()

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(content))

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
        color = _STATUS_COLORS.get(status, "#8FA0BD")
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
        self._dl = UpdateDownloadWorker(self._info.download_url, dest)
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
        self.download_btn.setEnabled(True)
        self.progress.setValue(100)
        self._set_status(updater.INSTALLING, "Download complete. Ready to install.")
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
        try:
            if sys.platform == "win32":
                os.startfile(path)  # launches the installer  # noqa: S606
            self._set_status(updater.COMPLETED,
                             "Installer launched. Follow its steps; the app will update.")
            self.toast.show_message("Installer launched.", "success", 5000)
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
        tabs.addTab(self._tab(self._terms_html()), "Terms & Conditions")
        tabs.addTab(self._tab(self._privacy_html()), "Privacy Policy")
        v.addWidget(tabs, 1)

    def _tab(self, html: str) -> QWidget:
        b = QTextBrowser(); b.setOpenExternalLinks(True); b.setHtml(html)
        return b

    def _wrap(self, inner: str) -> str:
        return f"<div style='font-family:Segoe UI; font-size:13.5px; line-height:1.5;'>{inner}</div>"

    def _about_html(self) -> str:
        repo = updater.repo_url(self.ctx.settings.get("github_repo", "")) or "(provided on release)"
        return self._wrap(f"""
          <h2>{__app_name__}</h2>
          <p><b>Version:</b> v{__version__}</p>
          <p>{__app_name__} turns your meeting recordings and transcripts into
          professional minutes — <b>entirely offline</b>. Audio is transcribed on
          your computer with Whisper, and minutes are written by a local Ollama AI
          model. Your meeting data never leaves your machine.</p>
          <h3>Contact</h3>
          <p>Email: <a href='mailto:{SUPPORT_EMAIL}'>{SUPPORT_EMAIL}</a><br>
             Website / Repository: {repo}</p>
          <p style='color:#888'>© {time.strftime('%Y')} MICO360. All rights reserved.</p>""")

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
          <p><b>Nothing.</b> {__app_name__} does not send your audio, video, transcripts
             or minutes to any server. All transcription (Whisper) and summarization
             (Ollama) run locally on your computer.</p>
          <h3>Local storage</h3>
          <p>Recordings, transcripts, minutes, company profiles and history are stored
             only in your local app-data folder. You can delete them at any time.</p>
          <h3>Network access</h3>
          <p>The only optional network activity is: (1) the one-time download of a
             Whisper or Ollama model you choose, (2) checking GitHub for application
             updates when you click “Check for updates”, and (3) the opt-in crash
             reporter — if (and only if) you choose to report a problem, it opens a
             pre-filled GitHub issue or email that you review and send yourself.
             Nothing is sent automatically and no meeting content is included.</p>
          <h3>Contact</h3>
          <p>Privacy questions: <a href='mailto:{SUPPORT_EMAIL}'>{SUPPORT_EMAIL}</a></p>""")
