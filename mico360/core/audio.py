"""Audio/video handling built on PyAV (no system ffmpeg required).

faster-whisper can decode most containers directly, but some `.m4a`/`.mov`
files fail with cryptic errors. We therefore probe every file first and, when
needed, transcode to a clean 16 kHz mono WAV that Whisper always accepts.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import TMP_DIR

log = logging.getLogger("mico360.audio")

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

TARGET_RATE = 16_000


def is_media_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTS


def probe(path: str | Path) -> dict:
    """Return basic stream info; raises ValueError if no audio stream exists."""
    import av  # local import keeps startup fast

    path = str(path)
    with av.open(path) as container:
        astreams = [s for s in container.streams if s.type == "audio"]
        if not astreams:
            raise ValueError("No audio stream found in this file.")
        a = astreams[0]
        duration = float(container.duration / 1_000_000) if container.duration else 0.0
        return {
            "duration": duration,
            "sample_rate": getattr(a.codec_context, "sample_rate", None),
            "channels": getattr(a.codec_context, "channels", None),
            "codec": a.codec_context.name if a.codec_context else "unknown",
            "has_video": any(s.type == "video" for s in container.streams),
        }


def to_wav16k(path: str | Path, out_path: str | Path | None = None) -> Path:
    """Decode any supported media to 16 kHz mono PCM WAV.

    Done in pure PyAV so it works on machines without ffmpeg on PATH.
    """
    import av

    src = Path(path)
    if out_path is None:
        out_path = TMP_DIR / (src.stem + "_16k.wav")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("transcoding %s -> %s", src.name, out_path.name)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)

    with av.open(str(src)) as in_c, av.open(str(out_path), mode="w") as out_c:
        in_stream = next((s for s in in_c.streams if s.type == "audio"), None)
        if in_stream is None:
            raise ValueError("No audio stream found in this file.")
        out_stream = out_c.add_stream("pcm_s16le", rate=TARGET_RATE)
        out_stream.layout = "mono"

        for frame in in_c.decode(in_stream):
            frame.pts = None
            for rframe in resampler.resample(frame):
                for packet in out_stream.encode(rframe):
                    out_c.mux(packet)
        # flush
        for packet in out_stream.encode(None):
            out_c.mux(packet)

    return out_path


def prepare_for_whisper(path: str | Path) -> tuple[Path, float]:
    """Return a path Whisper can read + duration in seconds.

    Tries the original file; if probing fails or the container is unusual,
    transcodes to WAV. Returns (usable_path, duration_seconds).
    """
    src = Path(path)
    try:
        info = probe(src)
        # WAV/MP3/FLAC are safest to pass through directly.
        if src.suffix.lower() in {".wav", ".mp3", ".flac", ".ogg"}:
            return src, info.get("duration", 0.0)
        # Everything else (m4a, mp4, mov, mkv, aac...) -> normalize to WAV.
        wav = to_wav16k(src)
        return wav, info.get("duration", 0.0)
    except Exception as exc:
        log.warning("probe failed for %s (%s); attempting transcode", src.name, exc)
        wav = to_wav16k(src)
        try:
            dur = probe(wav).get("duration", 0.0)
        except Exception:
            dur = 0.0
        return wav, dur
