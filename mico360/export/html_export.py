"""Render minutes Markdown to HTML — used for the in-app preview and .html export."""
from __future__ import annotations

from pathlib import Path

from ..core.profiles import CompanyProfile
from . import md_blocks


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text: str) -> str:
    out = []
    for txt, bold in md_blocks.runs(text):
        out.append(f"<strong>{_esc(txt)}</strong>" if bold else _esc(txt))
    return "".join(out)


def render_fragment(minutes_md: str, accent: str = "#8B1E1E") -> str:
    """Return an HTML body fragment (for QTextBrowser preview)."""
    # dir='auto' lets the browser align each block by its own first strong
    # character, so Arabic blocks flip to RTL while English stays LTR.
    parts: list[str] = []
    for blk in md_blocks.parse(minutes_md):
        if blk.kind == "h1":
            parts.append(f"<h1 dir='auto' style='color:{accent}'>{_inline(blk.text)}</h1>")
        elif blk.kind == "h2":
            parts.append(f"<h2 dir='auto' style='color:{accent}'>{_inline(blk.text)}</h2>")
        elif blk.kind == "kv":
            parts.append(f"<p dir='auto'><strong>{_esc(blk.key)}:</strong> {_inline(blk.text)}</p>")
        elif blk.kind == "bullet":
            items = "".join(f"<li dir='auto'>{_inline(it)}</li>" for it in blk.items)
            parts.append(f"<ul>{items}</ul>")
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


def render_document(minutes_md: str, profile: CompanyProfile | None = None) -> str:
    accent = profile.accent_color if profile else "#8B1E1E"
    header = ""
    if profile:
        bits = " &nbsp;•&nbsp; ".join(
            _esc(b) for b in (profile.address, profile.phone, profile.email, profile.website) if b)
        logo = ""
        if profile.logo_path and Path(profile.logo_path).exists():
            logo = f"<img src='file:///{Path(profile.logo_path).as_posix()}' style='height:48px'><br>"
        header = (f"<div dir='auto' style='border-bottom:2px solid {accent};padding-bottom:8px;margin-bottom:16px'>"
                  f"{logo}<span style='font-size:18px;font-weight:700;color:{accent}'>{_esc(profile.name)}</span>"
                  f"<div style='color:#666;font-size:12px'>{bits}</div></div>")
    footer = ""
    if profile and profile.footer_text:
        footer = (f"<div style='border-top:1px solid #ddd;margin-top:24px;padding-top:8px;"
                  f"color:#888;font-size:11px'>{_esc(profile.footer_text)}</div>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:Segoe UI,Tahoma,Arial,sans-serif;max-width:820px;margin:32px auto;"
        "padding:0 24px;color:#1a2333;line-height:1.5} table{width:100%;margin:8px 0} "
        "th{text-align:start} h1{font-size:24px} h2{font-size:17px;margin-top:20px}</style></head>"
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
        bits = " · ".join(b for b in (profile.address, profile.phone, profile.email, profile.website) if b)
        head = f"**{profile.name}**" + (f"  \n{bits}" if bits else "") + "\n\n---\n\n"
    foot = f"\n\n---\n{profile.footer_text}\n" if (profile and profile.footer_text) else ""
    path.write_text(head + minutes_md.strip() + foot, encoding="utf-8")
    return path
