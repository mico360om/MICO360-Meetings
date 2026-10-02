"""Fonts for the PDF export: an embedded Unicode family plus per-character fallback.

reportlab's built-in Helvetica only covers Western European (WinAnsi)
characters: anything else — Łódź, Dvořák, Ольга, Nguyễn, 张伟, ₹ — was silently
printed as a black box. So the minutes use an EMBEDDED TrueType family
(Arial: metric-compatible with Helvetica and on every Windows install, so the
layout doesn't change), which also makes the PDF look the same in every viewer
and on every device. Characters that family lacks (Chinese/Japanese, Korean,
Indic, symbols, emoji) are drawn with the first fallback font that has them.

Without any usable TrueType font (a stripped-down system) everything falls
back to Helvetica, as before.
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path

_FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# Main family candidates: (regular, bold, italic, bold-italic). Arial first —
# it has Helvetica's metrics, so switching from the built-in font moves nothing.
_FAMILIES = [
    ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
]
# Serif family for the "Formal" PDF design (falls back to the sans family).
_SERIF_FAMILIES = [
    ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    ("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"),
]
# Per-character fallbacks, tried in order: (file, index in a .ttc collection).
_FALLBACKS = [
    ("seguisym.ttf", 0),     # symbols, arrows, check marks, dingbats
    ("msyh.ttc", 0),         # Chinese + Japanese kana (Microsoft YaHei)
    ("YuGothR.ttc", 0),      # Japanese
    ("malgun.ttf", 0),       # Korean
    ("Nirmala.ttc", 0),      # Hindi and other Indic scripts
    ("Nirmala.ttf", 0),
    ("LeelawUI.ttf", 0),     # Thai, Lao, Khmer
    ("tahoma.ttf", 0),       # Thai (older systems)
    ("sylfaen.ttf", 0),      # Armenian, Georgian
    ("ebrima.ttf", 0),       # Ethiopic and other African scripts
    ("mmrtext.ttf", 0),      # Myanmar
    ("himalaya.ttf", 0),     # Tibetan
    ("seguiemj.ttf", 0),     # emoji (monochrome outlines)
    ("simsun.ttc", 0),       # older Windows CJK
]

_lock = threading.Lock()


class Family:
    """The resolved main font names (reportlab font names)."""

    def __init__(self, regular: str, bold: str, italic: str, bold_italic: str,
                 embedded: bool, charset=None, mono: str = "Courier", mono_charset=None):
        self.regular, self.bold = regular, bold
        self.italic, self.bold_italic = italic, bold_italic
        self.embedded = embedded
        self._charset = charset                  # set of code points, or None (WinAnsi)
        self.mono = mono                         # for `code` spans
        self._mono_charset = mono_charset

    def mono_has(self, ch: str) -> bool:
        if self._mono_charset is None:
            try:
                ch.encode("cp1252")
                return True
            except UnicodeEncodeError:
                return False
        return ord(ch) in self._mono_charset

    def name(self, bold: bool = False, italic: bool = False) -> str:
        if bold and italic:
            return self.bold_italic
        return self.bold if bold else self.italic if italic else self.regular

    def has(self, ch: str) -> bool:
        if self._charset is None:
            try:
                ch.encode("cp1252")
                return True
            except UnicodeEncodeError:
                return False
        return ord(ch) in self._charset


def _register(name: str, path: Path, index: int = 0):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font = TTFont(name, str(path), subfontIndex=index) if path.suffix.lower() == ".ttc" \
        else TTFont(name, str(path))
    pdfmetrics.registerFont(font)
    return font


@lru_cache(maxsize=4)
def family(kind: str = "sans") -> Family:
    """Register (once) and return an embedded family: "sans" (the default) or
    "serif". Serif falls back to sans, sans to the built-in Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    if kind == "serif":
        got = _register_family("MICOSerif", _SERIF_FAMILIES)
        return got or family("sans")
    got = _register_family("MICOSans", _FAMILIES)
    return got or Family("Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
                         "Helvetica-BoldOblique", embedded=False)


def _register_family(base: str, candidates: list) -> "Family | None":
    from reportlab.pdfbase import pdfmetrics
    with _lock:
        for files in candidates:
            paths = [_FONTS_DIR / f for f in files]
            if not paths[0].exists():
                continue
            try:
                names = [base, f"{base}-Bold", f"{base}-Italic", f"{base}-BoldItalic"]
                reg = _register(names[0], paths[0])
                for n, p in zip(names[1:], paths[1:]):
                    _register(n, p if p.exists() else paths[0])
                pdfmetrics.registerFontFamily(names[0], normal=names[0], bold=names[1],
                                              italic=names[2], boldItalic=names[3])
                mono, mono_set = "Courier", None
                if (_FONTS_DIR / "cour.ttf").exists():
                    try:
                        mono_set = frozenset(_register("MICOMono", _FONTS_DIR / "cour.ttf").face.charToGlyph)
                        mono = "MICOMono"
                    except Exception:
                        pass
                return Family(*names, embedded=True, charset=frozenset(reg.face.charToGlyph),
                              mono=mono, mono_charset=mono_set)
            except Exception:
                continue
    return None


_loaded: list[tuple[str, frozenset]] = []
_tried = 0


def _fallback_for(ch: str) -> str | None:
    """Name of the first fallback font that has `ch` (fonts load lazily)."""
    global _tried
    cp = ord(ch)
    for name, charset in _loaded:
        if cp in charset:
            return name
    with _lock:
        while _tried < len(_FALLBACKS):
            file, index = _FALLBACKS[_tried]
            _tried += 1
            path = _FONTS_DIR / file
            if not path.exists():
                continue
            name = f"MICOFallback{_tried}"
            try:
                font = _register(name, path, index)
            except Exception:
                continue
            charset = frozenset(font.face.charToGlyph)
            _loaded.append((name, charset))
            if cp in charset:
                return name
    return None


@lru_cache(maxsize=8192)
def font_for(ch: str, kind: str = "sans") -> str | None:
    """None if the main family has `ch`, else the fallback font to use (or None
    when no installed font has it — the main font then shows its missing-glyph
    box, which is the best anyone can do)."""
    if ch.isspace() or ord(ch) < 0x20 or family(kind).has(ch):
        return None
    return _fallback_for(ch)


def runs(text: str, kind: str = "sans") -> list[tuple[str, str | None]]:
    """Split text into (segment, fallback font or None) runs."""
    out: list[tuple[str, str | None]] = []
    cur, cur_font = [], None
    for ch in text:
        f = font_for(ch, kind)
        if f != cur_font and cur:
            out.append(("".join(cur), cur_font))
            cur = []
        cur_font = f
        cur.append(ch)
    if cur:
        out.append(("".join(cur), cur_font))
    return out or [("", None)]
