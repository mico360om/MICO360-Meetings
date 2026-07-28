"""Application update checking against GitHub Releases.

Produces a rich UpdateInfo with everything the UI needs to show: current and
latest version, status, size, release date, description, and parsed lists of
new features / bug fixes / security improvements. Network access is via the
standard library only (no extra dependency); failures are reported clearly.

The GitHub repo is configurable (the user supplies it later) via settings key
``github_repo`` in the form "owner/name". Until then the repo link/button still
works and checks degrade to a clear "not configured" message.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .. import __app_name__, __version__

log = logging.getLogger("mico360.updater")

# Update status values
AVAILABLE, UP_TO_DATE, CHECKING, DOWNLOADING, INSTALLING, COMPLETED, FAILED, NOT_CONFIGURED = (
    "Available", "Up to date", "Checking", "Downloading", "Installing",
    "Completed", "Failed", "Not configured")


@dataclass
class UpdateInfo:
    app_name: str = __app_name__
    current_version: str = __version__
    latest_version: str = ""
    status: str = CHECKING
    size_bytes: int = 0
    release_date: str = ""
    description: str = ""
    features: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    security: list[str] = field(default_factory=list)
    download_url: str = ""          # direct asset (.exe) if present
    release_url: str = ""           # human release page
    repo_url: str = ""              # repository home
    restart_required: bool = True
    error: str = ""

    @property
    def update_available(self) -> bool:
        return self.status == AVAILABLE

    @property
    def size_human(self) -> str:
        f = float(self.size_bytes)
        for u in ("B", "KB", "MB", "GB"):
            if f < 1024 or u == "GB":
                return f"{f:.0f} {u}" if u == "B" else f"{f:.1f} {u}"
            f /= 1024
        return f"{f:.1f} GB"


def _parse_version(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def repo_url(repo: str) -> str:
    """GitHub URL for a repo, falling back to the app's default repository."""
    repo = (repo or "").strip().strip("/")
    if not repo:
        from ..config import DEFAULT_REPO
        repo = DEFAULT_REPO.strip().strip("/")
    return f"https://github.com/{repo}" if repo else ""


def _classify_body(body: str) -> tuple[list[str], list[str], list[str], str]:
    """Split a release body (markdown) into features / fixes / security + summary."""
    features: list[str] = []
    fixes: list[str] = []
    security: list[str] = []
    summary_lines: list[str] = []
    bucket = summary_lines
    for raw in (body or "").splitlines():
        line = raw.strip()
        low = line.lower().lstrip("#* -")
        if line.startswith("#") or re.match(r"^\*\*.+\*\*$", line):
            if any(k in low for k in ("feature", "new", "added", "added")):
                bucket = features
            elif any(k in low for k in ("fix", "bug", "patch")):
                bucket = fixes
            elif any(k in low for k in ("security", "vuln", "cve")):
                bucket = security
            else:
                bucket = summary_lines
            continue
        if line.startswith(("- ", "* ", "+ ")):
            item = line[2:].strip()
            if item:
                (bucket if bucket is not summary_lines else features).append(item)
        elif line:
            summary_lines.append(line)
    summary = " ".join(summary_lines).strip()
    return features, fixes, security, summary


def check_for_updates(repo: str, timeout: float = 8.0) -> UpdateInfo:
    info = UpdateInfo(repo_url=repo_url(repo))
    repo = (repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        info.status = NOT_CONFIGURED
        info.error = ("GitHub repository is not configured yet. Add it in Settings "
                      "(owner/name) to enable automatic update checks.")
        return info

    api = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(api, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{__app_name__}/{__version__}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        info.status = FAILED
        if exc.code == 404:
            info.error = ("No releases found for this repository yet "
                          f"({repo}). Publish a release to enable updates.")
        else:
            info.error = f"GitHub returned HTTP {exc.code}."
        return info
    except Exception as exc:
        info.status = FAILED
        info.error = f"Could not reach GitHub: {exc}. Check your internet connection."
        return info

    info.latest_version = (data.get("tag_name") or data.get("name") or "").lstrip("vV")
    info.release_date = (data.get("published_at") or "")[:10]
    info.release_url = data.get("html_url", "")
    feats, fixes, sec, summary = _classify_body(data.get("body", ""))
    info.features, info.fixes, info.security = feats, fixes, sec
    info.description = summary or (data.get("name") or "")

    # find a Windows installer/exe asset for the size + direct download
    for asset in data.get("assets", []):
        name = (asset.get("name") or "").lower()
        if name.endswith((".exe", ".msi", ".zip")):
            info.size_bytes = int(asset.get("size", 0))
            info.download_url = asset.get("browser_download_url", "")
            break

    if info.latest_version and is_newer(info.latest_version, __version__):
        info.status = AVAILABLE
    else:
        info.status = UP_TO_DATE
    return info


def download_asset(url: str, dest: str, progress=None, cancel=None) -> str:
    """Download an update asset to dest, reporting progress(0..1, bytes, total)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"{__app_name__}/{__version__}"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length", 0))
        read = 0
        while True:
            if cancel and cancel():
                raise InterruptedError("Download cancelled.")
            chunk = resp.read(65536)
            if not chunk:
                break
            fh.write(chunk)
            read += len(chunk)
            if progress:
                progress(read / total if total else 0.0, read, total)
    return dest
