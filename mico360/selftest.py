"""Built-in self-test for a compiled (PyInstaller) build.

    MICO360Meetings.exe --self-test --report <path.json> [--audio <speech.wav>]

Runs INSIDE the frozen app, so it proves the bundle really contains every
library, codec and asset each major feature needs — the thing a source-tree
test suite cannot show. It never touches the user's data: the data folder is
redirected to a throwaway temp folder before any app module is imported (only
an already-downloaded Whisper "tiny" model is copied in, so nothing is
downloaded). Results are written as JSON to --report; the exit code is 0 only
if every required check passed. Checks that need something outside the app
(a running Ollama, a speech sample) are reported as "skip" when it's absent.

This module must import nothing from the app at module level (see isolate()).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

APP_SLUG = "MICO360Meetings"          # must match config.APP_SLUG

_results: list[dict] = []
_tmp: Path | None = None


def _check(name: str, fn, required: bool = True) -> None:
    t0 = time.time()
    try:
        detail = fn()
        status = "skip" if isinstance(detail, str) and detail.startswith("SKIP") else "pass"
        _results.append({"name": name, "status": status, "detail": str(detail or ""),
                         "required": required, "secs": round(time.time() - t0, 2)})
    except Exception as exc:                                     # noqa: BLE001
        _results.append({"name": name, "status": "fail", "required": required,
                         "detail": f"{type(exc).__name__}: {exc}",
                         "trace": traceback.format_exc()[-1500:],
                         "secs": round(time.time() - t0, 2)})


def isolate() -> Path:
    """Point the app's data folder at a temp dir BEFORE mico360.config loads."""
    global _tmp
    real_root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    real_models = Path(real_root) / APP_SLUG / "whisper-models"
    _tmp = Path(tempfile.mkdtemp(prefix="mico360_selftest_"))
    models = _tmp / APP_SLUG / "whisper-models"
    models.mkdir(parents=True, exist_ok=True)
    if real_models.is_dir():
        for d in real_models.glob("*faster-whisper-tiny*"):
            try:
                shutil.copytree(d, models / d.name, dirs_exist_ok=True)
            except Exception:
                pass
    os.environ["LOCALAPPDATA"] = str(_tmp)
    os.environ["XDG_DATA_HOME"] = str(_tmp)
    os.environ["HF_HUB_OFFLINE"] = "1"          # never download during a self-test
    return _tmp


# ----------------------------------------------------------------------------
def _checks(audio: str | None) -> None:
    from mico360 import __version__, config
    config.ensure_dirs()                        # as the app does at start-up

    _check("data folder isolated", lambda: (
        str(config.DATA_DIR).startswith(str(_tmp)) or (_ for _ in ()).throw(
            AssertionError(f"not isolated: {config.DATA_DIR}"))) and str(config.DATA_DIR))
    _check("app version", lambda: __version__)

    # -- every bundled library the features rely on --------------------------
    libs = ["PySide6.QtWidgets", "faster_whisper", "ctranslate2", "av", "numpy", "soundfile",
            "sounddevice", "soundcard", "mss", "cv2", "onnxruntime", "tokenizers",
            "huggingface_hub", "ollama", "reportlab", "docx", "openpyxl", "PIL",
            "arabic_reshaper", "bidi.algorithm"]
    if sys.platform == "win32":
        libs += ["win32gui", "pythoncom", "win32com.client"]
    for mod in libs:
        _check(f"import {mod}", lambda m=mod: __import__(m) and "ok")
    _check("import pypdf (PDF text import)", lambda: __import__("pypdf") and "ok", required=False)

    # -- bundled assets / config ---------------------------------------------
    for parts in (("assets", "app.ico"), ("assets", "logo.png"), ("assets", "logo-w.png"),
                  ("assets", "logo_256.png"), ("samples", "sample_transcript.txt")):
        _check(f"asset {'/'.join(parts)}", lambda p=parts: (
            config.resource_path(*p).exists() or (_ for _ in ()).throw(
                FileNotFoundError(str(config.resource_path(*p))))) and "present")
    _check("default settings load", lambda: config.Settings().get("theme") and "ok")

    # -- codecs used by recording / import -------------------------------------
    def _codecs():
        import av
        import numpy as np
        out = []
        for codec, suffix in (("libmp3lame", ".mp3"), ("aac", ".m4a")):
            p = _tmp / f"codec{suffix}"
            c = av.open(str(p), mode="w")
            st = c.add_stream(codec, rate=16000)
            st.layout = "mono"
            fr = av.AudioFrame.from_ndarray(
                (np.sin(np.linspace(0, 440 * 2 * np.pi, 16000)) * 3000).astype("int16").reshape(1, -1),
                format="s16", layout="mono")
            fr.sample_rate = 16000
            for pkt in st.encode(fr):
                c.mux(pkt)
            for pkt in st.encode(None):
                c.mux(pkt)
            c.close()
            out.append(codec)
        p = _tmp / "codec.mp4"
        c = av.open(str(p), mode="w")
        vs = c.add_stream("libx264", rate=10)
        vs.width, vs.height, vs.pix_fmt = 64, 48, "yuv420p"
        for i in range(5):
            f = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 40, dtype="uint8"), format="rgb24")
            for pkt in vs.encode(f):
                c.mux(pkt)
        for pkt in vs.encode(None):
            c.mux(pkt)
        c.close()
        out.append("libx264")
        return ", ".join(out)
    _check("audio/video codecs (mp3, aac, h264)", _codecs)

    def _mix():
        import numpy as np
        import soundfile as sf
        from mico360.core import recording as R
        rec = R.AudioRecorder(R.RecordingConfig(kind="audio", source="both"))
        t16 = np.arange(16000) / 16000
        t48 = np.arange(48000) / 48000
        mic, sysf = str(_tmp / "_src_mic_st.wav"), str(_tmp / "_src_sys_st.wav")
        sf.write(mic, (0.3 * np.sin(2 * np.pi * 440 * t16)).astype("float32"), 16000)
        lp = (0.3 * np.sin(2 * np.pi * 1000 * t48)).astype("float32")
        sf.write(sysf, np.stack([lp, lp], axis=1), 48000)
        rec._finalize_sources([mic, sysf])
        data, sr = sf.read(rec.output_path, dtype="float32")
        spec = np.abs(np.fft.rfft(data))
        freqs = np.fft.rfftfreq(len(data), 1 / sr)
        has = lambda f: spec[(freqs > f - 20) & (freqs < f + 20)].max() > spec.mean() * 20  # noqa: E731
        assert has(440) and has(1000), "mix is missing a source"
        assert Path(rec.output_path).parent == config.RECORDINGS_DIR
        return "mic + system mixed into the recordings folder"
    _check("recording mix (mic + system audio)", _mix)

    # -- transcription + diarisation -------------------------------------------
    def _transcribe():
        if not audio or not Path(audio).exists():
            return "SKIP: no --audio sample given"
        from mico360.core import audio as A
        from mico360.core.transcription import TranscriptionEngine, model_cached
        if not model_cached("tiny"):
            return "SKIP: Whisper 'tiny' model not downloaded on this PC"
        wav = A.to_wav16k(audio, _tmp / "st16k.wav")
        res = TranscriptionEngine("tiny", "cpu", "int8").transcribe_file(str(wav), language="en")
        words = res.text.lower()
        hits = sum(w in words for w in ("meeting", "morning", "friday", "beta"))
        assert hits >= 2, f"transcript looks wrong: {res.text[:120]!r}"
        return f"{len(res.text)} chars, {hits}/4 keywords"
    _check("transcription (Whisper, CPU)", _transcribe)

    def _diar():
        if not audio or not Path(audio).exists():
            return "SKIP: no --audio sample given"
        from mico360.core import diarization as D

        class S:
            def __init__(s, a, b):
                s.start, s.end, s.speaker = a, b, None
        return f"{D.apply_to_segments(audio, [S(0, 2), S(2, 4), S(4, 6)])} speaker(s)"
    _check("speaker identification", _diar)

    # -- AI minutes (real model if Ollama runs) --------------------------------
    sample_md = ("# Meeting Minutes\n**Meeting Title:** Weekly sync\n**Date and Time:** 2026-09-27\n"
                 "## Decisions Made\n- Launch the beta on **Friday**\n"
                 "## Action Items\n| Task | Responsible Person | Deadline | Status |\n"
                 "| --- | --- | --- | --- |\n| Fix the login bug | Sarah | 2026-10-10 | Pending |\n"
                 "| Prepare the report \\| appendix | Ali | 10 Oct | Pending |\n")

    def _generate():
        from mico360.core import ollama_client as O
        from mico360.core import prompts
        st = O.check_status("http://127.0.0.1:11434", use_cache=False)
        if not st.running or not st.models:
            return "SKIP: Ollama not running"
        small = sorted(st.models, key=lambda m: (not any(s in m for s in ("0.5b", "1b", "mini", "tiny")), m))[0]
        gen = O.OllamaGenerator("http://127.0.0.1:11434", small)
        tr = config.resource_path("samples", "sample_transcript.txt").read_text(encoding="utf-8")[:3000]
        md = gen.generate_minutes(tr, prompts.BASE_TEMPLATE, "Short Summary")
        assert len(md.strip()) > 80, "model returned almost nothing"
        return f"{len(md)} chars with {small}"
    _check("AI minutes generation (Ollama)", _generate)

    # -- exports incl. Arabic ---------------------------------------------------
    def _exports():
        from mico360.core.profiles import CompanyProfile
        from mico360.export.service import export
        prof = CompanyProfile(name="Self-test Co", phone="96824123456", footer_text="تذييل",
                              show_page_numbers=True)
        md = sample_md + "\n## القرارات\n- الموافقة على الميزانية الجديدة للمشروع\n"
        sizes = {}
        for ext in (".pdf", ".docx", ".html", ".txt", ".md"):
            p = export(md, _tmp / f"minutes{ext}", prof)
            sizes[ext] = Path(p).stat().st_size
            assert sizes[ext] > 200, f"{ext} is empty"
        try:
            from pypdf import PdfReader
            txt = "".join((pg.extract_text() or "") for pg in PdfReader(str(_tmp / "minutes.pdf")).pages)
            assert any(0x0600 <= ord(c) <= 0x06FF or 0xFB50 <= ord(c) <= 0xFEFF for c in txt), \
                "Arabic glyphs missing from PDF"
        except ImportError:
            pass
        return ", ".join(f"{k} {v // 1024} KB" for k, v in sizes.items())
    _check("export PDF/Word/HTML/TXT/MD (with Arabic)", _exports)

    # -- data: history, action items, calendar, profiles -----------------------
    def _data():
        from mico360.core import calendar_ics, calendar_import
        from mico360.core.history import History, Meeting
        from mico360.core.profiles import CompanyProfile, ProfileStore
        from mico360.core.tasks import ActionItemStore
        h = History()
        mid = h.save(Meeting(id=0, title="Self-test", created_at=0, updated_at=0,
                             transcript="t", minutes=sample_md))
        assert h.get(mid).minutes == sample_md
        items = ActionItemStore(h).all_items()
        assert len(items) >= 2, f"{len(items)} action items"
        ics = _tmp / "items.ics"
        n = calendar_ics.write_ics(ics, items)
        assert n >= 1 and b"\r\r\n" not in ics.read_bytes()
        assert calendar_import.parse_ics(ics)["title"]
        ActionItemStore(h).export_csv(_tmp / "items.csv", items)
        ps = ProfileStore()
        ps.save(CompanyProfile(name="Xlsx Co", phone="123"))
        ps.export_file(ps.list(), _tmp / "profiles.xlsx")
        got = ps.import_file(_tmp / "profiles.xlsx")
        assert any(p.name == "Xlsx Co" for p in got)
        return f"history ok, {len(items)} action items, {n} calendar event(s), xlsx round-trip"
    _check("history, action items, calendar, profiles", _data)

    # -- integrations -------------------------------------------------------------
    def _watch():
        from mico360.core import meeting_watch as MW
        assert MW.detection_available(), "pywin32 missing — detection off"
        titles = MW.list_window_titles()
        assert MW.classify_window("Weekly sync | Microsoft Teams"), "Teams title not recognised"
        return f"{len(titles)} windows visible; Teams meeting recognised"
    _check("live-meeting detection", _watch, required=sys.platform == "win32")

    def _secret():
        from mico360.config import protect_secret, unprotect_secret
        enc = protect_secret("pa ss")
        assert unprotect_secret(enc) == "pa ss"
        return "encrypted" if enc != "pa ss" else "stored as-is (no DPAPI)"
    _check("email password encryption", _secret)

    def _cloud_key():
        # Only reports WHETHER a key was bundled at build time — never its value.
        try:
            from mico360 import _build_key          # type: ignore[attr-defined]
        except Exception:
            return "SKIP: no MICO360 Connect key in this build (Cloud mode inactive)"
        return "bundled (Cloud mode available)" if str(getattr(_build_key, "KEY", "")).strip()             else "SKIP: key file present but empty"
    _check("MICO360 Cloud key bundled", _cloud_key, required=False)

    def _updater():
        from mico360.core import updater as U
        assert U.is_newer("1.3.0", "1.3.0-rc1") and U.normalize_repo(
            "https://github.com/mico360om/MICO360-Meetings.git") == "mico360om/MICO360-Meetings"
        return "version + repo parsing ok"
    _check("updater logic", _updater)

    # -- GUI: build the whole window and visit every page ------------------------
    def _gui():
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QApplication, QMessageBox
        from mico360 import crash_reporter
        from mico360.ui.context import AppContext
        from mico360.ui.main_window import NAV, MainWindow
        app = QApplication.instance() or QApplication(sys.argv[:1])
        for name in ("question", "information", "warning", "critical"):   # never block
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.No))
        ctx = AppContext()
        ctx.settings.set("onboarded", True)
        ctx.settings.set("auto_check_updates", False)
        crash_reporter.install(ctx.settings)
        crash_reporter._show_dialog = lambda *a, **k: None               # report only
        win = MainWindow(ctx)
        win.show()
        for _ in range(10):
            app.processEvents()
        for i in range(len(NAV)):
            win._navigate(i)
            for _ in range(3):
                app.processEvents()

        class Boom(QThread):                                            # C4 regression
            def run(self):
                raise RuntimeError("self-test background error")
        b = Boom()
        b.start()
        b.wait(5000)
        for _ in range(10):
            app.processEvents()
        page = win.new_page
        page.transcript.setPlainText("Self-test transcript")
        page._save_history(silent=True)
        assert page._current_id, "meeting not saved"
        win._navigate(0)
        pages = len(NAV)
        page._autosave_sig = page._content_sig()                         # nothing unsaved
        if hasattr(win, "settings_page") and hasattr(win.settings_page, "_mark_clean"):
            win.settings_page._mark_clean()                              # no unsaved settings
        win.close()
        for _ in range(10):
            app.processEvents()
        return f"main window + {pages} pages rendered; background error survived"
    _check("user interface (all pages)", _gui)

    def _lock():
        from mico360 import single_instance as SI
        ok = SI.acquire()
        SI.release()
        return "acquired" if ok else "SKIP: another instance is running"
    _check("single-instance lock", _lock)


def run_cli(argv: list[str]) -> int:
    report = None
    audio = None
    if "--report" in argv:
        report = argv[argv.index("--report") + 1]
    if "--audio" in argv:
        audio = argv[argv.index("--audio") + 1]
    isolate()
    t0 = time.time()
    try:
        _checks(audio)
    except Exception as exc:                                              # noqa: BLE001
        _results.append({"name": "self-test harness", "status": "fail", "required": True,
                         "detail": f"{type(exc).__name__}: {exc}",
                         "trace": traceback.format_exc()[-1500:]})
    failed = [r for r in _results if r["status"] == "fail" and r.get("required", True)]
    out = {
        "ok": not failed,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "python": sys.version.split()[0],
        "seconds": round(time.time() - t0, 1),
        "passed": sum(r["status"] == "pass" for r in _results),
        "failed": len(failed),
        "skipped": sum(r["status"] == "skip" for r in _results),
        "results": _results,
    }
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if report:
        Path(report).write_text(text, encoding="utf-8")
    else:
        try:
            print(text)
        except Exception:
            pass
    try:
        shutil.rmtree(_tmp, ignore_errors=True)
    except Exception:
        pass
    return 0 if not failed else 1
