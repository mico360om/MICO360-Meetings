"""Meeting-isolation regression tests: work started for one meeting must never
be written into — or saved over — a different meeting.

    python tests/workflow_isolation.py

Covers the two data-loss bugs from docs/BUG_REPORT.md:
  C1  switching meetings (History "Open" / Ctrl+N) while minutes are being
      generated wrote the result into the meeting now on screen and saved it
      over that meeting's record;
  C2  an auto-recorded meeting was appended to whatever meeting was still open
      and saved over it.

Runs against a THROWAWAY data folder (LOCALAPPDATA is redirected before the app
is imported), so it never touches the real History. Generation is replaced by a
fake worker so no Ollama / Whisper / network is needed — it runs in CI.
Set MICO360_TEST_ROOT to run these tests against another checkout.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# -- isolate BEFORE importing the app ----------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="mico360_isolation_"))
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

from PySide6.QtCore import QThread, Signal          # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360 import config                            # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"
config.ensure_dirs()

from mico360.core.history import Meeting              # noqa: E402
from mico360.ui import pages as P                     # noqa: E402
from mico360.ui.context import AppContext             # noqa: E402

results: list[tuple[str, bool, str]] = []
# Qt objects kept alive until os._exit: letting Python garbage-collect live
# widgets/threads while Qt still runs can crash at shutdown (seen in CI).
_KEEP: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


# -- fakes ---------------------------------------------------------------------
class FakeGenerateWorker(QThread):
    """Stands in for GenerateWorker: returns minutes that name the transcript it
    was given, after a short delay (long enough to switch meetings meanwhile)."""
    progress = Signal(float, str)
    finished_ok = Signal(str)
    failed = Signal(str)
    DELAY = 0.6

    def __init__(self, gen, transcript, *a, **k):
        super().__init__()
        self._t = transcript
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        time.sleep(self.DELAY)
        body = self._t.strip().splitlines()[-1] if self._t.strip() else ""
        self.finished_ok.emit(f"# Meeting Minutes\n**Meeting Title:** Not specified\n"
                              f"MINUTES OF [{body}]")


class _Status:
    running = True
    models = ["fake-model"]
    error = ""


class _Toast:
    def show_message(self, *a, **k):
        pass


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def working(page) -> bool:
    w = getattr(page, "_worker", None)
    return bool(w is not None and w.isRunning())


def busy(page) -> bool:
    if hasattr(page, "is_busy"):
        return page.is_busy()
    return working(page)


def wait_idle(page, timeout: float = 5.0) -> None:
    end = time.time() + timeout
    while time.time() < end and busy(page):
        app.processEvents()
        time.sleep(0.02)
    pump(0.2)


def make_ctx() -> AppContext:
    ctx = AppContext()
    ctx.ai_status = lambda *a, **k: _Status()
    ctx.settings.set("auto_check_updates", False)      # no network from MainWindow
    ctx.settings.set("onboarded", True)                # no modal onboarding
    ctx.settings.set("auto_record", False)
    return ctx


def prep_page(page) -> None:
    page.model_box.clear()
    page.model_box.addItem("fake-model")


def new_meeting(page):
    """Ctrl+N. (Older checkouts' _new_meeting() had no `quiet` parameter.)"""
    try:
        return page._new_meeting(quiet=True)
    except TypeError:
        return page._new_meeting()


def saved(ctx, mid):
    return ctx.history.get(mid)


def ids(ctx) -> set[int]:
    return {m.id for m in ctx.history.list(limit=100000)}


# =============================================================================
def main() -> int:
    P.GenerateWorker = FakeGenerateWorker
    # "Stop the transcription and switch?" etc. → answer Yes without a dialog
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    ctx = make_ctx()
    H = ctx.history
    page = P.NewMeetingPage(ctx, _Toast())
    _KEEP.extend([ctx, page])
    prep_page(page)

    # ---- C1a: open another meeting from History while generating ------------
    print("C1 — switching meetings while minutes are generated")
    b_id = H.save(Meeting(id=0, title="Meeting B", created_at=0, updated_at=0,
                          source_type="text", transcript="TRANSCRIPT-B",
                          minutes="ORIGINAL MINUTES B"))
    before = ids(ctx)
    page.transcript.setPlainText("TRANSCRIPT-A")
    page.meet_title.setText("Setup title for A")
    page._generate()
    check("generation started for A", working(page))
    if hasattr(page, "is_busy"):
        page._generate()                               # a 2nd Ctrl+G must be ignored
        check("second generate while running is ignored", working(page))
    else:   # a checkout without the guard aborts the process here (see BUG_REPORT H1)
        print("  [SKIP] second generate — this checkout has no running-worker guard")
    opened = page.load_meeting(saved(ctx, b_id))
    check("History 'Open' succeeds mid-generation", opened is not False)
    wait_idle(page)
    b = saved(ctx, b_id)
    check("B keeps its own minutes", b.minutes == "ORIGINAL MINUTES B", repr(b.minutes[:60]))
    check("B keeps its own transcript", b.transcript == "TRANSCRIPT-B", repr(b.transcript[:60]))
    new = [saved(ctx, i) for i in ids(ctx) - before]
    check("A was saved as its own record", len(new) == 1, f"{len(new)} new records")
    if new:
        check("A's record has A's transcript", new[0].transcript == "TRANSCRIPT-A",
              repr(new[0].transcript[:60]))
        check("A's record has A's minutes", "[TRANSCRIPT-A]" in new[0].minutes,
              repr(new[0].minutes[-40:]))
    check("screen still shows B's minutes", page.minutes.toPlainText() == "ORIGINAL MINUTES B")
    check("A's setup fields don't leak into B", page.meet_title.text() == "")

    # ---- C1b: Ctrl+N (new meeting) while generating --------------------------
    new_meeting(page)
    before = ids(ctx)
    page.transcript.setPlainText("TRANSCRIPT-D")
    page._generate()
    new_meeting(page)                      # Ctrl+N mid-generation
    wait_idle(page)
    new = [saved(ctx, i) for i in ids(ctx) - before]
    check("Ctrl+N mid-generation: D saved once, with D's minutes",
          len(new) == 1 and new[0].transcript == "TRANSCRIPT-D"
          and "[TRANSCRIPT-D]" in new[0].minutes,
          f"{[(m.transcript, m.minutes[-20:]) for m in new]}")
    check("Ctrl+N mid-generation: new meeting stays empty",
          not page.transcript.toPlainText() and not page.minutes.toPlainText())

    # ---- C1c: reopening the SAME meeting while it generates -----------------
    new_meeting(page)
    page.transcript.setPlainText("TRANSCRIPT-E")
    page._save_history(silent=True)
    e_id = page._current_id
    page._generate()
    page.load_meeting(saved(ctx, e_id))                # reopen E from History
    wait_idle(page)
    e = saved(ctx, e_id)
    check("reopened meeting receives its own minutes (screen + History)",
          "[TRANSCRIPT-E]" in e.minutes and "[TRANSCRIPT-E]" in page.minutes.toPlainText(),
          repr(e.minutes[-40:]))

    # ---- C2: auto-record after a finished meeting ----------------------------
    print("C2 — auto-record must start a new meeting")
    from mico360.ui.main_window import MainWindow
    win = MainWindow(ctx)
    _KEEP.append(win)
    wp = win.new_page
    prep_page(wp)
    panel = wp.recorder_panel
    panel.start_auto = lambda *a, **k: True            # no real audio device in tests
    panel.is_recording = lambda: False

    new_meeting(wp)
    wp.transcript.setPlainText("TRANSCRIPT-1")
    wp._generate()
    wait_idle(wp)
    m1_id = wp._current_id
    m1 = saved(ctx, m1_id)
    check("meeting 1 generated and saved", m1 is not None and "[TRANSCRIPT-1]" in m1.minutes)
    before = ids(ctx)
    started = win._begin_auto_record("Microsoft Teams: Weekly sync")
    check("auto-record starts", started is True)
    check("auto-record cleared the form", not wp.transcript.toPlainText()
          and not wp.minutes.toPlainText() and wp._current_id is None)
    # the meeting ends: the recording's transcript arrives and minutes are generated
    wp._on_live_transcript("TRANSCRIPT-2")
    wait_idle(wp)
    m1_after = saved(ctx, m1_id)
    check("meeting 1 is untouched", m1_after.transcript == "TRANSCRIPT-1"
          and m1_after.minutes == m1.minutes and m1_after.title == m1.title,
          repr((m1_after.transcript[:40], m1_after.minutes[-30:])))
    new = [saved(ctx, i) for i in ids(ctx) - before]
    check("meeting 2 got its own record",
          len(new) == 1 and new[0].transcript == "TRANSCRIPT-2"
          and "[TRANSCRIPT-2]" in new[0].minutes,
          f"{[(m.transcript, m.minutes[-20:]) for m in new]}")

    # auto-record while a generation for the open meeting is still running
    new_meeting(wp)
    wp.transcript.setPlainText("TRANSCRIPT-3")
    wp._generate()
    before = ids(ctx)
    win._begin_auto_record("Zoom: Standup")
    wp._on_live_transcript("TRANSCRIPT-4")
    wait_idle(wp)
    new = sorted((saved(ctx, i) for i in ids(ctx) - before), key=lambda m: m.id)
    by_t = {m.transcript: m for m in new}
    check("auto-record mid-generation: both meetings saved separately",
          set(by_t) >= {"TRANSCRIPT-3", "TRANSCRIPT-4"}
          and "[TRANSCRIPT-3]" in by_t.get("TRANSCRIPT-3", Meeting(0, "", 0, 0)).minutes
          and "[TRANSCRIPT-4]" in by_t.get("TRANSCRIPT-4", Meeting(0, "", 0, 0)).minutes,
          f"{[(m.transcript, m.minutes[-20:]) for m in new]}")

    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== ISOLATION: {passed}/{len(results)} passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        print("\n==== ISOLATION: crashed ====")
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        try:
            shutil.rmtree(_TMP, ignore_errors=True)
        except Exception:
            pass
        # PySide6 + Python 3.14 can crash during interpreter teardown; exit
        # directly (same approach as the other suites).
        os._exit(rc)
