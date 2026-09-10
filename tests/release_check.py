"""Release-readiness gate — ONE command that runs everything automatable.

    python tests/release_check.py            # full gate (suites + app + security)
    python tests/release_check.py --fast     # suites only (skip built-app launch)

Runs, in order:
  1. Module tests           (tests/module_tests.py)
  2. Integration QA         (tests/qa_check.py)
  3. Consistency audit      (tests/consistency_audit.py)
  4. Security scan          (no secrets in tracked files / source tree)
  5. Version coherence      (mico360.__version__ == installer.iss AppVersion)
  6. Built-app launch       (build/dist exe starts and stays up)   [skipped with --fast]
  7. Integration liveness   (Ollama reachable; email configured — WARN only)

Exits 0 only if every REQUIRED step passes. WARN steps never fail the gate.
Reminder: the manual pass in docs/PRERELEASE_CHECKLIST.md is still required
before tagging — this gate cannot test real microphones or a clean machine.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
ENV = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONUTF8": "1",
       "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}

results: list[tuple[str, str, str]] = []      # (name, PASS/FAIL/WARN/SKIP, detail)


def record(name, status, detail=""):
    results.append((name, status, detail))
    print(f"  [{status:4}] {name}  {detail}")


def run_suite(name, script, pattern, timeout=360):
    t0 = time.time()
    try:
        p = subprocess.run([PY, "-u", str(ROOT / script)], env=ENV, cwd=ROOT,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        m = re.search(pattern, out)
        took = f"{time.time()-t0:.0f}s"
        if p.returncode == 0 and m:
            record(name, "PASS", f"{m.group(0)} in {took}")
        else:
            tail = "\n".join(out.strip().splitlines()[-5:])
            record(name, "FAIL", f"rc={p.returncode}; last lines:\n{tail}")
    except subprocess.TimeoutExpired:
        record(name, "FAIL", f"timeout after {timeout}s")


def main() -> int:
    fast = "--fast" in sys.argv
    print(f"=== MICO360 release gate ({'fast' if fast else 'full'}) ===\n")

    # 1-3: the three suites
    run_suite("Module tests", "tests/module_tests.py", r"\d+/\d+ module tests passed")
    run_suite("Integration QA", "tests/qa_check.py", r"\d+/\d+ checks passed")
    run_suite("Consistency audit", "tests/consistency_audit.py", r"\d+/\d+ consistency checks OK")
    run_suite("Parser tests", "tests/test_parsers.py", r"PARSERS: \d+/\d+ passed")
    if not fast:
        run_suite("E2E workflow", "tests/e2e_workflow.py", r"E2E: \d+/\d+ passed", timeout=600)
    else:
        record("E2E workflow", "SKIP", "--fast (needs Whisper + Ollama)")

    # 4: security — no secret-shaped strings in tracked files
    try:
        p = subprocess.run(["git", "grep", "-lE",
                            r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
                            r"[0-9a-f]{32}['\"]?\s*(#|$).*(secret|api.?key)|smtp_password.\s*:\s*['\"][^'\"]{8,}"],
                           cwd=ROOT, capture_output=True, text=True)
        if p.stdout.strip():
            record("Security: secrets in git", "FAIL", p.stdout.strip()[:200])
        else:
            record("Security: secrets in git", "PASS", "no token/secret patterns in tracked files")
    except Exception as exc:
        record("Security: secrets in git", "WARN", str(exc))
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    record("Security: settings.json untracked",
           "FAIL" if "settings.json" in tracked else "PASS")

    # 5: version coherence
    sys.path.insert(0, str(ROOT))
    import mico360
    iss = (ROOT / "build" / "installer.iss").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'AppVersion\s+"([\d.]+)"', iss)
    iss_ver = m.group(1) if m else "?"
    record("Version coherence", "PASS" if iss_ver == mico360.__version__ else "FAIL",
           f"code v{mico360.__version__} vs installer v{iss_ver}")

    # 6: built-app launch
    exe = ROOT / "build" / "dist" / "MICO360Meetings" / "MICO360Meetings.exe"
    if fast:
        record("Built-app launch", "SKIP", "--fast")
    elif not exe.exists():
        record("Built-app launch", "WARN", "no build present — run build_all.ps1")
    else:
        proc = subprocess.Popen([str(exe)], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(10)
        alive = proc.poll() is None
        if alive:
            proc.terminate()
        record("Built-app launch", "PASS" if alive else "FAIL",
               "stayed up 10s" if alive else f"exited rc={proc.poll()}")

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
    print("\n" + "=" * 56)
    print(f"RESULT: {'READY' if not fails else 'NOT READY'} — "
          f"{sum(1 for r in results if r[1]=='PASS')} pass, {len(fails)} fail, {len(warns)} warn")
    for n, s, d in fails:
        print(f"  FAIL: {n} — {d.splitlines()[0] if d else ''}")
    for n, s, d in warns:
        print(f"  warn: {n} — {d}")
    print("Manual step still required before tagging: docs/PRERELEASE_CHECKLIST.md")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
