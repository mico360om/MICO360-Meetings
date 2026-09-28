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
from ..core import ollama_client as _ollama
from ..core.audio import MEDIA_EXTS
from ..core.history import Meeting
from ..core.prompts import OUTPUT_STYLES, SavedPrompt
from ..core.transcription import WHISPER_MODELS
from . import metrics as M
from . import theme
from .components import (
    Card, CollapsibleSection, DropArea, EmptyState, StepIndicator, hint,
    keep_alive as _keep_alive, running as _running,
    scroll_area as _scroll, section_title, subtitle, tip,
)
from .context import AppContext
from .dialogs import ProfileDialog, PromptDialog
from .recording_panel import RecordingPanel
from .workers import GenerateWorker, TranscribeWorker

log = logging.getLogger("mico360.pages")

UPLOAD_EXTS = set(MEDIA_EXTS) | documents.DOC_EXTS | documents.IMAGE_EXTS

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


# Transcription language helpers live in core (used by the engine too).
from ..core.languages import (  # noqa: E402,F401
    LANGUAGE_NAMES, language_codes, language_name, normalize_language,
)


# ===========================================================================
# GitHub repository field: always stored as "owner/name"
# ===========================================================================
_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")


def normalize_repo(text) -> str | None:
    """"owner/name", a github.com URL (…/releases, .git, ?tab=…) or an SSH remote
    → "owner/name". "" for an empty field; None if it isn't a GitHub repo."""
    s = str(text or "").strip()
    if not s:
        return ""
    s = re.sub(r"^git@github\.com:", "", s, flags=re.I)
    s = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", s, flags=re.I)
    s = s.split("?")[0].split("#")[0].strip().strip("/")
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not (_OWNER_RE.fullmatch(owner) and _NAME_RE.fullmatch(name)) or name in (".", ".."):
        return None
    return f"{owner}/{name}"


# ===========================================================================
# Settings
# ===========================================================================
class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, toast, on_theme_change, on_models_change):
        super().__init__()
        self.ctx = ctx; self.toast = toast
        self.on_theme_change = on_theme_change; self.on_models_change = on_models_change
        self._bg_workers: set = set()          # running QThreads, kept until finished
        self._pull_worker = None
        self._test_worker = None
        self._migrate_stored_values()
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
                       "http://127.0.0.1:11434 unless you run Ollama elsewhere. Refresh and "
                       "Install use the address typed here")
        form.addRow("Ollama host", self.host)
        self.model = QComboBox(); self._reload_models()
        self.model.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(16)
        tip(self.model, "The Ollama model pre-selected for new meetings — you can still switch "
                        "per-meeting in Step 3")
        refresh = QPushButton("Refresh models"); refresh.clicked.connect(self._refresh_clicked)
        tip(refresh, "Re-query the Ollama host above for installed models — use after "
                     "installing or removing one, or after changing the host")
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
        self.install_cancel_btn = QPushButton("Cancel")
        self.install_cancel_btn.setObjectName("Ghost")
        self.install_cancel_btn.setVisible(False)
        self.install_cancel_btn.clicked.connect(self._cancel_install)
        tip(self.install_cancel_btn, "Stop downloading this model")
        irow = QHBoxLayout(); irow.addWidget(self.install_model_box, 1)
        irow.addWidget(self.install_btn); irow.addWidget(self.install_cancel_btn)
        iwrap = QWidget(); iwrap.setLayout(irow)
        form.addRow("Install AI model", iwrap)
        self.install_progress = QProgressBar(); self.install_progress.setVisible(False)
        form.addRow("", self.install_progress)
        self.install_status = QLabel(""); self.install_status.setObjectName("Hint"); self.install_status.setWordWrap(True)
        form.addRow("", self.install_status)
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
        self.whisper.activated.connect(self._whisper_fields_changed)
        tip(self.whisper, "Speech-to-text model size: tiny is fastest, large-v3 most accurate. "
                          "Downloads once on first use (size shown per model)")
        form.addRow("Whisper model", self.whisper)
        self.compute = QComboBox(); self.compute.addItems(["int8", "int8_float16", "float16", "float32"])
        self.compute.setCurrentText(ctx.settings.get("whisper_compute"))
        self.compute.activated.connect(self._whisper_fields_changed)
        tip(self.compute, "Numeric precision for transcription — int8 is best for most CPUs; "
                          "float16 only helps on a GPU")
        form.addRow("Whisper compute", self.compute)
        self.device = QComboBox(); self.device.addItems(["auto", "cpu", "cuda"])
        self.device.setCurrentText(ctx.settings.get("whisper_device"))
        tip(self.device, "Where transcription runs. 'auto' uses the CPU (safe everywhere); "
                         "'cuda' needs an NVIDIA GPU with CUDA libraries — falls back to CPU if unavailable")
        form.addRow("Whisper device", self.device)
        # A fixed list (auto + every language Whisper knows) — free text such as
        # "Arabic", "AR" or "ar-SA" used to break transcription.
        self.lang = QComboBox()
        self.lang.addItem("Auto-detect", "auto")
        for code in sorted(language_codes(), key=lambda c: language_name(c).lower()):
            self.lang.addItem(f"{language_name(code)}  ({code})", code)
        self._set_lang(ctx.settings.get("language"))
        tip(self.lang, "Spoken language of your meetings. 'Auto-detect' works for any language; "
                       "choosing the language (English, Urdu, Arabic…) is faster and more reliable")
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

        # Auto-record: detect a live Teams / Meet / Zoom / Webex window (or a
        # calendar meeting starting) and offer to record it — no clicks needed.
        self.auto_record = QCheckBox("Offer to record detected meetings")
        self.auto_record.setChecked(bool(ctx.settings.get("auto_record", False)))
        self.auto_record.setToolTip(
            "When a meeting window opens (or one starts in your Outlook calendar), MICO360 "
            "asks to record it — system audio + microphone with a live transcript — then "
            "generates the minutes when it ends. Everything stays on this PC.")
        self.auto_record.toggled.connect(self._auto_record_toggled)
        form.addRow("Auto-record", self.auto_record)
        self.auto_record_note = QLabel(
            "Detects Teams, Google Meet, Zoom and Webex windows and Outlook calendar starts. "
            "Recording a call may require every participant's consent — the window title shows "
            "“● Recording” while capturing; tell people the meeting is being recorded.")
        self.auto_record_note.setObjectName("Hint"); self.auto_record_note.setWordWrap(True)
        form.addRow("", self.auto_record_note)
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
                                "in your local settings file on this PC, encrypted with your "
                                "Windows account. Saved exactly as typed (spaces are kept)")
        form.addRow("SMTP password / Secret", self.smtp_password)
        self.test_btn = QPushButton("Send test email")
        self.test_btn.clicked.connect(self._send_test_email)
        tip(self.test_btn, "Send a test message to the From address to confirm these settings work")
        form.addRow("", self.test_btn)
        self._tabs.addTab(em_sa, "Email")

        # ---- Updates tab --------------------------------------------------
        up_sa, form = self._tab_form()
        self._updates_scroll = up_sa
        self.repo = QLineEdit(ctx.settings.get("github_repo", ""))
        self.repo.setPlaceholderText("owner/name  (e.g. mico360om/MICO360-Meetings)")
        tip(self.repo, "GitHub repository checked for new releases (owner/name — a pasted "
                       "github.com link is converted). Also used by the crash reporter's "
                       "'Report on GitHub' button")
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
        self._updates_tab_index = self._tabs.addTab(up_sa, "Updates")

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
        tip(save, "Save all settings across every tab (Ctrl+S) — appearance, text size and "
                  "AI mode apply immediately")
        save_row.addWidget(save)
        outer.addLayout(save_row)
        self._built = True
        self._mark_clean()

    # -- stored-value migration ---------------------------------------------
    def _migrate_stored_values(self):
        """Heal values older builds accepted as free text, so transcription
        (including live transcription, which reads the setting directly) and
        update checks work before the user ever opens this page."""
        s = self.ctx.settings
        stored = s.get("language", "auto")
        norm = normalize_language(stored)
        if norm != stored:
            s.set("language", norm)
        repo = s.get("github_repo", "")
        nrepo = normalize_repo(repo)
        if nrepo and nrepo != repo:
            s.set("github_repo", nrepo)

    def _set_lang(self, value):
        code = normalize_language(value)
        i = self.lang.findData(code)
        self.lang.setCurrentIndex(i if i >= 0 else 0)

    # -- unsaved-changes tracking (M11) --------------------------------------
    def _form_state(self) -> dict:
        """Everything that is applied only by "Save settings" (theme, text size
        and AI mode apply immediately and are not part of this)."""
        model = self.model.currentText()
        return {
            "ollama_host": self.host.text().strip(),
            "ollama_model": "" if model.startswith("(") else model,
            "whisper_model": self.whisper.currentData(),
            "whisper_compute": self.compute.currentText(),
            "whisper_device": self.device.currentText(),
            "language": self.lang.currentData(),
            "remove_fillers": self.fillers.isChecked(),
            "diarize": self.diarize.isChecked(),
            "auto_record": self.auto_record.isChecked(),
            "quality_preset": self.preset.currentText(),
            "chunk_chars": self.chunk.value(),
            "github_repo": self.repo.text().strip(),
            "auto_check_updates": self.auto_check.isChecked(),
            "crash_reporter": self.crash_reporter.isChecked(),
            "smtp_host": self.smtp_host.text().strip(),
            "smtp_port": self.smtp_port.value(),
            "email_from": self.email_from.text().strip(),
            "smtp_user": self.smtp_user.text().strip(),
            "smtp_password": self.smtp_password.text(),
        }

    def _mark_clean(self, *keys):
        """Take the current form as the saved baseline (all keys, or just `keys`)."""
        if not getattr(self, "_built", False):
            return                                     # still constructing the form
        state = self._form_state()
        if not keys or not hasattr(self, "_baseline"):
            self._baseline = state
        else:
            for k in keys:
                self._baseline[k] = state[k]

    def dirty_fields(self) -> list[str]:
        if not hasattr(self, "_baseline"):
            return []
        state = self._form_state()
        return [k for k, v in state.items() if self._baseline.get(k) != v]

    def is_dirty(self) -> bool:
        return bool(self.dirty_fields())

    def revert(self):
        """Discard unsaved edits: put every field back to the stored settings."""
        s = self.ctx.settings
        self.host.setText(s.get("ollama_host") or "")
        self._reload_models(prefer_saved=True)
        cur = s.get("whisper_model")
        for i in range(self.whisper.count()):
            if self.whisper.itemData(i) == cur:
                self.whisper.setCurrentIndex(i); break
        self.compute.setCurrentText(s.get("whisper_compute"))
        self.device.setCurrentText(s.get("whisper_device"))
        self._set_lang(s.get("language"))
        self.fillers.setChecked(s.get("remove_fillers", True))
        self.diarize.setChecked(s.get("diarize", False))
        self.auto_record.blockSignals(True)            # no consent notice for a revert
        self.auto_record.setChecked(bool(s.get("auto_record", False)))
        self.auto_record.blockSignals(False)
        self.preset.setCurrentText(s.get("quality_preset", "Balanced"))
        self.chunk.setValue(int(s.get("chunk_chars", 6000)))
        self.repo.setText(s.get("github_repo", ""))
        self.auto_check.setChecked(s.get("auto_check_updates", True))
        self.crash_reporter.setChecked(s.get("crash_reporter", True))
        self.smtp_host.setText(s.get("smtp_host", "in-v3.mailjet.com"))
        self.smtp_port.setValue(int(s.get("smtp_port", 587)))
        self.email_from.setText(s.get("email_from", ""))
        self.smtp_user.setText(s.get("smtp_user", ""))
        self.smtp_password.setText(s.get("smtp_password", ""))
        self._refresh_env()
        self._mark_clean()

    def confirm_leave(self) -> bool:
        """Called before navigating away / closing. Unsaved changes → ask to
        Save, Discard or stay. Returns True if it's OK to leave."""
        if not self.is_dirty():
            return True
        r = QMessageBox.question(
            self, "Unsaved settings",
            "You have changed settings that haven't been saved yet.\n\n"
            "Save them now?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Save:
            return bool(self._save())
        if r == QMessageBox.Discard:
            self.revert()
            return True
        return False

    def _auto_record_toggled(self, on: bool):
        """First-time consent notice when auto-record is switched on."""
        if on and not self.ctx.settings.get("auto_record_consent_ack", False):
            QMessageBox.information(
                self, "Before you auto-record",
                "MICO360 will offer to record meetings it detects on this PC.\n\n"
                "Recording a conversation can require the consent of everyone in it "
                "(laws and company policies vary). You are responsible for telling "
                "participants. While capturing, the window title shows “● Recording”.")
            self.ctx.settings.set("auto_record_consent_ack", True)

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

    # -- Ollama host (M10): the field's value, not the last saved one --------
    def _typed_host(self) -> str:
        return self.host.text().strip() or DEFAULT_OLLAMA_HOST

    def _host_status(self):
        """Reachability + models of the Ollama host typed in the field."""
        return _ollama.check_status(self._typed_host())

    def sync_from_settings(self) -> None:
        """Called when the page is shown: pick up values changed elsewhere (the
        model chosen on New Meeting) unless the user is editing them here."""
        if "ollama_model" in self.dirty_fields():
            return
        saved = self.ctx.settings.get("ollama_model") or ""
        if saved and self.model.findText(saved) >= 0 and self.model.currentText() != saved:
            self.model.setCurrentText(saved)
        self._mark_clean("ollama_model")

    def _refresh_clicked(self):
        self._clear_status_cache()                   # an explicit Refresh must re-query now
        self._reload_models()
        self._refresh_env()

    def _reload_models(self, prefer_saved: bool = False):
        was_clean = "ollama_model" not in self.dirty_fields()
        current = self.model.currentText() if self.model.count() else ""
        status = self._host_status()
        self.model.clear()
        if status.running and status.models:
            self.model.addItems(status.models)
            saved = self.ctx.settings.get("ollama_model")
            # keep the user's (unsaved) choice across a refresh, else the saved one
            if not prefer_saved and current in status.models:
                self.model.setCurrentText(current)
            elif saved in status.models:
                self.model.setCurrentText(saved)
        else:
            self.model.addItem("(Ollama not running)")
        if was_clean:
            self._mark_clean("ollama_model")

    def _provider_changed(self):
        key = self.provider_box.currentData()
        prev = self.ctx.settings.get("ai_provider", "local")
        if key == "cloud" and prev != "cloud" and not self._confirm_cloud_transport():
            i = self.provider_box.findData(prev)             # user declined — stay put
            self.provider_box.setCurrentIndex(i if i >= 0 else 0)
            return
        self.ctx.settings.set("ai_provider", key)
        self._refresh_env()
        self.on_models_change()          # rebuild the New Meeting model list
        from ..config import AI_PROVIDERS
        self.toast.show_message(f"AI mode: {AI_PROVIDERS.get(key, key)}.", "success")

    def _confirm_cloud_transport(self) -> bool:
        """Cloud mode sends transcripts to the MICO360 Connect server. While that
        connection is not encrypted, make the user choose it knowingly (C5)."""
        from ..core import cloud_client
        cloud_client.start_https_probe()
        if cloud_client.is_encrypted():
            return True
        return QMessageBox.question(
            self, "Switch to MICO360 Cloud?",
            "In MICO360 Cloud mode your meeting transcripts are sent to the MICO360 "
            "Connect server to write the minutes (audio never leaves this PC).\n\n"
            "That connection is currently NOT encrypted, so on shared or public "
            "networks others could read the transcripts. For confidential meetings "
            "use Local (Ollama).\n\nSwitch to MICO360 Cloud anyway?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

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
        st = self._host_status()
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
        if _running(self._pull_worker):
            return                                       # one install at a time
        st = self._host_status()
        if not st.running:
            QMessageBox.warning(self, "Ollama not running",
                                f"Ollama must be running at {self._typed_host()} to install a "
                                "model.\n\nStart it (open the Ollama app or run 'ollama serve') "
                                "and try again.")
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
        from . import workers as _workers
        self.install_btn.setEnabled(False)
        self.install_cancel_btn.setEnabled(True); self.install_cancel_btn.setText("Cancel")
        self.install_cancel_btn.setVisible(True)
        self.install_progress.setVisible(True); self.install_progress.setRange(0, 100)
        self.install_status.setText(f"Installing “{model}”…")
        w = self._pull_worker = _workers.ModelPullWorker(self._typed_host(), model)
        _keep_alive(self, w)                             # never dropped while running
        w.progress.connect(self._on_pull_progress)
        w.finished_ok.connect(self._on_pull_done)
        w.failed.connect(self._on_pull_failed)
        w.start()

    def _cancel_install(self):
        if _running(self._pull_worker):
            self._pull_worker.cancel()                   # finishes via failed("Cancelled.")
            self.install_cancel_btn.setEnabled(False); self.install_cancel_btn.setText("Cancelling…")
            self.install_status.setText("Cancelling…")

    def _install_finished_ui(self):
        self.install_btn.setEnabled(True)
        self.install_cancel_btn.setVisible(False)

    def _on_pull_progress(self, frac: float, status: str):
        if frac < 0:
            self.install_progress.setRange(0, 0)        # indeterminate (e.g. "pulling manifest")
        else:
            self.install_progress.setRange(0, 100)
            self.install_progress.setValue(int(frac * 100))
        self.install_status.setText(status)

    def _on_pull_done(self, model: str):
        self._install_finished_ui()
        self.install_progress.setRange(0, 100); self.install_progress.setValue(100)
        self.install_status.setText(f"✓ Installed “{model}”.")
        self.toast.show_message(f"Model “{model}” installed.", "success", 5000)
        self._clear_status_cache()
        self._reload_models(); self._refresh_env()
        self.on_models_change()

    def _on_pull_failed(self, msg: str):
        self._install_finished_ui()
        self.install_progress.setVisible(False)
        if "cancel" in msg.lower():
            self.install_status.setText("Install cancelled.")
            return
        self.install_status.setText(f"Install failed: {msg}")
        QMessageBox.critical(self, "Model install failed",
                             f"{msg}\n\nIf you are offline, connect to the internet and retry.")

    def is_busy(self) -> bool:
        return any(_running(w) for w in self._bg_workers)

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
        apply_quality_preset(self.ctx.settings, name)    # stored immediately
        # reflect the preset's Whisper model/compute in the combos
        target = QUALITY_PRESETS[name]["whisper_model"]
        for i in range(self.whisper.count()):
            if self.whisper.itemData(i) == target:
                self.whisper.setCurrentIndex(i); break
        self.compute.setCurrentText(QUALITY_PRESETS[name]["whisper_compute"])
        self._mark_clean("quality_preset", "whisper_model", "whisper_compute")
        self.toast.show_message(f"{name} preset applied.", "success")

    def _whisper_fields_changed(self, *_):
        """L5: editing Whisper model/compute by hand no longer matches the named
        preset → show the preset that matches them, or "Custom"."""
        from ..config import QUALITY_PRESETS
        size, compute = self.whisper.currentData(), self.compute.currentText()
        match = next((n for n, p in QUALITY_PRESETS.items()
                      if p.get("whisper_model") == size and p.get("whisper_compute") == compute),
                     "Custom")
        if self.preset.currentText() != match:
            self.preset.setCurrentText(match)

    def _clear_status_cache(self):
        """Forget the context's short-lived AI status so the next read queries
        the (possibly new) host instead of a stale cached answer."""
        inv = getattr(self.ctx, "invalidate_ai_status", None)
        if callable(inv):
            inv()

    def _save(self) -> bool:
        repo = normalize_repo(self.repo.text())
        if repo is None:
            self._tabs.setCurrentIndex(self._updates_tab_index)
            self.repo.setFocus(Qt.OtherFocusReason)
            QMessageBox.warning(
                self, "GitHub repo not recognised",
                "The GitHub repo must look like owner/name (for example "
                "mico360om/MICO360-Meetings) or be a github.com link to the repository.\n\n"
                "Nothing was saved — fix the repo field (or clear it) and save again.")
            return False
        s = self.ctx.settings
        model_changed = "ollama_model" in self.dirty_fields()
        s.set("ui_scale", float(self.scale_box.currentData()))
        s.set("ai_provider", self.provider_box.currentData())
        s.set("ollama_host", self._typed_host())
        # Only when edited HERE: the model can also be picked on New Meeting, and
        # re-saving this (stale) combo would silently undo that choice.
        if model_changed and not self.model.currentText().startswith("("):
            s.set("ollama_model", self.model.currentText())
        s.set("whisper_model", self.whisper.currentData())
        s.set("whisper_compute", self.compute.currentText())
        s.set("whisper_device", self.device.currentText())
        s.set("language", normalize_language(self.lang.currentData()))
        s.set("remove_fillers", self.fillers.isChecked())
        s.set("diarize", self.diarize.isChecked())
        s.set("auto_record", self.auto_record.isChecked())
        cb = getattr(self, "on_auto_record_change", None)
        if cb:
            cb(self.auto_record.isChecked())
        s.set("quality_preset", self.preset.currentText())
        s.set("chunk_chars", self.chunk.value())
        self.repo.setText(repo)                          # show the normalised form
        s.set("github_repo", repo)
        s.set("auto_check_updates", self.auto_check.isChecked())
        s.set("crash_reporter", self.crash_reporter.isChecked())
        s.set("smtp_host", self.smtp_host.text().strip() or "in-v3.mailjet.com")
        s.set("smtp_port", self.smtp_port.value())
        s.set("email_from", self.email_from.text().strip())
        s.set("smtp_user", self.smtp_user.text().strip())
        # Passwords may legitimately start/end with spaces — store exactly as typed.
        s.set("smtp_password", self.smtp_password.text())
        self.host.setText(self._typed_host())
        # A changed host (or anything else) must show up now, not after 4 s.
        self._clear_status_cache()
        self._reload_models(prefer_saved=True)
        self._refresh_env()
        self._mark_clean()
        self.toast.show_message("Settings saved.", "success")
        self.on_models_change()
        return True

    def _send_test_email(self):
        if _running(self._test_worker):
            return                                       # a test is already being sent
        from ..core.emailer import SmtpConfig
        cfg = SmtpConfig(host=self.smtp_host.text().strip() or "in-v3.mailjet.com",
                         port=self.smtp_port.value(),
                         user=self.smtp_user.text().strip(),
                         password=self.smtp_password.text(),
                         sender=self.email_from.text().strip())
        if not cfg.configured:
            QMessageBox.information(self, "Incomplete", "Fill in host, from, user and password first.")
            return
        self.toast.show_message("Sending test email…", "info")

        from . import workers as _workers
        self.test_btn.setEnabled(False); self.test_btn.setText("Sending…")
        w = self._test_worker = _workers.EmailWorker(
            cfg, cfg.sender, "MICO360 Meetings - test email",
            "This is a test email from MICO360 Meetings. Your SMTP settings work.")
        _keep_alive(self, w)                             # kept until the thread has finished
        w.finished.connect(self._test_email_finished)
        w.finished_ok.connect(
            lambda: self.toast.show_message(f"Test email sent to {cfg.sender}.", "success", 6000))
        w.failed.connect(
            lambda m: QMessageBox.critical(self, "Test failed",
                                           f"{m}\n\nCheck host/port/user/password and that the "
                                           "sender is a validated Mailjet sender."))
        w.start()

    def _test_email_finished(self):
        self.test_btn.setEnabled(True); self.test_btn.setText("Send test email")

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
