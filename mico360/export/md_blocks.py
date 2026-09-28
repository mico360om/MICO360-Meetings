"""A small, dependency-free Markdown block parser.

It understands just the subset our minutes use: headings (# … ######),
**key:** value lines, bullet lists, numbered lists, pipe tables, and plain
paragraphs. Inline **bold** spans are surfaced as (text, bold) runs so each
exporter can style them natively.

Block kinds:  h1  h2  h3 (level 3-6; see Block.level)  kv  bullet
              olist (ordered list; Block.start is the first number)  table  para
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_KV_RE = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_OLIST_RE = re.compile(r"^(\d{1,4})[.)]\s+(.*)$")
_BULLETS = ("- ", "* ", "• ", "+ ")
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
# Characters XML 1.0 (DOCX) cannot hold, plus other C0 controls that render as
# junk: keep \t \n \r only. (A pasted \x0b crashed DOCX export.)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿\ud800-\udfff]")


def clean_text(s) -> str:
    """Drop control characters that are invalid in XML / meaningless in print."""
    return _CTRL_RE.sub("", "" if s is None else str(s))


@dataclass
class Block:
    kind: str                       # h1 h2 h3 kv bullet olist table para
    text: str = ""
    key: str = ""
    items: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    level: int = 0                  # heading level (1-6) for h1/h2/h3
    start: int = 1                  # first number of an ordered list


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


def plain(text: str) -> str:
    """The text with **bold** markers removed."""
    return "".join(t for t, _b in runs(text))


def _is_table_sep(line: str) -> bool:
    return bool(re.match(r"^\s*\|?[\s:|-]+\|?\s*$", line)) and "-" in line


def _split_cells(line: str) -> list[str]:
    """Split a table row on UNESCAPED pipes that are not inside `code` spans.
    '\\|' becomes a literal '|' in the cell."""
    s = line.strip()
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|"); i += 2
            continue
        if ch == "`":
            # Only a PAIRED backtick opens a code span: a lone one ("Don`t") is
            # a literal character — toggling on it hid every later '|' and
            # emptied the whole row.
            if in_code:
                in_code = False
            elif "`" in s[i + 1:]:
                in_code = True
        if ch == "|" and not in_code:
            cells.append("".join(buf)); buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf))
    # a leading / trailing pipe produces an empty edge cell — drop those
    if s.startswith("|") and cells:
        cells = cells[1:]
    if s.endswith("|") and not s.endswith("\\|") and cells:
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _split_row(line: str) -> list[str]:
    return _split_cells(line)


_INDEX_HEADERS = {"#", "no", "no.", "nr", "s/n", "sn", "sr", "sr.", "م", "رقم", "الرقم"}


def _fit_row(row: list[str], headers: list[str]) -> list[str]:
    """Keep a row aligned with its headers when it has EXTRA cells (an
    unescaped '|' inside a cell): trailing empty cells are dropped, and any
    remaining overflow is merged back into the main text column (the first one
    that isn't a row-number column) instead of shifting every later column."""
    n = len(headers)
    if n == 0 or len(row) <= n:
        return row
    row = list(row)
    while len(row) > n and not row[-1]:
        row.pop()
    extra = len(row) - n
    if extra <= 0:
        return row
    target = 1 if (n > 1 and headers[0].strip().lower() in _INDEX_HEADERS) else 0
    merged = " | ".join(row[target:target + extra + 1])
    return row[:target] + [merged] + row[target + extra + 1:]


def _is_table_start(lines: list[str], i: int) -> bool:
    """A header row (contains '|', not a heading/bullet) followed by a separator."""
    s = lines[i].strip()
    return ("|" in s and i + 1 < len(lines) and _is_table_sep(lines[i + 1])
            and not s.startswith(("#",) + _BULLETS) and not _is_table_sep(s))


def _is_special(lines: list[str], i: int) -> bool:
    """True when line i starts a non-paragraph block (so a paragraph ends)."""
    s = lines[i].strip()
    return bool(
        not s or s.startswith(("#", "|") + _BULLETS) or _KV_RE.match(s)
        or _OLIST_RE.match(s) or _HR_RE.match(s) or _is_table_start(lines, i))


def parse(markdown: str) -> list[Block]:
    lines = clean_text(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
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
        if _is_table_start(lines, i):
            headers = _split_row(stripped) or [""]   # a lone '|' header: one empty column
            rows: list[list[str]] = []
            i += 2
            # Body rows contain a '|' (with or without a leading one) and are not
            # themselves separator rows; a blank line ends the table.
            while i < n and "|" in lines[i] and not _is_table_sep(lines[i]):
                rows.append(_fit_row(_split_row(lines[i]), headers))
                i += 1
            blocks.append(Block("table", headers=headers, rows=rows))
            continue

        hm = _HEADING_RE.match(stripped)
        if hm:
            level = len(hm.group(1))
            kind = "h1" if level == 1 else "h2" if level == 2 else "h3"
            blocks.append(Block(kind, text=hm.group(2).strip(), level=level))
            i += 1
            continue

        if _HR_RE.match(stripped):          # horizontal rule: layout only
            i += 1
            continue

        kv = _KV_RE.match(stripped)
        if kv:
            blocks.append(Block("kv", key=kv.group(1).strip(), text=kv.group(2).strip()))
            i += 1
            continue

        if stripped.startswith(_BULLETS):
            items: list[str] = []
            while i < n and lines[i].strip().startswith(_BULLETS):
                items.append(lines[i].strip()[2:].strip())
                i += 1
            blocks.append(Block("bullet", items=items))
            continue

        om = _OLIST_RE.match(stripped)
        if om:
            start = int(om.group(1))
            items = []
            while i < n:
                m2 = _OLIST_RE.match(lines[i].strip())
                if not m2:
                    break
                items.append(m2.group(2).strip())
                i += 1
            blocks.append(Block("olist", items=items, start=start))
            continue

        # Paragraph: always consume the current line first so `i` advances even for
        # a stray '|' line that wasn't a table (otherwise the loop would hang),
        # then gather following lines until a blank line or another block starts
        # (including a table that directly follows the text).
        para: list[str] = [stripped]
        i += 1
        while i < n and not _is_special(lines, i):
            para.append(lines[i].strip())
            i += 1
        blocks.append(Block("para", text=" ".join(para)))
    return blocks
