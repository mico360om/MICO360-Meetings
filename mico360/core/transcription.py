"""Offline speech-to-text via faster-whisper.

The model is loaded lazily and cached. Transcription reports progress through
a callback (0.0–1.0) derived from segment end-time vs. total duration.
"""
from __future__ import annotations

import logging
import threading
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

# Approximate one-time download size per model, for first-run reassurance.
APPROX_SIZE = {
    "tiny": "75 MB", "base": "145 MB", "small": "480 MB",
    "medium": "1.5 GB", "large-v3": "3 GB", "large": "3 GB",
}


def model_cached(size: str) -> bool:
    """Whether the faster-whisper model for `size` is already downloaded locally
    (so we can tell a first-run download from a fast warm load)."""
    try:
        if not MODELS_DIR.exists():
            return False
        for d in MODELS_DIR.iterdir():
            if d.is_dir() and size in d.name and next(d.rglob("*.bin"), None):
                return True
    except Exception:
        pass
    return False


def _whisper_lang(language) -> str | None:
    """Setting value -> faster-whisper code, or None for auto-detect. Accepts
    "AR", "ar-SA", "Arabic", "Auto" (the engine used to receive these raw and
    faster-whisper rejected them, failing every transcription)."""
    from .languages import normalize_language
    code = normalize_language(language)
    return None if code == "auto" else code


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


_GPU_ERR_KEYS = ("cublas", "cuda", "cudnn", "cudart", "gpu")


def _is_gpu_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in _GPU_ERR_KEYS)


class TranscriptionEngine:
    """`device` / `compute_type` stay what the user asked for (the app context
    compares them with the settings to decide whether to rebuild the engine).
    When the GPU turns out to be unusable the engine falls back to CPU and
    REMEMBERS it (`effective_device`), so it isn't rebuilt on CUDA and failing
    again for every file (M33)."""

    def __init__(self, model_size: str = "base", device: str = "auto", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._loaded_key: tuple | None = None
        self._cpu_fallback = False
        self._lock = threading.RLock()          # file + live transcription share the model

    # -- effective device -----------------------------------------------------
    def _effective(self) -> tuple[str, str]:
        if self._cpu_fallback:
            return "cpu", "int8"
        # Device selection is conservative: "auto" means CPU. GPU (CUDA) needs
        # the cuBLAS/cuDNN runtime DLLs which we do NOT bundle, so auto-selecting
        # CUDA on a machine without them fails with "cublas64_12.dll not found".
        # Only honour CUDA when the user explicitly asks for it — and even then
        # fall back to CPU if the CUDA libraries can't be loaded.
        device = "cpu" if self.device in ("auto", "", None) else self.device
        compute = self.compute_type
        if device == "cpu" and compute in ("float16", "fp16"):
            compute = "int8"  # fp16 is not supported on CPU
        return device, compute

    @property
    def effective_device(self) -> str:
        return self._effective()[0]

    @property
    def using_fallback(self) -> bool:
        return self._cpu_fallback

    def _fall_back_to_cpu(self, exc: BaseException, progress: ProgressCb | None = None,
                          frac: float = 0.06) -> None:
        log.warning("Whisper on '%s' failed (%s); using CPU/int8 from now on.",
                    self.effective_device, exc)
        if progress:
            progress(frac, "GPU unavailable — using CPU…")
        with self._lock:
            self._cpu_fallback = True
            self._model = None
            self._loaded_key = None

    # -- model lifecycle ----------------------------------------------------
    def _key(self) -> tuple:
        return (self.model_size,) + self._effective()

    def load(self, progress: ProgressCb | None = None):
        """Load (or reuse) the model and return it. K9: callers transcribe with
        the returned reference — another thread (live transcription falling
        back to CPU) may reset `self._model` at any time."""
        from faster_whisper import WhisperModel

        with self._lock:
            if self._model is not None and self._loaded_key == self._key():
                return self._model
            if progress:
                if model_cached(self.model_size):
                    progress(0.02, f"Loading the Whisper '{self.model_size}' model…")
                else:
                    approx = APPROX_SIZE.get(self.model_size, "a few hundred MB")
                    progress(0.02, f"Downloading the Whisper '{self.model_size}' model "
                                   f"(~{approx}, first run — this can take a minute)…")

            device, compute = self._effective()

            def _make(dev, comp):
                log.info("loading whisper model=%s device=%s compute=%s", self.model_size, dev, comp)
                m = WhisperModel(self.model_size, device=dev, compute_type=comp,
                                 download_root=str(MODELS_DIR))
                try:
                    m._mico360_device = dev
                except Exception:
                    pass
                return m

            try:
                self._model = _make(device, compute)
            except Exception as exc:
                if device != "cpu":
                    self._fall_back_to_cpu(exc, progress, 0.03)
                    self._model = _make(*self._effective())
                else:
                    raise
            self._loaded_key = self._key()
            return self._model

    # -- transcription ------------------------------------------------------
    def transcribe_file(
        self,
        path: str | Path,
        language: str | None = None,
        progress: ProgressCb | None = None,
        cancel: Callable[[], bool] | None = None,
        diarize: bool = False,
    ) -> TranscriptResult:
        model = self.load(progress)
        if progress:
            progress(0.05, "Preparing audio…")
        usable, duration = audio.prepare_for_whisper(path)

        lang = _whisper_lang(language)
        if progress:
            progress(0.08, "Transcribing…")

        try:
            result = self._run(usable, lang, duration, progress, cancel, model=model)
        except InterruptedError:
            raise
        except Exception as exc:
            # CUDA/cuBLAS/cuDNN errors surface lazily during decode — fall back
            # to CPU (for good) and retry once so transcription still succeeds.
            # (The live worker may already have switched the engine to CPU, so
            # judge by the device of the model that actually failed.)
            if _is_gpu_error(exc) and self._model_device(model) != "cpu":
                if self.effective_device != "cpu":
                    self._fall_back_to_cpu(exc, progress)
                model = self.load(progress)
                result = self._run(usable, lang, duration, progress, cancel, model=model)
            else:
                raise

        if diarize and result.segments:
            try:
                if progress:
                    progress(0.99, "Identifying speakers…")
                from . import diarization
                result.speakers = diarization.apply_to_segments(
                    str(usable), result.segments, cancel=cancel)
            except InterruptedError:
                raise
            except Exception:
                log.warning("diarization failed", exc_info=True)
        return result

    def transcribe_array(self, audio_f32, language: str | None = None) -> str:
        """Transcribe a mono 16 kHz float32 numpy array to text — for live capture
        (fast: greedy decoding, VAD-filtered, no progress/diarisation). Falls back
        to CPU like transcribe_file when the GPU turns out to be unusable."""
        model = self.load()
        lang = _whisper_lang(language)
        try:
            return self._run_array(audio_f32, lang, model=model)
        except Exception as exc:
            if _is_gpu_error(exc) and self._model_device(model) != "cpu":
                if self.effective_device != "cpu":
                    self._fall_back_to_cpu(exc)
                model = self.load()
                return self._run_array(audio_f32, lang, model=model)
            raise

    def _model_device(self, model) -> str:
        """Device a loaded model runs on (tagged at load time)."""
        return getattr(model, "_mico360_device", None) or self.effective_device

    def _run_array(self, audio_f32, lang, model=None) -> str:
        model = model if model is not None else self.load()
        segments, _info = model.transcribe(
            audio_f32, language=lang, vad_filter=True, beam_size=1)
        return "".join(seg.text for seg in segments).strip()

    def _run(self, usable, lang, duration, progress, cancel, model=None) -> TranscriptResult:
        model = model if model is not None else self.load()
        segments_iter, info = model.transcribe(
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
