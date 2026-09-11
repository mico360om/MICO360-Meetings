"""Company Profiles page. (Split out of pages.py — see pages.py for the New Meeting wizard.)"""
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
# Company Profiles
# ===========================================================================
class ProfilesPage(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx = ctx; self.toast = toast
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        v = QVBoxLayout(content); v.setContentsMargins(*M.PAGE_MARGINS); v.setSpacing(M.PAGE_GAP)
        title = QLabel("Company Profiles"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("Branding used on exported minutes: logo, footer, page numbers and layout."))

        # Global actions only. Edit / Delete / Set active live on each card.
        bar = QHBoxLayout()
        new = QPushButton("➕  New profile"); new.setObjectName("Primary"); new.clicked.connect(self._new)
        tip(new, "Create a company profile: name, contact details, logo, footer and page numbering")
        imp = QPushButton("Import…"); imp.setObjectName("Ghost"); imp.clicked.connect(self._import)
        tip(imp, "Import profiles from a JSON, CSV or Excel file")
        exp = QPushButton("Export…"); exp.setObjectName("Ghost"); exp.clicked.connect(self._export)
        tip(exp, "Export all profiles to JSON, CSV or Excel — useful for backup or another PC")
        bar.addWidget(new); bar.addStretch(); bar.addWidget(imp); bar.addWidget(exp)
        v.addLayout(bar)

        # list of profile cards (the whole page scrolls when there are many)
        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0); self.cards_layout.setSpacing(M.MD)
        self.empty = EmptyState("🏢", "No company profiles yet",
                                "Create a profile to brand your exported minutes with a logo, "
                                "footer and page numbers. Click “New profile” to start.")
        v.addWidget(self.cards_host)
        v.addWidget(self.empty)
        v.addStretch()

        self.active_lbl = QLabel(""); self.active_lbl.setObjectName("Hint")
        v.addWidget(self.active_lbl)
        outer.addWidget(_scroll(content))
        self.reload()

    def reload(self):
        while self.cards_layout.count():
            it = self.cards_layout.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._profiles = self.ctx.profiles.list()
        active = self.ctx.settings.get("active_profile", "")
        for p in self._profiles:
            self.cards_layout.addWidget(self._build_card(p, active))
        self.cards_host.setVisible(bool(self._profiles))
        self.empty.setVisible(not self._profiles)
        act = self.ctx.active_profile()
        self.active_lbl.setText(f"Active profile: {act.name}" if act else "No active profile (plain export).")

    def _build_card(self, p, active_id: str) -> QWidget:
        import time
        pal = theme.palette(self.ctx.settings.get("theme", "light"))
        is_active = (p.id == active_id)
        card = QWidget(); card.setObjectName("CardActive" if is_active else "Card")
        row = QHBoxLayout(card); row.setContentsMargins(M.LG, M.MD, M.LG, M.MD); row.setSpacing(M.LG)

        # logo thumbnail (or a coloured initial)
        thumb = QLabel(); thumb.setObjectName("LogoThumb")
        thumb.setFixedSize(52, 52); thumb.setAlignment(Qt.AlignCenter)
        pm = QPixmap(p.logo_path) if p.logo_path and Path(p.logo_path).exists() else QPixmap()
        if not pm.isNull():
            thumb.setPixmap(pm.scaled(46, 46, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            thumb.setText((p.name[:1] or "?").upper())
            thumb.setStyleSheet(f"background:{pal['accent']}; color:{pal['accent_text']}; "
                                f"border:none; border-radius:{M.MD}px; font-weight:800; font-size:16pt;")
        row.addWidget(thumb)

        # name + active badge, footer/layout summary, last-updated
        mid = QVBoxLayout(); mid.setSpacing(2)
        namerow = QHBoxLayout(); namerow.setSpacing(M.SM)
        name = QLabel(p.name or "Untitled"); name.setObjectName("ProfileName")
        namerow.addWidget(name)
        if is_active:
            badge = QLabel("● Active"); badge.setObjectName("ActiveBadge")
            namerow.addWidget(badge)
        namerow.addStretch()
        mid.addLayout(namerow)

        foot = (p.footer_text or "").strip()
        foot_txt = f"Footer: “{foot[:44]}”" if foot else "No footer"
        pages = "page numbers on" if p.show_page_numbers else "page numbers off"
        logo_txt = f"logo {p.logo_position}" if (p.logo_path and not pm.isNull()) else "no logo"
        summary = QLabel(f"{foot_txt}  ·  {pages}  ·  {logo_txt}"); summary.setObjectName("Hint")
        summary.setWordWrap(True)
        mid.addWidget(summary)

        mt = self.ctx.profiles.updated_at(p.id)
        updated = time.strftime("%d %b %Y", time.localtime(mt)) if mt else "—"
        upd = QLabel(f"Updated {updated}"); upd.setObjectName("Hint")
        mid.addWidget(upd)
        row.addLayout(mid, 1)

        # per-card actions
        if is_active:
            act_btn = QPushButton("✓ Active"); act_btn.setObjectName("Ghost"); act_btn.setEnabled(False)
            tip(act_btn, "This profile is used on all exported and emailed minutes")
        else:
            act_btn = QPushButton("Set active"); act_btn.setObjectName("Ghost")
            act_btn.clicked.connect(lambda _=False, pid=p.id, nm=p.name: self._set_active(pid, nm))
            tip(act_btn, "Use this profile's branding on all exported and emailed minutes")
        edit_btn = QPushButton("Edit"); edit_btn.setObjectName("Ghost")
        edit_btn.clicked.connect(lambda _=False, pid=p.id: self._edit(pid))
        tip(edit_btn, "Edit this profile with a live page-layout preview")
        del_btn = QPushButton("Delete"); del_btn.setObjectName("Danger")
        del_btn.clicked.connect(lambda _=False, pid=p.id, nm=p.name: self._delete(pid, nm))
        tip(del_btn, "Permanently delete this profile — exports already made are unaffected")
        for b in (act_btn, edit_btn, del_btn):
            row.addWidget(b)
        return card

    def _new(self):
        dlg = ProfileDialog(self.ctx.profiles, None, self)
        if dlg.exec():
            self.reload(); self.toast.show_message("Profile created.", "success")

    def _edit(self, pid: str):
        prof = self.ctx.profiles.get(pid)
        if not prof:
            return
        dlg = ProfileDialog(self.ctx.profiles, prof, self)
        if dlg.exec():
            self.reload(); self.toast.show_message("Profile saved.", "success")

    def _delete(self, pid: str, name: str = ""):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Delete profile")
        box.setText(f"Delete “{name or 'this profile'}”?")
        box.setInformativeText("This permanently removes the profile and its logo. Minutes you "
                               "have already exported are not affected.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)      # safe default
        if box.exec() != QMessageBox.Yes:
            return
        was_active = self.ctx.settings.get("active_profile") == pid
        self.ctx.profiles.delete(pid)
        if was_active:
            self.ctx.settings.set("active_profile", "")
        self.reload()
        self.toast.show_message("Profile deleted.", "success")

    def _set_active(self, pid: str, name: str = ""):
        if QMessageBox.question(
                self, "Set active profile",
                f"Use “{name or 'this profile'}” for all exported and emailed minutes?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes) != QMessageBox.Yes:
            return
        self.ctx.settings.set("active_profile", pid)
        self.reload(); self.toast.show_message(f"“{name}” is now the active profile.", "success")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import profiles", "", "Profiles (*.json *.csv *.xlsx)")
        if path:
            try:
                n = len(self.ctx.profiles.import_file(path))
                self.reload(); self.toast.show_message(f"Imported {n} profile(s).", "success")
            except Exception as exc:
                QMessageBox.critical(self, "Import failed", str(exc))

    def _export(self):
        if not self._profiles:
            self.toast.show_message("No profiles to export.", "warn"); return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export profiles", "company-profiles.json",
            "JSON (*.json);;CSV (*.csv);;Excel (*.xlsx)")
        if path:
            try:
                self.ctx.profiles.export_file(self._profiles, path)
                self.toast.show_message("Profiles exported.", "success")
            except Exception as exc:
                QMessageBox.critical(self, "Export failed", str(exc))
