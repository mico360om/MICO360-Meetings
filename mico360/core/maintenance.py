"""Housekeeping so the app stays light on storage over time.

Recordings and their intermediate files live in the temp directory and are not
referenced once a meeting's transcript/minutes are saved to history. Left alone
they accumulate — a real problem on machines with little free disk. `purge_tmp`
removes temp files older than a retention window; it is age-based so a file that
is currently being written (recording in progress) is never touched.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import TMP_DIR

log = logging.getLogger("mico360.maintenance")

# Temp artefacts produced by the recorder / audio pipeline (see core.recording,
# core.audio). Only these prefixes are purged, so nothing unexpected is removed.
_TMP_PATTERNS = ("recording_*.wav", "recording_*.mp3", "recording_*.mp4",
                 "_src_*.wav", "_vid_*.mp4", "_aud_*.wav", "*_16k.wav")


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
    if removed:
        log.info("purged %d stale temp file(s), freed %.1f MB", removed, freed / 1e6)
    return (removed, freed)
