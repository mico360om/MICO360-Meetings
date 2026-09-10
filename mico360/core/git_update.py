"""Git-based self-update for source / developer checkouts.

When the app runs from a git working copy (not the packaged installer build), it
can update itself with ``git pull --ff-only`` instead of downloading a release.
Everything is fast-forward only, so local commits/changes are never clobbered —
a divergent or dirty tree makes the pull fail cleanly with a readable message.

The packaged (PyInstaller) build has no ``.git``, so is_git_checkout() is False
there and the UI falls back to the GitHub-release updater.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("mico360.gitupdate")


def repo_root() -> Path | None:
    """The project root if it's a git working copy, else None."""
    try:
        import mico360
        root = Path(mico360.__file__).resolve().parent.parent
        return root if (root / ".git").exists() else None
    except Exception:
        return None


def is_git_checkout() -> bool:
    """True when git is available AND the app runs from a checkout."""
    return shutil.which("git") is not None and repo_root() is not None


def _run(root: Path, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=timeout)


def current_commit() -> str:
    """Short hash of HEAD, or '' if unavailable."""
    root = repo_root()
    if not root:
        return ""
    try:
        r = _run(root, "rev-parse", "--short", "HEAD", timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def check_update() -> dict:
    """Fetch and report how many commits behind the upstream branch we are.

    Returns {ok, behind, branch, commit} or {ok:False, error}.
    """
    root = repo_root()
    if not root:
        return {"ok": False, "error": "This build is not a git checkout."}
    try:
        f = _run(root, "fetch", "--quiet", timeout=60)
        if f.returncode != 0:
            return {"ok": False, "error": f.stderr.strip() or "git fetch failed."}
        c = _run(root, "rev-list", "--count", "HEAD..@{u}", timeout=15)
        if c.returncode != 0:
            return {"ok": False,
                    "error": "No upstream branch is configured (git branch -u origin/<branch>)."}
        behind = int((c.stdout.strip() or "0"))
        branch = _run(root, "rev-parse", "--abbrev-ref", "HEAD", timeout=10).stdout.strip()
        return {"ok": True, "behind": behind, "branch": branch, "commit": current_commit()}
    except Exception as exc:
        log.warning("git check failed", exc_info=True)
        return {"ok": False, "error": str(exc)}


def pull_update() -> dict:
    """Fast-forward the checkout to the upstream. Returns {ok, updated, commit,
    message} or {ok:False, error}. Never overwrites local work (ff-only)."""
    root = repo_root()
    if not root:
        return {"ok": False, "error": "This build is not a git checkout."}
    try:
        r = _run(root, "pull", "--ff-only", timeout=120)
        if r.returncode == 0:
            msg = r.stdout.strip() or "Updated."
            up_to_date = "up to date" in msg.lower() or "up-to-date" in msg.lower()
            return {"ok": True, "updated": not up_to_date, "message": msg,
                    "commit": current_commit()}
        return {"ok": False,
                "error": (r.stderr.strip() or r.stdout.strip()
                          or "git pull failed — commit or stash local changes and try again.")}
    except Exception as exc:
        log.warning("git pull failed", exc_info=True)
        return {"ok": False, "error": str(exc)}
