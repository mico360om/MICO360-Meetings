"""Regression tests for the release-blocking fixes in docs/BUG_REPORT.md.

    python tests/release_fixes.py

  C3   auto-recorded minutes must include the other participants (system audio)
  C4   an exception on a background thread must not crash the app
  H23  CI-built installers must include live-meeting detection (pywin32)
  H6   finished recordings must be kept (never auto-deleted)
  H24  a release with more than one installer must still update correctly

Runs against a THROWAWAY data folder (LOCALAPPDATA is redirected before the app
is imported) and needs no network, Ollama, Whisper or audio hardware.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mico360_fixes_"))
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
from PySide6.QtWidgets import QApplication              # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360 import config                              # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"
config.ensure_dirs()

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


# =============================================================================
def test_remote_participants() -> None:
    print("C3 — auto-record minutes include the other participants")
    from mico360.core import recording as R
    from mico360.ui.recording_panel import RecordingPanel

    # 1) the saved mic+system recording really contains the system audio
    rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
    t16 = np.arange(16000 * 2) / 16000
    t48 = np.arange(48000 * 2) / 48000
    mic = str(config.TMP_DIR / "_src_mic_test.wav")
    sys_ = str(config.TMP_DIR / "_src_sys_test.wav")
    sf.write(mic, (0.3 * np.sin(2 * np.pi * 440 * t16)).astype("float32"), 16000)
    loop = (0.3 * np.sin(2 * np.pi * 1000 * t48)).astype("float32")
    sf.write(sys_, np.stack([loop, loop], axis=1), 48000)   # loopback is 48 kHz stereo
    rec._finalize_sources([mic, sys_])
    out, sr = sf.read(rec.output_path, dtype="float32")
    fq = dominant_freqs(out, sr)
    check("saved recording mixes the mic (440 Hz) AND system audio (1 kHz)",
          any(abs(f - 440) < 15 for f in fq) and any(abs(f - 1000) < 15 for f in fq), f"{fq}")

    # 2) auto-record generates from that full recording, not the mic-only live draft
    class FakeRec:
        state = R.RECORDING

        def __init__(self, path):
            self.path = path

        def stop(self):
            self.state = R.STOPPED
            return R.RecordingResult(path=self.path, kind="audio", duration=2.0,
                                     size_bytes=Path(self.path).stat().st_size, fmt="wav",
                                     started_at=time.time())

    def run_stop(auto: bool):
        panel = RecordingPanel(toast=None, ctx=None)
        got = {"file": [], "live": []}
        panel.recordingReady.connect(got["file"].append)
        panel.liveTranscriptReady.connect(got["live"].append)
        panel._rec = FakeRec(rec.output_path)
        panel._auto_mode = auto
        panel._live_text = "only what the local microphone heard"
        panel._finish_stop()
        return got

    got = run_stop(auto=True)
    check("auto-record: full recording is queued for transcription",
          got["file"] == [rec.output_path], f"{got}")
    check("auto-record: mic-only live draft is NOT used for the minutes",
          got["live"] == [], f"{got}")
    got = run_stop(auto=False)
    check("manual recording with live text still offers the live draft",
          got["live"] and not got["file"], f"{got}")


# =============================================================================
_CRASH_SCRIPT = textwrap.dedent(r'''
    import os, sys, threading, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {root!r})
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QThread, QTimer
    app = QApplication([])
    from mico360 import crash_reporter as cr
    shown = []
    cr._show_dialog = lambda report, path, summary: shown.append(
        (threading.current_thread() is threading.main_thread(), summary))
    class S:
        def get(self, k, d=None): return {{"crash_reporter": True}}.get(k, d)
    cr.install(S())
    t = threading.Thread(target=lambda: 1 / 0); t.start(); t.join()
    class Q(QThread):
        def run(self): raise ValueError("boom in QThread")
    q = Q(); q.start(); q.wait()
    QTimer.singleShot(400, app.quit); app.exec()
    print("SHOWN", len(shown), "ALL_MAIN", all(m for m, _ in shown))
    sys.stdout.flush(); os._exit(0)
''')


def test_background_crash() -> None:
    print("C4 — an exception on a background thread doesn't crash the app")
    script = _TMP / "crash_check.py"
    script.write_text(_CRASH_SCRIPT.format(root=str(ROOT)), encoding="utf-8")
    env = {**os.environ, "LOCALAPPDATA": str(_TMP / "crash_data")}
    p = subprocess.run([sys.executable, "-u", str(script)], env=env, capture_output=True,
                       text=True, timeout=120, encoding="utf-8", errors="replace")
    m = re.search(r"SHOWN (\d+) ALL_MAIN (True|False)", p.stdout or "")
    check("process survives exceptions on a thread and a QThread (no crash)",
          p.returncode == 0 and m is not None, f"rc={p.returncode} {p.stdout[-200:]}")
    check("the crash dialog is shown for both, on the GUI thread",
          m is not None and m.group(1) == "2" and m.group(2) == "True",
          (m.group(0) if m else "no result"))


# =============================================================================
def test_meeting_detection_in_builds() -> None:
    print("H23 — builds include live-meeting detection (pywin32)")
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    check("requirements.txt installs pywin32 on Windows",
          re.search(r"^pywin32\S*\s*;\s*sys_platform\s*==\s*[\"']win32[\"']", req, re.M), "")
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    check("release build fails if the win32 modules are missing",
          "import win32gui, pythoncom, win32com.client" in wf)
    if sys.platform == "win32":
        from mico360.core import meeting_watch as MW
        check("meeting detection is available in this environment", MW.detection_available())
        titles = MW.list_window_titles()
        check("window titles can be enumerated", isinstance(titles, list))


# =============================================================================
def test_recordings_kept() -> None:
    print("H6 — finished recordings are kept")
    from mico360.core import maintenance as MT
    from mico360.core import recording as R
    a = R.AudioRecorder(R.RecordingConfig(kind="audio"))
    v = R.VideoRecorder(R.RecordingConfig(kind="screen"))
    check("audio recordings are saved in the permanent recordings folder",
          Path(a.output_path).parent == config.RECORDINGS_DIR != config.TMP_DIR, a.output_path)
    check("screen/camera recordings are saved in the permanent recordings folder",
          Path(v.output_path).parent == config.RECORDINGS_DIR, v.output_path)
    d = _TMP / "purge"; d.mkdir()
    old = time.time() - 30 * 86400
    files = {n: d / n for n in ("recording_1.wav", "recording_2.mp4", "_src_mic_1.wav",
                                "_aud_1.wav", "x_16k.wav")}
    for f in files.values():
        f.write_bytes(b"x" * 10)
        os.utime(f, (old, old))
    MT.purge_tmp(older_than_days=7, tmp_dir=d)
    check("a 30-day-old recording survives the startup clean-up",
          files["recording_1.wav"].exists() and files["recording_2.mp4"].exists())
    check("old intermediate files are still cleaned up",
          not any(files[n].exists() for n in ("_src_mic_1.wav", "_aud_1.wav", "x_16k.wav")))
    dest = _TMP / "recs"
    moved = MT.adopt_legacy_recordings(tmp_dir=d, dest_dir=dest)
    check("recordings left in the temp folder by older versions are moved to Recordings",
          moved == 2 and (dest / "recording_1.wav").exists()
          and not files["recording_1.wav"].exists(), f"moved={moved}")


# =============================================================================
def test_multi_installer_release() -> None:
    print("H24 — a release with two installers still updates correctly")
    from mico360.core import updater as up
    good, bad = "a" * 64, "b" * 64
    base = "https://github.com/o/r/releases/download/v1.2.2/"
    assets = [                                  # mirrors the real v1.2.2 release
        {"name": "MICO360Meetings-Setup-1.2.2.exe", "size": 169, "browser_download_url": base + "MICO360Meetings-Setup-1.2.2.exe"},
        {"name": "MICO360Meetings-Setup-1.2.2.exe.sha256", "size": 66, "browser_download_url": base + "keyed.sha256"},
        {"name": "MICO360Meetings-Setup.exe", "size": 139, "browser_download_url": base + "MICO360Meetings-Setup.exe"},
        {"name": "MICO360Meetings-Setup.exe.sha256", "size": 66, "browser_download_url": base + "ci.sha256"},
        {"name": "SHA256SUMS.txt", "size": 93, "browser_download_url": base + "SHA256SUMS.txt"},
    ]
    served = {base + "keyed.sha256": good + "\r\n", base + "ci.sha256": bad + "\r\n",
              base + "SHA256SUMS.txt": f"{bad} *MICO360Meetings-Setup.exe\r\n"}
    body = ("## Download\n**MICO360Meetings-Setup.exe** (161 MB)\n\n```\n"
            f"{good}  MICO360Meetings-Setup-1.2.2.exe\n```\n")
    real_fetch = up.fetch_text
    up.fetch_text = lambda url, timeout=8.0: served[url]
    try:
        for label, order in (("API order", assets), ("reversed order", assets[::-1])):
            url, size, cks, per_file = up._pick_assets(order, "1.2.2")
            info = up.UpdateInfo(download_url=url, checksum_url=cks, checksum_per_file=per_file,
                                 expected_sha256=up.parse_checksum(body, Path(url).name))
            check(f"{label}: picks the release's own installer",
                  url.endswith("MICO360Meetings-Setup-1.2.2.exe"), url)
            check(f"{label}: expects that installer's own hash",
                  up.resolve_expected_sha256(info) == good)
    finally:
        up.fetch_text = real_fetch
    check("a hash that names a different file is never used",
          up.parse_checksum(body, "MICO360Meetings-Setup.exe") == "")
    check("a per-file .sha256 (bare hash) is accepted",
          up.parse_checksum(good + "\n", "x.exe", allow_bare=True) == good)
    one = [{"name": "MICO360Meetings-Setup.exe", "size": 1, "browser_download_url": "u/e"},
           {"name": "SHA256SUMS.txt", "size": 1, "browser_download_url": "u/s"}]
    check("a normal single-installer release is unaffected",
          up._pick_assets(one, "1.2.4")[0] == "u/e" and up._pick_assets(one, "1.2.4")[2] == "u/s")
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    job_env = wf.split("steps:")[0]
    check("release workflow: no secret values in job-wide env",
          "secrets.MICO360_CONNECT_API_KEY }}" not in job_env.replace("!= '' }}", "")
          and "CERT_PASSWORD: ${{" not in job_env)
    check("release workflow: publishes the maintained notes, not an auto body",
          "body_path: build/RELEASE_NOTES_${{ github.ref_name }}.md" in wf
          and "generate_release_notes" not in wf)
    check("release workflow: refuses a tag that doesn't match the app version",
          "does not match mico360.__version__" in wf)


# =============================================================================
def test_smtp_password_encrypted() -> None:
    print("L6 — the SMTP password is encrypted at rest")
    import json
    from mico360.config import Settings, _SECRET_PREFIX
    f = _TMP / "secret_settings.json"
    legacy = " ".join(["", "s3cret", "pass", ""])     # " s3cret pass " (built at runtime so the
    f.write_text(json.dumps({"smtp_password": legacy}), encoding="utf-8")  # secret scan stays strict)
    s = Settings(f)
    on_disk = f.read_text(encoding="utf-8")
    if sys.platform == "win32":
        check("an old plaintext password is encrypted on load",
              json.loads(on_disk)["smtp_password"].startswith(_SECRET_PREFIX) and "s3cret" not in on_disk)
    check("the password reads back exactly (spaces kept)", s.get("smtp_password") == " s3cret pass ")
    s.set("smtp_password", "new-Pa55")
    check("a newly saved password is not stored in plain text",
          "new-Pa55" not in f.read_text(encoding="utf-8") or sys.platform != "win32")
    check("…and survives a reload", Settings(f).get("smtp_password") == "new-Pa55")
    import base64 as _b64
    foreign = _SECRET_PREFIX + _b64.b64encode(
        bytes.fromhex("01000000d08c9ddf0115d1118c7a00c04fc297eb") + b"\x00" * 40).decode()
    f.write_text(json.dumps({"smtp_password": foreign}), encoding="utf-8")   # another PC's blob
    check("an undecryptable value is treated as empty (no crash)", Settings(f).get("smtp_password") == "")
    f.write_text("{}", encoding="utf-8")
    s3 = Settings(f)
    s3.set("smtp_password", "dpapi:mypass")
    check("a password that starts with 'dpapi:' is kept (and encrypted)",
          Settings(f).get("smtp_password") == "dpapi:mypass" and "mypass" not in f.read_text(encoding="utf-8"))

# =============================================================================
def test_resume_long_generation() -> None:
    print("M27 — a failed long generation resumes instead of starting over")
    from mico360.core import generation as G
    G.clear_part_cache()
    calls, state = [], {"fail": True}

    def chat(prompt, cancel=None):
        calls.append(prompt)
        if "part 3 of" in prompt.lower() and state["fail"]:
            raise ConnectionError("503 no node")
        return "notes"

    long = " ".join(f"Sentence number {i} about the budget." for i in range(3000))
    tmpl = "T\nTranscript:\n[TRANSCRIPT_HERE]"
    try:
        G.run_minutes_pipeline(chat, "m", long, tmpl, chunk_chars=6000)
    except ConnectionError:
        pass
    state["fail"] = False
    calls.clear()
    G.run_minutes_pipeline(chat, "m", long, tmpl, chunk_chars=6000)
    parts = [c for c in calls if "summarizing PART" in c]
    check("retry reuses the parts that already finished",
          parts and "part 3 of" in parts[0].lower(), parts[0][:60] if parts else "no parts")
    calls.clear()
    G.run_minutes_pipeline(chat, "other-model", long, tmpl, chunk_chars=6000)
    check("a different model never reuses cached notes",
          sum("summarizing PART" in c for c in calls) > 3)


# =============================================================================
def test_cloud_transport_security() -> None:
    print("C5 — Cloud mode upgrades to HTTPS when offered and asks before plain HTTP")
    import importlib
    import urllib.request
    from mico360.core import cloud_client as CC
    CC = importlib.reload(CC)                       # fresh probe state
    CC.MICO360_CONNECT_HTTPS_CANDIDATES = ("https://127.0.0.1:9/v1",)   # nothing listens
    CC._probe_https(timeout=1.0)
    gen = CC.CloudGenerator(key="k")
    check("without server TLS it stays on the configured URL (not encrypted)",
          not CC.is_encrypted() and gen.base_url.startswith("http://"))
    import json as _json
    import urllib.error as _ue

    class _Resp:
        def __init__(self, status, body):
            self.status, self._b = status, body
        def read(self, n=-1): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def close(self): pass

    def serve(kind):
        def fake(req, timeout=None):
            if kind == "api":
                return _Resp(200, _json.dumps({"object": "list", "data": [{"id": "m"}]}).encode())
            if kind == "api401":
                raise _ue.HTTPError(req.full_url, 401, "u", {}, __import__("io").BytesIO(
                    b'{"error":{"message":"Invalid or missing API key."}}'))
            if kind == "404":
                raise _ue.HTTPError(req.full_url, 404, "nf", {}, __import__("io").BytesIO(b"<html>"))
            if kind == "html":
                return _Resp(200, b"<html>website</html>")
            if kind == "redirect":
                raise _ue.HTTPError(req.full_url, 301, "moved", {}, None)   # not followed
        return fake
    real_open = CC._open
    try:
        for kind in ("404", "html", "redirect"):
            CC._open = serve(kind)
            CC.MICO360_CONNECT_HTTPS_CANDIDATES = (f"https://{kind}.example/v1",)
            CC._probe_https(timeout=1.0)
        check("a website, 404 or redirect is NOT taken as the encrypted API",
              not CC.is_encrypted() and gen.base_url.startswith("http://"))
        CC._open = serve("api401")                          # the real API without a key
        CC.MICO360_CONNECT_HTTPS_CANDIDATES = ("https://secure.example/v1",)
        CC._probe_https(timeout=1.0)
    finally:
        CC._open = real_open
    check("once HTTPS is offered, all Cloud traffic uses it",
          CC.is_encrypted() and gen.base_url == "https://secure.example/v1")
    # a redirect must never carry the API key to another server
    import http.server
    import threading as _th
    hits_b = []

    class B(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits_b.append(self.headers.get("Authorization"))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[]}')
        def log_message(self, *a): pass
    sb = http.server.HTTPServer(("127.0.0.1", 0), B)
    _th.Thread(target=sb.serve_forever, daemon=True).start()

    class A(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(301)
            self.send_header("Location", f"http://127.0.0.1:{sb.server_port}/v1/models")
            self.end_headers()
        def log_message(self, *a): pass
    sa = http.server.HTTPServer(("127.0.0.1", 0), A)
    _th.Thread(target=sa.serve_forever, daemon=True).start()
    followed = True
    try:
        CC._open(urllib.request.Request(f"http://127.0.0.1:{sa.server_port}/v1/models",
                                        headers={"Authorization": "Bearer secret"}), 3)
    except _ue.HTTPError as exc:
        followed = exc.code != 301
    sa.shutdown(); sb.shutdown()
    check("Cloud requests don't follow redirects (the key never reaches another server)",
          not followed and hits_b == [], f"followed={followed} hits={hits_b}")
    # consent prompt when switching to Cloud while unencrypted
    from PySide6.QtWidgets import QMessageBox
    from mico360.ui.context import AppContext
    from mico360.ui.settings_page import SettingsPage
    CC = importlib.reload(CC)                       # back to "not encrypted"
    CC.start_https_probe = lambda: None
    ctx = AppContext()
    ctx.settings.set("ai_provider", "local")

    class _T:
        def show_message(self, *a, **k): pass
    sp = SettingsPage(ctx, _T(), lambda *a: None, lambda *a: None)
    asked = []
    real_q = QMessageBox.question
    try:
        QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(1), QMessageBox.No)[1])
        sp.provider_box.setCurrentIndex(sp.provider_box.findData("cloud"))
        sp._provider_changed()
        check("switching to Cloud asks first; declining keeps Local",
              asked and ctx.settings.get("ai_provider") == "local"
              and sp.provider_box.currentData() == "local")
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        sp.provider_box.setCurrentIndex(sp.provider_box.findData("cloud"))
        sp._provider_changed()
        check("accepting switches to Cloud", ctx.settings.get("ai_provider") == "cloud")
    finally:
        QMessageBox.question = real_q
        ctx.settings.set("ai_provider", "local")


# =============================================================================
_KEEP: list = []                                  # Qt objects kept alive until os._exit


def test_review_round2() -> None:
    print("Review round 2 — recording/switching, quitting, settings, drops, e-mail")
    import threading
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QMessageBox
    from mico360.core.history import Meeting
    from mico360.ui.context import AppContext
    from mico360.ui.main_window import MainWindow

    msgs = []
    real = {n: getattr(QMessageBox, n) for n in ("question", "information", "warning")}
    QMessageBox.information = staticmethod(lambda *a, **k: msgs.append(a[1] if len(a) > 1 else ""))
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        ctx = AppContext()
        ctx.settings.set("onboarded", True)
        ctx.settings.set("auto_check_updates", False)
        win = MainWindow(ctx)
        _KEEP.append(win)
        page = win.new_page
        toasts = []
        page.toast.show_message = lambda m, kind="info", *a, **k: toasts.append((kind, m))

        # U1: a running recording blocks opening another meeting
        b_id = ctx.history.save(Meeting(id=0, title="B", created_at=0, updated_at=0,
                                        transcript="BOARD", minutes="MB"))
        page.transcript.setPlainText("CURRENT CALL")
        page._save_history(silent=True)
        cur = page._current_id
        page.recorder_panel.is_recording = lambda: True
        opened = win._open_meeting(ctx.history.get(b_id))
        check("opening another meeting while recording is refused",
              not opened and page._current_id == cur and msgs, f"opened={opened}")
        check("…and the other meeting is untouched", ctx.history.get(b_id).transcript == "BOARD")
        page.recorder_panel.is_recording = lambda: False

        # U2: once quitting, completion handlers start no new work
        page._closing = True
        page.transcript.setPlainText("text")
        page._generate()
        page._send_prepared_email(None, {"to": ["a@x.com"], "subject": "s", "body": "b"}, [], "")
        check("while quitting, no generation or e-mail send starts",
              not page._is_working() and getattr(page, "_email_worker", None) is None)
        page._closing = False

        # U4: saving Settings keeps the model picked on New Meeting
        sp = win.settings_page
        st = ctx.ai_status(force=True) if hasattr(ctx, "ai_status") else None
        models = list(getattr(st, "models", []) or [])
        picked = models[-1] if models else "any-model"         # a model that is installed
        ctx.settings.set("ollama_model", picked)
        win._navigate(win.stack.indexOf(sp))
        sp.fillers.setChecked(not sp.fillers.isChecked())      # an unrelated edit
        sp._save()
        check("an unrelated Settings save keeps the model chosen on New Meeting",
              ctx.settings.get("ollama_model") == picked,
              ctx.settings.get("ollama_model"))

        # U6: one summary toast that names skipped files
        toasts.clear()
        page._add_files([str(_TMP / "call.mp3"), str(_TMP / "notes.xyz")])
        kind, m = toasts[-1] if toasts else ("", "")
        check("multi-file drop reports skipped files instead of hiding them",
              kind == "warn" and "notes.xyz" in m and "1 of 2" in m, m)

        # U3: a task that ignores Cancel can't hang quitting or crash the app
        class Stubborn(QThread):
            def cancel(self): pass
            def run(self):
                import time as _t
                _t.sleep(20)
        stub = Stubborn()
        _KEEP.append(stub)
        stub.start()
        page._bg_workers.add(stub)
        import time as _t
        t0 = _t.time()
        win.close()
        waited = _t.time() - t0
        check("quitting waits at most ~15 s for stuck work",
              waited < 17, f"{waited:.1f}s")
        check("…and marks it so the app exits directly instead of crashing",
              stub in getattr(win, "unfinished_threads", []))
    finally:
        for n, f in real.items():
            setattr(QMessageBox, n, f)


# =============================================================================
def main() -> int:
    for fn in (test_remote_participants, test_background_crash,
               test_meeting_detection_in_builds, test_recordings_kept,
               test_multi_installer_release, test_smtp_password_encrypted,
               test_resume_long_generation, test_cloud_transport_security,
               test_review_round2):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== FIXES: {passed}/{len(results)} passed ====")
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
