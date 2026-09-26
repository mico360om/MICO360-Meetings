"""Render minutes Markdown to HTML — used for the in-app preview and .html export."""
from __future__ import annotations

import html as _html
import re
from pathlib import Path

from ..core.profiles import CompanyProfile
from . import md_blocks

DEFAULT_ACCENT = "#8B1E1E"
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{3,8}$")


def safe_color(value, default: str = DEFAULT_ACCENT) -> str:
    """A #hex colour, or `default`. Profile colours come from imported files and
    are placed inside style attributes — anything else could inject markup."""
    s = str(value or "").strip()
    return s if _COLOR_RE.match(s) else default


def _esc(s: str) -> str:
    return (md_blocks.clean_text(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _attr(s: str) -> str:
    """Escape for a double-quoted attribute value."""
    return _html.escape(md_blocks.clean_text(s), quote=True)


def _inline(text: str) -> str:
    out = []
    for txt, bold in md_blocks.runs(text):
        out.append(f"<strong>{_esc(txt)}</strong>" if bold else _esc(txt))
    return "".join(out)


def render_fragment(minutes_md: str, accent: str = DEFAULT_ACCENT) -> str:
    """Return an HTML body fragment (for QTextBrowser preview)."""
    accent = safe_color(accent)
    # dir='auto' lets the browser align each block by its own first strong
    # character, so Arabic blocks flip to RTL while English stays LTR.
    parts: list[str] = []
    for blk in md_blocks.parse(minutes_md):
        if blk.kind == "h1":
            parts.append(f"<h1 dir='auto' style='color:{accent}'>{_inline(blk.text)}</h1>")
        elif blk.kind == "h2":
            parts.append(f"<h2 dir='auto' style='color:{accent}'>{_inline(blk.text)}</h2>")
        elif blk.kind == "h3":
            lvl = min(max(blk.level, 3), 6)
            parts.append(f"<h{lvl} dir='auto' style='color:{accent}'>{_inline(blk.text)}</h{lvl}>")
        elif blk.kind == "kv":
            parts.append(f"<p dir='auto'><strong>{_inline(blk.key)}:</strong> {_inline(blk.text)}</p>")
        elif blk.kind == "bullet":
            items = "".join(f"<li dir='auto'>{_inline(it)}</li>" for it in blk.items)
            parts.append(f"<ul>{items}</ul>")
        elif blk.kind == "olist":
            items = "".join(f"<li dir='auto'>{_inline(it)}</li>" for it in blk.items)
            start = f" start='{int(blk.start)}'" if blk.start != 1 else ""
            parts.append(f"<ol{start}>{items}</ol>")
        elif blk.kind == "table":
            head = "".join(f"<th dir='auto'>{_inline(h)}</th>" for h in blk.headers)
            rows = ""
            for row in blk.rows:
                cells = "".join(
                    f"<td dir='auto'>{_inline(row[j] if j < len(row) else '')}</td>"
                    for j in range(len(blk.headers)))
                rows += f"<tr>{cells}</tr>"
            parts.append(
                f"<table border='1' cellspacing='0' cellpadding='6' "
                f"style='border-collapse:collapse;border-color:#ccc'>"
                f"<thead style='background:{accent};color:#fff'><tr>{head}</tr></thead>"
                f"<tbody>{rows}</tbody></table>")
        elif blk.kind == "para":
            parts.append(f"<p dir='auto'>{_inline(blk.text)}</p>")
    return "\n".join(parts)


def _logo_src(logo_path: str) -> str:
    """A proper file:// URI for the logo (quotes, '#', spaces, non-ASCII all
    percent-encoded), or '' when there is no usable file."""
    try:
        p = Path(logo_path)
        if not (logo_path and p.is_file()):
            return ""
        return p.resolve().as_uri()
    except Exception:
        return ""


def render_document(minutes_md: str, profile: CompanyProfile | None = None) -> str:
    accent = safe_color(profile.accent_color if profile else DEFAULT_ACCENT)
    header = ""
    if profile:
        bits = " &nbsp;•&nbsp; ".join(
            _esc(str(b)) for b in (profile.address, profile.phone, profile.email,
                                   profile.website) if b)
        logo = ""
        src = _logo_src(profile.logo_path)
        if src:
            logo = f'<img src="{_attr(src)}" alt="" style="height:48px"><br>'
        header = (f"<div dir='auto' style='border-bottom:2px solid {accent};padding-bottom:8px;margin-bottom:16px'>"
                  f"{logo}<span style='font-size:18px;font-weight:700;color:{accent}'>{_esc(str(profile.name))}</span>"
                  f"<div style='color:#666;font-size:12px'>{bits}</div></div>")
    footer = ""
    if profile and profile.footer_text:
        footer = (f"<div style='border-top:1px solid #ddd;margin-top:24px;padding-top:8px;"
                  f"color:#888;font-size:11px'>{_esc(str(profile.footer_text))}</div>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:Segoe UI,Tahoma,Arial,sans-serif;max-width:820px;margin:32px auto;"
        "padding:0 24px;color:#1a2333;line-height:1.5} table{width:100%;margin:8px 0} "
        "th{text-align:start} h1{font-size:24px} h2{font-size:17px;margin-top:20px} "
        "h3,h4,h5,h6{font-size:14px;margin-top:14px}</style></head>"
        f"<body dir='auto'>{header}{render_fragment(minutes_md, accent)}{footer}</body></html>")


def export_html(minutes_md: str, path: str | Path, profile: CompanyProfile | None = None) -> Path:
    path = Path(path)
    path.write_text(render_document(minutes_md, profile), encoding="utf-8")
    return path


def export_md(minutes_md: str, path: str | Path, profile: CompanyProfile | None = None) -> Path:
    """Markdown export — the minutes are already Markdown; prepend a profile header."""
    path = Path(path)
    head = ""
    if profile:
        bits = " · ".join(str(b) for b in (profile.address, profile.phone, profile.email,
                                           profile.website) if b)
        head = f"**{profile.name}**" + (f"  \n{bits}" if bits else "") + "\n\n---\n\n"
    foot = f"\n\n---\n{profile.footer_text}\n" if (profile and profile.footer_text) else ""
    path.write_text(md_blocks.clean_text(head + (minutes_md or "").strip() + foot),
                    encoding="utf-8")
    return path
