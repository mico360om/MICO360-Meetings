"""Settings → PDF design: each company picks the layout of its PDF minutes.

The choice is stored in that company's profile (`CompanyProfile.pdf_design`),
independently of every other company, and is applied to every PDF exported or
e-mailed for it. "No company" keeps its own choice in the app settings
(`pdf_design`) for exports made without a profile.

The thumbnails are page 1 of a sample export rendered from the real PDF in the
selected company's branding, so the picker shows exactly what will be produced.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..export import pdf_designs
from . import metrics as M
from . import pdf_preview
from .components import hint, scroll_area as _scroll, tip
from .context import AppContext

log = logging.getLogger("mico360.pdf_design_tab")

THUMB_W = 188


class PdfDesignTab(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx, self.toast = ctx, toast
        self._cards: dict[str, dict] = {}
        self._thumbs: dict[tuple, QPixmap] = {}       # (profile signature, design) -> pixmap
        self._loaded_for: object = None

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(M.XXL, M.XL, M.XXL, M.XL); v.setSpacing(M.CARD_GAP)
        v.addWidget(hint("Choose how each company's PDF minutes look. Every design uses that "
                         "company's own logo, letterhead, colour, footer and page numbers — "
                         "only the layout changes. The choice is saved per company and used "
                         "for every PDF you export or e-mail for it."))

        row = QHBoxLayout(); row.setSpacing(M.SM)
        row.addWidget(QLabel("Company"))
        self.company = QComboBox()
        self.company.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.company.setMinimumContentsLength(26)
        self.company.activated.connect(lambda *_: self.refresh(force=True))
        tip(self.company, "The company whose PDF design you are choosing. Each company keeps "
                          "its own design")
        row.addWidget(self.company)
        self.current_lbl = QLabel(""); self.current_lbl.setObjectName("Hint")
        row.addWidget(self.current_lbl, 1)
        self.sample_btn = QPushButton("Open sample PDF"); self.sample_btn.setObjectName("Ghost")
        self.sample_btn.clicked.connect(self._open_sample)
        tip(self.sample_btn, "Open a sample set of minutes in this company's selected design "
                             "with your PDF viewer")
        row.addWidget(self.sample_btn)
        v.addLayout(row)

        grid = QGridLayout(); grid.setSpacing(M.MD)
        for i, d in enumerate(pdf_designs.choices()):
            grid.addWidget(self._build_card(d), 0, i)
            grid.setColumnStretch(i, 1)
        v.addLayout(grid)
        v.addStretch()
        outer.addWidget(_scroll(content, max_width=980))

    # -- cards ----------------------------------------------------------------
    def _build_card(self, d: pdf_designs.Design) -> QWidget:
        card = QWidget(); card.setObjectName("Card")
        lay = QVBoxLayout(card); lay.setContentsMargins(M.MD, M.MD, M.MD, M.MD); lay.setSpacing(M.SM)
        thumb = QLabel(); thumb.setAlignment(Qt.AlignCenter)
        thumb.setFixedSize(THUMB_W + 2, int(THUMB_W * 1.414) + 2)
        thumb.setStyleSheet("border: 1px solid #C9C2C0; background: #FFFFFF; color: #6C6269;")
        thumb.setWordWrap(True)
        name = QLabel(d.name); name.setObjectName("ProfileName")
        blurb = QLabel(d.blurb); blurb.setObjectName("Hint"); blurb.setWordWrap(True)
        blurb.setAlignment(Qt.AlignTop)
        btn = QPushButton("Use this design")
        btn.clicked.connect(lambda _=False, k=d.key: self.select(k))
        tip(btn, f"Use the {d.name} design for this company's PDF minutes")
        lay.addWidget(thumb, 0, Qt.AlignHCenter)
        lay.addWidget(name); lay.addWidget(blurb, 1); lay.addWidget(btn)
        self._cards[d.key] = {"card": card, "thumb": thumb, "btn": btn}
        return card

    # -- data -------------------------------------------------------------------
    def company_id(self) -> str:
        return self.company.currentData() or ""

    def _profile(self):
        pid = self.company_id()
        return self.ctx.profiles.get(pid) if pid else None

    def current_design(self) -> str:
        p = self._profile()
        key = p.pdf_design if p else self.ctx.settings.get("pdf_design", "classic")
        return pdf_designs.get(key).key

    def _signature(self, p) -> str:
        """What a thumbnail depends on: the company's branding (not which design
        it currently uses — the design is the other half of the cache key)."""
        if p is None:
            return "plain"
        d = p.to_dict()
        d.pop("pdf_design", None)
        try:
            d["_logo_mtime"] = Path(p.logo_path).stat().st_mtime if p.logo_path else 0
        except OSError:
            d["_logo_mtime"] = 0
        return repr(sorted(d.items()))

    def _fill_companies(self) -> None:
        keep = self.company_id() if self.company.count() else None
        profiles = self.ctx.profiles.list()
        self.company.blockSignals(True)
        self.company.clear()
        for p in profiles:
            self.company.addItem(p.name, p.id)
        self.company.addItem("No company (plain exports)", "")
        want = keep if keep is not None else self.ctx.settings.get("active_profile", "")
        idx = self.company.findData(want)
        self.company.setCurrentIndex(idx if idx >= 0 else 0)
        self.company.blockSignals(False)

    def refresh(self, force: bool = False) -> None:
        """(Re)load the company list, the selection and the thumbnails. Cheap when
        nothing changed: thumbnails are cached per company state."""
        if not force:
            self._fill_companies()
        p = self._profile()
        sig = self._signature(p)
        chosen = self.current_design()
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for d in pdf_designs.choices():
                w = self._cards[d.key]
                key = (sig, d.key)
                if key not in self._thumbs:
                    img = pdf_preview.render_design(p, d.key, THUMB_W * 2)
                    if img is not None:
                        pm = QPixmap.fromImage(img)
                        pm.setDevicePixelRatio(2.0)
                        self._thumbs[key] = pm
                pm = self._thumbs.get(key)
                if pm is not None:
                    w["thumb"].setPixmap(pm)
                else:
                    w["thumb"].setText(f"{d.name}\n\n(preview unavailable)")
        finally:
            QGuiApplication.restoreOverrideCursor()
        self._mark(chosen)

    def _mark(self, chosen: str) -> None:
        for key, w in self._cards.items():
            on = key == chosen
            w["card"].setObjectName("CardActive" if on else "Card")
            w["card"].style().unpolish(w["card"]); w["card"].style().polish(w["card"])
            w["btn"].setText("✓ In use" if on else "Use this design")
            w["btn"].setEnabled(not on)
            w["btn"].setObjectName("Ghost" if on else "Primary")
            w["btn"].style().unpolish(w["btn"]); w["btn"].style().polish(w["btn"])
        who = self.company.currentText() if self.company_id() else "Exports without a company"
        self.current_lbl.setText(f"{who}: {pdf_designs.get(chosen).name} design")

    def select(self, design_key: str) -> None:
        """Save `design_key` for the company shown (only that company)."""
        key = pdf_designs.get(design_key).key
        p = self._profile()
        if p is not None:
            p.pdf_design = key
            self.ctx.profiles.save(p)
            who = f"“{p.name}”"
        else:
            self.ctx.settings.set("pdf_design", key)
            who = "Exports without a company"
        self._mark(key)
        self.toast.show_message(f"{who} now use{'s' if p is not None else ''} the "
                                f"{pdf_designs.get(key).name} PDF design.", "success", 4000)

    def _open_sample(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..config import TMP_DIR
        from ..export.pdf_export import export_pdf
        try:
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            import time
            # a new name each time: the viewer may still hold the previous sample open
            out = Path(TMP_DIR) / f"sample-minutes-{time.strftime('%H%M%S')}.pdf"
            export_pdf(pdf_preview.SAMPLE_MINUTES, out, self._profile(),
                       design=self.current_design())
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))
        except Exception as exc:
            log.warning("sample PDF failed", exc_info=True)
            self.toast.show_message(f"Couldn't create the sample PDF: {exc}", "error", 6000)
