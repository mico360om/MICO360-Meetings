"""PDF designs a company can choose for its minutes.

Every design carries the same content and the company's own letterhead, logo
position, accent colour, footer and page numbering (CompanyProfile); a design
only decides how that is laid out. The choice is stored per company
(`CompanyProfile.pdf_design`), so each company's exports stay consistent.

    classic  accent letterhead rule, accent headings, filled table header
    modern   accent strip, tinted heading bars, detail cards, light tables
    formal   serif type, numbered capital headings, ruled tables, signature block
    compact  smaller type and spacing, no fills — fewer pages, less ink
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.profiles import DEFAULT_PDF_DESIGN, PDF_DESIGNS


@dataclass(frozen=True)
class Design:
    key: str
    name: str
    blurb: str
    font: str = "sans"            # sans | serif
    body: float = 10.5            # body text size / leading
    lead: float = 15.0
    cell: float = 9.0             # table text size
    title_size: float = 20.0
    title_align: str = "left"     # left | center
    h2_size: float = 13.0
    gap: float = 1.0              # vertical spacing multiplier
    letterhead: str = "rule"      # rule under the letterhead: rule | hairline | double
    logo_max_mm: float = 20.0     # tallest the logo may be
    name_max: float = 14.0        # company-name size (shrinks to fit)
    name_accent: bool = True      # company name in the accent colour (else dark)
    top_band_mm: float = 0.0      # accent strip across the top of every page
    heading: str = "rule"         # section headings: rule | bar | numbered | plain
    details: str = "panel"        # meeting details: panel | cards | grid | inline
    table: str = "fill"           # tables: fill | tint | grid | lines
    status_color: bool = True     # colour-code the Status column
    signatures: bool = False      # "recorded by / approved by" block at the end


DESIGNS: dict[str, Design] = {
    "classic": Design(
        "classic", "Classic",
        "Brand-coloured letterhead rule and headings, a shaded details panel and "
        "tables with a solid brand-colour header."),
    "modern": Design(
        "modern", "Modern",
        "A brand-colour strip across the page, headings on tinted bars, meeting "
        "details as cards and light, airy tables.",
        title_size=22.0, letterhead="hairline", name_accent=False, top_band_mm=4.0,
        heading="bar", details="cards", table="tint"),
    "formal": Design(
        "formal", "Formal",
        "Serif type, centred title, numbered capital headings, fully ruled tables "
        "and a signature block — for board and official minutes.",
        font="serif", body=11.0, lead=15.5, cell=9.5, title_size=18.0, title_align="center",
        h2_size=12.0, letterhead="double", heading="numbered", details="grid", table="grid",
        status_color=False, signatures=True),
    "compact": Design(
        "compact", "Compact",
        "Smaller type and tighter spacing with no colour fills — long minutes in "
        "fewer pages, and light on printer ink.",
        body=9.5, lead=13.0, cell=8.5, title_size=16.0, h2_size=11.5, gap=0.7,
        letterhead="hairline", logo_max_mm=13.0, name_max=12.0, heading="plain",
        details="inline", table="lines"),
}

assert list(DESIGNS) == PDF_DESIGNS, "keep core.profiles.PDF_DESIGNS in step"


def get(key: str | None) -> Design:
    """The design for a stored key (unknown / empty -> the default)."""
    return DESIGNS.get(str(key or "").strip().lower()) or DESIGNS[DEFAULT_PDF_DESIGN]


def choices() -> list[Design]:
    return [DESIGNS[k] for k in PDF_DESIGNS]
