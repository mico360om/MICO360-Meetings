"""Dialogs: company-profile editor (with live layout preview) and prompt editor."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core.profiles import (
    ALIGNMENTS, CompanyProfile, LOGO_POSITIONS, PAGENUM_POSITIONS, ProfileStore,
)
from ..core.prompts import TRANSCRIPT_TOKEN
from .components import tip


class PagePreview(QWidget):
    """Paints a small A4-ish page thumbnail reflecting the profile layout."""
    def __init__(self):
        super().__init__()
        self.profile: CompanyProfile | None = None
        self.setMinimumSize(230, 320)

    def set_profile(self, p: CompanyProfile):
        self.profile = p
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # page
        margin = 8
        page = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)
        p.fillRect(self.rect(), QColor("#201E24"))
        p.setBrush(QColor("#FFFFFF")); p.setPen(QColor("#D7DEEA"))
        p.drawRoundedRect(page, 6, 6)
        if not self.profile:
            return
        prof = self.profile
        accent = QColor(prof.accent_color if prof.accent_color.startswith("#") else "#8B1E1E")
        pad = margin + 12
        top = margin + 16

        # logo block
        logo_w = max(24.0, min(70.0, prof.logo_width_mm * 1.4))
        logo_h = logo_w * 0.5
        if prof.logo_position == "center":
            lx = (w - logo_w) / 2
        elif prof.logo_position == "right":
            lx = w - pad - logo_w
        else:
            lx = pad
        if prof.logo_path and Path(prof.logo_path).exists():
            pm = QPixmap(prof.logo_path)
            if not pm.isNull():
                pm = pm.scaled(int(logo_w), int(logo_h), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(int(lx), int(top), pm)
        else:
            p.setBrush(accent); p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(lx, top, logo_w, logo_h), 3, 3)

        # company name + contact
        name_align = Qt.AlignRight if prof.logo_position != "right" else Qt.AlignLeft
        p.setPen(accent); f = QFont(); f.setBold(True); f.setPointSize(8); p.setFont(f)
        p.drawText(QRectF(pad, top, w - 2 * pad, 16), name_align | Qt.AlignTop, prof.name or "Company")
        p.setPen(QColor("#888")); f2 = QFont(); f2.setPointSize(5); p.setFont(f2)
        contact = "  ".join(x for x in (prof.phone, prof.email, prof.website) if x)
        p.drawText(QRectF(pad, top + 14, w - 2 * pad, 12), name_align | Qt.AlignTop, contact)

        # divider
        p.setPen(accent); p.drawLine(int(pad), int(top + 34), int(w - pad), int(top + 34))

        # body lines
        p.setPen(QColor("#C9D2E0"))
        y = top + 48
        for _i in range(8):
            p.drawLine(int(pad), int(y), int(w - pad - 20), int(y))
            y += 14

        # footer
        p.setPen(QColor("#888")); f3 = QFont(); f3.setPointSize(5); p.setFont(f3)
        fy = h - margin - 16
        falign = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                  "right": Qt.AlignRight}[prof.footer_alignment]
        p.drawText(QRectF(pad, fy, w - 2 * pad, 12), falign, prof.footer_text or "")
        if prof.show_page_numbers:
            pn = prof.page_number_format.replace("{n}", "1").replace("{total}", "3")
            pos = prof.page_number_position
            pa = (Qt.AlignLeft if pos.endswith("left")
                  else Qt.AlignRight if pos.endswith("right") else Qt.AlignHCenter)
            yy = (h - margin - 10) if "footer" in pos else (margin + 4)
            p.drawText(QRectF(pad, yy, w - 2 * pad, 12), pa, pn)


class ProfileDialog(QDialog):
    def __init__(self, store: ProfileStore, profile: CompanyProfile | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.profile = profile or CompanyProfile()
        self.setWindowTitle("Company Profile")
        self.resize(820, 600)

        root = QHBoxLayout()

        # --- left: form (scrollable-friendly) ---
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setLabelAlignment(Qt.AlignRight)

        self.name = QLineEdit(self.profile.name)
        self.address = QLineEdit(self.profile.address)
        self.phone = QLineEdit(self.profile.phone)
        self.email = QLineEdit(self.profile.email)
        self.website = QLineEdit(self.profile.website)
        self.footer = QLineEdit(self.profile.footer_text)

        self.logo_pos = QComboBox(); self.logo_pos.addItems(LOGO_POSITIONS)
        self.logo_pos.setCurrentText(self.profile.logo_position)
        self.logo_w = QDoubleSpinBox(); self.logo_w.setRange(10, 90)
        self.logo_w.setValue(self.profile.logo_width_mm); self.logo_w.setSuffix(" mm")
        self.footer_align = QComboBox(); self.footer_align.addItems(ALIGNMENTS)
        self.footer_align.setCurrentText(self.profile.footer_alignment)
        self.page_nums = QCheckBox("Show page numbers")
        self.page_nums.setChecked(self.profile.show_page_numbers)
        self.page_pos = QComboBox(); self.page_pos.addItems(PAGENUM_POSITIONS)
        self.page_pos.setCurrentText(self.profile.page_number_position)
        self.page_fmt = QLineEdit(self.profile.page_number_format)

        tip(self.logo_pos, "Where the logo sits in the exported letterhead: left, centre or right")
        tip(self.logo_w, "Printed logo width in millimetres — height scales automatically "
                         "without losing quality")
        tip(self.footer_align, "Alignment of the footer text on exported pages")
        tip(self.page_nums, "Print page numbers on exported PDF/Word documents")
        tip(self.page_pos, "Where page numbers appear: header or footer, left/centre/right")
        tip(self.page_fmt, "Page-number wording — {n} is the page, {total} the page count, "
                           "e.g. 'Page {n} of {total}'")
        self._logo_path = self.profile.logo_path
        self.logo_btn = QPushButton("Choose logo…")
        self.logo_btn.clicked.connect(self._choose_logo)
        tip(self.logo_btn, "Pick a PNG/JPG logo — it is copied into the profile at full "
                           "resolution, so the original file can move")
        self.logo_lbl = QLabel(Path(self._logo_path).name if self._logo_path else "No logo")
        self.logo_lbl.setObjectName("Hint")
        logo_row = QHBoxLayout(); logo_row.addWidget(self.logo_btn); logo_row.addWidget(self.logo_lbl, 1)

        self._accent = self.profile.accent_color
        self.color_btn = QPushButton("Accent colour…")
        self.color_btn.clicked.connect(self._choose_color)
        tip(self.color_btn, "Brand colour used for headings, the letterhead rule and table "
                            "headers in exports")
        self._paint_color_btn()

        form.addRow("Company name", self.name)
        form.addRow("Address", self.address)
        form.addRow("Phone", self.phone)
        form.addRow("Email", self.email)
        form.addRow("Website", self.website)
        form.addRow("Logo", logo_row)
        form.addRow("Logo position", self.logo_pos)
        form.addRow("Logo width", self.logo_w)
        form.addRow("Footer text", self.footer)
        form.addRow("Footer alignment", self.footer_align)
        form.addRow("", self.page_nums)
        form.addRow("Page number position", self.page_pos)
        form.addRow("Page number format", self.page_fmt)
        form.addRow("Accent", self.color_btn)

        # --- right: live preview ---
        right = QVBoxLayout()
        right.addWidget(QLabel("Live preview"))
        self.preview = PagePreview()
        right.addWidget(self.preview, 1)

        root.addWidget(form_host, 3)
        right_host = QWidget(); right_host.setLayout(right)
        root.addWidget(right_host, 2)

        # buttons
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)

        main = QVBoxLayout(self)
        main.addLayout(root, 1)
        main.addWidget(bb)

        for w in (self.name, self.address, self.phone, self.email, self.website,
                  self.footer, self.page_fmt):
            w.textChanged.connect(self._refresh_preview)
        for w in (self.logo_pos, self.footer_align, self.page_pos):
            w.currentTextChanged.connect(self._refresh_preview)
        self.logo_w.valueChanged.connect(self._refresh_preview)
        self.page_nums.toggled.connect(self._refresh_preview)
        self._refresh_preview()

    def _collect(self) -> CompanyProfile:
        p = self.profile
        p.name = self.name.text().strip() or "Company"
        p.address = self.address.text().strip()
        p.phone = self.phone.text().strip()
        p.email = self.email.text().strip()
        p.website = self.website.text().strip()
        p.footer_text = self.footer.text().strip()
        p.logo_position = self.logo_pos.currentText()
        p.logo_width_mm = self.logo_w.value()
        p.footer_alignment = self.footer_align.currentText()
        p.show_page_numbers = self.page_nums.isChecked()
        p.page_number_position = self.page_pos.currentText()
        p.page_number_format = self.page_fmt.text().strip() or "Page {n} of {total}"
        p.logo_path = self._logo_path
        p.accent_color = self._accent
        return p

    def _refresh_preview(self):
        self.preview.set_profile(self._collect())

    def _choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose logo", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if path:
            saved = self.store.set_logo(self._collect(), path)
            self._logo_path = saved.logo_path
            self.logo_lbl.setText(Path(self._logo_path).name)
            self._refresh_preview()

    def _choose_color(self):
        col = QColorDialog.getColor(QColor(self._accent), self, "Accent colour")
        if col.isValid():
            self._accent = col.name()
            self._paint_color_btn()
            self._refresh_preview()

    def _paint_color_btn(self):
        self.color_btn.setStyleSheet(
            f"background:{self._accent}; color:white; border:none; border-radius:8px; padding:8px 14px;")

    def _save(self):
        self.profile = self.store.save(self._collect())
        self.accept()


class PromptDialog(QDialog):
    def __init__(self, name: str = "", text: str = "", category: str = "General", parent=None):
        super().__init__(parent)
        from ..core.prompts import PROMPT_CATEGORIES
        self.setWindowTitle("Prompt")
        self.resize(700, 560)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        ncol = QVBoxLayout(); ncol.addWidget(QLabel("Prompt name"))
        self.name = QLineEdit(name); ncol.addWidget(self.name)
        ccol = QVBoxLayout(); ccol.addWidget(QLabel("Category"))
        self.category = QComboBox(); self.category.setEditable(True)
        self.category.addItems(PROMPT_CATEGORIES + ["General"])
        self.category.setCurrentText(category or "General")
        tip(self.category, "Groups the prompt in the library — pick one or type a new category")
        ccol.addWidget(self.category)
        row.addLayout(ncol, 2); row.addLayout(ccol, 1)
        lay.addLayout(row)

        lay.addWidget(QLabel("Prompt text"))
        info = QLabel(f"Use {TRANSCRIPT_TOKEN} where the transcript should be inserted "
                      "(it is added automatically if you omit it).")
        info.setObjectName("Hint"); info.setWordWrap(True)
        lay.addWidget(info)
        self.text = QPlainTextEdit(text)
        self.text.setMinimumHeight(280)
        lay.addWidget(self.text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._validate_accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _validate_accept(self):
        from PySide6.QtWidgets import QMessageBox
        if not self.name.text().strip():
            QMessageBox.warning(self, "Name required", "Please enter a prompt name.")
            return
        if not self.text.toPlainText().strip():
            QMessageBox.warning(self, "Prompt required", "The prompt text cannot be empty.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (self.name.text().strip(), self.text.toPlainText(),
                self.category.currentText().strip() or "General")


class FollowupDialog(QDialog):
    """Per-owner follow-up drafts: copy each, or open the email composer."""
    sendRequested = Signal(str, str, str)      # owner, subject, body

    def __init__(self, drafts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Send follow-up emails")
        self.resize(600, 480)
        self._drafts = drafts
        lay = QVBoxLayout(self)
        info = QLabel("One draft per person, listing their open action items with deadlines. "
                      "Email addresses aren't stored — you'll enter each recipient when sending.")
        info.setObjectName("Hint"); info.setWordWrap(True)
        lay.addWidget(info)

        host = QWidget(); rows = QVBoxLayout(host); rows.setContentsMargins(0, 0, 0, 0); rows.setSpacing(8)
        for owner, subject, body, count in drafts:
            card = QWidget(); card.setObjectName("Card")
            rl = QHBoxLayout(card); rl.setContentsMargins(14, 10, 12, 10); rl.setSpacing(8)
            col = QVBoxLayout(); col.setSpacing(1)
            name = QLabel(owner); name.setObjectName("ProfileName")
            sub = QLabel(f"{count} open item{'s' if count != 1 else ''}"); sub.setObjectName("Hint")
            col.addWidget(name); col.addWidget(sub)
            rl.addLayout(col, 1)
            copy = QPushButton("Copy"); copy.setObjectName("Ghost")
            copy.clicked.connect(lambda _=False, b=f"Subject: {subject}\n\n{body}": self._copy(b))
            tip(copy, "Copy this follow-up (subject + body) to the clipboard")
            email = QPushButton("Email…"); email.setObjectName("Primary")
            email.clicked.connect(lambda _=False, o=owner, s=subject, b=body: self.sendRequested.emit(o, s, b))
            tip(email, "Open the email composer pre-filled with this person's follow-up")
            rl.addWidget(copy); rl.addWidget(email)
            rows.addWidget(card)
        rows.addStretch()
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QScrollArea.NoFrame)
        sa.setWidget(host)
        lay.addWidget(sa, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject); bb.accepted.connect(self.reject)
        lay.addWidget(bb)

    def _copy(self, text: str):
        QGuiApplication.clipboard().setText(text)


class ActionItemDialog(QDialog):
    """Edit one action item's fields directly (no reopening the meeting)."""
    def __init__(self, item, parent=None):
        super().__init__(parent)
        from ..core.tasks import STATUS_CYCLE, PRIORITIES
        self.setWindowTitle("Edit action item")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        form = QFormLayout(); form.setSpacing(10)

        self.task = QPlainTextEdit(item.task); self.task.setMaximumHeight(80)
        tip(self.task, "What needs to be done")
        form.addRow("Task", self.task)
        self.owner = QLineEdit(item.owner)
        tip(self.owner, "Responsible person")
        form.addRow("Responsible", self.owner)
        self.deadline = QLineEdit(item.deadline)
        self.deadline.setPlaceholderText("e.g. 2026-09-30")
        tip(self.deadline, "Deadline — a real date (YYYY-MM-DD) enables overdue detection")
        form.addRow("Deadline", self.deadline)

        self.priority = QComboBox(); self.priority.addItems(["—"] + PRIORITIES)
        self.priority.setCurrentText(item.priority or "—")
        tip(self.priority, "Priority: High, Medium or Low")
        form.addRow("Priority", self.priority)
        self.status = QComboBox(); self.status.addItems(STATUS_CYCLE)
        if item.status not in STATUS_CYCLE:
            self.status.addItem(item.status)
        self.status.setCurrentText(item.status)
        tip(self.status, "Workflow status")
        form.addRow("Status", self.status)

        self.notes = QPlainTextEdit(item.notes); self.notes.setMinimumHeight(90)
        self.notes.setPlaceholderText("Optional notes — context, blockers, links…")
        tip(self.notes, "Free-text notes kept with this action item")
        form.addRow("Notes", self.notes)
        lay.addLayout(form, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._validate_accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _validate_accept(self):
        if not self.task.toPlainText().strip():
            QMessageBox.warning(self, "Task required", "The task cannot be empty.")
            return
        self.accept()

    def values(self) -> dict:
        prio = self.priority.currentText()
        return {
            "task": self.task.toPlainText().strip(),
            "owner": self.owner.text().strip(),
            "deadline": self.deadline.text().strip(),
            "priority": "" if prio == "—" else prio,
            "status": self.status.currentText().strip(),
            "notes": self.notes.toPlainText().strip(),
        }


class EmailComposeDialog(QDialog):
    """Compose an email (of the minutes, or a follow-up) — attachments optional."""
    def __init__(self, subject: str, body: str, to: str = "", parent=None,
                 attachments: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Email minutes" if attachments else "Send follow-up")
        self.resize(620, 540)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.to = QLineEdit(to); self.to.setPlaceholderText("recipient@example.com, another@example.com")
        tip(self.to, "Recipient address(es), separated by commas — required")
        self.cc = QLineEdit()
        tip(self.cc, "Optional carbon-copy addresses, separated by commas")
        self.subject = QLineEdit(subject)
        form.addRow("To", self.to)
        form.addRow("Cc", self.cc)
        form.addRow("Subject", self.subject)
        lay.addLayout(form)

        self.att_pdf = QCheckBox("PDF"); self.att_pdf.setChecked(True)
        self.att_docx = QCheckBox("Word (.docx)")
        if attachments:
            att = QHBoxLayout()
            att.addWidget(QLabel("Attach:"))
            tip(self.att_pdf, "Attach the minutes as a PDF, branded with the active company profile")
            tip(self.att_docx, "Attach the minutes as an editable Word document")
            att.addWidget(self.att_pdf); att.addWidget(self.att_docx); att.addStretch()
            lay.addLayout(att)
        else:                                   # follow-up: plain text, no attachments
            self.att_pdf.setChecked(False)

        lay.addWidget(QLabel("Message"))
        self.body = QPlainTextEdit(body)
        self.body.setMinimumHeight(240)
        lay.addWidget(self.body, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.send_btn = bb.addButton("Send", QDialogButtonBox.AcceptRole)
        self.send_btn.setObjectName("Primary")
        bb.accepted.connect(self._validate); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _validate(self):
        if not self.to.text().strip():
            QMessageBox.warning(self, "Recipient required", "Enter at least one 'To' address.")
            return
        self.accept()

    def values(self) -> dict:
        fmts = []
        if self.att_pdf.isChecked():
            fmts.append(".pdf")
        if self.att_docx.isChecked():
            fmts.append(".docx")
        return {
            "to": [a.strip() for a in self.to.text().split(",") if a.strip()],
            "cc": [a.strip() for a in self.cc.text().split(",") if a.strip()],
            "subject": self.subject.text().strip() or "Meeting Minutes",
            "body": self.body.toPlainText(),
            "formats": fmts,
        }
