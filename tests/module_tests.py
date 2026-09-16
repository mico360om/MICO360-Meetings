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

    def _settings_migrate():
        import json, tempfile, os
        from pathlib import Path
        from mico360 import config
        # older build persisted a blank update repo -> should heal to DEFAULT_REPO
        fd, name = tempfile.mkstemp(suffix=".json"); os.close(fd)
        p = Path(name)
        try:
            p.write_text(json.dumps({"github_repo": "", "theme": "dark"}), encoding="utf-8")
            s = config.Settings(p)
            assert s.get("github_repo") == config.DEFAULT_REPO
            assert s.get("theme") == "dark"                 # other keys untouched
            assert json.loads(p.read_text(encoding="utf-8"))["github_repo"] == config.DEFAULT_REPO
            # a real repo is preserved, not overwritten
            p.write_text(json.dumps({"github_repo": "acme/app"}), encoding="utf-8")
            assert config.Settings(p).get("github_repo") == "acme/app"
        finally:
            p.unlink(missing_ok=True)
        return "blank github_repo healed; real repo kept"
    t("config.migrate", _settings_migrate)

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

    def _live_transcription():
        import soundfile as sf, numpy as np
        from mico360.core.transcription import TranscriptionEngine
        from mico360.core.recording import AudioRecorder, RecordingConfig
        from mico360.ui.workers import resample_16k, LiveTranscribeWorker
        data, sr = sf.read("samples/spoken_test.wav", dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        audio16 = resample_16k(data, sr)
        eng = TranscriptionEngine("tiny", "auto", "int8")
        assert "meeting" in eng.transcribe_array(audio16).lower()
        # recorder live tap: buffer + drain-and-clear
        rec = AudioRecorder(RecordingConfig(source="mic"))
        assert not rec._live_on
        rec.enable_live(); assert rec._live_on
        rec._live_rate = 16000
        rec._live_push(np.ones(100, "float32")); rec._live_push(np.zeros(50, "float32"))
        pulled, rate = rec.pull_live()
        assert rate == 16000 and len(pulled) == 150 and rec.pull_live()[0] is None
        # resample maths
        assert len(resample_16k(np.zeros(32000, "float32"), 32000)) == 16000
        # worker transcribes a chunk pulled from a (fake) recorder
        class _Fake:
            _served = False
            def pull_live(self):
                if self._served:
                    return None, 16000
                self._served = True; return audio16, 16000
        w = LiveTranscribeWorker(_Fake(), eng)
        got = []; w.partial.connect(lambda t: got.append(t))
        w._flush(final=True)
        assert got and "meeting" in got[0].lower()
        return "array-transcribe + live tap + resample + worker flush"
    t("core.live_transcription", _live_transcription)

    def _ollama():
        from mico360.core import ollama_client as oc
        st = oc.check_status()
        assert isinstance(st.models, list)
        assert oc.RECOMMENDED_MODELS and callable(oc.pull_model)
        # text-model filter: vision/embedding models are hidden, text models kept
        u = oc.usable_for_text
        assert u("llama3.1:latest", {"family": "llama"})
        assert u("qwen2.5:3b", {"families": ["qwen2"]})
        assert not u("llama3.2-vision:11b", {"families": ["mllama", "clip"]})
        assert not u("llava:7b", {"family": "llava"})
        assert not u("nomic-embed-text", {"family": "nomic-bert"})
        assert not u("mxbai-embed-large", None)      # name hint, no details
        assert u("mistral", None)                     # unknown details → keep
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
        assert any(m.id == mid for m in h.list("login"))        # search still matches transcript
        # low-resource: list() must NOT carry the heavy transcript; it's lazy
        row = next(m for m in h.list() if m.id == mid)
        assert row.transcript == "" and row.minutes == "# M"
        assert h.get_transcript(mid) == "login bug"             # fetched only on demand
        assert h.get(mid).transcript == "login bug"             # full get still complete
        h.delete(mid); h.close()
        (TMP / "_qa_hist.db").unlink(missing_ok=True)
        return "CRUD+search; list() omits transcript, lazy get_transcript"
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
        assert lib.get(sp.id)
        # favourite toggles + persists
        lib.set_favorite(sp.id, True); assert lib.get(sp.id).favorite is True
        lib.set_favorite(sp.id, False); assert lib.get(sp.id).favorite is False
        # duplicate makes an editable Custom copy
        dup = lib.duplicate(sp.id)
        assert dup and dup.id != sp.id and dup.builtin is False and dup.name.endswith("(copy)")
        assert dup.text == sp.text and dup.category == sp.category
        lib.delete(sp.id); lib.delete(dup.id)
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
        assert win.stack.count() == 9
        from PySide6.QtGui import QShortcut
        assert len(win.findChildren(QShortcut)) >= 5
        for i in range(win.stack.count()):
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
        assert np_.transcribe_btn.isEnabled()
        np_._set_transcribe_enabled(False)
        assert not np_.transcribe_btn.isEnabled()
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
        # progress stepper: completed steps show a check, current is active
        assert np_.stepper._badges[0].text() == "✓"
        assert np_.stepper._badges[np_.STEP_REVIEW].property("state") == "active"
        assert np_.stepper._badges[np_.STEP_MINUTES].property("state") == "todo"
        np_._back(); assert np_.wizard.currentIndex() == np_.STEP_SETUP
        np_.transcript.clear(); np_._reached = 0; np_._goto_step(0)
        # Paste-text source: three source tabs; pasted content becomes the transcript
        assert np_.source_tabs.count() == 3
        np_.paste_box.setPlainText("Pasted meeting notes: agreed to ship Friday.")
        np_._use_pasted_text()
        assert np_.wizard.currentIndex() == np_.STEP_TRANSCRIPT
        assert "ship Friday" in np_.transcript.toPlainText() and not np_.paste_box.toPlainText()
        # select_record_tab targets the Record tab by widget regardless of tab order
        np_.select_record_tab()
        assert np_.source_tabs.currentWidget() is np_._record_tab
        np_._new_meeting()                          # clears paste box + transcript
        assert not np_.paste_box.toPlainText() and not np_.transcript.toPlainText()
        np_._reached = 0; np_._goto_step(0)
        return "5 steps + validation + next/back + review + paste-text source"
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

    def _history_features():
        from mico360.core.history import Meeting
        hp = win.history_page
        orig = ctx.history.list
        data = [
            Meeting(1, "Complete one", 0, 300, style="Formal", model="llama3.1",
                    transcript="t", minutes="# M\nbody"),
            Meeting(2, "Draft one", 0, 200, transcript="only transcript", minutes=""),
            Meeting(3, "Empty one", 0, 100, transcript="", minutes=""),
        ]
        try:
            ctx.history.list = lambda q="", limit=500: list(data)
            # status classification
            assert hp._status_of(data[0]) == "Complete"
            assert hp._status_of(data[1]) == "Draft"
            assert hp._status_of(data[2]) == "Empty"
            # 5 sortable columns; newest-first default
            hp.status_filter.setCurrentText("All statuses"); hp.reload()
            assert hp.table.columnCount() == 5 and hp.table.isSortingEnabled()
            assert hp.table.rowCount() == 3 and "3 meetings" in hp.count_lbl.text()
            # status filter narrows the list
            hp.status_filter.setCurrentText("Draft"); hp.reload()
            assert hp.table.rowCount() == 1 and "filtered" in hp.count_lbl.text()
            assert hp._selected_meeting().id == 2
            # single-click preview reflects the selected meeting
            hp.status_filter.setCurrentText("All statuses"); hp.reload()
            hp.table.selectRow(0); hp._update_preview()
            assert hp.pv_title.text() and "Select a meeting" not in hp.pv_title.text()
        finally:
            hp.status_filter.setCurrentText("All statuses")
            ctx.history.list = orig; hp.reload()
        return "sortable cols + status filter + preview + status badges"
    t("ui.history_features", _history_features)

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
            combo = ap.table.cellWidget(0, ap._COL_STATUS)
            assert isinstance(combo, QComboBox) and combo.currentText() == "Pending"
            assert [combo.itemText(i) for i in range(combo.count())] == \
                ["Pending", "In Progress", "Completed", "Cancelled"]
            combo.setCurrentText("Completed"); ap._change_status(0)
            assert calls == ["Completed"], calls
        finally:
            ctx.action_items.all_items, ctx.action_items.set_status = orig_all, orig_set
            ap.reload()
        return "inline status dropdown + change"
    t("ui.action_status_dropdown", _action_status_dropdown)

    def _action_logic():
        from datetime import date, timedelta
        from mico360.core import tasks as T
        from mico360.core.tasks import ActionItem
        assert T.normalize_status("Done") == "Completed"
        assert T.normalize_status("wip") == "In Progress"
        assert T.normalize_status("") == "Pending"
        assert T.parse_deadline("2020-01-15") == date(2020, 1, 15)
        assert T.parse_deadline("15/01/2020") == date(2020, 1, 15)
        assert T.parse_deadline("TBD") is None
        past = (date.today() - timedelta(days=2)).isoformat()
        future = (date.today() + timedelta(days=5)).isoformat()
        assert T.is_overdue(ActionItem("t", "o", past, "Pending"))
        assert not T.is_overdue(ActionItem("t", "o", past, "Completed"))   # closed
        assert not T.is_overdue(ActionItem("t", "o", future, "Pending"))
        assert T.effective_status(ActionItem("t", "o", past, "In Progress")) == "Overdue"
        assert T.effective_status(ActionItem("t", "o", future, "Pending")) == "Pending"
        assert T.normalize_priority("urgent") == "High" and T.normalize_priority("") == ""
        return "normalize + deadline parse + overdue detection"
    t("core.action_logic", _action_logic)

    def _insights():
        from datetime import date, timedelta
        from mico360.core.insights import compute_insights
        from mico360.core.tasks import ActionItem
        from mico360.core.history import Meeting
        past = (date.today() - timedelta(days=3)).isoformat()
        future = (date.today() + timedelta(days=5)).isoformat()

        class _H:
            def list(self):
                import time
                now = time.time()
                return [Meeting(1, "Kickoff planning budget", now - 86400 * 2, now - 86400 * 2,
                                minutes="Budget budget planning roadmap roadmap vendor"),
                        Meeting(2, "Weekly sync", now - 86400 * 9, now - 86400 * 9,
                                minutes="roadmap vendor pricing")]
        class _A:
            def all_items(self, q=""):
                return [ActionItem("t1", "Bob", past, "Pending", 1),          # overdue
                        ActionItem("t2", "Carol", future, "In Progress", 1),
                        ActionItem("t3", "Bob", "", "Completed", 2),
                        ActionItem("t4", "Bob", "", "Cancelled", 2)]
        ins = compute_insights(_H(), _A(), weeks=4)
        assert ins.total_meetings == 2 and ins.total_items == 4
        assert ins.overdue_items == 1
        assert ins.open_items == 2                       # Pending + In Progress (Bob t1, Carol t2)
        assert ins.status_counts.get("Overdue") == 1 and ins.status_counts.get("Completed") == 1
        assert round(ins.completion_rate * 100) == 33    # 1 completed / 3 non-cancelled
        assert ins.by_owner and ins.by_owner[0][0] == "Bob" and ins.by_owner[0][1] == 1  # only open
        assert len(ins.cadence) == 4                     # continuous last 4 weeks
        assert any(w == "roadmap" for w, _ in ins.keywords)   # recurring theme mined
        assert ins.has_data
        return "status/owner/cadence/keyword aggregation"
    t("core.insights", _insights)

    def _action_editing():
        import tempfile, os
        from pathlib import Path
        from mico360.core.tasks import ActionItemStore, ActionItem

        class _H:   # minimal history stub with one meeting whose minutes have a task
            def list(self):
                class M: pass
                m = M(); m.id = 7; m.title = "Sync"; m.updated_at = 0
                m.minutes = ("## Action Items\n| Task | Responsible | Deadline | Priority | Status |\n"
                             "| - | - | - | - | - |\n| Ship v2 | Bob | 2030-01-01 | High | Pending |")
                return [m]
        fd, name = tempfile.mkstemp(suffix=".json"); os.close(fd); p = Path(name)
        p.unlink()          # start with no overrides file (empty file isn't valid JSON)
        try:
            store = ActionItemStore(_H(), status_file=p)
            it = store.all_items()[0]
            assert it.task == "Ship v2" and it.priority == "High" and it.owner == "Bob"
            # full edit persists across re-list (task text can change; key is stable)
            store.update_item(it, task="Ship v2.1", owner="Carol", deadline="2030-02-02",
                              priority="Low", status="In Progress", notes="waiting on QA")
            it2 = store.all_items()[0]
            assert (it2.task, it2.owner, it2.priority, it2.status, it2.notes) == \
                ("Ship v2.1", "Carol", "Low", "In Progress", "waiting on QA")
            # quick priority + status setters
            store.set_priority(it2, "High"); store.set_status(it2, "Completed")
            it3 = store.all_items()[0]
            assert it3.priority == "High" and it3.status == "Completed"
            # reset reverts to the minutes' original values
            store.reset_item(it3)
            it4 = store.all_items()[0]
            assert it4.task == "Ship v2" and it4.owner == "Bob" and it4.status == "Pending"
        finally:
            p.unlink(missing_ok=True)
        return "edit persists + quick set + reset (stable key)"
    t("core.action_editing", _action_editing)

    def _followup_and_ics():
        from datetime import date, timedelta
        from mico360.core import followup, calendar_ics, tasks as T
        from mico360.core.tasks import ActionItem
        past = (date.today() - timedelta(days=2)).isoformat()
        soon = (date.today() + timedelta(days=3)).isoformat()
        items = [
            ActionItem("Ship v2", "Bob", past, "Pending", 1, "Kickoff", priority="High"),
            ActionItem("Review pricing", "Carol", soon, "In Progress", 1, "Kickoff"),
            ActionItem("Old thing", "Bob", "", "Completed", 2, "Sync"),      # closed → excluded
            ActionItem("No owner task", "", soon, "Pending", 2, "Sync"),     # no owner → excluded
        ]
        # startup reminder counts
        overdue, due_soon = T.reminder_counts(items)
        assert overdue == 1 and due_soon == 2                # Bob(past) overdue; Carol+No-owner due soon
        # per-owner follow-up drafts (only open, owned items)
        drafts = followup.build_all(items)
        owners = {o for o, *_ in drafts}
        assert owners == {"Bob", "Carol"}
        bob = next(d for d in drafts if d[0] == "Bob")
        assert bob[3] == 1 and "Ship v2" in bob[2] and "OVERDUE" in bob[2]
        # .ics: an event only for dated items
        dated = calendar_ics.dated_items(items)
        assert len(dated) == 3                               # Ship v2, Review pricing, No owner task
        ics = calendar_ics.build_ics(dated)
        assert ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") == 3
        assert "SUMMARY:[Bob] Ship v2" in ics and "BEGIN:VALARM" in ics
        assert "DTSTART;VALUE=DATE:" in ics
        return "reminders + per-owner drafts + .ics events"
    t("core.followup_ics", _followup_and_ics)

    def _action_filters():
        from datetime import date, timedelta
        from mico360.core.tasks import ActionItem
        ap = win.actions_page
        orig = ctx.action_items.all_items
        past = (date.today() - timedelta(days=2)).isoformat()
        data = [
            ActionItem("t1", "Bob", past, "Pending", 1, "M1", "d"),        # overdue
            ActionItem("t2", "Carol", "", "Completed", 2, "M2", "d"),
            ActionItem("t3", "Bob", "", "Pending", 1, "M1", "d"),
        ]
        try:
            ctx.action_items.all_items = lambda q="": list(data)
            ap.reload(); assert ap.table.rowCount() == 3
            ap.person_filter.setCurrentText("Bob"); assert ap.table.rowCount() == 2
            ap.person_filter.setCurrentText("All people")
            ap.status_filter.setCurrentText("Overdue")
            assert ap.table.rowCount() == 1 and ap._items[0].task == "t1"
            ap.status_filter.setCurrentText("Completed")
            assert ap.table.rowCount() == 1 and ap._items[0].task == "t2"
            ap.status_filter.setCurrentText("All statuses")
            ap.meeting_filter.setCurrentText("M2")
            assert ap.table.rowCount() == 1 and ap._items[0].task == "t2"
            ap.meeting_filter.setCurrentText("All meetings")
            ap.deadline_filter.setCurrentText("Overdue")
            assert ap.table.rowCount() == 1 and ap._items[0].task == "t1"
            ap.deadline_filter.setCurrentText("No date"); assert ap.table.rowCount() == 2
        finally:
            for cb in (ap.person_filter, ap.status_filter, ap.deadline_filter, ap.meeting_filter):
                cb.setCurrentIndex(0)
            ctx.action_items.all_items = orig; ap.reload()
        return "person/status/deadline/meeting filters + overdue"
    t("ui.action_filters", _action_filters)

    def _responsive_tables():
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QHeaderView
        ap, hp = win.actions_page, win.history_page
        # primary text column stretches; long text elides instead of scrolling
        assert ap.table.horizontalHeader().sectionResizeMode(ap._COL_TASK) == QHeaderView.Stretch
        assert hp.table.horizontalHeader().sectionResizeMode(hp._COL_TITLE) == QHeaderView.Stretch
        assert ap.table.textElideMode() == Qt.ElideRight
        assert hp.table.textElideMode() == Qt.ElideRight
        # the fixed (non-stretch) columns stay narrow enough to fit the 1080px
        # minimum window, leaving room for the stretch column
        afix = sum(ap.table.columnWidth(c) for c in
                   (ap._COL_OWNER, ap._COL_DUE, ap._COL_PRIO, ap._COL_STATUS,
                    ap._COL_MEETING, ap._COL_DATE))
        hfix = sum(hp.table.columnWidth(c) for c in
                   (hp._COL_STATUS, hp._COL_STYLE, hp._COL_MODEL, hp._COL_UPDATED))
        assert afix < 780 and hfix < 560
        return "tables: stretch primary + elide + bounded columns"
    t("ui.responsive_tables", _responsive_tables)

    def _navigation():
        from PySide6.QtWidgets import QLabel
        # programmatic navigation (e.g. keyboard shortcuts) syncs the active state
        win._navigate(2)
        assert win.stack.currentIndex() == 2 and win.nav_group.checkedId() == 2
        win._navigate(5)
        assert win.nav_group.checkedId() == 5
        # logical sidebar grouping is present
        groups = [l.text() for l in win.findChildren(QLabel) if l.objectName() == "NavGroup"]
        assert set(groups) == {"WORKSPACE", "LIBRARY", "SYSTEM"}
        # editing a meeting from History surfaces page context, cleared on New
        from mico360.core.history import Meeting
        win.new_page.load_meeting(Meeting(3, "Ctx", 0, 0, minutes="m"))
        assert not win.new_page.context_lbl.isHidden() and "Ctx" in win.new_page.context_lbl.text()
        win.new_page._new_meeting()
        assert win.new_page.context_lbl.isHidden()
        win._navigate(0)
        return "active-state sync + groups + editing context"
    t("ui.navigation", _navigation)

    def _speaker_naming():
        np_ = win.new_page
        np_._new_meeting()
        np_.transcript.setPlainText(
            "[Speaker 1] Hello everyone.\n[Speaker 2] Hi, let's start.\n"
            "[Speaker 1] Bob will send the SOW by Friday.")
        # panel appears with the two detected speakers
        assert not np_.speaker_panel.isHidden()
        assert list(np_._speaker_edits.keys()) == ["Speaker 1", "Speaker 2"]
        np_._speaker_edits["Speaker 1"].setText("Alice")
        np_._speaker_edits["Speaker 2"].setText("Bob")
        np_.meet_attendees.clear()
        np_._apply_speaker_names()
        txt = np_.transcript.toPlainText()
        assert "[Alice]" in txt and "[Bob]" in txt and "Speaker 1" not in txt
        # attendees auto-filled; panel hides (no more Speaker N labels); roster remembered
        assert "Alice" in np_.meet_attendees.text() and "Bob" in np_.meet_attendees.text()
        assert np_.speaker_panel.isHidden()
        assert "Alice" in (ctx.settings.get("speaker_names", []) or [])
        np_._new_meeting()
        return "detect + rename + apply + attendees + roster autocomplete"
    t("ui.speaker_naming", _speaker_naming)

    def _live_ui():
        np_ = win.new_page
        rp = np_.recorder_panel
        assert hasattr(rp, "live_check") and hasattr(rp, "live_box")
        rp._live_text = ""
        rp._on_live_partial("Hello there"); rp._on_live_partial("second part")
        assert "Hello there" in rp.live_box.toPlainText() and "second part" in rp.live_box.toPlainText()
        # live transcript hands off to the editor + jumps to the Transcript step
        np_._new_meeting()
        np_._on_live_transcript("Full live transcript text")
        assert "Full live transcript text" in np_.transcript.toPlainText()
        assert np_.wizard.currentIndex() == np_.STEP_TRANSCRIPT
        np_._new_meeting()
        return "live checkbox + preview append + transcript handoff"
    t("ui.live_transcription", _live_ui)

    def _meeting_watch():
        from datetime import datetime, timedelta
        from mico360.core import meeting_watch as MW
        from mico360.ui.workers import MeetingWatchWorker
        c = MW.classify_window
        assert c("Weekly Sync | Microsoft Teams") == ("Teams", "Weekly Sync")
        assert c("Chat | Microsoft Teams") is None and c("Microsoft Teams") is None
        assert c("Meet – abc-defg-hij") == ("Google Meet", "abc-defg-hij")
        assert c("Zoom Meeting") == ("Zoom", "Zoom Meeting")
        assert c("Meetings - OneNote") is None and c("MICO360 Meetings") is None
        assert MW.detect_live_meeting(["Notepad", "Budget Review | Microsoft Teams"]) == ("Teams", "Budget Review")
        assert MW.detect_live_meeting([]) is None
        # join links (ICS-escaped commas, trailing punctuation)
        u = MW.extract_join_url("Join: https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=x\\, thanks.")
        assert u.startswith("https://teams.microsoft.com/l/meetup-join/") and u.endswith("context=x")
        assert MW.extract_join_url("see https://meet.google.com/abc-defg-hij now") == "https://meet.google.com/abc-defg-hij"
        assert MW.extract_join_url("no links here") == ""
        # calendar: multi-event ICS within horizon, next_due lead window
        now = datetime(2026, 9, 11, 10, 0)
        ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Soon\nDTSTART:20260911T100200\nDTEND:20260911T103000\n"
               "DESCRIPTION:https://meet.google.com/abc-defg-hij\nEND:VEVENT\n"
               "BEGIN:VEVENT\nSUMMARY:Later\nDTSTART:20260911T150000\nEND:VEVENT\n"
               "BEGIN:VEVENT\nSUMMARY:Yesterday\nDTSTART:20260910T100000\nEND:VEVENT\nEND:VCALENDAR")
        ups = MW.upcoming_from_ics_text(ics, now=now)
        assert [m.title for m in ups] == ["Soon", "Later"] and ups[0].join_url.endswith("abc-defg-hij")
        due = MW.next_due(ups, now=now, lead_minutes=3)
        assert due and due.title == "Soon" and MW.next_due(ups, now=now, lead_minutes=1) is None
        # worker transitions with injected titles (no thread): detect -> debounce -> ended
        seen = []
        state = {"t": ["Standup | Microsoft Teams"]}
        w = MeetingWatchWorker(titles_fn=lambda: state["t"],
                               calendar_fn=lambda: ups, calendar_seconds=0)
        w.meetingDetected.connect(lambda a, t: seen.append(("det", a, t)))
        w.meetingEnded.connect(lambda: seen.append(("end",)))
        w.meetingDue.connect(lambda t, u: seen.append(("due", t)))
        w.poll_once(now=1.0); state["t"] = []; w.poll_once(now=2.0); w.poll_once(now=3.0)
        assert ("det", "Teams", "Standup") in seen and ("end",) in seen
        assert seen.count(("end",)) == 1                    # one miss is debounced, second ends
        assert not [s for s in seen if s[0] == "due"]         # 2026-09-11 10:00 isn't "now"
        return "window classify + join links + ics/next_due + worker transitions"
    t("core.meeting_watch", _meeting_watch)

    def _auto_record_ui():
        from PySide6.QtWidgets import QMessageBox
        from mico360.core import recording as R
        panel = win.new_page.recorder_panel
        sp = win.settings_page
        assert hasattr(sp, "auto_record") and hasattr(win, "apply_auto_record")
        # a fake recorder so no device is touched
        class _Fake:
            def __init__(self, cfg):
                self.cfg = cfg; self.state = R.RECORDING; self.output_path = str(TMP / "_auto.wav")
                self.started_at = time.time(); self.resolution = ""; self.has_audio = True; self.error = ""
            def start(self): pass
            def enable_live(self): pass
            def pull_live(self): return None, 16000     # live worker runs; no audio to transcribe
            def elapsed(self): return 1.0
            def level(self): return 0.0
            def file_size(self): return 0
            def stop(self):
                self.state = R.STOPPED
                return R.RecordingResult(self.output_path, "audio", 1.0, 0, "wav", self.started_at)
            def cancel(self): self.state = R.CANCELLED
        orig_make, orig_q = R.make_recorder, QMessageBox.question
        orig_create = win.new_page._create_meeting
        created = []
        try:
            R.make_recorder = lambda cfg: _Fake(cfg)
            QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
            win.new_page._create_meeting = lambda: created.append(True)
            win.setWindowTitle("MICO360 Meetings")
            # detection prompt -> auto start (live transcript on, as in real use)
            win._on_meeting_detected("Teams", "Weekly Sync")
            assert panel.is_recording() and panel._auto_mode and panel.live_check.isChecked()
            assert win.windowTitle().startswith("● Recording")          # unmissable indicator
            assert win.new_page._auto_generate_pending
            # meeting ends -> stop -> file queued -> minutes generated with no clicks
            (TMP / "_auto.wav").write_bytes(b"RIFF")
            win._on_meeting_ended()                       # -> _stop -> deferred _finish_stop
            t0 = time.time()
            while time.time() - t0 < 0.4:
                app.processEvents()
            assert not panel.is_recording() and created == [True]
            assert not win.windowTitle().startswith("● Recording")
            # setting toggle wires the watcher on/off
            win.apply_auto_record(True); assert win._watch is not None
            win.apply_auto_record(False); assert win._watch is None
        finally:
            R.make_recorder, QMessageBox.question = orig_make, orig_q
            win.new_page._create_meeting = orig_create
            win.new_page._auto_generate_pending = False
            (TMP / "_auto.wav").unlink(missing_ok=True)
            win.new_page._media_queue.clear(); win.new_page._clear_files(); win.new_page._new_meeting()
        return "prompt -> auto start -> indicator -> auto stop -> auto minutes; watcher toggle"
    t("ui.auto_record", _auto_record_ui)

    def _table_cell_widgets():
        """Regression: QSS item padding used to shrink the status/priority dropdowns
        to a 10px strip (text invisible). Cell widgets must fill the row."""
        from mico360.core.tasks import ActionItem
        from mico360.ui import theme
        ap = win.actions_page
        win._navigate(2); app.processEvents()             # page must be shown for cell geometry
        ap._items = [ActionItem("Row fit", "Zed", "2026-01-01", "Pending", 1, "M")]
        ap._populate(); app.processEvents()
        assert ap.table.rowCount() >= 1
        for col in (ap._COL_STATUS, ap._COL_PRIO):
            w = ap.table.cellWidget(0, col)
            assert w is not None and w.height() >= 24, (col, w.geometry())
        assert ap.table.rowHeight(0) >= 30
        assert not ap._lite                               # small list keeps rich dropdowns
        # low-resource: a large list drops per-row widgets for plain coloured text
        big = [ActionItem(f"Task {i}", "Zed", "", "Pending", i, "M")
               for i in range(ap._MAX_INLINE_ROWS + 5)]
        ap._items = big; ap._populate(); app.processEvents()
        assert ap._lite
        assert ap.table.cellWidget(0, ap._COL_STATUS) is None      # no live widgets
        assert ap.table.item(0, ap._COL_STATUS).text() == "Pending"
        assert ap.table.item(0, ap._COL_PRIO) is not None
        ap._items = ap._items[:1]; ap._populate(); app.processEvents()   # back under cap
        assert not ap._lite and ap.table.cellWidget(0, ap._COL_STATUS) is not None
        win.apply_theme("dark"); assert theme.CURRENT == "dark"
        win.apply_theme(ctx.settings.get("theme", "light"))
        return "cell widgets fill rows; lite fallback over cap; theme.CURRENT tracks theme"
    t("ui.table_cell_widgets", _table_cell_widgets)

    def _maintenance():
        from mico360.core.maintenance import purge_tmp
        d = TMP / "_purge_test"; d.mkdir(exist_ok=True)
        old = d / "recording_1.wav"; old.write_bytes(b"x" * 100)
        new = d / "recording_2.wav"; new.write_bytes(b"y" * 50)
        keep = d / "meeting_notes.txt"; keep.write_bytes(b"z")     # not a temp pattern
        import os as _os
        _os.utime(old, (time.time() - 10 * 86400,) * 2)            # 10 days old
        removed, freed = purge_tmp(older_than_days=7, tmp_dir=d)
        assert removed == 1 and freed == 100
        assert not old.exists() and new.exists() and keep.exists()
        assert purge_tmp(older_than_days=0, tmp_dir=d) == (0, 0)   # disabled
        for f in d.glob("*"): f.unlink()
        d.rmdir()
        return "age-based temp purge: removes old recordings, spares recent + non-temp"
    t("core.maintenance", _maintenance)

    def _prompt_library():
        pp = win.prompts_page
        pp.search.clear(); pp.cat_filter.setCurrentText("All categories"); pp.reload()
        total = len(pp._prompts); assert total >= 20
        pp.search.setText("action")                     # search narrows the list
        assert 0 < len(pp._prompts) < total
        pp.search.clear()
        for r, idx in enumerate(pp._row_map):           # select first real prompt
            if idx >= 0:
                pp.list.setCurrentRow(r); break
        cur = pp._current(); assert cur is not None
        assert pp.pv_tag.text() in ("Built-in", "Custom")   # clear label in preview
        try:
            pp.ctx.prompts.set_favorite(cur.id, True); pp.reload()
            pp.cat_filter.setCurrentText("★ Favourites")    # favourites filter
            assert pp._prompts and all(p.favorite for p in pp._prompts)
        finally:
            pp.ctx.prompts.set_favorite(cur.id, False)
            pp.cat_filter.setCurrentText("All categories"); pp.reload()
        return "search + category + favourites filter + built-in/custom labels"
    t("ui.prompt_library", _prompt_library)

    def _generation_status():
        np_ = win.new_page
        # progress drives named stages + percentage
        np_._gen_active = True
        np_._on_progress(0.05, "Cleaning transcript…")
        assert np_._gen_stages[0].property("state") == "active"
        np_._on_progress(0.5, "Analyzing part 2 of 5…")
        assert np_._gen_stages[0].property("state") == "done"
        assert np_._gen_stages[1].property("state") == "active"
        assert np_.progress.value() == 50 and np_.progress_pct.text() == "50%"
        # AI-model failure → inline error card + Retry + Open Settings (no dialog)
        np_._on_failed("error loading model: unknown model architecture: 'mllama'")
        assert not np_.gen_error.isHidden() and not np_.gen_retry_btn.isHidden()
        assert np_.gen_err_action_btn.text() == "Open Settings"
        assert np_.stage_row.isHidden() and np_.progress_row.isHidden()
        # generic failure → error card, no contextual action
        np_._on_failed("disk full")
        assert not np_.gen_error.isHidden() and np_.gen_err_action_btn.isHidden()
        # cancellation → message, not an error card
        np_._on_failed("Generation cancelled.")
        assert "kept" in np_.status.text()
        # completion → green success state (mock the history write)
        orig = np_._save_history
        np_._save_history = lambda *a, **k: None
        try:
            np_._gen_active = True
            np_._on_generated("# Minutes\nbody")
            assert np_.status.property("state") == "ok" and np_.gen_error.isHidden()
        finally:
            np_._save_history = orig
            np_._gen_active = False; np_._goto_step(0); np_.transcript.clear()
        return "stages + inline error/retry + cancel + completion"
    t("ui.generation_status", _generation_status)

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
        from mico360.ui.dialogs import ProfileDialog, PromptDialog, PagePreview, ActionItemDialog
        from mico360.core.profiles import CompanyProfile
        from mico360.core.tasks import ActionItem
        pd = ProfileDialog(ctx.profiles, CompanyProfile(name="Z"), win)
        assert pd.preview is not None
        prd = PromptDialog("n", "t [TRANSCRIPT_HERE]", "Summary", win)
        name, text, cat = prd.values(); assert cat == "Summary"
        aid = ActionItemDialog(ActionItem("Do X", "Bob", "2030-01-01", "Pending",
                                          priority="High", notes="hi"), win)
        vals = aid.values()
        assert vals["task"] == "Do X" and vals["priority"] == "High" and vals["notes"] == "hi"
        return "Profile+Prompt+Preview+ActionItem"
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
    _rc = main()
    # Flush, then os._exit to skip Python/Qt finalization — PySide6 on
    # Python 3.14 intermittently crashes during interpreter teardown
    # (0xC0000409) AFTER tests pass, which would mask a clean result.
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(_rc)
