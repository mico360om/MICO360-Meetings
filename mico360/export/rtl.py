"""Arabic / right-to-left helpers shared by the exporters.

Arabic needs three things that plain text output does not provide:
  * detection    — is a run Arabic at all?
  * shaping      — reportlab (and any non-shaping renderer) draws code points as
                   isolated, left-to-right letters; real Arabic joins its letters
                   and runs right-to-left. `shape()` reshapes + reorders so a
                   non-shaping renderer draws it correctly.
  * a font       — the reportlab built-ins (Helvetica) carry no Arabic glyphs.
                   `pdf_arabic_font()` registers a system Arabic font on demand.

Qt (on-screen), browsers (HTML) and Word (DOCX) all shape Arabic themselves, so
they only need detection to set direction/alignment — never `shape()`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# Arabic + Arabic Supplement + Extended-A + presentation forms A/B.
_ARABIC_RANGES = (
    (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
)


def has_arabic(text: str) -> bool:
    """True if the string contains at least one Arabic-script character."""
    for ch in text:
        cp = ord(ch)
        for lo, hi in _ARABIC_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def shape(text: str) -> str:
    """Reshape + bidi-reorder Arabic for renderers that do not shape (reportlab).

    Returns the visual-order string. A no-op for non-Arabic text, and a safe
    fall-back (the original text) if the optional shaping libraries are absent.
    """
    if not has_arabic(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


# Windows always ships these; the first two shape Arabic well. A bundled OFL
# font (assets/fonts/*.ttf) wins when present so the look is machine-independent.
_CANDIDATES = (
    ("Tahoma", "tahoma.ttf", "tahomabd.ttf"),
    ("Arial", "arial.ttf", "arialbd.ttf"),
    ("Segoe UI", "segoeui.ttf", "segoeuib.ttf"),
    ("Calibri", "calibri.ttf", "calibrib.ttf"),
)


@lru_cache(maxsize=1)
def pdf_arabic_font() -> tuple[str | None, str | None]:
    """Register an Arabic-capable TTF with reportlab; return (regular, bold) names.

    Returns (None, None) if no suitable font can be found or registered — callers
    then leave the text in the default font (glyphs may be missing, but no crash).
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    reg, bold = "MICOArabic", "MICOArabic-Bold"

    # 1) a bundled font shipped with the app (future-proof, license permitting)
    here = Path(__file__).resolve().parent
    for base in (here.parent.parent / "assets" / "fonts", here / "fonts"):
        if base.is_dir():
            ttfs = sorted(base.glob("*.ttf"))
            if ttfs:
                try:
                    pdfmetrics.registerFont(TTFont(reg, str(ttfs[0])))
                    bold_ttf = next((t for t in ttfs if "bold" in t.name.lower()), ttfs[0])
                    pdfmetrics.registerFont(TTFont(bold, str(bold_ttf)))
                    pdfmetrics.registerFontFamily(reg, normal=reg, bold=bold)
                    return reg, bold
                except Exception:
                    pass

    # 2) a system Arabic font (Windows)
    import os
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for _name, reg_file, bold_file in _CANDIDATES:
        reg_path = fonts_dir / reg_file
        if not reg_path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(reg, str(reg_path)))
            bold_path = fonts_dir / bold_file
            pdfmetrics.registerFont(TTFont(
                bold, str(bold_path if bold_path.exists() else reg_path)))
            pdfmetrics.registerFontFamily(reg, normal=reg, bold=bold)
            return reg, bold
        except Exception:
            continue

    return None, None
