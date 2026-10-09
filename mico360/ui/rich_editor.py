"""A rich-text editor for the minutes, backed by Markdown.

The minutes are stored, generated, exported and e-mailed as Markdown (the
headings / **Key:** lines / lists / tables that export/md_blocks understands).
This editor shows and edits them as formatted text, and converts back to that
Markdown so every export is exactly what the editor shows.

    markdown_to_html(md)        -> HTML the editor loads (headings, bold,
                                   italic, strike-through, code, links, lists,
                                   tables)
    document_to_markdown(doc)   -> the Markdown for a QTextDocument, walking
                                   its blocks, lists and tables (Qt's own
                                   toMarkdown() merges consecutive lines and
                                   mangles tables, so it is not used)

Round trip: markdown -> editor -> markdown keeps every block md_blocks parses
(tests/fixes_editor.py checks this on generated minutes too).
"""
from __future__ import annotations

import re

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import (
    QFont, QKeySequence, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
    QTextListFormat, QTextTable, QTextTableFormat,
)
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from ..export import md_blocks, rtl
from .components import tip

# ---------------------------------------------------------------------------
# Markdown -> HTML (what the editor loads)
# ---------------------------------------------------------------------------
_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(((?:https?://|mailto:)[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
_ITAL_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"
                      r"|(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_PH_RE = re.compile(r"\x00(\d+)\x00")
HEADING_SIZES = {1: 20, 2: 15, 3: 13, 4: 12, 5: 11, 6: 11}
BODY_PT = 11
TABLE_BORDER = "#8A8088"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline_html(text: str) -> str:
    """Inline Markdown -> HTML: **bold**, *italic*, ~~strike~~, `code`, links."""
    keep: list[str] = []

    def hold(markup: str) -> str:
        keep.append(markup)
        return f"\x00{len(keep) - 1}\x00"

    s = _CODE_RE.sub(lambda m: hold(f"<code>{_esc(m.group(1))}</code>"), text or "")
    s = _LINK_RE.sub(lambda m: hold(f'<a href="{_esc(m.group(2))}">{_esc(m.group(1))}</a>'), s)
    s = _esc(s)
    s = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _STRIKE_RE.sub(r"<s>\1</s>", s)
    s = _ITAL_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)
    return _PH_RE.sub(lambda m: keep[int(m.group(1))], s)


def _dir(text: str) -> str:
    return ' dir="rtl"' if rtl.has_arabic(text) else ""


def markdown_to_html(md: str) -> str:
    parts: list[str] = []
    for blk in md_blocks.parse(md):
        if blk.kind in ("h1", "h2", "h3"):
            lvl = blk.level if blk.kind == "h3" else (1 if blk.kind == "h1" else 2)
            lvl = min(max(lvl, 1), 6)
            parts.append(f"<h{lvl}{_dir(blk.text)}>{inline_html(blk.text)}</h{lvl}>")
        elif blk.kind == "kv":
            parts.append(f"<p{_dir(blk.key)}><b>{inline_html(blk.key)}:</b> {inline_html(blk.text)}</p>")
        elif blk.kind == "bullet":
            items = "".join(f"<li{_dir(it)}>{inline_html(it)}</li>" for it in blk.items)
            parts.append(f"<ul>{items}</ul>")
        elif blk.kind == "olist":
            items = "".join(f"<li{_dir(it)}>{inline_html(it)}</li>" for it in blk.items)
            start = f' start="{blk.start}"' if blk.start != 1 else ""
            parts.append(f"<ol{start}>{items}</ol>")
        elif blk.kind == "table":
            n = max(len(blk.headers), 1)
            head = "".join(f"<th{_dir(h)}>{inline_html(h)}</th>" for h in blk.headers) or "<th></th>"
            body = ""
            for row in blk.rows:
                cells = [row[j] if j < len(row) else "" for j in range(n)]
                body += "<tr>" + "".join(f"<td{_dir(c)}>{inline_html(c)}</td>" for c in cells) + "</tr>"
            parts.append(f'<table border="1" cellspacing="0" cellpadding="4" '
                         f'bordercolor="{TABLE_BORDER}" width="100%"><thead><tr>{head}</tr></thead>'
                         f"<tbody>{body}</tbody></table>")
        elif blk.kind == "para":
            parts.append(f"<p{_dir(blk.text)}>{inline_html(blk.text)}</p>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# QTextDocument -> Markdown
# ---------------------------------------------------------------------------
def _is_code(fmt: QTextCharFormat) -> bool:
    if fmt.fontFixedPitch():
        return True
    try:                                     # Qt 6: fontFamilies() (fontFamily() is gone)
        fams = fmt.fontFamilies()
        fam = str(fams[0]) if isinstance(fams, (list, tuple)) and fams else str(fams or "")
    except Exception:
        fam = ""
    fam = fam.lower()
    return "courier" in fam or "mono" in fam


def _runs(block, in_heading: bool = False) -> list[tuple[str, tuple]]:
    """(text, (bold, italic, strike, code, href)) runs of a block, merged."""
    out: list[tuple[str, tuple]] = []
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid():
            f = frag.charFormat()
            key = (bool(f.fontWeight() >= 600) and not in_heading, f.fontItalic(),
                   f.fontStrikeOut(), _is_code(f), f.anchorHref() if f.isAnchor() else "")
            text = frag.text().replace("\u2028", "\n").replace("\u2029", "\n")
            if out and out[-1][1] == key:
                out[-1] = (out[-1][0] + text, key)
            else:
                out.append((text, key))
        it += 1
    return out


def _wrap(text: str, open_: str, close: str) -> str:
    """Markdown markers must hug the text: '** a **' is not bold."""
    lead = len(text) - len(text.lstrip())
    trail = len(text) - len(text.rstrip())
    core = text.strip()
    if not core:
        return text
    return text[:lead] + open_ + core + close + (text[len(text) - trail:] if trail else "")


def _run_md(text: str, key: tuple, in_cell: bool) -> str:
    bold, italic, strike, code, href = key
    if in_cell:
        text = text.replace("|", "\\|").replace("\n", " ")
    if code:
        s = f"`{text}`" if text.strip() else text
    else:
        s = text
        if strike:
            s = _wrap(s, "~~", "~~")
        if bold and italic:
            s = _wrap(s, "***", "***")
        elif bold:
            s = _wrap(s, "**", "**")
        elif italic:
            s = _wrap(s, "*", "*")
    if href:
        s = f"[{s.strip() or href}]({href})"
    return s


def block_markdown(block, in_cell: bool = False, in_heading: bool = False) -> str:
    return "".join(_run_md(t, k, in_cell) for t, k in _runs(block, in_heading)).strip()


def _table_markdown(table: QTextTable) -> str:
    rows, cols = table.rows(), table.columns()
    grid: list[list[str]] = []
    for r in range(rows):
        row = []
        for c in range(cols):
            cell = table.cellAt(r, c)
            texts = []
            cur = cell.firstCursorPosition()
            end = cell.lastCursorPosition().position()
            blk = cur.block()
            while blk.isValid() and blk.position() <= end:
                t = block_markdown(blk, in_cell=True, in_heading=(r == 0))
                if t:
                    texts.append(t)
                blk = blk.next()
            row.append(" ".join(texts))
        grid.append(row)
    if not grid:
        return ""
    head = grid[0]
    lines = ["| " + " | ".join(h or " " for h in head) + " |",
             "| " + " | ".join("---" for _ in head) + " |"]
    lines += ["| " + " | ".join(c or " " for c in row) + " |" for row in grid[1:]]
    return "\n".join(lines)


def _list_prefix(block) -> str:
    lst = block.textList()
    if lst is None:
        return ""
    style = lst.format().style()
    if style in (QTextListFormat.Style.ListDecimal, QTextListFormat.Style.ListLowerAlpha,
                 QTextListFormat.Style.ListUpperAlpha, QTextListFormat.Style.ListLowerRoman,
                 QTextListFormat.Style.ListUpperRoman):
        return f"{lst.itemNumber(block) + max(1, lst.format().start() if lst.format().start() > 0 else 1)}. "
    return "- "


def document_to_markdown(doc: QTextDocument) -> str:
    chunks: list[str] = []          # each chunk is one block of Markdown
    root = doc.rootFrame()
    it = root.begin()
    pending_list: list[str] = []
    pending_owner: list = [None]            # the QTextList the pending items belong to

    def flush_list():
        if pending_list:
            chunks.append("\n".join(pending_list))
            pending_list.clear()
        pending_owner[0] = None

    while not it.atEnd():
        frame = it.currentFrame()
        block = it.currentBlock()
        it += 1                                     # advance first: no branch may skip it
        if frame is not None:
            flush_list()
            if isinstance(frame, QTextTable):
                md = _table_markdown(frame)
                if md:
                    chunks.append(md)
            else:                                   # a plain frame: its blocks
                sub = frame.begin()
                while not sub.atEnd():
                    b = sub.currentBlock()
                    if b.isValid():
                        t = block_markdown(b)
                        if t:
                            chunks.append(t)
                    sub += 1
        elif block.isValid():
            lvl = block.blockFormat().headingLevel()
            prefix = _list_prefix(block)
            text = block_markdown(block, in_heading=lvl > 0)
            if prefix:
                if pending_owner[0] is not None and pending_owner[0] != block.textList():
                    flush_list()                    # a different list: keep them apart
                pending_owner[0] = block.textList()
                if text:
                    pending_list.append(prefix + text)
                continue
            flush_list()
            if not text:
                continue
            if lvl > 0:
                chunks.append("#" * min(lvl, 6) + " " + text)
            else:
                chunks.append(text)
    flush_list()
    out = "\n\n".join(chunks).strip()
    return out + "\n" if out else ""


# ---------------------------------------------------------------------------
# the widget
# ---------------------------------------------------------------------------
class _TextEdit(QTextEdit):
    """QTextEdit with minutes-friendly keys: Enter after a heading starts a
    normal paragraph; Enter on an empty list item leaves the list; pasted
    rich text is normalised to what the minutes can hold."""

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            cur = self.textCursor()
            blk = cur.block()
            lst = blk.textList()
            if lst is not None and not blk.text().strip():          # leave the list
                fmt = QTextBlockFormat()
                cur.beginEditBlock()
                lst.remove(blk)
                fmt.setIndent(0)
                cur.setBlockFormat(fmt)
                cur.setCharFormat(QTextCharFormat())
                cur.endEditBlock()
                return
            if blk.blockFormat().headingLevel() > 0 and cur.atBlockEnd():
                cur.beginEditBlock()
                cur.insertBlock(QTextBlockFormat(), QTextCharFormat())
                cur.endEditBlock()
                self.setTextCursor(cur)
                self.setCurrentCharFormat(QTextCharFormat())
                return
        super().keyPressEvent(e)

    def insertFromMimeData(self, source):
        if source is not None and source.hasHtml():
            tmp = QTextDocument()
            tmp.setHtml(source.html())
            md = document_to_markdown(tmp)
            self.textCursor().insertHtml(markdown_to_html(md) or _esc(source.text()))
        elif source is not None and source.hasText():
            self.textCursor().insertText(source.text())
        else:
            super().insertFromMimeData(source)


class RichMinutesEditor(QWidget):
    """Formatting toolbar + editor. `set_markdown()` loads, `to_markdown()`
    returns the Markdown of what is shown; `changed` fires on every edit."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._loading = False
        self.edit = _TextEdit()
        self.edit.setAcceptRichText(True)
        self.edit.setTabChangesFocus(False)
        self.edit.document().setDefaultFont(QFont(self.font().family(), BODY_PT))
        self.edit.document().setDocumentMargin(10)
        lay.addWidget(self._build_toolbar())
        lay.addWidget(self.edit, 1)
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.cursorPositionChanged.connect(self._sync_toolbar)

    # -- toolbar ----------------------------------------------------------------
    def _build_toolbar(self) -> QWidget:
        bar = QWidget(); bar.setObjectName("EditorBar")
        h = QHBoxLayout(bar); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        self.style_box = QComboBox()
        for label, lvl in (("Normal text", 0), ("Heading 1", 1), ("Heading 2", 2), ("Heading 3", 3)):
            self.style_box.addItem(label, lvl)
        self.style_box.activated.connect(lambda *_: self.set_heading(self.style_box.currentData()))
        tip(self.style_box, "Paragraph style: normal text or a section heading")
        h.addWidget(self.style_box)

        def button(text, slot, tooltip, shortcut=None, checkable=True, bold=False, italic=False,
                   strike=False):
            b = QToolButton(); b.setText(text); b.setCheckable(checkable)
            b.setCursor(Qt.PointingHandCursor); b.setObjectName("EditorBtn")
            f = b.font(); f.setBold(bold); f.setItalic(italic); f.setStrikeOut(strike); b.setFont(f)
            b.clicked.connect(slot)
            if shortcut:
                b.setShortcut(QKeySequence(shortcut))
                tooltip = f"{tooltip}  ({shortcut})"
            tip(b, tooltip)
            h.addWidget(b)
            return b
        self.bold_btn = button("B", self.toggle_bold, "Bold", "Ctrl+B", bold=True)
        self.italic_btn = button("I", self.toggle_italic, "Italic", "Ctrl+I", italic=True)
        self.strike_btn = button("S", self.toggle_strike, "Strike-through", "Ctrl+Shift+X", strike=True)
        h.addSpacing(8)
        self.bullet_btn = button("•  List", self.toggle_bullets, "Bulleted list", "Ctrl+Shift+8")
        self.number_btn = button("1.  List", self.toggle_numbers, "Numbered list", "Ctrl+Shift+7")
        h.addSpacing(8)
        button("Table", self.insert_table, "Insert a table (for example action items)", checkable=False)
        self.row_btn = button("+ Row", self.add_row, "Add a row below the current table row", checkable=False)
        self.del_row_btn = button("− Row", self.remove_row, "Delete the current table row", checkable=False)
        h.addStretch()
        return bar

    def _sync_toolbar(self):
        cur = self.edit.textCursor()
        f = cur.charFormat()
        self.bold_btn.setChecked(f.fontWeight() >= 600)
        self.italic_btn.setChecked(f.fontItalic())
        self.strike_btn.setChecked(f.fontStrikeOut())
        lst = cur.block().textList()
        style = lst.format().style() if lst else None
        self.bullet_btn.setChecked(style == QTextListFormat.Style.ListDisc)
        self.number_btn.setChecked(style == QTextListFormat.Style.ListDecimal)
        lvl = cur.block().blockFormat().headingLevel()
        self.style_box.setCurrentIndex(max(0, self.style_box.findData(min(lvl, 3))))
        in_table = cur.currentTable() is not None
        self.row_btn.setEnabled(in_table); self.del_row_btn.setEnabled(in_table)

    # -- formatting actions ---------------------------------------------------------
    def _merge(self, fmt: QTextCharFormat):
        cur = self.edit.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.WordUnderCursor)
        cur.mergeCharFormat(fmt)
        self.edit.mergeCurrentCharFormat(fmt)

    def toggle_bold(self):
        f = QTextCharFormat()
        f.setFontWeight(QFont.Weight.Normal if self.edit.fontWeight() >= 600 else QFont.Weight.Bold)
        self._merge(f)

    def toggle_italic(self):
        f = QTextCharFormat(); f.setFontItalic(not self.edit.fontItalic()); self._merge(f)

    def toggle_strike(self):
        f = QTextCharFormat(); f.setFontStrikeOut(not self.edit.currentCharFormat().fontStrikeOut())
        self._merge(f)

    def set_heading(self, level: int):
        cur = self.edit.textCursor()
        cur.beginEditBlock()
        bf = cur.blockFormat()
        bf.setHeadingLevel(level)
        cur.setBlockFormat(bf)
        cf = QTextCharFormat()
        cf.setFontPointSize(HEADING_SIZES.get(level, BODY_PT) if level else BODY_PT)
        cf.setFontWeight(QFont.Weight.Bold if level else QFont.Weight.Normal)
        cur.select(QTextCursor.BlockUnderCursor)
        cur.mergeCharFormat(cf)
        cur.endEditBlock()
        self.edit.setFocus()

    def _toggle_list(self, style):
        cur = self.edit.textCursor()
        lst = cur.block().textList()
        cur.beginEditBlock()
        if lst is not None and lst.format().style() == style:
            lst.remove(cur.block())
            bf = cur.blockFormat(); bf.setIndent(0); cur.setBlockFormat(bf)
        else:
            fmt = QTextListFormat(); fmt.setStyle(style); fmt.setIndent(1)
            cur.createList(fmt)
        cur.endEditBlock()
        self.edit.setFocus()

    def toggle_bullets(self):
        self._toggle_list(QTextListFormat.Style.ListDisc)

    def toggle_numbers(self):
        self._toggle_list(QTextListFormat.Style.ListDecimal)

    def insert_table(self, rows: int | None = None, cols: int | None = None,
                     headers: list[str] | None = None):
        if rows is None:
            rows, ok = QInputDialog.getInt(self, "Insert table", "Rows (including the header):", 3, 2, 50)
            if not ok:
                return
            cols, ok = QInputDialog.getInt(self, "Insert table", "Columns:", 4, 1, 12)
            if not ok:
                return
        cur = self.edit.textCursor()
        fmt = QTextTableFormat()
        fmt.setHeaderRowCount(1); fmt.setCellPadding(4); fmt.setCellSpacing(0); fmt.setBorder(1)
        fmt.setBorderStyle(QTextTableFormat.BorderStyle.BorderStyle_Solid)
        cur.beginEditBlock()
        if not cur.atBlockStart():
            cur.insertBlock()
        table = cur.insertTable(rows, cols, fmt)
        heads = headers or ["Task", "Responsible Person", "Deadline", "Status"][:cols]
        for c, text in enumerate(heads[:cols]):
            cell = table.cellAt(0, c).firstCursorPosition()
            f = QTextCharFormat(); f.setFontWeight(QFont.Weight.Bold)
            cell.insertText(text, f)
        cur.endEditBlock()
        self.edit.setFocus()

    def add_row(self):
        cur = self.edit.textCursor()
        table = cur.currentTable()
        if table is not None:
            table.insertRows(table.cellAt(cur).row() + 1, 1)

    def remove_row(self):
        cur = self.edit.textCursor()
        table = cur.currentTable()
        if table is None:
            return
        if table.rows() <= 1:
            cur.beginEditBlock()
            cur.setPosition(table.firstCursorPosition().position() - 1)
            cur.setPosition(table.lastCursorPosition().position() + 1, QTextCursor.KeepAnchor)
            cur.removeSelectedText()
            cur.endEditBlock()
            return
        table.removeRows(table.cellAt(cur).row(), 1)

    # -- content ----------------------------------------------------------------------
    def set_markdown(self, md: str) -> None:
        self._loading = True
        try:
            self.edit.setHtml(markdown_to_html(md or ""))
            self._apply_directions()
            self.edit.document().clearUndoRedoStacks()  # loading is not something to undo
            self.edit.document().setModified(False)
        finally:
            self._loading = False

    def to_markdown(self) -> str:
        return document_to_markdown(self.edit.document())

    def _apply_directions(self):
        """Arabic / Urdu paragraphs read right-to-left (and sit on the right)."""
        doc = self.edit.document()
        blk = doc.begin()
        while blk.isValid():
            want = Qt.RightToLeft if rtl.has_arabic(blk.text()) else Qt.LeftToRight
            if blk.blockFormat().layoutDirection() != want:
                cur = QTextCursor(blk)
                bf = cur.blockFormat(); bf.setLayoutDirection(want); cur.setBlockFormat(bf)
            blk = blk.next()

    def _on_text_changed(self):
        if self._loading:
            return
        blk = self.edit.textCursor().block()          # keep the typed paragraph's direction right
        want = Qt.RightToLeft if rtl.has_arabic(blk.text()) else Qt.LeftToRight
        if blk.blockFormat().layoutDirection() != want:
            self._loading = True
            try:
                cur = QTextCursor(blk)
                cur.joinPreviousEditBlock()           # part of the same undo step as the typing
                bf = cur.blockFormat(); bf.setLayoutDirection(want); cur.setBlockFormat(bf)
                cur.endEditBlock()
            finally:
                self._loading = False
        self.changed.emit()

    # QPlainTextEdit-like conveniences used by the page
    def setPlaceholderText(self, text: str) -> None:
        self.edit.setPlaceholderText(text)

    def setMinimumHeight(self, h: int) -> None:         # the editor, not the toolbar
        self.edit.setMinimumHeight(h)

    def document(self) -> QTextDocument:
        return self.edit.document()
