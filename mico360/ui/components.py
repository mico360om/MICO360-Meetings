"""Reusable UI building blocks: drop area, cards, section headers, toasts."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from ..core.audio import MEDIA_EXTS
from . import metrics as M


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")


class CollapsibleSection(QFrame):
    """A card with a clickable header that folds its content away.

    Add widgets/layouts via `.content` (a QVBoxLayout). The header shows the
    title, a fold arrow, and an optional right-aligned status hint.
    """
    toggled = Signal(bool)

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._btn = QToolButton()
        self._btn.setObjectName("SectionHeader")
        self._btn.setText("  " + title)
        self._btn.setCheckable(True)
        self._btn.setChecked(expanded)
        self._btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._btn.clicked.connect(lambda: self.set_expanded(self._btn.isChecked()))

        self._status = QLabel("")
        self._status.setObjectName("Hint")

        hrow = QHBoxLayout()
        hrow.setContentsMargins(8, 4, 16, 4)
        hrow.addWidget(self._btn, 1)
        hrow.addWidget(self._status, 0, Qt.AlignRight)
        header = QWidget()
        header.setObjectName("SectionHeaderRow")
        header.setLayout(hrow)
        outer.addWidget(header)

        self._body = QWidget()
        self.content = QVBoxLayout(self._body)
        self.content.setContentsMargins(M.LG, 0, M.LG, M.MD)
        self.content.setSpacing(M.SM)
        outer.addWidget(self._body)
        self._body.setVisible(expanded)

    def set_expanded(self, on: bool):
        self._btn.setChecked(on)
        self._btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self._body.setVisible(on)
        self.toggled.emit(on)

    def is_expanded(self) -> bool:
        return self._body.isVisible()

    def set_status(self, text: str):
        self._status.setText(text)

    def add(self, widget: QWidget):
        self.content.addWidget(widget)

    def add_layout(self, layout):
        self.content.addLayout(layout)


class _StepItem(QWidget):
    """A clickable badge+label pair inside the wizard StepIndicator."""
    clicked = Signal(int)

    def __init__(self, index: int):
        super().__init__()
        self._i = index
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):     # the whole item (badge + label) navigates
        self.clicked.emit(self._i)


class StepIndicator(QWidget):
    """A horizontal progress stepper: numbered circular badges joined by a
    progress line. Each step is done (✓), active, or upcoming, and clickable.

    States are driven by set_state(current, reached); clicks emit stepClicked.
    """
    stepClicked = Signal(int)

    def __init__(self, titles: list[str], parent=None):
        super().__init__(parent)
        self._badges: list[QLabel] = []
        self._labels: list[QLabel] = []
        self._conns: list[QFrame] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, M.XS)
        row.setSpacing(0)
        for i, name in enumerate(titles):
            item = _StepItem(i)
            item.clicked.connect(self.stepClicked)
            il = QHBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(M.SM)
            badge = QLabel(str(i + 1))
            badge.setObjectName("StepNum")
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedSize(26, 26)
            lbl = QLabel(name)
            lbl.setObjectName("StepLabel")
            il.addWidget(badge)
            il.addWidget(lbl)
            self._badges.append(badge)
            self._labels.append(lbl)
            row.addWidget(item)
            if i < len(titles) - 1:
                conn = QFrame()
                conn.setObjectName("StepConn")
                conn.setFixedHeight(2)
                conn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                self._conns.append(conn)
                row.addWidget(conn, 1)

    def set_state(self, current: int, reached: int):
        for i, (badge, label) in enumerate(zip(self._badges, self._labels)):
            state = "done" if i < current else ("active" if i == current else "todo")
            badge.setText("✓" if i < current else str(i + 1))
            for w in (badge, label):
                w.setProperty("state", state)
                w.style().unpolish(w)
                w.style().polish(w)
        for i, conn in enumerate(self._conns):
            conn.setProperty("on", "true" if i < current else "false")
            conn.style().unpolish(conn)
            conn.style().polish(conn)


class EmptyState(QWidget):
    """A centred placeholder shown when a list/table has nothing to display —
    an icon, a title and an optional one-line description, updatable via set()."""

    def __init__(self, icon: str = "📭", title: str = "", description: str = ""):
        super().__init__()
        v = QVBoxLayout(self); v.setSpacing(8); v.setAlignment(Qt.AlignCenter)
        v.addStretch()
        self._icon = QLabel(icon); self._icon.setObjectName("EmptyIcon")
        self._icon.setAlignment(Qt.AlignCenter)
        self._title = QLabel(title); self._title.setObjectName("EmptyTitle")
        self._title.setAlignment(Qt.AlignCenter); self._title.setWordWrap(True)
        self._desc = QLabel(description); self._desc.setObjectName("EmptyDesc")
        self._desc.setAlignment(Qt.AlignCenter); self._desc.setWordWrap(True)
        self._desc.setMaximumWidth(460); self._desc.setVisible(bool(description))
        v.addWidget(self._icon); v.addWidget(self._title)
        drow = QHBoxLayout(); drow.addStretch(); drow.addWidget(self._desc); drow.addStretch()
        v.addLayout(drow)
        v.addStretch()

    def set(self, title: str, description: str = "", icon: str | None = None):
        self._title.setText(title)
        self._desc.setText(description); self._desc.setVisible(bool(description))
        if icon is not None:
            self._icon.setText(icon)


def section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("SectionTitle")
    return lbl


def hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Hint")
    lbl.setWordWrap(True)
    return lbl


def subtitle(text: str) -> QLabel:
    """A page subtitle that wraps instead of forcing horizontal scroll."""
    lbl = QLabel(text)
    lbl.setObjectName("PageSub")
    lbl.setWordWrap(True)
    return lbl


def tip(widget, text: str):
    """Attach a tooltip AND matching accessible description (screen readers).

    One consistent voice app-wide: sentence case, concise, ends without a period
    unless multi-sentence. Qt shows tooltips on hover and keyboard focus, and
    positions them to avoid covering the control.
    """
    widget.setToolTip(text)
    try:
        widget.setAccessibleDescription(text)
    except Exception:
        pass
    return widget


class DropArea(QFrame):
    """Drag-and-drop + click-to-browse area for media/document files."""
    fileChosen = Signal(str)

    def __init__(self, accept_exts: set[str] | None = None,
                 caption: str = "Drag & drop a file here",
                 sub: str = "Audio, video or document — or click to browse"):
        super().__init__()
        self.setObjectName("Drop")
        self.setAcceptDrops(True)
        self.setProperty("hover", "false")
        self._accept = accept_exts or set(MEDIA_EXTS)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(M.XL, M.XXL, M.XL, M.XXL)
        lay.setSpacing(M.SM)
        lay.setAlignment(Qt.AlignCenter)

        icon = QLabel("⬆")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 26pt;")
        title = QLabel(caption); title.setObjectName("DropTitle")
        title.setAlignment(Qt.AlignCenter)
        self._sub = QLabel(sub); self._sub.setObjectName("Hint")
        self._sub.setAlignment(Qt.AlignCenter); self._sub.setWordWrap(True)

        browse = QPushButton("Browse files…")
        browse.setObjectName("Ghost")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._browse)

        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addWidget(self._sub)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(browse); row.addStretch()
        lay.addLayout(row)
        self.setMinimumHeight(140)

    def set_sub(self, text: str):
        self._sub.setText(text)

    def _filter(self) -> str:
        exts = " ".join(f"*{e}" for e in sorted(self._accept))
        return f"Supported files ({exts});;All files (*.*)"

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file", "", self._filter())
        if path:
            self.fileChosen.emit(path)

    def _ok(self, path: str) -> bool:
        return Path(path).suffix.lower() in self._accept or not self._accept

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setProperty("hover", "true"); self._restyle()

    def dragLeaveEvent(self, e):
        self.setProperty("hover", "false"); self._restyle()

    def dropEvent(self, e):
        self.setProperty("hover", "false"); self._restyle()
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p:
                self.fileChosen.emit(p)
                break

    def _restyle(self):
        self.style().unpolish(self); self.style().polish(self)


class Toast(QLabel):
    """Lightweight transient status message anchored to a parent widget."""
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setVisible(False)
        self.setAlignment(Qt.AlignCenter)
        # Charcoal chip (brand-neutral, reads on both themes) with a colour-coded
        # border per message kind — no off-brand navy/blue.
        self.setStyleSheet(
            "background: #26222A; color: #ECEAEF; border: 1px solid #39353F;"
            "border-radius: 10px; padding: 10px 16px; font-size: 10pt;"
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.setVisible(False))

    def show_message(self, text: str, kind: str = "info", msec: int = 3500):
        color = {"info": "#6E6670", "success": "#22C55E",
                 "error": "#EF4444", "warn": "#F59E0B"}.get(kind, "#6E6670")
        self.setStyleSheet(
            f"background: #26222A; color: #ECEAEF; border: 1px solid {color};"
            "border-radius: 10px; padding: 10px 16px; font-size: 10pt;"
        )
        self.setText(text)
        self.adjustSize()
        self._reposition()
        self.setVisible(True)
        self.raise_()
        self._timer.start(msec)

    def _reposition(self):
        if self.parent():
            pw = self.parent().width()
            self.move(int((pw - self.width()) / 2), 18)
