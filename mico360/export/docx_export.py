"""Word (.docx) export with company letterhead, logo, footer and page numbers."""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Mm, Pt, RGBColor

from ..core.profiles import CompanyProfile
from . import md_blocks
from .rtl import has_arabic

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}


def _rtl_paragraph(p, text: str) -> None:
    """If the text is Arabic, mark the paragraph RTL (bidi + right-aligned) and
    flag every run rtl so Word shapes and orders it as native Arabic."""
    if not has_arabic(text):
        return
    pPr = p._p.get_or_add_pPr()
    pPr.append(OxmlElement("w:bidi"))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for run in p.runs:
        rPr = run._r.get_or_add_rPr()
        rtl = OxmlElement("w:rtl"); rtl.set(qn("w:val"), "1")
        rPr.append(rtl)


def _hex_rgb(color: str) -> RGBColor:
    color = str(color or "#8B1E1E").strip().lstrip("#")
    if len(color) in (3, 4):                       # #abc → #aabbcc
        color = "".join(c * 2 for c in color[:3])
    try:
        if len(color) < 6:
            raise ValueError(color)
        return RGBColor(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
    except Exception:
        return RGBColor(0x8B, 0x1E, 0x1E)


def _x(s) -> str:
    """Text safe for Word XML: control characters (e.g. \\x0b from pasted text)
    raise 'All strings must be XML compatible' in python-docx."""
    return md_blocks.clean_text(s)


def _add_runs(p, text: str, force_bold: bool = False) -> None:
    """Add `text` to paragraph `p`, rendering **bold** spans as bold runs (not
    literal asterisks)."""
    for txt, bold in md_blocks.runs(_x(text)):
        if txt:
            p.add_run(txt).bold = bold or force_bold


def _add_field(paragraph, instr: str) -> None:
    """Insert a Word field (e.g. PAGE / NUMPAGES) into a paragraph."""
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar"); fld_begin.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText"); instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = instr
    fld_end = OxmlElement("w:fldChar"); fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin); run._r.append(instr_el); run._r.append(fld_end)


def _build_header(doc, profile: CompanyProfile) -> None:
    section = doc.sections[0]
    header = section.header
    header.is_linked_to_previous = False
    p = header.paragraphs[0]
    p.alignment = _ALIGN.get(profile.logo_position, WD_ALIGN_PARAGRAPH.LEFT)

    if profile.logo_path and Path(profile.logo_path).exists():
        try:
            p.add_run().add_picture(profile.logo_path, width=Mm(profile.logo_width_mm))
        except Exception:
            pass

    info = header.add_paragraph()
    info.alignment = _ALIGN.get(profile.logo_position, WD_ALIGN_PARAGRAPH.LEFT)
    name_run = info.add_run(_x(profile.name))
    name_run.bold = True
    name_run.font.size = Pt(13)
    name_run.font.color.rgb = _hex_rgb(profile.accent_color)
    contact_bits = [_x(b) for b in (profile.address, profile.phone, profile.email, profile.website) if b]
    if contact_bits:
        cr = info.add_run("\n" + "  •  ".join(contact_bits))
        cr.font.size = Pt(8)
        cr.font.color.rgb = RGBColor(0x66, 0x66, 0x66)


def _build_footer(doc, profile: CompanyProfile) -> None:
    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0]
    p.alignment = _ALIGN.get(profile.footer_alignment, WD_ALIGN_PARAGRAPH.CENTER)
    if profile.footer_text:
        r = p.add_run(_x(profile.footer_text))
        r.font.size = Pt(8)
        r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    if profile.show_page_numbers:
        pn = footer.add_paragraph()
        pos = profile.page_number_position
        pn.alignment = (WD_ALIGN_PARAGRAPH.RIGHT if pos.endswith("right")
                        else WD_ALIGN_PARAGRAPH.LEFT if pos.endswith("left")
                        else WD_ALIGN_PARAGRAPH.CENTER)
        fmt = profile.page_number_format or "Page {n} of {total}"
        # split on tokens, emitting fields for {n} and {total}
        for token in re.split(r"(\{n\}|\{total\})", fmt):
            if token == "{n}":
                _add_field(pn, "PAGE")
            elif token == "{total}":
                _add_field(pn, "NUMPAGES")
            elif token:
                run = pn.add_run(_x(token))
                run.font.size = Pt(8)


def export_docx(minutes_md: str, path: str | Path,
                profile: CompanyProfile | None = None) -> Path:
    path = Path(path)
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    if profile:
        _build_header(doc, profile)
        _build_footer(doc, profile)

    accent = _hex_rgb(profile.accent_color if profile else "#8B1E1E")
    for blk in md_blocks.parse(minutes_md):
        if blk.kind in ("h1", "h2", "h3"):
            # h1 → Title, h2 → Heading 1, h3..h6 → Heading 2..5
            level = 0 if blk.kind == "h1" else max(1, min((blk.level or 2) - 1, 9))
            h = doc.add_heading(level=level)
            _add_runs(h, blk.text)
            if blk.kind == "h1":
                for r in h.runs:
                    r.font.color.rgb = accent
            _rtl_paragraph(h, blk.text)
        elif blk.kind == "kv":
            p = doc.add_paragraph()
            _add_runs(p, blk.key + ": ", force_bold=True)
            _add_runs(p, blk.text)
            _rtl_paragraph(p, f"{blk.key} {blk.text}")
        elif blk.kind == "bullet":
            for it in blk.items:
                p = doc.add_paragraph(style="List Bullet")
                _add_runs(p, it)
                _rtl_paragraph(p, it)
        elif blk.kind == "olist":
            # explicit numbers (Word's shared "List Number" numbering would
            # continue from the previous list instead of restarting)
            for k, it in enumerate(blk.items):
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Mm(6)
                p.paragraph_format.first_line_indent = Mm(-6)
                p.add_run(f"{blk.start + k}.\t")
                _add_runs(p, it)
                _rtl_paragraph(p, it)
        elif blk.kind == "table":
            cols = max(len(blk.headers), 1)
            table = doc.add_table(rows=1, cols=cols)
            table.style = "Light Grid Accent 1"
            for j, htext in enumerate(blk.headers):
                cell = table.rows[0].cells[j]
                _add_runs(cell.paragraphs[0], htext, force_bold=True)
                _rtl_paragraph(cell.paragraphs[0], htext)
            for row in blk.rows:
                cells = table.add_row().cells
                for j in range(cols):
                    val = row[j] if j < len(row) else ""
                    _add_runs(cells[j].paragraphs[0], val)
                    _rtl_paragraph(cells[j].paragraphs[0], val)
        elif blk.kind == "para":
            p = doc.add_paragraph()
            _add_runs(p, blk.text)
            _rtl_paragraph(p, blk.text)

    doc.save(str(path))
    return path
