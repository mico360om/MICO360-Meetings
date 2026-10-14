"""Automatic update checks.

    python tests/fixes_updates.py

  U1  the app checks for an update every time it opens (auto-check on, the
      default), also when the repository setting was cleared; not when the
      user turned auto-check off
  U2  the result is logged; an available update marks Updates in the sidebar
      and the message offers "Open Updates"; a failed check stays quiet
  U3  while the app stays open: a 6-hourly re-check, and a re-check when the
      Updates page is opened after an hour
  U4  the release is read correctly: version comparison, the versioned
      installer and its own checksum are chosen when a release carries two
  U5  a live check against the real GitHub releases (skipped offline)

Runs against a THROWAWAY data folder; GitHub is faked except in U5.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mico360_upd_"))
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

from types import SimpleNamespace as NS                   # noqa: E402

from PySide6.QtCore import QThread, Signal                # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360 import config                                # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"
config.ensure_dirs()

from mico360.config import DEFAULT_REPO                   # noqa: E402
from mico360.core import ollama_client as OC              # noqa: E402
from mico360.core import updater                          # noqa: E402
from mico360.ui import pages_extra as PX                  # noqa: E402

results: list[tuple[str, bool, str]] = []
_KEEP: list = []


def check(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


def pump(seconds: float = 0.1) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def settled(up) -> bool:
    """A check has finished AND its result has been applied (the worker's reply
    is queued to the GUI thread, so the thread can end a moment before it)."""
    return not up.is_busy() and "Checking" not in up.status_row.text()


def wait_for(cond, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


# -- fakes -----------------------------------------------------------------------
OC.check_status = lambda *a, **k: OC.OllamaStatus(running=False, models=[], error="test")
for _n in ("question", "information", "warning", "critical"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: QMessageBox.Ok))


class FakeCheckWorker(QThread):
    done = Signal(object)
    repos: list = []
    result: updater.UpdateInfo | None = None

    def __init__(self, repo: str):
        super().__init__()
        self.repo = repo
        FakeCheckWorker.repos.append(repo)

    def run(self):
        info = FakeCheckWorker.result or updater.UpdateInfo(status=updater.UP_TO_DATE,
                                                            latest_version="1.2.3")
        info.repo_url = updater.repo_url(self.repo)
        self.done.emit(info)


PX.UpdateCheckWorker = FakeCheckWorker


class Toast:
    def __init__(self):
        self.calls = []

    def show_message(self, text, kind="info", msec=0, links=None):
        self.calls.append((text, kind, dict(links or {})))


class LogCatch(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def available(version: str = "9.9.9") -> updater.UpdateInfo:
    return updater.UpdateInfo(status=updater.AVAILABLE, latest_version=version,
                              download_url=f"https://example.invalid/MICO360Meetings-Setup-{version}.exe",
                              size_bytes=150_000_000, release_date="2026-10-01")


def make_window(auto_check: bool = True, repo: str = ""):
    from mico360.ui.context import AppContext
    from mico360.ui.main_window import MainWindow
    ctx = AppContext()
    ctx.settings.set("onboarded", True)
    ctx.settings.set("auto_record", False)
    ctx.settings.set("auto_check_updates", auto_check)
    ctx.settings.set("github_repo", repo)
    win = MainWindow(ctx)
    rec = Toast()                             # record what the user is told, on the real toast
    win.toast.calls = rec.calls
    win.toast.show_message = rec.show_message
    win.resize(1280, 820); win.show()
    _KEEP.extend([ctx, win])
    return ctx, win


# =============================================================================
def test_check_on_open() -> None:
    print("\n[U1] the app checks for updates every time it opens")
    catch = LogCatch()
    logging.getLogger("mico360.pages_extra").addHandler(catch)
    logging.getLogger("mico360.pages_extra").setLevel(logging.INFO)   # as setup_logging() does
    FakeCheckWorker.repos.clear()
    FakeCheckWorker.result = available()
    ctx, win = make_window(auto_check=True, repo="")      # repository setting blank
    t0 = time.time()
    ran = wait_for(lambda: FakeCheckWorker.repos and settled(win.updates_page), 10)
    check("a check runs by itself shortly after the window opens",
          ran and time.time() - t0 < 8, f"{time.time() - t0:.1f}s")
    check("…against the product's own repository when the setting is blank",
          FakeCheckWorker.repos[-1:] == [DEFAULT_REPO], str(FakeCheckWorker.repos))
    check("the check is logged (start and result) so it can be verified on any PC",
          any(l.startswith("update check: automatic") for l in catch.lines)
          and any(l.startswith(f"update check: {updater.AVAILABLE}") for l in catch.lines),
          str(catch.lines[-3:]))
    btn = win._updates_nav_button()
    check("an available update marks “Updates” in the sidebar with the version",
          btn is not None and "9.9.9" in btn.text() and btn.property("badge") == "true",
          btn.text() if btn else "")
    toast = win.toast.calls[-1] if win.toast.calls else ("", "", {})
    check("…and the message offers “Open Updates”",
          "9.9.9" in toast[0] and "Open Updates" in toast[2], str(toast))
    toast[2]["Open Updates"]()
    pump(0.1)
    check("the link opens the Updates page, which shows the download button",
          win.stack.currentWidget() is win.updates_page and not win.updates_page.download_btn.isHidden())
    check("the Updates page shows the new version", "9.9.9" in win.updates_page.status_row.text())
    check("…and a re-check every 6 hours is armed while the app stays open",
          win._update_timer.isActive() and win._update_timer.interval() == 6 * 3600 * 1000)
    win.close()

    # a stored URL instead of owner/name still works
    FakeCheckWorker.repos.clear()
    FakeCheckWorker.result = None
    ctx, win = make_window(auto_check=True, repo="https://github.com/acme/tool/releases")
    wait_for(lambda: FakeCheckWorker.repos and settled(win.updates_page), 10)
    check("a pasted repository URL is normalised for the check",
          FakeCheckWorker.repos[-1:] == ["acme/tool"], str(FakeCheckWorker.repos))
    check("when up to date nothing is announced and the sidebar is unmarked",
          not win.toast.calls and win._updates_nav_button().property("badge") != "true")
    win.close()

    # the user turned auto-check off
    FakeCheckWorker.repos.clear()
    ctx, win = make_window(auto_check=False)
    pump(3.5)
    check("with auto-check off no check runs on open and no re-check timer is armed",
          not FakeCheckWorker.repos and not win._update_timer.isActive())
    win.close()
    logging.getLogger("mico360.pages_extra").removeHandler(catch)


def test_quiet_failure_and_recheck() -> None:
    print("\n[U2/U3] offline start; re-checks while the app stays open")
    FakeCheckWorker.repos.clear()
    FakeCheckWorker.result = updater.UpdateInfo(
        status=updater.FAILED, error="Could not reach GitHub: offline. Check your internet connection.")
    ctx, win = make_window(auto_check=True)
    wait_for(lambda: FakeCheckWorker.repos and settled(win.updates_page), 10)
    up = win.updates_page
    check("an offline start-up check fails quietly: no message, no sidebar mark",
          not win.toast.calls and win._updates_nav_button().property("badge") != "true"
          and len(FakeCheckWorker.repos) == 1)
    check("…but the Updates page says what happened and offers Retry",
          "reach GitHub" in up.status_row.text() and not up.retry_btn.isHidden())

    n = len(FakeCheckWorker.repos)
    win._goto(win.updates_page); pump(0.3)
    check("opening the Updates page right after a check does not check again",
          len(FakeCheckWorker.repos) == n)
    up.last_check = time.time() - 2 * 3600
    FakeCheckWorker.result = available("9.9.10")
    win._goto(win.new_page); pump(0.1)
    win._goto(win.updates_page)
    wait_for(lambda: len(FakeCheckWorker.repos) > n and settled(up), 10)
    check("opening the Updates page after an hour re-checks", len(FakeCheckWorker.repos) == n + 1)
    check("…and the newly available update is shown and marked",
          "9.9.10" in up.status_row.text() and "9.9.10" in win._updates_nav_button().text())
    n = len(FakeCheckWorker.repos)
    win._auto_update_check()                                   # what the 6-hour timer fires
    wait_for(lambda: len(FakeCheckWorker.repos) > n and settled(up), 10)
    check("the periodic re-check runs a check", len(FakeCheckWorker.repos) == n + 1)
    FakeCheckWorker.result = updater.UpdateInfo(status=updater.UP_TO_DATE, latest_version="9.9.10")
    n = len(FakeCheckWorker.repos)
    win._auto_update_check()
    wait_for(lambda: len(FakeCheckWorker.repos) > n and settled(up), 10)
    check("once the update is installed the sidebar mark is cleared",
          win._updates_nav_button().property("badge") == "false"
          and "9.9.10" not in win._updates_nav_button().text())
    ctx.settings.set("auto_check_updates", False)
    n = len(FakeCheckWorker.repos)
    win._auto_update_check(); pump(0.3)
    check("turning auto-check off stops the periodic checks", len(FakeCheckWorker.repos) == n)
    win.close()


def test_release_parsing() -> None:
    print("\n[U4] reading a release")
    ok = all([
        updater.is_newer("1.2.4", "1.2.3"), updater.is_newer("1.3.0", "1.2.9"),
        updater.is_newer("2.0.0", "1.9.9"), not updater.is_newer("1.2.3", "1.2.3"),
        not updater.is_newer("1.2.2", "1.2.3"), updater.is_newer("1.2.3", "1.2.3-rc1"),
        not updater.is_newer("1.2.3-rc1", "1.2.3"), updater.is_newer("v1.10.0", "1.9.0"),
        not updater.is_newer("", "1.2.3"), not updater.is_newer("latest", "1.2.3"),
    ])
    check("version comparison: 1.10 > 1.9, release > rc, junk is never 'newer'", ok)
    assets = [
        {"name": "SHA256SUMS.txt", "size": 93, "browser_download_url": "u/sums"},
        {"name": "MICO360Meetings-Setup.exe", "size": 139_000_000, "browser_download_url": "u/bare"},
        {"name": "MICO360Meetings-Setup.exe.sha256", "size": 66, "browser_download_url": "u/bare.sha"},
        {"name": "MICO360Meetings-Setup-1.2.4.exe", "size": 169_000_000, "browser_download_url": "u/ver"},
        {"name": "MICO360Meetings-Setup-1.2.4.exe.sha256", "size": 66, "browser_download_url": "u/ver.sha"},
    ]
    url, size, sha, per_file = updater._pick_assets(assets, "v1.2.4")
    check("a release with two installers: the one named for the version wins, with its own checksum",
          (url, size, sha, per_file) == ("u/ver", 169_000_000, "u/ver.sha", True), str((url, sha)))
    url, size, sha, per_file = updater._pick_assets(list(reversed(assets)), "1.2.4")
    check("…whatever order GitHub lists them in", url == "u/ver" and sha == "u/ver.sha")
    url, _s, sha, per_file = updater._pick_assets(assets[:3], "1.2.4")
    check("an unversioned installer falls back to its own .sha256", url == "u/bare" and sha == "u/bare.sha")
    url, _s, sha, per_file = updater._pick_assets(assets[:2], "1.2.4")
    check("…else the SHA256SUMS file", url == "u/bare" and sha == "u/sums" and not per_file)
    check("no installer asset: nothing to download", updater._pick_assets(assets[:1], "1")[0] == "")
    body = ("## Fixes\n- a\n\nSHA256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  "
            "MICO360Meetings-Setup-1.2.4.exe\n")
    check("a checksum in the release notes counts only for the named installer",
          updater.parse_checksum(body, "MICO360Meetings-Setup-1.2.4.exe").startswith("0123")
          and updater.parse_checksum(body, "Other.exe") == "")
    body = ("# MICO360 Meetings v9\n\nIntro line one\nline two.\n\n## New features\n\n"
            "- **Bold:** first item that\n  wraps onto a second line.\n- `Setup.exe /X` <b>raw</b>\n\n"
            "## Fixes\n\n- A fix\n  continued.\n\n> **Note:** quoted.\n\n```\nabc123  Setup.exe\n```\n")
    feats, fixes, sec, summary = updater._classify_body(body)
    check("release notes: a wrapped bullet stays one item",
          feats == ["**Bold:** first item that wraps onto a second line.", "`Setup.exe /X` <b>raw</b>"]
          and fixes == ["A fix continued."], f"{feats} {fixes}")
    check("…the intro and quoted notes are the description; code blocks are left out",
          summary == "Intro line one line two. **Note:** quoted.", summary)
    info = available("9.9.9")
    info.features, info.fixes, info.description = feats, fixes, "**Local-only release:** a & b"
    html = PX.UpdatesPage._build_html(None, info)
    check("the Updates page renders Markdown in the notes (no literal ** or `)",
          "<b>Bold:</b>" in html and "<code>Setup.exe /X</code>" in html
          and "**" not in html and "`" not in html, html[:200])
    check("…and escapes HTML from the release body",
          "&lt;b&gt;raw&lt;/b&gt;" in html and "a &amp; b" in html)
    check("the default repository is the product's",
          DEFAULT_REPO == "mico360om/MICO360-Meetings"
          and updater.normalize_repo("https://github.com/mico360om/MICO360-Meetings.git") == DEFAULT_REPO)


def test_live() -> None:
    print("\n[U5] live check against GitHub")
    t0 = time.time()
    info = updater.check_for_updates(DEFAULT_REPO, timeout=12)
    took = time.time() - t0
    if info.status == updater.FAILED and "reach" in (info.error or ""):
        print(f"  [SKIP] offline: {info.error[:80]}")
        return
    check("the real latest release is read", info.status in (updater.UP_TO_DATE, updater.AVAILABLE),
          f"{info.status}: {info.error}")
    check("…within a few seconds", took < 10, f"{took:.1f}s")
    name = Path(info.download_url).name
    check("it points at the published installer with a checksum",
          name.startswith("MICO360Meetings-Setup") and name.endswith(".exe")
          and (info.checksum_url.endswith(".sha256") or info.expected_sha256), f"{name} {info.checksum_url}")
    check("the published version compares sensibly with this build",
          updater.is_newer(info.latest_version, "0.0.1") and info.latest_version.count(".") == 2,
          info.latest_version)


# =============================================================================
def main() -> int:
    for fn in (test_check_on_open, test_quiet_failure_and_recheck, test_release_parsing, test_live):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== UPDATES: {passed}/{len(results)} passed ====")
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
