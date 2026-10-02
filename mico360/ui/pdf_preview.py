"""True previews of the PDF designs: the real exporter writes a sample set of
minutes with the company's branding and the page is rendered with Qt's PDF
module — so what the picker shows is what an export will look like.

If Qt's PDF module isn't available the callers fall back to a text description
(nothing else depends on it).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from ..config import TMP_DIR
from ..core.profiles import CompanyProfile

log = logging.getLogger("mico360.pdf_preview")

SAMPLE_MINUTES = """# Meeting Minutes
**Meeting Title:** Quarterly Planning Review
**Date and Time:** 12 March 2026, 10:00 - 11:00
**Location:** Board Room 2 / Online
**Attendees:** Aisha Al-Balushi (Chair), John Smith, Fatima Al-Harthy, Rahul Menon, Sara Lopez

## Purpose of Meeting
Review progress against the quarterly plan and agree the priorities for next quarter.

## Key Discussion Points
- Delivery is on track; two workstreams need extra support.
- The revised budget was reviewed and accepted.

## Decisions Made
1. The revised budget is approved.
2. The migration moves to the weekend of 21 March.

## Action Items
| Task | Responsible Person | Deadline | Status |
| --- | --- | --- | --- |
| Circulate the revised delivery plan | John Smith | 19 March | In Progress |
| Confirm vendor delivery dates | Fatima Al-Harthy | 21 March | Pending |
| Close the Q1 risk review | Rahul Menon | 14 March | Completed |

## Next Meeting Notes
Next review on 9 April 2026 at 10:00.
"""


def available() -> bool:
    try:
        from PySide6.QtPdf import QPdfDocument  # noqa: F401
        return True
    except Exception:
        return False


def render_pdf_page(pdf_path: str | Path, width_px: int, page: int = 0) -> QImage | None:
    """Page `page` of a PDF as an opaque image `width_px` wide (None on failure)."""
    try:
        from PySide6.QtPdf import QPdfDocument
        doc = QPdfDocument(None)
        try:
            if doc.load(str(pdf_path)) != QPdfDocument.Error.None_ or doc.pageCount() <= page:
                return None
            size = doc.pagePointSize(page)
            if size.width() <= 0:
                return None
            height_px = max(1, round(width_px * size.height() / size.width()))
            raw = doc.render(page, QSize(int(width_px), int(height_px)))
        finally:
            doc.close()
        if raw.isNull():
            return None
        out = QImage(raw.size(), QImage.Format_RGB32)       # PDF pages render transparent
        out.fill(QColor("#FFFFFF"))
        painter = QPainter(out)
        painter.drawImage(0, 0, raw)
        painter.end()
        return out
    except Exception:
        log.warning("PDF preview failed", exc_info=True)
        return None


def render_design(profile: CompanyProfile | None, design: str, width_px: int = 420) -> QImage | None:
    """Page 1 of the sample minutes in `design`, with `profile`'s branding."""
    from ..export.pdf_export import export_pdf
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="preview_", dir=str(TMP_DIR)) as tmp:
            pdf = Path(tmp) / "preview.pdf"
            export_pdf(SAMPLE_MINUTES, pdf, profile, design=design)
            return render_pdf_page(pdf, width_px)
    except Exception:
        log.warning("design preview failed (%s)", design, exc_info=True)
        return None


def scaled(image: QImage, width: int) -> QImage:
    return image.scaledToWidth(int(width), Qt.SmoothTransformation)
