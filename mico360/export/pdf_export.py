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

import logging
import re
import threading
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as _canvas
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, KeepTogether, NextPageTemplate, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from ..core.profiles import CompanyProfile
from . import md_blocks, pdf_designs, pdf_fonts, rtl

log = logging.getLogger("mico360.pdf_export")

# The design of the export running on THIS thread (exports can run side by side:
# the Export button and an e-mail attachment each have their own worker).
_ctx = threading.local()
_BUILD_LOCK = threading.RLock()


def _D() -> pdf_designs.Design:
    return getattr(_ctx, "design", None) or pdf_designs.get(None)


def _fam() -> pdf_fonts.Family:
    return pdf_fonts.family(_D().font)

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
GRID_HEAD = colors.HexColor("#ECE9E8")       # ruled ("grid") tables: header / label fill
GRID_LINE = colors.HexColor("#9A9096")
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


def _tint(c: colors.Color, amount: float) -> colors.Color:
    """The colour mixed with white (amount 0..1 of the colour kept)."""
    return colors.Color(1 - (1 - c.red) * amount, 1 - (1 - c.green) * amount,
                        1 - (1 - c.blue) * amount)


def _hex(c: colors.Color) -> str:
    return "#%02X%02X%02X" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))


# ----------------------------------------------------------------------------
# text helpers
# ----------------------------------------------------------------------------
def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_placeholder(text: str) -> bool:
    return md_blocks.plain(text).strip().lower() in _PLACEHOLDERS


_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(((?:https?://|mailto:)[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
_ITAL_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"
                      r"|(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_PH_RE = re.compile(r"\x00(\d+)\x00")
LINK = "#1F5FA8"


def _visible(text: str) -> str:
    """The text as printed: inline Markdown markers removed, a link shown as
    'label (address)'."""
    s = _CODE_RE.sub(r"\1", text or "")
    s = _LINK_RE.sub(lambda m: m.group(1) if m.group(1).strip() == m.group(2)
                     else f"{m.group(1)} ({m.group(2)})", s)
    s = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2), s)
    s = _STRIKE_RE.sub(r"\1", s)
    return _ITAL_RE.sub(lambda m: m.group(1) or m.group(2), s)


def _text_markup(s: str) -> str:
    """Escape plain text for a Paragraph, switching to a fallback font for any
    character the main font lacks (CJK, Indic, symbols, emoji)."""
    reg, _bold = rtl.pdf_arabic_font()
    if reg and rtl.has_arabic(s):        # rare here: Arabic paragraphs take _para's RTL path
        return f'<font name="{reg}">{_esc(rtl.shape(s))}</font>'
    out = []
    for seg, fb in pdf_fonts.runs(s, _D().font):
        out.append(f'<font name="{fb}">{_esc(seg)}</font>' if fb else _esc(seg))
    return "".join(out)


def _inline(text: str) -> str:
    """Minutes Markdown -> reportlab paragraph markup: **bold**, *italic*,
    ~~strike~~, `code`, and [links](https://…) (clickable, with the address
    printed too so it survives on paper). Everything else is escaped text."""
    keep: list[str] = []

    def hold(markup: str) -> str:
        keep.append(markup)
        return f"\x00{len(keep) - 1}\x00"

    def code(m):
        body = m.group(1)
        mono = _fam().mono
        if mono and all(_fam().mono_has(ch) for ch in body):
            return hold(f'<font name="{mono}">{_esc(body)}</font>')
        return hold(_text_markup(body))

    def link(m):
        label, url = m.group(1), m.group(2)
        href = _esc(url).replace('"', "&quot;")
        out = f'<a href="{href}" color="{LINK}"><u>{_text_markup(label)}</u></a>'
        if label.strip() != url:
            out += f' <font color="{_hex(MUTED)}">({_text_markup(url)})</font>'
        return hold(out)

    s = _CODE_RE.sub(code, text or "")
    s = _LINK_RE.sub(link, s)
    s = _text_markup(s)
    s = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _STRIKE_RE.sub(r"<strike>\1</strike>", s)
    s = _ITAL_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)
    return _PH_RE.sub(lambda m: keep[int(m.group(1))], _balance(s))


_TAG_RE = re.compile(r"<(/?)(b|i|strike)>")
_EMPTY_TAG_RE = re.compile(r"<(b|i|strike)></\1>")


def _balance(markup: str) -> str:
    """Make overlapping emphasis well-formed: "**a *b** c*" gives <b>a <i>b</b>
    c</i>, which the paragraph parser rejects (the export failed). Tags that
    overlap are closed and reopened so the text keeps its formatting."""
    out, stack, pos = [], [], 0
    for m in _TAG_RE.finditer(markup):
        out.append(markup[pos:m.start()])
        pos = m.end()
        closing, tag = m.group(1) == "/", m.group(2)
        if not closing:
            stack.append(tag)
            out.append(f"<{tag}>")
        elif tag in stack:
            reopen = []
            while stack[-1] != tag:
                t = stack.pop()
                out.append(f"</{t}>")
                reopen.append(t)
            stack.pop()
            out.append(f"</{tag}>")
            for t in reversed(reopen):
                stack.append(t)
                out.append(f"<{t}>")
        # a close tag with nothing open is dropped
    out.append(markup[pos:])
    out.extend(f"</{t}>" for t in reversed(stack))
    res = "".join(out)
    while True:
        slim = _EMPTY_TAG_RE.sub("", res)
        if slim == res:
            return res
        res = slim


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
        try:
            return Paragraph(markup if markup is not None else _inline(text), style, **kw)
        except Exception:
            # Never let one odd line of formatting fail the whole export: print
            # the text plainly instead.
            log.warning("inline formatting rejected; printing as plain text: %r", text[:80])
            return Paragraph(_text_markup(_visible(text)), style, **kw)
    plain = _visible(text)
    font = bold_font if "Bold" in (style.fontName or "") else reg
    avail = max(width - style.leftIndent - style.rightIndent, 40.0) * 0.96
    lines = _rtl_lines(plain, font, style.fontSize, avail)
    body = "<br/>".join(f'<font name="{font}">{_esc(ln)}</font>' for ln in lines)
    return Paragraph(body, _rtl_style(style, text), **kw)


def _segments(text: str, font: str) -> list[tuple[str, str]]:
    """(font, drawable text) runs for a canvas string: Arabic is shaped and set
    in the Arabic font; other characters the main font lacks use a fallback."""
    if text and rtl.has_arabic(text):
        reg, bold_font = rtl.pdf_arabic_font()
        if reg:
            return [((bold_font if font.endswith("Bold") else reg), rtl.shape(text))]
    return [((fb or font), seg) for seg, fb in pdf_fonts.runs(text or "", _D().font)]


def _text_w(text: str, font: str, size: float) -> float:
    return sum(stringWidth(s, f, size) for f, s in _segments(text, font))


def _draw(canvas, text: str, x: float, y: float, *, font: str, size: float,
          align: str = "left") -> None:
    """Draw a canvas string (shaping Arabic, falling back per character)."""
    segs = _segments(text, font)
    width = sum(stringWidth(s, f, size) for f, s in segs)
    x0 = x - width if align == "right" else x - width / 2 if align == "center" else x
    for f, s in segs:
        canvas.setFont(f, size)
        canvas.drawString(x0, y, s)
        x0 += stringWidth(s, f, size)


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
    d = _D()
    g = d.gap
    ink = _readable(accent)
    align = TA_CENTER if d.title_align == "center" else TA_LEFT
    # bulletFontName too: its default is the unembedded built-in Helvetica.
    base = dict(fontName=_fam().regular, bulletFontName=_fam().regular, textColor=TEXT,
                allowWidows=0, allowOrphans=0)
    head_cell = {"fill": head_text, "tint": ink}.get(d.table, TEXT)
    S = {
        "eyebrow": ParagraphStyle("m_eyebrow", fontName=_fam().bold, fontSize=9,
                                  leading=12, textColor=ink, spaceAfter=3, alignment=align),
        "title": ParagraphStyle("m_title", fontName=_fam().bold, fontSize=d.title_size,
                                leading=d.title_size * 1.25, textColor=TEXT,
                                spaceAfter=10 * g, alignment=align),
        "h2": ParagraphStyle("m_h2", fontName=_fam().bold, fontSize=d.h2_size,
                             leading=d.h2_size * 1.3,
                             textColor=ink if d.heading == "rule" else TEXT,
                             spaceBefore=12 * g, spaceAfter=2),
        "h3": ParagraphStyle("m_h3", fontName=_fam().bold, fontSize=d.body + 0.5,
                             leading=d.lead, textColor=TEXT, spaceBefore=8 * g, spaceAfter=3),
        "body": ParagraphStyle("m_body", fontSize=d.body, leading=d.lead, spaceAfter=5 * g,
                               **base),
        "kv": ParagraphStyle("m_kv", fontSize=d.body, leading=d.lead, spaceAfter=3 * g, **base),
        "bullet": ParagraphStyle("m_bullet", fontSize=d.body, leading=d.lead, leftIndent=14,
                                 bulletIndent=3, spaceAfter=4 * g, bulletColor=ink, **base),
        "olist": ParagraphStyle("m_olist", fontSize=d.body, leading=d.lead, leftIndent=20,
                                bulletIndent=0, spaceAfter=4 * g, bulletColor=ink,
                                **{**base, "bulletFontName": _fam().bold}),
        "label": ParagraphStyle("m_label", fontName=_fam().bold, fontSize=d.cell,
                                leading=d.cell + 4,
                                textColor=TEXT if d.details == "grid" else MUTED),
        "card_label": ParagraphStyle("m_card_label", fontName=_fam().bold, fontSize=7.5,
                                     leading=10, textColor=MUTED, spaceAfter=1),
        "value": ParagraphStyle("m_value", fontSize=d.body - 0.5, leading=d.lead - 1, **base),
        "name": ParagraphStyle("m_name", fontSize=d.cell + 0.5, leading=d.cell + 4,
                               leftIndent=9, bulletIndent=0, bulletColor=ink, **base),
        "name_num": ParagraphStyle("m_name_num", fontSize=d.cell + 0.5, leading=d.cell + 4,
                                   leftIndent=18, bulletIndent=0, bulletColor=TEXT, **base),
        "group": ParagraphStyle("m_group", fontName=_fam().bold, fontSize=9,
                                leading=13, textColor=MUTED, spaceBefore=8 * g, spaceAfter=3),
        "cell": ParagraphStyle("m_cell", fontSize=d.cell, leading=d.cell + 3, **base),
        "cellh": ParagraphStyle("m_cellh", fontName=_fam().bold, fontSize=d.cell,
                                leading=d.cell + 3, textColor=head_cell),
    }
    return S


# ----------------------------------------------------------------------------
# letterhead (page 1) — measured once, so the body frame starts below it
# ----------------------------------------------------------------------------
class _Letterhead:
    NAME_MAX, NAME_MIN, CONTACT = 14.0, 9.0, 8.0

    def __init__(self, profile: CompanyProfile, accent: colors.Color):
        d = _D()
        self.p = profile
        self.ink = _readable(accent)
        self.rule = d.letterhead
        self.name_color = self.ink if d.name_accent else TEXT
        self.NAME_MAX = d.name_max
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
                    cap = d.logo_max_mm * mm
                    if h > cap:                              # tall logos: cap the height
                        h = cap
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
        self.name_size = _fit(name, _fam().bold, self.NAME_MAX, self.text_w, self.NAME_MIN)
        self.name_lines = _wrap(name, _fam().bold, self.name_size, self.text_w) if name else []
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
        lines = _wrap(address, _fam().regular, self.CONTACT, self.text_w) if address else []
        sep, cur = "   ·   ", ""
        for b in bits:
            cand = f"{cur}{sep}{b}" if cur else b
            if _text_w(cand, _fam().regular, self.CONTACT) <= self.text_w:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            wrapped = _wrap(b, _fam().regular, self.CONTACT, self.text_w)
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
        c.setFillColor(self.name_color)
        for ln in self.name_lines:
            y -= self.name_lead
            _draw(c, ln, tx, y + self.name_lead * 0.2, font=_fam().bold,
                  size=self.name_size, align=align)
        if self.contact_lines:
            y -= 2
        c.setFillColor(MUTED)
        for ln in self.contact_lines:
            y -= self.contact_lead
            _draw(c, ln, tx, y + self.contact_lead * 0.25, font=_fam().regular,
                  size=self.CONTACT, align=align)
        rule_y = PAGE_H - self.rule_from_top
        if self.rule == "hairline":
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.8)
            c.line(MARGIN_X, rule_y, PAGE_W - MARGIN_X, rule_y)
        elif self.rule == "double":
            c.setStrokeColor(self.ink)
            c.setLineWidth(1.6)
            c.line(MARGIN_X, rule_y, PAGE_W - MARGIN_X, rule_y)
            c.setLineWidth(0.5)
            c.line(MARGIN_X, rule_y - 2.4, PAGE_W - MARGIN_X, rule_y - 2.4)
        else:
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
        # Every page otherwise starts in the built-in (unembedded) Helvetica.
        if not kwargs.get("initialFontName"):
            kwargs["initialFontName"] = _fam().regular
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
        band = _D().top_band_mm * mm
        if band:                                    # accent strip across the top edge
            self.setFillColor(cfg["accent"])
            self.rect(0, PAGE_H - band, PAGE_W, band, stroke=0, fill=1)
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
            font = _fam().bold if company else _fam().regular
            _draw(self, _ellipsize(left, font, 8.5, half if right else CONTENT_W),
                  MARGIN_X, y, font=font, size=8.5)
        if right:
            self.setFillColor(MUTED)
            _draw(self, _ellipsize(right, _fam().regular, 8.5, half), PAGE_W - MARGIN_X, y,
                  font=_fam().regular, size=8.5, align="right")
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
            _draw(self, ln, f.x(f.side), y, font=_fam().regular, size=f.size, align=f.side)
            y -= f.lead
        if not f.label_fmt:
            return
        label = f.label_fmt.replace("{n}", str(self._pageNumber)).replace("{total}", str(total))
        if f.num_in_footer:
            ny = f.first_y if (f.same_line or not f.lines) else y
            _draw(self, label, f.x(f.num_side), ny, font=_fam().regular, size=7.5, align=f.num_side)
        else:                                                # header-left/center/right
            _draw(self, label, f.x(f.num_side), PAGE_H - 7 * mm, font=_fam().regular,
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
        label_w = _text_w(sample, _fam().regular, 7.5) if sample else 0
        self.same_line = bool(self.num_in_footer and text and self.num_side != self.side)
        if self.same_line and self.side == "center":        # number at one edge
            room = CONTENT_W - 2 * (label_w + 6 * mm)
        elif self.same_line and self.num_side == "center":  # text at an edge, number mid-page:
            room = CONTENT_W / 2 - label_w / 2 - 4 * mm      # the text must stop before it
        elif self.same_line:                                 # text and number at opposite edges
            room = CONTENT_W - label_w - 10 * mm
        else:
            room = CONTENT_W
        room = max(room, CONTENT_W * 0.3)
        self.size = 7.5
        self.lines = _wrap(text, _fam().regular, self.size, room) if text else []
        if len(self.lines) > 3:                              # very long footers: smaller
            self.size = 6.5
            self.lines = _wrap(text, _fam().regular, self.size, room)
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
        return "cell", f'<font color="{_hex(MUTED)}"><i>{_text_markup(_visible(text))}</i></font>'
    if status and not rtl.has_arabic(text):
        if not _D().status_color:                  # e.g. Formal: bold, no colour
            return "cell", f"<b>{_text_markup(_visible(text))}</b>"
        from ..core.tasks import normalize_status
        canon = normalize_status(md_blocks.plain(text))
        col = STATUS_COLORS.get(canon)
        if col:
            return "cell", (f'<font name="{_fam().bold}" color="{col}">•</font>'
                            f'&nbsp;<font color="{col}"><b>{_text_markup(_visible(text))}</b></font>')
    return "cell", None


_CELL_PAD = 13.0                 # left + right cell padding (see _table)
_STATUS_MARK = 10.0              # the coloured dot + spaces before a status


def _col_widths(headers: list[str], rows: list[list[str]], avail: float,
                status_col: int = -1, size: float | None = None,
                pad: float | None = None, fits: list | None = None) -> list[float]:
    """Content-based column widths. Every column gets at least its longest word
    (so words never break); short columns (dates, names, status) then get their
    whole one-line width while it is a fair share, and the long text columns
    divide what is left in proportion to their text."""
    n = len(headers)
    size = size or _D().cell
    pad = _CELL_PAD if pad is None else pad
    mins, prefs = [], []
    for j in range(n):
        body_font = _fam().bold if j == status_col else _fam().regular
        extra = _STATUS_MARK if j == status_col else 0.0
        texts = [(headers[j], _fam().bold, 0.0)] + [(r[j], body_font, extra) for r in rows]
        longest_word = full = 0.0
        for t, font, ex in texts:
            p = _visible(t)
            full = max(full, _text_w(p, font, size) + ex)
            for w in p.split():
                longest_word = max(longest_word, _text_w(w, font, size) + ex)
        mn = max(min(longest_word + pad + 1, avail * 0.4), min(9 * mm, pad + 3 * size))
        mins.append(mn)
        prefs.append(max(min(full + pad + 1, avail), mn))
    if fits is not None:                          # do whole words fit at this size?
        fits.append(sum(mins) <= avail)
    if sum(mins) >= avail:                        # too many columns
        # First cap any column at twice its fair share (one unbreakable token
        # shouldn't squeeze every other column), then shrink evenly if needed.
        cap = 2 * avail / n
        capped = [min(m, cap) for m in mins]
        if sum(capped) < avail:
            over = [m - c for m, c in zip(mins, capped)]
            spare = avail - sum(capped)
            return [c + spare * o / sum(over) for c, o in zip(capped, over)]
        # Still too wide: narrow columns (status, dates, numbers) keep the width
        # their words need; the wide ones share what is left equally.
        widths, open_cols, left = [0.0] * n, list(range(n)), avail
        while open_cols:
            share = left / len(open_cols)
            fit = [j for j in open_cols if capped[j] <= share]
            if not fit:
                break
            for j in fit:
                widths[j] = capped[j]
                left -= capped[j]
            open_cols = [j for j in open_cols if j not in fit]
        for j in open_cols:
            widths[j] = left / len(open_cols)
        return widths
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
    # A wide table (many columns) steps its text size and padding down until
    # whole words fit their columns, instead of breaking words mid-way.
    base = _D().cell
    size, cpad = base, _CELL_PAD
    for size, cpad in [(base, _CELL_PAD)] + [(sz, 9.0) for sz in (8.0, 7.5, 7.0, 6.5)
                                             if sz < base]:
        ok: list = []
        widths = _col_widths(headers, rows, CONTENT_W, status_col, size, cpad, ok)
        if ok[0]:
            break
    cell_st, head_st = S["cell"], S["cellh"]
    if size != base:
        cell_st = ParagraphStyle("m_cell_s", parent=cell_st, fontSize=size, leading=size + 2.5)
        head_st = ParagraphStyle("m_cellh_s", parent=head_st, fontSize=size, leading=size + 2.5)
    data = [[_para(h, head_st, widths[j] - cpad) for j, h in enumerate(headers)]]
    for r in rows:
        line = []
        for j, cell in enumerate(r):
            _key, markup = _cell_markup(cell, S, j == status_col)
            line.append(_para(cell, cell_st, widths[j] - cpad, markup=markup))
        data.append(line)
    tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
    d = _D()
    pad = 5 if d.gap >= 1 else 3
    look = {
        "fill": [("BACKGROUND", (0, 0), (-1, 0), accent),
                 ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
                 ("BOX", (0, 0), (-1, -1), 0.6, BORDER)],
        "tint": [("BACKGROUND", (0, 0), (-1, 0), _tint(accent, 0.12)),
                 ("LINEBELOW", (0, 0), (-1, 0), 1.2, _readable(accent)),
                 ("LINEBELOW", (0, 1), (-1, -1), 0.5, BORDER)],
        "grid": [("BACKGROUND", (0, 0), (-1, 0), GRID_HEAD),
                 ("GRID", (0, 0), (-1, -1), 0.5, GRID_LINE),
                 ("BOX", (0, 0), (-1, -1), 0.8, MUTED)],
        "lines": [("LINEABOVE", (0, 0), (-1, 0), 0.9, TEXT),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.9, TEXT),
                  ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER)],
    }[d.table]
    tbl.setStyle(TableStyle(look + [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), _fam().regular),     # not the unembedded default
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("LEFTPADDING", (0, 0), (-1, -1), 6 if cpad == _CELL_PAD else 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7 if cpad == _CELL_PAD else 5),
    ]))
    # Rows stay whole across page breaks. Only if a single row is taller than a
    # page may rows split — otherwise it could never be placed (LayoutError).
    tbl.wrap(CONTENT_W, max_row_h * 4)
    heights = list(getattr(tbl, "_rowHeights", []) or [])
    tbl.splitInRow = 1 if heights and max(heights) > max_row_h - (heights[0] if heights else 0) else 0
    return tbl


def _value(value: str, S: dict, width: float) -> Paragraph:
    if _is_placeholder(value):
        return _para(value, S["value"], width,
                     markup=f'<font color="{_hex(MUTED)}"><i>{_text_markup(_visible(value))}</i></font>')
    return _para(value, S["value"], width)


def _pairs(cells: list, mirror: bool) -> tuple[list, list]:
    """Lay cells out two per row; (rows, spans) — an odd last cell spans both."""
    rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
    spans = []
    if rows and len(rows[-1]) == 1:
        rows[-1].append("")
        spans.append(("SPAN", (0, len(rows) - 1), (1, len(rows) - 1)))
    elif mirror:
        rows = [r[::-1] for r in rows]
    if mirror and spans:
        rows = [r[::-1] for r in rows[:-1]] + [rows[-1]]
    return rows, spans


def _details(rows: list[tuple[str, str]], S: dict, ink: colors.Color, mirror: bool) -> Table:
    """The meeting-details block (date, location ...), laid out per the design."""
    kind = _D().details
    font = ("FONTNAME", (0, 0), (-1, -1), _fam().regular)       # not the unembedded default
    if kind == "cards":                         # label over value, two cards per row
        half = CONTENT_W / 2
        cells = [[_para(k, S["card_label"], half - 18,
                        markup=_text_markup(_visible(k).upper())),
                  _value(v, S, half - 18)] for k, v in rows]
        grid, spans = _pairs(cells, mirror)
        tbl = Table(grid, colWidths=[half, half], hAlign="LEFT", splitByRow=1)
        tbl.setStyle(TableStyle(spans + [
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
            ("INNERGRID", (0, 0), (-1, -1), 3, colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), font,
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ]))
        return tbl
    if kind == "inline":                        # "Label: value", two per row, no fill
        half = CONTENT_W / 2
        cells = [_para(f"**{k}:** {v}", S["value"], half - 10) for k, v in rows]
        grid, spans = _pairs(cells, mirror)
        tbl = Table(grid, colWidths=[half, half], hAlign="LEFT", splitByRow=1)
        tbl.setStyle(TableStyle(spans + [
            ("LINEABOVE", (0, 0), (-1, 0), 0.5, BORDER),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), font,
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        return tbl
    label_w = max((_text_w(_visible(k), _fam().bold, S["label"].fontSize) for k, _ in rows),
                  default=0)
    label_w = min(max(label_w + 16, 30 * mm), 50 * mm)
    value_w = CONTENT_W - label_w
    data = []
    for key, value in rows:
        lab = _para(key, S["label"], label_w - 16)
        val = _value(value, S, value_w - 16)
        data.append([val, lab] if mirror else [lab, val])
    widths = [value_w, label_w] if mirror else [label_w, value_w]
    tbl = Table(data, colWidths=widths, hAlign="LEFT", splitByRow=1)
    label_col = (-1, 0), (-1, -1)
    if not mirror:
        label_col = (0, 0), (0, -1)
    if kind == "grid":                          # fully ruled, shaded label column
        look = [("BACKGROUND", label_col[0], label_col[1], GRID_HEAD),
                ("GRID", (0, 0), (-1, -1), 0.5, GRID_LINE),
                ("BOX", (0, 0), (-1, -1), 0.8, MUTED)]
    else:                                       # "panel": shaded, accent edge
        look = [("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("LINEAFTER", (-1, 0), (-1, -1), 3, ink) if mirror
                else ("LINEBEFORE", (0, 0), (0, -1), 3, ink),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER)]
    tbl.setStyle(TableStyle(look + [
        ("VALIGN", (0, 0), (-1, -1), "TOP"), font,
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


def _people(key: str, value: str, S: dict, ink: colors.Color, mirror: bool) -> list:
    """An attendee (or apologies) list as a grid of names, 1-3 columns."""
    kind = _D().details
    numbered = kind == "grid"
    names = _split_names(_visible(value)) or [_visible(value)]
    real = [n for n in names if not _is_placeholder(n)]
    heading = f"{key} ({len(real)})" if len(real) > 1 else key
    out: list = [_Heading([_para(heading, S["group"], CONTENT_W)])]
    style = S["name_num"] if numbered else S["name"]
    widest = max((_text_w(n, _fam().regular, style.fontSize) for n in names), default=0) \
        + style.leftIndent + 16
    cols = 3 if widest <= CONTENT_W / 3 else 2 if widest <= CONTENT_W / 2 else 1
    cols = min(cols, max(len(names), 1))
    col_w = CONTENT_W / cols
    cells = []
    for i, nm in enumerate(names, 1):
        if _is_placeholder(nm):
            cells.append(_para(nm, S["value"], col_w - 16,
                               markup=f'<font color="{_hex(MUTED)}"><i>{_text_markup(nm)}</i></font>'))
        elif rtl.has_arabic(nm):                # the marker goes inside RTL text
            cells.append(_para(f"{i}. {nm}" if numbered else nm, style, col_w - 16))
        else:
            cells.append(_para(nm, style, col_w - 16, bulletText=f"{i}." if numbered else "•"))
    grid = [cells[i:i + cols] for i in range(0, len(cells), cols)]
    grid[-1] += [""] * (cols - len(grid[-1]))
    if mirror:
        grid = [row[::-1] for row in grid]
    tbl = Table(grid, colWidths=[col_w] * cols, hAlign="LEFT", splitByRow=1)
    if kind == "grid":
        look, edge = [("GRID", (0, 0), (-1, -1), 0.5, GRID_LINE),
                      ("BOX", (0, 0), (-1, -1), 0.8, MUTED)], 4
    elif kind == "inline":
        look, edge = [("LINEBELOW", (0, -1), (-1, -1), 0.5, BORDER)], 3
    else:
        look, edge = [("BACKGROUND", (0, 0), (-1, -1), PANEL)], 7
    tbl.setStyle(TableStyle(look + [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), _fam().regular),     # not the unembedded default
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, 0), edge),
        ("BOTTOMPADDING", (0, -1), (-1, -1), edge),
        ("LEFTPADDING", (0, 0), (-1, -1), 0 if kind == "inline" else 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    out.append(tbl)
    return out


def _section_heading(text: str, S: dict, accent: colors.Color, counter: list) -> "_Heading":
    """A section (##) heading in the design's style."""
    d = _D()
    ink = _readable(accent)
    if d.heading == "numbered":                 # "1. PURPOSE OF MEETING" + dark rule
        counter[0] += 1
        para = _para(f"{counter[0]}. {text}", S["h2"], CONTENT_W,
                     markup=_text_markup(f"{counter[0]}. {_visible(text).upper()}"))
        return _Heading([para, HRFlowable(width="100%", thickness=0.9, color=TEXT,
                                          spaceBefore=1, spaceAfter=6)])
    if d.heading == "bar":                      # tinted bar with an accent edge
        cell = _para(text, S["h2"], CONTENT_W - 18)
        cell.style = ParagraphStyle("m_h2_bar", parent=cell.style, spaceBefore=0, spaceAfter=0)
        tbl = Table([[cell]], colWidths=[CONTENT_W], hAlign="LEFT")
        rtl_text = rtl.has_arabic(text)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), _tint(accent, 0.08)),
            ("LINEAFTER", (-1, 0), (-1, -1), 3, ink) if rtl_text
            else ("LINEBEFORE", (0, 0), (0, -1), 3, ink),
            ("FONTNAME", (0, 0), (-1, -1), _fam().regular),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ]))
        tbl.spaceBefore, tbl.spaceAfter = 13 * d.gap, 7
        return _Heading([tbl])
    para = _para(text, S["h2"], CONTENT_W)
    if d.heading == "plain":                    # dark heading, hairline
        return _Heading([para, HRFlowable(width="100%", thickness=0.5, color=BORDER,
                                          spaceBefore=1, spaceAfter=4)])
    return _Heading([para, HRFlowable(width="100%", thickness=0.7, color=BORDER,
                                      spaceBefore=1, spaceAfter=6)])


_SIGN_EN = ("Minutes recorded by", "Approved by", "Name", "Signature", "Date")
_SIGN_AR = ("أعد المحضر", "اعتمد المحضر", "الاسم", "التوقيع", "التاريخ")


def _signatures(S: dict, arabic: bool) -> list:
    """The closing "recorded by / approved by" block (kept on one page)."""
    L = _SIGN_AR if arabic else _SIGN_EN
    gap, lab = 10 * mm, 24 * mm
    col = (CONTENT_W - gap) / 2
    data = [[_para(f"**{L[0]}**", S["value"], col), "", "",
             _para(f"**{L[1]}**", S["value"], col), ""]]
    for item in L[2:]:
        data.append([_para(item, S["label"], lab - 6), "", "",
                     _para(item, S["label"], lab - 6), ""])
    tbl = Table(data, colWidths=[lab, col - lab, gap, lab, col - lab],
                rowHeights=[None, 9 * mm, 9 * mm, 9 * mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)), ("SPAN", (3, 0), (4, 0)),
        ("LINEBELOW", (1, 1), (1, -1), 0.6, TEXT), ("LINEBELOW", (4, 1), (4, -1), 0.6, TEXT),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("FONTNAME", (0, 0), (-1, -1), _fam().regular),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return [Spacer(1, 9 * mm), KeepTogether([tbl])]


def _list_item(text: str, marker: str, style) -> Paragraph:
    """A bullet / numbered item. Arabic items carry the marker inside the text,
    so the bidi algorithm puts it at the START of the right-to-left line
    (a reportlab bullet is always drawn at the left edge)."""
    if rtl.has_arabic(text):
        return _para(f"{marker} {text}", style, CONTENT_W)
    markup = (f'<font color="{_hex(MUTED)}"><i>{_text_markup(_visible(text))}</i></font>'
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
               profile: CompanyProfile | None = None, design: str | None = None) -> Path:
    """Write the minutes as a PDF in the company's branding.

    The layout is the company's chosen design (`profile.pdf_design`); `design`
    overrides it (used for exports without a company profile and for previews)."""
    # One PDF is built at a time: a preview on the GUI thread and an export or
    # e-mail attachment on a worker can overlap, and reportlab's shared font
    # objects aren't documented as safe for concurrent builds. Builds take well
    # under a second, so serialising them costs nothing noticeable.
    with _BUILD_LOCK:
        _ctx.design = pdf_designs.get(design or (profile.pdf_design if profile else None))
        try:
            return _export_pdf(minutes_md, Path(path), profile)
        finally:
            _ctx.design = None


def _export_pdf(minutes_md: str, path: Path, profile: CompanyProfile | None) -> Path:
    d = _D()
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
    counter = [0]                                   # numbered section headings

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
                               markup=_text_markup(_visible(h1).upper())))
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
            items.append(_section_heading(blk.text, S, accent, counter))
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
            markup = (f'<font color="{_hex(MUTED)}"><i>{_text_markup(_visible(blk.text))}</i></font>'
                      if _is_placeholder(blk.text) else None)
            items.append(_para(blk.text, S["body"], CONTENT_W, markup=markup))
        elif blk.kind == "table":
            items.append(Spacer(1, 3))
            items.append(_table(blk.headers, blk.rows, S, accent, later_h))
            items.append(Spacer(1, 8))
    if len(items) == 1:                                   # nothing but the template switch
        items.append(_para("No minutes content.", S["body"], CONTENT_W))
    elif d.signatures:
        items.extend(_signatures(S, mirror))

    flow = _resolve_headings(items, later_h)

    first = Frame(MARGIN_X, bottom, CONTENT_W, first_h, id="first",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later = Frame(MARGIN_X, bottom, CONTENT_W, later_h, id="later",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc_title = _visible(title or h1).strip() or "Minutes of Meeting"
    company = md_blocks.clean_text(profile.name).strip() if profile else ""
    doc = BaseDocTemplate(
        str(path), pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X,
        topMargin=first_top, bottomMargin=bottom,
        title=doc_title, author=company or "MICO360 Meetings",
        subject="Minutes of Meeting", creator="MICO360 Meetings",
        initialFontName=_fam().regular,
    )
    doc.addPageTemplates([PageTemplate(id="first", frames=[first]),
                          PageTemplate(id="later", frames=[later])])
    doc.build(flow, canvasmaker=_PageCanvas.factory(
        footer=footer, letterhead=letterhead, ink=ink, accent=accent, company=company,
        title=_visible(title or h1).strip()))
    return path
