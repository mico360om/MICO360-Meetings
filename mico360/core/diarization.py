"""Lightweight, offline speaker diarization.

Assigns "Speaker 1/2/3…" labels to Whisper segments: each segment's voice is
the average of its MFCCs (without c0, i.e. without loudness), normalised per
coefficient over the whole recording; segments are compared by how far apart
those averages are relative to their standard error (longer segments carry
more evidence) and grouped by average-linkage agglomerative clustering with an
automatic speaker count. Pure NumPy — no PyTorch, no Hugging Face, no extra
downloads. Approximate: good for a few speakers taking turns of a few seconds,
weaker on heavy crosstalk and very short turns (those stay with one speaker).
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("mico360.diarization")

_SR = 16000
_N_MFCC = 13
_N_MELS = 26
_FRAME = 400          # 25 ms @ 16k
_HOP = 160            # 10 ms
_FFT = 512


# ---------------------------------------------------------------------------
def _load_audio(path: str) -> np.ndarray:
    """Load any media as 16 kHz mono float32 (uses the same PyAV path as Whisper)."""
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        if sr != _SR and len(data):
            n = int(len(data) * _SR / sr)
            data = np.interp(np.linspace(0, 1, n, endpoint=False),
                             np.linspace(0, 1, len(data), endpoint=False), data).astype("float32")
        return data
    except Exception:
        # fall back to PyAV (handles mp4/m4a/etc.)
        import av
        out = []
        with av.open(path) as c:
            stream = next((s for s in c.streams if s.type == "audio"), None)
            if stream is None:
                return np.zeros(0, dtype="float32")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=_SR)
            for frame in c.decode(stream):
                for rf in resampler.resample(frame):
                    out.append(rf.to_ndarray().reshape(-1).astype("float32") / 32768.0)
        return np.concatenate(out) if out else np.zeros(0, dtype="float32")


_MEL_FB = None


def _mel_filterbank() -> np.ndarray:
    global _MEL_FB
    if _MEL_FB is not None:
        return _MEL_FB

    def hz2mel(f):
        return 2595.0 * np.log10(1 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1)

    low, high = hz2mel(0), hz2mel(_SR / 2)
    pts = mel2hz(np.linspace(low, high, _N_MELS + 2))
    bins = np.floor((_FFT + 1) * pts / _SR).astype(int)
    fb = np.zeros((_N_MELS, _FFT // 2 + 1), dtype="float32")
    for m in range(1, _N_MELS + 1):
        a, b, c = bins[m - 1], bins[m], bins[m + 1]
        for k in range(a, b):
            if b > a:
                fb[m - 1, k] = (k - a) / (b - a)
        for k in range(b, c):
            if c > b:
                fb[m - 1, k] = (c - k) / (c - b)
    _MEL_FB = fb
    return fb


def _dct_matrix() -> np.ndarray:
    dct = np.zeros((_N_MFCC, _N_MELS), dtype="float32")
    for k in range(_N_MFCC):
        dct[k] = np.cos(np.pi * k / _N_MELS * (np.arange(_N_MELS) + 0.5))
    return dct


_DCT = None
_WIN = None
_MFCC_CHUNK = 2048          # frames per vectorised FFT batch (bounds memory)


def _mfcc(signal: np.ndarray) -> np.ndarray:
    """Return (n_frames, n_mfcc) MFCCs for a 1-D signal (vectorised: one batched
    FFT per chunk of frames instead of a Python loop per frame)."""
    global _DCT, _WIN
    if len(signal) < _FRAME:
        return np.zeros((0, _N_MFCC), dtype="float32")
    if _DCT is None:
        _DCT = _dct_matrix()
        _WIN = np.hamming(_FRAME).astype("float32")
    sig = np.asarray(signal, dtype="float32")
    sig = np.append(sig[0], sig[1:] - 0.97 * sig[:-1]).astype("float32")   # pre-emphasis
    n_frames = 1 + (len(sig) - _FRAME) // _HOP
    frames = np.lib.stride_tricks.sliding_window_view(sig, _FRAME)[::_HOP][:n_frames]
    fb = _mel_filterbank()
    out = np.empty((n_frames, _N_MFCC), dtype="float32")
    for s in range(0, n_frames, _MFCC_CHUNK):
        blk = frames[s:s + _MFCC_CHUNK] * _WIN
        mag = np.abs(np.fft.rfft(blk, _FFT, axis=1)) ** 2 / _FFT
        mel = np.log(np.maximum(mag @ fb.T, 1e-10))
        out[s:s + len(blk)] = mel @ _DCT.T
    return out


def _embedding(signal: np.ndarray) -> np.ndarray:
    m = _mfcc(signal)
    if len(m) == 0:
        return np.zeros(_N_MFCC * 2, dtype="float32")
    emb = np.concatenate([m.mean(axis=0), m.std(axis=0)])
    n = np.linalg.norm(emb)
    return emb / n if n > 0 else emb


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(1.0 - np.dot(a, b))


# M38: segment-mean distances are divided by their standard error and by this
# many frames per second, so the threshold reads as "separation per second of
# speech"; one voice stays well under it at any loudness, different voices
# taking turns of ~3 s or more are well over it.
_FRAMES_PER_SECOND = _SR / _HOP
VOICE_THRESHOLD = 0.05


def _voice_stats(audio: np.ndarray, segments, cancel=None):
    """Per-segment mean MFCC (c1..c12, normalised over the whole recording) and
    frame count. Segments too short for one frame get count 0."""
    means, counts = [], []
    s1 = np.zeros(_N_MFCC - 1)
    s2 = np.zeros(_N_MFCC - 1)
    total = 0
    for n, seg in enumerate(segments):
        if n % 25 == 0:
            _check_cancel(cancel)
        a = int(max(0, seg.start) * _SR)
        b = int(max(seg.start, seg.end) * _SR)
        m = _mfcc(audio[a:b])[:, 1:].astype(np.float64)     # c0 = loudness, not voice
        if len(m):
            means.append(m.mean(axis=0))
            counts.append(len(m))
            s1 += m.sum(axis=0)
            s2 += (m * m).sum(axis=0)
            total += len(m)
        else:
            means.append(np.zeros(_N_MFCC - 1))
            counts.append(0)
    if total == 0:
        return np.zeros((len(segments), _N_MFCC - 1)), np.zeros(len(segments))
    mu = s1 / total
    sd = np.sqrt(np.maximum(s2 / total - mu * mu, 1e-12))
    return (np.asarray(means) - mu) / sd, np.asarray(counts, dtype=np.float64)


def _voice_distances(M: np.ndarray, N: np.ndarray) -> np.ndarray:
    """Separation of segment voices: RMS difference of the normalised means
    divided by its standard error, per second of speech."""
    sq = (M * M).sum(axis=1)                     # |a-b|² = |a|² + |b|² - 2a·b: n×n memory only
    diff = np.sqrt(np.maximum(sq[:, None] + sq[None, :] - 2.0 * (M @ M.T), 0.0) / M.shape[1])
    se = np.sqrt(1.0 / N[:, None] + 1.0 / N[None, :])
    return diff / se / _FRAMES_PER_SECOND


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel():
        raise InterruptedError("Transcription cancelled.")


def _agglomerative(embeddings, threshold: float, max_k: int, cancel=None,
                   distances: np.ndarray | None = None) -> list[int]:
    """Average-linkage agglomerative clustering on cosine distance.

    Vectorised (H11): one distance matrix, updated in place with the
    Lance–Williams average-linkage rule after each merge, so every step is a
    NumPy argmin + two row updates instead of an O(n²) Python double loop that
    recomputed every pairwise mean. Same merges and stopping rule as before.
    `cancel()` is checked every step. `distances` (n×n) replaces the cosine
    distances of `embeddings` when given (M38)."""
    n = len(embeddings) if distances is None else len(distances)
    if n == 0:
        return []
    if n == 1:
        return [0]
    if distances is None:
        X = np.asarray(embeddings, dtype=np.float64).reshape(n, -1)
        D = 1.0 - X @ X.T
    else:
        D = np.array(distances, dtype=np.float64)
    np.fill_diagonal(D, np.inf)
    sizes = np.ones(n, dtype=np.float64)
    members: list[list[int]] = [[i] for i in range(n)]
    k = n
    while k > 1:
        _check_cancel(cancel)
        flat = int(np.argmin(D))                    # first minimum = lowest (i, j), i < j
        i, j = divmod(flat, n)
        if i > j:
            i, j = j, i
        best = float(D[i, j])
        if best > threshold and k <= max_k:
            break
        si, sj = sizes[i], sizes[j]
        row = (si * D[i] + sj * D[j]) / (si + sj)   # Lance–Williams, average linkage
        D[i, :] = row
        D[:, i] = row
        D[i, i] = np.inf
        D[j, :] = np.inf
        D[:, j] = np.inf
        sizes[i] = si + sj
        members[i].extend(members[j])
        members[j] = []
        k -= 1
        if best > threshold and k <= max_k:
            break
    labels = [0] * n
    cid = 0
    for group in members:
        if not group:
            continue
        for idx in group:
            labels[idx] = cid
        cid += 1
    return labels


# Clustering is O(n²) memory / ~O(n²)–O(n³) time: very long meetings are
# clustered on their longest (most reliable) segments and the rest are assigned
# to the nearest speaker.
_MAX_CLUSTER_ITEMS = 1200


def _cluster(embeddings, durations, threshold: float, max_k: int, cancel=None) -> list[int]:
    n = len(embeddings)
    if n <= _MAX_CLUSTER_ITEMS:
        return _agglomerative(embeddings, threshold, max_k, cancel)
    X = np.asarray(embeddings, dtype=np.float64).reshape(n, -1)
    keep = np.sort(np.argsort(-np.asarray(durations, dtype=np.float64), kind="stable")
                   [:_MAX_CLUSTER_ITEMS])
    sub = _agglomerative(X[keep], threshold, max_k, cancel)
    _check_cancel(cancel)
    n_clusters = max(sub) + 1
    cent = np.zeros((n_clusters, X.shape[1]))
    for row, lab in zip(keep, sub):
        cent[lab] += X[row]
    norms = np.linalg.norm(cent, axis=1, keepdims=True)
    cent = cent / np.where(norms > 0, norms, 1.0)
    labels = np.argmax(X @ cent.T, axis=1)
    labels[keep] = sub
    return [int(x) for x in labels]


def _cluster_voices(M: np.ndarray, N: np.ndarray, durations, threshold: float, max_k: int,
                    cancel=None) -> list[int]:
    """M38: cluster segment voices (see _voice_stats). Segments without a single
    frame join the speaker before them. Very long meetings are clustered on
    their longest segments; the rest join the nearest speaker's average voice."""
    n = len(M)
    labels = np.zeros(n, dtype=int)
    real = np.flatnonzero(N > 0)
    if len(real) <= 1:
        return [0] * n
    keep = real
    if len(real) > _MAX_CLUSTER_ITEMS:
        dur = np.asarray(durations, dtype=np.float64)[real]
        keep = np.sort(real[np.argsort(-dur, kind="stable")[:_MAX_CLUSTER_ITEMS]])
    sub = np.asarray(_agglomerative(None, threshold, max_k, cancel,
                                    distances=_voice_distances(M[keep], N[keep])))
    _check_cancel(cancel)
    labels[keep] = sub
    if len(keep) < len(real):
        k = int(sub.max()) + 1
        w = N[keep]
        cent = np.stack([(M[keep][sub == c] * w[sub == c, None]).sum(0) / w[sub == c].sum()
                         for c in range(k)])
        cn = np.array([w[sub == c].sum() for c in range(k)])
        rest = np.setdiff1d(real, keep)
        d = _voice_distances(np.vstack([M[rest], cent]), np.concatenate([N[rest], cn]))
        labels[rest] = np.argmin(d[:len(rest), len(rest):], axis=1)
    last = labels[real[0]]
    for i in range(n):                       # frame-less snippets: previous speaker
        if N[i] > 0:
            last = labels[i]
        else:
            labels[i] = last
    return [int(x) for x in labels]


def diarize(audio_path: str, segments, max_speakers: int = 4,
            threshold: float = VOICE_THRESHOLD, cancel=None) -> list[int]:
    """Return a speaker index (0-based) for each segment. Raises InterruptedError
    if `cancel()` becomes true."""
    if len(segments) <= 1:
        return [0] * len(segments)
    try:
        audio = _load_audio(audio_path)
        if len(audio) == 0:
            return [0] * len(segments)
        M, N = _voice_stats(audio, segments, cancel)
        durations = [max(0.0, float(seg.end) - float(seg.start)) for seg in segments]
        labels = _cluster_voices(M, N, durations, threshold, max_speakers, cancel)
        # relabel by first appearance so speakers are 0,1,2… in order
        remap: dict[int, int] = {}
        for lab in labels:
            if lab not in remap:
                remap[lab] = len(remap)
        return [remap[lab] for lab in labels]
    except InterruptedError:
        raise
    except Exception:
        log.exception("diarization failed; returning single speaker")
        return [0] * len(segments)


def apply_to_segments(audio_path: str, segments, max_speakers: int = 4, cancel=None) -> int:
    """Diarize and write 'Speaker N' onto each segment. Returns speaker count."""
    labels = diarize(audio_path, segments, max_speakers, cancel=cancel)
    for seg, lab in zip(segments, labels):
        seg.speaker = f"Speaker {lab + 1}"
    return (max(labels) + 1) if labels else 0
