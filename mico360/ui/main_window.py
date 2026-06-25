"""Main application window: sidebar navigation + stacked pages + status bar."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .. import __app_name__, __version__
from ..config import resource_path
from . import theme
from .components import Toast
from .context import AppContext
from .pages import (
    HistoryPage, NewMeetingPage, ProfilesPage, PromptsPage, SettingsPage,
)
from .pages_extra import HelpPage, UpdatesPage

log = logging.getLogger("mico360.window")

NAV = [
    ("New Meeting", "✚"),
    ("History", "🕑"),
    ("Company Profiles", "🏢"),
    ("Prompt Library", "💬"),
    ("Updates", "⬇"),
    ("Help & About", "ⓘ"),
    ("Settings", "⚙"),
]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{__app_name__}")
        # minimum chosen so no page overflows horizontally; everything above is responsive
        self.setMinimumSize(QSize(1080, 660))
        self.resize(1180, 760)

        icon_path = resource_path("assets", "app.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QWidget(); root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.toast = Toast(self.stack)

        self.new_page = NewMeetingPage(ctx, self.toast)
        self.history_page = HistoryPage(ctx, self.toast, self._open_meeting)
        self.profiles_page = ProfilesPage(ctx, self.toast)
        self.prompts_page = PromptsPage(ctx, self.toast, on_change=self.new_page.refresh_prompts)
        self.updates_page = UpdatesPage(ctx, self.toast)
        self.help_page = HelpPage(ctx)
        self.settings_page = SettingsPage(
            ctx, self.toast, on_theme_change=self.apply_theme,
            on_models_change=self.new_page.refresh_models)

        # order MUST match NAV
        for p in (self.new_page, self.history_page, self.profiles_page,
                  self.prompts_page, self.updates_page, self.help_page, self.settings_page):
            self.stack.addWidget(p)
        layout.addWidget(self.stack, 1)

        self.apply_theme(ctx.settings.get("theme"))
        self._update_status()
        self._setup_shortcuts()

        # optional silent update check on startup
        if ctx.settings.get("auto_check_updates", True) and ctx.settings.get("github_repo", ""):
            QTimer.singleShot(2500, self.updates_page.check)
        # first-run onboarding
        QTimer.singleShot(400, self._maybe_onboard)

    def _maybe_onboard(self):
        from .onboarding import maybe_show
        maybe_show(self.ctx, self)

    def _setup_shortcuts(self):
        def add(seq, fn):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)
        # page navigation
        for i in range(len(NAV)):
            add(f"Ctrl+{i + 1}", lambda idx=i: (self.nav_group.button(idx).setChecked(True),
                                                self._navigate(idx)))
        add("Ctrl+G", lambda: self.new_page._generate())
        add("Ctrl+E", lambda: self.new_page._export())
        add("Ctrl+S", lambda: self.new_page._save_history())
        add("Ctrl+Shift+C", lambda: self.new_page._copy())
        add(QKeySequence.Find, lambda: (self._goto(self.history_page),
                                        self.history_page.search.setFocus()))
        add("F1", lambda: self._goto(self.help_page))

    def _goto(self, page):
        idx = self.stack.indexOf(page)
        if idx >= 0:
            self.nav_group.button(idx).setChecked(True)
            self._navigate(idx)

    # -- sidebar ------------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        side = QWidget(); side.setObjectName("Sidebar")
        side.setFixedWidth(216)
        v = QVBoxLayout(side); v.setContentsMargins(14, 18, 14, 14); v.setSpacing(6)

        brand_row = QHBoxLayout()
        logo = QLabel()
        pm = QPixmap(str(resource_path("assets", "logo_256.png")))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand_text = QVBoxLayout(); brand_text.setSpacing(0)
        b1 = QLabel("MICO360"); b1.setObjectName("Brand")
        b2 = QLabel("Meetings"); b2.setObjectName("BrandSub")
        brand_text.addWidget(b1); brand_text.addWidget(b2)
        brand_row.addWidget(logo); brand_row.addLayout(brand_text); brand_row.addStretch()
        v.addLayout(brand_row)
        v.addSpacing(12)

        self.nav_group = QButtonGroup(self); self.nav_group.setExclusive(True)
        for i, (label, icon) in enumerate(NAV):
            btn = QPushButton(f"  {icon}   {label}")
            btn.setObjectName("NavBtn"); btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self._navigate(idx))
            self.nav_group.addButton(btn, i)
            v.addWidget(btn)
        self.nav_group.button(0).setChecked(True)

        v.addStretch()
        self.status_chip = QLabel(); self.status_chip.setObjectName("Hint")
        self.status_chip.setWordWrap(True)
        v.addWidget(self.status_chip)
        ver = QLabel(f"v{__version__}"); ver.setObjectName("Hint")
        v.addWidget(ver)
        return side

    def _navigate(self, idx: int):
        self.stack.setCurrentIndex(idx)
        page = self.stack.widget(idx)
        if page is self.history_page:
            self.history_page.reload()
        elif page is self.profiles_page:
            self.profiles_page.reload()
        elif page is self.new_page:
            self.new_page.refresh_models()
            self._update_status()

    def _open_meeting(self, meeting):
        self.new_page.load_meeting(meeting)
        self.nav_group.button(0).setChecked(True)
        self.stack.setCurrentIndex(0)

    def _update_status(self):
        st = self.ctx.ollama_status()
        if st.running:
            n = len(st.models)
            self.status_chip.setText(f"● Ollama online · {n} model{'s' if n != 1 else ''}")
            self.status_chip.setStyleSheet("color:#22C55E; font-size:9pt;")
        else:
            self.status_chip.setText("● Ollama offline — run 'ollama serve'")
            self.status_chip.setStyleSheet("color:#EF4444; font-size:9pt;")

    # -- theme --------------------------------------------------------------
    def apply_theme(self, name: str):
        self.setStyleSheet(theme.build_qss(name))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toast._reposition()

    def closeEvent(self, e):
        # flush any in-progress recording so no temp file is left dangling
        try:
            self.new_page.recorder_panel.stop_if_active()
        except Exception:
            pass
        super().closeEvent(e)
