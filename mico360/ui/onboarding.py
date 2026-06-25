"""First-run onboarding — checks the environment and guides setup."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from .. import __app_name__, __version__
from ..config import resource_path


def maybe_show(ctx, parent=None) -> None:
    """Show the onboarding dialog once (first run)."""
    if ctx.settings.get("onboarded", False):
        return
    OnboardingDialog(ctx, parent).exec()
    ctx.settings.set("onboarded", True)


class OnboardingDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(f"Welcome to {__app_name__}")
        self.resize(560, 480)
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 20)
        v.setSpacing(12)

        logo = QLabel()
        pm = QPixmap(str(resource_path("assets", "logo_256.png")))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title = QLabel(f"<h2>Welcome to {__app_name__}</h2>")
        head = QHBoxLayout(); head.addWidget(logo); head.addWidget(title, 1)
        v.addLayout(head)

        v.addWidget(self._wrap(
            "Turn meeting recordings and transcripts into professional minutes — "
            "<b>entirely on your computer</b>. Here's a quick setup check:"))

        self.checks = QLabel(); self.checks.setTextFormat(Qt.RichText)
        self.checks.setWordWrap(True)
        v.addWidget(self.checks)
        v.addWidget(self._wrap(
            "<b>How it works:</b> 1) Add a recording, file or pasted text → "
            "2) it's transcribed offline with Whisper → 3) a local Ollama model "
            "writes the minutes → 4) edit &amp; export to Word/PDF."))
        v.addStretch()

        row = QHBoxLayout()
        recheck = QPushButton("Re-check"); recheck.clicked.connect(self._refresh)
        self.settings_btn = QPushButton("Open Settings to install a model")
        self.settings_btn.clicked.connect(self._open_settings)
        ok = QPushButton("Get started"); ok.setObjectName("Primary"); ok.clicked.connect(self.accept)
        row.addWidget(recheck); row.addStretch(); row.addWidget(self.settings_btn); row.addWidget(ok)
        v.addLayout(row)
        self._refresh()

    def _wrap(self, html: str) -> QLabel:
        lbl = QLabel(html); lbl.setWordWrap(True); lbl.setTextFormat(Qt.RichText)
        return lbl

    def _refresh(self):
        import sys
        st = self.ctx.ollama_status()
        def mark(ok, warn=False):
            return "✅" if ok else ("⚠️" if warn else "❌")
        py = f"Python {sys.version_info.major}.{sys.version_info.minor} (bundled)"
        ollama = ("running" if st.running else "not detected — install from ollama.com")
        models = (f"{len(st.models)} installed" if st.models
                  else "none yet — install one in Settings")
        lines = [
            f"{mark(True)} <b>App ready</b> — {py}",
            f"{mark(st.running, warn=True)} <b>Ollama</b> — {ollama}",
            f"{mark(bool(st.models), warn=True)} <b>AI model</b> — {models}",
            f"{mark(True)} <b>Whisper</b> — downloads automatically on first transcription",
        ]
        self.checks.setText("<div style='line-height:1.8'>" + "<br>".join(lines) + "</div>")
        self.settings_btn.setVisible(not st.models)

    def _open_settings(self):
        # navigate the main window to Settings if available
        p = self.parent()
        try:
            if p is not None and hasattr(p, "nav_group"):
                idx = p.stack.indexOf(p.settings_page)
                p.nav_group.button(idx).setChecked(True)
                p.stack.setCurrentIndex(idx)
        except Exception:
            pass
        self.accept()
