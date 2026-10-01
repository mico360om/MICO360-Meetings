"""Regression tests for the capture fixes in docs/BUG_REPORT.md
(recording, transcription, diarization, meeting detection).

    python tests/fixes_capture.py

  H9   screen/camera recordings honour "System audio" / "Mic + System"
  H10  video timestamps follow the clock, so video length matches audio
  H11  diarization is vectorised, capped and cancellable
  H12  switching browser tabs during a Meet call doesn't stop auto-record
  H13  a device lost mid-recording is detected (reopened, or a clear error)
  H3   recording-panel worker threads are never dropped while running
  M31  mic + system audio are mixed in blocks, not in RAM; failures are reported
  M32  Stop & Save runs off the GUI thread
  M33  GPU->CPU fallback is remembered; live transcription falls back too
  M34  in Mic + System mode a mic that won't open falls back / is reported
  M35  Outlook lookup uses the locale date format and never launches Outlook
  M36  Teams section windows ("Chat | Jane | Microsoft Teams") aren't meetings

Runs against a THROWAWAY data folder (LOCALAPPDATA is redirected before the app
is imported) and needs no microphone, camera, network, Ollama or Whisper model:
audio devices, the loopback device, video frames, Whisper and Outlook are faked.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import types
from datetime import datetime
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mico360_capture_"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONUTF8", "1")
ROOT = Path(os.environ.get("MICO360_TEST_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np                                      # noqa: E402
import soundfile as sf                                  # noqa: E402
from PySide6.QtCore import QThread, QTimer              # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360 import config                              # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"
config.ensure_dirs()

from mico360.core import recording as R                 # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


def dominant_freqs(x: np.ndarray, sr: int, k: int = 2) -> list[int]:
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    top = np.argsort(spec)[-k * 5:][::-1]
    out: list[int] = []
    for i in top:
        f = int(round(freqs[i]))
        if all(abs(f - g) > 20 for g in out):
            out.append(f)
        if len(out) == k:
            break
    return sorted(out)


def has_freq(x: np.ndarray, sr: int, f0: float, k: int = 2) -> bool:
    return any(abs(f - f0) < 15 for f in dominant_freqs(x, sr, k))


def pump(until, timeout: float = 10.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(until())


def sine(freq: float, secs: float, rate: int, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(secs * rate)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype("float32")


class Toast:
    def __init__(self):
        self.msgs: list[tuple[str, str]] = []

    def show_message(self, msg, kind="info", ms=0):
        self.msgs.append((kind, msg))


# -----------------------------------------------------------------------------
# Fake audio devices (sounddevice + soundcard) — no hardware needed
# -----------------------------------------------------------------------------
class FakeStream:
    def __init__(self, sd, device, rate, blocksize, callback, stall, freq):
        self.sd, self.device, self.rate = sd, device, int(rate)
        self.blocksize, self.callback, self.stall, self.freq = blocksize, callback, stall, freq
        self._closed = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        n, t0 = 0, time.time()
        dt = self.blocksize / self.rate
        while not self._closed.is_set():
            if self.stall is not None and time.time() - t0 > self.stall:
                self.sd.fail.add(self.device)          # the device was "unplugged"
                return
            t = (n + np.arange(self.blocksize)) / self.rate
            blk = (0.3 * np.sin(2 * np.pi * self.freq * t)).astype("float32").reshape(-1, 1)
            try:
                self.callback(blk, self.blocksize, None, None)
            except Exception:
                pass
            n += self.blocksize
            self._closed.wait(dt)

    def stop(self):
        self._closed.set()

    def close(self):
        self._closed.set()


class FakeSD(types.ModuleType):
    """Stand-in for the `sounddevice` module."""

    def __init__(self, n_devices=2, fail=(), stall_after=None, default_rate=44100, freq=440.0):
        super().__init__("sounddevice")
        self.devices = [{"name": f"Mic {i}", "max_input_channels": 1, "max_output_channels": 0,
                         "default_samplerate": default_rate} for i in range(n_devices)]
        self.fail = set(fail)                  # device or (device, rate) that won't open
        self.stall_after = dict(stall_after or {})
        self.opened: list[tuple[int, int]] = []
        self.refreshes = 0
        self.freq = freq
        self.default = types.SimpleNamespace(device=[0, None])

    def query_hostapis(self):
        return [{"name": "Windows WASAPI", "default_input_device": 0}]

    def query_devices(self, idx=None):
        return self.devices if idx is None else self.devices[idx]

    def _terminate(self):
        self.refreshes += 1

    def _initialize(self):
        pass

    def InputStream(self, samplerate, channels, device, dtype, blocksize, callback):  # noqa: N802
        if device in self.fail or (device, int(samplerate)) in self.fail:
            raise RuntimeError("Error opening InputStream: Unanticipated host error [PaErrorCode -9999]")
        self.opened.append((device, int(samplerate)))
        return FakeStream(self, device, samplerate, blocksize, callback,
                          self.stall_after.pop(device, None), self.freq)


class FakeLoopRecorder:
    def __init__(self, rate, freq):
        self.rate, self.freq, self.n = rate, freq, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def record(self, numframes):
        time.sleep(numframes / self.rate)
        t = (self.n + np.arange(numframes)) / self.rate
        self.n += numframes
        return (0.3 * np.sin(2 * np.pi * self.freq * t)).astype("float32").reshape(-1, 1)


def fake_soundcard(ok=True, freq=1000.0):
    sc = types.ModuleType("soundcard")
    sc.default_speaker = lambda: types.SimpleNamespace(name="Speakers")

    def get_microphone(name, include_loopback=False):
        if not ok:
            raise RuntimeError("no loopback device")
        return types.SimpleNamespace(
            recorder=lambda samplerate, channels, blocksize: FakeLoopRecorder(samplerate, freq))
    sc.get_microphone = get_microphone
    return sc


class Devices:
    """Context manager installing fake sounddevice/soundcard modules."""

    def __init__(self, sd=None, sc=None):
        self.sd, self.sc = sd, sc
        self.saved = {}

    def __enter__(self):
        for name, mod in (("sounddevice", self.sd), ("soundcard", self.sc)):
            self.saved[name] = sys.modules.get(name)
            if mod is not None:
                sys.modules[name] = mod
        return self

    def __exit__(self, *a):
        for name, mod in self.saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        return False


def record_for(rec, secs: float):
    rec.start()
    t0 = time.time()
    while time.time() - t0 < secs and rec.state in (R.RECORDING, R.PAUSED):
        time.sleep(0.05)
    return rec.stop()


def read_media_audio(path: str):
    """Decode the audio track of any media file -> (mono float32, rate)."""
    import av
    out, rate = [], None
    with av.open(path) as c:
        st = c.streams.audio[0]
        rs = av.AudioResampler(format="flt", layout="mono", rate=st.rate)
        for fr in c.decode(st):
            for r in rs.resample(fr):
                out.append(r.to_ndarray().reshape(-1))
                rate = st.rate
    return (np.concatenate(out) if out else np.zeros(0, "float32")), rate


def stream_durations(path: str) -> dict:
    import av
    d = {}
    with av.open(path) as c:
        for st in c.streams:
            if st.duration is not None:
                d[st.type] = float(st.duration * st.time_base)
    return d


# =============================================================================
def test_h13_device_loss() -> None:
    print("H13 — a device lost mid-recording is detected")
    old = (R._STALL_SECONDS, R._REOPEN_SECONDS)
    R._STALL_SECONDS, R._REOPEN_SECONDS = 0.5, 3.0
    try:
        # 1) mic 0 is unplugged after 1 s; another input is available -> reopened
        sd = FakeSD(n_devices=2, stall_after={0: 1.0})
        with Devices(sd):
            rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="mic"))
            res = record_for(rec, 3.2)
        info = sf.info(res.path) if Path(res.path).exists() else None
        check("stalled microphone is detected and another input reopened",
              rec.reopened == 1 and (1, 16000) in sd.opened and rec.state == R.STOPPED,
              f"reopened={rec.reopened} opened={sd.opened} state={rec.state}")
        check("PortAudio device list refreshed before reopening", sd.refreshes >= 1)
        check("the user is told the mic dropped out",
              any("reopened" in w for w in rec.all_warnings()), f"{rec.all_warnings()}")
        check("recording continued after the reopen (file longer than the 1 s before the loss)",
              info is not None and info.duration > 1.8, f"{info.duration if info else None}")

        # 2) no other input -> ERROR with a clear message, the audio so far is kept
        sd = FakeSD(n_devices=1, stall_after={0: 1.0})
        R._REOPEN_SECONDS = 1.0
        with Devices(sd):
            rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="mic"))
            rec.start()
            pump(lambda: rec.state == R.ERROR, 8)
            elapsed = time.time() - rec._t0
            res = rec.stop()
        check("an unrecoverable device loss ends in ERROR (not a silently short file)",
              "unplugged" in rec.error, rec.error)
        dur = sf.info(res.path).duration if Path(res.path).exists() else -1
        check("the audio recorded before the loss is saved", dur > 0.8, f"dur={dur}")
        check("reported duration = audio actually written (frames / rate), not the clock",
              abs(res.duration - dur) < 0.15 and elapsed - res.duration > 0.8,
              f"reported={res.duration:.2f} file={dur:.2f} clock={elapsed:.2f}")

        # 3) the panel keeps and shows that partial recording
        from mico360.ui.recording_panel import RecordingPanel
        toast = Toast()
        panel = RecordingPanel(toast=toast, ctx=None)
        failed = []
        panel.recordingFailed.connect(lambda: failed.append(True))
        rec2 = R.AudioRecorder(R.RecordingConfig(kind="audio"))
        rec2.output_path = res.path
        rec2.state, rec2.error = R.ERROR, R._LOST_MIC_MSG
        rec2._written = {"mic": (16000, 16000)}
        panel._rec = rec2
        panel._tick()
        pump(lambda: not panel._stopping, 10)
        check("panel: partial recording shown in the summary, failure signalled",
              panel.stack.currentIndex() == 2 and failed == [True]
              and any("was saved" in m for _, m in toast.msgs), f"{toast.msgs}")
    finally:
        R._STALL_SECONDS, R._REOPEN_SECONDS = old


# =============================================================================
def test_m34_mic_fallback() -> None:
    print("M34 — Mic + System: a mic that won't open falls back or is reported")
    # device 0 is broken, device 1 only opens at its own default rate (44.1 kHz)
    sd = FakeSD(n_devices=2, fail={0, (1, 16000)}, default_rate=44100, freq=440.0)
    with Devices(sd, fake_soundcard(True, 1000.0)):
        rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
        res = record_for(rec, 2.0)
    ok = Path(res.path).exists()
    data, sr = sf.read(res.path, dtype="float32") if ok else (np.zeros(1), 16000)
    check("mic opened via device + samplerate fallback", (1, 44100) in sd.opened, f"{sd.opened}")
    check("mixed recording contains the mic (440 Hz) and system audio (1 kHz)",
          ok and has_freq(data, sr, 440) and has_freq(data, sr, 1000), f"{dominant_freqs(data, sr)}")

    sd = FakeSD(n_devices=2, fail={0, 1})
    with Devices(sd, fake_soundcard(True, 1000.0)):
        rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
        res = record_for(rec, 1.5)
    ws = rec.all_warnings()
    check("a mic that can't be opened is reported (recording system audio only)",
          any("microphone could not be opened" in w.lower() and "system audio only" in w for w in ws),
          f"{ws}")
    check("…and the system audio is still recorded",
          rec.state != R.ERROR and Path(res.path).exists()
          and has_freq(sf.read(res.path, dtype="float32")[0], sf.info(res.path).samplerate, 1000))

    sd = FakeSD(n_devices=1, fail={0})
    with Devices(sd, fake_soundcard(False)):
        rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
        rec.start()
        t0 = time.time()
        pump(lambda: rec.state == R.ERROR, 8)
        took = time.time() - t0
        rec.stop()
    check("when every source fails: ERROR at once with the reasons (no 6 s wait)",
          rec.state in (R.ERROR, R.STOPPED) and "microphone" in rec.error.lower()
          and "system audio" in rec.error.lower() and took < 4.0,
          f"took={took:.1f}s err={rec.error!r}")


# =============================================================================
def test_m31_streaming_mix() -> None:
    print("M31 — mic + system audio mixed in blocks (not in RAM)")
    import tracemalloc
    secs = 40
    mic = str(config.TMP_DIR / "_src_mic_m31.wav")
    sys_ = str(config.TMP_DIR / "_src_sys_m31.wav")
    m = sine(440, secs, 16000)
    s = sine(1000, secs, 48000)
    sf.write(mic, m, 16000)
    sf.write(sys_, np.stack([s, s], axis=1), 48000)
    out = str(config.TMP_DIR / "m31_mix.wav")
    tracemalloc.start()
    R.mix_sources_to_file([mic, sys_], out, "wav", target=16000, block_seconds=1.0)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    full = (len(s) * 2 + len(m)) * 4          # what loading both sources at once costs
    data, sr = sf.read(out, dtype="float32")
    check("streamed mix contains both sources", has_freq(data, sr, 440) and has_freq(data, sr, 1000),
          f"{dominant_freqs(data, sr)}")
    check("streamed mix has the full length", sr == 16000 and abs(len(data) - secs * 16000) <= 2,
          f"{len(data)}")
    ref = m + np.interp(np.linspace(0, 1, secs * 16000, endpoint=False),
                        np.linspace(0, 1, len(s), endpoint=False), s).astype("float32")
    n = min(len(ref), len(data))
    err = float(np.abs(ref[:n] - data[:n]).max())
    check("block-wise resample/mix matches a whole-signal mix", err < 2e-3, f"max err {err:.5f}")
    check("peak memory is a small fraction of the sources' size",
          peak < full / 5, f"peak={peak / 1e6:.1f} MB vs full={full / 1e6:.1f} MB")

    # loud sources are still normalised (never clipped)
    a = str(config.TMP_DIR / "_src_mic_loud.wav")
    b = str(config.TMP_DIR / "_src_sys_loud.wav")
    sf.write(a, sine(300, 3, 16000, 0.8), 16000)
    sf.write(b, sine(300, 3, 16000, 0.8), 16000)
    R.mix_sources_to_file([a, b], out, "wav", block_seconds=0.5)
    pk = float(np.abs(sf.read(out, dtype="float32")[0]).max())
    check("a clipping mix is normalised to full scale", 0.97 < pk <= 1.0, f"peak={pk:.3f}")

    # MP3 output is streamed too
    out3 = str(config.TMP_DIR / "m31_mix.mp3")
    R.mix_sources_to_file([mic, sys_], out3, "mp3", block_seconds=2.0)
    d3, r3 = read_media_audio(out3)
    check("MP3 mix decodes with both sources and the full length",
          abs(len(d3) / r3 - secs) < 0.3 and has_freq(d3[: r3 * 10], r3, 440)
          and has_freq(d3[: r3 * 10], r3, 1000), f"{len(d3) / (r3 or 1):.2f}s")

    # a failing mix (e.g. MemoryError) is reported; the captured audio is kept
    real = R.mix_sources_to_file

    def boom(*a, **k):
        raise MemoryError("simulated")
    R.mix_sources_to_file = boom
    try:
        sd = FakeSD(n_devices=1)
        with Devices(sd, fake_soundcard(True)):
            rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
            record_for(rec, 1.2)
    finally:
        R.mix_sources_to_file = real
    temps = [t for t in rec._temps if Path(t).exists()]
    check("a failed mix sets an error and keeps the captured audio",
          "MemoryError" in rec.error and len(temps) == 2 and not Path(rec.output_path).exists(),
          f"err={rec.error!r} temps={temps}")
    from mico360.ui.recording_panel import RecordingPanel
    toast = Toast()
    panel = RecordingPanel(toast=toast, ctx=None)
    failed = []
    panel.recordingFailed.connect(lambda: failed.append(True))
    panel._rec = rec
    panel._finish_stop()
    check("panel says the recording could NOT be saved (no 'Recording saved')",
          failed == [True] and panel.stack.currentIndex() != 2
          and not any("Recording saved" in m for _, m in toast.msgs)
          and any("could not be saved" in m for _, m in toast.msgs), f"{toast.msgs}")
    for t in temps:
        Path(t).unlink(missing_ok=True)


# =============================================================================
class SlowRec:
    """A recorder whose stop() takes a while (like muxing a long video)."""

    def __init__(self, path, delay=1.5):
        self.path, self.delay = path, delay
        self.state = R.RECORDING
        self.cfg = R.RecordingConfig()
        self.error = ""
        self.stop_thread = None
        self.cancelled = False

    def elapsed(self):
        return 1.0

    def level(self):
        return 0.0

    def file_size(self):
        return 0

    def stop(self):
        self.stop_thread = threading.current_thread()
        time.sleep(self.delay)
        self.state = R.STOPPED
        return R.RecordingResult(path=self.path, kind="audio", duration=2.0,
                                 size_bytes=Path(self.path).stat().st_size, fmt="wav",
                                 started_at=time.time())

    def cancel(self):
        self.cancelled = True
        self.stop()
        self.state = R.CANCELLED


def test_m32_async_stop() -> None:
    print("M32 — Stop & Save runs off the GUI thread")
    from mico360.ui.recording_panel import RecordingPanel
    path = str(config.RECORDINGS_DIR / "m32.wav")
    sf.write(path, sine(440, 1, 16000), 16000)

    panel = RecordingPanel(toast=Toast(), ctx=None)
    got = {"file": [], "live": []}
    panel.recordingReady.connect(got["file"].append)
    panel.liveTranscriptReady.connect(got["live"].append)
    rec = SlowRec(path)
    panel._rec = rec
    panel._auto_mode = True
    panel._live_text = "mic-only draft"
    panel.stack.setCurrentIndex(1)
    ticks = [0]
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.__setitem__(0, ticks[0] + 1))
    timer.start()
    t0 = time.time()
    panel._stop()
    took = time.time() - t0
    check("_stop() returns immediately", took < 0.3, f"{took:.2f}s")
    check("panel reports 'busy' while saving (no new recording on top)",
          panel.is_recording() and panel.is_saving())
    workers = list(panel._stop_workers)
    ended = []
    for w in workers:
        w.finished.connect(lambda: ended.append(True))
    pump(lambda: bool(got["file"]), 10)
    timer.stop()
    check("the slow stop ran on a worker thread",
          rec.stop_thread is not None and rec.stop_thread is not threading.main_thread())
    check("GUI kept processing events during the stop", ticks[0] >= 30, f"ticks={ticks[0]}")
    check("auto-record: FULL recording emitted via recordingReady, live draft unused",
          got["file"] == [path] and got["live"] == [], f"{got}")
    check("summary shown after the save", panel.stack.currentIndex() == 2)
    pump(lambda: not panel._stop_workers, 5)
    check("H3: the stop thread was kept until it finished, then released",
          len(workers) == 1 and ended == [True] and not panel._stop_workers)

    # manual recording: the live draft is still offered
    panel2 = RecordingPanel(toast=Toast(), ctx=None)
    got2 = {"file": [], "live": []}
    panel2.recordingReady.connect(got2["file"].append)
    panel2.liveTranscriptReady.connect(got2["live"].append)
    panel2._rec = SlowRec(path, 0.3)
    panel2._live_text = "live words"
    panel2._stop()
    pump(lambda: bool(got2["live"]), 10)
    check("manual recording: live transcript still delivered after async stop",
          got2["live"] == ["live words"] and got2["file"] == [], f"{got2}")

    # cancel runs off the GUI thread too
    panel3 = RecordingPanel(toast=Toast(), ctx=None)
    r3 = SlowRec(path, 0.5)
    panel3._rec = r3
    t0 = time.time()
    panel3._cancel()
    took = time.time() - t0
    pump(lambda: not panel3._stopping, 10)
    check("cancel returns immediately and runs on a worker thread",
          took < 0.3 and r3.cancelled and r3.stop_thread is not threading.main_thread())

    # H3: closing the app while a save is in progress waits for it
    panel4 = RecordingPanel(toast=Toast(), ctx=None)
    panel4._rec = SlowRec(path, 0.8)
    panel4._stop()
    ws = list(panel4._stop_workers)
    panel4.stop_if_active()
    check("H3: stop_if_active() waits for a running save thread",
          ws and all(not w.isRunning() for w in ws))
    pump(lambda: not panel4._stopping, 5)


# =============================================================================
def _frame_source(delay: float, size=(64, 48)):
    w, h = size
    counter = [0]

    def grab():
        time.sleep(delay)
        counter[0] += 1
        img = np.zeros((h, w, 3), np.uint8)
        img[:, : (counter[0] * 4) % w] = 200
        return img
    return grab, size


def test_h10_video_clock() -> None:
    print("H10 — video timing follows the clock")
    check("frame_pts: ms from the first frame, strictly increasing",
          R.frame_pts(10.0, 10.0, -1) == 0 and R.frame_pts(10.2505, 10.0, 0) == 251
          and R.frame_pts(10.2505, 10.0, 251) == 252)
    rec = R.VideoRecorder(R.RecordingConfig(kind="screen", fps=12, include_audio=False))
    rec._make_source = lambda: _frame_source(0.2)          # ~5 fps, far below 12
    rec.start()
    time.sleep(2.4)
    res = rec.stop()
    d = stream_durations(res.path)
    frames = rec._frames
    check("slow capture: video length matches the recording time, not frames/fps",
          abs(d.get("video", 0) - res.duration) < 0.45 and frames / 12 < 1.5,
          f"video={d.get('video')} elapsed={res.duration:.2f} frames={frames}")


def test_h9_video_system_audio() -> None:
    print("H9 — screen/camera recordings use the selected audio source")
    for src in ("system", "both", "mic"):
        vr = R.VideoRecorder(R.RecordingConfig(kind="screen", source=src))
        with Devices(FakeSD(n_devices=1)):
            a = vr._make_audio_recorder()
        check(f"source '{src}': video soundtrack captures '{src}'",
              isinstance(a, R.AudioRecorder) and a.cfg.source == src
              and a.output_path == vr._tmp_wav and a.cfg.audio_format == "wav")
    vr = R.VideoRecorder(R.RecordingConfig(kind="camera", source="both", include_audio=False))
    check("'No audio' still records silent video", vr._make_audio_recorder() is None)

    # end to end: screen + SYSTEM audio -> the MP4 carries the system audio
    with Devices(FakeSD(n_devices=1, freq=440.0), fake_soundcard(True, 1000.0)):
        vr = R.VideoRecorder(R.RecordingConfig(kind="screen", source="system", fps=10))
        vr._make_source = lambda: _frame_source(0.1)
        vr.start()
        time.sleep(2.5)
        res = vr.stop()
    ok = Path(res.path).exists() and res.path.endswith(".mp4")
    audio, rate = read_media_audio(res.path) if ok else (np.zeros(1), 16000)
    check("screen recording with 'System audio' contains the system sound (1 kHz)",
          ok and res.has_audio and has_freq(audio, rate, 1000, k=1), f"{dominant_freqs(audio, rate)}")
    d = stream_durations(res.path) if ok else {}
    check("H10: audio and video tracks have matching lengths",
          ok and abs(d.get("video", 0) - d.get("audio", -9)) < 0.5, f"{d}")

    with Devices(FakeSD(n_devices=1, freq=440.0), fake_soundcard(True, 1000.0)):
        vr = R.VideoRecorder(R.RecordingConfig(kind="camera", source="both", fps=10))
        vr._make_source = lambda: _frame_source(0.1)
        vr.start()
        time.sleep(2.0)
        vr.pause()
        time.sleep(0.3)
        vr.resume()
        time.sleep(0.5)
        res = vr.stop()
    audio, rate = read_media_audio(res.path)
    check("camera recording with 'Mic + System' contains both (440 Hz + 1 kHz)",
          has_freq(audio, rate, 440) and has_freq(audio, rate, 1000), f"{dominant_freqs(audio, rate)}")


def test_h10_audio_alignment() -> None:
    print("H10 — audio before the first video frame is trimmed")
    rec = R.VideoRecorder(R.RecordingConfig(kind="screen", fps=10))
    import av
    from fractions import Fraction
    c = av.open(rec._tmp_video, mode="w")
    st = c.add_stream("libx264", rate=10)
    st.width, st.height, st.pix_fmt = 64, 48, "yuv420p"
    st.time_base = Fraction(1, 1000)
    st.codec_context.time_base = Fraction(1, 1000)
    for i in range(20):
        fr = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")
        fr.pts, fr.time_base = i * 100, Fraction(1, 1000)
        for p in st.encode(fr):
            c.mux(p)
    for p in st.encode(None):
        c.mux(p)
    c.close()
    # 1 s of 440 Hz captured BEFORE the camera delivered its first frame, then 1 kHz
    sf.write(rec._tmp_wav, np.concatenate([sine(440, 1.0, 16000), sine(1000, 2.0, 16000)]), 16000)
    rec._frames, rec.has_audio = 20, True
    rec._audio = types.SimpleNamespace(_first_audio_elapsed=0.0)
    rec._first_frame_elapsed = 1.0
    rec._mux()
    audio, rate = read_media_audio(rec.output_path)
    head = audio[int(0.1 * rate): int(0.6 * rate)]
    check("audio is aligned to the first video frame (leading 1 s trimmed)",
          abs(len(audio) / rate - 2.0) < 0.15 and has_freq(head, rate, 1000, k=1)
          and not has_freq(head, rate, 440, k=1), f"len={len(audio) / rate:.2f}s")


# =============================================================================
def _old_agglomerative(embeddings, threshold, max_k):
    """The pre-fix implementation, as a reference for the vectorised one."""
    clusters = [[i] for i in range(len(embeddings))]
    emb = embeddings

    def cdist(c1, c2):
        return np.mean([float(1.0 - np.dot(emb[i], emb[j])) for i in c1 for j in c2])

    while len(clusters) > 1:
        best, bi, bj = 1e9, -1, -1
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = cdist(clusters[i], clusters[j])
                if d < best:
                    best, bi, bj = d, i, j
        if best > threshold and len(clusters) <= max_k:
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


def _relabel(labels):
    remap = {}
    return [remap.setdefault(x, len(remap)) for x in labels]


def _synthetic_embeddings(n, k, seed, noise=0.35):
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(k, 26))
    out = []
    for i in range(n):
        v = centers[rng.integers(k)] + noise * rng.normal(size=26)
        out.append((v / np.linalg.norm(v)).astype("float32"))
    return out


def test_h11_diarization() -> None:
    print("H11 — diarization is vectorised, capped and cancellable")
    from mico360.core import diarization as D
    same = True
    for seed in range(6):
        emb = _synthetic_embeddings(70, 3 + seed % 3, seed)
        for thr, mk in ((0.22, 4), (0.4, 2), (0.05, 6)):
            a = _relabel(_old_agglomerative(emb, thr, mk))
            b = _relabel(D._agglomerative(emb, thr, mk))
            same &= a == b
    check("vectorised clustering gives the same speakers as the old algorithm", same)

    emb = _synthetic_embeddings(600, 3, 42)
    t0 = time.time()
    labels = D._agglomerative(emb, 0.22, 4)
    took = time.time() - t0
    check("600 segments cluster in well under the old ~2 minutes", took < 5.0 and len(labels) == 600,
          f"{took:.2f}s")

    emb = _synthetic_embeddings(2500, 3, 7)
    t0 = time.time()
    labels = D._cluster(emb, [1.0 + (i % 7) for i in range(2500)], 0.22, 4)
    took = time.time() - t0
    check("very long meetings (2500 segments) are capped and finish quickly",
          len(labels) == 2500 and max(labels) < 4 and took < 20.0, f"{took:.2f}s")

    calls = [0]

    def cancel_after_5():
        calls[0] += 1
        return calls[0] > 5
    try:
        D._agglomerative(_synthetic_embeddings(200, 3, 1), 0.22, 4, cancel=cancel_after_5)
        raised = False
    except InterruptedError:
        raised = True
    check("clustering honours cancel", raised)

    # MFCC vectorisation keeps the old values
    sig = np.random.default_rng(3).normal(size=16000).astype("float32") * 0.1
    old = []
    emph = np.append(sig[0], sig[1:] - 0.97 * sig[:-1])
    win = np.hamming(D._FRAME).astype("float32")
    fb = D._mel_filterbank()
    dct = D._dct_matrix()
    for i in range(1 + (len(emph) - D._FRAME) // D._HOP):
        fr = emph[i * D._HOP: i * D._HOP + D._FRAME] * win
        mag = np.abs(np.fft.rfft(fr, D._FFT)) ** 2 / D._FFT
        old.append(dct @ np.log(np.maximum(fb @ mag, 1e-10)))
    new = D._mfcc(sig)
    check("vectorised MFCCs match the per-frame computation",
          new.shape == np.asarray(old).shape and np.allclose(new, np.asarray(old), atol=1e-3))

    # end to end on a file, with the public signature unchanged + cancel
    wav = str(config.TMP_DIR / "h11.wav")
    sf.write(wav, np.concatenate([sine(180, 3, 16000), sine(2400, 3, 16000)] * 2), 16000)
    segs = [types.SimpleNamespace(start=float(i * 3), end=float(i * 3 + 3), speaker=None)
            for i in range(4)]
    n = D.apply_to_segments(wav, segs)
    check("apply_to_segments(path, segments) still works (default signature)",
          n >= 1 and all(s.speaker for s in segs), f"n={n}")
    try:
        D.apply_to_segments(wav, segs, cancel=lambda: True)
        raised = False
    except InterruptedError:
        raised = True
    check("Cancel during 'Identifying speakers…' stops diarization", raised)


# =============================================================================
class FakeWhisper:
    created: list[tuple[str, str]] = []
    fail_on_create = False

    def __init__(self, size, device="cpu", compute_type="int8", download_root=None):
        if FakeWhisper.fail_on_create and device == "cuda":
            raise RuntimeError("CUDA driver version is insufficient")
        FakeWhisper.created.append((device, compute_type))
        self.device = device

    def transcribe(self, audio, language=None, vad_filter=True, beam_size=5):
        dev = self.device

        def gen():
            if dev == "cuda":            # like faster-whisper: fails lazily, during decode
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            yield types.SimpleNamespace(start=0.0, end=1.0, text=" hello")
        return gen(), types.SimpleNamespace(duration=1.0, language="en")


def test_m33_gpu_fallback() -> None:
    print("M33 — GPU->CPU fallback is remembered; live transcription falls back")
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = FakeWhisper
    saved = sys.modules.get("faster_whisper")
    sys.modules["faster_whisper"] = fw
    try:
        from mico360.ui.context import AppContext
        from mico360.core import diarization as D
        ctx = AppContext()
        ctx.settings.set("whisper_device", "cuda")
        ctx.settings.set("whisper_compute", "float16")
        wav = str(config.TMP_DIR / "m33.wav")
        sf.write(wav, sine(300, 1, 16000), 16000)
        FakeWhisper.created = []
        e1 = ctx.transcription_engine()
        r1 = e1.transcribe_file(wav)
        e2 = ctx.transcription_engine()
        r2 = e2.transcribe_file(wav)
        cuda = [c for c in FakeWhisper.created if c[0] == "cuda"]
        check("file transcription falls back to CPU and succeeds",
              r1.text == "hello" and r2.text == "hello" and e1.effective_device == "cpu")
        check("the engine is not rebuilt on CUDA for the next file",
              e2 is e1 and len(cuda) == 1 and len(FakeWhisper.created) == 2, f"{FakeWhisper.created}")
        check("the requested device is kept for the settings comparison",
              e1.device == "cuda" and e1.compute_type == "float16" and e1.using_fallback)

        FakeWhisper.created = []
        from mico360.core.transcription import TranscriptionEngine
        eng = TranscriptionEngine("base", "cuda", "float16")
        txt = eng.transcribe_array(np.zeros(16000, "float32"))
        txt2 = eng.transcribe_array(np.zeros(16000, "float32"))
        check("live transcription (transcribe_array) falls back to CPU too",
              txt == "hello" and txt2 == "hello"
              and [c[0] for c in FakeWhisper.created] == ["cuda", "cpu"], f"{FakeWhisper.created}")

        FakeWhisper.created, FakeWhisper.fail_on_create = [], True
        eng = TranscriptionEngine("base", "cuda", "int8")
        eng.load()
        eng._model = None
        eng.load()
        check("a CUDA model that fails to load isn't retried on every load",
              [c[0] for c in FakeWhisper.created] == ["cpu", "cpu"], f"{FakeWhisper.created}")
        FakeWhisper.fail_on_create = False

        # H11: transcription passes Cancel through to diarization and doesn't swallow it
        seen = {}
        real = D.apply_to_segments

        def spy(path, segs, max_speakers=4, cancel=None):
            seen["cancel"] = cancel
            raise InterruptedError("Transcription cancelled.")
        D.apply_to_segments = spy
        try:
            cancel_fn = lambda: False           # noqa: E731
            eng = TranscriptionEngine("base", "cpu", "int8")
            try:
                eng.transcribe_file(wav, diarize=True, cancel=cancel_fn)
                raised = False
            except InterruptedError:
                raised = True
        finally:
            D.apply_to_segments = real
        check("H11: Cancel reaches diarization and a cancel isn't swallowed",
              seen.get("cancel") is cancel_fn and raised)
    finally:
        if saved is None:
            sys.modules.pop("faster_whisper", None)
        else:
            sys.modules["faster_whisper"] = saved


# =============================================================================
def test_meeting_watch() -> None:
    print("M36 — Teams section windows aren't meetings")
    from mico360.core import meeting_watch as MW
    c = MW.classify_window
    check("'Chat | Jane Doe | Microsoft Teams' is not a meeting",
          c("Chat | Jane Doe | Microsoft Teams") is None)
    check("other Teams sections are not meetings",
          all(c(t) is None for t in ("Calendar | Calendar | Microsoft Teams",
                                     "Activity | Microsoft Teams", "Meet | Microsoft Teams",
                                     "Search | budget | Microsoft Teams")))
    check("real Teams meetings are still detected",
          c("Weekly sync | Microsoft Teams") == ("Teams", "Weekly sync")
          and c("Meeting compact view | Weekly sync | Microsoft Teams") == ("Teams", "Weekly sync"))
    check("a chat window no longer masks a real meeting",
          MW.detect_live_meeting(["Chat | Jane Doe | Microsoft Teams", "Budget review | Microsoft Teams"])
          == ("Teams", "Budget review"))
    check("existing classification unchanged",
          c("Chat | Microsoft Teams") is None and c("Microsoft Teams") is None
          and c("Meet – abc-defg-hij") == ("Google Meet", "abc-defg-hij")
          and c("Zoom Meeting") == ("Zoom", "Zoom Meeting")
          and c("Meetings - OneNote") is None and c("MICO360 Meetings") is None)

    print("H12 — switching browser tabs doesn't stop auto-record")
    from mico360.ui.workers import MeetingWatchWorker
    meet = "Meet - abc-defg-hij - Google Chrome"
    check("browser suffix recognised (Meet in Chrome, Webex in Edge)",
          c(meet) == ("Google Meet", "abc-defg-hij")
          and MW.find_live_meeting(["Webex meeting 123 - Personal - Microsoft​ Edge"]).in_browser
          and not MW.find_live_meeting(["Weekly sync | Microsoft Teams"]).in_browser)

    def run(seq, grace=900.0, poll=8.0):
        ev = []
        state = {"t": []}
        w = MeetingWatchWorker(titles_fn=lambda: state["t"], browser_grace_seconds=grace,
                               poll_seconds=poll)
        w.meetingDetected.connect(lambda a, t: ev.append(("det", a)))
        w.meetingEnded.connect(lambda: ev.append(("end",)))
        now = 1000.0
        for titles, n in seq:
            for _ in range(n):
                state["t"] = titles
                w.poll_once(now=now)
                ends = [e for e in ev if e[0] == "end"]
                if ends and "end_at" not in state:
                    state["end_at"] = now
                now += poll
        return ev, state.get("end_at")

    other_tab = ["Inbox (3) - Gmail - Google Chrome", "Document1 - Word"]
    ev, end_at = run([([meet], 1), (other_tab, 60)])          # 8 minutes on another tab
    check("8 minutes on another browser tab: recording keeps going",
          ("det", "Google Meet") in ev and ("end",) not in ev, f"{ev}")
    ev, end_at = run([([meet], 1), (other_tab, 30), ([meet], 1), (other_tab, 100), ([meet], 1)])
    check("coming back to the Meet tab resets the grace period", ("end",) not in ev, f"{ev}")
    ev, end_at = run([([meet], 1), (other_tab, 130)])
    check("…but a meeting tab gone for longer than the grace period ends it",
          ev.count(("end",)) == 1 and end_at is not None and end_at - 1000.0 >= 900.0, f"{end_at}")
    ev, end_at = run([([meet], 1), (["Document1 - Word"], 3)])
    check("closing the browser ends the meeting promptly (2 polls)",
          ev.count(("end",)) == 1 and end_at == 1000.0 + 2 * 8.0, f"{end_at}")
    ev, end_at = run([([meet], 1), (["Google Meet - Google Chrome"], 3)])
    check("leaving the call (Meet home page) ends it promptly",
          ev.count(("end",)) == 1 and end_at == 1000.0 + 2 * 8.0, f"{end_at}")
    ev, end_at = run([(["Standup | Microsoft Teams"], 1), (["Document1 - Word"], 3)])
    check("desktop apps keep the short debounce (2 missed polls)",
          ev.count(("end",)) == 1 and end_at == 1000.0 + 2 * 8.0, f"{end_at}")

    print("M35 — Outlook calendar lookup")
    lo, hi = datetime(2026, 9, 26, 14, 5), datetime(2026, 9, 27, 9, 30)
    flt = MW.outlook_restrict_filter(lo, hi, fmt=lambda d: d.strftime("%d/%m/%Y %H:%M"))
    check("Restrict filter uses the given (locale) format",
          flt == "[Start] >= '26/09/2026 14:05' AND [Start] <= '27/09/2026 09:30'", flt)
    s = MW._locale_datetime_str(lo)
    check("locale formatting of a date works on this machine",
          "2026" in s and "26" in s and "05" in s, s)

    class Appt:
        def __init__(self, subject, start, mins=30, body=""):
            from datetime import timedelta
            self.Subject, self.Start, self.End = subject, start, start + timedelta(minutes=mins)
            self.Location, self.Body = "", body

    class Items:
        def __init__(self, appts):
            object.__setattr__(self, "log", [])
            object.__setattr__(self, "appts", appts)

        def __setattr__(self, k, v):
            self.log.append(("set", k, v))
            object.__setattr__(self, k, v)

        def Sort(self, key):                                    # noqa: N802
            self.log.append(("sort", key))

        def Restrict(self, f):                                  # noqa: N802
            self.log.append(("restrict", f))
            return list(self.appts)

    now = datetime(2026, 9, 26, 10, 0)
    items = Items([Appt("Board", datetime(2026, 9, 26, 10, 2), body="https://meet.google.com/abc-defg-hij"),
                   Appt("Mis-parsed (wrong month)", datetime(2026, 2, 9, 10, 0)),
                   Appt("Tomorrow", datetime(2026, 9, 27, 9, 0))])
    ns = types.SimpleNamespace(GetDefaultFolder=lambda n: types.SimpleNamespace(Items=items))
    outlook = types.SimpleNamespace(GetNamespace=lambda n: ns)
    dispatched = []
    running = types.SimpleNamespace(GetActiveObject=lambda name: outlook,
                                    Dispatch=lambda name: dispatched.append(name))
    com = types.SimpleNamespace(CoInitialize=lambda: None)
    real_fmt = MW._locale_datetime_str
    MW._locale_datetime_str = lambda d: d.strftime("%d/%m/%Y %H:%M")    # an en-GB user
    try:
        got = MW.upcoming_from_outlook(24, now=now, _client=running, _com=com)
    finally:
        MW._locale_datetime_str = real_fmt
    restrict = [x for x in items.log if x[0] == "restrict"]
    check("Outlook filter is written in the user's date format",
          restrict and "26/09/2026 09:55" in restrict[0][1], f"{restrict}")
    check("appointments outside the window are dropped even if the filter mis-parses",
          [m.title for m in got] == ["Board", "Tomorrow"] and got[0].join_url.endswith("abc-defg-hij"),
          f"{[m.title for m in got]}")
    check("Sort is applied before IncludeRecurrences",
          [x[0] for x in items.log][:2] == ["sort", "set"], f"{items.log[:3]}")

    def not_running(name):
        raise OSError("Operation unavailable")
    closed = types.SimpleNamespace(GetActiveObject=not_running,
                                   Dispatch=lambda name: dispatched.append(name))
    got = MW.upcoming_from_outlook(24, now=now, _client=closed, _com=com)
    check("Outlook is never launched by the poll when it isn't running",
          got == [] and dispatched == [], f"{dispatched}")


# =============================================================================
def main() -> int:
    for fn in (test_meeting_watch, test_h11_diarization, test_m33_gpu_fallback,
               test_m31_streaming_mix, test_m32_async_stop, test_h13_device_loss,
               test_m34_mic_fallback, test_h10_video_clock, test_h10_audio_alignment,
               test_h9_video_system_audio):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== CAPTURE: {passed}/{len(results)} passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        shutil.rmtree(_TMP, ignore_errors=True)
        from mico360.hard_exit import hard_exit
        hard_exit(rc)
