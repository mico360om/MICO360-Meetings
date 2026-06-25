"""Lightweight, offline speaker diarization.

Assigns "Speaker 1/2/3…" labels to Whisper segments by computing a small MFCC
voice embedding for each segment and clustering them (cosine distance,
average-linkage agglomerative, auto speaker count). Pure NumPy — no PyTorch,
no Hugging Face, no extra downloads. Approximate: good for a few clear
speakers, weaker on heavy crosstalk.
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


def _mfcc(signal: np.ndarray) -> np.ndarray:
    """Return (n_frames, n_mfcc) MFCCs for a 1-D signal."""
    if len(signal) < _FRAME:
        return np.zeros((0, _N_MFCC), dtype="float32")
    sig = np.append(signal[0], signal[1:] - 0.97 * signal[:-1])      # pre-emphasis
    n_frames = 1 + (len(sig) - _FRAME) // _HOP
    win = np.hamming(_FRAME).astype("float32")
    fb = _mel_filterbank()
    mfccs = np.empty((n_frames, _N_MFCC), dtype="float32")
    # DCT-II matrix
    dct = np.zeros((_N_MFCC, _N_MELS), dtype="float32")
    for k in range(_N_MFCC):
        dct[k] = np.cos(np.pi * k / _N_MELS * (np.arange(_N_MELS) + 0.5))
    for i in range(n_frames):
        frame = sig[i * _HOP: i * _HOP + _FRAME] * win
        mag = np.abs(np.fft.rfft(frame, _FFT)) ** 2 / _FFT
        mel = np.log(np.maximum(fb @ mag, 1e-10))
        mfccs[i] = dct @ mel
    return mfccs


def _embedding(signal: np.ndarray) -> np.ndarray:
    m = _mfcc(signal)
    if len(m) == 0:
        return np.zeros(_N_MFCC * 2, dtype="float32")
    emb = np.concatenate([m.mean(axis=0), m.std(axis=0)])
    n = np.linalg.norm(emb)
    return emb / n if n > 0 else emb


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(1.0 - np.dot(a, b))


def _agglomerative(embeddings: list[np.ndarray], threshold: float, max_k: int) -> list[int]:
    """Average-linkage agglomerative clustering on cosine distance."""
    clusters = [[i] for i in range(len(embeddings))]
    emb = embeddings

    def cdist(c1, c2):
        return np.mean([_cosine(emb[i], emb[j]) for i in c1 for j in c2])

    while len(clusters) > 1:
        best, bi, bj = 1e9, -1, -1
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = cdist(clusters[i], clusters[j])
                if d < best:
                    best, bi, bj = d, i, j
        if best > threshold and len(clusters) <= max_k:
            break
        if len(clusters) <= 1:
            break
        clusters[bi].extend(clusters[bj])
        clusters.pop(bj)
        if best > threshold and len(clusters) <= max_k:
            break
    labels = [0] * len(embeddings)
    for cid, members in enumerate(clusters):
        for idx in members:
            labels[idx] = cid
    return labels


def diarize(audio_path: str, segments, max_speakers: int = 4, threshold: float = 0.22) -> list[int]:
    """Return a speaker index (0-based) for each segment."""
    if len(segments) <= 1:
        return [0] * len(segments)
    try:
        audio = _load_audio(audio_path)
        if len(audio) == 0:
            return [0] * len(segments)
        embeddings = []
        for seg in segments:
            a = int(max(0, seg.start) * _SR)
            b = int(max(seg.start, seg.end) * _SR)
            embeddings.append(_embedding(audio[a:b]))
        labels = _agglomerative(embeddings, threshold, max_speakers)
        # relabel by first appearance so speakers are 0,1,2… in order
        order, remap = {}, {}
        for lab in labels:
            if lab not in remap:
                remap[lab] = len(remap)
        return [remap[lab] for lab in labels]
    except Exception:
        log.exception("diarization failed; returning single speaker")
        return [0] * len(segments)


def apply_to_segments(audio_path: str, segments, max_speakers: int = 4) -> int:
    """Diarize and write 'Speaker N' onto each segment. Returns speaker count."""
    labels = diarize(audio_path, segments, max_speakers)
    for seg, lab in zip(segments, labels):
        seg.speaker = f"Speaker {lab + 1}"
    return (max(labels) + 1) if labels else 0
