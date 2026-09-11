"""Background workers (QThread) for transcription, generation and recording,
so the UI never blocks. Progress and results are delivered via Qt signals.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.ollama_client import OllamaGenerator
from ..core.transcription import TranscriptionEngine, TranscriptResult
from ..core import updater

log = logging.getLogger("mico360.workers")


def resample_16k(data, rate: int):
    """Linear-resample a mono float32 array to 16 kHz (Whisper's rate)."""
    import numpy as np
    target = 16000
    if rate == target or data is None or len(data) == 0:
        return None if data is None else data.astype("float32")
    n = int(len(data) * target / rate)
    if n <= 0:
        return data.astype("float32")
    return np.interp(np.linspace(0, 1, n, endpoint=False),
                     np.linspace(0, 1, len(data), endpoint=False), data).astype("float32")


class LiveTranscribeWorker(QThread):
    """Poll a recorder's live audio tap and emit rolling partial transcript text
    while recording — so a draft transcript exists the moment recording stops."""
    partial = Signal(str)               # newly transcribed text

    def __init__(self, recorder, engine: TranscriptionEngine, language: str = "auto",
                 interval: float = 6.0, min_seconds: float = 2.5):
        super().__init__()
        self.recorder = recorder
        self.engine = engine
        self.language = language
        self.interval = interval
        self.min_seconds = min_seconds
        import threading
        self._stop = threading.Event()
        self._carry = None

    def run(self):
        try:
            self.engine.load()
        except Exception:
            log.warning("live transcription: model load failed", exc_info=True)
            return
        while not self._stop.wait(0):          # loop until stopped
            self._sleep(self.interval)
            if self._stop.is_set():
                break
            self._flush(final=False)
        self._flush(final=True)                # transcribe any tail audio

    def _sleep(self, secs: float):
        end = time.time() + secs
        while time.time() < end and not self._stop.is_set():
            self.msleep(120)

    def _flush(self, final: bool):
        import numpy as np
        new, rate = self.recorder.pull_live()
        if self._carry is not None:
            new = self._carry if new is None else np.concatenate([self._carry, new])
        self._carry = None
        if new is None or len(new) == 0:
            return
        if not final and len(new) < rate * self.min_seconds:
            self._carry = new                  # not enough yet — keep for next round
            return
        audio16 = resample_16k(new, rate)
        try:
            text = self.engine.transcribe_array(audio16, self.language)
        except Exception:
            log.warning("live transcription chunk failed", exc_info=True)
            return
        if text:
            self.partial.emit(text)

    def stop(self):
        self._stop.set()


class MeetingWatchWorker(QThread):
    """Background watcher for auto-record: polls for a live Teams/Meet/Zoom/Webex
    window and for calendar meetings that are about to start. Inputs are
    injectable (titles_fn / calendar_fn) so the transition logic is testable."""
    meetingDetected = Signal(str, str)      # app, meeting title   (window appeared)
    meetingEnded = Signal()                 # window gone
    meetingDue = Signal(str, str)           # title, join_url      (calendar, once each)

    def __init__(self, titles_fn=None, calendar_fn=None, poll_seconds: float = 8.0,
                 calendar_seconds: float = 60.0, lead_minutes: float = 3.0):
        super().__init__()
        from ..core import meeting_watch as MW
        self._MW = MW
        self.titles_fn = titles_fn or MW.list_window_titles
        self.calendar_fn = calendar_fn                  # () -> list[MeetingInfo]
        self.poll_seconds = poll_seconds
        self.calendar_seconds = calendar_seconds
        self.lead_minutes = lead_minutes
        import threading
        self._stop = threading.Event()
        self._live = None                               # (app, title) currently seen
        self._misses = 0
        self._prompted: set[str] = set()
        self._last_cal = 0.0

    def poll_once(self, now=None):
        """One detection round; emits transitions. Returns the live meeting or None."""
        hit = self._MW.detect_live_meeting(self.titles_fn())
        if hit and not self._live:
            self._live, self._misses = hit, 0
            self.meetingDetected.emit(*hit)
        elif hit:
            self._misses = 0
        elif self._live:
            self._misses += 1
            if self._misses >= 2:                       # debounce a flickering title
                self._live = None
                self.meetingEnded.emit()
        # calendar (throttled)
        if self.calendar_fn and (now or time.time()) - self._last_cal >= self.calendar_seconds:
            self._last_cal = now or time.time()
            try:
                due = self._MW.next_due(self.calendar_fn(), lead_minutes=self.lead_minutes)
            except Exception:
                due = None
            if due and due.key() not in self._prompted:
                self._prompted.add(due.key())
                self.meetingDue.emit(due.title, due.join_url)
        return self._live

    def run(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.debug("meeting watch poll failed", exc_info=True)
            end = time.time() + self.poll_seconds
            while time.time() < end and not self._stop.is_set():
                self.msleep(200)

    def stop(self):
        self._stop.set()


class TranscribeWorker(QThread):
    progress = Signal(float, str)
    finished_ok = Signal(object)        # TranscriptResult
    failed = Signal(str)

    def __init__(self, engine: TranscriptionEngine, path: str, language: str = "auto",
                 diarize: bool = False):
        super().__init__()
        self.engine = engine
        self.path = path
        self.language = language
        self.diarize = diarize
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            res = self.engine.transcribe_file(
                self.path,
                language=self.language,
                progress=lambda f, m: self.progress.emit(f, m),
                cancel=lambda: self._cancel,
                diarize=self.diarize,
            )
            if self._cancel:
                self.failed.emit("Cancelled.")
                return
            self.finished_ok.emit(res)
        except InterruptedError:
            self.failed.emit("Transcription cancelled.")
        except Exception as exc:
            log.exception("transcription failed")
            self.failed.emit(str(exc))


class GenerateWorker(QThread):
    progress = Signal(float, str)
    finished_ok = Signal(str)           # minutes markdown
    failed = Signal(str)

    def __init__(self, generator: OllamaGenerator, transcript: str, template: str,
                 style: str, remove_fillers: bool, chunk_chars: int):
        super().__init__()
        self.generator = generator
        self.transcript = transcript
        self.template = template
        self.style = style
        self.remove_fillers = remove_fillers
        self.chunk_chars = chunk_chars
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            md = self.generator.generate_minutes(
                self.transcript, self.template, self.style,
                remove_fillers=self.remove_fillers, chunk_chars=self.chunk_chars,
                progress=lambda f, m: self.progress.emit(f, m),
                cancel=lambda: self._cancel,
            )
            if self._cancel:
                self.failed.emit("Cancelled.")
                return
            self.finished_ok.emit(md)
        except InterruptedError:
            self.failed.emit("Generation cancelled.")
        except Exception as exc:
            log.exception("generation failed")
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    """Run DOCX/PDF/TXT export off the UI thread (reportlab/python-docx can be slow)."""
    finished_ok = Signal(str)           # output path
    failed = Signal(str)

    def __init__(self, minutes_md: str, path: str, profile):
        super().__init__()
        self.minutes_md = minutes_md
        self.path = path
        self.profile = profile

    def run(self):
        try:
            from ..export import service
            out = service.export(self.minutes_md, self.path, self.profile)
            self.finished_ok.emit(str(out))
        except Exception as exc:
            log.exception("export failed")
            self.failed.emit(str(exc))


class EmailWorker(QThread):
    """Send an email off the UI thread (SMTP can block)."""
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, cfg, to, subject, body, html=None, attachments=None, cc=None):
        super().__init__()
        self.cfg = cfg
        self.to = to
        self.subject = subject
        self.body = body
        self.html = html
        self.attachments = attachments or []
        self.cc = cc

    def run(self):
        try:
            from ..core.emailer import send_email
            send_email(self.cfg, self.to, self.subject, self.body,
                       html=self.html, attachments=self.attachments, cc=self.cc)
            self.finished_ok.emit()
        except Exception as exc:
            log.exception("email send failed")
            self.failed.emit(str(exc))


class ModelPullWorker(QThread):
    """Download/install an Ollama model with progress (Settings page)."""
    progress = Signal(float, str)       # frac (0..1, -1 = indeterminate), status text
    finished_ok = Signal(str)           # model name
    failed = Signal(str)

    def __init__(self, host: str, model: str):
        super().__init__()
        self.host = host
        self.model = model
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        from ..core.ollama_client import pull_model
        try:
            def prog(frac, status, completed, total):
                self.progress.emit(-1.0 if frac is None else frac, status or "downloading")
            pull_model(self.host, self.model, progress=prog, cancel=lambda: self._cancel)
            self.finished_ok.emit(self.model)
        except InterruptedError:
            self.failed.emit("Cancelled.")
        except Exception as exc:
            log.exception("model pull failed")
            self.failed.emit(str(exc))


class UpdateCheckWorker(QThread):
    done = Signal(object)               # updater.UpdateInfo

    def __init__(self, repo: str):
        super().__init__()
        self.repo = repo

    def run(self):
        info = updater.check_for_updates(self.repo)
        self.done.emit(info)


class GitUpdateWorker(QThread):
    """Run a git check/pull off the UI thread (network + subprocess can block)."""
    done = Signal(dict)

    def __init__(self, action: str = "check"):   # "check" | "pull"
        super().__init__()
        self.action = action

    def run(self):
        from ..core import git_update
        try:
            res = git_update.pull_update() if self.action == "pull" else git_update.check_update()
        except Exception as exc:                 # never let the thread crash silently
            log.exception("git update worker failed")
            res = {"ok": False, "error": str(exc)}
        self.done.emit(res)


class UpdateDownloadWorker(QThread):
    progress = Signal(float, int, int)  # frac, read, total
    finished_ok = Signal(str)           # downloaded path (only after verification)
    failed = Signal(str)

    def __init__(self, info, dest: str):
        super().__init__()
        self.info = info                # updater.UpdateInfo (carries checksum metadata)
        self.url = info.download_url
        self.dest = dest
        self.verify_note = ""           # human-readable summary of what was checked
        self.signature_status = ""      # Authenticode status
        self.expected_sha256 = ""       # resolved expected hash ("" if none published)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            path = updater.download_asset(
                self.url, self.dest,
                progress=lambda f, r, t: self.progress.emit(f, r, t),
                cancel=lambda: self._cancel)
            self.progress.emit(1.0, 0, 0)
            # verify integrity BEFORE the file is ever offered to run
            self.expected_sha256 = updater.resolve_expected_sha256(self.info)
            self.verify_note, self.signature_status = updater.verify_download(
                path, self.expected_sha256)
            self.finished_ok.emit(path)
        except InterruptedError:
            self.failed.emit("Download cancelled.")
        except updater.IntegrityError as exc:
            # delete the bad file so it can never be executed
            try:
                Path(self.dest).unlink(missing_ok=True)
            except Exception:
                pass
            self.failed.emit(str(exc))
        except Exception as exc:
            log.exception("update download failed")
            self.failed.emit(str(exc))
