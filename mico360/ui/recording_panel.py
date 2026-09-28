"""Recording panel: configure → record (with live details + visualizer) → summary.

Shows, in real time: status (Recording/Paused/Stopped), type (Audio/Screen/
Camera), HH:MM:SS timer, start date/time, file name, format, quality/resolution,
microphone status, camera status, live file size and save location, plus a red
blinking indicator and an audio level visualizer. After stopping, a summary card
shows total duration, file name, format, size and save location.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core import recording as R
from .components import Card, section_title, tip

log = logging.getLogger("mico360.recording_panel")

# Microphone picker data values that aren't device indices.
_SILENT = -1         # the user chose "No audio (silent)"
_NO_MIC = -2         # no microphone detected on this PC


def _is_empty_media(path) -> bool:
    """A recording file with nothing in it — e.g. the 44-byte header-only WAV
    left behind when a stop came before any audio arrived (K4)."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return False
    if size == 0:
        return True
    if p.suffix.lower() == ".wav":
        try:
            import soundfile as sf
            return sf.info(str(p)).frames == 0
        except Exception:
            return size <= 44
    return False


def _captured_nothing(rec, result) -> bool:
    """True when a stopped recording produced no usable file (K4): no file at
    all, an audio recorder that wrote no samples, a video without frames or
    sound, or a header-only file."""
    if result is None or not getattr(result, "path", ""):
        return True
    p = Path(result.path)
    if not p.exists():
        return True
    if isinstance(rec, R.AudioRecorder):
        try:
            if rec.recorded_seconds() <= 0:
                return True
        except Exception:
            pass
    if (isinstance(rec, R.VideoRecorder) and getattr(result, "kind", "") in ("screen", "camera")
            and getattr(rec, "_frames", 1) == 0 and not getattr(result, "has_audio", False)):
        return True
    return _is_empty_media(p)


def _discard_empty_outputs(rec, result) -> None:
    """Delete what an empty recording left behind (K4): the output file and any
    header-only temp files. Temp files with audio in them are always kept."""
    paths: list[str] = []
    if result is not None and getattr(result, "path", ""):
        paths.append(result.path)
    for r in (rec, getattr(rec, "_audio", None)):
        if r is None:
            continue
        paths += [t for t in (getattr(r, "_temps", None) or []) if t]
        for attr in ("_tmp_wav", "output_path"):
            v = getattr(r, attr, None)
            if isinstance(v, str) and v:
                paths.append(v)
    tmp_video = getattr(rec, "_tmp_video", None)
    no_frames = isinstance(rec, R.VideoRecorder) and getattr(rec, "_frames", 1) == 0
    no_samples = False
    if isinstance(rec, R.AudioRecorder):
        try:
            no_samples = rec.recorded_seconds() <= 0
        except Exception:
            no_samples = False
    own_output = getattr(rec, "output_path", None)
    seen: set[str] = set()
    for p in paths + ([tmp_video] if (tmp_video and no_frames) else []):
        if p in seen:
            continue
        seen.add(p)
        try:
            if not Path(p).exists():
                continue
            if (_is_empty_media(p) or (no_frames and p == tmp_video)
                    or (no_samples and p == own_output)):
                Path(p).unlink(missing_ok=True)
                log.info("removed empty recording file %s", p)
        except OSError:
            log.debug("could not remove %s", p, exc_info=True)


# ---------------------------------------------------------------------------
class AudioVisualizer(QWidget):
    """Live bar-style audio level meter."""
    def __init__(self, bars: int = 32):
        super().__init__()
        self.setMinimumHeight(56)
        self._bars = bars
        self._levels = deque([0.0] * bars, maxlen=bars)
        self._active = False

    def push(self, level: float):
        self._levels.append(max(0.0, min(1.0, level)))
        self.update()

    def set_active(self, on: bool):
        self._active = on
        if not on:
            self._levels = deque([0.0] * self._bars, maxlen=self._bars)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._levels)
        if n == 0:
            return
        gap = 3
        bw = max(2.0, (w - (n - 1) * gap) / n)
        for i, lv in enumerate(self._levels):
            bh = max(2.0, lv * (h - 4))
            x = i * (bw + gap)
            y = (h - bh) / 2
            if lv > 0.75:
                col = QColor("#EF4444")
            elif lv > 0.45:
                col = QColor("#F59E0B")
            else:
                col = QColor("#22C55E") if self._active else QColor("#4A4550")
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(int(x), int(y), int(bw), int(bh), 2, 2)


# ---------------------------------------------------------------------------
class RecordingPanel(QWidget):
    recordingReady = Signal(str)         # final media path -> queue for transcription
    liveTranscriptReady = Signal(str)    # full live transcript -> populate the transcript box
    recordingStateChanged = Signal(bool) # True while recording (drives the global indicator)
    recordingFailed = Signal()           # recorder hit an error and was torn down

    def __init__(self, toast=None, ctx=None):
        super().__init__()
        self.toast = toast
        self.ctx = ctx
        self._rec: R.BaseRecorder | None = None
        self._result: R.RecordingResult | None = None
        self._blink = False
        self._live_worker = None
        # K5: the live worker whose partial transcripts belong to the current
        # recording; chunks from any other (retired) worker are ignored.
        self._live_source = None
        self._live_text = ""
        self._auto_mode = False
        # M32: stop/mux runs on a RecorderStopWorker; while it runs we're "saving".
        self._stopping = False
        self._stop_ctx: dict = {}
        self._stop_workers: list = []            # kept until each thread has ended (H3)
        self._warned = 0                         # recorder warnings already shown

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_config())   # 0
        self.stack.addWidget(self._build_active())    # 1
        self.stack.addWidget(self._build_summary())   # 2
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._toggle_blink)

        self.refresh_devices()

    # ----- config page -----------------------------------------------------
    def _build_config(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        v.addWidget(section_title("Record a meeting"))

        # type selector
        type_row = QHBoxLayout()
        self.type_group = QButtonGroup(self)
        self._type_btns = {}
        _type_tips = {
            "audio": "Record sound only — smallest files, ideal for transcription (WAV or MP3)",
            "screen": "Record your screen as MP4 video with the selected audio — good for "
                      "online meetings shown on screen",
            "camera": "Record your webcam as MP4 video with the selected audio",
        }
        for key, label, icon in (("audio", "Audio", "🎙"), ("screen", "Screen + audio", "🖥"),
                                  ("camera", "Camera + audio", "📷")):
            b = QPushButton(f"{icon}  {label}")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(self._update_config_visibility)
            tip(b, _type_tips[key])
            self.type_group.addButton(b)
            self._type_btns[key] = b
            type_row.addWidget(b)
        self._type_btns["audio"].setChecked(True)
        v.addWidget(QLabel("What do you want to record?"))
        v.addLayout(type_row)

        # options row
        opt = QGridLayout()
        opt.setColumnStretch(1, 1)
        self.mic_box = QComboBox()
        # long device names must not widen the page — cap width, full text in popup
        self.mic_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mic_box.setMinimumContentsLength(16)
        tip(self.mic_box, "Which microphone to record from — pick 'No audio' for silent video. "
                          "Use Refresh devices after plugging in a mic")
        self.format_box = QComboBox()
        self.format_box.addItems(["WAV (recommended)", "MP3"])
        tip(self.format_box, "Audio file format: WAV is lossless and best for transcription; "
                             "MP3 is smaller for sharing")
        # Audio source: mic / system (speaker loopback) / both
        self.source_box = QComboBox()
        self._sys_ok = R.system_audio_supported()
        self.source_box.addItem("Microphone", "mic")
        if self._sys_ok:
            self.source_box.addItem("System audio (what you hear)", "system")
            self.source_box.addItem("Microphone + System audio", "both")
        self.source_box.currentIndexChanged.connect(self._update_config_visibility)
        tip(self.source_box, "What to capture: your microphone, the computer's own sound "
                             "('what you hear' — captures online-meeting participants), or both mixed")
        self.mic_status = QLabel()
        self.mic_status.setObjectName("Hint")
        self.mic_status.setWordWrap(True)        # long "no mic" message must wrap, not widen the page
        opt.addWidget(QLabel("Audio source"), 0, 0)
        opt.addWidget(self.source_box, 0, 1)
        opt.addWidget(QLabel("Microphone"), 1, 0)
        opt.addWidget(self.mic_box, 1, 1)
        self._fmt_label = QLabel("Audio format")
        opt.addWidget(self._fmt_label, 2, 0)
        opt.addWidget(self.format_box, 2, 1)
        opt.addWidget(self.mic_status, 3, 0, 1, 2)
        v.addLayout(opt)

        # Live transcription (beta) — transcribe from the mic as you record.
        self.live_check = QCheckBox("Live transcript (beta) — transcribe as you record")
        if self.ctx is not None:
            self.live_check.setChecked(bool(self.ctx.settings.get("live_transcription", False)))
            self.live_check.toggled.connect(
                lambda on: self.ctx.settings.set("live_transcription", on))
        tip(self.live_check, "Show a rough transcript on screen while you record (audio only), so "
                             "the minutes are ready the moment you stop. Uses the microphone; for "
                             "responsiveness pick the tiny or base Whisper model in Settings.")
        v.addWidget(self.live_check)

        start_row = QHBoxLayout()
        refresh = QPushButton("↻ Refresh devices")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.refresh_devices)
        tip(refresh, "Re-scan microphones — use after plugging in or enabling a device")
        self.start_btn = QPushButton("● Start recording")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self._start)
        tip(self.start_btn, "Begin recording with the options above — a live panel shows the "
                            "timer, level meter and file size while recording")
        start_row.addWidget(refresh)
        start_row.addStretch()
        start_row.addWidget(self.start_btn)
        v.addLayout(start_row)
        self._update_config_visibility()
        return card

    def _selected_type(self) -> str:
        for k, b in self._type_btns.items():
            if b.isChecked():
                return k
        return "audio"

    def _update_config_visibility(self):
        is_audio = self._selected_type() == "audio"
        self._fmt_label.setVisible(is_audio)
        self.format_box.setVisible(is_audio)
        # the mic picker is irrelevant when capturing system audio only
        source = self.source_box.currentData() or "mic"
        self.mic_box.setEnabled(source in ("mic", "both"))

    def refresh_devices(self):
        self.mic_box.clear()
        mics = R.list_microphones()
        if mics:
            for m in mics:
                self.mic_box.addItem(f"{m.name}", m.index)
            self.mic_box.addItem("No audio (silent)", -1)
            self.mic_status.setText(f"✓ {len(mics)} microphone(s) detected")
            self.mic_status.setStyleSheet("color:#22C55E;")
            self.start_btn.setEnabled(True)
        else:
            # -2 = none available (K8), unlike -1 = the user chose "No audio"
            self.mic_box.addItem("No microphone detected", _NO_MIC)
            self.mic_status.setText("⚠ No microphone detected — audio will be silent. "
                                    "You can still record screen/camera video.")
            self.mic_status.setStyleSheet("color:#EF4444;")

    # ----- active page -----------------------------------------------------
    def _build_active(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(12)

        head = QHBoxLayout()
        self.rec_dot = QLabel("●")
        self.rec_dot.setStyleSheet("color:#EF4444; font-size:16pt;")
        self.status_lbl = QLabel("Recording")
        self.status_lbl.setStyleSheet("font-size:13pt; font-weight:700;")
        self.timer_lbl = QLabel("00:00:00")
        self.timer_lbl.setStyleSheet("font-size:20pt; font-weight:700;")
        head.addWidget(self.rec_dot)
        head.addWidget(self.status_lbl)
        head.addStretch()
        head.addWidget(self.timer_lbl)
        v.addLayout(head)

        self.viz = AudioVisualizer()
        v.addWidget(self.viz)

        # live transcript preview (shown only when live transcription is on)
        self.live_box = QPlainTextEdit(); self.live_box.setReadOnly(True)
        self.live_box.setPlaceholderText("Live transcript will appear here as you speak…")
        self.live_box.setMaximumHeight(120); self.live_box.setVisible(False)
        tip(self.live_box, "A rough live transcript. It becomes your editable transcript when you "
                           "stop — use “Use for transcription” to re-transcribe for higher accuracy.")
        v.addWidget(self.live_box)

        # details grid
        self.detail_labels: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(7)
        fields = ["Type", "Status", "Format", "Quality", "Microphone",
                  "Camera", "File name", "File size", "Started", "Save location"]
        for i, name in enumerate(fields):
            r, c = divmod(i, 2)
            key = QLabel(name); key.setObjectName("Hint")
            val = QLabel("—"); val.setWordWrap(True)
            self.detail_labels[name] = val
            cell = QVBoxLayout(); cell.setSpacing(0)
            cell.addWidget(key); cell.addWidget(val)
            holder = QWidget(); holder.setLayout(cell)
            grid.addWidget(holder, r, c)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        v.addLayout(grid)

        # controls
        ctl = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Pause"); self.pause_btn.clicked.connect(self._toggle_pause)
        tip(self.pause_btn, "Pause the recording — the timer freezes and nothing is captured "
                            "until you resume")
        self.cancel_btn = QPushButton("✕ Cancel"); self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.clicked.connect(self._cancel)
        tip(self.cancel_btn, "Discard this recording completely — the file is deleted and "
                             "cannot be recovered")
        self.stop_btn = QPushButton("⏹ Stop & Save"); self.stop_btn.setObjectName("Primary")
        self.stop_btn.clicked.connect(self._stop)
        tip(self.stop_btn, "Finish and save the recording, then show a summary with the file "
                           "details. Video may take a moment to finalise")
        ctl.addWidget(self.pause_btn)
        ctl.addStretch()
        ctl.addWidget(self.cancel_btn)
        ctl.addWidget(self.stop_btn)
        v.addLayout(ctl)
        return card

    # ----- summary page ----------------------------------------------------
    def _build_summary(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        title = QHBoxLayout()
        ok = QLabel("✓")
        ok.setStyleSheet("color:#22C55E; font-size:16pt; font-weight:700;")
        t = QLabel("Recording saved"); t.setObjectName("SectionTitle")
        title.addWidget(ok); title.addWidget(t); title.addStretch()
        v.addLayout(title)

        self.summary_grid = QGridLayout()
        self.summary_grid.setHorizontalSpacing(18)
        self.summary_grid.setVerticalSpacing(7)
        v.addLayout(self.summary_grid)

        row = QHBoxLayout()
        self.open_folder_btn = QPushButton("📂 Open folder")
        self.open_folder_btn.clicked.connect(self._open_folder)
        tip(self.open_folder_btn, "Open the folder containing the saved recording in Explorer")
        new_btn = QPushButton("● New recording")
        new_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        tip(new_btn, "Back to the recording options to start another recording — this one "
                     "stays saved")
        self.use_btn = QPushButton("✓ Use for transcription")
        self.use_btn.setObjectName("Primary")
        self.use_btn.clicked.connect(self._use_recording)
        tip(self.use_btn, "Add this recording to the transcription queue in the Upload tab")
        row.addWidget(self.open_folder_btn)
        row.addStretch()
        row.addWidget(new_btn)
        row.addWidget(self.use_btn)
        v.addLayout(row)
        return card

    # ----- lifecycle -------------------------------------------------------
    # ----- programmatic control (auto-record) --------------------------------
    def is_recording(self) -> bool:
        """True while recording — and while the last recording is still being
        saved, so nothing starts a new one on top of it."""
        if self._stopping:
            return True
        return bool(self._rec) and self._rec.state in (R.RECORDING, R.PAUSED)

    def is_saving(self) -> bool:
        return self._stopping

    def start_auto(self, source: str = "both", live: bool = True) -> bool:
        """Start an audio recording without user clicks (auto-record). Uses the
        requested source if available (falls back to the microphone) and the
        live transcript so minutes are ready the moment the meeting ends."""
        if self.is_recording():
            return True
        self._type_btns["audio"].setChecked(True)
        i = self.source_box.findData(source)
        self.source_box.setCurrentIndex(i if i >= 0 else 0)
        if hasattr(self, "live_check"):
            self.live_check.setChecked(bool(live))
        self._auto_mode = True
        self._rec = None
        self._start()
        ok = self.stack.currentIndex() == 1 and self.is_recording()
        if not ok:
            self._auto_mode = False
        return ok

    def stop_auto(self) -> None:
        if self.is_recording():
            self._stop()

    def _start(self):
        if self._stopping:
            if self.toast:
                self.toast.show_message("The previous recording is still being saved — "
                                        "try again in a moment.", "warn", 5000)
            return
        self._warned = 0
        kind = self._selected_type()
        source = self.source_box.currentData() or "mic"
        mic_index = self.mic_box.currentData()
        if mic_index == _SILENT:
            # "No audio (silent)" mutes the microphone; system-only still records
            include_audio = source == "system"
        elif mic_index == _NO_MIC:
            # K8: no microphone on this PC — still record the part of the source
            # that exists (system audio for "System" / "Mic + System"), like an
            # audio-only recording does, instead of a silent video.
            include_audio = source in ("system", "both") and bool(self._sys_ok)
        else:
            include_audio = True
        cfg = R.RecordingConfig(
            kind=kind,
            source=source,
            audio_format="mp3" if (kind == "audio" and self.format_box.currentIndex() == 1) else "wav",
            include_audio=include_audio,
            mic_index=None if (mic_index in (_SILENT, _NO_MIC, None)) else mic_index,
        )
        try:
            self._rec = R.make_recorder(cfg)
            self._rec.start()
        except Exception as exc:
            rec, self._rec = self._rec, None
            try:
                if rec is not None:
                    rec.stop()                   # release anything that did open
            except Exception:
                pass
            if self.toast:
                self.toast.show_message(f"Could not start recording: {exc}", "error", 6000)
            return
        # Live transcription (audio recordings only, when enabled).
        self._retire_live_worker(0)
        self._live_source = None                 # K5: earlier workers no longer count
        self._live_text = ""
        live_on = (getattr(self, "live_check", None) is not None and self.live_check.isChecked()
                   and self.ctx is not None and kind == "audio")
        if live_on:
            try:
                self._rec.enable_live()
                from .workers import LiveTranscribeWorker
                engine = self.ctx.transcription_engine()
                self._live_worker = LiveTranscribeWorker(
                    self._rec, engine, self.ctx.settings.get("language", "auto"))
                self._live_source = self._live_worker
                self._live_worker.partial.connect(self._on_live_partial)
                self._live_worker.start()
                self.live_box.clear(); self.live_box.setVisible(True)
            except Exception:
                self._live_worker = self._live_source = None
                self.live_box.setVisible(False)
        else:
            self.live_box.setVisible(False)

        self._fill_static_details(cfg)
        self.viz.set_active(True)
        self.stack.setCurrentIndex(1)
        self._timer.start()
        self._blink_timer.start()
        self.recordingStateChanged.emit(True)

    def _on_live_partial(self, text: str):
        # K5: a retired worker still finishing a chunk of an EARLIER recording
        # must not append to this one's transcript.
        src = self.sender()
        if src is not None and src is not self._live_source:
            log.debug("ignoring a live-transcript chunk from a retired worker")
            return
        self._live_text = (self._live_text + " " + text).strip()
        self.live_box.setPlainText(self._live_text)
        self.live_box.moveCursor(QTextCursor.End)

    def _fill_static_details(self, cfg: R.RecordingConfig):
        rec = self._rec
        type_name = {"audio": "Audio", "screen": "Screen + audio", "camera": "Camera + audio"}[cfg.kind]
        fmt = "MP4" if cfg.kind in ("screen", "camera") else cfg.audio_format.upper()
        if cfg.kind == "audio":
            quality = "16 kHz mono · PCM 16-bit" if cfg.audio_format == "wav" else "16 kHz mono · MP3"
        else:
            quality = f"{rec.resolution or '...'} @ {cfg.fps} fps"
        mic_name = "None (silent)"
        if cfg.include_audio:
            sel = self.mic_box.currentText()
            mic_name = sel if sel else "Default microphone"
        cam = "Active" if cfg.kind == "camera" else ("N/A" if cfg.kind != "camera" else "—")
        self.detail_labels["Type"].setText(type_name)
        self.detail_labels["Format"].setText(fmt)
        self.detail_labels["Quality"].setText(quality)
        self.detail_labels["Microphone"].setText(mic_name)
        self.detail_labels["Camera"].setText("Active" if cfg.kind == "camera" else "N/A")
        self.detail_labels["File name"].setText(Path(rec.output_path).name)
        self.detail_labels["Started"].setText(time.strftime("%Y-%m-%d  %H:%M:%S", time.localtime(rec.started_at)))
        self.detail_labels["Save location"].setText(str(Path(rec.output_path).parent))

    def _tick(self):
        rec = self._rec
        if not rec or self._stopping:
            return
        if rec.state == R.ERROR:
            # Fully tear the failed recording down: release the devices and the
            # capture threads, stop the live worker, and clear the recording and
            # auto-record state so nothing keeps running or triggers later. The
            # stop runs off the GUI thread; whatever was recorded before the
            # problem (e.g. an unplugged mic, H13) is kept and shown.
            self._begin_stop(error=rec.error or "the recorder stopped unexpectedly")
            return
        self._show_new_warnings(rec)
        secs = int(rec.elapsed())
        h, rem = divmod(secs, 3600); m, s = divmod(rem, 60)
        self.timer_lbl.setText(f"{h:02d}:{m:02d}:{s:02d}")
        paused = rec.state == R.PAUSED
        self.status_lbl.setText("Paused" if paused else "Recording")
        self.status_lbl.setStyleSheet(
            "font-size:13pt; font-weight:700; color:%s;" % ("#F59E0B" if paused else "#EF4444"))
        self.detail_labels["Status"].setText("Paused" if paused else "Recording")
        self.detail_labels["File size"].setText(R.human_size(rec.file_size()))
        # once the video resolution is known, reflect it in the Quality field
        if rec.cfg.kind in ("screen", "camera") and rec.resolution:
            self.detail_labels["Quality"].setText(f"{rec.resolution} @ {rec.cfg.fps} fps")
        self.viz.set_active(not paused)
        self.viz.push(rec.level())

    def _toggle_blink(self):
        self._blink = not self._blink
        if self._rec and self._rec.state == R.PAUSED:
            self.rec_dot.setVisible(True)
            self.rec_dot.setStyleSheet("color:#F59E0B; font-size:16pt;")
        else:
            self.rec_dot.setVisible(self._blink)
            self.rec_dot.setStyleSheet("color:#EF4444; font-size:16pt;")

    def _toggle_pause(self):
        if not self._rec:
            return
        if self._rec.state == R.RECORDING:
            self._rec.pause()
            self.pause_btn.setText("▶ Resume")
        elif self._rec.state == R.PAUSED:
            self._rec.resume()
            self.pause_btn.setText("⏸ Pause")

    def _show_new_warnings(self, rec) -> None:
        """Surface non-fatal recorder problems once each (a source that couldn't
        be opened, a mic that dropped out and was reopened, …)."""
        getter = getattr(rec, "all_warnings", None)
        if getter is None:
            return
        try:
            warnings = list(getter())
        except Exception:
            return
        if len(warnings) > self._warned:
            for msg in warnings[self._warned:]:
                log.warning("recording: %s", msg)
                if self.toast:
                    self.toast.show_message(msg, "warn", 9000)
            self._warned = len(warnings)

    def _retire_live_worker(self, wait_ms: int) -> None:
        """Stop the live-transcription worker. If it's still finishing a chunk
        after `wait_ms`, keep a reference until it really ends — dropping the last
        reference to a running QThread aborts the whole process."""
        w = getattr(self, "_live_worker", None)
        self._live_worker = None
        self._retire_worker(w, wait_ms)

    def _retire_worker(self, w, wait_ms: int = 0) -> None:
        if w is None:
            return
        try:
            w.stop()
        except Exception:
            pass
        if wait_ms:
            w.wait(wait_ms)
        if w is not self._live_source:
            self._disconnect_partial(w)           # K5: its chunks belong to no recording
        if w.isRunning():
            retired = self.__dict__.setdefault("_retired_workers", [])
            if w not in retired:
                retired.append(w)
                w.finished.connect(lambda w=w: w in retired and retired.remove(w))

    def _disconnect_partial(self, w) -> None:
        sig = getattr(w, "partial", None)
        if sig is None:
            return
        try:
            sig.disconnect(self._on_live_partial)
        except (RuntimeError, TypeError, SystemError):
            pass                                  # not connected (any more)

    def _release_live_source(self) -> None:
        """The current recording's live transcript has been consumed (K5)."""
        w, self._live_source = self._live_source, None
        if w is not None:
            self._disconnect_partial(w)

    def background_threads(self) -> list:
        """K6: every thread this panel runs — stop/save workers, the live
        transcription worker (also the one handed to a stop still in flight)
        and retired workers still finishing — so the main window can wait for
        them before Qt tears the widgets down."""
        cands = list(self._stop_workers)
        cands.append(self._live_worker)
        cands.append((self._stop_ctx or {}).get("live"))
        cands += list(self.__dict__.get("_retired_workers", ()))
        out: list = []
        for w in cands:
            if w is None or any(w is o for o in out):
                continue
            try:
                if w.isRunning():
                    out.append(w)
            except RuntimeError:                  # already deleted on the C++ side
                continue
        return out

    def _cancel(self):
        if self._stopping:
            return
        if not self._rec:
            self._retire_live_worker(0)
            self._teardown()
            self.stack.setCurrentIndex(0)
            return
        self._begin_stop(cancel=True)

    def _stop(self):
        if not self._rec or self._stopping:
            return
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Saving…")
        self._begin_stop()

    def _begin_stop(self, cancel: bool = False, error: str = "") -> None:
        """Stop / cancel / tear down the recorder on a background thread (M32):
        closing devices, mixing mic + system audio and muxing a long video used
        to freeze the window. The result is handled in _on_stop_done."""
        from .workers import RecorderStopWorker
        rec = self._rec
        self._stopping = True
        self._timer.stop()
        self._blink_timer.stop()
        self.rec_dot.setVisible(True)
        for b in (self.pause_btn, self.cancel_btn, self.stop_btn):
            b.setEnabled(False)
        label = "Cancelling…" if cancel else ("Stopping…" if error else "Saving…")
        self.status_lbl.setText(label)
        self.detail_labels["Status"].setText(label)
        live, self._live_worker = self._live_worker, None
        auto = bool(self._auto_mode)
        self._stop_ctx = {"rec": rec, "cancel": cancel, "error": error, "live": live}
        # Manual recordings wait (off the GUI thread) for the live worker's last
        # chunk; auto-record doesn't use the live draft when the file was saved.
        w = RecorderStopWorker(rec, cancel=cancel, live_worker=live,
                               live_wait_ms=0 if (cancel or error) else 6000,
                               skip_live_wait_if_file=auto)
        self._stop_workers.append(w)
        w.done.connect(self._on_stop_done)
        w.finished.connect(self._on_stop_thread_finished)
        w.start()

    def _on_stop_thread_finished(self):
        for w in list(self._stop_workers):
            if not w.isRunning():
                w.wait(2000)
                self._stop_workers.remove(w)
                w.deleteLater()

    def _on_stop_done(self, result, err: str):
        ctx, self._stop_ctx = self._stop_ctx, {}
        self._stopping = False
        # the live worker may still be finishing a chunk: keep it alive (H3)
        self._retire_worker(ctx.get("live"), 0)
        for b in (self.pause_btn, self.cancel_btn, self.stop_btn):
            b.setEnabled(True)
        self.stop_btn.setText("⏹ Stop & Save")
        self.pause_btn.setText("⏸ Pause")
        if ctx.get("cancel"):
            self._teardown()
            self.stack.setCurrentIndex(0)
            if self.toast:
                self.toast.show_message("Recording cancelled.", "warn")
            return
        if ctx.get("error"):
            self._on_recording_error(result, ctx["error"], rec=ctx.get("rec"))
            return
        if result is None:
            rec = ctx.get("rec")
            if rec is not None and not getattr(rec, "error", ""):
                try:
                    rec.error = err
                except Exception:
                    pass
        self._finish_stop(result, live_wait_ms=0, stopped=True)

    def _on_recording_error(self, res, err: str, rec=None) -> None:
        """The recorder failed mid-way. Keep and show whatever it saved."""
        rec = rec if rec is not None else self._rec
        self._rec = None
        self._teardown()
        partial = False
        try:
            partial = bool(res is not None and res.path and Path(res.path).exists()
                           and Path(res.path).stat().st_size > 1024
                           and not _captured_nothing(rec, res))
        except OSError:
            partial = False
        if not partial and _captured_nothing(rec, res):
            _discard_empty_outputs(rec, res)      # K4: no header-only files left behind
        self.recordingFailed.emit()
        if partial:
            self._result = res
            self._show_summary(res, announce=False)
            if self.toast:
                self.toast.show_message(f"Recording stopped: {err} The part recorded before the "
                                        "problem was saved.", "error", 9000)
        else:
            self.stack.setCurrentIndex(0)
            if self.toast:
                self.toast.show_message(f"Recording error: {err}", "error", 6000)

    def _finish_stop(self, result=None, live_wait_ms: int = 6000, stopped: bool = False):
        """Handle a stopped recording. Called with the result from the background
        stop worker; called with no result it stops the recorder here (the
        synchronous path)."""
        rec = self._rec
        if not stopped and result is None and rec is not None:
            result = rec.stop()
        auto = bool(self._auto_mode)
        # K4: a file that exists isn't enough — a stop before any audio arrived
        # leaves a header-only WAV, which is neither "saved" nor transcribable.
        try:
            has_file = not _captured_nothing(rec, result)
        except OSError:
            has_file = False
        self._result = result if has_file else None
        # flush + stop the live worker (its last chunk lands in _live_text)
        self._retire_live_worker(live_wait_ms)
        live = self._live_text.strip()
        self._release_live_source()               # K5: later chunks belong to no recording
        if auto and has_file:
            # Auto-record: transcribe the full recording. The live draft only hears
            # the local microphone, so minutes built from it would leave out
            # everything the other participants said (system audio).
            self.recordingReady.emit(result.path)
        elif live:
            self.liveTranscriptReady.emit(live)
        self._auto_mode = False
        self._teardown()
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("⏹ Stop & Save")
        if has_file:
            self._show_summary(result)
            return
        # M31: nothing was written (e.g. the mix ran out of memory / disk) — say
        # so instead of claiming "Recording saved". K4: an empty recording is
        # said to be empty, and its header-only output / temp files are removed.
        _discard_empty_outputs(rec, result)
        why = getattr(rec, "error", "") if rec is not None else ""
        self.stack.setCurrentIndex(0)
        self.recordingFailed.emit()
        if self.toast:
            extra = " The live transcript was kept." if live else ""
            if why:
                msg = f"The recording could not be saved: {why}{extra}"
            else:
                msg = ("Nothing was recorded — no audio was captured, so no file was saved. "
                       "Check that the microphone isn't muted (or that something was playing "
                       f"for system audio).{extra}")
            self.toast.show_message(msg, "error", 10000)

    def _teardown(self):
        self._timer.stop()
        self._blink_timer.stop()
        self.viz.set_active(False)
        self._auto_mode = False
        self._live_source = None                  # K5
        self.recordingStateChanged.emit(False)

    def _show_summary(self, res: R.RecordingResult, announce: bool = True):
        # clear grid
        while self.summary_grid.count():
            it = self.summary_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        secs = int(res.duration); h, rem = divmod(secs, 3600); m, s = divmod(rem, 60)
        rows = [
            ("Total duration", f"{h:02d}:{m:02d}:{s:02d}"),
            ("Type", {"audio": "Audio", "screen": "Screen + audio", "camera": "Camera + audio"}.get(res.kind, res.kind)),
            ("File name", Path(res.path).name),
            ("Format", res.fmt.upper()),
            ("File size", res.size_human),
            ("Resolution", res.resolution or "—"),
            ("Audio", "Included" if res.has_audio else "No audio"),
            ("Save location", str(Path(res.path).parent)),
        ]
        for i, (k, val) in enumerate(rows):
            r, c = divmod(i, 2)
            key = QLabel(k); key.setObjectName("Hint")
            value = QLabel(val); value.setWordWrap(True)
            cell = QVBoxLayout(); cell.setSpacing(0)
            cell.addWidget(key); cell.addWidget(value)
            holder = QWidget(); holder.setLayout(cell)
            self.summary_grid.addWidget(holder, r, c)
        self.summary_grid.setColumnStretch(0, 1)
        self.summary_grid.setColumnStretch(1, 1)
        self.stack.setCurrentIndex(2)
        if self.toast and announce:
            self.toast.show_message("Recording saved.", "success")

    def _open_folder(self):
        if not self._result:
            return
        folder = str(Path(self._result.path).parent)
        try:
            if sys.platform == "win32":
                os.startfile(folder)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

    def _use_recording(self):
        if self._result and Path(self._result.path).exists():
            self.recordingReady.emit(self._result.path)
            if self.toast:
                self.toast.show_message("Added to the transcription queue.", "success")

    def stop_if_active(self):
        """Called on app close to flush an in-progress recording, and to let every
        worker thread end first (a QThread destroyed while running aborts the
        process — H3)."""
        # a save already under way must finish, or the file is lost
        for w in list(self._stop_workers):
            try:
                if w.isRunning():
                    w.wait(180000)
            except Exception:
                pass
        # K6: a stop that finished while we blocked here never gets its done
        # signal delivered (_on_stop_done doesn't run), so the live worker it
        # was handed is only referenced from _stop_ctx — stop it and wait.
        pending_live = (self._stop_ctx or {}).get("live")
        if pending_live is not None:
            try:
                self._retire_worker(pending_live, 8000)
            except Exception:
                pass
        try:
            self._retire_live_worker(8000)
        except Exception:
            pass
        if self._rec and self._rec.state in (R.RECORDING, R.PAUSED):
            try:
                self._rec.stop()
            except Exception:
                pass
        for w in list(self.__dict__.get("_retired_workers", ())):
            try:
                if w.isRunning():
                    w.stop()
                    w.wait(10000)
            except Exception:
                pass
