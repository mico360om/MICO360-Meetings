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

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .. import __app_name__, __version__

log = logging.getLogger("mico360.updater")


class IntegrityError(Exception):
    """Raised when a downloaded update fails checksum or signature verification."""

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
    checksum_url: str = ""          # SHA256SUMS / .sha256 asset, if published
    checksum_per_file: bool = False # checksum_url is "<installer>.sha256" (bare hash ok)
    expected_sha256: str = ""       # hash for THIS installer named in the release body
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
    """Comparable key for 'v1.2.3', '1.3.0-rc1', '1.10'.

    Only the numeric release core (up to 4 parts) is compared; a pre-release
    suffix ('-rc1', '-beta') ranks *below* the same final release, so users on
    1.3.0-rc1 are still offered 1.3.0 (previously '-rc1' parsed as an extra
    version part and looked newer).
    """
    s = (v or "").strip().lstrip("vV")
    core, sep, _pre = s.partition("-")
    nums = tuple(int(n) for n in re.findall(r"\d+", core)[:4]) or (0,)
    nums = nums + (0,) * (4 - len(nums))            # 1.2 == 1.2.0
    return nums + ((0,) if sep else (1,))           # final > pre-release


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def repo_url(repo: str) -> str:
    """GitHub URL for a repo, falling back to the app's default repository."""
    repo = (repo or "").strip().strip("/")
    if not repo:
        from ..config import DEFAULT_REPO
        repo = DEFAULT_REPO.strip().strip("/")
    return f"https://github.com/{repo}" if repo else ""


# --- integrity verification -------------------------------------------------
_SHA256_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")
# checksum asset names we recognise (besides a per-file "<installer>.sha256")
_CHECKSUM_NAMES = {"sha256sums", "sha256sums.txt", "checksums.txt",
                   "checksums.sha256", "sha256sum.txt"}
# Authenticode statuses that unambiguously mean a signed file failed validation
# — a strong tamper signal. HashMismatch = signed bytes were altered; NotTrusted =
# signed by an untrusted/revoked certificate. Other statuses (NotSigned,
# NotSupportedFileFormat, UnknownError) just mean "no verifiable signature" and are
# allowed — SHA256 is the real integrity gate until code-signing is in place.
_BAD_SIGNATURE = {"HashMismatch", "NotTrusted"}


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """SHA256 hex digest of a file, read in chunks (handles large installers)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def parse_checksum(text: str, filename: str = "", allow_bare: bool = False) -> str:
    """Pull the SHA256 for ``filename`` out of a checksums file or release body.

    With a filename, only a line naming that exact file counts (``<hash>  <file>``
    / ``<hash> *<file>`` / "<file> … <hash>"). A hash that doesn't name the file
    is never attributed to it — a release listing several installers once had
    its notes' hash picked for the wrong file, failing every update as
    "tampered". ``allow_bare`` accepts a lone hash (a per-file ``<file>.sha256``
    asset, which holds only the digest). Without a filename, the first
    standalone 64-hex token is returned.
    """
    if not text:
        return ""
    fn = (filename or "").lower()
    if fn:
        name_re = re.compile(r"(?<![\w.-])\*?" + re.escape(fn) + r"(?![\w.-])", re.I)
        for line in text.splitlines():
            if name_re.search(line):
                m = _SHA256_RE.search(line)
                if m:
                    return m.group(1).lower()
        if not allow_bare:
            return ""
        hashes = {h.lower() for h in _SHA256_RE.findall(text)}
        return hashes.pop() if len(hashes) == 1 else ""
    m = _SHA256_RE.search(text)
    return m.group(1).lower() if m else ""


def fetch_text(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"{__app_name__}/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def resolve_expected_sha256(info: "UpdateInfo") -> str:
    """Expected hash for the installer being downloaded.

    The checksum asset published next to the installer is authoritative; a hash
    in the release notes is used only if it names this exact file and no asset
    is available.
    """
    name = Path(info.download_url).name if info.download_url else ""
    if info.checksum_url:
        try:
            txt = fetch_text(info.checksum_url)
        except Exception:
            log.warning("could not fetch checksum asset", exc_info=True)
            txt = ""
        found = parse_checksum(txt, name, allow_bare=info.checksum_per_file)
        if found:
            return found
    return info.expected_sha256.lower() if info.expected_sha256 else ""


def authenticode_status(path: str | Path) -> str:
    """Windows Authenticode signature status ('Valid', 'NotSigned', 'HashMismatch',
    …). Returns 'Unsupported' off Windows and 'Unknown' if it can't be determined."""
    if sys.platform != "win32":
        return "Unsupported"
    try:
        env = {**os.environ, "MICO_VERIFY_PATH": str(path)}
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-AuthenticodeSignature -LiteralPath $env:MICO_VERIFY_PATH).Status"],
            capture_output=True, text=True, timeout=30, env=env)
        out = (r.stdout or "").strip()
        return out.splitlines()[-1].strip() if out else "Unknown"
    except Exception:
        log.warning("authenticode check failed", exc_info=True)
        return "Unknown"


def verify_download(path: str | Path, expected_sha256: str = "") -> tuple[str, str]:
    """Verify a downloaded installer before it is ever executed.

    Enforces the published SHA256 (if any) and rejects a present-but-invalid
    Authenticode signature. Raises :class:`IntegrityError` on any tamper signal.
    Returns ``(note, signature_status)`` describing what was checked.
    """
    notes: list[str] = []
    actual = sha256_file(path)
    if expected_sha256:
        if actual.lower() != expected_sha256.lower():
            raise IntegrityError(
                "Checksum mismatch — the downloaded file does not match the published "
                f"SHA256 (expected {expected_sha256[:12]}…, got {actual[:12]}…). "
                "The file may be corrupted or tampered with and will not be run.")
        notes.append("SHA256 verified")
    else:
        notes.append("no published checksum")

    status = authenticode_status(path)
    if status == "Valid":
        notes.append("signature valid")
    elif status in _BAD_SIGNATURE:
        raise IntegrityError(
            f"The installer's digital signature is invalid ({status}). "
            "It will not be run.")
    elif status in ("NotSigned", "NotSupportedFileFormat", "UnknownError"):
        notes.append("unsigned installer")
    # 'Unsupported' (non-Windows) / 'Unknown' → can't judge; SHA256 is the gate.
    return ", ".join(notes), status


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


def _pick_assets(assets: list, version: str) -> tuple[str, int, str, bool]:
    """Choose the installer and its checksum asset from a release's assets.

    Returns ``(download_url, size, checksum_url, checksum_is_per_file)``. The
    choice never depends on the order GitHub lists assets in: if a release
    carries more than one installer, the one whose name contains the release
    version wins, then one that has its own ``<name>.sha256``. The checksum is
    that installer's own ``.sha256`` if present, else a SHA256SUMS-style file.
    """
    by_name = {(a.get("name") or "").lower(): a for a in assets}
    installers = [a for a in assets
                  if (a.get("name") or "").lower().endswith((".exe", ".msi", ".zip"))]
    if not installers:
        return "", 0, "", False
    ver = (version or "").lower().lstrip("v")

    def rank(a):
        n = (a.get("name") or "").lower()
        return (0 if ver and ver in n else 1, 0 if n + ".sha256" in by_name else 1)

    pick = sorted(installers, key=rank)[0]          # stable: ties keep API order
    pname = (pick.get("name") or "").lower()
    own = by_name.get(pname + ".sha256")
    if own:
        return (pick.get("browser_download_url", ""), int(pick.get("size", 0)),
                own.get("browser_download_url", ""), True)
    sums = next((a for n, a in by_name.items() if n in _CHECKSUM_NAMES), None)
    return (pick.get("browser_download_url", ""), int(pick.get("size", 0)),
            sums.get("browser_download_url", "") if sums else "", False)


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

    info.download_url, info.size_bytes, info.checksum_url, info.checksum_per_file =         _pick_assets(data.get("assets", []), info.latest_version)
    # a checksum in the release body counts only if it names this exact file
    info.expected_sha256 = parse_checksum(
        data.get("body", ""), Path(info.download_url).name if info.download_url else "")

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
