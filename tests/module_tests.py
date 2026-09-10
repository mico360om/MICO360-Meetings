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

    def _reassurance():
        from mico360.core import transcription as T
        assert isinstance(T.model_cached("base"), bool)
        assert not T.model_cached("definitely-not-a-model-xyz")
        assert "MB" in T.APPROX_SIZE["tiny"]
        # the generation pipeline surfaces a cold-load reassurance in its first message
        from mico360.core.generation import run_minutes_pipeline
        msgs: list[str] = []
        run_minutes_pipeline(lambda p, c: "ok", "m", "a short meeting transcript",
                             "[TRANSCRIPT_HERE]", "Formal Minutes",
                             progress=lambda f, s: msgs.append(s))
        assert any("can take up to a minute" in m for m in msgs), msgs
        return "whisper first-run detection + cold-load messaging"
    t("core.reassurance", _reassurance)

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

    def _readiness_banner():
        from types import SimpleNamespace as NS
        np_ = win.new_page
        orig_status, orig_prov = ctx.ai_status, ctx.provider
        try:
            ctx.provider = lambda: "local"
            ctx.ai_status = lambda *a, **k: NS(running=True, models=["m"], error="")
            np_.refresh_readiness(); assert np_.ready_banner.isHidden(), "ready should hide"
            # no model installed -> banner with an Install action
            ctx.ai_status = lambda *a, **k: NS(running=True, models=[], error="")
            np_.refresh_readiness()
            assert (not np_.ready_banner.isHidden()) and np_.banner_btn1.text() == "Install a model"
            # dismissing keeps the same problem hidden
            np_._dismiss_banner(); np_.refresh_readiness()
            assert np_.ready_banner.isHidden(), "dismiss should suppress same problem"
            # a different problem (cloud) still shows, with a Switch-to-Local action
            ctx.provider = lambda: "cloud"
            ctx.ai_status = lambda *a, **k: NS(running=False, models=[], error="no key")
            np_.refresh_readiness()
            assert (not np_.ready_banner.isHidden()) and np_.banner_btn1.text() == "Switch to Local"
        finally:
            ctx.ai_status, ctx.provider = orig_status, orig_prov
            np_.refresh_readiness()
        return "banner shows/hides + dismiss + per-mode actions"
    t("ui.readiness_banner", _readiness_banner)

    def _one_click():
        np_ = win.new_page
        np_._set_transcribe_enabled(True)
        assert np_.tg_btn.isEnabled() and np_.transcribe_btn.isEnabled()
        np_._set_transcribe_enabled(False)
        assert not np_.tg_btn.isEnabled() and not np_.transcribe_btn.isEnabled()
        called = {}
        orig_start, orig_gen = np_._start_transcription, np_._generate
        np_._start_transcription = lambda: called.setdefault("start", True)
        np_._generate = lambda: called.setdefault("gen", True)
        try:
            # media queued -> set the chain flag and transcribe (generation chains later)
            np_._media_queue = ["a.wav"]; np_.transcript.clear(); np_._auto_generate = False
            np_._transcribe_and_generate()
            assert np_._auto_generate and called.get("start") and "gen" not in called
            # no media but a transcript present -> generate directly, no transcription
            called.clear(); np_._media_queue = []
            np_.transcript.setPlainText("some transcript"); np_._auto_generate = False
            np_._transcribe_and_generate()
            assert called.get("gen") and "start" not in called and not np_._auto_generate
        finally:
            np_._start_transcription, np_._generate = orig_start, orig_gen
            np_._media_queue = []; np_.transcript.clear(); np_._auto_generate = False
        return "one-click chain flag + transcript fallback"
    t("ui.one_click_generate", _one_click)

    def _wizard():
        np_ = win.new_page
        assert np_.wizard.count() == 5, "5 wizard steps"
        np_._media_queue = []; np_.transcript.clear(); np_._reached = 0; np_._goto_step(0)
        # can't leave Source with no source; Back hidden on the first step
        assert not np_._validate_step(np_.STEP_SOURCE)[0]
        assert np_.back_btn.isHidden() and not np_.next_btn.isHidden()
        # add a transcript -> advance through the steps
        np_.transcript.setPlainText("hello meeting notes")
        assert np_._validate_step(np_.STEP_SOURCE)[0]
        np_._next(); assert np_.wizard.currentIndex() == np_.STEP_TRANSCRIPT
        np_._next(); assert np_.wizard.currentIndex() == np_.STEP_SETUP
        np_._next(); assert np_.wizard.currentIndex() == np_.STEP_REVIEW
        # Review populated; footer swaps Next -> Create
        assert "words" in np_._review_vals["Transcript"].text()
        assert np_.next_btn.isHidden() and not np_.generate_btn.isHidden()
        np_._back(); assert np_.wizard.currentIndex() == np_.STEP_SETUP
        np_.transcript.clear(); np_._reached = 0; np_._goto_step(0)
        return "5 steps + validation + next/back + review"
    t("ui.wizard", _wizard)

    def _preview_default():
        np_ = win.new_page
        np_.minutes_tabs.setCurrentIndex(0)                 # start on raw Edit
        np_.minutes.setPlainText("# Minutes\n\n- point one")
        np_._show_minutes_preview()
        assert np_.minutes_tabs.tabText(np_.minutes_tabs.currentIndex()) == "Preview"
        assert np_.minutes_preview.toHtml().strip()
        np_.minutes.clear(); np_.minutes_tabs.setCurrentIndex(0)
        return "minutes default to formatted Preview"
    t("ui.minutes_preview_default", _preview_default)

    def _empty_states():
        from mico360.core.history import Meeting
        hp, ap = win.history_page, win.actions_page
        orig_h, orig_a = ctx.history.list, ctx.action_items.all_items
        try:
            ctx.history.list = lambda *a, **k: []
            ctx.action_items.all_items = lambda *a, **k: []
            hp.reload(); ap.reload()
            assert not hp.empty.isHidden() and hp.table.isHidden(), "History empty shown"
            assert not ap.empty.isHidden() and ap.table.isHidden(), "Action Items empty shown"
            # with data -> table shown, empty hidden
            ctx.history.list = lambda *a, **k: [Meeting(1, "X", 0, 0, minutes="m")]
            hp.reload()
            assert hp.empty.isHidden() and not hp.table.isHidden(), "History table restored"
        finally:
            ctx.history.list, ctx.action_items.all_items = orig_h, orig_a
            hp.reload(); ap.reload()
        return "History + Action Items empty/populated toggle"
    t("ui.empty_states", _empty_states)

    def _save_indicator():
        np_ = win.new_page
        np_.transcript.clear(); np_.minutes.clear()
        np_._autosave_sig = ""; np_._last_saved_at = None; np_._current_id = None
        np_.minutes.setPlainText("# Minutes\nbody")
        np_._update_save_indicator(); assert "Unsaved" in np_.save_status.text()
        np_._save_history(silent=True); assert "Saved" in np_.save_status.text()
        np_.minutes.setPlainText("# Minutes\nbody 2"); assert "Unsaved" in np_.save_status.text()
        if np_._current_id:
            ctx.history.delete(np_._current_id)
        np_.transcript.clear(); np_.minutes.clear()
        np_._current_id = None; np_._autosave_sig = ""; np_._last_saved_at = None
        np_._update_save_indicator()
        return "unsaved → saved → unsaved"
    t("ui.save_indicator", _save_indicator)

    def _action_status_dropdown():
        from PySide6.QtWidgets import QComboBox
        from mico360.core.tasks import ActionItem
        ap = win.actions_page
        orig_all, orig_set = ctx.action_items.all_items, ctx.action_items.set_status
        calls = []
        try:
            ctx.action_items.all_items = lambda *a, **k: [
                ActionItem("T", "O", "D", "Pending", 1, "M", "2026-01-01")]
            ctx.action_items.set_status = lambda item, status: calls.append(status)
            ap.reload()
            combo = ap.table.cellWidget(0, 3)
            assert isinstance(combo, QComboBox) and combo.currentText() == "Pending"
            assert [combo.itemText(i) for i in range(combo.count())] == \
                ["Pending", "In Progress", "Done", "Cancelled"]
            combo.setCurrentText("Done"); ap._change_status(0)
            assert calls == ["Done"], calls
        finally:
            ctx.action_items.all_items, ctx.action_items.set_status = orig_all, orig_set
            ap.reload()
        return "inline status dropdown + change"
    t("ui.action_status_dropdown", _action_status_dropdown)

    def _editable_title():
        np_ = win.new_page
        np_.transcript.clear(); np_.minutes.clear(); np_.meeting_title.clear()
        np_._current_id = None; np_._autosave_sig = ""; np_._last_saved_at = None
        np_.minutes.setPlainText("# M\nbody"); np_.meeting_title.setText("Q3 Budget Review")
        mid = np_._save_history(silent=True)
        assert ctx.history.get(mid).title == "Q3 Budget Review", "explicit title saved"
        np_.meeting_title.setText("Q3 Budget Review — final")   # rename → dirty → autosave
        assert np_._is_dirty()
        np_._autosave()
        assert ctx.history.get(mid).title == "Q3 Budget Review — final", "rename persisted"
        np_.meeting_title.clear(); np_.load_meeting(ctx.history.get(mid))
        assert np_.meeting_title.text() == "Q3 Budget Review — final", "title restored on load"
        ctx.history.delete(mid)
        np_.transcript.clear(); np_.minutes.clear(); np_.meeting_title.clear()
        np_._current_id = None; np_._autosave_sig = ""; np_._loaded_from_history = False
        return "explicit title saved + renamed + restored"
    t("ui.editable_title", _editable_title)

    def _settings_tabs():
        sp = win.settings_page
        assert [sp._tabs.tabText(i) for i in range(sp._tabs.count())] == \
            ["AI", "Transcription", "Email", "Updates", "Data"]
        for attr in ("theme", "provider_box", "host", "model", "install_btn", "preset",
                     "whisper", "compute", "device", "lang", "fillers", "diarize", "chunk",
                     "repo", "auto_check", "crash_reporter", "smtp_host", "smtp_port",
                     "email_from", "smtp_user", "smtp_password"):
            assert hasattr(sp, attr), attr
        sp.focus_install(); assert sp._tabs.currentIndex() == sp._ai_tab_index
        sp._save()                                   # save still works across tabs
        return "5 tabs + every field preserved + focus_install + save"
    t("ui.settings_tabs", _settings_tabs)

    def _text_scale():
        import re
        from mico360.ui import theme
        base = float(re.search(r"font-size: ([\d.]+)pt", theme.build_qss("dark", 1.0)).group(1))
        big = float(re.search(r"font-size: ([\d.]+)pt", theme.build_qss("dark", 1.3)).group(1))
        assert abs(big - base * 1.3) < 0.01, (base, big)
        sp = win.settings_page
        assert sp.scale_box.count() == 4
        sp.scale_box.setCurrentIndex(3); sp._scale_changed()          # Larger
        assert abs(float(ctx.settings.get("ui_scale")) - 1.3) < 1e-6
        sp.scale_box.setCurrentIndex(1); sp._scale_changed()          # back to Default
        assert abs(float(ctx.settings.get("ui_scale")) - 1.0) < 1e-6
        return "pt scaling + Settings control"
    t("ui.text_scale", _text_scale)

    def _git_update():
        from mico360.core import git_update as g
        from mico360.ui.workers import GitUpdateWorker
        assert isinstance(g.is_git_checkout(), bool)
        root = g.repo_root()
        if root is not None:
            assert (root / ".git").exists()
            c = g.current_commit()
            assert c == "" or all(ch in "0123456789abcdef" for ch in c)
        assert GitUpdateWorker("pull").action == "pull"
        up = win.updates_page
        if hasattr(up, "git_update_btn"):        # present only on a source checkout
            up._on_git_check({"ok": True, "behind": 2, "branch": "main"})
            assert not up.git_update_btn.isHidden()
            up._on_git_check({"ok": True, "behind": 0})
            assert up.git_update_btn.isHidden()
            up._on_git_check({"ok": False, "error": "boom"})
            assert "boom" in up.git_status.text()
        return "git detect + commit + worker + check logic"
    t("core.git_update", _git_update)

    def _pages_extra():
        from PySide6.QtWidgets import QTabWidget, QTextBrowser
        assert win.help_page.findChild(QTabWidget).count() == 5     # incl. Shortcuts
        html = " ".join(b.toHtml() for b in win.help_page.findChildren(QTextBrowser))
        assert "info@mico360.com" in html
        assert "Keyboard shortcuts" in html and "Ctrl+G" in html
        win.updates_page.check  # callable exists
        return "Help(5 tabs incl. Shortcuts)+Updates"
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

    def _cloud():
        import json as _j
        import urllib.request as _u
        from mico360.core import cloud_client as cc, prompts as _p

        class _Resp:
            def __init__(self, payload):
                self._b = _j.dumps(payload).encode("utf-8")

            def read(self):
                return self._b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            url = req.full_url
            if url.endswith("/models"):
                return _Resp({"object": "list",
                              "data": [{"id": "llama3.1:latest"}, {"id": "qwen2.5:0.5b"}]})
            if url.endswith("/chat/completions"):
                return _Resp({"choices": [{"message": {"role": "assistant", "content": "Paris."}}]})
            raise AssertionError("unexpected url " + url)

        orig = _u.urlopen
        _u.urlopen = fake_urlopen
        try:
            st = cc.check_cloud_status(key="mico_test")
            assert st.running and st.models == ["llama3.1:latest", "qwen2.5:0.5b"], st
            st2 = cc.check_cloud_status(key="")               # no key -> clear, not a crash
            assert not st2.running and "key" in st2.error.lower(), st2
            g = cc.CloudGenerator(key="mico_test", model="qwen2.5:0.5b")
            assert g._chat("Say VERIFY", None) == "Paris."
            out = g.generate_minutes("A short meeting transcript.", _p.BASE_TEMPLATE, "Formal Minutes")
            assert isinstance(out, str) and out
        finally:
            _u.urlopen = orig
        return "status+chat+pipeline (mocked, no network)"
    t("core.cloud_client", _cloud)

    def _providers():
        from mico360.ui.context import AppContext
        from mico360.config import AI_PROVIDERS
        from mico360.core.ollama_client import OllamaGenerator
        from mico360.core.cloud_client import CloudGenerator
        assert set(AI_PROVIDERS) == {"local", "cloud"}
        c = AppContext()
        c.settings._data["ai_provider"] = "local"
        assert isinstance(c.generator(), OllamaGenerator)
        c.settings._data["ai_provider"] = "cloud"
        assert isinstance(c.generator(), CloudGenerator)
        return "provider-aware generator selection"
    t("ui.context (providers)", _providers)

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
