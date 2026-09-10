"""A small, dependency-free Markdown block parser.

It understands just the subset our minutes use: H1/H2 headings, **key:** value
lines, bullet lists, pipe tables, and plain paragraphs. Inline **bold** spans
are surfaced as (text, bold) runs so each exporter can style them natively.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_KV_RE = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")


@dataclass
class Block:
    kind: str                       # h1 h2 kv bullet table para
    text: str = ""
    key: str = ""
    items: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def runs(text: str) -> list[tuple[str, bool]]:
    """Split a line into (text, is_bold) runs based on **bold** markers."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out or [("", False)]


def _is_table_sep(line: str) -> bool:
    return bool(re.match(r"^\s*\|?[\s:|-]+\|?\s*$", line)) and "-" in line


def _split_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    return [c.strip() for c in line.split("|")]


def parse(markdown: str) -> list[Block]:
    lines = markdown.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Tables: a header row followed by a separator row. The header may or may
        # not have a leading pipe (small models often drop it); guard against
        # headings/bullets that merely happen to contain a '|'.
        if ("|" in stripped and i + 1 < n and _is_table_sep(lines[i + 1])
                and not stripped.startswith(("#", "- ", "* ", "• "))):
            headers = _split_row(stripped)
            rows: list[list[str]] = []
            i += 2
            # Body rows contain a '|' (with or without a leading one) and are not
            # themselves separator rows; a blank line ends the table.
            while i < n and "|" in lines[i] and not _is_table_sep(lines[i]):
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append(Block("table", headers=headers, rows=rows))
            continue

        if stripped.startswith("## "):
            blocks.append(Block("h2", text=stripped[3:].strip()))
            i += 1
            continue
        if stripped.startswith("# "):
            blocks.append(Block("h1", text=stripped[2:].strip()))
            i += 1
            continue

        kv = _KV_RE.match(stripped)
        if kv:
            blocks.append(Block("kv", key=kv.group(1).strip(), text=kv.group(2).strip()))
            i += 1
            continue

        if stripped.startswith(("- ", "* ", "• ")):
            items: list[str] = []
            while i < n and lines[i].strip().startswith(("- ", "* ", "• ")):
                items.append(lines[i].strip()[2:].strip())
                i += 1
            blocks.append(Block("bullet", items=items))
            continue

        # Paragraph: always consume the current line first so `i` advances even for
        # a stray '|' line that wasn't a table (otherwise the loop would hang),
        # then gather following non-special lines.
        para: list[str] = [stripped]
        i += 1
        while i < n and lines[i].strip() and not lines[i].strip().startswith(
            ("#", "- ", "* ", "• ", "|")
        ) and not _KV_RE.match(lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        blocks.append(Block("para", text=" ".join(para)))
    return blocks
