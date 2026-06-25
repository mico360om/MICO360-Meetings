"""Offline speech-to-text via faster-whisper.

The model is loaded lazily and cached. Transcription reports progress through
a callback (0.0–1.0) derived from segment end-time vs. total duration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import MODELS_DIR
from . import audio

log = logging.getLogger("mico360.transcription")

ProgressCb = Callable[[float, str], None]

# (label, whisper size, approx download)
WHISPER_MODELS = [
    ("Tiny  (~75 MB, fastest)", "tiny"),
    ("Base  (~145 MB, balanced)", "base"),
    ("Small (~480 MB, better)", "small"),
    ("Medium (~1.5 GB, accurate)", "medium"),
    ("Large-v3 (~3 GB, best)", "large-v3"),
]


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass
class TranscriptResult:
    text: str
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    speakers: int = 0

    def as_plain(self) -> str:
        return self.text.strip()

    def as_speaker_text(self) -> str:
        """Transcript grouped by speaker, e.g. '[Speaker 1] …'. Falls back to plain."""
        if not self.speakers or not any(s.speaker for s in self.segments):
            return self.as_plain()
        lines, cur_spk, buf = [], None, []
        for seg in self.segments:
            spk = seg.speaker or "Speaker ?"
            if spk != cur_spk:
                if buf:
                    lines.append(f"[{cur_spk}] {' '.join(buf).strip()}")
                cur_spk, buf = spk, []
            buf.append(seg.text.strip())
        if buf:
            lines.append(f"[{cur_spk}] {' '.join(buf).strip()}")
        return "\n".join(lines)

    def as_timestamped(self) -> str:
        def ts(s: float) -> str:
            m, sec = divmod(int(s), 60)
            h, m = divmod(m, 60)
            return f"{h:02d}:{m:02d}:{sec:02d}"
        lines = []
        for seg in self.segments:
            who = f"{seg.speaker}: " if seg.speaker else ""
            lines.append(f"[{ts(seg.start)}] {who}{seg.text.strip()}")
        return "\n".join(lines)


class TranscriptionEngine:
    def __init__(self, model_size: str = "base", device: str = "auto", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._loaded_key: tuple | None = None

    # -- model lifecycle ----------------------------------------------------
    def _key(self) -> tuple:
        return (self.model_size, self.device, self.compute_type)

    def load(self, progress: ProgressCb | None = None) -> None:
        from faster_whisper import WhisperModel

        if self._model is not None and self._loaded_key == self._key():
            return
        if progress:
            progress(0.02, f"Loading Whisper '{self.model_size}' model…")

        # Device selection is conservative: "auto" means CPU. GPU (CUDA) needs
        # the cuBLAS/cuDNN runtime DLLs which we do NOT bundle, so auto-selecting
        # CUDA on a machine without them fails with "cublas64_12.dll not found".
        # Only honour CUDA when the user explicitly asks for it — and even then
        # fall back to CPU if the CUDA libraries can't be loaded.
        device = "cpu" if self.device in ("auto", "", None) else self.device
        compute = self.compute_type
        if device == "cpu" and compute in ("float16", "fp16"):
            compute = "int8"  # fp16 is not supported on CPU

        def _make(dev, comp):
            log.info("loading whisper model=%s device=%s compute=%s", self.model_size, dev, comp)
            return WhisperModel(self.model_size, device=dev, compute_type=comp,
                                download_root=str(MODELS_DIR))

        try:
            self._model = _make(device, compute)
        except Exception as exc:
            if device != "cpu":
                log.warning("Whisper on '%s' failed (%s); falling back to CPU/int8.", device, exc)
                if progress:
                    progress(0.03, "GPU unavailable — using CPU…")
                device, compute = "cpu", "int8"
                self._model = _make(device, compute)
            else:
                raise
        self._loaded_key = self._key()

    # -- transcription ------------------------------------------------------
    def transcribe_file(
        self,
        path: str | Path,
        language: str | None = None,
        progress: ProgressCb | None = None,
        cancel: Callable[[], bool] | None = None,
        diarize: bool = False,
    ) -> TranscriptResult:
        self.load(progress)
        if progress:
            progress(0.05, "Preparing audio…")
        usable, duration = audio.prepare_for_whisper(path)

        lang = None if (not language or language == "auto") else language
        if progress:
            progress(0.08, "Transcribing…")

        try:
            result = self._run(usable, lang, duration, progress, cancel)
        except Exception as exc:
            # CUDA/cuBLAS/cuDNN errors surface lazily during decode — fall back
            # to CPU and retry once so transcription still succeeds.
            msg = str(exc).lower()
            gpu_err = any(k in msg for k in ("cublas", "cuda", "cudnn", "cudart", "gpu"))
            if gpu_err and self.device != "cpu":
                log.warning("GPU decode failed (%s); retrying on CPU/int8.", exc)
                if progress:
                    progress(0.06, "GPU unavailable — retrying on CPU…")
                self._model = None
                self._loaded_key = None
                self.device, self.compute_type = "cpu", "int8"
                self.load(progress)
                result = self._run(usable, lang, duration, progress, cancel)
            else:
                raise

        if diarize and result.segments:
            try:
                if progress:
                    progress(0.99, "Identifying speakers…")
                from . import diarization
                result.speakers = diarization.apply_to_segments(str(usable), result.segments)
            except Exception:
                log.warning("diarization failed", exc_info=True)
        return result

    def _run(self, usable, lang, duration, progress, cancel) -> TranscriptResult:
        segments_iter, info = self._model.transcribe(  # type: ignore[union-attr]
            str(usable),
            language=lang,
            vad_filter=True,
            beam_size=5,
        )
        total = duration or float(getattr(info, "duration", 0.0)) or 0.0
        detected = getattr(info, "language", "") or (lang or "")

        segs: list[Segment] = []
        parts: list[str] = []
        for seg in segments_iter:
            if cancel and cancel():
                raise InterruptedError("Transcription cancelled.")
            segs.append(Segment(start=seg.start, end=seg.end, text=seg.text))
            parts.append(seg.text)
            if progress and total > 0:
                frac = 0.08 + 0.9 * min(seg.end / total, 1.0)
                progress(min(frac, 0.98), "Transcribing…")

        if progress:
            progress(1.0, "Transcription complete.")
        return TranscriptResult(
            text="".join(parts).strip(),
            segments=segs,
            language=detected,
            duration=total,
        )
