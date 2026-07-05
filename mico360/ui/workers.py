"""Background workers (QThread) for transcription, generation and recording,
so the UI never blocks. Progress and results are delivered via Qt signals.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..config import TMP_DIR
from ..core.ollama_client import OllamaGenerator
from ..core.transcription import TranscriptionEngine, TranscriptResult
from ..core import updater

log = logging.getLogger("mico360.workers")


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


class UpdateDownloadWorker(QThread):
    progress = Signal(float, int, int)  # frac, read, total
    finished_ok = Signal(str)           # downloaded path
    failed = Signal(str)

    def __init__(self, url: str, dest: str):
        super().__init__()
        self.url = url
        self.dest = dest
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            path = updater.download_asset(
                self.url, self.dest,
                progress=lambda f, r, t: self.progress.emit(f, r, t),
                cancel=lambda: self._cancel)
            self.finished_ok.emit(path)
        except InterruptedError:
            self.failed.emit("Download cancelled.")
        except Exception as exc:
            log.exception("update download failed")
            self.failed.emit(str(exc))


class RecorderThread(QThread):
    """Microphone recorder. Writes a 16 kHz mono WAV to the temp dir."""
    level = Signal(float)               # 0..1 input level for a VU meter
    elapsed = Signal(float)             # seconds
    finished_ok = Signal(str)           # wav path
    failed = Signal(str)

    def __init__(self, samplerate: int = 16000):
        super().__init__()
        self.samplerate = samplerate
        self._stop = False
        self.out_path = str(TMP_DIR / f"recording_{int(time.time())}.wav")

    def stop(self):
        self._stop = True

    def run(self):
        try:
            import numpy as np
            import sounddevice as sd
            import soundfile as sf

            TMP_DIR.mkdir(parents=True, exist_ok=True)
            start = time.time()
            with sf.SoundFile(self.out_path, mode="w", samplerate=self.samplerate,
                              channels=1, subtype="PCM_16") as f:
                with sd.InputStream(samplerate=self.samplerate, channels=1,
                                    dtype="float32", blocksize=2048) as stream:
                    while not self._stop:
                        data, _ = stream.read(2048)
                        f.write(data)
                        self.level.emit(float(min(1.0, float(np.abs(data).max()) * 1.5)))
                        self.elapsed.emit(time.time() - start)
            self.finished_ok.emit(self.out_path)
        except Exception as exc:
            log.exception("recording failed")
            self.failed.emit(str(exc))
