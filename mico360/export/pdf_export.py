"""PDF export (reportlab) with company letterhead, logo, footer and page numbers.

A two-pass canvas is used so "{n} of {total}" page numbering is accurate.
The logo keeps its aspect ratio and is drawn at the configured printed width.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.pdfgen import canvas as _canvas

from ..core.profiles import CompanyProfile
from . import md_blocks

_ALIGN = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT}


def _styles(accent: str):
    ss = getSampleStyleSheet()
    accent_color = colors.HexColor(accent if accent.startswith("#") else "#8B1E1E")
    styles = {
        "title": ParagraphStyle("m_title", parent=ss["Title"], fontSize=18,
                                 textColor=accent_color, spaceAfter=10),
        "h2": ParagraphStyle("m_h2", parent=ss["Heading2"], fontSize=12.5,
                             textColor=accent_color, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("m_body", parent=ss["BodyText"], fontSize=10.5,
                               leading=15, spaceAfter=4),
        "kv": ParagraphStyle("m_kv", parent=ss["BodyText"], fontSize=10.5, leading=15),
        "bullet": ParagraphStyle("m_bullet", parent=ss["BodyText"], fontSize=10.5,
                                 leading=15, leftIndent=12, bulletIndent=2, spaceAfter=2),
        "cell": ParagraphStyle("m_cell", parent=ss["BodyText"], fontSize=9, leading=12),
        "cellh": ParagraphStyle("m_cellh", parent=ss["BodyText"], fontSize=9,
                                leading=12, textColor=colors.white),
    }
    return styles, accent_color


def _inline(text: str) -> str:
    """Convert **bold** runs to reportlab <b> markup, escaping the rest."""
    out = []
    for txt, bold in md_blocks.runs(text):
        safe = (txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        out.append(f"<b>{safe}</b>" if bold else safe)
    return "".join(out)


def export_pdf(minutes_md: str, path: str | Path,
               profile: CompanyProfile | None = None) -> Path:
    path = Path(path)
    accent = profile.accent_color if profile else "#8B1E1E"
    styles, accent_color = _styles(accent)

    # margins leave room for the letterhead/footer drawn by the canvas callback
    top_margin = 38 * mm if profile else 20 * mm
    bottom_margin = 22 * mm if profile else 18 * mm

    flow = []
    for blk in md_blocks.parse(minutes_md):
        if blk.kind == "h1":
            flow.append(Paragraph(_inline(blk.text), styles["title"]))
        elif blk.kind == "h2":
            flow.append(Paragraph(_inline(blk.text), styles["h2"]))
        elif blk.kind == "kv":
            flow.append(Paragraph(f"<b>{blk.key}:</b> {_inline(blk.text)}", styles["kv"]))
        elif blk.kind == "bullet":
            for it in blk.items:
                flow.append(Paragraph(_inline(it), styles["bullet"], bulletText="•"))
        elif blk.kind == "para":
            flow.append(Paragraph(_inline(blk.text), styles["body"]))
        elif blk.kind == "table":
            data = [[Paragraph(_inline(h), styles["cellh"]) for h in blk.headers]]
            for row in blk.rows:
                cells = [row[j] if j < len(row) else "" for j in range(len(blk.headers))]
                data.append([Paragraph(_inline(c), styles["cell"]) for c in cells])
            tbl = Table(data, repeatRows=1, hAlign="LEFT")
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), accent_color),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FB")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ]))
            flow.append(Spacer(1, 4))
            flow.append(tbl)
            flow.append(Spacer(1, 6))

    def _decorate(canvas, doc):
        _draw_letterhead(canvas, doc, profile, accent_color)

    page_w, page_h = A4
    frame = Frame(18 * mm, bottom_margin, page_w - 36 * mm,
                  page_h - top_margin - bottom_margin, id="body")
    template = PageTemplate(id="main", frames=[frame], onPage=_decorate)

    doc = BaseDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=top_margin, bottomMargin=bottom_margin,
        title="Meeting Minutes",
    )
    doc.addPageTemplates([template])
    doc.build(flow, canvasmaker=_NumberedCanvas.factory(profile))
    return path


def _draw_letterhead(canvas, doc, profile, accent_color):
    if not profile:
        return
    page_w, page_h = A4

    # logo
    if profile.logo_path and Path(profile.logo_path).exists():
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(profile.logo_path)
            iw, ih = img.getSize()
            w = profile.logo_width_mm * mm
            h = w * (ih / iw) if iw else w
            h = min(h, 22 * mm)
            w = h * (iw / ih) if ih else w
            pos = profile.logo_position
            if pos == "center":
                x = (page_w - w) / 2
            elif pos == "right":
                x = page_w - 18 * mm - w
            else:
                x = 18 * mm
            canvas.drawImage(img, x, page_h - 14 * mm - h, width=w, height=h,
                             mask="auto", preserveAspectRatio=True)
        except Exception:
            pass

    # company name + contact line (top-right when logo is left, else left)
    canvas.saveState()
    canvas.setFillColor(accent_color)
    canvas.setFont("Helvetica-Bold", 12)
    text_right = profile.logo_position != "right"
    tx = page_w - 18 * mm if text_right else 18 * mm
    if text_right:
        canvas.drawRightString(tx, page_h - 16 * mm, profile.name)
    else:
        canvas.drawString(tx, page_h - 16 * mm, profile.name)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.setFont("Helvetica", 7.5)
    bits = [b for b in (profile.address, profile.phone, profile.email, profile.website) if b]
    line = "   ".join(bits)
    if line:
        if text_right:
            canvas.drawRightString(tx, page_h - 20 * mm, line)
        else:
            canvas.drawString(tx, page_h - 20 * mm, line)
    # divider rule
    canvas.setStrokeColor(accent_color)
    canvas.setLineWidth(0.8)
    canvas.line(18 * mm, page_h - 24 * mm, page_w - 18 * mm, page_h - 24 * mm)
    canvas.restoreState()


class _NumberedCanvas(_canvas.Canvas):
    """Two-pass canvas so total page count is known when drawing footers."""
    _profile = None

    @classmethod
    def factory(cls, profile):
        return type("NC", (cls,), {"_profile": profile})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total):
        profile = self._profile
        page_w, page_h = A4
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#888888"))
        if profile and profile.footer_text:
            align = profile.footer_alignment
            if align == "left":
                self.drawString(18 * mm, 12 * mm, profile.footer_text)
            elif align == "right":
                self.drawRightString(page_w - 18 * mm, 12 * mm, profile.footer_text)
            else:
                self.drawCentredString(page_w / 2, 12 * mm, profile.footer_text)
        if not profile or profile.show_page_numbers:
            fmt = (profile.page_number_format if profile else "Page {n} of {total}")
            label = fmt.replace("{n}", str(self._pageNumber)).replace("{total}", str(total))
            pos = profile.page_number_position if profile else "footer-right"
            y = 8 * mm if "footer" in pos else page_h - 8 * mm
            if pos.endswith("left"):
                self.drawString(18 * mm, y, label)
            elif pos.endswith("center"):
                self.drawCentredString(page_w / 2, y, label)
            else:
                self.drawRightString(page_w - 18 * mm, y, label)
