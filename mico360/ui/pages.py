"""Application pages: New Meeting, History, Company Profiles, Prompt Library, Settings."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core import documents
from ..core.audio import MEDIA_EXTS
from ..core.history import Meeting
from ..core.prompts import OUTPUT_STYLES, SavedPrompt
from ..core.transcription import WHISPER_MODELS
from .components import (
    Card, CollapsibleSection, DropArea, EmptyState, hint, section_title, subtitle, tip,
)
from .context import AppContext
from .dialogs import ProfileDialog, PromptDialog
from .recording_panel import RecordingPanel
from .workers import GenerateWorker, TranscribeWorker

log = logging.getLogger("mico360.pages")

UPLOAD_EXTS = set(MEDIA_EXTS) | documents.DOC_EXTS | documents.IMAGE_EXTS


def _scroll(inner: QWidget, max_width: int = 1160) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.NoFrame)
    # Cap the content measure and centre it, so forms/text don't stretch
    # edge-to-edge on a wide window (a comfortable, professional line length).
    if max_width:
        inner.setMaximumWidth(max_width)
        sa.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    sa.setWidget(inner)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    return sa


# ===========================================================================
# New Meeting
# ===========================================================================
class NewMeetingPage(QWidget):
    # Wizard step indices
    STEP_SOURCE, STEP_TRANSCRIPT, STEP_SETUP, STEP_REVIEW, STEP_MINUTES = range(5)
    STEP_TITLES = ["Source", "Transcript", "Setup", "Review", "Minutes"]

    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx = ctx
        self.toast = toast
        self._media_queue: list[str] = []
        self._worker = None
        self._current_id: int | None = None
        self._loaded_from_history = False       # True while editing a record opened from History
        self._auto_generate = False             # chain generation after a one-click transcription
        self._last_saved_at = None              # timestamp of the last History save (for the indicator)
        # Wired by MainWindow so the readiness banner's actions can navigate.
        self.on_open_settings = None            # callable() -> open the Settings page
        self.on_install_model = None            # callable() -> Settings + focus the installer
        self.on_switch_local = None             # callable() -> switch AI mode to Local
        self._build()
        self.refresh_models()
        self.refresh_prompts()
        self.refresh_meeting_types()
        self._autosave_sig = ""
        self._setup_autosave()

    # -- UI -----------------------------------------------------------------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(14)

        title = QLabel("New Meeting"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Follow the steps to turn a recording, file or transcript into minutes."))

        # Readiness banner sits just below the title.
        self.ready_banner = self._build_ready_banner()
        v.addWidget(self.ready_banner)

        # Progress stepper header
        v.addWidget(self._build_stepper())

        # The steps live in a stack; only one shows at a time.
        self.wizard = QStackedWidget()
        self.sec_source = self._step1_source()
        self.sec_transcript = self._step2_transcript()
        self.sec_generate = self._step3_generate()      # meeting details + AI setup
        self.sec_review = self._step_review()
        self.sec_minutes = self._step4_minutes()
        for p in (self.sec_source, self.sec_transcript, self.sec_generate,
                  self.sec_review, self.sec_minutes):
            self.wizard.addWidget(p)
        v.addWidget(self.wizard)

        # Operation status + progress (transcription / generation).
        self.status = QLabel(""); self.status.setObjectName("Hint")
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        self.progress.setVisible(False)
        v.addWidget(self.status)
        v.addWidget(self.progress)

        # Back / Next / Create footer.
        v.addLayout(self._build_footer())
        v.addStretch()

        self._reached = 0
        self._goto_step(self.STEP_SOURCE)
        outer.addWidget(_scroll(content))

    # -- wizard shell -------------------------------------------------------
    def _build_stepper(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w); row.setContentsMargins(0, 0, 0, 2); row.setSpacing(4)
        self._step_chips = []
        for i, name in enumerate(self.STEP_TITLES):
            chip = QPushButton(f"{i + 1} {name}"); chip.setObjectName("StepChip")
            chip.setCheckable(True); chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, idx=i: self._goto_step(idx))
            tip(chip, f"Step {i + 1}: {name}")
            self._step_chips.append(chip); row.addWidget(chip)
        row.addStretch()
        return w

    def _build_footer(self) -> QHBoxLayout:
        f = QHBoxLayout()
        self.back_btn = QPushButton("←  Back"); self.back_btn.setObjectName("Ghost")
        self.back_btn.clicked.connect(self._back)
        tip(self.back_btn, "Return to the previous step")
        self.new_meeting_btn = QPushButton("✚  New meeting"); self.new_meeting_btn.setObjectName("Ghost")
        self.new_meeting_btn.clicked.connect(self._new_meeting)
        tip(self.new_meeting_btn, "Save this meeting to History and start a fresh one (Ctrl+N)")
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setObjectName("Ghost")
        self.cancel_btn.setVisible(False); self.cancel_btn.clicked.connect(self._cancel)
        tip(self.cancel_btn, "Stop the current generation — the transcript is kept")
        self.generate_btn = QPushButton("✨  Create meeting"); self.generate_btn.setObjectName("Primary")
        self.generate_btn.clicked.connect(self._create_meeting)
        tip(self.generate_btn, "Transcribe (if needed) and generate the minutes, then save to "
                               "History (Ctrl+G)")
        self.next_btn = QPushButton("Next  →"); self.next_btn.setObjectName("Primary")
        self.next_btn.clicked.connect(self._next)
        tip(self.next_btn, "Continue to the next step")
        f.addWidget(self.back_btn); f.addStretch()
        f.addWidget(self.new_meeting_btn); f.addWidget(self.cancel_btn)
        f.addWidget(self.generate_btn); f.addWidget(self.next_btn)
        return f

    def _goto_step(self, i: int):
        i = max(0, min(i, len(self.STEP_TITLES) - 1))
        if i > self._reached and not self._advance_ok(self.wizard.currentIndex()):
            return                                       # forward jump blocked by validation
        self.wizard.setCurrentIndex(i)
        self._reached = max(self._reached, i)
        if i == self.STEP_REVIEW:
            self._refresh_review()
        self._update_stepper()

    def _update_stepper(self):
        i = self.wizard.currentIndex()
        for idx, chip in enumerate(self._step_chips):
            chip.setChecked(idx == i)
            chip.setProperty("done", idx < i)
            chip.setEnabled(idx <= self._reached)
            chip.style().unpolish(chip); chip.style().polish(chip)
        self.back_btn.setVisible(i > 0)
        self.next_btn.setVisible(i in (self.STEP_SOURCE, self.STEP_TRANSCRIPT, self.STEP_SETUP))
        self.generate_btn.setVisible(i == self.STEP_REVIEW)
        self.new_meeting_btn.setVisible(i == self.STEP_MINUTES)

    def _advance_ok(self, i: int) -> bool:
        ok, msg = self._validate_step(i)
        if not ok:
            self.toast.show_message(msg, "warn")
        return ok

    def _validate_step(self, i: int):
        if i == self.STEP_SOURCE:
            if self._media_queue or self.transcript.toPlainText().strip():
                return True, ""
            return False, "Add a file, make a recording, or paste a transcript to continue."
        if i == self.STEP_TRANSCRIPT:
            if self.transcript.toPlainText().strip():
                return True, ""
            return False, "Transcribe your media (Step 1) or paste a transcript to continue."
        return True, ""

    def _next(self):
        i = self.wizard.currentIndex()
        if self._advance_ok(i):
            self._goto_step(i + 1)

    def _back(self):
        self._goto_step(self.wizard.currentIndex() - 1)

    def _create_meeting(self):
        """Review step's action: transcribe (if needed), generate minutes, save."""
        self._transcribe_and_generate()

    # -- readiness banner ---------------------------------------------------
    def _build_ready_banner(self) -> QWidget:
        w = QWidget(); w.setObjectName("Banner"); w.setVisible(False)
        row = QHBoxLayout(w); row.setContentsMargins(14, 10, 10, 10); row.setSpacing(10)
        icon = QLabel("⚠"); icon.setObjectName("BannerIcon")
        self.banner_text = QLabel(); self.banner_text.setObjectName("BannerText")
        self.banner_text.setWordWrap(True)
        self.banner_btn1 = QPushButton(); self.banner_btn1.setObjectName("Primary")
        self.banner_btn1.clicked.connect(lambda: self._banner_act1 and self._banner_act1())
        self.banner_btn2 = QPushButton(); self.banner_btn2.setObjectName("Ghost")
        self.banner_btn2.clicked.connect(lambda: self._banner_act2 and self._banner_act2())
        close = QPushButton("✕"); close.setObjectName("BannerClose")
        close.setFixedSize(26, 26); close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Dismiss until the status changes")
        close.clicked.connect(self._dismiss_banner)
        row.addWidget(icon); row.addWidget(self.banner_text, 1)
        row.addWidget(self.banner_btn1); row.addWidget(self.banner_btn2); row.addWidget(close)
        self._banner_act1 = None; self._banner_act2 = None
        self._banner_sig = None; self._banner_dismissed_sig = None
        return w

    def refresh_readiness(self):
        """Show a one-click 'AI not ready' banner when the active provider can't
        generate minutes yet (Ollama down / no model / cloud unreachable)."""
        st = self.ctx.ai_status()
        if st.running and st.models:
            self._set_banner(None)
            return
        if self.ctx.provider() == "cloud":
            self._set_banner(
                "cloud",
                (st.error or "MICO360 Cloud is unreachable.") + " Minutes can't be generated.",
                primary=("Switch to Local", self._act_switch_local),
                secondary=("Open Settings", self._act_open_settings))
        elif not st.running:
            self._set_banner(
                "ollama_down",
                "Ollama isn't running, so minutes can't be generated. Start it "
                "(open the Ollama app or run “ollama serve”), then Retry.",
                primary=("Retry", self.refresh_models),
                secondary=("Open Settings", self._act_open_settings))
        else:  # running, but no models installed
            self._set_banner(
                "no_model",
                "No AI model is installed yet — you need one to generate minutes.",
                primary=("Install a model", self._act_install_model),
                secondary=("Retry", self.refresh_models))

    def _set_banner(self, sig, text="", primary=None, secondary=None):
        if sig is None:                         # ready → clear (and un-dismiss)
            self._banner_dismissed_sig = None
            self.ready_banner.setVisible(False)
            return
        self._banner_sig = sig
        if sig == self._banner_dismissed_sig:   # user dismissed this exact problem
            self.ready_banner.setVisible(False)
            return
        self.banner_text.setText(text)
        self._banner_act1 = primary[1] if primary else None
        self._banner_act2 = secondary[1] if secondary else None
        for btn, spec in ((self.banner_btn1, primary), (self.banner_btn2, secondary)):
            btn.setVisible(bool(spec))
            if spec:
                btn.setText(spec[0])
        self.ready_banner.setVisible(True)

    def _dismiss_banner(self):
        self._banner_dismissed_sig = self._banner_sig
        self.ready_banner.setVisible(False)

    def _act_open_settings(self):
        if self.on_open_settings:
            self.on_open_settings()

    def _act_install_model(self):
        (self.on_install_model or self.on_open_settings or (lambda: None))()

    def _act_switch_local(self):
        if self.on_switch_local:
            self.on_switch_local()

    def _card(self, title_text: str, expanded: bool = True):
        """A plain wizard step panel: a header + a content layout to fill."""
        panel = QWidget(); panel.setObjectName("Card")
        lay = QVBoxLayout(panel); lay.setContentsMargins(22, 18, 22, 20); lay.setSpacing(12)
        hdr = QLabel(title_text); hdr.setObjectName("StepHeader")
        lay.addWidget(hdr)
        return panel, lay

    def _step_review(self) -> QWidget:
        card, lay = self._card("Review")
        lay.addWidget(hint("Check the details below, then click “Create meeting” to transcribe "
                           "(if needed) and generate the minutes."))
        grid = QGridLayout(); grid.setHorizontalSpacing(18); grid.setVerticalSpacing(9)
        self._review_vals: dict[str, QLabel] = {}
        for r, name in enumerate(("Title", "Date / time", "Attendees", "Source",
                                  "Transcript", "AI mode", "Model", "Style", "Prompt")):
            k = QLabel(name); k.setObjectName("Hint")
            val = QLabel("—"); val.setObjectName("ReviewVal"); val.setWordWrap(True)
            grid.addWidget(k, r, 0, Qt.AlignTop); grid.addWidget(val, r, 1)
            self._review_vals[name] = val
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addStretch()
        return card

    def _refresh_review(self):
        v = self._review_vals
        v["Title"].setText(self.meet_title.text().strip() or "(auto — inferred from the minutes)")
        v["Date / time"].setText(self.meet_date.text().strip() or "—")
        v["Attendees"].setText(self.meet_attendees.text().strip() or "—")
        n = len(self._media_queue)
        v["Source"].setText(f"{n} media file(s) queued for transcription" if n
                            else "Pasted / typed transcript")
        words = len(self.transcript.toPlainText().split())
        v["Transcript"].setText(f"{words:,} words" if words else "empty — nothing to summarise yet")
        v["AI mode"].setText("MICO360 Cloud" if self.ctx.provider() == "cloud" else "Local (Ollama)")
        v["Model"].setText(self.model_box.currentText() or "—")
        v["Style"].setText(self.style_box.currentText() or "—")
        v["Prompt"].setText("Custom (edited for this run)" if self.toggle_prompt.isChecked()
                            else (self.prompt_box.currentText() or "—"))

    def _step1_source(self) -> QWidget:
        card, lay = self._card("Add your meeting")
        self.source_tabs = QTabWidget()

        # Upload tab
        up = QWidget(); upl = QVBoxLayout(up)
        self.drop = DropArea(
            accept_exts=UPLOAD_EXTS,
            caption="Drag & drop audio, video or documents",
            sub="MP3 · WAV · M4A · MP4 · MOV · MKV · PDF · DOCX · TXT · images — multiple files supported",
        )
        self.drop.fileChosen.connect(self._add_file)
        self.files_list = QListWidget(); self.files_list.setMaximumHeight(120)
        self.files_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.files_list.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.files_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files_list.setDragDropMode(QAbstractItemView.InternalMove)  # drag to reorder
        self.files_list.setToolTip("Drag to reorder · select + Remove to delete")

        # add / remove controls
        list_bar = QHBoxLayout()
        add_btn = QPushButton("➕ Add files…"); add_btn.clicked.connect(self.drop._browse)
        tip(add_btn, "Browse for audio, video or document files to add to the queue")
        self.remove_btn = QPushButton("Remove selected"); self.remove_btn.clicked.connect(self._remove_selected)
        tip(self.remove_btn, "Remove the highlighted file(s) from the queue — the files on disk are not deleted")
        self.clear_btn = QPushButton("Clear all"); self.clear_btn.clicked.connect(self._clear_files)
        tip(self.clear_btn, "Empty the whole queue (files on disk are not deleted)")
        list_bar.addWidget(add_btn)
        list_bar.addStretch()
        list_bar.addWidget(self.remove_btn)
        list_bar.addWidget(self.clear_btn)

        # Secondary: transcribe only (for reviewing/editing the transcript first).
        self.transcribe_btn = QPushButton("Transcribe only")
        self.transcribe_btn.setObjectName("Ghost")
        self.transcribe_btn.clicked.connect(self._start_transcription)
        self.transcribe_btn.setEnabled(False)
        tip(self.transcribe_btn, "Transcribe the queued media to text only, so you can review and "
                                 "edit it before generating. Offline Whisper — first run downloads "
                                 "the model (internet needed once)")
        # Primary happy-path: transcribe, then generate minutes in one click.
        self.tg_btn = QPushButton("✨  Transcribe & generate minutes")
        self.tg_btn.setObjectName("Primary")
        self.tg_btn.clicked.connect(self._transcribe_and_generate)
        self.tg_btn.setEnabled(False)
        tip(self.tg_btn, "One click: transcribe the queued media and then generate minutes using "
                         "the model, style and prompt selected in Step 3 — the whole flow in one go")
        cta = QHBoxLayout(); cta.addStretch()
        cta.addWidget(self.transcribe_btn); cta.addWidget(self.tg_btn)
        upl.addWidget(self.drop)
        upl.addWidget(hint("Documents are read instantly into the transcript; audio/video are transcribed with Whisper. "
                           "Recordings also appear here."))
        upl.addWidget(self.files_list)
        upl.addLayout(list_bar)
        upl.addLayout(cta)
        self.source_tabs.addTab(up, "Upload files")

        # Record tab — full audio/screen/camera recorder with live details
        rec = QWidget(); rl = QVBoxLayout(rec)
        rl.setContentsMargins(0, 0, 0, 0)
        self.recorder_panel = RecordingPanel(self.toast)
        self.recorder_panel.recordingReady.connect(self._on_recording_ready)
        rl.addWidget(self.recorder_panel)
        self.source_tabs.addTab(rec, "Record")

        lay.addWidget(self.source_tabs)
        return card

    # -- queue management ---------------------------------------------------
    def _set_transcribe_enabled(self, on: bool):
        """Enable/disable both source-action buttons together."""
        self.transcribe_btn.setEnabled(on)
        self.tg_btn.setEnabled(on)

    def _add_queue_item(self, path: str, label: str, is_media: bool):
        it = QListWidgetItem(label)
        it.setData(Qt.UserRole, path)
        it.setData(Qt.UserRole + 1, is_media)
        self.files_list.addItem(it)
        if is_media:
            self._set_transcribe_enabled(True)

    def _remove_selected(self):
        for it in self.files_list.selectedItems():
            path = it.data(Qt.UserRole)
            if it.data(Qt.UserRole + 1) and path in self._media_queue:
                self._media_queue.remove(path)
            self.files_list.takeItem(self.files_list.row(it))
        self._set_transcribe_enabled(bool(self._media_queue))

    def _clear_files(self):
        self.files_list.clear()
        self._media_queue.clear()
        self._set_transcribe_enabled(False)

    def _on_recording_ready(self, path: str):
        """A finished recording -> queue it for transcription (shown in Upload tab)."""
        self._media_queue.append(path)
        self._add_queue_item(path, f"⏺  {Path(path).name}  (recorded)", True)
        self.source_tabs.setCurrentIndex(0)   # show the queue + action buttons
        self.toast.show_message("Recording added — click ‘Transcribe & generate minutes’.",
                                "success", 5000)

    def _step2_transcript(self) -> QWidget:
        card, lay = self._card("Transcript")
        lay.addWidget(hint("Editable — paste a transcript here, or review the transcription result."))
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("Paste or edit the meeting transcript here…")
        self.transcript.setMinimumHeight(150)
        self.transcript.textChanged.connect(self._update_counts)
        tip(self.transcript, "The meeting text the AI will summarise. Fully editable — "
                             "Ctrl+Z to undo. Autosaved to History every 20 seconds")
        self.transcript_count = QLabel("0 words"); self.transcript_count.setObjectName("Hint")
        row = QHBoxLayout()
        clear = QPushButton("Clear"); clear.clicked.connect(lambda: self.transcript.clear())
        tip(clear, "Clear the transcript text (Ctrl+Z restores it)")
        row.addWidget(self.transcript_count); row.addStretch(); row.addWidget(clear)
        lay.addWidget(self.transcript)
        lay.addLayout(row)
        return card

    def _step3_generate(self) -> QWidget:
        card, lay = self._card("Meeting details & AI setup")

        # Meeting type preset + calendar pre-fill
        mrow = QHBoxLayout()
        tcol = QVBoxLayout(); tcol.setSpacing(3)
        tl = QLabel("Meeting type"); tl.setObjectName("Hint")
        self.mtype_box = QComboBox(); self.mtype_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.mtype_box.activated.connect(self._apply_meeting_type)
        tip(self.mtype_box, "One-click preset: applies a matching prompt, output style and "
                            "company profile for this kind of meeting")
        tcol.addWidget(tl); tcol.addWidget(self.mtype_box)
        mrow.addLayout(tcol, 1)
        icscol = QVBoxLayout(); icscol.setSpacing(3)
        icscol.addWidget(QLabel(""))
        self.ics_btn = QPushButton("📅  Import .ics")
        self.ics_btn.setToolTip("Pre-fill meeting title, date and attendees from a calendar invite")
        self.ics_btn.clicked.connect(self._import_ics)
        icscol.addWidget(self.ics_btn)
        mrow.addLayout(icscol)
        lay.addLayout(mrow)

        # meeting details (pre-filled from .ics; used in the minutes header)
        det = QHBoxLayout()
        self.meet_title = QLineEdit(); self.meet_title.setPlaceholderText("Meeting title (optional)")
        tip(self.meet_title, "Used as the Meeting Title in the minutes header — otherwise the AI "
                             "infers it or writes 'Not specified'")
        self.meet_date = QLineEdit(); self.meet_date.setPlaceholderText("Date/time (optional)")
        tip(self.meet_date, "Meeting date/time for the minutes header, e.g. 2026-07-14 10:00")
        self.meet_attendees = QLineEdit(); self.meet_attendees.setPlaceholderText("Attendees, comma-separated (optional)")
        tip(self.meet_attendees, "Attendee names for the minutes header, separated by commas — "
                                 "filled automatically when you import a .ics invite")
        det.addWidget(self.meet_title, 2); det.addWidget(self.meet_date, 1); det.addWidget(self.meet_attendees, 2)
        lay.addLayout(det)

        controls = QHBoxLayout()

        self.model_box = QComboBox(); self.model_box.setMinimumWidth(130)
        self.style_box = QComboBox(); self.style_box.addItems(list(OUTPUT_STYLES.keys()))
        self.style_box.setCurrentText(self.ctx.settings.get("output_style"))
        self.prompt_box = QComboBox(); self.prompt_box.setMinimumWidth(130)
        self.prompt_box.currentIndexChanged.connect(self._load_prompt_text)
        for b in (self.model_box, self.style_box, self.prompt_box):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        tip(self.model_box, "Which AI model writes the minutes, from the active AI mode "
                            "(Settings → AI mode). Larger models give richer minutes but are slower")
        tip(self.style_box, "Output format: Formal, Short Summary, Detailed, Action Item Report "
                            "or Executive Summary")
        tip(self.prompt_box, "The instruction template sent to the AI — manage templates in the "
                             "Prompt Library page")
        for col_i, (label, w) in enumerate(
                (("Model", self.model_box), ("Style", self.style_box), ("Prompt", self.prompt_box))):
            col = QVBoxLayout(); col.setSpacing(3)
            l = QLabel(label); l.setObjectName("Hint")
            col.addWidget(l); col.addWidget(w)
            controls.addLayout(col, 1)
        lay.addLayout(controls)

        # editable per-upload prompt
        self.toggle_prompt = QCheckBox("Edit prompt for this generation")
        self.toggle_prompt.toggled.connect(self._toggle_prompt_editor)
        tip(self.toggle_prompt, "Tweak the prompt for this run only — the saved template in the "
                                "Prompt Library is not changed")
        lay.addWidget(self.toggle_prompt)
        self.prompt_edit = QPlainTextEdit(); self.prompt_edit.setVisible(False)
        self.prompt_edit.setMinimumHeight(120)
        tip(self.prompt_edit, "One-off prompt for this generation. Keep [TRANSCRIPT_HERE] where "
                              "the transcript should be inserted")
        lay.addWidget(self.prompt_edit)
        lay.addStretch()
        # Note: the primary action (Create meeting) + Cancel live in the wizard footer.
        return card

    def _step4_minutes(self) -> QWidget:
        card, lay = self._card("Meeting minutes")

        # Editable title — this names the meeting in History (auto-filled, editable).
        trow = QHBoxLayout()
        tl = QLabel("Title"); tl.setObjectName("Hint")
        self.meeting_title = QLineEdit()
        self.meeting_title.setPlaceholderText("Give this meeting a clear title so it's easy to find in History…")
        self.meeting_title.textChanged.connect(self._update_save_indicator)
        tip(self.meeting_title, "The name this meeting gets in History. Auto-filled from the minutes — "
                                "edit it to keep past meetings easy to scan and search")
        trow.addWidget(tl); trow.addWidget(self.meeting_title, 1)
        lay.addLayout(trow)

        self.minutes_tabs = QTabWidget()
        self.minutes = QPlainTextEdit()
        self.minutes.setPlaceholderText("Generated minutes will appear here. You can edit before exporting.")
        self.minutes.setMinimumHeight(240)
        self.minutes.textChanged.connect(self._update_minutes_status)
        self.minutes_preview = QTextBrowser()
        self.minutes_preview.setOpenExternalLinks(True)
        self.minutes_tabs.addTab(self.minutes, "Edit")
        self.minutes_tabs.addTab(self.minutes_preview, "Preview")
        self.minutes_tabs.currentChanged.connect(self._on_minutes_tab)
        tip(self.minutes, "The generated minutes — edit freely before exporting (Ctrl+Z to undo). "
                          "Autosaved to History")
        tip(self.minutes_preview, "Read-only formatted view of the minutes, exactly as headings and "
                                  "tables will look when exported")
        lay.addWidget(self.minutes_tabs)

        row = QHBoxLayout()
        self.minutes_count = QLabel(""); self.minutes_count.setObjectName("Hint")
        self.save_status = QLabel(""); self.save_status.setObjectName("Hint")
        tip(self.save_status, "Your work autosaves to History every 20 seconds — this shows when "
                              "it was last saved")
        self.copy_btn = QPushButton("Copy"); self.copy_btn.clicked.connect(self._copy)
        tip(self.copy_btn, "Copy the minutes with formatting — pastes styled into Word/Outlook, "
                           "plain into text editors (Ctrl+Shift+C)")
        self.save_btn = QPushButton("Save to history"); self.save_btn.clicked.connect(self._save_history)
        tip(self.save_btn, "Save this meeting to History now (Ctrl+S) — it also autosaves every "
                           "20 seconds")
        self.email_btn = QPushButton("📧 Email…"); self.email_btn.clicked.connect(self._email_minutes)
        tip(self.email_btn, "Send the minutes by email with optional PDF/Word attachments. "
                            "Needs SMTP details in Settings → Email first")
        self.export_btn = QPushButton("Export…"); self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self._export)
        tip(self.export_btn, "Save as Word, PDF, text, Markdown or HTML (Ctrl+E). Uses the active "
                             "Company Profile for logo, footer and page numbers")
        row.addWidget(self.minutes_count); row.addWidget(self.save_status)
        row.addWidget(self.copy_btn); row.addWidget(self.save_btn)
        row.addStretch(); row.addWidget(self.email_btn); row.addWidget(self.export_btn)
        lay.addLayout(row)
        return card

    # -- data refresh -------------------------------------------------------
    def refresh_models(self):
        status = self.ctx.ai_status()
        cloud = self.ctx.provider() == "cloud"
        self.model_box.clear()
        if status.running and status.models:
            self.model_box.setEnabled(True)
            self.model_box.addItems(status.models)
            saved = self.ctx.ai_model()
            if saved in status.models:
                self.model_box.setCurrentText(saved)
            else:
                self.ctx.set_ai_model(self.model_box.currentText())
        elif status.running:
            self.model_box.addItem("⚠ No models available")
            self.model_box.setEnabled(False)
        else:
            self.model_box.addItem("⚠ MICO360 Cloud unavailable" if cloud
                                   else "⚠ Ollama not running")
            self.model_box.setEnabled(False)
        self.refresh_readiness()

    def refresh_prompts(self):
        self.prompt_box.blockSignals(True)
        self.prompt_box.clear()
        self._prompts: list[SavedPrompt] = self.ctx.prompts.list()
        for p in self._prompts:
            self.prompt_box.addItem(p.name, p.id)
        self.prompt_box.blockSignals(False)
        self._load_prompt_text()

    def _load_prompt_text(self):
        idx = self.prompt_box.currentIndex()
        if 0 <= idx < len(self._prompts):
            self.prompt_edit.setPlainText(self._prompts[idx].text)

    def refresh_meeting_types(self):
        self.mtype_box.blockSignals(True)
        self.mtype_box.clear()
        self.mtype_box.addItem("Custom (use selections below)", None)
        for mt in self.ctx.meeting_types.list():
            self.mtype_box.addItem(mt.name, mt.name)
        self.mtype_box.blockSignals(False)

    def _apply_meeting_type(self):
        name = self.mtype_box.currentData()
        if not name:
            return
        mt = self.ctx.meeting_types.get(name)
        if not mt:
            return
        if mt.style:
            self.style_box.setCurrentText(mt.style)
        if mt.prompt_name:
            for i in range(self.prompt_box.count()):
                if self.prompt_box.itemText(i) == mt.prompt_name:
                    self.prompt_box.setCurrentIndex(i); break
        if mt.profile_name:
            for p in self.ctx.profiles.list():
                if p.name == mt.profile_name:
                    self.ctx.settings.set("active_profile", p.id); break
        self.toast.show_message(f"Applied “{mt.name}” meeting type.", "success")

    def _import_ics(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import calendar invite", "",
                                              "Calendar (*.ics);;All files (*.*)")
        if not path:
            return
        try:
            from ..core.calendar_import import parse_ics
            d = parse_ics(path)
            if d.get("title"):
                self.meet_title.setText(d["title"])
            if d.get("date"):
                self.meet_date.setText(d["date"])
            if d.get("attendees"):
                self.meet_attendees.setText(", ".join(d["attendees"]))
            self.toast.show_message("Meeting details imported from calendar.", "success")
        except Exception as exc:
            self.toast.show_message(f"Could not read .ics: {exc}", "warn", 5000)

    def _meeting_preamble(self) -> str:
        from ..core.calendar_import import format_preamble
        attendees = [a.strip() for a in self.meet_attendees.text().split(",") if a.strip()]
        return format_preamble({"title": self.meet_title.text().strip(),
                                "date": self.meet_date.text().strip(),
                                "attendees": attendees})

    def _toggle_prompt_editor(self, on: bool):
        self.prompt_edit.setVisible(on)

    def _update_counts(self):
        words = len(self.transcript.toPlainText().split())
        self.transcript_count.setText(f"{words:,} words")
        self._update_save_indicator()

    def _update_minutes_status(self):
        words = len(self.minutes.toPlainText().split())
        self.minutes_count.setText(f"{words:,} words" if words else "")
        self._update_save_indicator()

    # -- file handling ------------------------------------------------------
    def _add_file(self, path: str):
        p = Path(path)
        ext = p.suffix.lower()
        if ext in MEDIA_EXTS:
            self._media_queue.append(path)
            self._add_queue_item(path, f"🎵  {p.name}  (queued for transcription)", True)
        elif documents.is_document(path) or documents.is_image(path):
            try:
                text = documents.extract_text(path)
                if text.strip():
                    cur = self.transcript.toPlainText()
                    sep = "\n\n" if cur.strip() else ""
                    self.transcript.setPlainText(cur + sep + text.strip())
                    self._add_queue_item(path, f"📄  {p.name}  (text imported)", False)
                    self.toast.show_message(f"Imported text from {p.name}", "success")
                else:
                    self._add_queue_item(path, f"📄  {p.name}  (no text found)", False)
            except Exception as exc:
                self._add_queue_item(path, f"⚠  {p.name}  ({exc})", False)
                self.toast.show_message(str(exc), "warn", 5000)
        else:
            self.toast.show_message(f"Unsupported file: {p.name}", "warn")

    # -- transcription ------------------------------------------------------
    def _sync_queue_from_list(self):
        """Rebuild the media queue to match the (possibly reordered) list."""
        ordered = []
        for i in range(self.files_list.count()):
            it = self.files_list.item(i)
            if it.data(Qt.UserRole + 1) and it.data(Qt.UserRole) in self._media_queue:
                ordered.append(it.data(Qt.UserRole))
        # keep any not represented in the list (safety)
        for p in self._media_queue:
            if p not in ordered:
                ordered.append(p)
        self._media_queue = ordered

    def _transcribe_and_generate(self):
        """One-click happy path: transcribe the queued media, then generate minutes.
        Falls back to plain generation if a transcript is already present."""
        if getattr(self, "_worker", None) and self._worker.isRunning():
            return
        self._sync_queue_from_list()
        if self._media_queue:
            self._auto_generate = True            # generation is chained on completion
            self._start_transcription()
        elif self.transcript.toPlainText().strip():
            self._generate()
        else:
            self.toast.show_message("Add media or paste a transcript first.", "warn")

    def _start_transcription(self):
        if getattr(self, "_worker", None) and self._worker.isRunning():
            return                                    # already transcribing — ignore re-click
        self._sync_queue_from_list()
        if not self._media_queue:
            self.toast.show_message("No media files queued.", "warn")
            return
        self._set_transcribe_enabled(False)           # prevent a second, interleaved run
        # First-run reassurance: the Whisper model downloads once on first use.
        from ..core import transcription as _T
        size = self.ctx.settings.get("whisper_model", "base")
        if not _T.model_cached(size):
            approx = _T.APPROX_SIZE.get(size, "a few hundred MB")
            self.toast.show_message(
                f"First run: downloading the Whisper ‘{size}’ model (~{approx}). This happens "
                "once and can take a minute — you can Cancel any time.", "info", 9000)
        self.cancel_btn.setVisible(True)              # keep Cancel prominent during the wait
        self._media_total = len(self._media_queue)
        self._media_done = 0
        self._busy(True, "Starting transcription…")
        self._transcribe_next()

    def _transcribe_next(self):
        if not self._media_queue:
            self._set_transcribe_enabled(False)
            if self._auto_generate:                   # one-click flow → chain into generation
                self._auto_generate = False
                self._generate()
            else:
                self.cancel_btn.setVisible(False)
                self._busy(False)
                self.toast.show_message("Transcription complete.", "success")
            return
        path = self._media_queue.pop(0)
        engine = self.ctx.transcription_engine()
        self._media_done = getattr(self, "_media_done", 0) + 1
        total = getattr(self, "_media_total", 1)
        prefix = f"File {self._media_done} of {total} · " if total > 1 else ""
        self.status.setText(f"{prefix}Transcribing {Path(path).name} …")
        self._worker = TranscribeWorker(
            engine, path, self.ctx.settings.get("language", "auto"),
            diarize=self.ctx.settings.get("diarize", False))
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_transcribed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_transcribed(self, result):
        cur = self.transcript.toPlainText()
        sep = "\n\n" if cur.strip() else ""
        text = result.as_speaker_text() if getattr(result, "speakers", 0) else result.as_plain()
        self.transcript.setPlainText(cur + sep + text)
        self._transcribe_next()

    # -- generation ---------------------------------------------------------
    def _generate(self):
        transcript = self.transcript.toPlainText().strip()
        if not transcript:
            self._busy(False)
            self.toast.show_message("Add or paste a transcript first.", "warn")
            return
        status = self.ctx.ai_status()
        cloud = self.ctx.provider() == "cloud"
        if not status.running:
            self._busy(False)                         # clear a chained-transcription busy state
            if cloud:
                QMessageBox.warning(self, "MICO360 Cloud unavailable",
                                    (status.error or "Could not reach the MICO360 Connect server.")
                                    + "\n\nYou can switch to Local (Ollama) in Settings → AI mode.")
            else:
                QMessageBox.warning(self, "Ollama not running",
                                    "Could not reach the local Ollama server.\n\n"
                                    "Start it with:  ollama serve\n"
                                    "and pull a model, e.g.:  ollama pull llama3.1")
            return
        model = self.model_box.currentText()
        if not model or model.startswith("⚠"):
            self._busy(False)
            if cloud:
                self.toast.show_message("No MICO360 Cloud model is available right now.", "warn")
            elif status.running and not status.models:
                QMessageBox.warning(self, "No AI model installed",
                                    "Ollama is running but has no models.\n\n"
                                    "Go to Settings → “Install Required Model” to download one "
                                    "(e.g. Llama 3.1), then try again.")
            else:
                self.toast.show_message("Select a valid model.", "warn")
            return
        self.ctx.set_ai_model(model)
        style = self.style_box.currentText()
        self.ctx.settings.set("output_style", style)
        template = (self.prompt_edit.toPlainText() if self.toggle_prompt.isChecked()
                    else (self._prompts[self.prompt_box.currentIndex()].text
                          if self._prompts else self.prompt_edit.toPlainText()))

        gen = self.ctx.generator(); gen.model = model
        # prepend known meeting details (from .ics or the fields) so the header
        # isn't left as 'Not specified'
        transcript_in = self._meeting_preamble() + transcript
        self._busy(True, "Generating minutes…")
        self.cancel_btn.setVisible(True); self.generate_btn.setEnabled(False)
        self._worker = GenerateWorker(
            gen, transcript_in, template, style,
            remove_fillers=self.ctx.settings.get("remove_fillers", True),
            chunk_chars=int(self.ctx.settings.get("chunk_chars", 6000)),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_generated)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_generated(self, md: str):
        self.minutes.setPlainText(md)
        self._busy(False)
        self.cancel_btn.setVisible(False); self.generate_btn.setEnabled(True)
        self._set_transcribe_enabled(bool(self._media_queue))   # reflect leftover queue
        # Auto-fill an editable title (from the Setup field or the minutes) so the
        # History record is findable — the user can rename it on this step.
        if not self.meeting_title.text().strip():
            self.meeting_title.setText(self.meet_title.text().strip() or self._guess_title())
        # "Create meeting" saves the result and lands on the final Minutes step.
        self._save_history(silent=True)
        self._reached = self.STEP_MINUTES
        self._goto_step(self.STEP_MINUTES)
        self.toast.show_message("Minutes generated and saved to History.", "success")

    def _cancel(self):
        if self._worker:
            self._worker.cancel()
        self.status.setText("Cancelling…")

    # -- output actions -----------------------------------------------------
    def _on_minutes_tab(self, idx: int):
        # render markdown -> HTML when the Preview tab is shown
        if self.minutes_tabs.tabText(idx) == "Preview":
            from ..export.html_export import render_fragment
            md = self.minutes.toPlainText()
            self.minutes_preview.setHtml(render_fragment(md) if md.strip()
                                         else "<p style='color:#888'>Nothing to preview yet.</p>")

    def _copy(self):
        txt = self.minutes.toPlainText()
        if not txt.strip():
            return
        # put both plain text AND rich HTML on the clipboard (paste keeps formatting)
        from PySide6.QtCore import QMimeData
        from ..export.html_export import render_fragment
        mime = QMimeData()
        mime.setText(txt)
        mime.setHtml(render_fragment(txt))
        QGuiApplication.clipboard().setMimeData(mime)
        self.toast.show_message("Copied (with formatting) to clipboard.", "success")

    def _content_sig(self) -> str:
        """Change signature covering everything that gets saved (title included,
        so renaming a meeting is autosaved too)."""
        return "\x00".join((self.transcript.toPlainText(), self.minutes.toPlainText(),
                            self.meeting_title.text()))

    def _save_history(self, silent: bool = False) -> int | None:
        if not self.minutes.toPlainText().strip() and not self.transcript.toPlainText().strip():
            if not silent:
                self.toast.show_message("Nothing to save yet.", "warn")
            return None
        title = self.meeting_title.text().strip() or self._guess_title()
        m = Meeting(
            id=self._current_id or 0, title=title, created_at=0, updated_at=0,
            source_type="mixed", style=self.style_box.currentText(),
            model=self.model_box.currentText(),
            profile_id=self.ctx.settings.get("active_profile", ""),
            transcript=self.transcript.toPlainText(),
            minutes=self.minutes.toPlainText(),
        )
        self._current_id = self.ctx.history.save(m)
        import time
        self._autosave_sig = self._content_sig()
        self._last_saved_at = time.time()
        self._update_save_indicator()
        if not silent:
            self.toast.show_message("Saved to history.", "success")
        return self._current_id

    # -- save-state indicator ----------------------------------------------
    def _is_dirty(self) -> bool:
        content = self.transcript.toPlainText() + self.minutes.toPlainText()
        return bool(content.strip()) and self._content_sig() != getattr(self, "_autosave_sig", "")

    def _update_save_indicator(self):
        if not hasattr(self, "save_status"):
            return
        if self._is_dirty():
            self.save_status.setText("● Unsaved changes")
            self.save_status.setStyleSheet("color:#F59E0B;")          # amber
        elif getattr(self, "_last_saved_at", None):
            import time
            ago = time.time() - self._last_saved_at
            when = ("just now" if ago < 45 else f"{int(ago // 60)}m ago" if ago < 3600
                    else "over an hour ago")
            self.save_status.setText(f"✓ Saved · {when}")
            self.save_status.setStyleSheet("color:#22C55E;")          # green
        else:
            self.save_status.setText("")

    # -- autosave -----------------------------------------------------------
    def _setup_autosave(self):
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(20000)      # every 20s
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

    def _autosave(self):
        self._update_save_indicator()                # keep the relative time fresh
        content = self.transcript.toPlainText() + self.minutes.toPlainText()
        if not content.strip():
            # Form emptied. For a brand-new meeting, detach from the saved record
            # so the NEXT meeting is stored separately (never overwriting the
            # previous one). For a meeting opened from History, keep the link so
            # clearing and retyping still edits that same record rather than
            # forking a duplicate.
            if not self._loaded_from_history:
                self._current_id = None
            self._autosave_sig = ""
            return
        if self._content_sig() == self._autosave_sig:   # nothing changed (title included)
            return
        self._save_history(silent=True)              # updates _autosave_sig on success

    def _new_meeting(self):
        """Clear the form and start a fresh, unlinked meeting."""
        if (self.transcript.toPlainText().strip() or self.minutes.toPlainText().strip()):
            self._autosave()                          # keep what's there
        self._current_id = None
        self._loaded_from_history = False
        self._autosave_sig = ""
        self.transcript.clear()
        self.minutes.clear()
        self.meeting_title.clear()
        self._clear_files()
        self.meet_title.clear(); self.meet_date.clear(); self.meet_attendees.clear()
        self._reached = 0
        self._goto_step(self.STEP_SOURCE)
        self.toast.show_message("Started a new meeting — the previous one is saved in History.",
                                "success", 4000)

    def _guess_title(self) -> str:
        for line in self.minutes.toPlainText().splitlines():
            if "Meeting Title:" in line:
                t = line.split("Meeting Title:")[-1].replace("*", "").strip()
                if t and t.lower() != "not specified":
                    return t[:80]
        return "Meeting " + (Path(self._media_queue[0]).stem if self._media_queue else "minutes")

    def _export(self):
        from ..export import service
        md = self.minutes.toPlainText().strip()
        if not md:
            self.toast.show_message("Generate or write minutes first.", "warn")
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "Export minutes", "Meeting-Minutes", service.FILTERS)
        if not path:
            return
        if "." not in Path(path).name:
            ext = (".docx" if "Word" in selected else ".pdf" if "PDF" in selected
                   else ".md" if "Markdown" in selected else ".html" if "HTML" in selected
                   else ".txt")
            path += ext
        # Run export off the UI thread so the app never shows "not responding".
        from .workers import ExportWorker
        self.export_btn.setEnabled(False)
        self.export_btn.setText("Exporting…")
        self._busy(True, f"Exporting to {Path(path).name}…")
        self._export_worker = ExportWorker(md, path, self.ctx.active_profile())
        self._export_worker.finished_ok.connect(self._on_exported)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_exported(self, out: str):
        self._busy(False)
        self.export_btn.setEnabled(True); self.export_btn.setText("Export…")
        self.toast.show_message(f"Exported to {Path(out).name}", "success", 5000)

    def _on_export_failed(self, msg: str):
        self._busy(False)
        self.export_btn.setEnabled(True); self.export_btn.setText("Export…")
        QMessageBox.critical(self, "Export failed", msg)

    # -- email --------------------------------------------------------------
    def _email_minutes(self):
        from ..core.emailer import SmtpConfig
        md = self.minutes.toPlainText().strip()
        if not md:
            self.toast.show_message("Generate or write minutes first.", "warn")
            return
        cfg = SmtpConfig.from_settings(self.ctx.settings)
        if not cfg.configured:
            QMessageBox.information(self, "Email not set up",
                                    "Add your email (SMTP) details in Settings → Email first.")
            return
        from .dialogs import EmailComposeDialog
        title = self._guess_title()
        prefill_to = ""  # attendee names aren't emails; leave blank
        body = ("Hi,\n\nPlease find the minutes for our meeting below.\n\n"
                + md.replace("**", "") + "\n\n— Sent from MICO360 Meetings")
        dlg = EmailComposeDialog(f"Meeting Minutes — {title}", body, prefill_to, self)
        if not dlg.exec():
            return
        v = dlg.values()
        # export chosen attachment formats to temp
        from ..config import TMP_DIR
        from ..export import service
        attachments = []
        try:
            for ext in v["formats"]:
                p = TMP_DIR / f"Meeting-Minutes{ext}"
                service.export(md, str(p), self.ctx.active_profile())
                attachments.append(str(p))
        except Exception as exc:
            QMessageBox.critical(self, "Attachment failed", str(exc)); return

        from .workers import EmailWorker
        from ..export.html_export import render_document
        self.email_btn.setEnabled(False); self.email_btn.setText("Sending…")
        self._email_worker = EmailWorker(
            cfg, v["to"], v["subject"], v["body"],
            html=render_document(md, self.ctx.active_profile()),
            attachments=attachments, cc=v["cc"])
        self._email_worker.finished_ok.connect(lambda: self._on_email_done(True))
        self._email_worker.failed.connect(lambda m: self._on_email_done(False, m))
        self._email_worker.start()

    def _on_email_done(self, ok: bool, msg: str = ""):
        self.email_btn.setEnabled(True); self.email_btn.setText("📧 Email…")
        if ok:
            self.toast.show_message("Minutes emailed.", "success", 5000)
        else:
            QMessageBox.critical(self, "Email failed",
                                 f"{msg}\n\nCheck your SMTP settings and that the sender "
                                 "address is a validated Mailjet sender.")

    # -- shared -------------------------------------------------------------
    def _on_progress(self, frac: float, msg: str):
        self.progress.setValue(int(frac * 100))
        self.status.setText(msg)

    def _on_failed(self, msg: str):
        self._busy(False)
        self._auto_generate = False               # don't chain generation after a failure
        self.cancel_btn.setVisible(False); self.generate_btn.setEnabled(True)
        # allow retrying the remaining transcription queue after a failure
        self._set_transcribe_enabled(bool(self._media_queue))
        if msg and "cancel" not in msg.lower():
            QMessageBox.critical(self, "Error", msg)
        self.toast.show_message(msg or "Failed.", "error", 5000)

    def _busy(self, on: bool, msg: str = ""):
        self.progress.setVisible(on)
        if on:
            self.progress.setValue(0)
            self.status.setText(msg)
        else:
            QTimer.singleShot(1200, lambda: (self.progress.setVisible(False), self.status.setText("")))

    def load_meeting(self, m: Meeting):
        self._current_id = m.id
        self._loaded_from_history = True
        self.meeting_title.setText(m.title)
        self.transcript.setPlainText(m.transcript)
        self.minutes.setPlainText(m.minutes)
        # seed the autosave signature so opening a meeting doesn't trigger an
        # immediate redundant re-save (which would bump its "Updated" time)
        self._autosave_sig = self._content_sig()
        self._last_saved_at = m.updated_at or None
        if m.style:
            self.style_box.setCurrentText(m.style)
        # opening a saved meeting unlocks the whole wizard; land on Minutes if it
        # already has them, otherwise on the Transcript step to continue.
        self._reached = len(self.STEP_TITLES) - 1
        self._goto_step(self.STEP_MINUTES if m.minutes else self.STEP_TRANSCRIPT)
        self.toast.show_message(f"Loaded: {m.title}", "info")


# ===========================================================================
# History
# ===========================================================================
class HistoryPage(QWidget):
    def __init__(self, ctx: AppContext, toast, on_open):
        super().__init__()
        self.ctx = ctx; self.toast = toast; self.on_open = on_open
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 24); v.setSpacing(12)
        title = QLabel("Meeting History"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Search and reopen past meetings — everything stays on this computer."))

        row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search title, transcript or minutes…")
        self.search.textChanged.connect(self.reload)
        tip(self.search, "Filter as you type — matches meeting titles, transcripts and minutes text")
        refresh = QPushButton("Refresh"); refresh.clicked.connect(self.reload)
        tip(refresh, "Reload the list, including meetings autosaved in the background")
        row.addWidget(self.search); row.addWidget(refresh)
        v.addLayout(row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Title", "Style", "Model", "Updated"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._open_selected)
        tip(self.table, "Your saved meetings — double-click a row to reopen it in New Meeting")
        v.addWidget(self.table, 1)
        self.empty = EmptyState("🕑", "No meetings yet",
                                "Meetings you save appear here. Start one in New Meeting to "
                                "record, transcribe and generate minutes.")
        self.empty.setVisible(False)
        v.addWidget(self.empty, 1)

        actions = QHBoxLayout()
        open_btn = QPushButton("Open"); open_btn.setObjectName("Primary"); open_btn.clicked.connect(self._open_selected)
        tip(open_btn, "Load the selected meeting's transcript and minutes for editing or re-export")
        del_btn = QPushButton("Delete"); del_btn.setObjectName("Danger"); del_btn.clicked.connect(self._delete_selected)
        tip(del_btn, "Permanently delete the selected meeting from History — this cannot be undone")
        actions.addStretch(); actions.addWidget(del_btn); actions.addWidget(open_btn)
        v.addLayout(actions)
        self._rows: list[Meeting] = []

    def reload(self):
        import time
        self._rows = self.ctx.history.list(self.search.text())
        self.table.setRowCount(len(self._rows))
        for i, m in enumerate(self._rows):
            updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.updated_at))
            for j, val in enumerate((m.title, m.style, m.model, updated)):
                self.table.setItem(i, j, QTableWidgetItem(val))
        # empty state: distinguish "no meetings" from "no search matches"
        q = self.search.text().strip()
        if self._rows:
            self.table.setVisible(True); self.empty.setVisible(False)
        else:
            if q:
                self.empty.set(f"No meetings match “{q}”",
                               "Try a different search, or clear the box to see everything.", "🔍")
            else:
                self.empty.set("No meetings yet",
                               "Meetings you save appear here. Start one in New Meeting to "
                               "record, transcribe and generate minutes.", "🕑")
            self.table.setVisible(False); self.empty.setVisible(True)

    def _selected_meeting(self) -> Meeting | None:
        r = self.table.currentRow()
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def _open_selected(self):
        m = self._selected_meeting()
        if m:
            self.on_open(m)

    def _delete_selected(self):
        m = self._selected_meeting()
        if not m:
            return
        if QMessageBox.question(self, "Delete", f"Delete '{m.title}'?") == QMessageBox.Yes:
            self.ctx.history.delete(m.id)
            self.ctx.action_items.drop_meeting(m.id)   # purge orphaned status overrides
            self.reload()
            self.toast.show_message("Deleted.", "success")


# ===========================================================================
# Company Profiles
# ===========================================================================
class ProfilesPage(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx = ctx; self.toast = toast
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 24); v.setSpacing(12)
        title = QLabel("Company Profiles"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Branding used on exported minutes: logo, footer, page numbers and layout."))

        bar = QHBoxLayout()
        new = QPushButton("New profile"); new.setObjectName("Primary"); new.clicked.connect(self._new)
        tip(new, "Create a company profile: name, contact details, logo, footer and page numbering")
        edit = QPushButton("Edit"); edit.clicked.connect(self._edit)
        tip(edit, "Edit the selected profile with a live page-layout preview (or double-click it)")
        dele = QPushButton("Delete"); dele.setObjectName("Danger"); dele.clicked.connect(self._delete)
        tip(dele, "Permanently delete the selected profile — exports made with it are unaffected")
        imp = QPushButton("Import…"); imp.clicked.connect(self._import)
        tip(imp, "Import profiles from a JSON, CSV or Excel file")
        exp = QPushButton("Export…"); exp.clicked.connect(self._export)
        tip(exp, "Export all profiles to JSON, CSV or Excel — useful for backup or another PC")
        use = QPushButton("Set as active"); use.clicked.connect(self._set_active)
        tip(use, "Use the selected profile's branding on all exported and emailed minutes")
        for b in (new, edit, dele, imp, exp):
            bar.addWidget(b)
        bar.addStretch(); bar.addWidget(use)
        v.addLayout(bar)

        self.list = QListWidget(); self.list.itemDoubleClicked.connect(lambda *_: self._edit())
        v.addWidget(self.list, 1)
        self.active_lbl = QLabel(""); self.active_lbl.setObjectName("Hint")
        v.addWidget(self.active_lbl)
        self.reload()

    def reload(self):
        self.list.clear()
        self._profiles = self.ctx.profiles.list()
        active = self.ctx.settings.get("active_profile", "")
        for p in self._profiles:
            mark = "  ⭐ active" if p.id == active else ""
            it = QListWidgetItem(f"{p.name}{mark}")
            it.setData(Qt.UserRole, p.id)
            self.list.addItem(it)
        act = self.ctx.active_profile()
        self.active_lbl.setText(f"Active profile: {act.name}" if act else "No active profile (plain export).")

    def _current_id(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _new(self):
        dlg = ProfileDialog(self.ctx.profiles, None, self)
        if dlg.exec():
            self.reload(); self.toast.show_message("Profile created.", "success")

    def _edit(self):
        pid = self._current_id()
        if not pid:
            return
        prof = self.ctx.profiles.get(pid)
        dlg = ProfileDialog(self.ctx.profiles, prof, self)
        if dlg.exec():
            self.reload(); self.toast.show_message("Profile saved.", "success")

    def _delete(self):
        pid = self._current_id()
        if pid and QMessageBox.question(self, "Delete", "Delete this profile?") == QMessageBox.Yes:
            self.ctx.profiles.delete(pid)
            if self.ctx.settings.get("active_profile") == pid:
                self.ctx.settings.set("active_profile", "")
            self.reload()

    def _set_active(self):
        pid = self._current_id()
        if pid:
            self.ctx.settings.set("active_profile", pid)
            self.reload(); self.toast.show_message("Active profile set.", "success")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import profiles", "", "Profiles (*.json *.csv *.xlsx)")
        if path:
            try:
                n = len(self.ctx.profiles.import_file(path))
                self.reload(); self.toast.show_message(f"Imported {n} profile(s).", "success")
            except Exception as exc:
                QMessageBox.critical(self, "Import failed", str(exc))

    def _export(self):
        if not self._profiles:
            self.toast.show_message("No profiles to export.", "warn"); return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export profiles", "company-profiles.json",
            "JSON (*.json);;CSV (*.csv);;Excel (*.xlsx)")
        if path:
            try:
                self.ctx.profiles.export_file(self._profiles, path)
                self.toast.show_message("Profiles exported.", "success")
            except Exception as exc:
                QMessageBox.critical(self, "Export failed", str(exc))


# ===========================================================================
# Prompt Library
# ===========================================================================
class PromptsPage(QWidget):
    def __init__(self, ctx: AppContext, toast, on_change=None):
        super().__init__()
        self.ctx = ctx; self.toast = toast; self.on_change = on_change
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 24); v.setSpacing(12)
        title = QLabel("Prompt Library"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Create and manage reusable prompts for minutes generation."))

        split = QSplitter(Qt.Horizontal)
        left = QWidget(); ll = QVBoxLayout(left)
        self.list = QListWidget(); self.list.currentRowChanged.connect(self._show)
        tip(self.list, "Prompts grouped by meeting category — select one to preview its full text")
        ll.addWidget(self.list)
        btns = QHBoxLayout()
        add = QPushButton("Add"); add.setObjectName("Primary"); add.clicked.connect(self._add)
        tip(add, "Create a custom prompt — include [TRANSCRIPT_HERE] where the meeting text goes")
        edit = QPushButton("Edit"); edit.clicked.connect(self._edit)
        tip(edit, "Edit the selected prompt's name, category and text (built-ins can be edited too)")
        dele = QPushButton("Delete"); dele.setObjectName("Danger"); dele.clicked.connect(self._delete)
        tip(dele, "Permanently delete the selected prompt from the library")
        for b in (add, edit, dele):
            btns.addWidget(b)
        ll.addLayout(btns)

        right = QWidget(); rl = QVBoxLayout(right)
        rl.addWidget(section_title("Preview"))
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        rl.addWidget(self.preview)
        split.addWidget(left); split.addWidget(right)
        split.setSizes([320, 520])
        v.addWidget(split, 1)
        self.reload()

    def reload(self):
        self.list.clear()
        self._prompts = self.ctx.prompts.list()
        # group by category with non-selectable header rows
        last_cat = None
        self._row_map: list[int] = []   # list-row -> prompt index (or -1 for header)
        for i, p in enumerate(self._prompts):
            if p.category != last_cat:
                last_cat = p.category
                hdr = QListWidgetItem(f"— {p.category} —")
                hdr.setFlags(Qt.NoItemFlags)
                self.list.addItem(hdr); self._row_map.append(-1)
            tag = "  ·  built-in" if p.builtin else ""
            self.list.addItem(f"   {p.name}{tag}")
            self._row_map.append(i)
        # select first real prompt
        for row, idx in enumerate(self._row_map):
            if idx >= 0:
                self.list.setCurrentRow(row); break
        if self.on_change:
            self.on_change()

    def _current(self) -> SavedPrompt | None:
        r = self.list.currentRow()
        if 0 <= r < len(self._row_map) and self._row_map[r] >= 0:
            return self._prompts[self._row_map[r]]
        return None

    def _show(self, _row):
        p = self._current()
        self.preview.setPlainText(p.text if p else "")

    def _add(self):
        dlg = PromptDialog(parent=self)
        if dlg.exec():
            name, text, category = dlg.values()
            self.ctx.prompts.add(name, text, category=category)
            self.reload(); self.toast.show_message("Prompt added.", "success")

    def _edit(self):
        p = self._current()
        if not p:
            return
        dlg = PromptDialog(p.name, p.text, p.category, self)
        if dlg.exec():
            name, text, category = dlg.values()
            self.ctx.prompts.update(p.id, name, text, category=category)
            self.reload(); self.toast.show_message("Prompt updated.", "success")

    def _delete(self):
        p = self._current()
        if p and QMessageBox.question(self, "Delete", f"Delete '{p.name}'?") == QMessageBox.Yes:
            self.ctx.prompts.delete(p.id)
            self.reload(); self.toast.show_message("Prompt deleted.", "success")


# ===========================================================================
# Settings
# ===========================================================================
class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, toast, on_theme_change, on_models_change):
        super().__init__()
        self.ctx = ctx; self.toast = toast
        self.on_theme_change = on_theme_change; self.on_models_change = on_models_change
        outer = QVBoxLayout(self); outer.setContentsMargins(24, 20, 24, 24); outer.setSpacing(12)

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
        form.setContentsMargins(24, 18, 24, 20); form.setSpacing(12)
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
