"""Recording engine: microphone audio, screen capture, and webcam — each with
optional microphone audio. Built on sounddevice (audio), mss (screen), OpenCV
(camera) and PyAV (encoding/muxing).

Design notes
------------
* Every recorder owns its own capture thread(s) and exposes a small, pollable
  stats surface (state, elapsed, byte size, level) so the UI can drive a live
  details panel + audio visualizer with a simple timer.
* Video is captured to a temporary video-only file while microphone audio is
  captured to a temporary WAV; on stop they are muxed into the final MP4. This
  is far more robust than live A/V interleaving and avoids the classic
  "valid file but black/empty" failure mode (we verify frames were written).
* Pause/Resume freezes the elapsed clock and stops writing frames/samples.
"""
from __future__ import annotations

import logging
import threading
import time
import wave
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..config import RECORDINGS_DIR, TMP_DIR

log = logging.getLogger("mico360.recording")

# Recording states
IDLE, RECORDING, PAUSED, STOPPED, CANCELLED, ERROR = (
    "idle", "recording", "paused", "stopped", "cancelled", "error")


# ---------------------------------------------------------------------------
# Microphone detection
# ---------------------------------------------------------------------------
@dataclass
class MicInfo:
    index: int
    name: str
    channels: int


def _query_devices_safe(timeout: float = 4.0):
    """Enumerate devices with a timeout so a wedged audio subsystem can't freeze
    the UI/startup. Returns the device list, or None on timeout/error."""
    result: dict = {}

    def run():
        try:
            import sounddevice as sd
            result["d"] = list(sd.query_devices())
        except Exception as exc:
            result["err"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        log.warning("audio device enumeration timed out after %ss", timeout)
        return None
    return result.get("d")


def list_microphones() -> list[MicInfo]:
    """Return available input devices (de-duplicated by name)."""
    devices = _query_devices_safe()
    if not devices:
        return []
    seen: dict[str, MicInfo] = {}
    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            name = d["name"].strip()
            if name not in seen:
                seen[name] = MicInfo(i, name, int(d["max_input_channels"]))
    return list(seen.values())


def default_microphone() -> MicInfo | None:
    try:
        import sounddevice as sd
        idx = sd.default.device[0]
        if idx is not None and idx >= 0:
            d = sd.query_devices(idx)
            if d.get("max_input_channels", 0) > 0:
                return MicInfo(idx, d["name"].strip(), int(d["max_input_channels"]))
    except Exception:
        pass
    mics = list_microphones()
    return mics[0] if mics else None


def has_microphone() -> bool:
    return bool(list_microphones())


_LOOPBACK_HINTS = ("stereo mix", "what u hear", "loopback", "wave out",
                   "rec. playback", "mixage", "wave-out", "speakers (loopback)")


def system_audio_device(sd):
    """Return (device_index, channels, samplerate, extra_settings) for capturing
    system/speaker audio ("what you hear"), or None if unavailable.

    Preferred path is a WASAPI loopback setting; if this sounddevice build
    doesn't support it, fall back to a loopback-style INPUT device such as
    Windows "Stereo Mix" (which the user can enable in Sound settings).
    """
    # 1) Native WASAPI loopback, if this build supports it.
    try:
        if "loopback" in str(__import__("inspect").signature(sd.WasapiSettings.__init__)):
            for ha in sd.query_hostapis():
                if "wasapi" in ha["name"].lower():
                    out_dev = ha.get("default_output_device", -1)
                    if out_dev is not None and out_dev >= 0:
                        info = sd.query_devices(out_dev)
                        ch = max(1, int(info.get("max_output_channels") or 2))
                        rate = int(info.get("default_samplerate") or 48000)
                        return out_dev, ch, rate, sd.WasapiSettings(loopback=True)
    except Exception:
        pass
    # 2) Stereo-Mix-style input device (regular input, no special settings).
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and any(h in d["name"].lower() for h in _LOOPBACK_HINTS):
                ch = max(1, int(d["max_input_channels"]))
                rate = int(d.get("default_samplerate") or 44100)
                return i, ch, rate, None
    except Exception:
        log.warning("system-audio device lookup failed", exc_info=True)
    return None


def system_audio_supported(timeout: float = 4.0) -> bool:
    """Probe for system-audio capture off the calling thread, with a timeout, so a
    wedged audio driver can't freeze the UI (the recording panel asks while the
    main window is being built). A probe that doesn't answer in time counts as
    unsupported; the user still has the microphone."""
    result: dict = {}

    def run():
        try:
            result["ok"] = _probe_system_audio()
        except Exception:
            result["ok"] = False

    t = threading.Thread(target=run, daemon=True, name="system-audio-probe")
    t.start()
    t.join(timeout)
    if t.is_alive():
        log.warning("system-audio probe timed out after %ss", timeout)
        return False
    return bool(result.get("ok"))


def _probe_system_audio() -> bool:
    # Preferred: true WASAPI loopback via soundcard (no "Stereo Mix" needed).
    try:
        import soundcard as sc
        spk = sc.default_speaker()
        if spk and sc.get_microphone(spk.name, include_loopback=True):
            return True
    except Exception:
        pass
    # Fallback: a Stereo-Mix-style input device.
    try:
        import sounddevice as sd
        return system_audio_device(sd) is not None
    except Exception:
        return False


def input_candidates(sd, preferred=None) -> list[int]:
    """Ordered list of input-device indices to try opening.

    Different Windows host APIs (WASAPI, DirectSound, MME, WDM-KS) expose the
    same physical mic as separate device indices, and some combos raise
    PaErrorCode -9999 (MME/DirectSound errors). We try the user's choice first,
    then each host API's default input, then the system default, then every
    input device — so recording works even when one host API is broken.
    """
    cands: list[int] = []
    if preferred is not None and preferred >= 0:
        cands.append(preferred)
    # Prefer WASAPI first (most reliable on modern Windows), then the rest.
    try:
        apis = list(sd.query_hostapis())
        order = sorted(range(len(apis)),
                       key=lambda i: 0 if "wasapi" in apis[i]["name"].lower() else 1)
        for i in order:
            di = apis[i].get("default_input_device", -1)
            if di is not None and di >= 0:
                cands.append(int(di))
    except Exception:
        pass
    try:
        d0 = sd.default.device[0]
        if d0 is not None and d0 >= 0:
            cands.append(int(d0))
    except Exception:
        pass
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                cands.append(i)
    except Exception:
        pass
    seen: set[int] = set()
    out: list[int] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _refresh_portaudio(sd) -> None:
    """Re-scan audio devices. PortAudio caches the device list at start-up, so a
    headset that was unplugged and plugged back in (or a new default device) is
    only visible after a terminate/initialize cycle."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.debug("PortAudio refresh failed", exc_info=True)


# Device-loss detection (H13): no audio callback for this long while recording
# means the device went away (USB/Bluetooth unplugged, driver reset).
_STALL_SECONDS = 3.0
# How long to keep trying to reopen a lost microphone before giving up.
_REOPEN_SECONDS = 12.0
# Mic + system mixing is streamed in blocks of this many seconds (M31).
_MIX_BLOCK_SECONDS = 5.0
# Longest gap padded with silence after a reopened device (K2) — a sanity cap.
_MAX_GAP_SECONDS = 3600.0
# Most audio the live-transcription tap buffers when its worker falls behind (K7).
_LIVE_MAX_SECONDS = 120.0

_USE_CONFIG = object()          # _open_mic(): "use the configured device index"

_LOST_MIC_MSG = ("The microphone stopped sending audio (was it unplugged?). "
                 "Everything recorded up to that point was saved.")


# ---------------------------------------------------------------------------
# Streaming mix (M31): mic + system audio are mixed block by block, so a long
# meeting never has to fit in RAM.
# ---------------------------------------------------------------------------
class _LinearResampler:
    """Streaming linear-interpolation resampler (same result as one np.interp
    over the whole signal, but fed block by block)."""

    def __init__(self, src_rate: int, dst_rate: int):
        import numpy as np
        self._np = np
        self.src, self.dst = int(src_rate), int(dst_rate)
        self.step = self.src / float(self.dst)      # input samples per output sample
        self.next_pos = 0.0                         # absolute input position of next output
        self.offset = 0                             # absolute index of self.buf[0]
        self.buf = np.zeros(0, dtype="float32")

    def process(self, x):
        np = self._np
        x = np.asarray(x, dtype="float32")
        if self.src == self.dst:
            return x
        buf = np.concatenate([self.buf, x]) if len(self.buf) else x
        if not len(buf):
            return np.zeros(0, dtype="float32")
        last = self.offset + len(buf) - 1           # last absolute input index available
        if self.next_pos > last:
            n = 0
        else:
            n = int((last - self.next_pos) // self.step) + 1
        if n:
            pos = self.next_pos + self.step * np.arange(n) - self.offset
            out = np.interp(pos, np.arange(len(buf)), buf).astype("float32")
            self.next_pos += self.step * n
        else:
            out = np.zeros(0, dtype="float32")
        keep = int(self.next_pos) - self.offset     # keep from floor(next_pos) on
        keep = max(0, min(keep, len(buf) - 1))
        self.buf = buf[keep:]
        self.offset += keep
        return out


def _iter_mono(path: str, target: int, block_seconds: float):
    """Yield a media file as mono float32 blocks at `target` Hz."""
    import soundfile as sf
    with sf.SoundFile(path) as f:
        rs = _LinearResampler(f.samplerate, target)
        bs = max(1024, int(f.samplerate * block_seconds))
        for blk in f.blocks(blocksize=bs, dtype="float32", always_2d=True):
            mono = blk.mean(axis=1) if blk.shape[1] > 1 else blk[:, 0]
            out = rs.process(mono)
            if len(out):
                yield out


def _iter_mix(paths: list[str], target: int, block_seconds: float, offsets=None):
    """Yield the sum of several sources in blocks (shorter sources are padded).
    `offsets` (seconds, one per path) delays a source that started later on the
    recording clock, so the sources stay aligned (K2)."""
    import numpy as np
    gens = [_iter_mono(p, target, block_seconds) for p in paths]
    offsets = list(offsets or [0.0] * len(paths))
    bufs = [np.zeros(max(0, int(round(float(offsets[i] if i < len(offsets) else 0.0) * target))),
                     dtype="float32") for i in range(len(paths))]
    done = [False] * len(paths)
    size = max(1024, int(target * block_seconds))
    while True:
        for i, g in enumerate(gens):
            while not done[i] and len(bufs[i]) < size:
                try:
                    bufs[i] = np.concatenate([bufs[i], next(g)])
                except StopIteration:
                    done[i] = True
        n = max((min(len(b), size) for b in bufs), default=0)
        if n == 0:
            if all(done):
                return
            continue
        chunk = np.zeros(n, dtype="float32")
        for i, b in enumerate(bufs):
            take = b[:n]
            chunk[:len(take)] += take
            bufs[i] = b[len(take):]
        yield chunk


def mix_sources_to_file(paths: list[str], out_path: str, fmt: str = "wav", target: int = 16000,
                        block_seconds: float | None = None, scratch_dir: Path | None = None,
                        offsets: list[float] | None = None) -> None:
    """Mix `paths` (any rate / channel count) into a mono `target` Hz WAV or MP3
    at `out_path`, streaming in blocks. Two passes so the result can still be
    normalised when the sum clips. `offsets` = seconds of leading silence per
    source (K2). Raises on failure and never leaves a half-written `out_path`
    behind."""
    import numpy as np
    import soundfile as sf
    block = float(block_seconds or _MIX_BLOCK_SECONDS)
    scratch_dir = Path(scratch_dir or TMP_DIR)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch = scratch_dir / f"_mix_{int(time.time() * 1000)}_{threading.get_ident()}.wav"
    try:
        peak = 0.0
        with sf.SoundFile(str(scratch), mode="w", samplerate=target, channels=1,
                          subtype="FLOAT") as tmp:
            for chunk in _iter_mix(paths, target, block, offsets):
                if len(chunk):
                    peak = max(peak, float(np.abs(chunk).max()))
                    tmp.write(chunk)
        gain = (1.0 / peak) if peak > 1.0 else 1.0
        bs = max(1024, int(target * block))
        with sf.SoundFile(str(scratch)) as src:
            if fmt == "mp3":
                import av
                from fractions import Fraction
                c = av.open(out_path, mode="w")
                try:
                    stm = c.add_stream("libmp3lame", rate=target)
                    stm.layout = "mono"
                    pts = 0
                    for blk in src.blocks(blocksize=bs, dtype="float32"):
                        pcm = (np.clip(blk * gain, -1.0, 1.0) * 32767).astype("int16").reshape(1, -1)
                        fr = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
                        fr.sample_rate = target
                        fr.pts, fr.time_base = pts, Fraction(1, target)
                        pts += pcm.shape[1]
                        for p in stm.encode(fr):
                            c.mux(p)
                    for p in stm.encode(None):
                        c.mux(p)
                finally:
                    c.close()
            else:
                with sf.SoundFile(out_path, mode="w", samplerate=target, channels=1,
                                  subtype="PCM_16") as dst:
                    for blk in src.blocks(blocksize=bs, dtype="float32"):
                        dst.write(blk * gain if gain != 1.0 else blk)
    except BaseException:
        try:
            Path(out_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Config + result
# ---------------------------------------------------------------------------
@dataclass
class RecordingConfig:
    kind: str = "audio"             # audio | screen | camera
    audio_format: str = "wav"       # wav | mp3
    source: str = "mic"             # mic | system | both  (system = speaker loopback)
    fps: int = 12                   # screen/camera frame rate
    include_audio: bool = True
    mic_index: int | None = None
    monitor: int = 1                # mss monitor index for screen
    camera_index: int = 0
    samplerate: int = 16000         # 16k keeps Whisper-ready audio small


@dataclass
class RecordingResult:
    path: str
    kind: str
    duration: float
    size_bytes: int
    fmt: str
    started_at: float
    has_audio: bool = True
    resolution: str = ""

    @property
    def size_human(self) -> str:
        return human_size(self.size_bytes)


def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


# ---------------------------------------------------------------------------
# Base recorder: clock + state
# ---------------------------------------------------------------------------
class BaseRecorder:
    kind = "audio"

    def __init__(self, cfg: RecordingConfig):
        self.cfg = cfg
        self.state = IDLE
        # K10: `started_at` is wall-clock time, for display only; the recording
        # clock (`_t0`, pauses, device watchdogs) runs on time.monotonic() so an
        # NTP / manual clock change can't stretch the video against the
        # sample-counted audio.
        self.started_at = 0.0
        self._t0 = 0.0
        self._paused_total = 0.0
        self._pause_at = 0.0
        self._level = 0.0
        self._final_elapsed = 0.0
        self.output_path = ""
        self.has_audio = cfg.include_audio
        self.resolution = ""
        self.error = ""
        self.warnings: list[str] = []   # non-fatal problems to show the user
        self._discard = False           # cancel(): skip mixing/muxing, drop intermediates

    # -- clock --------------------------------------------------------------
    def elapsed(self) -> float:
        if self.state == IDLE:
            return 0.0
        if self.state == PAUSED:
            return self._pause_at - self._t0 - self._paused_total
        if self.state in (STOPPED, CANCELLED, ERROR):
            return self._final_elapsed
        return time.monotonic() - self._t0 - self._paused_total

    def _freeze_clock(self) -> None:
        """Capture elapsed time while still running, before flipping to STOPPED."""
        if self.state == PAUSED:
            self._final_elapsed = self._pause_at - self._t0 - self._paused_total
        elif self.state == RECORDING:
            self._final_elapsed = time.monotonic() - self._t0 - self._paused_total

    def _fail(self, message: str) -> None:
        """Enter ERROR from a capture thread (clock frozen so the duration is kept)."""
        self._freeze_clock()
        self.error = message
        self.state = ERROR

    def level(self) -> float:
        return 0.0 if self.state == PAUSED else self._level

    def all_warnings(self) -> list[str]:
        return list(self.warnings)

    def file_size(self) -> int:
        try:
            return Path(self.output_path).stat().st_size if self.output_path else 0
        except OSError:
            return 0

    # -- lifecycle (overridden) --------------------------------------------
    def start(self) -> None: ...
    def stop(self) -> RecordingResult: ...

    def pause(self) -> None:
        if self.state == RECORDING:
            self._pause_at = time.monotonic()
            self.state = PAUSED

    def resume(self) -> None:
        if self.state == PAUSED:
            self._paused_total += time.monotonic() - self._pause_at
            self.state = RECORDING

    def cancel(self) -> None:
        self._discard = True
        try:
            self.stop()
        finally:
            self.state = CANCELLED
            try:
                if self.output_path:
                    Path(self.output_path).unlink(missing_ok=True)
            except OSError:
                pass

    def _result(self, fmt: str) -> RecordingResult:
        return RecordingResult(
            path=self.output_path, kind=self.kind, duration=self.elapsed(),
            size_bytes=self.file_size(), fmt=fmt, started_at=self.started_at,
            has_audio=self.has_audio, resolution=self.resolution,
        )

    @staticmethod
    def _safe_close(stream):
        try:
            stream.stop(); stream.close()
        except Exception:
            pass

    def _close_with_timeout(self, stream, timeout: float = 3.0) -> bool:
        """Close a (possibly wedged) stream without hanging; True if it closed."""
        if stream is None:
            return True
        closer = threading.Thread(target=lambda: self._safe_close(stream), daemon=True)
        closer.start()
        closer.join(timeout=timeout)
        return not closer.is_alive()


# ---------------------------------------------------------------------------
# Audio-only recorder  (WAV or MP3)
# ---------------------------------------------------------------------------
class AudioRecorder(BaseRecorder):
    kind = "audio"

    def __init__(self, cfg: RecordingConfig):
        super().__init__(cfg)
        ext = "mp3" if cfg.audio_format == "mp3" else "wav"
        self.output_path = str(RECORDINGS_DIR / f"recording_{int(time.time())}.{ext}")
        self._rate = cfg.samplerate
        self._stop_flag = threading.Event()
        self._thread: threading.Thread | None = None
        self._frames_captured = 0
        # what was actually written, per source: {"mic"|"sys": (frames, rate)}
        self._written: dict[str, tuple[int, int]] = {}
        self._last_cb: dict[str, float] = {}         # source -> monotonic time of last device callback
        # K2: recording time (elapsed()) of each source's first sample, so the
        # sources are lined up on the recording clock when mixed / muxed; the
        # earliest of them is `_first_audio_elapsed` (the audio track's origin).
        self._first_elapsed: dict[str, float] = {}
        self._first_audio_elapsed: float | None = None
        self._pad_pending: set[str] = set()          # sources to re-sync after a reopen
        # Recording-clock time a reopened device's new stream started: the gap is
        # measured to HERE, not to when its first block happens to arrive (a late
        # first callback followed by a catch-up burst would otherwise be counted
        # twice and make the track longer than the recording).
        self._reopen_at: dict[str, float] = {}
        self._temp_kinds: dict[str, str] = {}        # temp WAV -> "mic" | "sys"
        self._failed_sources: dict[str, str] = {}
        self._temps: list[str] = []
        self._finalizing = False
        self.reopened = 0                            # times a lost mic was reopened
        # live-transcription tap (opt-in): captured mic frames buffered for a
        # separate worker to pull and transcribe as recording proceeds.
        self._live_on = False
        self._live_lock = threading.Lock()
        self._live_buf: list = []
        self._live_samples = 0
        self._live_dropped = 0
        self._live_rate = cfg.samplerate

    def enable_live(self) -> None:
        self._live_on = True

    def disable_live(self) -> None:
        """K7: the live worker has ended — stop buffering (and free the buffer)."""
        self._live_on = False
        with self._live_lock:
            self._live_buf = []
            self._live_samples = 0

    def _live_push(self, mono_f32) -> None:
        if not self._live_on:
            return
        dropped = 0
        try:
            with self._live_lock:
                self._live_buf.append(mono_f32)
                self._live_samples += len(mono_f32)
                # K7: a live worker that fell behind (slow model) must not make
                # the buffer grow for the whole meeting — keep the newest audio.
                cap = int(_LIVE_MAX_SECONDS * (self._live_rate or 16000))
                while self._live_samples > cap and len(self._live_buf) > 1:
                    old = self._live_buf.pop(0)
                    self._live_samples -= len(old)
                    dropped += len(old)
        except Exception:
            return
        if dropped:
            first = self._live_dropped == 0
            self._live_dropped += dropped
            (log.warning if first else log.debug)(
                "live transcription is falling behind: dropped %.1fs of the oldest buffered "
                "audio (%.1fs in total)", dropped / float(self._live_rate or 16000),
                self._live_dropped / float(self._live_rate or 16000))

    def pull_live(self):
        """Return (new_samples, rate) captured since the last pull, then clear."""
        import numpy as np
        with self._live_lock:
            if not self._live_buf:
                return None, self._live_rate
            arr = np.concatenate(self._live_buf).astype("float32")
            self._live_buf = []
            self._live_samples = 0
        return arr, self._live_rate

    # -- bookkeeping ----------------------------------------------------------
    def _note_audio(self, src: str, frames: int, rate: int) -> None:
        """Account for `frames` samples of `src` that were just written."""
        n, _ = self._written.get(src, (0, rate))
        self._written[src] = (n + int(frames), int(rate))
        self._frames_captured += int(frames)
        if src not in self._first_elapsed:
            first = max(0.0, self.elapsed() - frames / float(rate or 1))
            self._first_elapsed[src] = first
            if self._first_audio_elapsed is None or first < self._first_audio_elapsed:
                self._first_audio_elapsed = first

    def _gap_frames(self, src: str, frames: int, rate: int) -> int:
        """K2: samples of `src` missing before a block of `frames` that arrived
        just now — the recording time since the source's first sample (paused
        time excluded) minus what was written. Used after a reopened device, so
        the file keeps its place on the recording clock instead of dropping
        the gap."""
        first = self._first_elapsed.get(src)
        if first is None or not rate:
            return 0
        written = self._written.get(src, (0, rate))[0]
        start = self._reopen_at.pop(src, None)
        if start is None:
            start = self.elapsed() - frames / float(rate)
        gap = int(round((start - first) * rate)) - written
        return max(0, min(gap, int(_MAX_GAP_SECONDS * rate)))

    def _take_gap(self, src: str, frames: int, rate: int) -> int:
        """Frames of silence to write before this block (0 unless `src` was
        just reopened)."""
        if src not in self._pad_pending:
            return 0
        self._pad_pending.discard(src)
        gap = self._gap_frames(src, frames, rate)
        if gap:
            log.info("%s: padding a %.2fs gap after the device was reopened", src,
                     gap / float(rate))
        return gap

    def _source_offsets(self, temps: list[str]) -> list[float]:
        """Seconds of leading silence per temp source so all start on the
        recording clock (K2); also moves the audio origin to the earliest
        surviving source."""
        firsts = []
        for t in temps:
            kind = self._temp_kinds.get(t)
            if kind is None:
                name = Path(t).name
                kind = "mic" if name.startswith("_src_mic") else "sys" if name.startswith("_src_sys") else ""
            firsts.append(self._first_elapsed.get(kind))
        known = [f for f in firsts if f is not None]
        if not known:
            return [0.0] * len(temps)
        origin = min(known)
        self._first_audio_elapsed = origin
        return [0.0 if f is None else max(0.0, f - origin) for f in firsts]

    def recorded_seconds(self) -> float:
        """Length of the audio actually written (frames / rate) — the truthful
        duration, even if a device dropped out for part of the meeting (H13).
        Sources that started late count from their place on the clock (K2)."""
        origin = self._first_audio_elapsed or 0.0
        out = 0.0
        for src, (n, r) in list(self._written.items()):
            if r:
                lead = max(0.0, self._first_elapsed.get(src, origin) - origin)
                out = max(out, lead + n / float(r))
        return out

    def _stalled(self, src: str) -> bool:
        last = self._last_cb.get(src)
        return (last is not None and self._written.get(src, (0, 0))[0] > 0
                and self.state in (RECORDING, PAUSED)
                and time.monotonic() - last > _STALL_SECONDS)

    def _result(self, fmt: str) -> RecordingResult:
        res = super()._result(fmt)
        secs = self.recorded_seconds()
        if secs > 0:
            res.duration = secs
        return res

    def file_size(self) -> int:
        if self.state in (RECORDING, PAUSED) and self._temps:
            total = 0
            for t in self._temps:
                try:
                    total += Path(t).stat().st_size
                except OSError:
                    pass
            return total
        return super().file_size()

    def start(self) -> None:
        # Capture runs in a background thread so opening the audio device can
        # NEVER freeze the UI (some drivers/host APIs block on open). The UI
        # polls state/level; errors surface via state == ERROR.
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        # mic-only uses the proven single-stream path; system/both use the
        # multi-source capture+mix path.
        target = self._loop if self.cfg.source == "mic" else self._loop_sources
        self._thread = threading.Thread(target=target, daemon=True)
        self._t0 = time.monotonic()
        self.started_at = time.time()
        self.state = RECORDING
        self._thread.start()

    # -- microphone open / reopen (shared by the single and multi-source paths) --
    def _open_mic(self, sd, callback, rates, on_rate=None, on_fail=None, timeout: float = 6.0,
                  preferred=_USE_CONFIG):
        """Open the first working input: each candidate device (user's choice,
        host-API defaults, then every input) × each samplerate in `rates` (None =
        the device's default rate). `on_rate(rate)` runs before each attempt (to
        create a writer at that rate), `on_fail()` after a failed one. Runs in a
        helper thread so a driver that blocks in open() can't hang the recorder.
        `preferred` overrides the configured device index (None = no preference).
        Returns (stream, device, rate); raises RuntimeError."""
        box: dict = {}
        lock = threading.Lock()
        state = {"abandoned": False}
        first_choice = self.cfg.mic_index if preferred is _USE_CONFIG else preferred

        def run():
            err = None
            cands = input_candidates(sd, first_choice)
            if not cands:
                box["err"] = RuntimeError("No microphone detected. Connect a microphone and try again.")
                return
            for device in cands:
                for pref in rates:
                    rate = pref
                    if rate is None:
                        try:
                            rate = int(sd.query_devices(device).get("default_samplerate") or 44100)
                        except Exception:
                            rate = 44100
                    try:
                        if on_rate:
                            on_rate(rate)
                        st = sd.InputStream(samplerate=rate, channels=1, device=device,
                                            dtype="float32", blocksize=1600, callback=callback)
                        st.start()
                    except Exception as exc:
                        err = exc
                        if on_fail:
                            try:
                                on_fail()
                            except Exception:
                                pass
                        continue
                    with lock:
                        late = state["abandoned"]
                        if not late:
                            box["ok"] = (st, device, rate)
                    if late:                         # caller gave up: don't leak the device
                        self._safe_close(st)
                    return
            box["err"] = err

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout)
        with lock:
            if "ok" in box:
                return box["ok"]
            state["abandoned"] = True
        if t.is_alive():
            raise RuntimeError("Microphone did not respond (it may be busy, muted or disabled). "
                               "Try selecting a different microphone.")
        raise RuntimeError(str(box.get("err") or "The microphone could not be opened."))

    @staticmethod
    def _device_identity(sd, device):
        """(name, host API name) of an input device index, or None (K3)."""
        if device is None or device < 0:
            return None
        try:
            d = sd.query_devices(device)
            name = str(d.get("name", "")).strip()
            ha = d.get("hostapi")
            ha_name = None
            if ha is not None:
                try:
                    ha_name = str(sd.query_hostapis()[int(ha)]["name"])
                except Exception:
                    ha_name = None
            return (name, ha_name) if name else None
        except Exception:
            return None

    @staticmethod
    def _find_device(sd, ident):
        """Current index of the input device `ident` = (name, host API) after
        PortAudio renumbered its devices, or None (K3)."""
        if not ident:
            return None
        name, ha_name = ident
        try:
            apis = list(sd.query_hostapis())
        except Exception:
            apis = []
        try:
            devices = list(sd.query_devices())
        except Exception:
            return None
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) <= 0 or str(d.get("name", "")).strip() != name:
                continue
            if ha_name is not None:
                ha = d.get("hostapi")
                try:
                    if ha is not None and str(apis[int(ha)]["name"]) != ha_name:
                        continue
                except Exception:
                    pass
            return i
        return None

    def _reopen_mic(self, sd, callback, rate: int, dead_stream, device=None):
        """The mic stopped delivering audio (unplugged / driver reset). Close it
        and keep trying to open an input — the same one if it comes back, else
        another — at the SAME rate so the file stays consistent. Returns
        (stream, device, rate) or None.

        K2: the next block written pads the gap with silence first.
        K3: refreshing PortAudio renumbers the devices, so the lost device is
        remembered by name + host API and looked up again afterwards."""
        ident = self._device_identity(sd, device)
        self._pad_pending.add("mic")
        closed = self._close_with_timeout(dead_stream)
        deadline = time.monotonic() + _REOPEN_SECONDS
        while not self._stop_flag.is_set() and time.monotonic() < deadline:
            if closed:
                _refresh_portaudio(sd)
                preferred = self._find_device(sd, ident)   # None: not back (yet)
            else:
                preferred = device                        # no refresh: indices unchanged
            try:
                self._reopen_at["mic"] = self.elapsed()     # the new stream's audio starts now
                got = self._open_mic(sd, callback, (rate,), timeout=4.0, preferred=preferred)
                self._last_cb["mic"] = time.monotonic()
                self.reopened += 1
                log.warning("microphone lost and reopened on device %s @ %sHz", got[1], rate)
                return got
            except Exception as exc:
                log.info("reopening the microphone failed: %s", exc)
            self._stop_flag.wait(1.0)
        return None

    # -- multi-source (system / both) ---------------------------------------
    def _source_plan(self) -> list[str]:
        """Which sources to capture: 'mic' (sounddevice) and/or 'sys' (loopback)."""
        src = self.cfg.source
        plan = []
        if src in ("mic", "both"):
            plan.append("mic")
        if src in ("system", "both"):
            plan.append("sys")
        return plan

    def _source_failed(self, kind: str, message: str) -> None:
        """A source could not be captured (M34): remember why and, when another
        source keeps recording, tell the user instead of dropping it silently."""
        self._failed_sources[kind] = message
        log.warning("%s source failed: %s", kind, message)
        others = [k for k in self._source_plan() if k != kind and k not in self._failed_sources]
        if others:
            rest = "system audio" if kind == "mic" else "the microphone"
            self.warnings.append(f"{message} — recording {rest} only.")

    def _loop_sources(self):
        # Each source captures to its own temp WAV in a thread; on stop they are
        # mixed. Mic uses sounddevice; system uses true WASAPI loopback (soundcard)
        # so it works WITHOUT the user enabling "Stereo Mix".
        self._frames_captured = 0
        ts = int(self.started_at)
        temps, threads = [], []
        try:
            plan = self._source_plan()
            if not plan:
                raise RuntimeError("No audio source selected.")
            for kind in plan:
                tmp = str(TMP_DIR / f"_src_{kind}_{ts}.wav")
                temps.append(tmp)
                self._temp_kinds[tmp] = kind
                target = self._capture_mic if kind == "mic" else self._capture_system
                th = threading.Thread(target=target, args=(tmp,), daemon=True)
                th.start()
                threads.append(th)
            self._temps = list(temps)

            start = time.monotonic()
            while not self._stop_flag.is_set():
                time.sleep(0.1)
                if self._stop_flag.is_set():
                    break
                if not any(th.is_alive() for th in threads):
                    # every source has failed (M34) — say why, don't wait it out
                    why = "; ".join(self._failed_sources.values())
                    raise RuntimeError(why or "Audio capture stopped unexpectedly.")
                if (self.state == RECORDING and self._frames_captured == 0
                        and time.monotonic() - start > 6.0):
                    raise RuntimeError("No audio is being received. For system audio make "
                                       "sure something is playing; for the mic check it isn't muted.")
        except Exception as exc:
            self._fail(str(exc))
            log.exception("multi-source audio capture failed")
        finally:
            if self.state != ERROR:
                self.state = STOPPED
            self._stop_flag.set()                # also ends the surviving source threads
            for th in threads:
                th.join(timeout=6)
            if self._discard:
                for t in temps:
                    Path(t).unlink(missing_ok=True)
            else:
                self._finalizing = True
                try:
                    self._finalize_sources(temps)
                except BaseException as exc:          # MemoryError, disk full, …
                    kept = ", ".join(Path(t).name for t in temps if Path(t).exists())
                    self.error = (f"The recording could not be saved ({exc.__class__.__name__}: "
                                  f"{exc}). The captured audio was kept in {TMP_DIR}"
                                  + (f" ({kept})." if kept else "."))
                    log.exception("finalizing multi-source recording failed")
                finally:
                    self._finalizing = False

    def _capture_mic(self, temp: str):
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
        box: dict = {"writer": None, "rate": self.cfg.samplerate}
        stream = None

        def close_writer():
            w, box["writer"] = box["writer"], None
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass

        def open_writer(rate):
            close_writer()
            box["rate"] = rate
            box["writer"] = sf.SoundFile(temp, mode="w", samplerate=rate, channels=1,
                                         subtype="PCM_16")

        def cb(indata, frames, time_info, status):  # noqa: ARG001
            self._last_cb["mic"] = time.monotonic()
            if self.state != RECORDING:
                return
            w = box["writer"]
            if w is None:
                return
            try:
                data = indata if indata.ndim == 1 or indata.shape[1] == 1 \
                    else indata.mean(axis=1, keepdims=True)
                rate = box["rate"]
                gap = self._take_gap("mic", frames, rate)
                while gap > 0:                       # K2: silence for a reopened mic's gap
                    n = min(gap, rate)
                    w.write(np.zeros((n, 1), dtype="float32"))
                    self._note_audio("mic", n, rate)
                    gap -= n
                w.write(data.copy())
                self._note_audio("mic", frames, rate)
                self._level = float(min(1.0, float(np.abs(data).max()) * 1.4))
                if self._live_on:
                    self._live_rate = rate
                    self._live_push(np.asarray(data).reshape(-1).copy())
            except Exception:
                pass

        try:
            try:
                # M34: same device + samplerate fallback as the mic-only path
                stream, device, rate = self._open_mic(
                    sd, cb, (self.cfg.samplerate, None), on_rate=open_writer, on_fail=close_writer)
                log.info("mic source on device %s @ %sHz", device, rate)
            except Exception as exc:
                self._source_failed("mic", f"The microphone could not be opened ({exc})")
                return
            while not self._stop_flag.is_set():
                time.sleep(0.05)
                if self._stalled("mic"):
                    got = self._reopen_mic(sd, cb, box["rate"], stream, device)
                    stream = None
                    if got is None:
                        if not self._stop_flag.is_set():
                            self._source_failed("mic", "The microphone stopped sending audio "
                                                       "(was it unplugged?)")
                        return
                    stream, device = got[0], got[1]
                    self.warnings.append("The microphone stopped responding and was reopened — "
                                         "the gap was filled with silence.")
        except Exception as exc:
            self._source_failed("mic", f"Microphone capture failed ({exc})")
        finally:
            if stream is not None:
                self._close_with_timeout(stream)
            close_writer()

    def _capture_system(self, temp: str):
        import numpy as np
        import soundfile as sf
        writer = None
        try:
            import soundcard as sc
            spk = sc.default_speaker()
            loop_mic = sc.get_microphone(spk.name, include_loopback=True)
            rate = 48000
            writer = sf.SoundFile(temp, mode="w", samplerate=rate, channels=1, subtype="PCM_16")
            with loop_mic.recorder(samplerate=rate, channels=1, blocksize=2048) as r:
                while not self._stop_flag.is_set():
                    data = r.record(numframes=2048)
                    self._last_cb["sys"] = time.monotonic()
                    if self.state != RECORDING:
                        continue
                    d = data.reshape(-1) if getattr(data, "ndim", 1) > 1 else data
                    writer.write(d.astype("float32"))
                    self._note_audio("sys", len(d), rate)
                    self._level = float(min(1.0, float(np.abs(d).max()) * 1.4))
        except Exception as exc:
            log.warning("system-audio (loopback) capture failed", exc_info=True)
            if not self._stop_flag.is_set():
                self._source_failed("sys", f"System audio could not be captured ({exc})")
        finally:
            try:
                if writer is not None:
                    writer.close()
            except Exception:
                pass

    def _finalize_sources(self, temps: list[str]):
        temps = [t for t in temps if Path(t).exists() and Path(t).stat().st_size > 1024]
        if not temps:
            return
        # K2: each source starts at its own first sample on the recording clock
        # (a mic can open seconds after the loopback) — delay the later ones.
        offsets = self._source_offsets(temps)
        if len(temps) == 1 and self.cfg.audio_format != "mp3":
            try:
                Path(temps[0]).replace(self.output_path)
                return
            except OSError:
                pass
        # M31: streamed block by block — never loads a whole source into RAM.
        mix_sources_to_file(temps, self.output_path, self.cfg.audio_format, target=16000,
                            offsets=offsets)
        for t in temps:
            Path(t).unlink(missing_ok=True)

    def _open_writer(self, rate: int):
        """(Re)create the output writer at the given samplerate."""
        if self.cfg.audio_format == "mp3":
            import av
            self._container = av.open(self.output_path, mode="w")
            self._av_stream = self._container.add_stream("libmp3lame", rate=rate)
            self._av_stream.layout = "mono"
        else:
            import soundfile as sf
            self._writer = sf.SoundFile(self.output_path, mode="w", samplerate=rate,
                                        channels=1, subtype="PCM_16")

    def _close_writer(self, finalize: bool = True):
        try:
            if getattr(self, "_av_stream", None) is not None:
                if finalize:
                    for packet in self._av_stream.encode(None):
                        self._container.mux(packet)
                self._container.close()
                self._av_stream = self._container = None
            elif getattr(self, "_writer", None) is not None:
                self._writer.close()
                self._writer = None
        except Exception:
            log.warning("error closing audio writer", exc_info=True)

    def _loop(self):
        # CALLBACK input stream (blocking read() hangs on some Windows drivers);
        # the thread just owns the stream and waits for the stop flag. Opened at
        # the preferred rate directly (proven to work; PortAudio resamples),
        # falling back to the device default rate only if open() actually fails.
        import numpy as np
        import sounddevice as sd
        self._writer = self._av_stream = self._container = None
        stream = None
        self._frames_captured = 0
        wlock = threading.Lock()

        def write(block) -> bool:
            """Write a (n, 1) float32 block to the open writer (call under wlock)."""
            if self._av_stream is not None:
                import av as _av
                frame = _av.AudioFrame.from_ndarray(
                    (block.T * 32767).astype(np.int16), format="s16", layout="mono")
                frame.sample_rate = self._rate
                for packet in self._av_stream.encode(frame):
                    self._container.mux(packet)
                return True
            if self._writer is not None:
                self._writer.write(block.copy())
                return True
            return False

        def callback(indata, frames, time_info, status):  # noqa: ARG001
            self._last_cb["mic"] = time.monotonic()
            if self.state != RECORDING:
                return
            try:
                with wlock:
                    gap = self._take_gap("mic", frames, self._rate)
                    while gap > 0:                   # K2: silence for a reopened mic's gap
                        n = min(gap, self._rate)
                        if not write(np.zeros((n, 1), dtype="float32")):
                            break
                        self._note_audio("mic", n, self._rate)
                        gap -= n
                    if not write(indata):
                        return
                self._note_audio("mic", frames, self._rate)
                self._level = float(min(1.0, float(np.abs(indata).max()) * 1.4))
                if self._live_on:
                    self._live_rate = self._rate
                    self._live_push(indata.reshape(-1).copy())
            except Exception:
                log.warning("audio write error", exc_info=True)

        def open_writer(rate):
            self._rate = rate
            self._open_writer(rate)

        try:
            stream, device, rate = self._open_mic(
                sd, callback, (self.cfg.samplerate, None), on_rate=open_writer,
                on_fail=lambda: self._close_writer(finalize=False))
            log.info("audio recording on device %s @ %sHz", device, rate)

            start = time.monotonic()
            while not self._stop_flag.is_set():
                time.sleep(0.1)
                # Watchdog: device opened but delivers no audio (muted / no permission).
                if (self.state == RECORDING and self._frames_captured == 0
                        and time.monotonic() - start > 4.0):
                    raise RuntimeError("Microphone opened but no audio is being received. "
                                       "Check it isn't muted or disabled, or select a different microphone.")
                # H13: the device went away mid-recording — reopen it (or another
                # input) at the same rate; if that fails, stop with a clear error
                # instead of silently producing a file that ends early.
                if self._stalled("mic"):
                    log.warning("microphone stopped delivering audio; trying to reopen it")
                    got = self._reopen_mic(sd, callback, self._rate, stream, device)
                    stream = None
                    if got is None:
                        if self._stop_flag.is_set():
                            break
                        raise RuntimeError(_LOST_MIC_MSG)
                    stream, device = got[0], got[1]
                    self.warnings.append("The microphone stopped responding and was reopened — "
                                         "the gap was filled with silence.")
        except Exception as exc:
            self._fail(str(exc))
            log.exception("audio capture failed")
        finally:
            if self.state != ERROR:
                self.state = STOPPED
            # Closing a wedged stream can block — do it with a timeout so stop() returns.
            if stream is not None:
                self._close_with_timeout(stream)
            with wlock:
                self._close_writer(finalize=True)

    def _join_capture(self) -> None:
        th = self._thread
        if th is None:
            return
        th.join(timeout=15)
        # Mixing a long mic+system recording can take a while; it runs on the
        # capture thread, so wait for it (stop() runs off the GUI thread).
        while th.is_alive() and self._finalizing:
            th.join(timeout=0.5)

    def stop(self) -> RecordingResult:
        if self.state in (STOPPED, CANCELLED):
            self._stop_flag.set()
            self._join_capture()
            return self._result(self.cfg.audio_format)
        self._freeze_clock()
        self.state = STOPPED
        self._stop_flag.set()
        self._join_capture()
        return self._result(self.cfg.audio_format)


# ---------------------------------------------------------------------------
# Video recorder (screen or camera) + optional audio  -> MP4
# ---------------------------------------------------------------------------
def frame_pts(t: float, t_first: float, last_pts: int) -> int:
    """Presentation time (ms, time base 1/1000) of a frame captured at recording
    time `t` (H10): video follows the clock, not the frame count, so it stays in
    step with the audio even when capture runs below the target fps."""
    return max(last_pts + 1, int(round((t - t_first) * 1000)))


class VideoRecorder(BaseRecorder):
    kind = "screen"

    def __init__(self, cfg: RecordingConfig):
        super().__init__(cfg)
        self.kind = cfg.kind if cfg.kind in ("screen", "camera") else "screen"
        ts = int(time.time())
        self.output_path = str(RECORDINGS_DIR / f"recording_{ts}.mp4")
        self._tmp_video = str(TMP_DIR / f"_vid_{ts}.mp4")
        self._tmp_wav = str(TMP_DIR / f"_aud_{ts}.wav")
        self._frames = 0
        self._stop_flag = threading.Event()
        self._vthread: threading.Thread | None = None
        self._audio: AudioRecorder | None = None
        self._first_frame_elapsed: float | None = None

    def _make_audio_recorder(self) -> AudioRecorder | None:
        """H9: the soundtrack uses the same capture + mix path as audio-only
        recordings, so "System audio" and "Mic + System" work for screen and
        camera recordings too (remote participants are no longer dropped)."""
        if not self.cfg.include_audio:
            return None
        src = self.cfg.source if self.cfg.source in ("mic", "system", "both") else "mic"
        if src == "mic" and not has_microphone():
            return None
        acfg = replace(self.cfg, kind="audio", audio_format="wav", source=src)
        rec = AudioRecorder(acfg)
        rec.output_path = self._tmp_wav
        return rec

    def file_size(self) -> int:
        if self.state in (STOPPED, CANCELLED):
            return super().file_size()
        total = 0
        try:
            total += Path(self._tmp_video).stat().st_size
        except OSError:
            pass
        if self._audio is not None:
            total += self._audio.file_size()
        return total

    def level(self) -> float:
        if self.state == PAUSED or self._audio is None:
            return 0.0
        return self._audio.level()

    def all_warnings(self) -> list[str]:
        out = list(self.warnings)
        if self._audio is not None:
            out += self._audio.all_warnings()
            if self._audio.state == ERROR and self._audio.error:
                out.append(f"Audio stopped: {self._audio.error} The video continues without sound.")
        return out

    def pause(self) -> None:
        super().pause()
        if self._audio is not None:
            self._audio.pause()

    def resume(self) -> None:
        super().resume()
        if self._audio is not None:
            self._audio.resume()

    def start(self) -> None:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self._audio = self._make_audio_recorder()
        self.has_audio = self._audio is not None
        self._vthread = threading.Thread(target=self._video_loop, daemon=True)
        if self._audio is not None:
            self._audio.start()
            self._t0 = self._audio._t0             # one clock for both tracks
            self.started_at = self._audio.started_at
        else:
            self._t0 = time.monotonic()
            self.started_at = time.time()
        self.state = RECORDING
        self._vthread.start()

    # -- capture loop -------------------------------------------------------
    def _video_loop(self):
        import av
        import numpy as np
        from fractions import Fraction
        container = None
        tb = Fraction(1, 1000)
        try:
            grab, size = self._make_source()
            self.resolution = f"{size[0]}x{size[1]}"
            container = av.open(self._tmp_video, mode="w")
            stream = container.add_stream("libx264", rate=self.cfg.fps)
            stream.width, stream.height = size
            stream.pix_fmt = "yuv420p"
            stream.time_base = tb                  # H10: recording-clock timestamps (ms)
            stream.codec_context.time_base = tb
            stream.options = {"preset": "ultrafast", "crf": "26"}

            frame_interval = 1.0 / max(1, self.cfg.fps)
            next_t = time.monotonic()
            last_pts = -1
            while not self._stop_flag.is_set():
                if self.state == PAUSED:
                    time.sleep(0.03)
                    next_t = time.monotonic()
                    continue
                rgb = grab()
                if rgb is None:
                    time.sleep(frame_interval)
                    continue
                t = self.elapsed()                  # pause-free recording clock
                if self.state != RECORDING:
                    continue
                if self._first_frame_elapsed is None:
                    self._first_frame_elapsed = t
                pts = frame_pts(t, self._first_frame_elapsed, last_pts)
                last_pts = pts
                vframe = av.VideoFrame.from_ndarray(np.ascontiguousarray(rgb), format="rgb24")
                vframe.pts = pts
                vframe.time_base = tb
                for packet in stream.encode(vframe):
                    container.mux(packet)
                self._frames += 1
                next_t += frame_interval
                sleep = next_t - time.monotonic()
                if sleep > 0:
                    time.sleep(sleep)
                else:
                    next_t = time.monotonic()
            for packet in stream.encode(None):
                container.mux(packet)
        except Exception as exc:
            self._fail(str(exc))
            log.exception("video capture failed")
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    log.warning("closing the video file failed", exc_info=True)
            self._close_source()

    def _make_source(self):
        if self.kind == "camera":
            import cv2
            cap = cv2.VideoCapture(self.cfg.camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                raise RuntimeError("Could not open the camera.")
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
            w -= w % 2; h -= h % 2
            self._cap = cap

            def grab():
                ok, frame = cap.read()
                if not ok:
                    return None
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return frame[:h, :w]
            return grab, (w, h)
        else:  # screen
            import mss
            import numpy as np
            self._sct = mss.mss()
            mon = self._sct.monitors[min(self.cfg.monitor, len(self._sct.monitors) - 1)]
            w = mon["width"] - (mon["width"] % 2)
            h = mon["height"] - (mon["height"] % 2)

            def grab():
                shot = self._sct.grab(mon)
                arr = np.asarray(shot)[:, :, :3]          # BGRA -> BGR
                arr = arr[:h, :w, ::-1]                    # BGR -> RGB
                return arr
            return grab, (w, h)

    def _close_source(self):
        try:
            if getattr(self, "_cap", None) is not None:
                self._cap.release()
        except Exception:
            pass
        try:
            if getattr(self, "_sct", None) is not None:
                self._sct.close()
        except Exception:
            pass

    # -- stop + mux ---------------------------------------------------------
    def stop(self) -> RecordingResult:
        if self.state in (STOPPED, CANCELLED):
            return self._result(self._fmt())
        started = self.state != IDLE
        self._freeze_clock()
        self.state = STOPPED
        self._stop_flag.set()
        if self._vthread:
            self._vthread.join(timeout=10)
        if self._audio is not None:
            # always stop the audio capture — also after a video error, so the
            # devices are released and the sound isn't lost
            self._audio._discard = self._discard
            try:
                self._audio.stop()
            except Exception:
                log.exception("stopping the audio track failed")
        wav = Path(self._tmp_wav)
        self.has_audio = bool(self._audio is not None and wav.exists() and wav.stat().st_size > 1024)
        if self._discard:
            for p in (self._tmp_video, self._tmp_wav):
                Path(p).unlink(missing_ok=True)
        elif started:
            self._mux()
        return self._result(self._fmt())

    def _fmt(self) -> str:
        return "wav" if self.output_path.lower().endswith(".wav") else "mp4"

    def _audio_offset(self) -> float:
        """Seconds of audio recorded before the first video frame (> 0: trim it;
        < 0: the audio started late, pad it) so both tracks start together."""
        if self._audio is None or self._first_frame_elapsed is None:
            return 0.0
        a0 = self._audio._first_audio_elapsed
        return 0.0 if a0 is None else float(self._first_frame_elapsed - a0)

    def _mux(self):
        import av
        if not Path(self._tmp_video).exists() or self._frames == 0:
            log.error("no video frames captured")
            # The camera/screen produced nothing, but the audio track may be fine:
            # keep it as the recording rather than returning an empty video.
            wav = Path(self._tmp_wav)
            if self.has_audio and wav.exists() and wav.stat().st_size > 1024:
                dest = Path(self.output_path).with_suffix(".wav")
                try:
                    wav.replace(dest)
                    self.output_path = str(dest)
                    self.kind = "audio"
                    return
                except OSError:
                    log.exception("keeping the audio of a frameless recording")
            self.output_path = self._tmp_video
            return
        if not self.has_audio:
            # video only
            try:
                Path(self._tmp_video).replace(self.output_path)
            except OSError:
                self.output_path = self._tmp_video
            return
        try:
            self._mux_av(av)
            Path(self._tmp_video).unlink(missing_ok=True)
            Path(self._tmp_wav).unlink(missing_ok=True)
        except Exception:
            log.exception("muxing failed; keeping video-only file")
            try:
                Path(self.output_path).unlink(missing_ok=True)
                Path(self._tmp_video).replace(self.output_path)
            except OSError:
                self.output_path = self._tmp_video

    def _mux_av(self, av) -> None:
        import numpy as np
        import soundfile as sf
        from fractions import Fraction
        out = av.open(self.output_path, mode="w")
        vin = av.open(self._tmp_video)
        try:
            v_in = vin.streams.video[0]
            v_out = out.add_stream_from_template(v_in)
            with sf.SoundFile(self._tmp_wav) as wav:
                sr = int(wav.samplerate)
                a_out = out.add_stream("aac", rate=sr)
                a_out.layout = "mono"
                off = int(round(self._audio_offset() * sr))
                pad = 0
                if off > 0:
                    wav.seek(min(off, wav.frames))
                elif off < 0:
                    pad = -off

                for packet in vin.demux(v_in):
                    if packet.dts is None:
                        continue
                    packet.stream = v_out
                    out.mux(packet)

                pts = 0

                def encode(mono):
                    nonlocal pts
                    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).reshape(1, -1)
                    fr = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
                    fr.sample_rate = sr
                    fr.pts, fr.time_base = pts, Fraction(1, sr)
                    pts += pcm.shape[1]
                    for p in a_out.encode(fr):
                        out.mux(p)

                while pad > 0:
                    n = min(pad, sr)
                    encode(np.zeros(n, dtype="float32"))
                    pad -= n
                for blk in wav.blocks(blocksize=sr * 5, dtype="float32", always_2d=True):
                    encode(blk.mean(axis=1) if blk.shape[1] > 1 else blk[:, 0])
                for p in a_out.encode(None):
                    out.mux(p)
        finally:
            out.close()
            vin.close()


def make_recorder(cfg: RecordingConfig) -> BaseRecorder:
    if cfg.kind in ("screen", "camera"):
        return VideoRecorder(cfg)
    return AudioRecorder(cfg)
