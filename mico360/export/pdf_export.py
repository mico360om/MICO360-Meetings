"""PDF export (reportlab): a branded, print-ready Minutes of Meeting.

Layout
  * Page 1 carries the company letterhead — logo, company name and contact
    lines, wrapped so nothing runs off the page or under the logo — then a
    title block and a meeting-details panel built from the leading
    **Key:** value lines. Attendee lists become a name grid.
  * Later pages carry a compact running header (company + meeting title).
  * Section headings are kept with the start of their content (a measured
    CondPageBreak, not keepWithNext, which would push a heading AND a long
    table to the next page), so a heading is never stranded at a page bottom.
  * Tables are sized to their content, repeat their header row, and keep each
    row whole across a page break (only a row taller than a whole page may
    split). A Status column is colour-coded.
  * Footer: the profile's footer text (wrapped, never clipped) and
    "Page n of N" (two-pass canvas, so the total is known).

Arabic text is shaped, bidi-ordered and right-aligned; an Arabic document
mirrors the details panel and its tables.
"""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as _canvas
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, NextPageTemplate, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from ..core.profiles import CompanyProfile
from . import md_blocks, rtl

PAGE_W, PAGE_H = A4
MARGIN_X = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X
BOTTOM = 22 * mm                 # body frame bottom (footer lives below it)
HEAD_TOP = 12 * mm               # letterhead starts this far below the page top
LATER_TOP = 22 * mm              # body frame top on pages 2+ (running header above)
PLAIN_TOP = 18 * mm              # body frame top on page 1 without a letterhead

DEFAULT_ACCENT = "#8B1E1E"       # MICO360 brand maroon (ui/theme.py LIGHT accent)
# Warm neutrals from the app's light theme, so the PDF matches the product.
TEXT = colors.HexColor("#221D1D")
MUTED = colors.HexColor("#6C6269")
BORDER = colors.HexColor("#E4DEDC")
PANEL = colors.HexColor("#F7F4F3")
BAND = colors.HexColor("#FBF9F8")
STATUS_COLORS = {
    "Completed": "#16A34A", "In Progress": "#B8760F", "Pending": "#6C6269",
    "Cancelled": "#8A8088", "Overdue": "#DC2626", "Blocked": "#DC2626",
}

_TITLE_KEYS = {"meeting title", "title", "subject", "meeting subject", "meeting name",
               "عنوان الاجتماع", "العنوان", "الموضوع", "موضوع الاجتماع"}
_PEOPLE_KEYS = {"attendees", "attendee", "participants", "present", "attendance",
                "members present", "absent", "apologies", "regrets", "absentees",
                "invitees", "الحضور", "الحاضرون", "المشاركون", "الغائبون", "المعتذرون"}
_STATUS_HEADERS = {"status", "state", "الحالة"}
_PLACEHOLDERS = {"not specified", "n/a", "na", "tbd", "none", "-", "—", "...", "…",
                 "غير محدد"}


# ----------------------------------------------------------------------------
# colour helpers
# ----------------------------------------------------------------------------
def _accent(value: str) -> colors.Color:
    a = str(value or "").strip()
    try:
        return colors.HexColor(a if re.match(r"^#[0-9A-Fa-f]{3,8}$", a) else DEFAULT_ACCENT)
    except (ValueError, TypeError):               # e.g. "#GG0000" from an imported profile
        return colors.HexColor(DEFAULT_ACCENT)


def _luminance(c: colors.Color) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red) + 0.7152 * ch(c.green) + 0.0722 * ch(c.blue)


def _contrast(a: colors.Color, b: colors.Color) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _readable(c: colors.Color, minimum: float = 4.5) -> colors.Color:
    """The accent, darkened until text in it reads on white (a pale brand colour
    such as yellow would otherwise make headings invisible)."""
    out = c
    for _ in range(20):
        if _contrast(out, colors.white) >= minimum:
            break
        out = colors.Color(out.red * 0.85, out.green * 0.85, out.blue * 0.85)
    return out


def _hex(c: colors.Color) -> str:
    return "#%02X%02X%02X" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))


# ----------------------------------------------------------------------------
# text helpers
# ----------------------------------------------------------------------------
def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_placeholder(text: str) -> bool:
    return md_blocks.plain(text).strip().lower() in _PLACEHOLDERS


def _inline(text: str) -> str:
    """Convert **bold** runs to reportlab markup, escaping the rest.

    Arabic runs are reshaped + bidi-reordered (reportlab does not shape) and
    drawn with a registered Arabic font, since the Helvetica default has no
    Arabic glyphs.
    """
    reg, bold_font = rtl.pdf_arabic_font()
    out = []
    for txt, bold in md_blocks.runs(text):
        if reg and rtl.has_arabic(txt):
            safe = _esc(rtl.shape(txt))
            out.append(f'<font name="{bold_font if bold else reg}">{safe}</font>')
        else:
            safe = _esc(txt)
            out.append(f"<b>{safe}</b>" if bold else safe)
    return "".join(out)


def _rtl_lines(text: str, font: str, size: float, width: float) -> list[str]:
    """Wrap Arabic text into lines that fit `width`, in LOGICAL order, then
    shape + bidi-reorder each line on its own.

    Reordering a whole paragraph first and letting reportlab wrap the result
    makes multi-line Arabic read bottom-to-top (the first visual line holds the
    end of the text), so the wrapping has to happen before the reordering.
    """
    out: list[str] = []
    for src in text.splitlines() or [""]:
        cur: list[str] = []
        for word in src.split():
            cand = cur + [word]
            if cur and stringWidth(rtl.shape(" ".join(cand)), font, size) > width:
                out.append(rtl.shape(" ".join(cur)))
                cur = [word]
            else:
                cur = cand
        if cur:
            out.append(rtl.shape(" ".join(cur)))
    return out or [""]


def _rtl_style(style, text: str):
    """Right-align a paragraph style when its text is Arabic (else unchanged)."""
    if rtl.has_arabic(text):
        return ParagraphStyle(style.name + "_rtl", parent=style, alignment=TA_RIGHT)
    return style


def _para(text: str, style, width: float, markup: str | None = None, **kw) -> Paragraph:
    """A Paragraph for one piece of minutes text, laid out correctly for Arabic.

    Non-Arabic text keeps the usual **bold** markup (or the ready-made
    `markup`). Arabic text is wrapped with `_rtl_lines` and emitted one visual
    line per <br/>, right-aligned, in the Arabic font. (Inline bold is dropped
    inside Arabic lines: shaping each bold run separately placed the runs in the
    wrong order — e.g. a "key: value" line read value-first.)
    """
    reg, bold_font = rtl.pdf_arabic_font()
    if not (reg and rtl.has_arabic(text)):
        return Paragraph(markup if markup is not None else _inline(text), style, **kw)
    plain = md_blocks.plain(text)
    font = bold_font if "Bold" in (style.fontName or "") else reg
    avail = max(width - style.leftIndent - style.rightIndent, 40.0) * 0.96
    lines = _rtl_lines(plain, font, style.fontSize, avail)
    body = "<br/>".join(f'<font name="{font}">{_esc(ln)}</font>' for ln in lines)
    return Paragraph(body, _rtl_style(style, text), **kw)


def _canvas_font(text: str, font: str) -> tuple[str, str]:
    """(font, drawable text) for a canvas string: Arabic is shaped and switched
    to the registered Arabic font."""
    if text and rtl.has_arabic(text):
        reg, bold_font = rtl.pdf_arabic_font()
        if reg:
            return (bold_font if font.endswith("Bold") else reg), rtl.shape(text)
    return font, text


def _text_w(text: str, font: str, size: float) -> float:
    f, s = _canvas_font(text, font)
    return stringWidth(s, f, size)


def _draw(canvas, text: str, x: float, y: float, *, font: str, size: float,
          align: str = "left") -> None:
    """Draw a canvas string, switching to the Arabic font + shaping when needed."""
    f, s = _canvas_font(text, font)
    canvas.setFont(f, size)
    if align == "right":
        canvas.drawRightString(x, y, s)
    elif align == "center":
        canvas.drawCentredString(x, y, s)
    else:
        canvas.drawString(x, y, s)


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap for canvas text (logical order). A word wider than the
    line is broken by characters, so nothing is ever clipped."""
    lines: list[str] = []
    for src in (text or "").splitlines() or [""]:
        cur = ""
        for word in src.split():
            cand = f"{cur} {word}" if cur else word
            if _text_w(cand, font, size) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            cur = ""
            while _text_w(word, font, size) > width and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and _text_w(word[:cut], font, size) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            cur = word
        if cur:
            lines.append(cur)
    return lines or [""]


def _fit(text: str, font: str, size: float, width: float, minimum: float) -> float:
    """The largest size <= `size` (not below `minimum`) at which text fits."""
    while size > minimum and _text_w(text, font, size) > width:
        size -= 0.5
    return size


def _ellipsize(text: str, font: str, size: float, width: float) -> str:
    """For the RUNNING header only (the full text is on page 1)."""
    if _text_w(text, font, size) <= width:
        return text
    while text and _text_w(text + "…", font, size) > width:
        text = text[:-1]
    return (text.rstrip() + "…") if text else ""


# ----------------------------------------------------------------------------
# styles
# ----------------------------------------------------------------------------
def _styles(accent: colors.Color, head_text: colors.Color) -> dict:
    ink = _readable(accent)
    base = dict(fontName="Helvetica", textColor=TEXT, allowWidows=0, allowOrphans=0)
    S = {
        "eyebrow": ParagraphStyle("m_eyebrow", fontName="Helvetica-Bold", fontSize=9,
                                  leading=12, textColor=ink, spaceAfter=3),
        "title": ParagraphStyle("m_title", fontName="Helvetica-Bold", fontSize=20,
                                leading=25, textColor=TEXT, spaceAfter=10),
        "h2": ParagraphStyle("m_h2", fontName="Helvetica-Bold", fontSize=13, leading=17,
                             textColor=ink, spaceBefore=12, spaceAfter=2),
        "h3": ParagraphStyle("m_h3", fontName="Helvetica-Bold", fontSize=11, leading=15,
                             textColor=TEXT, spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("m_body", fontSize=10.5, leading=15, spaceAfter=5, **base),
        "kv": ParagraphStyle("m_kv", fontSize=10.5, leading=15, spaceAfter=3, **base),
        "bullet": ParagraphStyle("m_bullet", fontSize=10.5, leading=15, leftIndent=14,
                                 bulletIndent=3, spaceAfter=4, bulletColor=ink, **base),
        "olist": ParagraphStyle("m_olist", fontSize=10.5, leading=15, leftIndent=20,
                                bulletIndent=0, spaceAfter=4, bulletColor=ink,
                                bulletFontName="Helvetica-Bold", **base),
        "label": ParagraphStyle("m_label", fontName="Helvetica-Bold", fontSize=9,
                                leading=13, textColor=MUTED),
        "value": ParagraphStyle("m_value", fontSize=10, leading=14, **base),
        "name": ParagraphStyle("m_name", fontSize=9.5, leading=13, leftIndent=9,
                               bulletIndent=0, bulletColor=ink, **base),
        "group": ParagraphStyle("m_group", fontName="Helvetica-Bold", fontSize=9,
                                leading=13, textColor=MUTED, spaceBefore=8, spaceAfter=3),
        "cell": ParagraphStyle("m_cell", fontSize=9, leading=12, **base),
        "cellh": ParagraphStyle("m_cellh", fontName="Helvetica-Bold", fontSize=9,
                                leading=12, textColor=head_text),
    }
    return S


# ----------------------------------------------------------------------------
# letterhead (page 1) — measured once, so the body frame starts below it
# ----------------------------------------------------------------------------
class _Letterhead:
    NAME_MAX, NAME_MIN, CONTACT = 14.0, 9.0, 8.0

    def __init__(self, profile: CompanyProfile, accent: colors.Color):
        self.p = profile
        self.ink = _readable(accent)
        self.logo = None
        self.logo_w = self.logo_h = 0.0
        if profile.logo_path and Path(profile.logo_path).exists():
            try:
                from reportlab.lib.utils import ImageReader
                img = ImageReader(profile.logo_path)
                iw, ih = img.getSize()
                if iw > 0 and ih > 0:
                    try:
                        want = float(profile.logo_width_mm)
                    except (TypeError, ValueError):
                        want = 35.0
                    w = min(max(want, 10.0), 80.0) * mm
                    h = w * ih / iw
                    if h > 20 * mm:                          # tall logos: cap the height
                        h = 20 * mm
                        w = h * iw / ih
                    if w > CONTENT_W * 0.45:                 # very wide logos
                        w = CONTENT_W * 0.45
                        h = w * ih / iw
                    self.logo, self.logo_w, self.logo_h = img, w, h
            except Exception:
                self.logo = None
        pos = (profile.logo_position or "left").lower()
        self.pos = pos if pos in ("left", "center", "right") else "left"
        if self.logo and self.pos != "center":
            self.text_w = CONTENT_W - self.logo_w - 8 * mm
        else:
            self.text_w = CONTENT_W
        name = md_blocks.clean_text(profile.name).strip()
        self.name_size = _fit(name, "Helvetica-Bold", self.NAME_MAX, self.text_w, self.NAME_MIN)
        self.name_lines = _wrap(name, "Helvetica-Bold", self.name_size, self.text_w) if name else []
        self.contact_lines = self._contact_lines()
        self.name_lead = self.name_size * 1.25
        self.contact_lead = self.CONTACT * 1.4
        self.text_h = (len(self.name_lines) * self.name_lead
                       + (2 if self.contact_lines else 0)
                       + len(self.contact_lines) * self.contact_lead)
        if self.pos == "center":
            gap = 3 * mm if (self.logo and self.text_h) else 0
            self.block_h = self.logo_h + gap + self.text_h
        else:
            self.block_h = max(self.logo_h, self.text_h)
        self.rule_from_top = HEAD_TOP + self.block_h + 3.5 * mm
        self.frame_top = self.rule_from_top + 7 * mm

    def _contact_lines(self) -> list[str]:
        p = self.p
        bits = [md_blocks.clean_text(b).strip() for b in (p.phone, p.email, p.website)]
        bits = [b for b in bits if b]
        address = md_blocks.clean_text(p.address).strip()
        lines = _wrap(address, "Helvetica", self.CONTACT, self.text_w) if address else []
        sep, cur = "   ·   ", ""
        for b in bits:
            cand = f"{cur}{sep}{b}" if cur else b
            if _text_w(cand, "Helvetica", self.CONTACT) <= self.text_w:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            wrapped = _wrap(b, "Helvetica", self.CONTACT, self.text_w)
            lines.extend(wrapped[:-1])
            cur = wrapped[-1]
        if cur:
            lines.append(cur)
        return lines

    def draw(self, c) -> None:
        top = PAGE_H - HEAD_TOP
        if self.pos == "center":
            text_top = top - self.logo_h - (3 * mm if self.logo else 0)
            if self.logo:
                c.drawImage(self.logo, (PAGE_W - self.logo_w) / 2, top - self.logo_h,
                            width=self.logo_w, height=self.logo_h, mask="auto")
            tx, align = PAGE_W / 2, "center"
        else:
            logo_left = self.pos == "left"
            if self.logo:
                lx = MARGIN_X if logo_left else PAGE_W - MARGIN_X - self.logo_w
                ly = top - (self.block_h + self.logo_h) / 2       # centred in the block
                c.drawImage(self.logo, lx, ly, width=self.logo_w, height=self.logo_h,
                            mask="auto")
            text_top = top - (self.block_h - self.text_h) / 2
            if self.logo:
                tx, align = ((PAGE_W - MARGIN_X, "right") if logo_left else (MARGIN_X, "left"))
            else:
                tx, align = MARGIN_X, "left"
        c.saveState()
        y = text_top
        c.setFillColor(self.ink)
        for ln in self.name_lines:
            y -= self.name_lead
            _draw(c, ln, tx, y + self.name_lead * 0.2, font="Helvetica-Bold",
                  size=self.name_size, align=align)
        if self.contact_lines:
            y -= 2
        c.setFillColor(MUTED)
        for ln in self.contact_lines:
            y -= self.contact_lead
            _draw(c, ln, tx, y + self.contact_lead * 0.25, font="Helvetica",
                  size=self.CONTACT, align=align)
        rule_y = PAGE_H - self.rule_from_top
        c.setStrokeColor(self.ink)
        c.setLineWidth(1.2)
        c.line(MARGIN_X, rule_y, PAGE_W - MARGIN_X, rule_y)
        c.restoreState()


# ----------------------------------------------------------------------------
# page furniture: running header (pages 2+) and footer (all pages)
# ----------------------------------------------------------------------------
class _PageCanvas(_canvas.Canvas):
    """Two-pass canvas: pages are recorded, then header/footer are drawn with
    the total page count known."""
    _cfg: dict = {}

    @classmethod
    def factory(cls, **cfg):
        return type("MinutesCanvas", (cls,), {"_cfg": cfg})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        cfg = self._cfg
        self.saveState()
        if self._pageNumber == 1:
            if cfg.get("letterhead"):
                cfg["letterhead"].draw(self)
        else:
            self._running_header()
        self._footer(total)
        self.restoreState()

    def _running_header(self) -> None:
        cfg = self._cfg
        company, title, ink = cfg.get("company", ""), cfg.get("title", ""), cfg["ink"]
        if not (company or title):
            return
        y = PAGE_H - 14 * mm
        half = CONTENT_W / 2 - 3 * mm
        left, right = (company, title) if company else (title, "")
        if left:
            self.setFillColor(ink if company else MUTED)
            font = "Helvetica-Bold" if company else "Helvetica"
            _draw(self, _ellipsize(left, font, 8.5, half if right else CONTENT_W),
                  MARGIN_X, y, font=font, size=8.5)
        if right:
            self.setFillColor(MUTED)
            _draw(self, _ellipsize(right, "Helvetica", 8.5, half), PAGE_W - MARGIN_X, y,
                  font="Helvetica", size=8.5, align="right")
        self.setStrokeColor(BORDER)
        self.setLineWidth(0.6)
        self.line(MARGIN_X, y - 2.5 * mm, PAGE_W - MARGIN_X, y - 2.5 * mm)

    def _footer(self, total: int) -> None:
        f: _Footer = self._cfg["footer"]
        self.setStrokeColor(BORDER)
        self.setLineWidth(0.6)
        self.line(MARGIN_X, f.rule_y, PAGE_W - MARGIN_X, f.rule_y)
        y = f.first_y
        self.setFillColor(MUTED)
        for ln in f.lines:
            _draw(self, ln, f.x(f.side), y, font="Helvetica", size=f.size, align=f.side)
            y -= f.lead
        if not f.label_fmt:
            return
        label = f.label_fmt.replace("{n}", str(self._pageNumber)).replace("{total}", str(total))
        if f.num_in_footer:
            ny = f.first_y if (f.same_line or not f.lines) else y
            _draw(self, label, f.x(f.num_side), ny, font="Helvetica", size=7.5, align=f.num_side)
        else:                                                # header-left/center/right
            _draw(self, label, f.x(f.num_side), PAGE_H - 7 * mm, font="Helvetica",
                  size=7.5, align=f.num_side)


class _Footer:
    """Footer text + page-number placement, measured BEFORE the build so the
    body frame ends above it: a long footer gets the room it needs instead of
    running off the bottom of the page."""
    LOWEST = 5 * mm                  # no footer line below this (printers clip the edge)

    def __init__(self, profile: CompanyProfile | None):
        self.label_fmt = ""
        pos = "footer-right"
        if not profile or profile.show_page_numbers:
            self.label_fmt = (profile.page_number_format if profile else "") or "Page {n} of {total}"
            pos = (profile.page_number_position if profile else "") or "footer-right"
        self.num_side = "left" if pos.endswith("left") else "center" if pos.endswith("center") else "right"
        self.num_in_footer = bool(self.label_fmt) and not pos.startswith("header")
        text = md_blocks.clean_text(profile.footer_text).strip() if profile else ""
        side = (profile.footer_alignment or "center") if profile else "center"
        self.side = side if side in ("left", "center", "right") else "center"
        sample = self.label_fmt.replace("{n}", "888").replace("{total}", "888")
        label_w = _text_w(sample, "Helvetica", 7.5) if sample else 0
        self.same_line = bool(self.num_in_footer and text and self.num_side != self.side)
        if self.same_line:
            room = (CONTENT_W - 2 * (label_w + 6 * mm)) if self.side == "center"                 else (CONTENT_W - label_w - 10 * mm)
        else:
            room = CONTENT_W
        room = max(room, CONTENT_W * 0.4)
        self.size = 7.5
        self.lines = _wrap(text, "Helvetica", self.size, room) if text else []
        if len(self.lines) > 3:                              # very long footers: smaller
            self.size = 6.5
            self.lines = _wrap(text, "Helvetica", self.size, room)
        self.lead = self.size * 1.3
        rows = len(self.lines) + (1 if (self.num_in_footer and not self.same_line and self.lines) else 0)
        rows = max(rows, 1)
        self.first_y = max(11.5 * mm, self.LOWEST + (rows - 1) * self.lead)
        self.rule_y = max(16 * mm, self.first_y + self.size + 2.5 * mm)
        self.bottom = max(BOTTOM, self.rule_y + 6 * mm)      # body frame bottom

    @staticmethod
    def x(where: str) -> float:
        return MARGIN_X if where == "left" else (
            PAGE_W - MARGIN_X if where == "right" else PAGE_W / 2)


# ----------------------------------------------------------------------------
# body building blocks
# ----------------------------------------------------------------------------
def _split_names(value: str) -> list[str]:
    """'A (Chair), B; C and D' -> ['A (Chair)', 'B', 'C', 'D'] — commas inside
    brackets don't split."""
    parts, buf, depth = [], [], 0
    for ch in value:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(depth - 1, 0)
        if ch in ",;،؛\n" and depth == 0:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    names = [p.strip(" .") for p in parts if p.strip(" .")]
    if len(names) > 1 and re.search(r"\s+and\s+", names[-1]):
        names[-1:] = [n.strip() for n in re.split(r"\s+and\s+", names[-1]) if n.strip()]
    return names


def _cell_markup(text: str, S: dict, status: bool) -> tuple[str, str | None]:
    """(style key, ready markup or None) for a table body cell."""
    if _is_placeholder(text):
        return "cell", f'<font color="{_hex(MUTED)}"><i>{_esc(md_blocks.plain(text))}</i></font>'
    if status and not rtl.has_arabic(text):
        from ..core.tasks import normalize_status
        canon = normalize_status(md_blocks.plain(text))
        col = STATUS_COLORS.get(canon)
        if col:
            return "cell", (f'<font name="Helvetica-Bold" color="{col}">•</font>'
                            f'&nbsp;<font color="{col}"><b>{_esc(md_blocks.plain(text))}</b></font>')
    return "cell", None


_CELL_PAD = 13.0                 # left + right cell padding (see _table)
_STATUS_MARK = 10.0              # the coloured dot + spaces before a status


def _col_widths(headers: list[str], rows: list[list[str]], avail: float,
                status_col: int = -1) -> list[float]:
    """Content-based column widths. Every column gets at least its longest word
    (so words never break); short columns (dates, names, status) then get their
    whole one-line width while it is a fair share, and the long text columns
    divide what is left in proportion to their text."""
    n = len(headers)
    mins, prefs = [], []
    for j in range(n):
        body_font = "Helvetica-Bold" if j == status_col else "Helvetica"
        extra = _STATUS_MARK if j == status_col else 0.0
        texts = [(headers[j], "Helvetica-Bold", 0.0)] + [(r[j], body_font, extra) for r in rows]
        longest_word = full = 0.0
        for t, font, ex in texts:
            p = md_blocks.plain(t)
            full = max(full, _text_w(p, font, 9) + ex)
            for w in p.split():
                longest_word = max(longest_word, _text_w(w, font, 9) + ex)
        mn = max(min(longest_word + _CELL_PAD + 1, avail * 0.4), 9 * mm)
        mins.append(mn)
        prefs.append(max(min(full + _CELL_PAD + 1, avail), mn))
    if sum(mins) >= avail:                        # too many columns: shrink evenly
        return [m * avail / sum(mins) for m in mins]
    if sum(prefs) <= avail:                       # everything fits on one line
        spare = avail - sum(prefs)
        return [p + spare * p / sum(prefs) for p in prefs]
    widths = [0.0] * n
    open_cols = list(range(n))
    left = avail
    while open_cols:                              # max-min fair share
        share = left / len(open_cols)
        fits = [j for j in open_cols if prefs[j] <= share]
        if not fits:
            break
        for j in fits:
            widths[j] = prefs[j]
            left -= prefs[j]
        open_cols = [j for j in open_cols if j not in fits]
    if open_cols:
        need = sum(mins[j] for j in open_cols)
        if need >= left:                          # give back from the short columns
            short = [j for j in range(n) if j not in open_cols]
            give = need - left
            spare = sum(widths[j] - mins[j] for j in short) or 1.0
            for j in short:
                widths[j] -= give * (widths[j] - mins[j]) / spare
            left = need
        weight = sum(prefs[j] for j in open_cols)
        room = left - need
        for j in open_cols:
            widths[j] = mins[j] + room * prefs[j] / weight
    return widths


def _table(headers: list[str], rows: list[list[str]], S: dict, accent: colors.Color,
           max_row_h: float) -> Table:
    n = max(len(headers), 1)
    headers = list(headers) + [""] * (n - len(headers))
    rows = [[(r[j] if j < len(r) else "") for j in range(n)] for r in rows]
    mirror = sum(rtl.has_arabic(h) for h in headers) * 2 > n
    if mirror:                                   # Arabic table: first column on the right
        headers = headers[::-1]
        rows = [r[::-1] for r in rows]
    status_col = next((j for j, h in enumerate(headers)
                       if md_blocks.plain(h).strip().lower() in _STATUS_HEADERS), -1)
    widths = _col_widths(headers, rows, CONTENT_W, status_col)
    data = [[_para(h, S["cellh"], widths[j] - _CELL_PAD) for j, h in enumerate(headers)]]
    for r in rows:
        line = []
        for j, cell in enumerate(r):
            key, markup = _cell_markup(cell, S, j == status_col)
            line.append(_para(cell, S[key], widths[j] - _CELL_PAD, markup=markup))
        data.append(line)
    tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), accent),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    # Rows stay whole across page breaks. Only if a single row is taller than a
    # page may rows split — otherwise it could never be placed (LayoutError).
    tbl.wrap(CONTENT_W, max_row_h * 4)
    heights = list(getattr(tbl, "_rowHeights", []) or [])
    tbl.splitInRow = 1 if heights and max(heights) > max_row_h - (heights[0] if heights else 0) else 0
    return tbl


def _details(rows: list[tuple[str, str]], S: dict, ink: colors.Color, mirror: bool) -> Table:
    label_w = max((_text_w(md_blocks.plain(k), "Helvetica-Bold", 9) for k, _ in rows), default=0)
    label_w = min(max(label_w + 16, 30 * mm), 50 * mm)
    value_w = CONTENT_W - label_w
    data = []
    for key, value in rows:
        lab = _para(key, S["label"], label_w - 16)
        if _is_placeholder(value):
            val = _para(value, S["value"], value_w - 16,
                        markup=f'<font color="{_hex(MUTED)}"><i>{_esc(md_blocks.plain(value))}</i></font>')
        else:
            val = _para(value, S["value"], value_w - 16)
        data.append([val, lab] if mirror else [lab, val])
    widths = [value_w, label_w] if mirror else [label_w, value_w]
    tbl = Table(data, colWidths=widths, hAlign="LEFT", splitByRow=1)
    edge = ("LINEAFTER", (-1, 0), (-1, -1), 3, ink) if mirror else \
        ("LINEBEFORE", (0, 0), (0, -1), 3, ink)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        edge,
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


def _people(key: str, value: str, S: dict, ink: colors.Color, mirror: bool) -> list:
    """An attendee (or apologies) list as a grid of names, 1-3 columns."""
    names = _split_names(md_blocks.plain(value)) or [md_blocks.plain(value)]
    real = [n for n in names if not _is_placeholder(n)]
    heading = f"{key} ({len(real)})" if len(real) > 1 else key
    out: list = [_Heading([_para(heading, S["group"], CONTENT_W)])]
    widest = max((_text_w(n, "Helvetica", 9.5) for n in names), default=0) + 9 + 16
    cols = 3 if widest <= CONTENT_W / 3 else 2 if widest <= CONTENT_W / 2 else 1
    cols = min(cols, max(len(names), 1))
    col_w = CONTENT_W / cols
    cells = []
    for nm in names:
        if _is_placeholder(nm):
            cells.append(_para(nm, S["value"], col_w - 16,
                               markup=f'<font color="{_hex(MUTED)}"><i>{_esc(nm)}</i></font>'))
        else:
            cells.append(_para(nm, S["name"], col_w - 16,
                               bulletText=None if rtl.has_arabic(nm) else "•"))
    grid = [cells[i:i + cols] for i in range(0, len(cells), cols)]
    grid[-1] += [""] * (cols - len(grid[-1]))
    if mirror:
        grid = [row[::-1] for row in grid]
    tbl = Table(grid, colWidths=[col_w] * cols, hAlign="LEFT", splitByRow=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    out.append(tbl)
    return out


def _list_item(text: str, marker: str, style) -> Paragraph:
    """A bullet / numbered item. Arabic items carry the marker inside the text,
    so the bidi algorithm puts it at the START of the right-to-left line
    (a reportlab bullet is always drawn at the left edge)."""
    if rtl.has_arabic(text):
        return _para(f"{marker} {text}", style, CONTENT_W)
    markup = (f'<font color="{_hex(MUTED)}"><i>{_esc(md_blocks.plain(text))}</i></font>'
              if _is_placeholder(text) else None)
    return _para(text, style, CONTENT_W, markup=markup, bulletText=marker)


class _Heading:
    """Placeholder for a section heading; resolved into a CondPageBreak sized to
    keep the heading together with the start of the following content."""

    def __init__(self, flowables: list):
        self.flowables = flowables


def _start_height(f) -> float:
    """Height of the first unbreakable piece of a flowable."""
    try:
        _w, h = f.wrap(CONTENT_W, PAGE_H)
    except Exception:
        return 0.0
    if isinstance(f, Table):
        rh = list(getattr(f, "_rowHeights", []) or [])
        return sum(rh[:2]) if rh else h
    if isinstance(f, Paragraph):
        return min(h, 3 * f.style.leading) + f.style.spaceBefore
    return h


def _resolve_headings(items: list, frame_h: float) -> list:
    flow: list = []
    for i, it in enumerate(items):
        if not isinstance(it, _Heading):
            flow.append(it)
            continue
        need = 0.0
        for f in it.flowables:
            try:
                need += f.wrap(CONTENT_W, PAGE_H)[1] + f.getSpaceBefore() + f.getSpaceAfter()
            except Exception:
                pass
        nxt = next((x for x in items[i + 1:] if not isinstance(x, (Spacer, _Heading))), None)
        if nxt is not None:
            need += _start_height(nxt)
        flow.append(CondPageBreak(min(need + 4 * mm, frame_h * 0.6)))
        flow.extend(it.flowables)
    return flow


# ----------------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------------
def export_pdf(minutes_md: str, path: str | Path,
               profile: CompanyProfile | None = None) -> Path:
    path = Path(path)
    accent = _accent(profile.accent_color if profile else DEFAULT_ACCENT)
    ink = _readable(accent)
    head_text = colors.white if _contrast(accent, colors.white) >= 3 else TEXT
    S = _styles(accent, head_text)
    blocks = md_blocks.parse(minutes_md)

    # -- title + details: the first H1 and the **Key:** lines right after it ----
    i = 0
    h1 = ""
    if blocks and blocks[0].kind == "h1":
        h1, i = blocks[0].text, 1
    details: list[tuple[str, str]] = []
    while i < len(blocks) and blocks[i].kind == "kv":
        details.append((blocks[i].key, blocks[i].text))
        i += 1
    title, title_at = "", -1
    for n, (k, v) in enumerate(details):
        if md_blocks.plain(k).strip().lower() in _TITLE_KEYS and not _is_placeholder(v):
            title, title_at = v, n
            break
    mirror = rtl.has_arabic(h1 or title or (details[0][0] if details else ""))

    letterhead = _Letterhead(profile, accent) if profile else None
    footer = _Footer(profile)
    bottom = footer.bottom
    first_top = letterhead.frame_top if letterhead else PLAIN_TOP
    first_h = PAGE_H - first_top - bottom
    later_h = PAGE_H - LATER_TOP - bottom

    items: list = [NextPageTemplate("later")]
    if title:
        if h1:
            items.append(_para(h1, S["eyebrow"], CONTENT_W,
                               markup=_esc(md_blocks.plain(h1).upper())))
        items.append(_para(title, S["title"], CONTENT_W))
    elif h1:
        items.append(_para(h1, S["title"], CONTENT_W))
    panel = [(k, v) for n, (k, v) in enumerate(details)
             if n != title_at and md_blocks.plain(k).strip().lower() not in _PEOPLE_KEYS]
    people = [(k, v) for k, v in details if md_blocks.plain(k).strip().lower() in _PEOPLE_KEYS]
    if panel:
        items.append(_details(panel, S, ink, mirror))
    for k, v in people:
        items.extend(_people(k, v, S, ink, mirror))
    if details:
        items.append(Spacer(1, 4))

    for blk in blocks[i:]:
        if blk.kind in ("h1", "h2"):
            items.append(_Heading([
                _para(blk.text, S["h2"], CONTENT_W),
                HRFlowable(width="100%", thickness=0.7, color=BORDER, spaceBefore=1,
                           spaceAfter=6),
            ]))
        elif blk.kind == "h3":
            items.append(_Heading([_para(blk.text, S["h3"], CONTENT_W)]))
        elif blk.kind == "olist":
            for k, it in enumerate(blk.items):
                items.append(_list_item(it, f"{blk.start + k}.", S["olist"]))
            items.append(Spacer(1, 3))
        elif blk.kind == "bullet":
            for it in blk.items:
                items.append(_list_item(it, "•", S["bullet"]))
            items.append(Spacer(1, 3))
        elif blk.kind == "kv":
            items.append(_para(f"**{blk.key}:** {blk.text}", S["kv"], CONTENT_W))
        elif blk.kind == "para":
            markup = (f'<font color="{_hex(MUTED)}"><i>{_esc(md_blocks.plain(blk.text))}</i></font>'
                      if _is_placeholder(blk.text) else None)
            items.append(_para(blk.text, S["body"], CONTENT_W, markup=markup))
        elif blk.kind == "table":
            items.append(Spacer(1, 3))
            items.append(_table(blk.headers, blk.rows, S, accent, later_h))
            items.append(Spacer(1, 8))
    if len(items) == 1:                                   # nothing but the template switch
        items.append(_para("No minutes content.", S["body"], CONTENT_W))

    flow = _resolve_headings(items, later_h)

    first = Frame(MARGIN_X, bottom, CONTENT_W, first_h, id="first",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later = Frame(MARGIN_X, bottom, CONTENT_W, later_h, id="later",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc_title = md_blocks.plain(title or h1).strip() or "Minutes of Meeting"
    company = md_blocks.clean_text(profile.name).strip() if profile else ""
    doc = BaseDocTemplate(
        str(path), pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X,
        topMargin=first_top, bottomMargin=bottom,
        title=doc_title, author=company or "MICO360 Meetings",
        subject="Minutes of Meeting", creator="MICO360 Meetings",
    )
    doc.addPageTemplates([PageTemplate(id="first", frames=[first]),
                          PageTemplate(id="later", frames=[later])])
    doc.build(flow, canvasmaker=_PageCanvas.factory(
        footer=footer, letterhead=letterhead, ink=ink, company=company,
        title=md_blocks.plain(title or h1).strip()))
    return path
