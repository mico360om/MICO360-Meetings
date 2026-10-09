"""The New Meeting page fits the window: no vertical scrolling on any step.

    python tests/fixes_layout.py

  Y1  every wizard step fits without a vertical scrollbar at the default
      window size, on a 1366x768 laptop, and at the 1080x660 minimum — with
      a queued file and the progress bar showing, using the real Windows
      fonts (the off-screen fallback font is smaller than what users see)
  Y2  the progress / status area and the Back / Next / Create buttons stay
      pinned at the bottom, visible on every step at every size
  Y3  a short window switches the compact layout on, a tall one off; the
      step stack and the Source tabs are only as tall as the page on screen

Runs against a THROWAWAY data folder; no network, Ollama or audio.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mico360_layout_"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONUTF8", "1")
if Path(r"C:\Windows\Fonts").is_dir():                   # measure with the fonts users see
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
ROOT = Path(os.environ.get("MICO360_TEST_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360 import config                                # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"
config.ensure_dirs()

from mico360.core import ollama_client as OC              # noqa: E402

OC.check_status = lambda *a, **k: OC.OllamaStatus(running=True, models=["llama3.1:latest"])
for _n in ("question", "information", "warning", "critical"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: QMessageBox.Ok))

results: list[tuple[str, bool, str]] = []
_KEEP: list = []
SIZES = [(1320, 820, "default"), (1366, 729, "1366x768 laptop"), (1080, 660, "minimum")]


def check(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


def pump(n: int = 25) -> None:
    for _ in range(n):
        app.processEvents()
        time.sleep(0.002)


def make_window():
    from mico360.ui.context import AppContext
    from mico360.ui.main_window import MainWindow
    ctx = AppContext()
    ctx.settings.set("onboarded", True)
    ctx.settings.set("auto_check_updates", False)
    ctx.settings.set("auto_record", False)
    win = MainWindow(ctx)
    win.show()
    _KEEP.extend([ctx, win])
    return ctx, win


def test_fits() -> None:
    print("\n[Y1/Y2] every step fits; the footer stays visible")
    ctx, win = make_window()
    page = win.new_page
    # a realistic state: a queued file, a transcript, the progress bar showing
    page.transcript.setPlainText("hello " * 60)
    page._media_queue = ["C:/x/lcv oxy Voice 261005_101029.m4a"]
    page.files_list.addItem("🎵  lcv oxy Voice 261005_101029.m4a  (queued for transcription)")
    page.progress_row.setVisible(True); page.progress.setValue(59); page.progress_pct.setText("59%")
    page.status.setText("Transcribing…")
    for w, h, label in SIZES:
        win.resize(w, h); pump()
        for i, step in enumerate(page.STEP_TITLES):
            page._reached = len(page.STEP_TITLES) - 1
            page._goto_step(i); pump()
            rng = page._scroll.verticalScrollBar().maximum()
            check(f"{label} ({w}x{h}): the {step} step fits without a vertical scrollbar",
                  rng == 0, f"scrolls {rng}px")
            btn = page.next_btn if not page.next_btn.isHidden() else (
                page.generate_btn if not page.generate_btn.isHidden() else page.new_meeting_btn)
            top = btn.mapTo(page, btn.rect().topLeft()).y()
            bottom = btn.mapTo(page, btn.rect().bottomLeft()).y()
            check(f"{label}: the {step} step's action button is on screen, pinned near the bottom",
                  0 <= top and bottom <= page.height() and bottom >= page.height() - 60,
                  f"button y {top}-{bottom}, page height {page.height()}")
        page._goto_step(0); pump()
        pr = page.progress.mapTo(page, page.progress.rect().topLeft()).y()
        check(f"{label}: the progress bar is visible without scrolling",
              not page.progress.isHidden() and 0 <= pr <= page.height() - 40, f"progress y {pr}")
    win.close()


def test_sizing_mechanics() -> None:
    print("\n[Y3] compact mode and page-sized containers")
    ctx, win = make_window()
    page = win.new_page
    win.resize(1320, 820); pump()
    check("a tall window uses the full layout", page._compact is False and page.page_sub.isVisible()
          and page.drop.minimumHeight() >= 100)
    win.resize(1080, 660); pump()
    check("a short window switches to the compact layout (subtitle hidden, one-row drop area)",
          page._compact is True and not page.page_sub.isVisible() and page.drop.minimumHeight() <= 70
          and page.files_list.maximumHeight() <= 64)
    win.resize(1320, 820); pump()
    check("…and back", page._compact is False and page.page_sub.isVisible()
          and page.drop.minimumHeight() >= 100)
    stack = page.wizard
    heights = []
    for i in range(stack.count()):
        page._reached = 4; page._goto_step(i); pump()
        heights.append(stack.sizeHint().height())
        check(f"step {i + 1}: only the visible step is in the stack's layout",
              stack.layout().count() == 1 and stack.layout().itemAt(0).widget() is stack.widget(i)
              and all((stack.widget(k).isHidden()) == (k != i) for k in range(stack.count())))
    check("the stack's height follows the step on screen (steps differ in height)",
          len(set(heights)) > 1, str(heights))
    page._goto_step(0); pump()
    tabs = page.source_tabs
    up_h = tabs.sizeHint().height()
    tabs.setCurrentIndex(2); pump()                      # Record tab (the tall one)
    rec_h = tabs.sizeHint().height()
    tabs.setCurrentIndex(0); pump()
    check("the Source tabs are only as tall as the visible tab (Upload is shorter than Record)",
          up_h < rec_h and tabs.currentWidget() is tabs.widget(0), f"upload {up_h} record {rec_h}")
    check("select_record_tab() still brings the Record tab to the front",
          (page.select_record_tab(), tabs.currentIndex())[1] == 2)
    win.close()


def main() -> int:
    for fn in (test_fits, test_sizing_mechanics):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== LAYOUT: {passed}/{len(results)} passed ====")
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
