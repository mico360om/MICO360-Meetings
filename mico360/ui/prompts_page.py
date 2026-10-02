"""Prompt Library page. (Split out of pages.py — see pages.py for the New Meeting wizard.)"""
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
# Prompt Library
# ===========================================================================
class PromptsPage(QWidget):
    def __init__(self, ctx: AppContext, toast, on_change=None):
        super().__init__()
        self.ctx = ctx; self.toast = toast; self.on_change = on_change
        self._prompts = []; self._row_map = []
        v = QVBoxLayout(self); v.setContentsMargins(*M.PAGE_MARGINS); v.setSpacing(M.PAGE_GAP)
        title = QLabel("Prompt Library"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Reusable prompts for minutes generation — search, favourite, "
                             "duplicate and edit. Built-ins are provided; your own are Custom."))

        split = QSplitter(Qt.Horizontal)

        # -- left: search + category filter + list + Add --------------------
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search prompts…")
        self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self._refilter)
        tip(self.search, "Filter prompts as you type — matches name, category and text")
        self.cat_filter = QComboBox(); self.cat_filter.currentIndexChanged.connect(self._refilter)
        tip(self.cat_filter, "Show one category, or just your favourites")
        ll.addWidget(self.search); ll.addWidget(self.cat_filter)
        self.list = QListWidget(); self.list.currentRowChanged.connect(self._show)
        self.list.itemDoubleClicked.connect(lambda *_: self._edit())
        tip(self.list, "Prompts grouped by category; ★ marks a favourite. Select one to preview.")
        ll.addWidget(self.list, 1)
        add = QPushButton("➕  Add prompt"); add.setObjectName("Primary"); add.clicked.connect(self._add)
        tip(add, "Create a custom prompt — include [TRANSCRIPT_HERE] where the meeting text goes")
        ll.addWidget(add)
        io = QHBoxLayout()
        imp = QPushButton("Import…"); imp.setObjectName("Ghost"); imp.clicked.connect(self._import)
        tip(imp, "Import prompts from a prompt export (.json), or one prompt from a .txt / .md "
                 "file. Nothing you have is overwritten")
        exp = QPushButton("Export…"); exp.setObjectName("Ghost"); exp.clicked.connect(self._export)
        tip(exp, "Save your prompts to a file — for backup, or to use them on another PC")
        io.addWidget(imp); io.addWidget(exp)
        ll.addLayout(io)

        # -- right: preview header + text + actions -------------------------
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout(); head.setSpacing(M.SM)
        self.pv_name = QLabel("Select a prompt"); self.pv_name.setObjectName("SectionTitle")
        self.pv_name.setWordWrap(True)
        self.pv_tag = QLabel(""); self.pv_tag.setObjectName("Tag"); self.pv_tag.setVisible(False)
        self.fav_btn = QPushButton("☆"); self.fav_btn.setObjectName("FavBtn")
        self.fav_btn.setCursor(Qt.PointingHandCursor); self.fav_btn.clicked.connect(self._toggle_fav)
        tip(self.fav_btn, "Mark this prompt as a favourite (shows under ★ Favourites)")
        head.addWidget(self.pv_name, 1); head.addWidget(self.pv_tag); head.addWidget(self.fav_btn)
        rl.addLayout(head)
        self.pv_cat = QLabel(""); self.pv_cat.setObjectName("Hint")
        rl.addWidget(self.pv_cat)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        rl.addWidget(self.preview, 1)
        act = QHBoxLayout()
        self.edit_btn = QPushButton("Edit"); self.edit_btn.setObjectName("Ghost"); self.edit_btn.clicked.connect(self._edit)
        tip(self.edit_btn, "Edit this prompt's name, category and text")
        self.dup_btn = QPushButton("Duplicate"); self.dup_btn.setObjectName("Ghost"); self.dup_btn.clicked.connect(self._duplicate)
        tip(self.dup_btn, "Make an editable Custom copy — the safe way to tweak a built-in prompt")
        self.del_btn = QPushButton("Delete"); self.del_btn.setObjectName("Danger"); self.del_btn.clicked.connect(self._delete)
        tip(self.del_btn, "Permanently delete this prompt from the library")
        self.restore_btn = QPushButton("Restore default"); self.restore_btn.setObjectName("Ghost")
        self.restore_btn.clicked.connect(self._restore)
        tip(self.restore_btn, "Put this built-in prompt's original text back (your edits are replaced)")
        self.restore_btn.setVisible(False)
        act.addWidget(self.restore_btn)
        act.addStretch(); act.addWidget(self.dup_btn); act.addWidget(self.edit_btn); act.addWidget(self.del_btn)
        rl.addLayout(act)

        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 2); split.setStretchFactor(1, 3)
        split.setSizes([340, 560])
        v.addWidget(split, 1)
        self.reload()

    # -- data / filter ------------------------------------------------------
    def reload(self):
        self._all = self.ctx.prompts.list()
        cats = sorted({p.category for p in self._all})
        prev = self.cat_filter.currentText()
        self.cat_filter.blockSignals(True)
        self.cat_filter.clear()
        self.cat_filter.addItems(["All categories", "★ Favourites"] + cats)
        i = self.cat_filter.findText(prev)
        self.cat_filter.setCurrentIndex(i if i >= 0 else 0)
        self.cat_filter.blockSignals(False)
        self._refilter()
        if self.on_change:
            self.on_change()

    def _refilter(self):
        keep_id = self._current().id if self._current() else None
        q = self.search.text().strip().lower()
        cat = self.cat_filter.currentText()
        def match(p):
            if cat == "★ Favourites" and not p.favorite:
                return False
            if cat not in ("", "All categories", "★ Favourites") and p.category != cat:
                return False
            if q and q not in p.name.lower() and q not in p.text.lower() and q not in p.category.lower():
                return False
            return True
        self._prompts = [p for p in self._all if match(p)]

        self.list.blockSignals(True)
        self.list.clear()
        self._row_map = []                 # list-row -> index into self._prompts (or -1 header)
        last_cat = None
        select_row = None
        for i, p in enumerate(self._prompts):
            if p.category != last_cat:
                last_cat = p.category
                hdr = QListWidgetItem(f"— {p.category} —"); hdr.setFlags(Qt.NoItemFlags)
                self.list.addItem(hdr); self._row_map.append(-1)
            star = "★ " if p.favorite else ""
            tag = "Built-in" if p.builtin else "Custom"
            self.list.addItem(f"   {star}{p.name}   ·  {tag}")
            self._row_map.append(i)
            if p.id == keep_id:
                select_row = len(self._row_map) - 1
        self.list.blockSignals(False)

        if select_row is None:
            select_row = next((r for r, idx in enumerate(self._row_map) if idx >= 0), -1)
        if select_row >= 0:
            self.list.setCurrentRow(select_row)
        else:
            self._show(-1)     # nothing to show (empty filter result)

    def _current(self) -> SavedPrompt | None:
        r = self.list.currentRow()
        if 0 <= r < len(self._row_map) and self._row_map[r] >= 0:
            return self._prompts[self._row_map[r]]
        return None

    def _show(self, _row):
        p = self._current()
        has = p is not None
        self.preview.setPlainText(p.text if has else "")
        self.pv_name.setText(p.name if has else "Select a prompt")
        self.pv_cat.setText(f"Category: {p.category}" if has else "")
        self.pv_tag.setVisible(has)
        edited = bool(has and self.ctx.prompts.is_modified(p))
        self.restore_btn.setVisible(edited)
        if has:
            self.pv_tag.setText(("Built-in · edited" if edited else "Built-in") if p.builtin
                                else "Custom")
            self.pv_tag.setProperty("kind", "builtin" if p.builtin else "custom")
            self.pv_tag.style().unpolish(self.pv_tag); self.pv_tag.style().polish(self.pv_tag)
            self.fav_btn.setText("★" if p.favorite else "☆")
            self.fav_btn.setProperty("on", "true" if p.favorite else "false")
            self.fav_btn.style().unpolish(self.fav_btn); self.fav_btn.style().polish(self.fav_btn)
        for b in (self.fav_btn, self.edit_btn, self.dup_btn, self.del_btn):
            b.setEnabled(has)

    # -- actions ------------------------------------------------------------
    def _toggle_fav(self):
        p = self._current()
        if p:
            self.ctx.prompts.set_favorite(p.id, not p.favorite)
            self.reload()

    def _add(self):
        dlg = PromptDialog(parent=self)
        if dlg.exec():
            name, text, category = dlg.values()
            new = self.ctx.prompts.add(name, text, category=category)
            self.reload()
            for r, idx in enumerate(self._row_map):     # select the new prompt
                if idx >= 0 and self._prompts[idx].id == new.id:
                    self.list.setCurrentRow(r); break
            self.toast.show_message("Prompt added.", "success")

    def _edit(self):
        p = self._current()
        if not p:
            return
        dlg = PromptDialog(p.name, p.text, p.category, self)
        if dlg.exec():
            name, text, category = dlg.values()
            self.ctx.prompts.update(p.id, name, text, category=category)
            self.reload(); self.toast.show_message("Prompt updated.", "success")

    def _duplicate(self):
        p = self._current()
        if not p:
            return
        dup = self.ctx.prompts.duplicate(p.id)
        if dup:
            self.reload()
            # select the new copy
            for r, idx in enumerate(self._row_map):
                if idx >= 0 and self._prompts[idx].id == dup.id:
                    self.list.setCurrentRow(r); break
            self.toast.show_message("Prompt duplicated — edit your copy.", "success")

    def _select(self, prompt_id: str) -> None:
        for r, idx in enumerate(self._row_map):
            if idx >= 0 and self._prompts[idx].id == prompt_id:
                self.list.setCurrentRow(r)
                return

    def _restore(self):
        p = self._current()
        if not p or not self.ctx.prompts.is_modified(p):
            return
        if QMessageBox.question(
                self, "Restore default",
                f"Put the original text of “{p.name}” back?\n\nYour edits to this built-in "
                "prompt will be replaced. (Use Duplicate first to keep them as a custom prompt.)",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self.ctx.prompts.restore_builtin(p.id)
        self.reload(); self._select(p.id)
        self.toast.show_message("Original prompt restored.", "success")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import prompts", "", "Prompts (*.json *.txt *.md)")
        if not path:
            return
        try:
            res = self.ctx.prompts.import_file(path)
        except Exception as exc:
            log.warning("prompt import failed", exc_info=True)
            QMessageBox.critical(self, "Import failed",
                                 f"“{Path(path).name}” could not be imported.\n\n{exc}\n\n"
                                 "Your prompt library is unchanged.")
            return
        self.search.clear()
        self.cat_filter.setCurrentIndex(0)
        self.reload()
        if res.imported:
            self._select(res.imported[0].id)
        if res.imported and not (res.skipped or res.renamed or res.invalid):
            self.toast.show_message(res.summary(), "success", 5000)
        else:
            QMessageBox.information(self, "Import finished", res.summary())

    def _export(self):
        from PySide6.QtCore import QStandardPaths
        custom = [p for p in self._all if not p.builtin or self.ctx.prompts.is_modified(p)]
        box = QMessageBox(self); box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Export prompts")
        box.setText("Which prompts do you want to export?")
        mine = box.addButton(f"My prompts ({len(custom)})", QMessageBox.AcceptRole)
        everything = box.addButton(f"All prompts ({len(self._all)})", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        mine.setEnabled(bool(custom))
        box.exec()
        if box.clickedButton() is mine:
            chosen = custom
        elif box.clickedButton() is everything:
            chosen = list(self._all)
        else:
            return
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        start = str(Path(docs) / "mico360-prompts.json") if docs else "mico360-prompts.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export prompts", start, "Prompts (*.json)")
        if not path:
            return
        if Path(path).suffix.lower() != ".json":
            path += ".json"
        try:
            self.ctx.prompts.export_file(chosen, path)
            self.toast.show_message(f"Exported {len(chosen)} prompt(s) to {Path(path).name}.",
                                    "success")
        except Exception as exc:
            log.warning("prompt export failed", exc_info=True)
            QMessageBox.critical(self, "Export failed", str(exc))

    def _delete(self):
        p = self._current()
        if not p:
            return
        box = QMessageBox(self); box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Delete prompt"); box.setText(f"Delete “{p.name}”?")
        box.setInformativeText("This removes it from the library. Built-ins reappear on the next "
                               "launch; custom prompts are gone for good."
                               if p.builtin else "This permanently removes your custom prompt.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() == QMessageBox.Yes:
            self.ctx.prompts.delete(p.id)
            self.reload(); self.toast.show_message("Prompt deleted.", "success")
