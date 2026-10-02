"""Release-readiness gate — ONE command that runs everything automatable.

    python tests/release_check.py            # full gate (suites + app + security)
    python tests/release_check.py --fast     # suites only (skip built-app launch)

Runs, in order:
  1. Module tests           (tests/module_tests.py)
  2. Integration QA         (tests/qa_check.py)
  3. Consistency audit      (tests/consistency_audit.py)
  3a. Parser tests          (tests/test_parsers.py)
  3b. Meeting isolation    (tests/workflow_isolation.py — switching meetings /
                            auto-record never overwrite another meeting)
  3c. Release fixes        (tests/release_fixes.py — remote audio, thread crashes,
                            pywin32 in builds, recordings kept, multi-installer)
  3d. Fix suites           (tests/fixes_capture.py, fixes_capture2.py, fixes_ai.py,
                            fixes_data.py, fixes_ui.py, fixes_pdf.py,
                            fixes_templates.py — each runs
                            when present, SKIP otherwise)
  4. Security scan          (no secrets in tracked files / source tree)
  5. Version coherence      (mico360.__version__ == installer.iss AppVersion)
  6. Built-app launch       (build/dist exe shows its MAIN window — not the
                            "already running" box or a crash dialog — writes no
                            crash report, and quits cleanly with exit code 0)
                                                               [skipped with --fast]
  7. Integration liveness   (Ollama reachable; email configured — WARN only)

Every suite runs against its own throwaway data folder (tests/_isolation.py),
so the gate never changes the developer's real meetings or settings.

Exits 0 only if every REQUIRED step passes. WARN and SKIP steps never fail the
gate, but they are reported as such — a check that did not run is never a PASS.
Reminder: the manual pass in docs/PRERELEASE_CHECKLIST.md is still required
before tagging — this gate cannot test real microphones or a clean machine.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The gate prints arrows/dashes; never let a legacy code page (cp1252) crash it.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
ENV = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONUTF8": "1",
       "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}

results: list[tuple[str, str, str]] = []      # (name, PASS/FAIL/WARN/SKIP, detail)

# Secret-shaped strings that must never be committed (GitHub tokens, MICO360
# Connect keys, 32-hex API keys next to the word secret/api key, SMTP passwords).
SECRET_PATTERN = (r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
                  r"mico_[A-Za-z0-9]{16,}|"
                  r"[0-9a-f]{32}['\"]?\s*(#|$).*(secret|api.?key)|"
                  r"smtp_password.\s*:\s*['\"][^'\"]{8,}")


def record(name, status, detail=""):
    results.append((name, status, detail))
    print(f"  [{status:4}] {name}  {detail}")


# Lines worth showing when a suite fails: its own FAIL/DIFF verdicts and any
# traceback / error summary lines (not just the last few lines of output).
_FAIL_LINE = re.compile(r"\[(FAIL|DIFF)\]|Traceback \(most recent|^\s*\w*(Error|Exception)\b|"
                        r"crashed|NOT ISOLATED", re.I)


def run_suite(name, script, pattern, timeout=360, optional=False):
    """Run one test script; PASS only if it exits 0 AND prints its tally line.

    optional=True: the script may not exist in this checkout (SKIP, not FAIL).
    """
    path = ROOT / script
    if not path.exists():
        record(name, "SKIP" if optional else "FAIL", f"{script} not present")
        return
    t0 = time.time()
    try:
        p = subprocess.run([PY, "-u", str(path)], env=ENV, cwd=ROOT,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        m = re.search(pattern, out)
        took = f"{time.time()-t0:.0f}s"
        n_skip = len(re.findall(r"^\s*\[SKIP\]", out, re.M))
        if p.returncode == 0 and m:
            record(name, "PASS", f"{m.group(0)} in {took}"
                   + (f"  ({n_skip} check(s) SKIPPED - see suite output)" if n_skip else ""))
        else:
            lines = out.strip().splitlines()
            bad = [ln.rstrip() for ln in lines if _FAIL_LINE.search(ln)]
            shown = bad[:40] + (["  ..."] if len(bad) > 40 else [])
            tail = lines[-5:]
            detail = (f"rc={_fmt_rc(p.returncode)}; tally "
                      f"{'found' if m else 'NOT found'} ({pattern})")
            if shown:
                detail += "\n    failing lines:\n" + "\n".join("      " + s for s in shown)
            detail += "\n    last lines:\n" + "\n".join("      " + s for s in tail)
            record(name, "FAIL", detail)
    except subprocess.TimeoutExpired:
        record(name, "FAIL", f"timeout after {timeout}s")


def _fmt_rc(rc) -> str:
    if rc is None:
        return "None"
    if rc < 0 or rc > 255:
        return f"{rc & 0xFFFFFFFF:#010x}"         # e.g. 0xc0000409 (native crash)
    return str(rc)


# ---------------------------------------------------------------------------
# Built-app self-test: run the feature checks INSIDE the frozen exe
# ---------------------------------------------------------------------------
def _windows_crashes(exe: Path, since: float) -> list[str]:
    """Crashes of `exe` that Windows logged since `since` (epoch seconds).

    A crash while the process unloads its DLLs happens AFTER the exit code is
    set, so the exit code alone can look clean; Windows Error Reporting still
    logs it (Application log, event 1000). [] when not on Windows / unreadable."""
    if sys.platform != "win32":
        return []
    time.sleep(3)                                     # WER logs within a second or two
    import datetime as _dt
    start = _dt.datetime.fromtimestamp(since).strftime("%Y-%m-%dT%H:%M:%S")
    ps = ("Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; "
          f"StartTime=[datetime]'{start}'}} -ErrorAction SilentlyContinue | "
          "ForEach-Object { $_.Message -replace \"`r?`n\", ' | ' }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=60).stdout
    except Exception:
        return []
    hits = []
    for line in out.splitlines():
        if str(exe).lower() in line.lower():
            mod = re.search(r"Faulting module name: ([^,|]+)", line)
            off = re.search(r"Fault offset: (\S+)", line)
            hits.append(f"{mod.group(1).strip() if mod else '?'} @ {off.group(1) if off else '?'}")
    return hits


def check_built_selftest(exe: Path, timeout_s: int = 900) -> None:
    """`MICO360Meetings.exe --self-test` exercises every major feature with the
    libraries, codecs and assets actually bundled in the build (see
    mico360/selftest.py). Uses a throwaway data folder inside the app."""
    import json
    import tempfile
    name = "Built-app self-test"
    report = Path(tempfile.gettempdir()) / f"mico360_selftest_{os.getpid()}.json"
    report.unlink(missing_ok=True)
    wav = ROOT / "samples" / "spoken_test.wav"
    cmd = [str(exe), "--self-test", "--report", str(report)]
    if wav.exists():
        cmd += ["--audio", str(wav)]
    t0 = time.time()
    try:
        subprocess.run(cmd, timeout=timeout_s, capture_output=True)
    except subprocess.TimeoutExpired:
        record(name, "FAIL", f"no result after {timeout_s}s")
        return
    if not report.exists():
        record(name, "FAIL", "the exe wrote no self-test report")
        return
    data = json.loads(report.read_text(encoding="utf-8"))
    report.unlink(missing_ok=True)
    bad = [r for r in data["results"] if r["status"] == "fail" and r.get("required", True)]
    skipped = [r["name"] for r in data["results"] if r["status"] == "skip"]
    detail = (f"{data['passed']} passed, {data['failed']} failed, {data['skipped']} skipped "
              f"in {time.time() - t0:.0f}s (frozen={data.get('frozen')})")
    if skipped:
        detail += " — skipped: " + ", ".join(skipped)
    if bad:
        detail += "\n" + "\n".join(f"      FAIL {r['name']}: {r['detail']}" for r in bad)
    crashed = _windows_crashes(exe, t0)
    if crashed:
        detail += f"\n      FAIL the exe crashed while exiting (Windows logged: {crashed[0]})"
    record(name, "PASS" if data.get("ok") and not bad and data.get("frozen") and not crashed
           else "FAIL", detail)


# ---------------------------------------------------------------------------
# Built-app launch + quit (Windows)
# ---------------------------------------------------------------------------
def _process_windows(pid: int) -> list[tuple[int, str, int, int]]:
    """Visible top-level windows owned by pid: (hwnd, title, width, height)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: list[tuple[int, str, int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((int(hwnd or 0), buf.value, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(_cb, 0)
    return found


def _is_main_window(win) -> bool:
    _, title, w, h = win
    # The main window is large; the "already running" box and the crash dialog
    # carry the same app title but are small dialogs.
    return "MICO360" in title and w >= 700 and h >= 450


def check_built_app(exe: Path, wait_s: int = 45) -> None:
    name = "Built-app launch + quit"
    if sys.platform != "win32":
        record(name, "SKIP", "Windows only")
        return
    root = Path(tempfile.mkdtemp(prefix="mico360_t_launch_"))
    data = root / "MICO360Meetings"
    (data / "logs").mkdir(parents=True)
    # Fresh, isolated data folder; skip the first-run wizard so it can't block.
    (data / "settings.json").write_text(json.dumps({"onboarded": True}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    env["LOCALAPPDATA"] = str(root)
    env["XDG_DATA_HOME"] = str(root)
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    try:
        main_win, seen = None, []
        while time.time() - t0 < wait_s and proc.poll() is None:
            seen = _process_windows(proc.pid)
            main_win = next((w for w in seen if _is_main_window(w)), None)
            if main_win:
                break
            time.sleep(0.5)
        if main_win:
            time.sleep(3)                    # let deferred startup work run
        shown_after = time.time() - t0

        def _log_text() -> str:
            try:
                return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                                 for p in sorted((data / "logs").glob("*.log")))
            except OSError:
                return ""

        def _crashes() -> list[str]:
            return sorted(p.name for p in (data / "logs").glob("crash_*.txt"))

        problems = []
        log_text = _log_text()
        if proc.poll() is not None:
            problems.append(f"exited during startup (rc={_fmt_rc(proc.returncode)})")
        if re.search(r"already running|another instance", log_text, re.I):
            problems.append("stuck on the 'already running' box - close any running "
                            "MICO360 Meetings (and its tray icon) and re-run")
        if _crashes():
            problems.append(f"crash report written: {_crashes()[0]}")
        if re.search(r"fatal error|Traceback \(most recent", log_text):
            problems.append("errors in the app log")
        if not main_win and not problems:
            problems.append(f"no main window within {wait_s}s (stuck on a dialog?) - "
                            f"visible windows: {[(t, w, h) for _, t, w, h in seen]}")
        if problems:
            record(name, "FAIL", "; ".join(problems))
            return

        # Graceful quit: close the main window like a user would. A non-zero exit
        # code here is the frozen-app teardown crash the source suites cannot see
        # (they end with os._exit).
        import ctypes
        WM_CLOSE = 0x0010
        ctypes.windll.user32.PostMessageW(main_win[0], WM_CLOSE, 0, 0)
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            record(name, "FAIL", f"main window shown in {shown_after:.0f}s, but the app "
                                 "did not exit within 30s of closing it")
            return
        after = _crashes()
        logged = _windows_crashes(exe, t0)
        if logged:
            record(name, "FAIL", f"main window shown in {shown_after:.0f}s, but the app "
                                 f"crashed while exiting (Windows logged: {logged[0]})")
            return
        if rc != 0 or after:
            record(name, "FAIL", f"main window shown in {shown_after:.0f}s, but quitting "
                                 f"ended with exit code {_fmt_rc(rc)}"
                                 + (f" and crash report {after[0]}" if after else ""))
            return
        record(name, "PASS", f"main window shown in {shown_after:.0f}s; "
                             "closed cleanly (exit 0, no crash report)")
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    fast = "--fast" in sys.argv
    print(f"=== MICO360 release gate ({'fast' if fast else 'full'}) ===\n")

    # 1-3: the suites (each isolates its own data folder)
    run_suite("Module tests", "tests/module_tests.py", r"\d+/\d+ module tests passed")
    run_suite("Integration QA", "tests/qa_check.py", r"\d+/\d+ checks passed")
    run_suite("Consistency audit", "tests/consistency_audit.py", r"\d+/\d+ consistency checks OK")
    run_suite("Parser tests", "tests/test_parsers.py", r"PARSERS: \d+/\d+ passed")
    # Meeting isolation (switching meetings / auto-record must never overwrite
    # another meeting). Isolated data folder + fake generator — runs anywhere.
    run_suite("Meeting isolation", "tests/workflow_isolation.py", r"ISOLATION: (\d+)/\1 passed")
    # Release-blocker regressions (remote audio in auto-record, background-thread
    # crashes, pywin32 in builds, recordings kept, multi-installer releases).
    run_suite("Release fixes", "tests/release_fixes.py", r"FIXES: (\d+)/\1 passed")
    # Per-area fix suites (docs/BUG_REPORT.md). Each prints
    # "==== <AREA>: X/Y passed ====" and must pass all of its checks.
    for area, script in (("CAPTURE", "tests/fixes_capture.py"), ("CAPTURE2", "tests/fixes_capture2.py"),
                         ("AI", "tests/fixes_ai.py"),
                         ("DATA", "tests/fixes_data.py"), ("UI", "tests/fixes_ui.py"),
                         ("PDF", "tests/fixes_pdf.py"), ("TEMPLATES", "tests/fixes_templates.py")):
        run_suite(f"Fixes: {area.lower()}", script, rf"==== {area}: (\d+)/\1 passed ====",
                  optional=True)
    if not fast:
        run_suite("E2E workflow", "tests/e2e_workflow.py", r"E2E: \d+/\d+ passed", timeout=600)
    else:
        record("E2E workflow", "SKIP", "--fast (needs Whisper + Ollama)")

    # 4: security — no secret-shaped strings in tracked files
    try:
        p = subprocess.run(["git", "grep", "-lE", SECRET_PATTERN],
                           cwd=ROOT, capture_output=True, text=True)
        if p.returncode not in (0, 1):
            record("Security: secrets in git", "WARN", (p.stderr or "git grep failed").strip()[:200])
        elif p.stdout.strip():
            record("Security: secrets in git", "FAIL", p.stdout.strip()[:200])
        else:
            record("Security: secrets in git", "PASS", "no token/secret patterns in tracked files")
    except Exception as exc:
        record("Security: secrets in git", "WARN", str(exc))
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    record("Security: settings.json untracked",
           "FAIL" if "settings.json" in tracked else "PASS")
    record("Security: build key untracked",
           "FAIL" if "mico360/_build_key.py" in tracked else "PASS")

    # 5: version coherence
    sys.path.insert(0, str(ROOT))
    import mico360
    iss = (ROOT / "build" / "installer.iss").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'AppVersion\s+"([\d.]+)"', iss)
    iss_ver = m.group(1) if m else "?"
    record("Version coherence", "PASS" if iss_ver == mico360.__version__ else "FAIL",
           f"code v{mico360.__version__} vs installer v{iss_ver}")

    # 6: built-app launch + clean quit
    exe = ROOT / "build" / "dist" / "MICO360Meetings" / "MICO360Meetings.exe"
    if fast:
        record("Built-app launch + quit", "SKIP", "--fast")
    elif not exe.exists():
        record("Built-app launch + quit", "WARN", "no build present — run build_all.ps1")
    else:
        check_built_app(exe)
        check_built_selftest(exe)

    # 7: integration liveness (warn-only — environment, not code)
    try:
        from mico360.core.ollama_client import check_status
        st = check_status()
        record("Ollama reachable", "PASS" if st.running else "WARN",
               f"{len(st.models)} model(s)" if st.running else "not running")
    except Exception as exc:
        record("Ollama reachable", "WARN", str(exc)[:80])
    try:
        from mico360.config import Settings
        from mico360.core.emailer import SmtpConfig
        cfg = SmtpConfig.from_settings(Settings())
        record("Email configured", "PASS" if cfg.configured else "WARN",
               cfg.host if cfg.configured else "not set (Settings → Email)")
    except Exception as exc:
        record("Email configured", "WARN", str(exc)[:80])

    # verdict
    fails = [r for r in results if r[1] == "FAIL"]
    warns = [r for r in results if r[1] == "WARN"]
    skips = [r for r in results if r[1] == "SKIP"]
    print("\n" + "=" * 56)
    print(f"RESULT: {'READY' if not fails else 'NOT READY'} — "
          f"{sum(1 for r in results if r[1]=='PASS')} pass, {len(fails)} fail, "
          f"{len(warns)} warn, {len(skips)} skipped")
    for n, s, d in fails:
        print(f"  FAIL: {n} — {d.splitlines()[0] if d else ''}")
    for n, s, d in warns:
        print(f"  warn: {n} — {d}")
    for n, s, d in skips:
        print(f"  skip: {n} — {d}")
    print("Manual step still required before tagging: docs/PRERELEASE_CHECKLIST.md")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
