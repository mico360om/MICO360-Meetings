"""Regression tests for the second round of recording / meeting-detection fixes.

    python tests/fixes_capture2.py

  K1   named Google Meet calls in a browser are detected (Chrome/Edge/Firefox/Brave)
  K2   a reopened mic pads the gap; each source starts at its own place on the clock
  K3   the lost mic is found again by name after PortAudio renumbers its devices
  K4   a stop with nothing captured isn't "Recording saved"; empty files are removed
  K5   live-transcript chunks of a retired worker don't land in the next recording
  K6   closing mid-stop still stops + waits for the live worker; panel threads exposed
  K7   the live buffer is capped and switched off when the live worker ends
  K8   screen/camera with "Mic + System" and no microphone still records system audio
  K9   a CPU fallback on another thread can't break a file transcription in flight
  K10  the recording clock is monotonic (wall-clock jumps don't move it)

Runs against a THROWAWAY data folder (LOCALAPPDATA is redirected before the app
is imported) and needs no microphone, camera, network, Ollama or Whisper model:
audio devices, the loopback device, video frames, Whisper and window titles are
faked.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mico360_capture2_"))
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
from PySide6.QtCore import QThread, Signal              # noqa: E402
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


def pump(until, timeout: float = 10.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(until())


def settle(secs: float) -> None:
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def sine(freq: float, secs: float, rate: int, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(secs * rate)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype("float32")


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
    return len(x) > 0 and any(abs(f - f0) < 15 for f in dominant_freqs(x, sr, k))


def rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


class Toast:
    def __init__(self):
        self.msgs: list[tuple[str, str]] = []

    def show_message(self, msg, kind="info", ms=0):
        self.msgs.append((kind, msg))


# -----------------------------------------------------------------------------
# Fake audio devices — real-time paced, can stall (go quiet) and come back, can
# renumber on a PortAudio refresh, open slowly, or never deliver audio at all.
# -----------------------------------------------------------------------------
class FakeStream2:
    def __init__(self, sd, device, rate, blocksize, callback, stall, freq):
        self.sd, self.device, self.rate = sd, device, int(rate)
        self.blocksize, self.callback, self.stall, self.freq = blocksize, callback, stall, freq
        self._closed = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        if self.sd.silent:                         # opens fine, never delivers audio
            self._closed.wait()
            return
        n, t0 = 0, time.monotonic()
        while not self._closed.is_set():
            if self.stall is not None and time.monotonic() - t0 > self.stall:
                self._closed.wait()                # the device went quiet (unplugged)
                return
            t = (n + np.arange(self.blocksize)) / self.rate
            blk = (0.3 * np.sin(2 * np.pi * self.freq * t)).astype("float32").reshape(-1, 1)
            try:
                self.callback(blk, self.blocksize, None, None)
            except Exception:
                pass
            n += self.blocksize
            self._closed.wait(max(0.0, t0 + n / self.rate - time.monotonic()))

    def stop(self):
        self._closed.set()

    def close(self):
        self._closed.set()


def _dev(name, rate=16000):
    return {"name": name, "max_input_channels": 1, "max_output_channels": 0,
            "default_samplerate": rate, "hostapi": 0}


class FakeSD2(types.ModuleType):
    """Stand-in for `sounddevice`."""

    def __init__(self, names=("Mic 0",), stall_after=None, reopen_failures=0, open_delay=0.0,
                 silent=False, on_refresh=None, freq=440.0):
        super().__init__("sounddevice")
        self.devices = [_dev(n) for n in names]
        self.stall_after = dict(stall_after or {})        # device NAME -> seconds
        self.reopen_failures = reopen_failures            # failed opens after the first one
        self.open_delay, self.silent, self.on_refresh, self.freq = open_delay, silent, on_refresh, freq
        self.opened: list[tuple[int, str, int]] = []
        self.refreshes = 0
        self.default = types.SimpleNamespace(device=[0 if self.devices else -1, None])

    def query_hostapis(self):
        return [{"name": "Windows WASAPI", "default_input_device": 0 if self.devices else -1}]

    def query_devices(self, idx=None):
        return self.devices if idx is None else self.devices[idx]

    def _terminate(self):
        self.refreshes += 1
        if self.on_refresh is not None and self.refreshes == 1:
            self.on_refresh(self)                         # e.g. devices renumbered

    def _initialize(self):
        pass

    def InputStream(self, samplerate, channels, device, dtype, blocksize, callback):  # noqa: N802
        if self.open_delay:
            time.sleep(self.open_delay)
        if self.opened and self.reopen_failures > 0:
            self.reopen_failures -= 1
            raise RuntimeError("Error opening InputStream: Device unavailable [PaErrorCode -9985]")
        name = self.devices[device]["name"]
        self.opened.append((device, name, int(samplerate)))
        return FakeStream2(self, device, samplerate, blocksize, callback,
                           self.stall_after.pop(name, None), self.freq)


class FakeLoopRecorder2:
    """Real-time paced loopback recorder (soundcard)."""

    def __init__(self, rate, freq):
        self.rate, self.freq, self.n, self.t0 = rate, freq, 0, None

    def __enter__(self):
        self.t0 = time.monotonic()
        return self

    def __exit__(self, *a):
        return False

    def record(self, numframes):
        self.n += numframes
        time.sleep(max(0.0, self.t0 + self.n / self.rate - time.monotonic()))
        t = (self.n - numframes + np.arange(numframes)) / self.rate
        return (0.3 * np.sin(2 * np.pi * self.freq * t)).astype("float32").reshape(-1, 1)


def fake_soundcard(ok=True, freq=1000.0):
    sc = types.ModuleType("soundcard")
    sc.default_speaker = lambda: types.SimpleNamespace(name="Speakers")

    def get_microphone(name, include_loopback=False):
        if not ok:
            raise RuntimeError("no loopback device")
        return types.SimpleNamespace(
            recorder=lambda samplerate, channels, blocksize: FakeLoopRecorder2(samplerate, freq))
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


class FastWatchdogs:
    """Short stall / reopen timeouts for the duration of a test."""

    def __init__(self, stall=0.5, reopen=5.0):
        self.vals = (stall, reopen)

    def __enter__(self):
        self.old = (R._STALL_SECONDS, R._REOPEN_SECONDS)
        R._STALL_SECONDS, R._REOPEN_SECONDS = self.vals
        return self

    def __exit__(self, *a):
        R._STALL_SECONDS, R._REOPEN_SECONDS = self.old
        return False


def record_for(rec, secs: float):
    rec.start()
    t0 = time.time()
    while time.time() - t0 < secs and rec.state in (R.RECORDING, R.PAUSED):
        time.sleep(0.05)
    return rec.stop()


def stream_durations(path: str) -> dict:
    import av
    d = {}
    with av.open(path) as c:
        for st in c.streams:
            if st.duration is not None:
                d[st.type] = float(st.duration * st.time_base)
    return d


def _frame_source(delay: float, size=(64, 48)):
    w, h = size

    def grab():
        time.sleep(delay)
        return np.zeros((h, w, 3), np.uint8)
    return grab, size


# =============================================================================
def test_k1_named_meet() -> None:
    print("K1 — named Google Meet calls in a browser are detected")
    from mico360.core import meeting_watch as MW
    from mico360.ui.workers import MeetingWatchWorker
    c = MW.classify_window
    zw = "​"
    cases = {
        "Chrome": ("Meet – Weekly Sync - Google Chrome", "Weekly Sync"),
        "Chrome (hyphen)": ("Meet - Team standup - Google Chrome", "Team standup"),
        "Edge": (f"Meet – Weekly Sync - Microsoft{zw} Edge", "Weekly Sync"),
        "Edge + profile": (f"Meet – Weekly Sync - Profile 1 - Microsoft{zw} Edge", "Weekly Sync"),
        "Edge + Personal": ("Meet - Team standup - Personal - Microsoft Edge", "Team standup"),
        "Edge + more pages": ("Meet – Weekly Sync and 3 more pages - Khurram - Microsoft Edge",
                              "Weekly Sync"),
        "Firefox": ("Meet – Weekly Sync — Mozilla Firefox", "Weekly Sync"),
        "Brave": ("Meet - Team standup - Brave", "Team standup"),
    }
    for label, (title, name) in cases.items():
        got = c(title)
        check(f"named Meet call in {label} is detected", got == ("Google Meet", name), f"{title!r} -> {got}")
        check(f"…and is not mistaken for Meet's idle page ({label})", not MW.meet_left([title]))
    check("Meet code titles still work in every browser",
          c("Meet - abc-defg-hij - Google Chrome") == ("Google Meet", "abc-defg-hij")
          and c(f"Meet - abc-defg-hij - Profile 1 - Microsoft{zw} Edge") == ("Google Meet", "abc-defg-hij")
          and c("Meet – abc-defg-hij") == ("Google Meet", "abc-defg-hij")
          and c("Meet - abc-defg-hij — Mozilla Firefox") == ("Google Meet", "abc-defg-hij"))
    check("Meet's home page (also in an Edge profile) is still 'left the call'",
          MW.meet_left(["Google Meet - Google Chrome"])
          and MW.meet_left(["Google Meet - Personal - Microsoft Edge"])
          and MW.meet_left(["Meet - Profile 2 - Microsoft Edge"])
          and c("Google Meet - Google Chrome") is None)
    check("Teams / Zoom / Webex classification unchanged",
          c("Weekly sync | Microsoft Teams") == ("Teams", "Weekly sync")
          and c("Chat | Jane Doe | Microsoft Teams") is None
          and c("Zoom Meeting") == ("Zoom", "Zoom Meeting")
          and c(f"Webex meeting 123 - Personal - Microsoft{zw} Edge") == ("Webex", "Webex meeting 123")
          and c("Meetings - OneNote") is None)
    check("browser recognised for named calls",
          MW.split_browser("Meet – Weekly Sync - Google Chrome") == ("Meet – Weekly Sync", "Google Chrome")
          and MW.find_live_meeting(["Meet - Team standup - Brave"]).browser == "Brave")

    # the watcher: a named call in a background tab keeps recording; leaving ends it
    meet = "Meet – Weekly Sync - Google Chrome"

    def run(seq, poll=8.0):
        ev, state = [], {"t": []}
        w = MeetingWatchWorker(titles_fn=lambda: state["t"], browser_grace_seconds=900.0,
                               poll_seconds=poll)
        w.meetingDetected.connect(lambda a, t: ev.append(("det", a, t)))
        w.meetingEnded.connect(lambda: ev.append(("end",)))
        now = 1000.0
        for titles, n in seq:
            for _ in range(n):
                state["t"] = titles
                w.poll_once(now=now)
                if ("end",) in ev and "end_at" not in state:
                    state["end_at"] = now
                now += poll
        return ev, state.get("end_at")

    other = ["Inbox (3) - Gmail - Google Chrome", "Document1 - Word"]
    ev, _ = run([([meet], 1), (other, 60)])
    check("named Meet call: 8 minutes on another tab keeps recording",
          ("det", "Google Meet", "Weekly Sync") in ev and ("end",) not in ev, f"{ev}")
    ev, end_at = run([([meet], 1), ([meet], 3)])
    check("named Meet call in the foreground is never ended", ("end",) not in ev, f"{ev}")
    ev, end_at = run([([meet], 1), (["Google Meet - Google Chrome"], 3)])
    check("leaving the named call (Meet home page) ends it promptly",
          ev.count(("end",)) == 1 and end_at == 1000.0 + 2 * 8.0, f"{ev} {end_at}")


# =============================================================================
def test_k2_gap_padding() -> None:
    print("K2 — a reopened microphone pads the gap (audio only, mic path)")
    with FastWatchdogs():
        sd = FakeSD2(names=("Mic 0",), stall_after={"Mic 0": 1.0}, reopen_failures=1)
        with Devices(sd):
            rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="mic"))
            res = record_for(rec, 4.2)
    ok = Path(res.path).exists()
    data, sr = sf.read(res.path, dtype="float32") if ok else (np.zeros(1, "float32"), 16000)
    dur = len(data) / sr
    clock = rec._final_elapsed
    check("mic lost and reopened", rec.reopened == 1 and len(sd.opened) == 2, f"{sd.opened}")
    check("audio-only file length matches the recording time (gap padded, not dropped)",
          ok and abs(dur - clock) < 0.3, f"file={dur:.2f}s clock={clock:.2f}s")
    check("the gap is silence, the audio after the reopen continues",
          rms(data[int(1.3 * sr): int(2.2 * sr)]) < 1e-3 and rms(data[int(0.2 * sr): int(0.8 * sr)]) > 0.1
          and rms(data[int(3.3 * sr): int(3.9 * sr)]) > 0.1,
          f"gap={rms(data[int(1.3 * sr): int(2.2 * sr)]):.4f} after={rms(data[int(3.3 * sr): int(3.9 * sr)]):.4f}")
    check("reported duration counts the padded gap",
          abs(res.duration - dur) < 0.15, f"reported={res.duration:.2f} file={dur:.2f}")
    check("the user is told about the reopen", any("reopened" in w for w in rec.all_warnings()))

    print("K2 — Mic + System: the reopened mic stays aligned with the system audio")
    with FastWatchdogs():
        sd = FakeSD2(names=("Mic 0",), stall_after={"Mic 0": 1.0}, reopen_failures=1, freq=440.0)
        with Devices(sd, fake_soundcard(True, 1000.0)):
            rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
            res = record_for(rec, 4.2)
    mic_s = rec._written.get("mic", (0, 1))[0] / float(rec._written.get("mic", (0, 1))[1])
    sys_s = rec._written.get("sys", (0, 1))[0] / float(rec._written.get("sys", (0, 1))[1])
    check("mic track covers the same time span as the system track after a reopen",
          rec.reopened == 1 and abs(mic_s - sys_s) < 0.35, f"mic={mic_s:.2f}s sys={sys_s:.2f}s")
    if Path(res.path).exists():
        mix, msr = sf.read(res.path, dtype="float32")
        tail = mix[-int(0.8 * msr):]
        check("mixed file: mic (440 Hz) present after the reopen, at the end",
              has_freq(tail, msr, 440) and has_freq(tail, msr, 1000), f"{dominant_freqs(tail, msr)}")
    else:
        check("mixed file: mic (440 Hz) present after the reopen, at the end", False, "no file")

    print("K2 — each source starts at its own first sample (a late mic isn't mixed early)")
    rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
    mic = str(config.TMP_DIR / "_src_mic_k2.wav")
    sysw = str(config.TMP_DIR / "_src_sys_k2.wav")
    sf.write(mic, sine(440, 2.0, 16000), 16000)
    loop = sine(1000, 4.0, 48000)
    sf.write(sysw, np.stack([loop, loop], axis=1), 48000)
    rec._first_elapsed = {"mic": 2.0, "sys": 0.0}
    rec._first_audio_elapsed = 0.0
    rec._written = {"mic": (32000, 16000), "sys": (4 * 48000, 48000)}
    rec._finalize_sources([mic, sysw])
    out, osr = sf.read(rec.output_path, dtype="float32")
    head, late = out[int(0.2 * osr): int(1.8 * osr)], out[int(2.2 * osr): int(3.8 * osr)]
    check("a mic that started 2 s late is delayed by 2 s in the mix",
          not has_freq(head, osr, 440) and has_freq(head, osr, 1000, k=1)
          and has_freq(late, osr, 440) and has_freq(late, osr, 1000),
          f"head={dominant_freqs(head, osr)} late={dominant_freqs(late, osr)}")
    check("mix length = the later source's end on the clock (4 s)",
          abs(len(out) / osr - 4.0) < 0.05, f"{len(out) / osr:.2f}s")
    check("recorded_seconds() counts the late source's offset",
          abs(rec.recorded_seconds() - 4.0) < 0.05, f"{rec.recorded_seconds():.2f}")

    rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
    a = str(config.TMP_DIR / "_src_mic_k2b.wav")
    b = str(config.TMP_DIR / "_src_sys_k2b.wav")
    sf.write(a, sine(440, 1.0, 16000), 16000)
    sf.write(b, sine(1000, 1.0, 16000), 16000)
    rec._first_elapsed = {"mic": 1.5, "sys": 0.7}
    rec._finalize_sources([a, b])
    check("audio origin (for the A/V mux) = the earliest surviving source",
          rec._first_audio_elapsed == 0.7, f"{rec._first_audio_elapsed}")

    # end to end: the mic opens 1.5 s after the loopback started
    sd = FakeSD2(names=("Mic 0",), open_delay=1.5, freq=440.0)
    with Devices(sd, fake_soundcard(True, 1000.0)):
        rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
        res = record_for(rec, 3.5)
    fe = dict(rec._first_elapsed)
    ok = Path(res.path).exists()
    mix, msr = sf.read(res.path, dtype="float32") if ok else (np.zeros(1, "float32"), 16000)
    first = mix[int(0.1 * msr): int(1.1 * msr)]
    later = mix[int(2.0 * msr): int(3.0 * msr)]
    check("each source's first-sample time is recorded (mic ~1.5 s after system)",
          "mic" in fe and "sys" in fe and 1.1 < fe["mic"] - fe["sys"] < 2.0, f"{fe}")
    check("slow-opening mic is not shifted early in the mix",
          ok and not has_freq(first, msr, 440) and has_freq(later, msr, 440),
          f"first={dominant_freqs(first, msr)} later={dominant_freqs(later, msr)}")

    print("K2 — the reopen gap is measured to when the new stream opened")
    rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="mic"))
    rec._first_elapsed = {"mic": 0.0}
    rec._written = {"mic": (16000, 16000)}          # 1 s written before the device died
    rec._reopen_at = {"mic": 3.0}                   # new stream opened at 3.0 s …
    rec.elapsed = lambda: 6.0                       # … but its first block arrived at 6.0 s
    gap = rec._gap_frames("mic", 1600, 16000)
    check("a late first block after a reopen doesn't inflate the gap (no duplicated time)",
          gap == 2 * 16000, f"gap={gap} frames (want {2 * 16000})")
    check("the reopen time is used once", "mic" not in rec._reopen_at)

    print("K2 — screen recording: a reopened mic keeps the soundtrack in sync with the video")
    with FastWatchdogs():
        sd = FakeSD2(names=("Mic 0",), stall_after={"Mic 0": 1.0}, reopen_failures=1)
        with Devices(sd):
            vr = R.VideoRecorder(R.RecordingConfig(kind="screen", source="mic", fps=10))
            vr._make_source = lambda: _frame_source(0.1)
            vr.start()
            time.sleep(4.5)
            res = vr.stop()
    d = stream_durations(res.path) if Path(res.path).exists() and res.path.endswith(".mp4") else {}
    check("audio and video tracks keep the same length after a mic reopen",
          vr._audio is not None and vr._audio.reopened == 1
          and abs(d.get("video", 0) - d.get("audio", -9)) < 0.4, f"{d}")


# =============================================================================
def test_k3_renumbered_devices() -> None:
    print("K3 — the lost mic is found again by name after devices are renumbered")

    def insert_new(sd):                    # a new device appears in front of the headset
        sd.devices.insert(1, _dev("Mic B"))

    with FastWatchdogs():
        sd = FakeSD2(names=("Mic A", "USB Headset"), stall_after={"USB Headset": 1.0},
                     on_refresh=insert_new)
        with Devices(sd):
            rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="mic", mic_index=1))
            record_for(rec, 2.5)
    names = [n for _, n, _ in sd.opened]
    check("reopened the SAME device at its new index (not the stale index)",
          names[:2] == ["USB Headset", "USB Headset"] and sd.opened[1][0] == 2, f"{sd.opened}")

    def remove_headset(sd):                # the headset is gone; index 1 is now another mic
        sd.devices[:] = [_dev("Mic A"), _dev("Mic B")]

    with FastWatchdogs():
        sd = FakeSD2(names=("Mic A", "USB Headset"), stall_after={"USB Headset": 1.0},
                     on_refresh=remove_headset)
        with Devices(sd):
            rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both", mic_index=1))
            with Devices(None, fake_soundcard(True)):
                record_for(rec, 2.5)
    names = [n for _, n, _ in sd.opened]
    check("device gone: falls back to the default input, not whatever took the stale index",
          len(names) >= 2 and names[1] == "Mic A", f"{sd.opened}")

    sd = FakeSD2(names=("Mic A", "USB Headset"))
    sd.devices.append({**_dev("USB Headset"), "hostapi": 1})
    sd.query_hostapis = lambda: [{"name": "Windows WASAPI", "default_input_device": 0},
                                 {"name": "MME", "default_input_device": 0}]
    ident = R.AudioRecorder._device_identity(sd, 2)
    check("device identity = name + host API; lookup respects the host API",
          ident == ("USB Headset", "MME") and R.AudioRecorder._find_device(sd, ident) == 2
          and R.AudioRecorder._find_device(sd, ("USB Headset", "Windows WASAPI")) == 1
          and R.AudioRecorder._find_device(sd, ("Nope", None)) is None, f"{ident}")


# =============================================================================
class SlowRec:
    """A recorder whose stop() takes a while."""

    def __init__(self, path, delay=0.3):
        self.path, self.delay = path, delay
        self.state = R.RECORDING
        self.cfg = R.RecordingConfig()
        self.error = ""
        self.output_path = path

    def elapsed(self):
        return 1.0

    def level(self):
        return 0.0

    def file_size(self):
        return 0

    def stop(self):
        time.sleep(self.delay)
        self.state = R.STOPPED
        return R.RecordingResult(path=self.path, kind="audio", duration=1.0,
                                 size_bytes=Path(self.path).stat().st_size, fmt="wav",
                                 started_at=time.time())

    def cancel(self):
        self.state = R.CANCELLED


def _header_only_wav(path: str) -> None:
    with sf.SoundFile(path, mode="w", samplerate=16000, channels=1, subtype="PCM_16"):
        pass


def test_k4_nothing_captured() -> None:
    print("K4 — a stop with nothing captured is not 'Recording saved'")
    from mico360.ui.recording_panel import RecordingPanel

    # 1) mic-only recording stopped before any audio arrived (device silent)
    sd = FakeSD2(names=("Mic 0",), silent=True)
    toast = Toast()
    panel = RecordingPanel(toast=toast, ctx=None)
    got = []
    panel.recordingReady.connect(got.append)
    with Devices(sd):
        rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="mic"))
        rec.start()
        pump(lambda: bool(sd.opened), 5)
        time.sleep(0.4)
        panel._rec = rec
        panel._auto_mode = True
        panel._stop()
        pump(lambda: not panel._stopping, 15)
    out = Path(rec.output_path)
    msgs = [m for _, m in toast.msgs]
    check("no 'Recording saved' and no summary for an empty recording",
          not any("Recording saved" in m for m in msgs) and panel.stack.currentIndex() != 2, f"{msgs}")
    check("the user is told nothing was recorded",
          any("Nothing was recorded" in m for m in msgs), f"{msgs}")
    check("an empty auto-recording is not queued for transcription", got == [], f"{got}")
    check("the header-only WAV is deleted from the recordings folder", not out.exists(), str(out))

    # 2) Mic + System, nothing arrives: the _src_*.wav temp is removed too
    sd = FakeSD2(names=("Mic 0",), silent=True)
    toast = Toast()
    panel = RecordingPanel(toast=toast, ctx=None)
    with Devices(sd, fake_soundcard(False)):
        rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
        rec.start()
        pump(lambda: bool(sd.opened) and bool(rec._temps), 5)
        time.sleep(0.4)
        temps = list(rec._temps)
        panel._rec = rec
        panel._stop()
        pump(lambda: not panel._stopping, 15)
    left = [t for t in temps if Path(t).exists()]
    check("empty Mic + System stop: no leftover _src_*.wav temp files and no output",
          temps and not left and not Path(rec.output_path).exists()
          and any("Nothing was recorded" in m for _, m in toast.msgs), f"left={left} {toast.msgs}")

    # 3) the error path removes empty files as well
    toast = Toast()
    panel = RecordingPanel(toast=toast, ctx=None)
    rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="mic"))
    _header_only_wav(rec.output_path)
    tmp = str(config.TMP_DIR / "_src_mic_k4err.wav")
    _header_only_wav(tmp)
    rec._temps = [tmp]
    rec.state, rec.error = R.ERROR, "Microphone opened but no audio is being received."
    panel._rec = rec
    panel._tick()
    pump(lambda: not panel._stopping, 10)
    check("error with nothing captured: empty output + temp deleted, error shown",
          not Path(rec.output_path).exists() and not Path(tmp).exists()
          and any("Recording error" in m for _, m in toast.msgs), f"{toast.msgs}")

    # 4) real audio is never deleted (a failed mix keeps its temps)
    toast = Toast()
    panel = RecordingPanel(toast=toast, ctx=None)
    rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
    keep = str(config.TMP_DIR / "_src_mic_k4keep.wav")
    sf.write(keep, sine(440, 1.0, 16000), 16000)
    rec._temps, rec._written = [keep], {"mic": (16000, 16000)}
    rec.state, rec.error = R.STOPPED, "The recording could not be saved (MemoryError)."
    panel._rec = rec
    panel._finish_stop(R.RecordingResult(rec.output_path, "audio", 1.0, 0, "wav", time.time()),
                       live_wait_ms=0, stopped=True)
    check("temp files that hold audio are kept (and the save error is reported)",
          Path(keep).exists() and any("could not be saved" in m for _, m in toast.msgs), f"{toast.msgs}")
    Path(keep).unlink(missing_ok=True)

    # 5) a normal recording is still saved + announced
    toast = Toast()
    panel = RecordingPanel(toast=toast, ctx=None)
    path = str(config.RECORDINGS_DIR / "k4_ok.wav")
    sf.write(path, sine(440, 1.0, 16000), 16000)
    panel._rec = SlowRec(path, 0.05)
    panel._stop()
    pump(lambda: not panel._stopping, 10)
    check("a recording with audio is still 'Recording saved'",
          panel.stack.currentIndex() == 2 and any("Recording saved" in m for _, m in toast.msgs)
          and Path(path).exists(), f"{toast.msgs}")


# =============================================================================
class FakeLive(QThread):
    """Stand-in for LiveTranscribeWorker: emits one chunk when released."""
    partial = Signal(str)
    instances: list = []

    def __init__(self, recorder=None, engine=None, language="auto", text="", tail=0.0):
        super().__init__()
        self.recorder, self.text, self.tail = recorder, text, tail
        self.release = threading.Event()
        self._stopped = threading.Event()
        FakeLive.instances.append(self)

    def run(self):
        if self.text:
            self.release.wait(10)
            if self.release.is_set():
                self.partial.emit(self.text)
        self._stopped.wait(15)
        if self.tail:
            time.sleep(self.tail)                 # finishing a last chunk after stop

    def stop(self):
        self._stopped.set()

    cancel = stop


class FakeRec:
    def __init__(self, cfg, path):
        self.cfg, self.output_path, self.kind = cfg, path, cfg.kind
        self.state, self.error = R.RECORDING, ""
        self.started_at, self.resolution = time.time(), ""
        self.live = False

    def start(self):
        self.state = R.RECORDING

    def enable_live(self):
        self.live = True

    def elapsed(self):
        return 1.0

    def level(self):
        return 0.0

    def file_size(self):
        return 0

    def all_warnings(self):
        return []

    def stop(self):
        self.state = R.STOPPED
        return R.RecordingResult(self.output_path, "audio", 1.0, Path(self.output_path).stat().st_size,
                                 "wav", self.started_at)

    def cancel(self):
        self.state = R.CANCELLED


class FakeSettings:
    def __init__(self, **kw):
        self.d = dict(kw)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


def test_k5_late_partials() -> None:
    print("K5 — live chunks of a retired worker don't land in the next recording")
    from mico360.ui import workers as W
    from mico360.ui.recording_panel import RecordingPanel
    path = str(config.RECORDINGS_DIR / "k5.wav")
    sf.write(path, sine(440, 1.0, 16000), 16000)
    texts = iter(["LATE words from the first meeting", "NEW words"])
    real_worker, real_make = W.LiveTranscribeWorker, R.make_recorder
    W.LiveTranscribeWorker = lambda rec, eng, lang: FakeLive(rec, eng, lang, text=next(texts))
    R.make_recorder = lambda cfg: FakeRec(cfg, path)
    FakeLive.instances = []
    try:
        ctx = types.SimpleNamespace(settings=FakeSettings(live_transcription=True, language="auto"),
                                    transcription_engine=lambda: None)
        panel = RecordingPanel(toast=Toast(), ctx=ctx)
        panel.live_check.setChecked(True)
        panel._start()                                   # recording 1 + live worker 1
        w1 = FakeLive.instances[-1]
        panel._cancel()                                  # retired while mid-chunk
        pump(lambda: not panel._stopping, 10)
        panel._start()                                   # recording 2 + live worker 2
        w2 = FakeLive.instances[-1]
        check("two recordings, two live workers; the first is still running",
              w1 is not w2 and w1.isRunning() and w2.isRunning())
        w1.release.set()                                 # the old worker finishes its chunk
        pump(lambda: not w1.isRunning(), 10)
        settle(0.3)
        w2.release.set()
        pump(lambda: "NEW" in panel._live_text, 10)
        check("the retired worker's late chunk is ignored",
              "LATE" not in panel._live_text, repr(panel._live_text))
        check("the current worker's chunks still arrive", "NEW words" in panel._live_text,
              repr(panel._live_text))
        panel.stop_if_active()
        check("all live workers ended", not any(w.isRunning() for w in FakeLive.instances))
    finally:
        W.LiveTranscribeWorker, R.make_recorder = real_worker, real_make


# =============================================================================
def test_k6_close_mid_stop() -> None:
    print("K6 — closing mid-stop still stops and waits for the live worker")
    from mico360.ui.recording_panel import RecordingPanel
    path = str(config.RECORDINGS_DIR / "k6.wav")
    sf.write(path, sine(440, 1.0, 16000), 16000)
    panel = RecordingPanel(toast=Toast(), ctx=None)
    live = FakeLive(tail=1.0)                       # needs 1 s to finish its last chunk
    live.start()
    pump(lambda: live.isRunning(), 5)
    panel._rec = SlowRec(path, 0.3)
    panel._live_worker = live
    panel._auto_mode = True                         # auto-record: the stop doesn't wait for it
    panel._stop()
    threads = panel.background_threads()
    check("background_threads() exposes the stop worker and the live worker",
          live in threads and any(w in threads for w in panel._stop_workers), f"{threads}")
    panel.stop_if_active()                          # blocks: the stop's done signal isn't delivered
    check("stop_if_active() stopped and waited for the live worker held by the stop",
          not live.isRunning())
    pump(lambda: not panel._stopping, 10)
    check("nothing of the panel is still running afterwards", panel.background_threads() == [])

    retired = FakeLive(tail=0.5)
    retired.start()
    pump(lambda: retired.isRunning(), 5)
    panel._retire_worker(retired, 0)
    check("retired workers are exposed too", retired in panel.background_threads())
    retired.wait(5000)

    import inspect
    from mico360.ui import main_window as MWin
    from mico360.ui.workers import LiveTranscribeWorker
    src = inspect.getsource(MWin.MainWindow._background_workers)
    check("MainWindow._background_workers() includes the recording panel's threads",
          "recorder_panel.background_threads()" in src)
    check("the close handler can cancel a live worker (cancel() == stop())",
          LiveTranscribeWorker.cancel is LiveTranscribeWorker.stop)


# =============================================================================
def test_k7_live_buffer() -> None:
    print("K7 — the live buffer is bounded and switched off when the worker ends")
    rec = R.AudioRecorder(R.RecordingConfig(kind="audio"))
    rec.enable_live()
    rate = 16000
    for i in range(1800):                           # 3 minutes of 0.1 s blocks, nobody pulling
        rec._live_push(np.full(1600, i, dtype="float32"))
    buffered = sum(len(b) for b in rec._live_buf)
    cap = int(R._LIVE_MAX_SECONDS * rate)
    check("buffer never exceeds ~2 minutes of audio",
          buffered <= cap and rec._live_samples == buffered, f"{buffered / rate:.1f}s")
    arr, _ = rec.pull_live()
    check("the NEWEST audio is kept (oldest dropped)",
          arr is not None and arr[-1] == 1799 and arr[0] > 0, f"{arr[0] if arr is not None else None}")

    from mico360.ui.workers import LiveTranscribeWorker

    class BadEngine:
        def load(self):
            raise RuntimeError("model download failed")

    rec = R.AudioRecorder(R.RecordingConfig(kind="audio"))
    rec.enable_live()
    rec._live_push(np.zeros(1600, "float32"))
    LiveTranscribeWorker(rec, BadEngine()).run()    # dies at once
    rec._live_push(np.zeros(1600, "float32"))
    check("a live worker that dies switches the recorder's live tap off",
          rec._live_on is False and rec._live_buf == [], f"on={rec._live_on} n={len(rec._live_buf)}")

    class OkEngine:
        def load(self):
            return None

        def transcribe_array(self, a, lang):
            return ""

    rec = R.AudioRecorder(R.RecordingConfig(kind="audio"))
    rec.enable_live()
    w = LiveTranscribeWorker(rec, OkEngine(), interval=0.1)
    w.start()
    time.sleep(0.3)
    w.stop()
    w.wait(5000)
    check("…also when it ends normally", rec._live_on is False)


# =============================================================================
def test_k8_no_mic_system_audio() -> None:
    print("K8 — screen/camera + 'Mic + System' with no microphone records system audio")
    from mico360.ui.recording_panel import RecordingPanel
    path = str(config.RECORDINGS_DIR / "k8.wav")
    sf.write(path, sine(440, 1.0, 16000), 16000)
    real = (R.list_microphones, R.system_audio_supported, R.make_recorder)
    made = []

    def make(cfg):
        made.append(cfg)
        return FakeRec(cfg, path)

    try:
        R.system_audio_supported = lambda: True
        R.make_recorder = make

        def start_with(mics, kind, source, pick=None):
            R.list_microphones = lambda: mics
            panel = RecordingPanel(toast=Toast(), ctx=None)
            panel._type_btns[kind].setChecked(True)
            panel.source_box.setCurrentIndex(panel.source_box.findData(source))
            if pick is not None:
                panel.mic_box.setCurrentIndex(panel.mic_box.findData(pick))
            panel._start()
            panel._teardown()
            panel._rec = None
            return made[-1]

        for kind in ("screen", "camera"):
            cfg = start_with([], kind, "both")
            check(f"{kind} + 'Mic + System', no mic: audio included",
                  cfg.include_audio and cfg.source == "both" and cfg.mic_index is None, f"{cfg}")
        cfg = start_with([], "screen", "system")
        check("screen + 'System audio', no mic: audio included", cfg.include_audio)
        cfg = start_with([], "screen", "mic")
        check("screen + 'Microphone', no mic: silent video (nothing to record)", not cfg.include_audio)
        mics = [R.MicInfo(3, "Headset", 1)]
        cfg = start_with(mics, "screen", "both", pick=-1)
        check("an explicit 'No audio (silent)' still means a silent video", not cfg.include_audio)
        cfg = start_with(mics, "camera", "both", pick=3)
        check("with a mic selected: audio included, that mic used",
              cfg.include_audio and cfg.mic_index == 3)
    finally:
        R.list_microphones, R.system_audio_supported, R.make_recorder = real

    # the recorder then records the system part (the mic failure is reported)
    with Devices(FakeSD2(names=()), fake_soundcard(True, 1000.0)):
        vr = R.VideoRecorder(R.RecordingConfig(kind="screen", source="both", fps=10,
                                               include_audio=True, mic_index=None))
        vr._make_source = lambda: _frame_source(0.1)
        vr.start()
        time.sleep(2.0)
        res = vr.stop()
    d = stream_durations(res.path) if Path(res.path).exists() else {}
    check("…and the video's soundtrack holds the system audio",
          res.has_audio and "audio" in d, f"has_audio={res.has_audio} {d}")


# =============================================================================
class FakeWhisper:
    created: list = []

    def __init__(self, size, device="cpu", compute_type="int8", download_root=None):
        FakeWhisper.created.append(device)
        self.device = device

    def transcribe(self, audio, language=None, vad_filter=True, beam_size=5):
        dev = self.device

        def gen():
            if dev == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            yield types.SimpleNamespace(start=0.0, end=1.0, text=" hello")
        return gen(), types.SimpleNamespace(duration=1.0, language="en")


def test_k9_model_race() -> None:
    print("K9 — a CPU fallback on another thread can't break a transcription in flight")
    from mico360.core import transcription as T
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = FakeWhisper
    saved = sys.modules.get("faster_whisper")
    sys.modules["faster_whisper"] = fw
    real_prep, real_lang = T.audio.prepare_for_whisper, T._whisper_lang
    wav = str(config.TMP_DIR / "k9.wav")
    sf.write(wav, sine(300, 1, 16000), 16000)
    try:
        eng = T.TranscriptionEngine("base", "cpu", "int8")
        check("load() returns the model", eng.load() is eng._model and eng._model is not None)

        def prep_and_reset(path):
            with eng._lock:                          # what _fall_back_to_cpu does on the live thread
                eng._model, eng._loaded_key = None, None
            return path, 1.0
        T.audio.prepare_for_whisper = prep_and_reset
        try:
            r = eng.transcribe_file(wav)
            ok, err = r.text == "hello", ""
        except Exception as exc:
            ok, err = False, repr(exc)
        check("file transcription survives the shared model being reset mid-way", ok, err)
        T.audio.prepare_for_whisper = real_prep

        def lang_and_reset(language):
            eng._model = None
            return real_lang(language)
        T._whisper_lang = lang_and_reset
        try:
            ok = eng.transcribe_array(np.zeros(16000, "float32")) == "hello"
        except Exception:
            ok = False
        T._whisper_lang = real_lang
        check("live transcription (transcribe_array) survives it too", ok)

        # GPU model in flight while the live worker switches the engine to CPU
        FakeWhisper.created = []
        eng = T.TranscriptionEngine("base", "cuda", "float16")

        def prep_and_fallback(path):
            eng._fall_back_to_cpu(RuntimeError("CUDA failed on the live thread"))
            return path, 1.0
        T.audio.prepare_for_whisper = prep_and_fallback
        try:
            r = eng.transcribe_file(wav)
            ok, err = r.text == "hello", ""
        except Exception as exc:
            ok, err = False, repr(exc)
        check("GPU->CPU fallback still works when the other thread fell back first",
              ok and FakeWhisper.created == ["cuda", "cpu"] and eng.effective_device == "cpu",
              f"{err} {FakeWhisper.created}")
    finally:
        T.audio.prepare_for_whisper, T._whisper_lang = real_prep, real_lang
        if saved is None:
            sys.modules.pop("faster_whisper", None)
        else:
            sys.modules["faster_whisper"] = saved


# =============================================================================
def test_k10_monotonic_clock() -> None:
    print("K10 — the recording clock is monotonic")
    rec = R.BaseRecorder(R.RecordingConfig())
    rec.state = R.RECORDING
    rec._t0 = time.monotonic() - 2.0
    real = time.time
    try:
        time.time = lambda: real() + 3600.0          # NTP / user moves the clock forward
        e1 = rec.elapsed()
        rec.pause()
        time.time = lambda: real() - 3600.0          # … and back
        rec.resume()
        e2 = rec.elapsed()
    finally:
        time.time = real
    check("wall-clock jumps don't change elapsed() or the paused time",
          1.9 < e1 < 2.5 and 1.9 < e2 < 2.5 and abs(rec._paused_total) < 0.5,
          f"e1={e1:.2f} e2={e2:.2f} paused={rec._paused_total:.2f}")

    with Devices(FakeSD2(names=("Mic 0",))):
        rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="mic"))
        rec.start()
        started, t0 = rec.started_at, rec._t0
        time.sleep(0.5)
        res = rec.stop()
    check("started_at is wall-clock (for display); the clock origin is monotonic",
          abs(started - time.time()) < 10 and abs(t0 - time.monotonic()) < 10
          and res.started_at == started)

    vr = R.VideoRecorder(R.RecordingConfig(kind="screen", fps=10, include_audio=False))
    vr._make_source = lambda: _frame_source(0.1)
    real = time.time
    try:
        vr.start()
        time.sleep(0.8)
        time.time = lambda: real() + 30.0            # a jump mid-recording
        time.sleep(0.8)
    finally:
        time.time = real
    res = vr.stop()
    d = stream_durations(res.path) if Path(res.path).exists() else {}
    check("a clock jump mid-recording doesn't stretch the video",
          abs(d.get("video", 99) - res.duration) < 0.4 and res.duration < 3.0,
          f"video={d.get('video')} elapsed={res.duration:.2f}")


# =============================================================================
def main() -> int:
    for fn in (test_k1_named_meet, test_k9_model_race, test_k10_monotonic_clock,
               test_k7_live_buffer, test_k3_renumbered_devices, test_k2_gap_padding,
               test_k4_nothing_captured, test_k5_late_partials, test_k6_close_mid_stop,
               test_k8_no_mic_system_audio):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== CAPTURE2: {passed}/{len(results)} passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        shutil.rmtree(_TMP, ignore_errors=True)
        os._exit(rc)
