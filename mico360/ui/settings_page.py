"""Settings page. (Split out of pages.py — see pages.py for the New Meeting wizard.)"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core import documents
from ..core.audio import MEDIA_EXTS
from ..core.history import Meeting
from ..core.prompts import OUTPUT_STYLES, SavedPrompt
from ..core.transcription import WHISPER_MODELS
from . import metrics as M
from . import theme
from .components import (
    Card, CollapsibleSection, DropArea, EmptyState, StepIndicator, hint,
    scroll_area as _scroll, section_title, subtitle, tip,
)
from .context import AppContext
from .dialogs import ProfileDialog, PromptDialog
from .recording_panel import RecordingPanel
from .workers import GenerateWorker, TranscribeWorker

log = logging.getLogger("mico360.pages")

UPLOAD_EXTS = set(MEDIA_EXTS) | documents.DOC_EXTS | documents.IMAGE_EXTS


# ===========================================================================
# Settings
# ===========================================================================
class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, toast, on_theme_change, on_models_change):
        super().__init__()
        self.ctx = ctx; self.toast = toast
        self.on_theme_change = on_theme_change; self.on_models_change = on_models_change
        outer = QVBoxLayout(self); outer.setContentsMargins(*M.PAGE_MARGINS); outer.setSpacing(M.PAGE_GAP)

        # Header: page title + always-visible Text-size and Appearance selectors.
        header = QHBoxLayout()
        title = QLabel("Settings"); title.setObjectName("PageTitle")
        header.addWidget(title); header.addStretch()

        from ..config import UI_SCALES
        sl = QLabel("Text size"); sl.setObjectName("Hint")
        self.scale_box = QComboBox()
        self._scale_values = [v for _, v in UI_SCALES]
        for label, val in UI_SCALES:
            self.scale_box.addItem(label, val)
        _cur_scale = float(ctx.settings.get("ui_scale", 1.0) or 1.0)
        self.scale_box.setCurrentIndex(
            min(range(len(self._scale_values)),
                key=lambda i: abs(self._scale_values[i] - _cur_scale)))
        self.scale_box.activated.connect(self._scale_changed)
        tip(self.scale_box, "Make all text larger or smaller across the whole app — applies "
                            "immediately (accessibility)")
        header.addWidget(sl); header.addWidget(self.scale_box)

        al = QLabel("Appearance"); al.setObjectName("Hint")
        self.theme = QComboBox(); self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(ctx.settings.get("theme"))
        self.theme.currentTextChanged.connect(self._theme_changed)
        tip(self.theme, "Switch between dark and light themes — applies immediately")
        header.addWidget(al); header.addWidget(self.theme)
        outer.addLayout(header)
        outer.addWidget(subtitle("Grouped by area — AI, transcription, email, updates and data."))

        self._tabs = QTabWidget()
        outer.addWidget(self._tabs, 1)

        # ---- AI tab -------------------------------------------------------
        ai_sa, form = self._tab_form(); self._ai_scroll = ai_sa
        from ..config import AI_PROVIDERS
        self.provider_box = QComboBox()
        self._provider_keys = list(AI_PROVIDERS.keys())
        for k in self._provider_keys:
            self.provider_box.addItem(AI_PROVIDERS[k], k)
        _cur_prov = ctx.settings.get("ai_provider", "local")
        self.provider_box.setCurrentIndex(
            self._provider_keys.index(_cur_prov) if _cur_prov in self._provider_keys else 0)
        self.provider_box.activated.connect(self._provider_changed)
        tip(self.provider_box, "Where meeting minutes are generated: Local (Ollama) runs fully "
                               "offline on this PC; MICO360 Cloud uses the company AI server. "
                               "Transcription always stays local on this PC.")
        form.addRow("AI mode", self.provider_box)
        form.addRow(hint("Minutes are written by the selected AI mode. Transcription always runs "
                         "locally with Whisper — switching mode never sends your audio anywhere."))
        self.host = QLineEdit(ctx.settings.get("ollama_host"))
        tip(self.host, "Address of your local Ollama server. Leave the default "
                       "http://127.0.0.1:11434 unless you run Ollama elsewhere")
        form.addRow("Ollama host", self.host)
        self.model = QComboBox(); self._reload_models()
        self.model.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(16)
        tip(self.model, "The Ollama model pre-selected for new meetings — you can still switch "
                        "per-meeting in Step 3")
        refresh = QPushButton("Refresh models"); refresh.clicked.connect(self._reload_models)
        tip(refresh, "Re-query Ollama for installed models — use after installing or removing one")
        mrow = QHBoxLayout(); mrow.addWidget(self.model, 1); mrow.addWidget(refresh)
        mwrap = QWidget(); mwrap.setLayout(mrow)
        form.addRow("Default Ollama model", mwrap)
        self.env_lbl = QLabel(); self.env_lbl.setObjectName("Hint"); self.env_lbl.setWordWrap(True)
        form.addRow("Environment", self.env_lbl)
        from ..core.ollama_client import RECOMMENDED_MODELS
        self.install_model_box = QComboBox(); self.install_model_box.setEditable(True)
        self.install_model_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.install_model_box.setMinimumContentsLength(18)
        for lbl, name in RECOMMENDED_MODELS:
            self.install_model_box.addItem(lbl, name)
        tip(self.install_model_box, "Pick a recommended model or type any Ollama model name "
                                    "(e.g. llama3.1). Sizes shown are download sizes")
        self.install_btn = QPushButton("Install Required Model")
        self.install_btn.setObjectName("Primary")
        self.install_btn.clicked.connect(self._install_model)
        tip(self.install_btn, "Download and install the selected AI model through Ollama. "
                              "Needs internet once; already-installed models are skipped")
        irow = QHBoxLayout(); irow.addWidget(self.install_model_box, 1); irow.addWidget(self.install_btn)
        iwrap = QWidget(); iwrap.setLayout(irow)
        form.addRow("Install AI model", iwrap)
        self.install_progress = QProgressBar(); self.install_progress.setVisible(False)
        form.addRow("", self.install_progress)
        self.install_status = QLabel(""); self.install_status.setObjectName("Hint"); self.install_status.setWordWrap(True)
        form.addRow("", self.install_status)
        self._pull_worker = None
        self._refresh_env()
        self._ai_tab_index = self._tabs.addTab(ai_sa, "AI")

        # ---- Transcription tab -------------------------------------------
        tr_sa, form = self._tab_form()
        from ..config import QUALITY_PRESETS
        self.preset = QComboBox()
        self.preset.addItems(list(QUALITY_PRESETS.keys()) + ["Custom"])
        self.preset.setCurrentText(ctx.settings.get("quality_preset", "Balanced"))
        self.preset.activated.connect(self._apply_preset)
        tip(self.preset, "One-click transcription trade-off: Fast (tiny Whisper) → Accurate "
                         "(small Whisper). Adjusting the fields below switches this to Custom")
        form.addRow("Speed ⇄ Quality", self.preset)
        form.addRow(hint("Fast = tiny Whisper (quickest) · Balanced = base · Accurate = small (best). "
                         "Pick a larger Ollama model in the AI tab for richer minutes."))
        self.whisper = QComboBox()
        for label, size in WHISPER_MODELS:
            self.whisper.addItem(label, size)
        cur = ctx.settings.get("whisper_model")
        for i in range(self.whisper.count()):
            if self.whisper.itemData(i) == cur:
                self.whisper.setCurrentIndex(i); break
        tip(self.whisper, "Speech-to-text model size: tiny is fastest, large-v3 most accurate. "
                          "Downloads once on first use (size shown per model)")
        form.addRow("Whisper model", self.whisper)
        self.compute = QComboBox(); self.compute.addItems(["int8", "int8_float16", "float16", "float32"])
        self.compute.setCurrentText(ctx.settings.get("whisper_compute"))
        tip(self.compute, "Numeric precision for transcription — int8 is best for most CPUs; "
                          "float16 only helps on a GPU")
        form.addRow("Whisper compute", self.compute)
        self.device = QComboBox(); self.device.addItems(["auto", "cpu", "cuda"])
        self.device.setCurrentText(ctx.settings.get("whisper_device"))
        tip(self.device, "Where transcription runs. 'auto' uses the CPU (safe everywhere); "
                         "'cuda' needs an NVIDIA GPU with CUDA libraries — falls back to CPU if unavailable")
        form.addRow("Whisper device", self.device)
        self.lang = QLineEdit(ctx.settings.get("language"))
        self.lang.setPlaceholderText("auto, or a code like en / ur / ar")
        tip(self.lang, "Spoken language of your meetings. 'auto' detects it; a fixed code "
                       "(en, ur, ar…) is faster and more reliable")
        form.addRow("Language", self.lang)
        self.fillers = QCheckBox("Remove filler words")
        self.fillers.setChecked(ctx.settings.get("remove_fillers", True))
        tip(self.fillers, "Strip 'um', 'uh', repeated words etc. from transcripts before the AI "
                          "summarises them")
        form.addRow("", self.fillers)
        self.diarize = QCheckBox("Identify speakers (offline, beta)")
        self.diarize.setChecked(ctx.settings.get("diarize", False))
        self.diarize.setToolTip("Labels the transcript as Speaker 1/2/3 using offline "
                                "voice clustering. Approximate — best with a few clear speakers.")
        form.addRow("Speakers", self.diarize)
        self.chunk = QSpinBox(); self.chunk.setRange(1500, 20000); self.chunk.setSingleStep(500)
        self.chunk.setValue(int(ctx.settings.get("chunk_chars", 6000)))
        tip(self.chunk, "How much text the AI processes per part for long meetings. Lower = safer "
                        "on small models, higher = fewer parts. Default 6000 suits most setups")
        form.addRow("Transcript chunk size (chars)", self.chunk)
        self._tabs.addTab(tr_sa, "Transcription")

        # ---- Email tab ----------------------------------------------------
        em_sa, form = self._tab_form()
        form.addRow(hint("Used to send minutes by email. For Mailjet: host in-v3.mailjet.com, "
                         "port 587, user = API key, password = Secret key."))
        self.smtp_host = QLineEdit(ctx.settings.get("smtp_host", "in-v3.mailjet.com"))
        tip(self.smtp_host, "Your email provider's SMTP server — for Mailjet: in-v3.mailjet.com")
        form.addRow("SMTP host", self.smtp_host)
        self.smtp_port = QSpinBox(); self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(int(ctx.settings.get("smtp_port", 587)))
        tip(self.smtp_port, "587 (STARTTLS) works almost everywhere; 465 = SSL; 25 is often "
                            "blocked by ISPs")
        form.addRow("SMTP port", self.smtp_port)
        self.email_from = QLineEdit(ctx.settings.get("email_from", ""))
        self.email_from.setPlaceholderText("validated sender, e.g. admin@mico360.com")
        tip(self.email_from, "The From address — must be a sender you have validated with your "
                             "email provider, or sending will be rejected")
        form.addRow("From address", self.email_from)
        self.smtp_user = QLineEdit(ctx.settings.get("smtp_user", ""))
        self.smtp_user.setPlaceholderText("Mailjet API key")
        tip(self.smtp_user, "SMTP username — for Mailjet this is your API key")
        form.addRow("SMTP user / API key", self.smtp_user)
        self.smtp_password = QLineEdit(ctx.settings.get("smtp_password", ""))
        self.smtp_password.setEchoMode(QLineEdit.Password)
        self.smtp_password.setPlaceholderText("Mailjet Secret key")
        tip(self.smtp_password, "SMTP password — for Mailjet this is your Secret key. Stored only "
                                "in your local settings file, never in the app or repository")
        form.addRow("SMTP password / Secret", self.smtp_password)
        test_btn = QPushButton("Send test email")
        test_btn.clicked.connect(self._send_test_email)
        tip(test_btn, "Send a test message to the From address to confirm these settings work")
        form.addRow("", test_btn)
        self._tabs.addTab(em_sa, "Email")

        # ---- Updates tab --------------------------------------------------
        up_sa, form = self._tab_form()
        self.repo = QLineEdit(ctx.settings.get("github_repo", ""))
        self.repo.setPlaceholderText("owner/name  (e.g. mico360om/MICO360-Meetings)")
        tip(self.repo, "GitHub repository checked for new releases (owner/name). Also used by "
                       "the crash reporter's 'Report on GitHub' button")
        form.addRow("GitHub repo (for updates)", self.repo)
        self.auto_check = QCheckBox("Auto-check on startup")
        self.auto_check.setChecked(ctx.settings.get("auto_check_updates", True))
        tip(self.auto_check, "Quietly check GitHub for a newer version when the app starts — "
                             "nothing installs without your confirmation")
        form.addRow("", self.auto_check)
        self.crash_reporter = QCheckBox("Show crash reporter on unexpected errors")
        self.crash_reporter.setChecked(ctx.settings.get("crash_reporter", True))
        tip(self.crash_reporter, "On an unexpected error, offer a review-before-send report dialog. "
                                 "Nothing is ever sent automatically")
        form.addRow("Crash reporting", self.crash_reporter)
        report_btn = QPushButton("Report a problem…")
        report_btn.clicked.connect(self._report_problem)
        tip(report_btn, "Open a problem report with the recent app log — review/edit it, then "
                        "send via GitHub or email if you choose")
        form.addRow("", report_btn)
        self._tabs.addTab(up_sa, "Updates")

        # ---- Data tab -----------------------------------------------------
        da_sa, form = self._tab_form()
        from ..config import DATA_DIR, LOG_DIR
        form.addRow(hint("Where your meetings, transcripts, minutes, profiles and logs are stored "
                         "on this computer. Everything stays local."))
        for label, path in (("Data folder", DATA_DIR), ("Logs", LOG_DIR)):
            field = QLineEdit(str(path)); field.setReadOnly(True)
            field.setCursorPosition(0)
            form.addRow(label, field)
        self._tabs.addTab(da_sa, "Data")

        # ---- global Save --------------------------------------------------
        save_row = QHBoxLayout(); save_row.addStretch()
        save = QPushButton("Save settings"); save.setObjectName("Primary"); save.clicked.connect(self._save)
        tip(save, "Save all settings across every tab — model and appearance changes apply immediately")
        save_row.addWidget(save)
        outer.addLayout(save_row)

    def _tab_form(self):
        """A scrollable QFormLayout for one Settings tab; returns (scrollarea, form)."""
        from PySide6.QtWidgets import QFormLayout as _QFL
        content = QWidget()
        form = _QFL(content)
        # left stays 24 (this is the Settings page's first scroll content, which
        # the consistency audit measures); the rest follows the card rhythm.
        form.setContentsMargins(M.XXL, M.XL, M.XXL, M.XL); form.setSpacing(M.CARD_GAP)
        form.setRowWrapPolicy(_QFL.WrapLongRows)
        form.setFieldGrowthPolicy(_QFL.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignLeft)
        return _scroll(content, max_width=900), form

    def focus_install(self):
        """Open the AI tab and scroll to the model installer (used by the New
        Meeting readiness banner's 'Install a model' action)."""
        try:
            self._tabs.setCurrentIndex(self._ai_tab_index)
            self._ai_scroll.ensureWidgetVisible(self.install_btn, 60, 120)
            self.install_model_box.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def _reload_models(self):
        status = self.ctx.ollama_status()
        self.model.clear()
        if status.running and status.models:
            self.model.addItems(status.models)
            saved = self.ctx.settings.get("ollama_model")
            if saved in status.models:
                self.model.setCurrentText(saved)
        else:
            self.model.addItem("(Ollama not running)")

    def _provider_changed(self):
        key = self.provider_box.currentData()
        self.ctx.settings.set("ai_provider", key)
        self._refresh_env()
        self.on_models_change()          # rebuild the New Meeting model list
        from ..config import AI_PROVIDERS
        self.toast.show_message(f"AI mode: {AI_PROVIDERS.get(key, key)}.", "success")

    # -- environment + model installation -----------------------------------
    def _refresh_env(self):
        import sys
        py = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        if self.ctx.provider() == "cloud":
            from ..config import MICO360_CONNECT_BASE_URL
            st = self.ctx.ai_status()
            if st.running:
                cloud_txt = "<span style='color:#22C55E'>MICO360 Cloud online</span>"
                models_txt = (f"{len(st.models)} available ({', '.join(st.models[:4])})"
                              if st.models else "none available")
            else:
                cloud_txt = "<span style='color:#EF4444'>MICO360 Cloud unavailable</span>"
                models_txt = st.error or "—"
            self.env_lbl.setText(
                f"{py} &nbsp;•&nbsp; {cloud_txt} &nbsp;•&nbsp; {MICO360_CONNECT_BASE_URL} "
                f"&nbsp;•&nbsp; Models: {models_txt}")
            return
        st = self.ctx.ollama_status()
        if not st.running:
            ollama_txt = "<span style='color:#EF4444'>Ollama not running</span>"
            models_txt = "—"
        else:
            ollama_txt = "<span style='color:#22C55E'>Ollama online</span>"
            if st.models:
                models_txt = f"{len(st.models)} installed ({', '.join(st.models[:4])})"
            else:
                models_txt = ("<span style='color:#F59E0B'>No models installed — "
                              "use “Install Required Model” below</span>")
        self.env_lbl.setText(f"{py} &nbsp;•&nbsp; {ollama_txt} &nbsp;•&nbsp; Models: {models_txt}")

    def _install_model(self):
        st = self.ctx.ollama_status()
        if not st.running:
            QMessageBox.warning(self, "Ollama not running",
                                "Ollama must be running to install a model.\n\n"
                                "Start it (open the Ollama app or run 'ollama serve') and try again.")
            return
        # Resolve the model: if the box shows a recommended label, use its data;
        # otherwise treat whatever the user typed as the literal model name.
        text = self.install_model_box.currentText().strip()
        model = text
        for i in range(self.install_model_box.count()):
            if self.install_model_box.itemText(i) == text:
                model = self.install_model_box.itemData(i) or text
                break
        if not model:
            return
        if model in st.models:
            self.install_status.setText(f"“{model}” is already installed — skipped.")
            self.toast.show_message(f"{model} already installed.", "info")
            return
        from .workers import ModelPullWorker
        self.install_btn.setEnabled(False)
        self.install_progress.setVisible(True); self.install_progress.setRange(0, 100)
        self.install_status.setText(f"Installing “{model}”…")
        self._pull_worker = ModelPullWorker(self.ctx.settings.get("ollama_host"), model)
        self._pull_worker.progress.connect(self._on_pull_progress)
        self._pull_worker.finished_ok.connect(self._on_pull_done)
        self._pull_worker.failed.connect(self._on_pull_failed)
        self._pull_worker.start()

    def _on_pull_progress(self, frac: float, status: str):
        if frac < 0:
            self.install_progress.setRange(0, 0)        # indeterminate (e.g. "pulling manifest")
        else:
            self.install_progress.setRange(0, 100)
            self.install_progress.setValue(int(frac * 100))
        self.install_status.setText(status)

    def _on_pull_done(self, model: str):
        self.install_btn.setEnabled(True)
        self.install_progress.setRange(0, 100); self.install_progress.setValue(100)
        self.install_status.setText(f"✓ Installed “{model}”.")
        self.toast.show_message(f"Model “{model}” installed.", "success", 5000)
        self._reload_models(); self._refresh_env()
        self.on_models_change()

    def _on_pull_failed(self, msg: str):
        self.install_btn.setEnabled(True)
        self.install_progress.setVisible(False)
        self.install_status.setText(f"Install failed: {msg}")
        if "cancel" not in msg.lower():
            QMessageBox.critical(self, "Model install failed",
                                 f"{msg}\n\nIf you are offline, connect to the internet and retry.")

    def _theme_changed(self, name: str):
        self.ctx.settings.set("theme", name)
        self.on_theme_change(name)

    def _scale_changed(self):
        self.ctx.settings.set("ui_scale", float(self.scale_box.currentData()))
        self.on_theme_change(self.theme.currentText())   # re-apply QSS at the new text size

    def _apply_preset(self):
        from ..config import apply_quality_preset, QUALITY_PRESETS
        name = self.preset.currentText()
        if name not in QUALITY_PRESETS:
            return
        apply_quality_preset(self.ctx.settings, name)
        # reflect the preset's Whisper model/compute in the combos
        target = QUALITY_PRESETS[name]["whisper_model"]
        for i in range(self.whisper.count()):
            if self.whisper.itemData(i) == target:
                self.whisper.setCurrentIndex(i); break
        self.compute.setCurrentText(QUALITY_PRESETS[name]["whisper_compute"])
        self.toast.show_message(f"{name} preset applied.", "success")

    def _save(self):
        s = self.ctx.settings
        s.set("ui_scale", float(self.scale_box.currentData()))
        s.set("ai_provider", self.provider_box.currentData())
        s.set("ollama_host", self.host.text().strip() or "http://127.0.0.1:11434")
        if not self.model.currentText().startswith("("):
            s.set("ollama_model", self.model.currentText())
        s.set("whisper_model", self.whisper.currentData())
        s.set("whisper_compute", self.compute.currentText())
        s.set("whisper_device", self.device.currentText())
        s.set("language", self.lang.text().strip() or "auto")
        s.set("remove_fillers", self.fillers.isChecked())
        s.set("diarize", self.diarize.isChecked())
        s.set("quality_preset", self.preset.currentText())
        s.set("chunk_chars", self.chunk.value())
        s.set("github_repo", self.repo.text().strip().strip("/"))
        s.set("auto_check_updates", self.auto_check.isChecked())
        s.set("crash_reporter", self.crash_reporter.isChecked())
        s.set("smtp_host", self.smtp_host.text().strip() or "in-v3.mailjet.com")
        s.set("smtp_port", self.smtp_port.value())
        s.set("email_from", self.email_from.text().strip())
        s.set("smtp_user", self.smtp_user.text().strip())
        s.set("smtp_password", self.smtp_password.text().strip())
        self.toast.show_message("Settings saved.", "success")
        self.on_models_change()

    def _send_test_email(self):
        from ..core.emailer import SmtpConfig, send_test
        cfg = SmtpConfig(host=self.smtp_host.text().strip() or "in-v3.mailjet.com",
                         port=self.smtp_port.value(),
                         user=self.smtp_user.text().strip(),
                         password=self.smtp_password.text().strip(),
                         sender=self.email_from.text().strip())
        if not cfg.configured:
            QMessageBox.information(self, "Incomplete", "Fill in host, from, user and password first.")
            return
        self.toast.show_message("Sending test email…", "info")

        from .workers import EmailWorker
        self._test_worker = EmailWorker(cfg, cfg.sender, "MICO360 Meetings - test email",
                                        "This is a test email from MICO360 Meetings. "
                                        "Your SMTP settings work.")
        self._test_worker.finished_ok.connect(
            lambda: self.toast.show_message(f"Test email sent to {cfg.sender}.", "success", 6000))
        self._test_worker.failed.connect(
            lambda m: QMessageBox.critical(self, "Test failed",
                                           f"{m}\n\nCheck host/port/user/password and that the "
                                           "sender is a validated Mailjet sender."))
        self._test_worker.start()

    def _report_problem(self):
        """Open the crash-reporter dialog with the current log (no crash needed)."""
        from .. import crash_reporter
        report = (f"{__import__('mico360').__app_name__} — problem report (manual)\n"
                  f"Time: {__import__('time').strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                  "=== Recent log ===\n" + crash_reporter._tail_log(crash_reporter.LOG_TAIL_LINES))
        path = crash_reporter._write_report(report)
        repo = self.ctx.settings.get("github_repo", "")
        dlg = crash_reporter.CrashDialog(report, path, repo, "Manual problem report", self)
        dlg.exec()
