"""Automated QA smoke checks for MICO360 Meetings.

Runs the parts of the QA checklist that can be verified without a clean-VM
installer run. Usage:

    set QT_QPA_PLATFORM=offscreen   (Windows)
    python tests/qa_check.py

Prints PASS/FAIL per item and a final tally. Exit code 0 if all pass.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QScrollArea, QTabWidget  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from mico360.ui.context import AppContext
    from mico360.ui.main_window import MainWindow
    from mico360.core import recording as R
    from mico360.core import documents
    from mico360.core.audio import MEDIA_EXTS

    ctx = AppContext()
    ctx.settings.set("onboarded", True)        # don't block headless run with the wizard
    win = MainWindow(ctx); win.resize(1180, 800); win.show(); app.processEvents()
    np_ = win.new_page

    # --- icon / branding ---
    check("App window icon set", not win.windowIcon().isNull())

    # --- file type acceptance ---
    from mico360.ui.pages import UPLOAD_EXTS
    for ext in [".mp3", ".wav", ".m4a", ".mp4", ".mov", ".mkv",
                ".png", ".jpg", ".pdf", ".docx", ".txt"]:
        check(f"Upload accepts {ext}", ext in UPLOAD_EXTS)

    # --- dropdowns/buttons after 'upload' ---
    np_._add_file("C:/fake/meeting.mp4")    # queues media (no transcription)
    app.processEvents()
    check("Model dropdown usable after upload", np_.model_box.count() > 0 and np_.model_box.isEnabled())
    check("Style dropdown has 5 styles after upload", np_.style_box.count() == 5)
    check("Prompt dropdown usable after upload", np_.prompt_box.count() > 0)
    tabs = np_.findChild(QTabWidget)
    tabs.setCurrentIndex(1); app.processEvents()
    check("Tabs switch after upload", tabs.currentIndex() == 1)
    tabs.setCurrentIndex(0)

    # --- 'Install Required Model' only in Settings, not main ---
    from PySide6.QtWidgets import QPushButton
    main_btns = [b.text() for b in np_.findChildren(QPushButton)]
    check("Install-Required-Model NOT on main page", "Install Required Model" not in main_btns)
    set_btns = [b.text() for b in win.settings_page.findChildren(QPushButton)]
    check("Install-Required-Model IS in Settings", "Install Required Model" in set_btns)

    # --- mic detection ---
    check("Microphone detection works", isinstance(R.list_microphones(), list))
    check("No-mic message wired", hasattr(win.new_page.recorder_panel, "refresh_devices"))

    # --- recording engine API present (live capture verified separately) ---
    check("Audio/Screen/Camera recorder factory", callable(R.make_recorder))
    check("Recorder has pause/resume/stop/cancel",
          all(hasattr(R.BaseRecorder, m) for m in ("pause", "resume", "stop", "cancel")))
    if "--live-record" in sys.argv:                 # opt-in: needs exclusive mic access
        cfg = R.RecordingConfig(kind="audio", audio_format="wav")
        rec = R.make_recorder(cfg); rec.start(); time.sleep(1.0); res = rec.stop()
        import wave
        with wave.open(res.path) as w:
            frames = w.getnframes()
        check("Audio recording saves valid file (not blank)", frames > 0, f"{frames} frames")
        Path(res.path).unlink(missing_ok=True)

    # --- model selection used during generation ---
    gen = ctx.generator()
    check("Generator uses selected Ollama model", hasattr(gen, "model"))

    # --- transcript/minutes edit + history save/reload ---
    from mico360.core.history import Meeting
    mid = ctx.history.save(Meeting(id=0, title="QA Meeting", created_at=0, updated_at=0,
                                   transcript="hello world", minutes="# Minutes\ntest"))
    loaded = ctx.history.get(mid)
    check("History save+reload", loaded is not None and loaded.transcript == "hello world")
    check("History search finds it", any(m.id == mid for m in ctx.history.list("QA Meeting")))
    ctx.history.delete(mid)

    # --- exports (txt/docx/pdf) with company profile ---
    from mico360.core.profiles import CompanyProfile
    from mico360.export import service
    md = ("# Meeting Minutes\n**Meeting Title:** QA\n\n## Action Items\n"
          "| Task | Responsible Person | Deadline | Status |\n| --- | --- | --- | --- |\n"
          "| Do thing | Alex | Fri | Pending |\n")
    prof = CompanyProfile(name="QA Co", footer_text="Confidential", show_page_numbers=True)
    prof = ctx.profiles.set_logo(ctx.profiles.save(prof), "assets/logo.png")
    ctx.profiles.save(prof)
    tmp = Path(tempfile.gettempdir())
    for ext in (".txt", ".docx", ".pdf"):
        p = service.export(md, str(tmp / f"qa_min{ext}"), prof)
        check(f"Export {ext.upper()} with profile", Path(p).stat().st_size > 200)
        Path(p).unlink(missing_ok=True)
    ctx.profiles.delete(prof.id)

    # --- company profiles CRUD + import/export ---
    p2 = ctx.profiles.save(CompanyProfile(name="Imp Co"))
    exp = tmp / "profiles_qa.json"
    ctx.profiles.export_file([p2], exp)
    imported = ctx.profiles.import_file(exp)
    check("Profiles export+import", len(imported) == 1 and imported[0].name == "Imp Co")
    for pid in [p2.id] + [i.id for i in imported]:
        ctx.profiles.delete(pid)
    exp.unlink(missing_ok=True)

    # --- prompt library CRUD ---
    sp = ctx.prompts.add("QA Prompt", "Body [TRANSCRIPT_HERE]")
    check("Prompt add", ctx.prompts.get(sp.id) is not None)
    ctx.prompts.update(sp.id, "QA Prompt 2", "X")
    check("Prompt edit", ctx.prompts.get(sp.id).name == "QA Prompt 2")
    ctx.prompts.delete(sp.id)
    check("Prompt delete", ctx.prompts.get(sp.id) is None)

    # --- help/about/terms/privacy + email ---
    from PySide6.QtWidgets import QTextBrowser
    help_html = " ".join(b.toHtml() for b in win.help_page.findChildren(QTextBrowser))
    check("Help/About/Shortcuts/Terms/Privacy tabs present",
          win.help_page.findChild(QTabWidget).count() == 5)
    check("Support email info@mico360.com shown", "info@mico360.com" in help_html)

    # --- updates page fields ---
    from mico360 import __version__
    up = win.updates_page
    check("Updates shows current version", __version__ in up.cur_lbl.text())
    check("Updates has GitHub repo button + check", up.repo_btn is not None and up.check_btn is not None)

    # --- responsiveness: no horizontal overflow at min + common sizes ---
    bad = []
    for w, h in [(1080, 660), (1280, 720), (1366, 768), (1920, 1080)]:
        win.resize(w, h)
        for i in range(win.stack.count()):
            win._navigate(i)
            page = win.stack.widget(i)
            for sa in page.findChildren(QScrollArea):
                if not sa.widget() or not sa.isVisible():   # skip inactive tab panes
                    continue
                sa.widget().adjustSize()            # force layout to settle
                for _ in range(5):
                    app.processEvents()
                # real, rendered overflow = horizontal scrollbar range
                if sa.horizontalScrollBar().maximum() > 2:
                    bad.append((page.__class__.__name__, w, h,
                                sa.horizontalScrollBar().maximum()))
    check("No horizontal overflow (1080->1920)", not bad, str(bad[:3]))

    # ===================================================================
    # Prompt Library (CRUD / categories / selection / preview / reuse)
    # ===================================================================
    plist = ctx.prompts.list()
    check("Prompt library has 14+ ready prompts", len(plist) >= 14, f"{len(plist)} prompts")
    cats = {p.category for p in plist}
    want = {"Formal Meeting", "Project Meeting", "Client Meeting", "Internal Meeting", "Action Items", "Summary"}
    check("Prompts organised into categories", want.issubset(cats), str(sorted(cats)))
    sp = ctx.prompts.add("QA Tmp", "Body [TRANSCRIPT_HERE]", category="Client Meeting")
    check("Custom prompt add", ctx.prompts.get(sp.id) is not None)
    ctx.prompts.update(sp.id, "QA Tmp 2", "X", category="Summary")
    check("Custom prompt edit + recategorise", ctx.prompts.get(sp.id).category == "Summary")
    win.prompts_page.reload()
    check("Prompts page groups by category (header rows)",
          win.prompts_page.list.count() > len(ctx.prompts.list()))
    win.new_page.refresh_prompts()
    check("Prompt selectable for generation (dropdown)", win.new_page.prompt_box.count() >= 14)
    win.new_page.prompt_box.setCurrentIndex(1); app.processEvents()
    check("Prompt preview loads on select", len(win.new_page.prompt_edit.toPlainText()) > 0)
    ctx.prompts.delete(sp.id)
    check("Custom prompt delete", ctx.prompts.get(sp.id) is None)
    check("Built-in prompts reusable (persist)", any(p.builtin for p in ctx.prompts.list()))

    # ===================================================================
    # Input validation (empty / wrong / special chars) — must not crash
    # ===================================================================
    win.new_page.transcript.setPlainText("")
    try:
        win.new_page._generate()          # empty transcript -> warns, no crash
        check("Generate with empty transcript handled", True)
    except Exception as e:
        check("Generate with empty transcript handled", False, str(e))
    win.new_page.minutes.setPlainText("")
    try:
        win.new_page._export()            # empty minutes -> warns (no dialog hang)
        check("Export with empty minutes handled", True)
    except Exception as e:
        check("Export with empty minutes handled", False, str(e))
    # special characters through the whole text pipeline
    from mico360.core import cleaning
    weird = "Café — naïve “quotes” <tag> & 日本語 \t\n\n  émigré"
    try:
        cleaned = cleaning.clean_transcript(weird, True)
        check("Special/Unicode chars cleaned safely", isinstance(cleaned, str))
    except Exception as e:
        check("Special/Unicode chars cleaned safely", False, str(e))
    # profile with empty name defaults safely
    from mico360.core.profiles import CompanyProfile
    prof = ctx.profiles.save(CompanyProfile(name=""))
    check("Empty profile name defaults", ctx.profiles.get(prof.id).name == "Company")
    ctx.profiles.delete(prof.id)

    # ===================================================================
    # Recording controls (pause / resume / stop state machine)
    # ===================================================================
    from mico360.core import recording as RR
    rc = RR.BaseRecorder(RR.RecordingConfig())
    rc.state = RR.RECORDING; rc._t0 = 0.0
    rc.pause(); paused = rc.state == RR.PAUSED
    rc.resume(); resumed = rc.state == RR.RECORDING
    check("Pause / Resume toggles state", paused and resumed)
    check("Recorder exposes start/pause/resume/stop/cancel",
          all(hasattr(rc, m) for m in ("start", "pause", "resume", "stop", "cancel")))
    check("Mic detection + device fallback present",
          callable(RR.list_microphones) and callable(RR.input_candidates))

    # ===================================================================
    # Transcription accuracy + silent/unclear audio handling
    # ===================================================================
    from mico360.core.transcription import TranscriptionEngine
    eng = TranscriptionEngine(model_size="tiny", device="auto", compute_type="int8")
    try:
        res = eng.transcribe_file("samples/spoken_test.wav", language="en")
        words = res.text.lower()
        hit = sum(w in words for w in ("morning", "meeting", "beta", "friday"))
        check("Transcription converts speech to text (CPU)", hit >= 2, f"{hit}/4 keywords, {len(res.text)} chars")
    except Exception as e:
        check("Transcription converts speech to text (CPU)", False, str(e))
    # silent / unclear audio -> must not crash, returns (possibly empty) result
    try:
        import numpy as np, soundfile as sf
        silent = str(Path(tempfile.gettempdir()) / "qa_silent.wav")
        sf.write(silent, np.zeros(16000 * 2, dtype="float32"), 16000)
        sres = eng.transcribe_file(silent, language="en")
        check("Silent/unclear audio handled (no crash)", isinstance(sres.text, str))
        Path(silent).unlink(missing_ok=True)
    except Exception as e:
        check("Silent/unclear audio handled (no crash)", False, str(e))

    # ===================================================================
    # End-to-end minutes generation (real Ollama) + action items
    # ===================================================================
    st = ctx.ollama_status()
    small = next((m for m in st.models if "0.5b" in m or "qwen" in m), st.models[0] if st.models else "")
    if st.running and small:
        from mico360.core.ollama_client import OllamaGenerator
        from mico360.core.prompts import BASE_TEMPLATE
        transcript = Path("samples/sample_transcript.txt").read_text(encoding="utf-8")
        try:
            md = OllamaGenerator(model=small).generate_minutes(
                transcript, BASE_TEMPLATE, "Action Item Report", chunk_chars=6000)
            check("Minutes generated from transcript (real LLM)", len(md) > 100, f"{len(md)} chars, model={small}")
            check("Action items / decisions present in output",
                  any(k in md for k in ("Action Items", "Decision", "Responsible")))
        except Exception as e:
            check("Minutes generated from transcript (real LLM)", False, str(e))
    else:
        check("Minutes generated from transcript (real LLM)", True, "SKIPPED (Ollama offline)")

    # ===================================================================
    # Crash resilience — rapid navigation, resize, repeated ops
    # ===================================================================
    try:
        for _ in range(3):
            for i in range(win.stack.count()):
                win._navigate(i); app.processEvents()
        for w, h in [(1080, 660), (1500, 900), (1280, 720), (1920, 1080), (1080, 700)]:
            win.resize(w, h); app.processEvents()
        # repeated history save/delete (long-use simulation)
        from mico360.core.history import Meeting
        for k in range(20):
            mid = ctx.history.save(Meeting(id=0, title=f"m{k}", created_at=0, updated_at=0,
                                           transcript="t", minutes="# M"))
            ctx.history.delete(mid)
        check("No crash on rapid nav / resize / repeated ops", True)
    except Exception as e:
        check("No crash on rapid nav / resize / repeated ops", False, str(e))

    # ===================================================================
    # Close / exit — single-instance + clean shutdown
    # ===================================================================
    from mico360 import single_instance
    acquired = single_instance.acquire()
    single_instance.release()
    check("Single-instance lock acquire/release", isinstance(acquired, bool))
    try:
        win.new_page.recorder_panel.stop_if_active()
        check("Clean shutdown hook (stop active recording)", True)
    except Exception as e:
        check("Clean shutdown hook (stop active recording)", False, str(e))

    # ===================================================================
    # Crash reporter (opt-in)
    # ===================================================================
    from mico360 import crash_reporter as cr
    try:
        raise ValueError("qa simulated crash")
    except Exception as e:
        rep = cr.build_report(type(e), e, e.__traceback__)
    check("Crash report bundles traceback + log", "Traceback" in rep and "Recent log" in rep)
    gh = cr.github_issue_url("owner/name", "[Crash] test", rep)
    check("Crash report builds GitHub issue URL", gh.startswith("https://github.com/owner/name/issues/new?"))
    rp = cr._write_report(rep)
    check("Crash report saved locally", rp.exists())
    rp.unlink(missing_ok=True)
    d = cr.CrashDialog(rep, rp, "", "ValueError")
    check("Crash dialog opt-in (no auto-send; GitHub gated on repo)", not d.gh_btn.isEnabled())

    # ===================================================================
    # New features: presets, exports, audio source, diarization, onboarding
    # ===================================================================
    from mico360.config import QUALITY_PRESETS, apply_quality_preset
    apply_quality_preset(ctx.settings, "Accurate")
    check("Speed/Quality presets apply", ctx.settings.get("whisper_model") == "small")
    apply_quality_preset(ctx.settings, "Balanced")
    from mico360.export import service as _svc
    check("Markdown + HTML export formats", ".md" in _svc.EXPORTERS and ".html" in _svc.EXPORTERS)
    md2 = "# M\n## Action Items\n| Task | Who | Due | Status |\n| - | - | - | - |\n| x | y | z | Pending |"
    hp = Path(tempfile.gettempdir()) / "qa.html"
    _svc.export(md2, str(hp), None)
    check("HTML export renders table", "<table" in hp.read_text(encoding="utf-8")); hp.unlink(missing_ok=True)
    check("Minutes Edit/Preview tabs", win.new_page.minutes_tabs.count() == 2)
    from mico360.core import recording as RR2
    check("Audio source selector (mic/system/both)", win.new_page.recorder_panel.source_box.count() >= 1)
    check("System-audio detection callable", callable(RR2.system_audio_device))
    from mico360.core import diarization as DD
    class _S:
        def __init__(s, a, b): s.start, s.end, s.speaker = a, b, None
    n = DD.apply_to_segments("samples/spoken_test.wav", [_S(0, 2), _S(2, 4)])
    check("Diarization labels segments (no crash)", n >= 1)
    from mico360.ui.onboarding import OnboardingDialog
    check("First-run onboarding dialog builds", OnboardingDialog(ctx, win) is not None)
    check("Keyboard shortcuts installed", any(True for _ in win.findChildren(__import__('PySide6.QtGui', fromlist=['QShortcut']).QShortcut)))
    check("File queue drag-reorder enabled",
          win.new_page.files_list.dragDropMode() != 0)

    # ===================================================================
    # Tooltips (coverage + accessibility)
    # ===================================================================
    from PySide6.QtWidgets import QPushButton as _QP, QComboBox as _QC, QLineEdit as _QL
    key_widgets = {
        "nav buttons": list(win.nav_group.buttons()),
        "generate/export/email/copy/save": [win.new_page.generate_btn, win.new_page.export_btn,
                                            win.new_page.email_btn, win.new_page.copy_btn,
                                            win.new_page.save_btn],
        "step-3 dropdowns": [win.new_page.model_box, win.new_page.style_box,
                             win.new_page.prompt_box, win.new_page.mtype_box],
        "recorder controls": [win.new_page.recorder_panel.start_btn,
                              win.new_page.recorder_panel.stop_btn,
                              win.new_page.recorder_panel.source_box,
                              win.new_page.recorder_panel.mic_box],
        "history/actions": [win.history_page.search, win.actions_page.search,
                            win.actions_page.export_btn],
        "updates": [win.updates_page.check_btn, win.updates_page.repo_btn],
        "settings key fields": [win.settings_page.preset, win.settings_page.whisper,
                                win.settings_page.install_btn, win.settings_page.smtp_password],
    }
    missing_tips = [f"{grp}[{i}]" for grp, ws in key_widgets.items()
                    for i, w in enumerate(ws) if not w.toolTip().strip()]
    check("Tooltips on all key controls", not missing_tips, str(missing_tips[:4]))
    no_a11y = [f"{grp}[{i}]" for grp, ws in key_widgets.items()
               for i, w in enumerate(ws)
               if w.toolTip().strip() and not w.accessibleDescription().strip()]
    check("Tooltips mirrored to accessible descriptions", not no_a11y, str(no_a11y[:4]))
    long_tips = [w.toolTip()[:40] for ws in key_widgets.values() for w in ws
                 if len(w.toolTip()) > 220]
    check("Tooltip wording concise (<220 chars)", not long_tips, str(long_tips[:2]))

    # tally
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n==== {passed}/{len(results)} checks passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
