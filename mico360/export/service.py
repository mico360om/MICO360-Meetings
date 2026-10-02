"""Single entry point for exporting minutes by file extension."""
from __future__ import annotations

import errno
import os
import re
import uuid
from pathlib import Path

from ..core.profiles import CompanyProfile
from .docx_export import export_docx
from .html_export import export_html, export_md
from .pdf_export import export_pdf
from .txt_export import export_txt

EXPORTERS = {
    ".txt": export_txt,
    ".docx": export_docx,
    ".pdf": export_pdf,
    ".html": export_html,
    ".md": export_md,
}

FILTERS = ("Word Document (*.docx);;PDF Document (*.pdf);;Text File (*.txt);;"
           "Markdown (*.md);;HTML Page (*.html)")

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


class ExportError(Exception):
    """An export failure whose message is written for the user (shown as-is)."""


def safe_filename(name: str, fallback: str = "Meeting-Minutes", max_len: int = 100) -> str:
    """A Windows-safe file name (no extension) from a meeting title."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name or "")
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    s = s[:max_len].rstrip(". ")
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    return s or fallback


def _discard(tmp: Path) -> None:
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


def export(minutes_md: str, path: str | Path,
           profile: CompanyProfile | None = None, design: str | None = None) -> Path:
    """Write the minutes to `path` (format from its extension).

    A PDF uses the company's own design (`profile.pdf_design`); `design`
    overrides it — for exports without a company profile, and for previews.

    The file is built under a temporary name in the same folder and then moved
    over the target in one step, so a failed export never leaves a half-written
    file or destroys the previous one. Errors the user can act on (file open in
    a PDF viewer, read-only folder, disk full) raise ExportError with a plain
    explanation."""
    path = Path(path)
    fn = EXPORTERS.get(path.suffix.lower())
    if not fn:
        raise ValueError(f"Unsupported export type: {path.suffix}")
    folder = path.parent
    if not folder.is_dir():
        raise ExportError(f"The folder “{folder}” doesn't exist. Choose another folder.")
    tmp = folder / f".{path.stem[:60]}.{uuid.uuid4().hex[:8]}.tmp{path.suffix}"
    try:
        if design and path.suffix.lower() == ".pdf":
            fn(minutes_md, tmp, profile, design=design)
        else:
            fn(minutes_md, tmp, profile)
    except PermissionError as exc:
        _discard(tmp)
        raise ExportError(f"You don't have permission to save files in “{folder}”. "
                          "Choose another folder, such as Documents.") from exc
    except OSError as exc:
        _discard(tmp)
        if exc.errno == errno.ENOSPC:
            raise ExportError("There isn't enough free disk space to save the file.") from exc
        raise ExportError(f"Couldn't write the file: {exc.strerror or exc}") from exc
    except Exception:
        _discard(tmp)
        raise
    try:
        os.replace(tmp, path)
    except PermissionError as exc:
        _discard(tmp)
        if path.exists() and not os.access(path, os.W_OK):
            raise ExportError(f"“{path.name}” is read-only. Save under a different name.") from exc
        raise ExportError(f"“{path.name}” is open in another program (for example a PDF "
                          "viewer or Word). Close it and export again, or save under a "
                          "different name.") from exc
    except OSError as exc:
        _discard(tmp)
        raise ExportError(f"Couldn't save “{path.name}”: {exc.strerror or exc}") from exc
    return path
