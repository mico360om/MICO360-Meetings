"""Meeting History page. (Split out of pages.py — see pages.py for the New Meeting wizard.)"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core import documents
from ..core.audio import MEDIA_EXTS
from ..core.history import Meeting
from ..core.prompts import OUTPUT_STYLES, SavedPrompt
from ..core.transcription import WHISPER_MODELS
from . import metrics as M
from . import theme
from .components import (
    Card, CollapsibleSection, DropArea, EmptyState, StepIndicator, hint,
    scroll_area as _scroll, section_title, subtitle, tip,
)
from .context import AppContext
from .dialogs import ProfileDialog, PromptDialog
from .recording_panel import RecordingPanel
from .workers import GenerateWorker, TranscribeWorker

log = logging.getLogger("mico360.pages")

UPLOAD_EXTS = set(MEDIA_EXTS) | documents.DOC_EXTS | documents.IMAGE_EXTS


# ===========================================================================
# History
# ===========================================================================
class HistoryPage(QWidget):
    # Status filter options + the per-meeting status a meeting resolves to.
    _FILTERS = ["All statuses", "Complete", "Draft", "Empty"]
    _COL_TITLE, _COL_STATUS, _COL_STYLE, _COL_MODEL, _COL_UPDATED = range(5)

    def __init__(self, ctx: AppContext, toast, on_open):
        super().__init__()
        self.ctx = ctx; self.toast = toast; self.on_open = on_open
        self._rows: list[Meeting] = []
        self._by_id: dict[int, Meeting] = {}

        v = QVBoxLayout(self); v.setContentsMargins(*M.PAGE_MARGINS); v.setSpacing(M.PAGE_GAP)
        title = QLabel("Meeting History"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Search and reopen past meetings — everything stays on this computer."))

        # -- filter bar: search · status filter · refresh --------------------
        row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search title, transcript or minutes…")
        self.search.setClearButtonEnabled(True)
        tip(self.search, "Filter as you type — matches meeting titles, transcripts and minutes text")
        self.status_filter = QComboBox(); self.status_filter.addItems(self._FILTERS)
        self.status_filter.currentIndexChanged.connect(self.reload)
        tip(self.status_filter, "Show only meetings with a given status: Complete (has minutes), "
                                "Draft (transcript only) or Empty")
        refresh = QPushButton("↻  Refresh"); refresh.setObjectName("Ghost"); refresh.clicked.connect(self.reload)
        tip(refresh, "Reload the list, including meetings autosaved in the background")
        row.addWidget(self.search, 1); row.addWidget(self.status_filter); row.addWidget(refresh)
        v.addLayout(row)

        self.count_lbl = QLabel(""); self.count_lbl.setObjectName("Hint")
        v.addWidget(self.count_lbl)

        # search is debounced: show the loading state immediately, then reload
        # once typing settles (220ms) so rapid keystrokes don't thrash the DB.
        self._search_timer = QTimer(self); self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220); self._search_timer.timeout.connect(self.reload)
        self.search.textChanged.connect(self._on_search_changed)

        # -- table | preview split ------------------------------------------
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Title", "Status", "Style", "Model", "Updated"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(self._COL_TITLE, QHeaderView.Stretch)  # Title absorbs slack
        hh.setMinimumSectionSize(56)
        for c, w in ((self._COL_STATUS, 104), (self._COL_STYLE, 96),
                     (self._COL_MODEL, 108), (self._COL_UPDATED, 124)):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)                       # click headers to sort
        self.table.sortByColumn(self._COL_UPDATED, Qt.DescendingOrder)   # newest first by default
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.ElideRight)               # long titles elide, never scroll
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._open_selected)    # double-click opens
        self.table.itemSelectionChanged.connect(self._update_preview)  # single-click previews
        tip(self.table, "Your saved meetings — click a row to preview, double-click to reopen. "
                        "Click a column header to sort.")

        # empty & loading share the table's space via a stack
        self.empty = EmptyState("🕑", "No meetings yet",
                                "Meetings you save appear here. Start one in New Meeting to "
                                "record, transcribe and generate minutes.")
        self.loading = EmptyState("⏳", "Loading meetings…", "")
        self.left_stack = QStackedWidget()
        for w in (self.table, self.empty, self.loading):
            self.left_stack.addWidget(w)

        self.preview = self._build_preview()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.left_stack); split.addWidget(self.preview)
        split.setStretchFactor(0, 3); split.setStretchFactor(1, 2)
        split.setSizes([620, 420])
        v.addWidget(split, 1)

        # -- actions ---------------------------------------------------------
        actions = QHBoxLayout()
        self.open_btn = QPushButton("Open"); self.open_btn.setObjectName("Primary")
        self.open_btn.clicked.connect(self._open_selected)
        tip(self.open_btn, "Load the selected meeting's transcript and minutes for editing or re-export")
        self.del_btn = QPushButton("Delete"); self.del_btn.setObjectName("Danger")
        self.del_btn.clicked.connect(self._delete_selected)
        tip(self.del_btn, "Permanently delete the selected meeting from History — this cannot be undone")
        actions.addStretch(); actions.addWidget(self.del_btn); actions.addWidget(self.open_btn)
        v.addLayout(actions)

    # -- preview pane -------------------------------------------------------
    def _build_preview(self) -> QWidget:
        card = QWidget(); card.setObjectName("Card")
        lay = QVBoxLayout(card); lay.setContentsMargins(*M.CARD_MARGINS); lay.setSpacing(M.SM)
        self.pv_title = QLabel("Select a meeting"); self.pv_title.setObjectName("SectionTitle")
        self.pv_title.setWordWrap(True)
        self.pv_status = QLabel(""); self.pv_status.setObjectName("Hint")
        self.pv_meta = QLabel(""); self.pv_meta.setObjectName("Hint"); self.pv_meta.setWordWrap(True)
        self.pv_body = QTextBrowser(); self.pv_body.setOpenExternalLinks(False)
        self.pv_body.setPlaceholderText("Click a meeting on the left to preview its minutes here.")
        lay.addWidget(self.pv_title)
        lay.addWidget(self.pv_status)
        lay.addWidget(self.pv_meta)
        lay.addWidget(self.pv_body, 1)
        return card

    # -- status helpers -----------------------------------------------------
    @staticmethod
    def _status_of(m: Meeting) -> str:
        if (m.minutes or "").strip():
            return "Complete"
        if (m.transcript or "").strip():
            return "Draft"
        return "Empty"

    def _status_color(self, status: str) -> QColor:
        pal = theme.palette(self.ctx.settings.get("theme", "light"))
        return QColor({"Complete": pal["success"], "Draft": pal["warning"]}.get(status, pal["muted"]))

    # -- loading / reload ---------------------------------------------------
    def _on_search_changed(self, _text=None):
        self.left_stack.setCurrentWidget(self.loading)   # visible during the debounce
        self._search_timer.start()

    def reload(self):
        """Fetch + populate synchronously (callers rely on immediate results)."""
        self._populate()

    def _populate(self):
        import time
        query = self.search.text()
        want = self.status_filter.currentText()
        rows = self.ctx.history.list(query)
        if want != self._FILTERS[0]:
            rows = [m for m in rows if self._status_of(m) == want]
        self._rows = rows
        self._by_id = {m.id: m for m in rows}

        self.table.setSortingEnabled(False)                      # populate, then re-enable
        self.table.setRowCount(len(rows))
        for i, m in enumerate(rows):
            status = self._status_of(m)
            updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.updated_at))
            title_it = QTableWidgetItem(m.title or "Untitled meeting")
            title_it.setData(Qt.UserRole, m.id)                  # survive re-sorting
            status_it = QTableWidgetItem(f"● {status}")
            status_it.setForeground(self._status_color(status))
            cells = [title_it, status_it, QTableWidgetItem(m.style or "—"),
                     QTableWidgetItem(m.model or "—"), QTableWidgetItem(updated)]
            for j, it in enumerate(cells):
                self.table.setItem(i, j, it)
        self.table.setSortingEnabled(True)

        total = len(rows)
        filtered = query.strip() or want != self._FILTERS[0]
        self.count_lbl.setText(
            f"{total} meeting{'s' if total != 1 else ''}" + (" (filtered)" if filtered else ""))

        if rows:
            self.left_stack.setCurrentWidget(self.table)
            if self.table.currentRow() < 0:
                self.table.selectRow(0)
            else:
                self._update_preview()
        else:
            q = query.strip()
            if q or want != self._FILTERS[0]:
                self.empty.set("No matching meetings",
                               "Try a different search or status filter, or clear them "
                               "to see everything.", "🔍")
            else:
                self.empty.set("No meetings yet",
                               "Meetings you save appear here. Start one in New Meeting to "
                               "record, transcribe and generate minutes.", "🕑")
            self.left_stack.setCurrentWidget(self.empty)
            self._clear_preview()
        has_sel = bool(self._rows)
        self.open_btn.setEnabled(has_sel); self.del_btn.setEnabled(has_sel)

    # -- preview / selection ------------------------------------------------
    def _update_preview(self):
        m = self._selected_meeting()
        if not m:
            self._clear_preview(); return
        import time
        status = self._status_of(m)
        self.pv_title.setText(m.title or "Untitled meeting")
        self.pv_status.setText(f"● {status}")
        self.pv_status.setStyleSheet(f"color:{self._status_color(status).name()}; font-weight:700;")
        created = time.strftime("%d %b %Y, %H:%M", time.localtime(m.created_at))
        bits = [created]
        if m.style: bits.append(m.style)
        if m.model: bits.append(m.model)
        self.pv_meta.setText("  ·  ".join(bits))
        if (m.minutes or "").strip():
            self.pv_body.setMarkdown(m.minutes[:8000])
        elif (m.transcript or "").strip():
            excerpt = m.transcript.strip()[:2000]
            self.pv_body.setPlainText("Draft — no minutes generated yet.\n\nTranscript preview:\n\n" + excerpt)
        else:
            self.pv_body.setPlainText("This meeting has no transcript or minutes yet.")

    def _clear_preview(self):
        self.pv_title.setText("Select a meeting")
        self.pv_status.setText(""); self.pv_status.setStyleSheet("")
        self.pv_meta.setText("")
        self.pv_body.clear()

    def _selected_meeting(self) -> Meeting | None:
        r = self.table.currentRow()
        if r < 0:
            return None
        it = self.table.item(r, self._COL_TITLE)
        return self._by_id.get(it.data(Qt.UserRole)) if it else None

    def _open_selected(self):
        m = self._selected_meeting()
        if m:
            self.on_open(m)

    def _delete_selected(self):
        m = self._selected_meeting()
        if not m:
            return
        if QMessageBox.question(self, "Delete", f"Delete '{m.title}'?") == QMessageBox.Yes:
            self.ctx.history.delete(m.id)
            self.ctx.action_items.drop_meeting(m.id)   # purge orphaned status overrides
            self.reload()
            self.toast.show_message("Deleted.", "success")
