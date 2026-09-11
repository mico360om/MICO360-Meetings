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
from dataclasses import dataclass, field
from pathlib import Path

from ..config import TMP_DIR

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


def system_audio_supported() -> bool:
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

    # -- clock --------------------------------------------------------------
    def elapsed(self) -> float:
        if self.state == IDLE:
            return 0.0
        if self.state == PAUSED:
            return self._pause_at - self._t0 - self._paused_total
        if self.state in (STOPPED, CANCELLED, ERROR):
            return self._final_elapsed
        return time.time() - self._t0 - self._paused_total

    def _freeze_clock(self) -> None:
        """Capture elapsed time while still running, before flipping to STOPPED."""
        if self.state == PAUSED:
            self._final_elapsed = self._pause_at - self._t0 - self._paused_total
        elif self.state == RECORDING:
            self._final_elapsed = time.time() - self._t0 - self._paused_total

    def level(self) -> float:
        return 0.0 if self.state == PAUSED else self._level

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
            self._pause_at = time.time()
            self.state = PAUSED

    def resume(self) -> None:
        if self.state == PAUSED:
            self._paused_total += time.time() - self._pause_at
            self.state = RECORDING

    def cancel(self) -> None:
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


# ---------------------------------------------------------------------------
# Audio-only recorder  (WAV or MP3)
# ---------------------------------------------------------------------------
class AudioRecorder(BaseRecorder):
    kind = "audio"

    def __init__(self, cfg: RecordingConfig):
        super().__init__(cfg)
        ext = "mp3" if cfg.audio_format == "mp3" else "wav"
        self.output_path = str(TMP_DIR / f"recording_{int(time.time())}.{ext}")
        self._rate = cfg.samplerate
        self._stop_flag = threading.Event()
        self._thread: threading.Thread | None = None
        self._frames_captured = 0
        # live-transcription tap (opt-in): captured mic frames buffered for a
        # separate worker to pull and transcribe as recording proceeds.
        self._live_on = False
        self._live_lock = threading.Lock()
        self._live_buf: list = []
        self._live_rate = cfg.samplerate

    def enable_live(self) -> None:
        self._live_on = True

    def _live_push(self, mono_f32) -> None:
        if not self._live_on:
            return
        try:
            with self._live_lock:
                self._live_buf.append(mono_f32)
        except Exception:
            pass

    def pull_live(self):
        """Return (new_samples, rate) captured since the last pull, then clear."""
        import numpy as np
        with self._live_lock:
            if not self._live_buf:
                return None, self._live_rate
            arr = np.concatenate(self._live_buf).astype("float32")
            self._live_buf = []
        return arr, self._live_rate

    def start(self) -> None:
        # Capture runs in a background thread so opening the audio device can
        # NEVER freeze the UI (some drivers/host APIs block on open). The UI
        # polls state/level; errors surface via state == ERROR.
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        # mic-only uses the proven single-stream path; system/both use the
        # multi-source capture+mix path.
        target = self._loop if self.cfg.source == "mic" else self._loop_sources
        self._thread = threading.Thread(target=target, daemon=True)
        self._t0 = time.time()
        self.started_at = self._t0
        self.state = RECORDING
        self._thread.start()

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

    def _loop_sources(self):
        # Each source captures to its own temp WAV in a thread; on stop they are
        # mixed. Mic uses sounddevice; system uses true WASAPI loopback (soundcard)
        # so it works WITHOUT the user enabling "Stereo Mix".
        self._frames_captured = 0
        ts = int(self._t0)
        temps, threads = [], []
        try:
            plan = self._source_plan()
            if not plan:
                raise RuntimeError("No audio source selected.")
            for kind in plan:
                tmp = str(TMP_DIR / f"_src_{kind}_{ts}.wav")
                temps.append(tmp)
                target = self._capture_mic if kind == "mic" else self._capture_system
                th = threading.Thread(target=target, args=(tmp,), daemon=True)
                th.start()
                threads.append(th)

            start = time.time()
            while not self._stop_flag.is_set():
                time.sleep(0.1)
                if (self.state == RECORDING and self._frames_captured == 0
                        and time.time() - start > 6.0):
                    raise RuntimeError("No audio is being received. For system audio make "
                                       "sure something is playing; for the mic check it isn't muted.")
        except Exception as exc:
            self.error = str(exc); self.state = ERROR
            log.exception("multi-source audio capture failed")
        finally:
            if self.state != ERROR:
                self.state = STOPPED
            for th in threads:
                th.join(timeout=6)
            try:
                self._finalize_sources(temps)
            except Exception:
                log.exception("finalizing multi-source recording failed")

    def _capture_mic(self, temp: str):
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
        writer = stream = None
        try:
            device = self.cfg.mic_index
            if device is None:
                d = default_microphone()
                device = d.index if d else None
            rate = self.cfg.samplerate
            writer = sf.SoundFile(temp, mode="w", samplerate=rate, channels=1, subtype="PCM_16")

            def cb(indata, frames, time_info, status):  # noqa: ARG001
                if self.state != RECORDING:
                    return
                try:
                    data = indata if indata.ndim == 1 or indata.shape[1] == 1 \
                        else indata.mean(axis=1, keepdims=True)
                    self._frames_captured += frames
                    self._level = float(min(1.0, float(np.abs(data).max()) * 1.4))
                    if self._live_on:
                        self._live_rate = self.cfg.samplerate
                        self._live_push(np.asarray(data).reshape(-1).copy())
                    writer.write(data.copy())
                except Exception:
                    pass

            stream = sd.InputStream(samplerate=rate, channels=1, device=device,
                                    dtype="float32", blocksize=1600, callback=cb)
            stream.start()
            while not self._stop_flag.is_set():
                time.sleep(0.05)
        except Exception:
            log.warning("mic source capture failed", exc_info=True)
        finally:
            try:
                if stream is not None:
                    stream.stop(); stream.close()
            except Exception:
                pass
            try:
                if writer is not None:
                    writer.close()
            except Exception:
                pass

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
                    if self.state == PAUSED:
                        continue
                    d = data.reshape(-1) if getattr(data, "ndim", 1) > 1 else data
                    self._frames_captured += len(d)
                    self._level = float(min(1.0, float(np.abs(d).max()) * 1.4))
                    writer.write(d.astype("float32"))
        except Exception:
            log.warning("system-audio (loopback) capture failed", exc_info=True)
        finally:
            try:
                if writer is not None:
                    writer.close()
            except Exception:
                pass

    def _finalize_sources(self, temps: list[str]):
        import numpy as np
        import soundfile as sf
        temps = [t for t in temps if Path(t).exists() and Path(t).stat().st_size > 1024]
        if not temps:
            return
        if len(temps) == 1 and self.cfg.audio_format != "mp3":
            try:
                Path(temps[0]).replace(self.output_path)
                return
            except OSError:
                pass
        target = 16000
        arrays = []
        for t in temps:
            data, sr = sf.read(t, dtype="float32", always_2d=False)
            if getattr(data, "ndim", 1) > 1:
                data = data.mean(axis=1)
            if sr != target and len(data):
                n = int(len(data) * target / sr)
                data = np.interp(np.linspace(0, 1, n, endpoint=False),
                                 np.linspace(0, 1, len(data), endpoint=False), data).astype("float32")
            arrays.append(data)
        n = max((len(a) for a in arrays), default=0)
        mix = np.zeros(n, dtype="float32")
        for a in arrays:
            mix[:len(a)] += a
        peak = float(np.abs(mix).max()) if n else 0.0
        if peak > 1.0:
            mix /= peak
        if self.cfg.audio_format == "mp3":
            import av
            c = av.open(self.output_path, mode="w")
            stm = c.add_stream("libmp3lame", rate=target); stm.layout = "mono"
            frame = av.AudioFrame.from_ndarray((mix * 32767).astype("int16").reshape(1, -1),
                                               format="s16", layout="mono")
            frame.sample_rate = target
            for p in stm.encode(frame):
                c.mux(p)
            for p in stm.encode(None):
                c.mux(p)
            c.close()
        else:
            sf.write(self.output_path, mix, target, subtype="PCM_16")
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

        def callback(indata, frames, time_info, status):  # noqa: ARG001
            if self.state != RECORDING:
                return
            try:
                self._frames_captured += frames
                self._level = float(min(1.0, float(np.abs(indata).max()) * 1.4))
                if self._live_on:
                    self._live_rate = self._rate
                    self._live_push(indata.reshape(-1).copy())
                if self._av_stream is not None:
                    import av as _av
                    frame = _av.AudioFrame.from_ndarray(
                        (indata.T * 32767).astype(np.int16), format="s16", layout="mono")
                    frame.sample_rate = self._rate
                    for packet in self._av_stream.encode(frame):
                        self._container.mux(packet)
                elif self._writer is not None:
                    self._writer.write(indata.copy())
            except Exception:
                log.warning("audio write error", exc_info=True)

        box: dict = {"stream": None, "err": None}

        def _open():
            candidates = input_candidates(sd, self.cfg.mic_index)
            if not candidates:
                box["err"] = RuntimeError("No microphone detected. Connect a microphone and try again.")
                return
            for device in candidates:
                for rate_pref in (self.cfg.samplerate, None):
                    rate = rate_pref
                    if rate is None:
                        try:
                            rate = int(sd.query_devices(device).get("default_samplerate") or 44100)
                        except Exception:
                            rate = 44100
                    try:
                        self._rate = rate
                        self._open_writer(rate)
                        st = sd.InputStream(samplerate=rate, channels=1, device=device,
                                            dtype="float32", blocksize=1600, callback=callback)
                        st.start()
                        box["stream"] = st
                        box["device"] = device
                        box["err"] = None
                        log.info("audio recording on device %s @ %sHz", device, rate)
                        return
                    except Exception as exc:
                        box["err"] = exc
                        self._close_writer(finalize=False)

        try:
            # Open the device in a sub-thread; if a broken/busy device blocks the
            # native open call, we don't wait forever.
            opener = threading.Thread(target=_open, daemon=True)
            opener.start()
            opener.join(timeout=6)
            stream = box["stream"]
            if opener.is_alive() or stream is None:
                raise RuntimeError(str(box["err"]) if box["err"] else
                                   "Microphone did not respond (it may be busy, muted or disabled). "
                                   "Try selecting a different microphone.")

            # Watchdog: device opened but delivers no audio (muted / no permission).
            start = time.time()
            while not self._stop_flag.is_set():
                time.sleep(0.1)
                if (self.state == RECORDING and self._frames_captured == 0
                        and time.time() - start > 4.0):
                    raise RuntimeError("Microphone opened but no audio is being received. "
                                       "Check it isn't muted or disabled, or select a different microphone.")
        except Exception as exc:
            self.error = str(exc)
            self.state = ERROR
            log.exception("audio capture failed")
        finally:
            if self.state != ERROR:
                self.state = STOPPED
            # Closing a wedged stream can block — do it with a timeout so stop() returns.
            st = box.get("stream")
            if st is not None:
                closer = threading.Thread(target=lambda: self._safe_close(st), daemon=True)
                closer.start(); closer.join(timeout=3)
            self._close_writer(finalize=True)

    @staticmethod
    def _safe_close(stream):
        try:
            stream.stop(); stream.close()
        except Exception:
            pass

    def stop(self) -> RecordingResult:
        if self.state in (STOPPED, CANCELLED):
            return self._result(self.cfg.audio_format)
        self._freeze_clock()
        self.state = STOPPED
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=8)
        return self._result(self.cfg.audio_format)


# ---------------------------------------------------------------------------
# Video recorder (screen or camera) + optional mic audio  -> MP4
# ---------------------------------------------------------------------------
class VideoRecorder(BaseRecorder):
    kind = "screen"

    def __init__(self, cfg: RecordingConfig):
        super().__init__(cfg)
        self.kind = cfg.kind if cfg.kind in ("screen", "camera") else "screen"
        ts = int(time.time())
        self.output_path = str(TMP_DIR / f"recording_{ts}.mp4")
        self._tmp_video = str(TMP_DIR / f"_vid_{ts}.mp4")
        self._tmp_wav = str(TMP_DIR / f"_aud_{ts}.wav")
        self._frames = 0
        self._stop_flag = threading.Event()
        self._vthread: threading.Thread | None = None
        self._athread: threading.Thread | None = None

    def file_size(self) -> int:
        if self.state in (STOPPED, CANCELLED):
            return super().file_size()
        total = 0
        for p in (self._tmp_video, self._tmp_wav):
            try:
                total += Path(p).stat().st_size
            except OSError:
                pass
        return total

    def start(self) -> None:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        # audio first so we can downgrade gracefully if no mic
        if self.cfg.include_audio and has_microphone():
            self.has_audio = True
            self._athread = threading.Thread(target=self._audio_loop, daemon=True)
        else:
            self.has_audio = False
            self._athread = None
        self._vthread = threading.Thread(target=self._video_loop, daemon=True)

        self._t0 = time.time()
        self.started_at = self._t0
        self.state = RECORDING
        if self._athread:
            self._athread.start()
        self._vthread.start()

    # -- capture loops ------------------------------------------------------
    def _video_loop(self):
        import av
        import numpy as np
        try:
            grab, size = self._make_source()
            self.resolution = f"{size[0]}x{size[1]}"
            container = av.open(self._tmp_video, mode="w")
            stream = container.add_stream("libx264", rate=self.cfg.fps)
            stream.width, stream.height = size
            stream.pix_fmt = "yuv420p"
            stream.options = {"preset": "ultrafast", "crf": "26"}

            frame_interval = 1.0 / max(1, self.cfg.fps)
            next_t = time.time()
            while not self._stop_flag.is_set():
                if self.state == PAUSED:
                    time.sleep(0.03)
                    next_t = time.time()
                    continue
                rgb = grab()
                if rgb is None:
                    time.sleep(frame_interval)
                    continue
                vframe = av.VideoFrame.from_ndarray(np.ascontiguousarray(rgb), format="rgb24")
                # Let the encoder assign CFR timing (rate = fps); manual pts/time_base
                # is fragile across PyAV versions and not needed here.
                for packet in stream.encode(vframe):
                    container.mux(packet)
                self._frames += 1
                next_t += frame_interval
                sleep = next_t - time.time()
                if sleep > 0:
                    time.sleep(sleep)
                else:
                    next_t = time.time()
            for packet in stream.encode(None):
                container.mux(packet)
            container.close()
            self._close_source()
        except Exception as exc:
            self.error = str(exc)
            self.state = ERROR
            log.exception("video capture failed")

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

    def _audio_loop(self):
        # Callback stream + host-API fallback (blocking read() and the default
        # device can fail with PaErrorCode -9999 on some Windows machines).
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
        writer = stream = None
        try:
            writer = sf.SoundFile(self._tmp_wav, mode="w", samplerate=self.cfg.samplerate,
                                  channels=1, subtype="PCM_16")

            def callback(indata, frames, time_info, status):  # noqa: ARG001
                if self.state == PAUSED:
                    return
                try:
                    self._level = float(min(1.0, float(np.abs(indata).max()) * 1.4))
                    writer.write(indata.copy())
                except Exception:
                    pass

            last = None
            for device in input_candidates(sd, self.cfg.mic_index):
                try:
                    stream = sd.InputStream(samplerate=self.cfg.samplerate, channels=1,
                                            device=device, dtype="float32", blocksize=1600,
                                            callback=callback)
                    stream.start()
                    last = None
                    break
                except Exception as exc:
                    last = exc
                    stream = None
            if stream is None:
                raise RuntimeError(str(last))
            while not self._stop_flag.is_set():
                time.sleep(0.05)
        except Exception:
            log.warning("audio capture failed; continuing video-only", exc_info=True)
            self.has_audio = False
        finally:
            try:
                if stream is not None:
                    stream.stop(); stream.close()
            except Exception:
                pass
            try:
                if writer is not None:
                    writer.close()
            except Exception:
                pass

    # -- stop + mux ---------------------------------------------------------
    def stop(self) -> RecordingResult:
        if self.state in (STOPPED, CANCELLED):
            return self._result("mp4")
        was_recording = self.state in (RECORDING, PAUSED)
        self._freeze_clock()
        self.state = STOPPED
        self._stop_flag.set()
        if self._vthread:
            self._vthread.join(timeout=10)
        if self._athread:
            self._athread.join(timeout=5)
        if was_recording:
            self._mux()
        return self._result("mp4")

    def _mux(self):
        import av
        if not Path(self._tmp_video).exists() or self._frames == 0:
            log.error("no video frames captured")
            self.output_path = self._tmp_video
            return
        if not (self.has_audio and Path(self._tmp_wav).exists()
                and Path(self._tmp_wav).stat().st_size > 1024):
            # video only
            try:
                Path(self._tmp_video).replace(self.output_path)
            except OSError:
                self.output_path = self._tmp_video
            return
        try:
            out = av.open(self.output_path, mode="w")
            vin = av.open(self._tmp_video)
            v_in = vin.streams.video[0]
            v_out = out.add_stream_from_template(v_in)
            ain = av.open(self._tmp_wav)
            a_in = ain.streams.audio[0]
            a_out = out.add_stream("aac", rate=self.cfg.samplerate)

            for packet in vin.demux(v_in):
                if packet.dts is None:
                    continue
                packet.stream = v_out
                out.mux(packet)
            for frame in ain.decode(a_in):
                frame.pts = None
                for p in a_out.encode(frame):
                    out.mux(p)
            for p in a_out.encode(None):
                out.mux(p)
            out.close(); vin.close(); ain.close()
            Path(self._tmp_video).unlink(missing_ok=True)
            Path(self._tmp_wav).unlink(missing_ok=True)
        except Exception:
            log.exception("muxing failed; keeping video-only file")
            try:
                Path(self._tmp_video).replace(self.output_path)
            except OSError:
                self.output_path = self._tmp_video


def make_recorder(cfg: RecordingConfig) -> BaseRecorder:
    if cfg.kind in ("screen", "camera"):
        return VideoRecorder(cfg)
    return AudioRecorder(cfg)
