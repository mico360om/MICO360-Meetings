"""Regression tests for the UI-page and Settings fixes in docs/BUG_REPORT.md.

    python tests/fixes_ui.py

  M2  re-saving an opened meeting keeps its model/style/profile/source
  M3  the chosen model and prompt survive page changes / list refreshes
  M4  several dropped / browsed files are all added
  M8  dark-theme tables: readable alternate rows, visible overdue rows
  M9  Language is a list of codes; "AR", "ar-SA", "Arabic" are normalised
  M10 Refresh / Install use the typed Ollama host; Save clears the status cache
  M11 unsaved Settings → Save / Discard / Cancel on leaving; Ctrl+S saves settings
  L1  Arabic lines in the Transcript/Minutes editors are right-aligned
  L2  e-mail subject uses the edited title; attachments are built off the UI thread
  L3  clear + Undo doesn't create a duplicate History record
  L5  editing Whisper fields switches the preset (to Custom)
  L6  the SMTP password is stored exactly as typed
  L7  a pasted GitHub URL is saved as owner/name; junk is rejected
  H3  test e-mail / follow-up e-mail / model install / update download never drop
      a running QThread; model install and update download can be cancelled

Runs against a THROWAWAY data folder (LOCALAPPDATA is redirected before the app
is imported). Workers, Ollama status, dialogs and SMTP are faked: no network,
Ollama, Whisper or mail server is needed.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

# -- isolate BEFORE importing the app ----------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="mico360_ui_fixes_"))
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

from types import SimpleNamespace as NS                  # noqa: E402

from PySide6.QtCore import QMimeData, QPointF, Qt, QThread, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QDropEvent, QShortcut, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360 import config                                # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"
config.ensure_dirs()

from mico360.core import ollama_client as OC              # noqa: E402
from mico360.core.history import Meeting                  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


def pump(seconds: float = 0.1) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_for(cond, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


# -- fakes ---------------------------------------------------------------------
HOSTS_ASKED: list[str] = []
HOST_MODELS = {"http://127.0.0.1:11434": ["alpha", "beta"],
               "http://10.0.0.9:11434": ["remote-a", "remote-b"]}


def fake_check_status(host: str = "http://127.0.0.1:11434", **_kw):   # accepts use_cache= etc.
    HOSTS_ASKED.append(host)
    models = HOST_MODELS.get(host)
    if models is None:
        return OC.OllamaStatus(running=False, models=[], error="unreachable")
    return OC.OllamaStatus(running=True, models=list(models))


OC.check_status = fake_check_status                      # used by Settings (typed host)


class _Toast:
    def __init__(self):
        self.msgs = []

    def show_message(self, text, *a, **k):
        self.msgs.append(text)


class FakeEmailWorker(QThread):
    finished_ok = Signal()
    failed = Signal(str)
    DELAY = 0.4
    made: list = []

    def __init__(self, cfg, to, subject, body, html=None, attachments=None, cc=None):
        super().__init__()
        self.cfg, self.to, self.subject, self.body = cfg, to, subject, body
        self.html, self.attachments, self.cc = html, attachments or [], cc
        FakeEmailWorker.made.append(self)

    def run(self):
        time.sleep(self.DELAY)
        self.finished_ok.emit()


class FakePullWorker(QThread):
    progress = Signal(float, str)
    finished_ok = Signal(str)
    failed = Signal(str)
    made: list = []

    def __init__(self, host, model):
        super().__init__()
        self.host, self.model = host, model
        self._cancel = False
        self.cancel_called = False
        FakePullWorker.made.append(self)

    def cancel(self):
        self._cancel = self.cancel_called = True

    def run(self):
        end = time.time() + 10
        while not self._cancel and time.time() < end:
            self.progress.emit(0.5, "downloading")
            time.sleep(0.05)
        self.failed.emit("Cancelled." if self._cancel else "timeout")


class FakeDownloadWorker(QThread):
    progress = Signal(float, int, int)
    finished_ok = Signal(str)
    failed = Signal(str)
    made: list = []

    def __init__(self, info, dest):
        super().__init__()
        self.info, self.dest = info, dest
        self._cancel = False
        self.verify_note = ""; self.expected_sha256 = ""
        FakeDownloadWorker.made.append(self)

    def cancel(self):
        self._cancel = True

    def run(self):
        end = time.time() + 10
        while not self._cancel and time.time() < end:
            self.progress.emit(0.3, 30, 100)
            time.sleep(0.05)
        self.failed.emit("Download cancelled." if self._cancel else "timeout")


class FakeCheckWorker(QThread):
    done = Signal(object)
    repos: list = []

    def __init__(self, repo):
        super().__init__()
        FakeCheckWorker.repos.append(repo)

    def run(self):
        from mico360.core import updater
        self.done.emit(updater.UpdateInfo(status=updater.UP_TO_DATE))


class FakeTranscribeWorker(QThread):
    progress = Signal(float, str)
    finished_ok = Signal(object)
    failed = Signal(str)
    languages: list = []

    def __init__(self, engine, path, language="auto", diarize=False):
        super().__init__()
        FakeTranscribeWorker.languages.append(language)

    def cancel(self):
        pass

    def run(self):
        self.failed.emit("Cancelled.")


class FakeGenerateWorker(QThread):
    progress = Signal(float, str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, gen, transcript, *a, **k):
        super().__init__()

    def cancel(self):
        pass

    def run(self):
        time.sleep(0.1)
        self.finished_ok.emit("# Meeting Minutes\n**Meeting Title:** Generated\nbody")


# -- helpers -------------------------------------------------------------------
_ORIG_QUESTION = QMessageBox.question


def answer_questions(button):
    QMessageBox.question = staticmethod(lambda *a, **k: button)


def restore_questions():
    QMessageBox.question = _ORIG_QUESTION


def make_ctx():
    from mico360.ui.context import AppContext
    ctx = AppContext()
    ctx.ai_status = lambda *a, **k: NS(running=True, models=["alpha", "beta"], error="")
    ctx.transcription_engine = lambda: None
    s = ctx.settings
    s.set("auto_check_updates", False)
    s.set("onboarded", True)
    s.set("auto_record", False)
    s.set("ollama_host", "http://127.0.0.1:11434")
    s.set("language", "AR")                     # a free-text value from an older build (M9)
    s.set("github_repo", "https://github.com/acme/widgets.git")      # pasted URL (L7)
    return ctx


def shortcut(win, seq: str):
    for sc in win.findChildren(QShortcut):
        if sc.key().toString() == seq:
            return sc
    return None


# =============================================================================
def main() -> int:
    from mico360.ui import components as C
    from mico360.ui import dialogs as D
    from mico360.ui import pages as P
    from mico360.ui import pages_extra as PX
    from mico360.ui import settings_page as SP
    from mico360.ui import theme
    from mico360.ui import workers as W
    from mico360.ui.main_window import MainWindow

    W.EmailWorker = FakeEmailWorker
    W.ModelPullWorker = FakePullWorker
    PX.UpdateDownloadWorker = FakeDownloadWorker
    PX.UpdateCheckWorker = FakeCheckWorker
    P.TranscribeWorker = FakeTranscribeWorker
    P.GenerateWorker = FakeGenerateWorker
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)

    ctx = make_ctx()
    win = MainWindow(ctx)
    win.resize(1320, 820); win.show(); pump(0.2)
    page, sp, up, ap = win.new_page, win.settings_page, win.updates_page, win.actions_page
    H = ctx.history

    # ---- M9: language --------------------------------------------------------
    print("M9 — Language is a list of Whisper codes")
    check("stored 'AR' is normalised to 'ar' at startup", ctx.settings.get("language") == "ar",
          repr(ctx.settings.get("language")))
    check("Language is a combo showing names, storing codes",
          sp.lang.currentData() == "ar" and "Arabic" in sp.lang.currentText()
          and sp.lang.itemData(0) == "auto" and sp.lang.count() > 50)
    norm = SP.normalize_language
    cases = {"AR": "ar", "ar-SA": "ar", "ar_SA": "ar", "Arabic": "ar", "Auto": "auto",
             "": "auto", "EN-us": "en", "urdu": "ur", "zh-Hans": "zh", "klingon": "auto"}
    bad = {k: norm(k) for k, v in cases.items() if norm(k) != v}
    check("normalize_language handles codes, regions, names and junk", not bad, f"{bad}")
    ctx.settings.set("language", "ar-SA")                  # an old value slipped through
    page._media_queue = [str(_TMP / "clip.wav")]
    page._start_transcription(); wait_for(lambda: not page.is_busy())
    check("transcription is started with a valid code ('ar-SA' → 'ar')",
          FakeTranscribeWorker.languages[-1:] == ["ar"], f"{FakeTranscribeWorker.languages}")
    page._clear_files(); ctx.settings.set("language", "ar")

    # ---- L7: GitHub repo -------------------------------------------------------
    print("L7 — GitHub repo field is normalised to owner/name")
    check("a stored github.com URL is migrated to owner/name",
          ctx.settings.get("github_repo") == "acme/widgets", ctx.settings.get("github_repo"))
    nr = SP.normalize_repo
    ok = (nr("https://github.com/o/r/releases/tag/v1") == "o/r" and nr("git@github.com:o/r.git") == "o/r"
          and nr(" o/r/ ") == "o/r" and nr("") == "" and nr("not a repo") is None
          and nr("https://gitlab.com/o/r") is None)
    check("normalize_repo accepts URLs/SSH/owner-name, rejects junk", ok)
    sp.repo.setText("https://github.com/mico360om/MICO360-Meetings/releases")
    check("Save stores the pasted URL as owner/name",
          sp._save() and ctx.settings.get("github_repo") == "mico360om/MICO360-Meetings"
          and sp.repo.text() == "mico360om/MICO360-Meetings", ctx.settings.get("github_repo"))
    sp.repo.setText("this is not a repo")
    saved_ok = sp._save()
    check("an invalid repo is rejected (nothing saved)",
          saved_ok is False and ctx.settings.get("github_repo") == "mico360om/MICO360-Meetings")
    sp.revert()
    ctx.settings.set("github_repo", "https://github.com/acme/tool")    # hand-edited settings
    up.check(); wait_for(lambda: not up.is_busy())
    check("Updates check uses owner/name even for a stored URL",
          FakeCheckWorker.repos[-1:] == ["acme/tool"], f"{FakeCheckWorker.repos}")
    ctx.settings.set("github_repo", "mico360om/MICO360-Meetings")

    # ---- L5: preset → Custom ---------------------------------------------------
    print("L5 — editing Whisper fields updates the preset")
    sp.preset.setCurrentText("Balanced"); sp._apply_preset()
    i = sp.compute.findText("float32"); sp.compute.setCurrentIndex(i); sp.compute.activated.emit(i)
    check("changing compute switches the preset to Custom", sp.preset.currentText() == "Custom")
    i = sp.compute.findText("int8"); sp.compute.setCurrentIndex(i); sp.compute.activated.emit(i)
    i = sp.whisper.findData("small"); sp.whisper.setCurrentIndex(i); sp.whisper.activated.emit(i)
    check("fields matching a preset select that preset", sp.preset.currentText() == "Accurate")
    sp._save()
    check("the preset shown is what gets saved", ctx.settings.get("quality_preset") == "Accurate")

    # ---- L6: SMTP password ---------------------------------------------------------
    print("L6 — SMTP password stored exactly as typed")
    sp.smtp_host.setText("smtp.example.com"); sp.email_from.setText("me@example.com")
    sp.smtp_user.setText("user"); sp.smtp_password.setText("  s3cret with spaces ")
    sp._save()
    check("leading/trailing spaces are kept", ctx.settings.get("smtp_password") == "  s3cret with spaces ")

    # ---- H3: Send test email double-click -------------------------------------
    print("H3 — test e-mail never drops a running thread")
    FakeEmailWorker.made.clear()
    sp.test_btn.click(); sp.test_btn.click(); pump(0.05)
    check("a second click while sending starts no second worker", len(FakeEmailWorker.made) == 1)
    check("the button is disabled while sending", not sp.test_btn.isEnabled())
    check("the running worker is kept alive", FakeEmailWorker.made and FakeEmailWorker.made[0] in sp._bg_workers)
    check("test e-mail uses the unstripped password",
          FakeEmailWorker.made and FakeEmailWorker.made[0].cfg.password == "  s3cret with spaces ")
    wait_for(lambda: not sp.is_busy())
    pump(0.1)
    check("button re-enabled and worker released when done",
          sp.test_btn.isEnabled() and not sp._bg_workers)

    # ---- M10: typed Ollama host ------------------------------------------------
    print("M10 — Refresh / Install use the typed host")
    sp.host.setText("http://10.0.0.9:11434")
    HOSTS_ASKED.clear()
    sp._refresh_clicked()
    check("Refresh queries the typed host", HOSTS_ASKED and all(h == "http://10.0.0.9:11434" for h in HOSTS_ASKED),
          f"{HOSTS_ASKED}")
    check("model list comes from the typed host",
          [sp.model.itemText(i) for i in range(sp.model.count())] == ["remote-a", "remote-b"])
    check("environment line reflects the typed host", "2 installed" in sp.env_lbl.text())
    FakePullWorker.made.clear()
    sp.install_model_box.setCurrentText("tinyllama")
    sp._install_model()
    check("Install uses the typed host", FakePullWorker.made and FakePullWorker.made[0].host == "http://10.0.0.9:11434")

    # ---- H3: model install cancel -------------------------------------------------
    print("H3 — model install can be cancelled and is never dropped")
    w = FakePullWorker.made[0] if FakePullWorker.made else None
    check("Cancel button shown and Install disabled while installing",
          not sp.install_cancel_btn.isHidden() and not sp.install_btn.isEnabled())
    sp._install_model()
    check("a second Install while running is ignored", len(FakePullWorker.made) == 1)
    check("running pull worker is kept alive", w is not None and w in sp._bg_workers and w.isRunning())
    check("closeEvent sees the Settings worker", w in win._background_workers())
    sp.install_cancel_btn.click()
    wait_for(lambda: not sp.is_busy()); pump(0.1)
    check("Cancel calls the worker's cancel()", w is not None and w.cancel_called)
    check("after cancel: Install re-enabled, Cancel hidden, status says cancelled",
          sp.install_btn.isEnabled() and sp.install_cancel_btn.isHidden()
          and "cancel" in sp.install_status.text().lower())

    invalidated = []
    real_inv = ctx.invalidate_ai_status
    ctx.invalidate_ai_status = lambda: (invalidated.append(True), real_inv())
    real_ai_status = ctx.ai_status
    del ctx.ai_status                                     # use the real (cached) implementation
    sp._save()
    check("Save stores the typed host", ctx.settings.get("ollama_host") == "http://10.0.0.9:11434")
    check("Save clears the context's AI status cache", bool(invalidated))
    ctx.invalidate_ai_status = real_inv
    ctx.ai_status = real_ai_status
    sp.host.setText("http://127.0.0.1:11434"); sp._save()

    # ---- M11: unsaved settings --------------------------------------------------
    print("M11 — unsaved Settings are not silently dropped")
    win._goto(sp)
    old_chunk = ctx.settings.get("chunk_chars")
    sp.chunk.setValue(old_chunk + 500)
    check("changing a field marks Settings dirty", sp.is_dirty())
    answer_questions(QMessageBox.Cancel)
    moved = win._navigate(1)
    check("Cancel keeps you on Settings with the change",
          moved is False and win.stack.currentWidget() is sp and sp.chunk.value() == old_chunk + 500
          and win.nav_group.checkedButton() is win.nav_group.button(win.stack.indexOf(sp)))
    answer_questions(QMessageBox.Discard)
    win._navigate(1)
    check("Discard reverts the field and leaves",
          win.stack.currentWidget() is win.history_page and sp.chunk.value() == old_chunk
          and ctx.settings.get("chunk_chars") == old_chunk)
    win._goto(sp)
    sp.chunk.setValue(old_chunk + 1000)
    answer_questions(QMessageBox.Save)
    win._navigate(1)
    check("Save stores the change and leaves",
          win.stack.currentWidget() is win.history_page
          and ctx.settings.get("chunk_chars") == old_chunk + 1000)
    restore_questions()
    win._goto(sp)
    sp.chunk.setValue(old_chunk)
    sc = shortcut(win, "Ctrl+S")
    before = H.list(limit=100000)
    sc.activated.emit()
    check("Ctrl+S on Settings saves the settings", ctx.settings.get("chunk_chars") == old_chunk
          and not sp.is_dirty())
    check("Ctrl+S on Settings doesn't save a meeting", len(H.list(limit=100000)) == len(before))
    check("theme/size/AI mode (applied immediately) don't count as unsaved",
          "theme" not in sp._form_state() and "ai_provider" not in sp._form_state())
    win._goto(page)

    # ---- M3: model + prompt kept ------------------------------------------------
    print("M3 — the chosen model and prompt survive navigation")
    page.refresh_models()
    i = page.model_box.findText("beta"); page.model_box.setCurrentIndex(i); page.model_box.activated.emit(i)
    check("choosing a model remembers it", ctx.ai_model() == "beta")
    win._goto(win.history_page); win._goto(page)
    check("model still selected after changing pages", page.model_box.currentText() == "beta")
    page.refresh_prompts()
    page.prompt_box.setCurrentIndex(3)
    pid = page.prompt_box.currentData()
    page.toggle_prompt.setChecked(True)
    page.prompt_edit.setPlainText("MY ONE-OFF PROMPT [TRANSCRIPT_HERE]")
    page.refresh_prompts()                                 # what the Prompt Library triggers
    check("selected prompt kept across a prompt-list refresh", page.prompt_box.currentData() == pid)
    check("one-off prompt text kept while 'Edit prompt' is on",
          page.prompt_edit.toPlainText() == "MY ONE-OFF PROMPT [TRANSCRIPT_HERE]")
    page.toggle_prompt.setChecked(False)
    page.refresh_prompts()
    check("with 'Edit prompt' off, the template text is shown again",
          page.prompt_edit.toPlainText() != "MY ONE-OFF PROMPT [TRANSCRIPT_HERE]")

    # ---- M2: re-saving an opened meeting ------------------------------------------
    print("M2 — re-saving an opened meeting keeps its metadata")
    mid = H.save(Meeting(id=0, title="Board meeting", created_at=0, updated_at=0,
                         source_type="audio", source_path="C:/rec/board.wav", style="Detailed Minutes",
                         model="llama-orig", profile_id="prof-1",
                         transcript="board transcript", minutes="# Board minutes"))
    page.load_meeting(H.get(mid))
    ctx.ai_status = lambda *a, **k: NS(running=False, models=[], error="down")
    page.refresh_models()                                  # Setup now shows "⚠ Ollama not running"
    ctx.settings.set("active_profile", "prof-OTHER")
    page.style_box.setCurrentIndex((page.style_box.currentIndex() + 1) % page.style_box.count())
    page.minutes.setPlainText("# Board minutes (edited)")
    page._save_history(silent=True)
    m = H.get(mid)
    check("model kept (not the '⚠' placeholder)", m.model == "llama-orig", repr(m.model))
    check("style kept", m.style == "Detailed Minutes", repr(m.style))
    check("profile kept", m.profile_id == "prof-1", repr(m.profile_id))
    check("source kept", m.source_type == "audio" and m.source_path == "C:/rec/board.wav",
          repr((m.source_type, m.source_path)))
    check("edit itself saved", m.minutes == "# Board minutes (edited)")
    ctx.ai_status = lambda *a, **k: NS(running=True, models=["alpha", "beta"], error="")
    page.refresh_models()
    i = page.model_box.findText("alpha"); page.model_box.setCurrentIndex(i)
    page._generate(); wait_for(lambda: not page.is_busy()); pump(0.2)
    m = H.get(mid)
    check("new minutes generated → the new model/profile are stored",
          m.model == "alpha" and m.profile_id == "prof-OTHER" and m.source_type == "audio",
          repr((m.model, m.profile_id, m.source_type)))
    page._new_meeting(quiet=True)
    ctx.ai_status = lambda *a, **k: NS(running=False, models=[], error="down")
    page.refresh_models()
    page.transcript.setPlainText("draft with no AI available")
    nid = page._save_history(silent=True)
    check("a new draft never stores '⚠ …' as its model", not H.get(nid).model.startswith("⚠"),
          repr(H.get(nid).model))
    ctx.ai_status = lambda *a, **k: NS(running=True, models=["alpha", "beta"], error="")
    page.refresh_models()

    # ---- L3: clear + undo ----------------------------------------------------------
    print("L3 — clear then Undo doesn't duplicate the meeting")
    page._new_meeting(quiet=True)
    page.transcript.setPlainText("L3 transcript"); page.minutes.setPlainText("L3 minutes")
    lid = page._save_history(silent=True)
    n0 = len(H.list(limit=100000))
    P._undoable_clear(page.transcript); P._undoable_clear(page.minutes)
    page._autosave()                                        # autosave tick sees an empty form
    page.transcript.undo(); page.minutes.undo()
    check("Clear is undoable", page.transcript.toPlainText() == "L3 transcript"
          and page.minutes.toPlainText() == "L3 minutes")
    page._autosave()
    page._save_history(silent=True)
    check("no duplicate record after Undo", len(H.list(limit=100000)) == n0,
          f"{n0} → {len(H.list(limit=100000))}")
    check("the form is linked to the original record again", page._current_id == lid)
    P._undoable_clear(page.transcript); P._undoable_clear(page.minutes)
    page._autosave()
    page.transcript.setPlainText("a completely different meeting")
    page._autosave()
    check("clearing and typing a NEW meeting still makes a new record",
          len(H.list(limit=100000)) == n0 + 1 and H.get(lid).transcript == "L3 transcript")

    # ---- M4: multi-file drop + browse ---------------------------------------------
    print("M4 — every dropped / browsed file is added")
    page._new_meeting(quiet=True)
    files = []
    for name in ("a.wav", "b.mp3", "notes.txt"):
        f = _TMP / name
        f.write_text("imported notes text" if name.endswith(".txt") else "", encoding="utf-8")
        files.append(f)
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(f)) for f in files])
    ev = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    page.drop.dropEvent(ev)
    check("dropping 3 files adds all 3", page.files_list.count() == 3, str(page.files_list.count()))
    check("both media files are queued", [Path(p) for p in page._media_queue] == [files[0], files[1]],
          f"{page._media_queue}")
    check("the document's text is imported", "imported notes text" in page.transcript.toPlainText())
    page._clear_files()
    orig = QFileDialog.getOpenFileNames
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(files[0]), str(files[1])], ""))
    try:
        page.drop._browse()
    finally:
        QFileDialog.getOpenFileNames = orig
    check("Browse accepts several files", len(page._media_queue) == 2 and page.files_list.count() == 2)
    page._clear_files(); page._new_meeting(quiet=True)

    # ---- L1: Arabic alignment ------------------------------------------------------
    print("L1 — Arabic lines are right-aligned in the editors")

    def start_x(editor, block_no):
        b = editor.document().findBlockByNumber(block_no)
        line = b.layout().lineAt(0)
        x = line.cursorToX(0)
        return x[0] if isinstance(x, tuple) else x

    page.transcript.setPlainText("Hello team\nمرحبا بالجميع في الاجتماع")
    page._reached = 4; page.wizard.setCurrentIndex(page.STEP_TRANSCRIPT); pump(0.1)
    wv = page.transcript.viewport().width()
    xe, xa = start_x(page.transcript, 0), start_x(page.transcript, 1)
    check("transcript: English line starts at the left", xe < wv * 0.25, f"x={xe} w={wv}")
    check("transcript: Arabic line starts at the right", xa > wv * 0.6, f"x={xa} w={wv}")
    page.minutes.setPlainText("# Minutes\nمحضر الاجتماع")
    page.wizard.setCurrentIndex(page.STEP_MINUTES); page.minutes_tabs.setCurrentIndex(0); pump(0.1)
    wv = page.minutes.viewport().width()
    check("minutes: Arabic line is right-aligned", start_x(page.minutes, 1) > wv * 0.6)
    check("editor stays plain text (no rich paste)", not page.transcript.acceptRichText())
    page._new_meeting(quiet=True)

    # ---- L2: e-mail -------------------------------------------------------------------
    print("L2 — e-mail uses the edited title and builds attachments off the UI thread")
    ctx.settings.set("smtp_host", "smtp.example.com"); ctx.settings.set("smtp_user", "u")
    ctx.settings.set("smtp_password", "p"); ctx.settings.set("email_from", "me@example.com")
    page.minutes.setPlainText("# Meeting Minutes\n**Meeting Title:** Guessed Title\nbody")
    page.meeting_title.setText("Edited Title")
    seen = {}
    orig_exec = D.EmailComposeDialog.exec

    def fake_exec(dlg):
        seen["subject"] = dlg.subject.text()
        dlg.to.setText("someone@example.com")
        dlg.att_pdf.setChecked(True); dlg.att_docx.setChecked(True)
        return 1
    D.EmailComposeDialog.exec = fake_exec
    from mico360.export import service
    orig_export = service.export
    export_threads = []

    def fake_export(md, path, profile=None):
        export_threads.append(threading.current_thread() is threading.main_thread())
        time.sleep(0.2)
        Path(path).write_text("x", encoding="utf-8")
        return path
    service.export = fake_export
    FakeEmailWorker.made.clear()
    try:
        page._email_minutes()
        busy_now = page._email_busy() and not page.email_btn.isEnabled()
        page._email_minutes()                                # 2nd click while preparing
        wait_for(lambda: not page._email_busy(), 8)
        pump(0.1)
    finally:
        D.EmailComposeDialog.exec = orig_exec
        service.export = orig_export
    check("subject uses the edited title", "Edited Title" in seen.get("subject", ""), seen.get("subject"))
    check("attachments were built on a worker thread", export_threads and not any(export_threads),
          f"{export_threads}")
    check("button disabled while preparing; a 2nd click is ignored",
          busy_now and len(FakeEmailWorker.made) == 1, f"{len(FakeEmailWorker.made)} workers")
    em = FakeEmailWorker.made[0] if FakeEmailWorker.made else None
    check("e-mail sent with both attachments and an HTML body",
          em is not None and len(em.attachments) == 2 and bool(em.html))
    check("button re-enabled afterwards", page.email_btn.isEnabled())
    page._new_meeting(quiet=True)

    # ---- H3: follow-up e-mails ---------------------------------------------------------
    print("H3 — follow-up e-mails keep every running worker")
    FakeEmailWorker.made.clear()
    D.EmailComposeDialog.exec = lambda dlg: (dlg.to.setText("bob@example.com"), 1)[1]
    try:
        ap._send_followup_email("Bob", "Follow-up", "body")
        ap._send_followup_email("Ann", "Follow-up", "body")      # second while the first runs
        both_alive = (len(FakeEmailWorker.made) == 2
                      and all(w in ap._bg_workers for w in FakeEmailWorker.made))
        wait_for(lambda: not any(w.isRunning() for w in FakeEmailWorker.made), 5)
        pump(0.1)
    finally:
        D.EmailComposeDialog.exec = orig_exec
    check("both follow-up workers are kept while running", both_alive)
    check("closeEvent covers Action Items workers", hasattr(ap, "_bg_workers"))
    check("workers are released once finished", not ap._bg_workers)

    # ---- H3: update download cancel ----------------------------------------------------
    print("H3 — update download can be cancelled")
    from mico360.core import updater
    up._info = updater.UpdateInfo(status=updater.AVAILABLE, latest_version="9.9.9",
                                  download_url="https://example.invalid/Setup.exe")
    up._render(up._info)
    up._download()
    dl = FakeDownloadWorker.made[-1] if FakeDownloadWorker.made else None
    check("Cancel download is shown while downloading", not up.cancel_dl_btn.isHidden() and dl is not None)
    up._download()
    check("a second Download while running starts nothing", len(FakeDownloadWorker.made) == 1)
    check("running download worker is kept alive", dl in up._bg_workers and dl in win._background_workers())
    up.cancel_dl_btn.click()
    wait_for(lambda: not up.is_busy()); pump(0.1)
    check("after cancel: button hidden, Download available again",
          up.cancel_dl_btn.isHidden() and up.download_btn.isEnabled()
          and "cancelled" in up.status_row.text().lower())

    # ---- M8: dark theme tables ------------------------------------------------------
    print("M8 — Action Items readable in the dark theme")
    qss = theme.build_qss("dark")
    check("stylesheet sets alternate-background-color for tables",
          "alternate-background-color: " + theme.DARK["surface2"] in qss)
    from mico360.core.tasks import ActionItem
    ctx.action_items.all_items = lambda search="": [
        ActionItem("Send the report", "Alice", "2020-01-01", "Pending", meeting_id=1, meeting_title="M"),
        ActionItem("Book the room", "Bob", "", "Pending", meeting_id=1, meeting_title="M"),
        ActionItem("Plan Q3", "Cy", "", "Pending", meeting_id=1, meeting_title="M"),
    ]
    win.apply_theme("dark")
    win._goto(ap); pump(0.2)
    it = ap.table.item(0, ap._COL_TASK)
    check("overdue row text uses the theme's overdue colour",
          it is not None and it.foreground().color().name().lower() == theme.DARK["overdue_fg"].lower()
          and it.font().bold())
    check("overdue deadline is flagged with ⚠",
          ap.table.item(0, ap._COL_DUE).text().startswith("⚠"))
    img = ap.table.viewport().grab().toImage()
    x = ap.table.columnViewportPosition(ap._COL_TASK) + ap.table.columnWidth(ap._COL_TASK) - 6
    y = ap.table.rowViewportPosition(1) + 6
    col = img.pixelColor(x, y)
    check("alternate row is dark in the dark theme (not near-white)", col.lightness() < 110,
          f"{col.name()}")
    win._goto(page)

    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== UI: {passed}/{len(results)} passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        print("\n==== UI: crashed ====")
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        try:
            shutil.rmtree(_TMP, ignore_errors=True)
        except Exception:
            pass
        # PySide6 + Python 3.14 can crash during interpreter teardown; exit
        # directly (same approach as the other suites).
        os._exit(rc)
