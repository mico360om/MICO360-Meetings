"""Housekeeping so the app stays light on storage over time.

The recorder and audio pipeline leave intermediate files (per-source captures,
temporary video/audio tracks, 16 kHz transcoding copies) in the temp directory.
Left alone they accumulate — a real problem on machines with little free disk.
`purge_tmp` removes those intermediates once they are older than a retention
window; it is age-based so a file still being written is never touched.

Finished recordings live in RECORDINGS_DIR and are NEVER purged here — they are
the user's files and stay until the user deletes them.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import RECORDINGS_DIR, TMP_DIR

log = logging.getLogger("mico360.maintenance")

# Intermediate artefacts produced by the recorder / audio pipeline (see
# core.recording, core.audio). Only these prefixes are purged, so nothing
# unexpected is removed — and never a finished `recording_*` file.
_TMP_PATTERNS = ("_src_*.wav", "_vid_*.mp4", "_aud_*.wav", "*_16k.wav")


def purge_tmp(older_than_days: float = 7, tmp_dir: Path | None = None,
              now: float | None = None) -> tuple[int, int]:
    """Delete recorder temp files older than `older_than_days`.

    Returns (files_removed, bytes_freed). Never raises — housekeeping must not
    disrupt startup. `older_than_days <= 0` disables purging (returns 0, 0).
    """
    if older_than_days <= 0:
        return (0, 0)
    root = Path(tmp_dir) if tmp_dir is not None else TMP_DIR
    cutoff = (now if now is not None else time.time()) - older_than_days * 86400
    removed = freed = 0
    for pattern in _TMP_PATTERNS:
        for f in root.glob(pattern):
            try:
                st = f.stat()
                if st.st_mtime >= cutoff:
                    continue                 # too recent — may be in use
                size = st.st_size
                f.unlink()
                removed += 1
                freed += size
            except OSError:
                continue                     # locked / already gone — skip
    # e-mail attachment folders (normally removed as soon as the mail is sent)
    for d in root.glob("email_*"):
        try:
            if not d.is_dir() or d.stat().st_mtime >= cutoff:
                continue
            for f in d.iterdir():
                size = f.stat().st_size
                f.unlink()
                removed += 1
                freed += size
            d.rmdir()
        except OSError:
            continue
    if removed:
        log.info("purged %d stale temp file(s), freed %.1f MB", removed, freed / 1e6)
    return (removed, freed)


def adopt_legacy_recordings(tmp_dir: Path | None = None,
                            dest_dir: Path | None = None) -> int:
    """Move finished recordings left in the temp folder by older versions
    (which saved them there and auto-deleted them after a week) into the
    permanent recordings folder. Returns how many were moved. Never raises."""
    src = Path(tmp_dir) if tmp_dir is not None else TMP_DIR
    dst = Path(dest_dir) if dest_dir is not None else RECORDINGS_DIR
    moved = 0
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for pattern in ("recording_*.wav", "recording_*.mp3", "recording_*.mp4"):
            for f in src.glob(pattern):
                target = dst / f.name
                if target.exists():
                    continue
                try:
                    f.replace(target)
                    moved += 1
                except OSError:
                    continue                 # in use — try again next start
    except Exception:
        log.warning("could not move legacy recordings", exc_info=True)
    if moved:
        log.info("moved %d recording(s) from the temp folder to %s", moved, dst)
    return moved
