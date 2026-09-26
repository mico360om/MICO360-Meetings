"""Plain-text export. Strips Markdown emphasis but keeps structure readable."""
from __future__ import annotations

import re
from pathlib import Path

from ..core.profiles import CompanyProfile


def _header(profile: CompanyProfile | None) -> str:
    if not profile:
        return ""
    lines = [profile.name]
    for part in (profile.address, profile.phone, profile.email, profile.website):
        if part:
            lines.append(part)
    lines.append("=" * 60)
    return "\n".join(lines) + "\n\n"


def export_txt(minutes_md: str, path: str | Path, profile: CompanyProfile | None = None) -> Path:
    path = Path(path)
    from .md_blocks import clean_text
    body = clean_text(minutes_md or "").replace("**", "")
    body = re.sub(r"^#+\s*", "", body, flags=re.MULTILINE)
    text = _header(profile) + body.strip() + "\n"
    if profile and profile.footer_text:
        text += "\n" + "-" * 60 + "\n" + str(profile.footer_text) + "\n"
    path.write_text(clean_text(text), encoding="utf-8")
    return path
