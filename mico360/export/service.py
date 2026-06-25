"""Single entry point for exporting minutes by file extension."""
from __future__ import annotations

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


def export(minutes_md: str, path: str | Path,
           profile: CompanyProfile | None = None) -> Path:
    path = Path(path)
    fn = EXPORTERS.get(path.suffix.lower())
    if not fn:
        raise ValueError(f"Unsupported export type: {path.suffix}")
    return fn(minutes_md, path, profile)
