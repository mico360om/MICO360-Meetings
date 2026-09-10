"""Per-module test harness — exercises every module's real behaviour, grouped by
architectural layer. Run:  set QT_QPA_PLATFORM=offscreen && python tests/module_tests.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.gettempdir())

results: list[tuple[str, str, bool, str]] = []
_cur_group = ""


def group(name):
    global _cur_group
    _cur_group = name
    print(f"\n=== {name} ===")


def t(module: str, fn):
    """Run a test fn; record pass/fail with detail."""
    try:
        detail = fn() or ""
        results.append((_cur_group, module, True, str(detail)))
        print(f"  [PASS] {module}  {detail}")
    except Exception as exc:
        results.append((_cur_group, module, False, f"{type(exc).__name__}: {exc}"))
        print(f"  [FAIL] {module}  {type(exc).__name__}: {exc}")
        traceback.print_exc()


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # one shared offscreen window for UI modules
    from mico360.ui.context import AppContext
    ctx = AppContext()
    ctx.settings.set("onboarded", True)

    # =====================================================================
    group("Entry & bootstrap")

    def _init():
        import mico360
        assert mico360.__version__ and mico360.__app_name__
        return f"v{mico360.__version__}"
    t("mico360.__init__", _init)

    def _config():
        from mico360 import config
        s = config.Settings(config.SETTINGS_FILE)
        s.set("theme", "dark"); assert s.get("theme") == "dark"
        config.apply_quality_preset(s, "Accurate"); assert s.get("whisper_model") == "small"
        assert config.DEFAULT_REPO
        config.ensure_dirs(); assert config.DATA_DIR.exists()
        return "settings+presets+paths"
    t("config", _config)

    def _single_inst():
        from mico360 import single_instance as si
        ok = si.acquire(); si.release()
        return f"acquire={ok}"
    t("single_instance", _single_inst)

    def _main_mod():
        from mico360 import main
        assert callable(main.main) and callable(main._set_windows_appid)
        return "entrypoint present"
    t("main", _main_mod)

    # =====================================================================
    group("Core — processing")

    def _audio():
        from mico360.core import audio
        assert audio.is_media_file("x.mp3") and not audio.is_media_file("x.pdf")
        info = audio.probe("samples/spoken_test.wav")
        assert info["duration"] > 0
        return f"probe dur={info['duration']:.1f}s"
    t("core.audio", _audio)

    def _cleaning():
        from mico360.core import cleaning
        c = cleaning.clean_transcript("um the the meeting uh started", True)
        assert "the the" not in c
        chunks = cleaning.chunk_text("A. " * 4000, max_chars=2000, overlap_chars=200)
        assert len(chunks) > 1
        return f"clean+{len(chunks)} overlapping chunks"
    t("core.cleaning", _cleaning)

    def _transcription():
        from mico360.core.transcription import TranscriptionEngine, TranscriptResult, Segment
        r = TranscriptResult("hi", [Segment(0, 1, "hi", "Speaker 1")], speakers=1)
        assert "[Speaker 1]" in r.as_speaker_text()
        eng = TranscriptionEngine("tiny", "auto", "int8")
        res = eng.transcribe_file("samples/spoken_test.wav", language="en")
        assert "meeting" in res.text.lower()
        return f"CPU transcribe {len(res.text)} chars"
    t("core.transcription", _transcription)

    def _ollama():
        from mico360.core import ollama_client as oc
        st = oc.check_status()
        assert isinstance(st.models, list)
        assert oc.RECOMMENDED_MODELS and callable(oc.pull_model)
        return f"ollama running={st.running} models={len(st.models)}"
    t("core.ollama_client", _ollama)

    def _diar():
        from mico360.core import diarization as d
        class S:
            def __init__(s, a, b): s.start, s.end, s.speaker = a, b, None
        n = d.apply_to_segments("samples/spoken_test.wav", [S(0, 2), S(2, 4)])
        assert n >= 1
        return f"{n} speaker(s)"
    t("core.diarization", _diar)

    def _recording():
        from mico360.core import recording as R
        import sounddevice as sd
        assert callable(R.list_microphones) and callable(R.system_audio_device)
        # mixing pipeline (no device needed)
        import numpy as np, soundfile as sf
        a, b = TMP / "_m.wav", TMP / "_s.wav"
        sf.write(a, np.zeros(16000, "float32"), 16000)
        sf.write(b, np.zeros(44100, "float32"), 44100)
        rec = R.AudioRecorder(R.RecordingConfig(source="both"))
        rec._finalize_sources([str(a), str(b)])
        ok = Path(rec.output_path).exists()
        for p in (a, b, Path(rec.output_path)):
            Path(p).unlink(missing_ok=True)
        assert ok
        return f"sources+mix; sys_audio={R.system_audio_supported()}"
    t("core.recording", _recording)

    # =====================================================================
    group("Core — supporting services")

    def _history():
        from mico360.core.history import History, Meeting
        h = History(TMP / "_qa_hist.db")
        mid = h.save(Meeting(0, "Sprint Review", 0, 0, transcript="login bug", minutes="# M"))
        assert h.get(mid).title == "Sprint Review"
        assert any(m.id == mid for m in h.list("login"))
        h.delete(mid); h.close()
        (TMP / "_qa_hist.db").unlink(missing_ok=True)
        return "CRUD+search"
    t("core.history", _history)

    def _profiles():
        from mico360.core.profiles import ProfileStore, CompanyProfile
        st = ProfileStore()
        p = st.save(CompanyProfile(name="QA Co", footer_text="x"))
        exp = TMP / "_qa_prof.xlsx"
        st.export_file([p], exp)
        imp = st.import_file(exp)
        assert imp and imp[0].name == "QA Co"
        for pid in [p.id] + [i.id for i in imp]:
            st.delete(pid)
        exp.unlink(missing_ok=True)
        return "CRUD+xlsx round-trip"
    t("core.profiles", _profiles)

    def _prompts():
        from mico360.core.prompts import PromptLibrary, build_generation_prompt, BASE_TEMPLATE, PROMPT_CATEGORIES
        lib = PromptLibrary()
        assert len(lib.list()) >= 24
        cats = {p.category for p in lib.list()}
        assert set(PROMPT_CATEGORIES).issubset(cats)
        sp = lib.add("QA", "x [TRANSCRIPT_HERE]", category="Summary")
        assert lib.get(sp.id); lib.delete(sp.id)
        pr = build_generation_prompt(BASE_TEMPLATE, "Formal Minutes", "hello")
        assert "hello" in pr
        return f"{len(lib.list())} prompts, {len(cats)} categories"
    t("core.prompts", _prompts)

    def _documents():
        from mico360.core import documents as doc
        f = TMP / "_qa.txt"; f.write_text("hello world", encoding="utf-8")
        assert doc.extract_text(f) == "hello world"
        f.unlink(missing_ok=True)
        assert doc.is_document("a.docx") and doc.is_image("a.png")
        return "txt extract + type detect"
    t("core.documents", _documents)

    def _updater():
        from mico360.core import updater as u
        assert u.is_newer("2.0.0", "1.0.0") and not u.is_newer("1.0.0", "1.0.0")
        info = u.check_for_updates("")
        assert info.status == u.NOT_CONFIGURED
        assert u.repo_url("a/b") == "https://github.com/a/b"
        assert callable(u.check_for_updates) and callable(u.download_asset)
        return "version compare + repo URL"
    t("core.updater", _updater)

    # =====================================================================
    group("Export")

    def _mdblocks():
        from mico360.export import md_blocks
        blks = md_blocks.parse("# H1\n**Key:** v\n- a\n- b\n| C1 | C2 |\n| - | - |\n| 1 | 2 |")
        kinds = [b.kind for b in blks]
        assert "h1" in kinds and "kv" in kinds and "bullet" in kinds and "table" in kinds
        assert md_blocks.runs("a **b** c")[1] == ("b", True)
        return f"parsed {kinds}"
    t("export.md_blocks", _mdblocks)

    def _exports():
        from mico360.export import service
        from mico360.core.profiles import CompanyProfile
        md = ("# Meeting Minutes\n**Meeting Title:** T\n## Action Items\n"
              "| Task | Who | Due | Status |\n| - | - | - | - |\n| x | y | z | Pending |")
        prof = CompanyProfile(name="QA", footer_text="c", show_page_numbers=True)
        sizes = {}
        for ext in (".txt", ".docx", ".pdf", ".md", ".html"):
            p = service.export(md, str(TMP / f"_qa{ext}"), prof)
            sizes[ext] = Path(p).stat().st_size
            assert sizes[ext] > 100
            Path(p).unlink(missing_ok=True)
        return f"5 formats {sizes}"
    t("export.service (+txt/docx/pdf/html)", _exports)

    # =====================================================================
    group("UI — shell & pages")
    from mico360.ui.main_window import MainWindow
    win = MainWindow(ctx); win.resize(1180, 800); win.show()
    for _ in range(4):
        app.processEvents()

    def _mainwin():
        assert win.stack.count() == 8
        from PySide6.QtGui import QShortcut
        assert len(win.findChildren(QShortcut)) >= 5
        for i in range(7):
            win._navigate(i)
        return f"{win.stack.count()} pages + shortcuts + nav"
    t("ui.main_window", _mainwin)

    def _context():
        assert ctx.transcription_engine() and ctx.generator()
        assert hasattr(ctx, "history") and hasattr(ctx, "profiles") and hasattr(ctx, "prompts")
        return "services wired"
    t("ui.context", _context)

    def _pages():
        np_ = win.new_page
        np_.refresh_models(); np_.refresh_prompts()
        assert np_.style_box.count() == 5 and np_.prompt_box.count() >= 24
        assert np_.minutes_tabs.count() == 2
        win.history_page.reload(); win.profiles_page.reload(); win.prompts_page.reload()
        win.actions_page.reload()
        assert win.settings_page.preset.count() >= 3
        assert np_.mtype_box.count() >= 6            # meeting-type presets
        return "New/History/ActionItems/Profiles/Prompts/Settings"
    t("ui.pages", _pages)

    def _pages_extra():
        from PySide6.QtWidgets import QTabWidget, QTextBrowser
        assert win.help_page.findChild(QTabWidget).count() == 4
        html = " ".join(b.toHtml() for b in win.help_page.findChildren(QTextBrowser))
        assert "info@mico360.com" in html
        win.updates_page.check  # callable exists
        return "Help(4 tabs)+Updates"
    t("ui.pages_extra", _pages_extra)

    def _onboarding():
        from mico360.ui.onboarding import OnboardingDialog
        d = OnboardingDialog(ctx, win)
        assert d.checks.text()
        return "wizard builds"
    t("ui.onboarding", _onboarding)

    # =====================================================================
    group("UI — components")

    def _components():
        from mico360.ui import components as c
        sec = c.CollapsibleSection("X", expanded=True)
        sec.set_expanded(False); assert not sec.is_expanded()
        sec.set_status("3 words");
        da = c.DropArea(); assert da is not None
        assert c.hint("h") is not None and c.subtitle("s") is not None
        return "Card/Collapsible/DropArea/Toast/hint"
    t("ui.components", _components)

    def _dialogs():
        from mico360.ui.dialogs import ProfileDialog, PromptDialog, PagePreview
        from mico360.core.profiles import CompanyProfile
        pd = ProfileDialog(ctx.profiles, CompanyProfile(name="Z"), win)
        assert pd.preview is not None
        prd = PromptDialog("n", "t [TRANSCRIPT_HERE]", "Summary", win)
        name, text, cat = prd.values(); assert cat == "Summary"
        return "Profile+Prompt+Preview"
    t("ui.dialogs", _dialogs)

    def _recording_panel():
        rp = win.new_page.recorder_panel
        assert rp.stack.count() == 3
        assert rp.source_box.count() >= 1
        rp.viz.push(0.5); rp.viz.set_active(True)
        return f"{rp.source_box.count()} source(s)+visualizer"
    t("ui.recording_panel", _recording_panel)

    def _theme():
        from mico360.ui import theme
        assert "background" in theme.build_qss("dark") and "background" in theme.build_qss("light")
        return "dark+light qss"
    t("ui.theme", _theme)

    def _workers():
        from mico360.ui import workers as w
        for cls in ("TranscribeWorker", "GenerateWorker", "EmailWorker",
                    "UpdateCheckWorker", "UpdateDownloadWorker", "ModelPullWorker", "ExportWorker"):
            assert hasattr(w, cls), cls
        return "7 worker classes"
    t("ui.workers", _workers)

    # =====================================================================
    group("Cross-cutting / infrastructure")

    def _crash():
        from mico360 import crash_reporter as cr
        try:
            raise ValueError("boom")
        except Exception as e:
            rep = cr.build_report(type(e), e, e.__traceback__)
        assert "Traceback" in rep
        p = cr._write_report(rep); assert p.exists(); p.unlink(missing_ok=True)
        d = cr.CrashDialog(rep, p, "a/b", "ValueError")
        assert d.gh_btn.isEnabled()
        return "excepthook+report+dialog"
    t("crash_reporter", _crash)

    def _logging():
        from mico360 import config
        log = config.setup_logging()
        log.info("module-test log line")
        assert (config.LOG_DIR / "app.log").exists()
        return "rotating log"
    t("logging (config)", _logging)

    def _emailer():
        from mico360.core.emailer import SmtpConfig, send_email
        cfg = SmtpConfig(host="in-v3.mailjet.com", port=587, user="", password="", sender="")
        assert not cfg.configured
        try:
            send_email(cfg, "x@y.com", "s", "b")   # unconfigured -> must raise, not send
            raise AssertionError("should have refused to send")
        except ValueError:
            pass
        # message build with unicode + attachment (no network)
        from email.message import EmailMessage
        m = EmailMessage(); m["From"] = "a@b.com"; m["To"] = "c@d.com"
        m["Subject"] = "Minutes — Café"; m.set_content("Body — “ok”")
        assert len(m.as_bytes()) > 50
        return "SmtpConfig + guarded send + unicode encode"
    t("core.emailer", _emailer)

    def _update_verify():
        import hashlib as _h, tempfile, os as _os
        from mico360.core import updater as up
        # sha256_file matches the stdlib
        fd, p = tempfile.mkstemp(suffix=".bin"); _os.close(fd)
        data = b"MICO360 update payload \x00\x01\x02" * 1000
        with open(p, "wb") as fh:
            fh.write(data)
        digest = _h.sha256(data).hexdigest()
        assert up.sha256_file(p) == digest, "sha256_file mismatch"
        # parse_checksum: SHA256SUMS-style line, filename match, and fallback
        sums = f"{digest} *MICO360Meetings-Setup.exe\n<other>  decoy.zip\n"
        assert up.parse_checksum(sums, "MICO360Meetings-Setup.exe") == digest
        assert up.parse_checksum(f"Checksum: {digest}") == digest       # bare fallback
        assert up.parse_checksum("no hash here", "x.exe") == ""
        # resolve prefers an explicit expected hash
        info = up.UpdateInfo(download_url="https://x/MICO360Meetings-Setup.exe",
                             expected_sha256=digest)
        assert up.resolve_expected_sha256(info) == digest
        # verify_download passes on a match (unsigned temp file is allowed)…
        note, status = up.verify_download(p, digest)
        assert "SHA256 verified" in note
        # …and refuses on a mismatch
        try:
            up.verify_download(p, "0" * 64)
            raise AssertionError("should have rejected a bad checksum")
        except up.IntegrityError:
            pass
        _os.unlink(p)
        return "sha256+parse+resolve+verify(reject tamper)"
    t("core.updater (verify)", _update_verify)

    # tally
    by_group = {}
    for g, m, ok, _ in results:
        by_group.setdefault(g, [0, 0])
        by_group[g][0] += 1 if ok else 0
        by_group[g][1] += 1
    passed = sum(1 for _, _, ok, _ in results if ok)
    print("\n" + "=" * 50)
    for g, (p, tot) in by_group.items():
        print(f"  {g}: {p}/{tot}")
    print(f"\n==== {passed}/{len(results)} module tests passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
