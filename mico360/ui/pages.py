"""New Meeting wizard page (other pages: history_page, profiles_page, prompts_page, settings_page)."""
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
        v.setContentsMargins(*M.PAGE_MARGINS)
        v.setSpacing(M.PAGE_GAP)

        title = QLabel("New Meeting"); title.setObjectName("PageTitle")
        v.addWidget(title)
        self.page_sub = subtitle("Follow the steps to turn a recording, file or transcript into minutes.")
        v.addWidget(self.page_sub)
        # Page context: shows when you're editing a meeting opened from History.
        self.context_lbl = QLabel(""); self.context_lbl.setObjectName("ContextBadge")
        self.context_lbl.setVisible(False)
        v.addWidget(self.context_lbl)

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

        # Operation status: named stages, progress, message, inline errors.
        v.addWidget(self._build_gen_status())

        # Back / Next / Create footer.
        v.addLayout(self._build_footer())
        v.addStretch()

        self._reached = 0
        self._goto_step(self.STEP_SOURCE)
        outer.addWidget(_scroll(content))

    # -- wizard shell -------------------------------------------------------
    def _build_stepper(self) -> QWidget:
        self.stepper = StepIndicator(self.STEP_TITLES)
        self.stepper.stepClicked.connect(self._goto_step)
        return self.stepper

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
        self.stepper.set_state(i, self._reached)
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
        row = QHBoxLayout(w); row.setContentsMargins(M.MD, M.MD, M.MD, M.MD); row.setSpacing(M.SM)
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
        lay = QVBoxLayout(panel); lay.setContentsMargins(*M.CARD_MARGINS); lay.setSpacing(M.CARD_GAP)
        hdr = QLabel(title_text); hdr.setObjectName("StepHeader")
        lay.addWidget(hdr)
        return panel, lay

    def _step_review(self) -> QWidget:
        card, lay = self._card("Review & create")
        lay.addWidget(hint("Check everything below, then click “Create meeting” to transcribe "
                           "(if needed) and generate the minutes."))
        # Grouped for scannability. (display label, storage key) — keys are kept
        # stable because _refresh_review and tests look them up by key.
        self._review_vals: dict[str, QLabel] = {}
        groups = [
            ("Meeting", [("Title", "Title"), ("Date / time", "Date / time"),
                         ("Attendees", "Attendees")]),
            ("Source", [("Source", "Source"), ("Transcript", "Transcript")]),
            ("AI settings", [("Mode", "AI mode"), ("Model", "Model"),
                             ("Style", "Style"), ("Prompt", "Prompt")]),
        ]
        for gi, (gname, rows) in enumerate(groups):
            sub = QLabel(gname.upper()); sub.setObjectName("ReviewGroup")
            if gi:
                sub.setContentsMargins(0, M.SM, 0, 0)
            lay.addWidget(sub)
            grid = QGridLayout(); grid.setHorizontalSpacing(M.LG); grid.setVerticalSpacing(M.SM)
            for r, (disp, key) in enumerate(rows):
                k = QLabel(disp); k.setObjectName("Hint")
                val = QLabel("—"); val.setObjectName("ReviewVal"); val.setWordWrap(True)
                grid.addWidget(k, r, 0, Qt.AlignTop); grid.addWidget(val, r, 1)
                self._review_vals[key] = val
            grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(0, 130)
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
        upl.setContentsMargins(0, 0, 0, 0); upl.setSpacing(8)
        self.drop = DropArea(
            accept_exts=UPLOAD_EXTS,
            caption="Drag & drop audio, video or documents",
            sub="MP3 · WAV · M4A · MP4 · MOV · MKV · PDF · DOCX · TXT · images — multiple files supported",
        )
        self.drop.fileChosen.connect(self._add_file)
        self.files_list = QListWidget(); self.files_list.setMaximumHeight(92)
        self.files_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.files_list.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.files_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files_list.setDragDropMode(QAbstractItemView.InternalMove)  # drag to reorder
        self.files_list.setToolTip("Drag to reorder · select + Remove to delete")

        # A single action row: add on the left, queue controls + the primary
        # Transcribe on the right — so everything fits without scrolling.
        add_btn = QPushButton("➕ Add"); add_btn.clicked.connect(self.drop._browse)
        tip(add_btn, "Browse for audio, video or document files to add to the queue")
        self.remove_btn = QPushButton("Remove"); self.remove_btn.clicked.connect(self._remove_selected)
        tip(self.remove_btn, "Remove the highlighted file(s) from the queue — the files on disk are not deleted")
        self.clear_btn = QPushButton("Clear"); self.clear_btn.clicked.connect(self._clear_files)
        tip(self.clear_btn, "Empty the whole queue (files on disk are not deleted)")
        # Transcribe the queued media to text; then continue through the wizard
        # (review the transcript → Setup → Review → "Create meeting" generates).
        self.transcribe_btn = QPushButton("✨  Transcribe")
        self.transcribe_btn.setObjectName("Primary")
        self.transcribe_btn.clicked.connect(self._start_transcription)
        self.transcribe_btn.setEnabled(False)
        tip(self.transcribe_btn, "Transcribe the queued media to text, so you can review and edit it "
                                 "before generating minutes. Offline Whisper — first run downloads "
                                 "the model (internet needed once). Then click Next to continue")
        list_bar = QHBoxLayout()
        list_bar.addWidget(add_btn)
        list_bar.addStretch()
        list_bar.addWidget(self.remove_btn)
        list_bar.addWidget(self.clear_btn)
        list_bar.addSpacing(8)
        list_bar.addWidget(self.transcribe_btn)
        upl.addWidget(self.drop)
        upl.addWidget(hint("Documents are read instantly into the transcript; audio/video are transcribed with Whisper. "
                           "Recordings also appear here."))
        upl.addWidget(self.files_list)
        upl.addLayout(list_bar)
        self.source_tabs.addTab(up, "Upload files")

        # Record tab — full audio/screen/camera recorder with live details
        rec = QWidget(); rl = QVBoxLayout(rec)
        rl.setContentsMargins(0, 0, 0, 0)
        self.recorder_panel = RecordingPanel(self.toast, self.ctx)
        self.recorder_panel.recordingReady.connect(self._on_recording_ready)
        self.recorder_panel.liveTranscriptReady.connect(self._on_live_transcript)
        rl.addWidget(self.recorder_panel)
        self.source_tabs.addTab(rec, "Record")

        lay.addWidget(self.source_tabs)
        return card

    # -- queue management ---------------------------------------------------
    def _set_transcribe_enabled(self, on: bool):
        """Enable/disable the Transcribe action (has media queued)."""
        self.transcribe_btn.setEnabled(on)

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
        if getattr(self, "_auto_generate_pending", False):     # auto-record → no clicks
            self._auto_generate_pending = False
            self._create_meeting()
            return
        self.toast.show_message("Recording added — click ‘Transcribe’ to continue.",
                                "success", 5000)

    def _on_live_transcript(self, text: str):
        """A live-transcribed recording -> the transcript is already written; drop
        it into the editor and jump to the Transcript step so the user can review."""
        cur = self.transcript.toPlainText().strip()
        self.transcript.setPlainText((cur + "\n\n" + text).strip() if cur else text)
        self._reached = max(self._reached, self.STEP_TRANSCRIPT)
        self._goto_step(self.STEP_TRANSCRIPT)
        if getattr(self, "_auto_generate_pending", False):     # auto-record → straight to minutes
            self._auto_generate_pending = False
            self.toast.show_message("Meeting ended — generating minutes…", "info", 5000)
            self._create_meeting()
            return
        self.toast.show_message("Live transcript captured — review it, then continue. "
                                "(Use the recording for a full re-transcribe if you want more accuracy.)",
                                "success", 7000)

    def _step2_transcript(self) -> QWidget:
        card, lay = self._card("Transcript")
        lay.addWidget(hint("Editable — paste a transcript here, or review the transcription result."))

        # Speaker naming — shown when the transcript has diarised [Speaker N] labels.
        self._speaker_edits: dict[str, QLineEdit] = {}
        self.speaker_panel = QWidget(); self.speaker_panel.setObjectName("SpeakerPanel")
        spv = QVBoxLayout(self.speaker_panel)
        spv.setContentsMargins(M.LG, M.MD, M.LG, M.MD); spv.setSpacing(M.SM)
        spv.addWidget(section_title("Name the speakers"))
        spv.addWidget(hint("Diarisation labelled the voices below. Give each a real name and click "
                           "“Apply names” — they flow into the transcript, minutes and action items."))
        self.speaker_rows = QVBoxLayout(); self.speaker_rows.setSpacing(M.XS)
        spv.addLayout(self.speaker_rows)
        sbtn = QHBoxLayout(); sbtn.addStretch()
        apply_btn = QPushButton("Apply names"); apply_btn.setObjectName("Primary")
        apply_btn.clicked.connect(self._apply_speaker_names)
        tip(apply_btn, "Replace [Speaker N] with the names you entered, throughout the transcript, "
                       "and set the attendees")
        sbtn.addWidget(apply_btn)
        spv.addLayout(sbtn)
        self.speaker_panel.setVisible(False)
        lay.addWidget(self.speaker_panel)

        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("Paste or edit the meeting transcript here…")
        self.transcript.setMinimumHeight(150)
        self.transcript.textChanged.connect(self._update_counts)
        self.transcript.textChanged.connect(self._refresh_speakers)
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

    # -- speaker identification ---------------------------------------------
    _SPEAKER_RE = re.compile(r"\[(Speaker \d+)\]")

    def _detect_speakers(self) -> list[str]:
        seen: list[str] = []
        for m in self._SPEAKER_RE.finditer(self.transcript.toPlainText()):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        return seen

    def _name_roster(self) -> list[str]:
        return list(self.ctx.settings.get("speaker_names", []) or [])

    def _refresh_speakers(self):
        """Rebuild the naming rows when the set of diarised labels changes."""
        labels = self._detect_speakers()
        if list(self._speaker_edits.keys()) == labels:
            return                                   # unchanged — don't disturb typing
        while self.speaker_rows.count():
            it = self.speaker_rows.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._speaker_edits = {}
        completer = QCompleter(self._name_roster(), self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        for lab in labels:
            r = QWidget(); rl = QHBoxLayout(r)
            rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(M.SM)
            badge = QLabel(lab); badge.setObjectName("SpeakerBadge"); badge.setFixedWidth(96)
            edit = QLineEdit(); edit.setPlaceholderText("Real name…")
            edit.setCompleter(completer); edit.returnPressed.connect(self._apply_speaker_names)
            rl.addWidget(badge); rl.addWidget(edit, 1)
            self.speaker_rows.addWidget(r)
            self._speaker_edits[lab] = edit
        self.speaker_panel.setVisible(bool(labels))

    def _apply_speaker_names(self):
        text = self.transcript.toPlainText()
        names, used = [], 0
        for lab, edit in self._speaker_edits.items():
            name = edit.text().strip()
            if not name:
                continue
            text = text.replace(f"[{lab}]", f"[{name}]")
            names.append(name); used += 1
        if not used:
            self.toast.show_message("Enter at least one name to apply.", "warn")
            return
        self.transcript.setPlainText(text)           # triggers _refresh_speakers → panel updates
        # remember names for future autocomplete
        roster = self._name_roster()
        for n in names:
            if n not in roster:
                roster.append(n)
        self.ctx.settings.set("speaker_names", roster[-50:])
        # attendees follow the named speakers (only if the field is empty)
        if not self.meet_attendees.text().strip():
            self.meet_attendees.setText(", ".join(names))
            msg = f"Applied {used} name(s) — attendees set."
        else:
            msg = f"Applied {used} name(s)."
        self.toast.show_message(msg, "success")

    def _step3_generate(self) -> QWidget:
        card, lay = self._card("Meeting details & AI setup")

        # Meeting type preset + calendar pre-fill
        mrow = QHBoxLayout()
        tcol = QVBoxLayout(); tcol.setSpacing(M.XS)
        tl = QLabel("Meeting type"); tl.setObjectName("Hint")
        self.mtype_box = QComboBox(); self.mtype_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.mtype_box.activated.connect(self._apply_meeting_type)
        tip(self.mtype_box, "One-click preset: applies a matching prompt, output style and "
                            "company profile for this kind of meeting")
        tcol.addWidget(tl); tcol.addWidget(self.mtype_box)
        mrow.addLayout(tcol, 1)
        icscol = QVBoxLayout(); icscol.setSpacing(M.XS)
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
            col = QVBoxLayout(); col.setSpacing(M.XS)
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
        self._gen_active = False              # transcription has no generation stages
        self._busy(True, "Starting transcription…")
        self.stage_row.setVisible(False)
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
        self._gen_active = True                        # show generation stages
        self._busy(True, "Generating minutes…")
        self.stage_row.setVisible(True); self._update_gen_stage(0.0)
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
        self._gen_active = False
        self._busy(False)
        self.gen_error.setVisible(False)
        self.stage_row.setVisible(False)
        self.status.setText("✓  Minutes generated and saved to History."); self._set_msg_state("ok")
        self.cancel_btn.setVisible(False); self.generate_btn.setEnabled(True)
        self._set_transcribe_enabled(bool(self._media_queue))   # reflect leftover queue
        # Auto-fill an editable title (from the Setup field or the minutes) so the
        # History record is findable — the user can rename it on this step.
        if not self.meeting_title.text().strip():
            self.meeting_title.setText(self.meet_title.text().strip() or self._guess_title())
        # "Create meeting" saves the result and lands on the final Minutes step,
        # showing the formatted Preview first.
        self._save_history(silent=True)
        self._reached = self.STEP_MINUTES
        self._goto_step(self.STEP_MINUTES)
        self._show_minutes_preview()
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

    def _show_minutes_preview(self):
        """Open the formatted Preview tab (so readers see nicely formatted minutes
        first, not raw Markdown) and make sure it's rendered."""
        self.minutes_tabs.setCurrentIndex(1)      # 0 = Edit, 1 = Preview
        self._on_minutes_tab(1)

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
        self.context_lbl.setVisible(False)            # back to "new" context
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
    # -- generation status panel -------------------------------------------
    def _build_gen_status(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(M.SM)
        # named stages: Prepare → Analyze → Compose
        self._gen_stage_names = ["Prepare", "Analyze", "Compose"]
        self._gen_stages: list[QLabel] = []
        srow = QHBoxLayout(); srow.setSpacing(M.SM)
        for i, nm in enumerate(self._gen_stage_names):
            if i:
                sep = QLabel("→"); sep.setObjectName("GenSep"); srow.addWidget(sep)
            lbl = QLabel(nm); lbl.setObjectName("GenStage")
            self._gen_stages.append(lbl); srow.addWidget(lbl)
        srow.addStretch()
        self.stage_row = QWidget(); self.stage_row.setLayout(srow); self.stage_row.setVisible(False)
        lay.addWidget(self.stage_row)
        # progress bar + percentage
        prow = QHBoxLayout(); prow.setSpacing(M.SM)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        self.progress_pct = QLabel(""); self.progress_pct.setObjectName("Hint")
        self.progress_pct.setFixedWidth(40); self.progress_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prow.addWidget(self.progress, 1); prow.addWidget(self.progress_pct)
        self.progress_row = QWidget(); self.progress_row.setLayout(prow); self.progress_row.setVisible(False)
        lay.addWidget(self.progress_row)
        # message line (turns green on success, styled on state)
        self.status = QLabel(""); self.status.setObjectName("GenMsg"); self.status.setWordWrap(True)
        lay.addWidget(self.status)
        # inline error card with a Retry (+ optional contextual action)
        self.gen_error = self._build_error_card()
        lay.addWidget(self.gen_error)
        return w

    def _build_error_card(self) -> QWidget:
        w = QWidget(); w.setObjectName("ErrorCard"); w.setVisible(False)
        row = QHBoxLayout(w); row.setContentsMargins(M.LG, M.MD, M.MD, M.MD); row.setSpacing(M.SM)
        icon = QLabel("⚠"); icon.setObjectName("ErrorIcon")
        self.gen_error_text = QLabel(""); self.gen_error_text.setObjectName("ErrorText")
        self.gen_error_text.setWordWrap(True)
        self._gen_err_action = None
        self.gen_err_action_btn = QPushButton(""); self.gen_err_action_btn.setObjectName("Ghost")
        self.gen_err_action_btn.setVisible(False)
        self.gen_err_action_btn.clicked.connect(lambda: self._gen_err_action and self._gen_err_action())
        self.gen_retry_btn = QPushButton("↻  Retry"); self.gen_retry_btn.setObjectName("Primary")
        self.gen_retry_btn.clicked.connect(self._retry_generation)
        tip(self.gen_retry_btn, "Try generating the minutes again")
        row.addWidget(icon); row.addWidget(self.gen_error_text, 1)
        row.addWidget(self.gen_err_action_btn); row.addWidget(self.gen_retry_btn)
        return w

    def _set_msg_state(self, state: str):
        self.status.setProperty("state", state or "")
        self.status.style().unpolish(self.status); self.status.style().polish(self.status)

    def _update_gen_stage(self, frac: float):
        # map the pipeline's fraction onto the three named stages
        idx = 0 if frac < 0.12 else (1 if frac < 0.85 else 2)
        for i, lbl in enumerate(self._gen_stages):
            st = "done" if i < idx else ("active" if i == idx else "todo")
            lbl.setProperty("state", st)
            lbl.style().unpolish(lbl); lbl.style().polish(lbl)

    def _retry_generation(self):
        self.gen_error.setVisible(False)
        self._create_meeting()

    def _on_progress(self, frac: float, msg: str):
        self.progress_row.setVisible(True)
        pct = int(frac * 100)
        self.progress.setValue(pct); self.progress_pct.setText(f"{pct}%")
        self.status.setText(msg); self._set_msg_state("")
        if getattr(self, "_gen_active", False):       # stages apply to generation only
            self.stage_row.setVisible(True); self._update_gen_stage(frac)

    def _on_failed(self, msg: str):
        self._busy(False)
        self._auto_generate = False               # don't chain generation after a failure
        self.cancel_btn.setVisible(False); self.generate_btn.setEnabled(True)
        self._set_transcribe_enabled(bool(self._media_queue))
        self.stage_row.setVisible(False); self.progress_row.setVisible(False)
        if not msg or "cancel" in msg.lower():
            self.status.setText("Cancelled — your transcript is kept."); self._set_msg_state("")
            return
        # Clear, inline error with a Retry (no blocking dialog).
        friendly = self._explain_ai_error(msg)
        short = friendly[2] if friendly else msg
        self.status.setText(""); self._set_msg_state("")
        self.gen_error_text.setText(short)
        if friendly and self.on_open_settings:        # AI-model errors → offer Settings
            self.gen_err_action_btn.setText("Open Settings"); self.gen_err_action_btn.setVisible(True)
            self._gen_err_action = self.on_open_settings
        else:
            self.gen_err_action_btn.setVisible(False); self._gen_err_action = None
        self.gen_error.setVisible(True)
        self.toast.show_message(short, "error", 5000)

    def _explain_ai_error(self, msg: str):
        """Turn a raw Ollama/model failure into a clear, actionable message.
        Returns (title, body, short) or None to fall back to the raw error."""
        low = msg.lower()
        model = self.model_box.currentText().strip()
        if "unknown model architecture" in low or "error loading model" in low:
            body = (f"Ollama couldn't load the model “{model}”.\n\n"
                    "This model can't write minutes — it's most likely a vision or "
                    "embedding model, or your Ollama version is too old to run it.\n\n"
                    "What to do:\n"
                    "•  In Step 3 (Setup), pick a text model such as “llama3.1”.\n"
                    "•  If you don't have one, open Settings → Install Required Model and "
                    "download Llama 3.1.\n"
                    "•  Or update Ollama from ollama.com/download, then: ollama pull llama3.1\n\n"
                    f"Details: {msg}")
            return ("Incompatible AI model", body,
                    "That model can't generate minutes — pick a text model like llama3.1.")
        return None

    def _busy(self, on: bool, msg: str = ""):
        self.progress_row.setVisible(on)
        if on:
            self.gen_error.setVisible(False)
            self.progress.setValue(0); self.progress_pct.setText("0%")
            self.status.setText(msg); self._set_msg_state("")
        else:
            QTimer.singleShot(1200, lambda: (self.progress_row.setVisible(False),
                                             self.stage_row.setVisible(False)))

    def load_meeting(self, m: Meeting):
        self._current_id = m.id
        self._loaded_from_history = True
        self.context_lbl.setText(f"✎  Editing “{m.title}” — opened from History")
        self.context_lbl.setVisible(True)
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
        if m.minutes:
            self._show_minutes_preview()
        self.toast.show_message(f"Loaded: {m.title}", "info")


# Other pages live in their own modules; re-exported so existing imports
# (`from .pages import HistoryPage, ...`) keep working unchanged.
from .history_page import HistoryPage      # noqa: E402
from .profiles_page import ProfilesPage    # noqa: E402
from .prompts_page import PromptsPage      # noqa: E402
from .settings_page import SettingsPage    # noqa: E402

__all__ = ["NewMeetingPage", "HistoryPage", "ProfilesPage", "PromptsPage", "SettingsPage"]
