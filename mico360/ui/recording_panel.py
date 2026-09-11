"""Recording panel: configure → record (with live details + visualizer) → summary.

Shows, in real time: status (Recording/Paused/Stopped), type (Audio/Screen/
Camera), HH:MM:SS timer, start date/time, file name, format, quality/resolution,
microphone status, camera status, live file size and save location, plus a red
blinking indicator and an audio level visualizer. After stopping, a summary card
shows total duration, file name, format, size and save location.
"""
from __future__ import annotations

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

    def __init__(self, toast=None, ctx=None):
        super().__init__()
        self.toast = toast
        self.ctx = ctx
        self._rec: R.BaseRecorder | None = None
        self._result: R.RecordingResult | None = None
        self._blink = False
        self._live_worker = None
        self._live_text = ""

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
            self.mic_box.addItem("No microphone detected", -1)
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
        return bool(self._rec) and self._rec.state in (R.RECORDING, R.PAUSED)

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
        kind = self._selected_type()
        source = self.source_box.currentData() or "mic"
        mic_index = self.mic_box.currentData()
        include_audio = mic_index != -1 or source == "system"
        cfg = R.RecordingConfig(
            kind=kind,
            source=source,
            audio_format="mp3" if (kind == "audio" and self.format_box.currentIndex() == 1) else "wav",
            include_audio=include_audio,
            mic_index=None if (mic_index in (-1, None)) else mic_index,
        )
        try:
            self._rec = R.make_recorder(cfg)
            self._rec.start()
        except Exception as exc:
            if self.toast:
                self.toast.show_message(f"Could not start recording: {exc}", "error", 6000)
            return
        # Live transcription (audio recordings only, when enabled).
        self._live_text = ""; self._live_worker = None
        live_on = (getattr(self, "live_check", None) is not None and self.live_check.isChecked()
                   and self.ctx is not None and kind == "audio")
        if live_on:
            try:
                self._rec.enable_live()
                from .workers import LiveTranscribeWorker
                engine = self.ctx.transcription_engine()
                self._live_worker = LiveTranscribeWorker(
                    self._rec, engine, self.ctx.settings.get("language", "auto"))
                self._live_worker.partial.connect(self._on_live_partial)
                self._live_worker.start()
                self.live_box.clear(); self.live_box.setVisible(True)
            except Exception:
                self._live_worker = None
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
        if not rec:
            return
        if rec.state == R.ERROR:
            self._timer.stop(); self._blink_timer.stop()
            if self.toast:
                self.toast.show_message(f"Recording error: {rec.error}", "error", 6000)
            self.stack.setCurrentIndex(0)
            return
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

    def _cancel(self):
        if self._live_worker is not None:
            self._live_worker.stop(); self._live_worker.wait(4000); self._live_worker = None
        if self._rec:
            self._rec.cancel()
        self._teardown()
        self.stack.setCurrentIndex(0)
        if self.toast:
            self.toast.show_message("Recording cancelled.", "warn")

    def _stop(self):
        if not self._rec:
            return
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Saving…")
        # mux may take a moment for long videos; do it without freezing the timer UI badly
        QTimer.singleShot(50, self._finish_stop)

    def _finish_stop(self):
        rec = self._rec
        self._result = rec.stop()
        # flush + stop the live worker, then hand the transcript to New Meeting
        if self._live_worker is not None:
            self._live_worker.stop()
            self._live_worker.wait(6000)
            self._live_worker = None
            if self._live_text.strip():
                self.liveTranscriptReady.emit(self._live_text.strip())
        # Auto-record with no live text: hand the file straight to the queue so
        # minutes still get generated without a click.
        if getattr(self, "_auto_mode", False) and not self._live_text.strip() \
                and self._result and Path(self._result.path).exists():
            self.recordingReady.emit(self._result.path)
        self._auto_mode = False
        self._teardown()
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("⏹ Stop & Save")
        self._show_summary(self._result)

    def _teardown(self):
        self._timer.stop()
        self._blink_timer.stop()
        self.viz.set_active(False)
        self._auto_mode = False
        self.recordingStateChanged.emit(False)

    def _show_summary(self, res: R.RecordingResult):
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
        if self.toast:
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
        """Called on app close to flush an in-progress recording."""
        if self._live_worker is not None:
            try:
                self._live_worker.stop(); self._live_worker.wait(3000)
            except Exception:
                pass
            self._live_worker = None
        if self._rec and self._rec.state in (R.RECORDING, R.PAUSED):
            try:
                self._rec.stop()
            except Exception:
                pass
