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
from .pages_extra import ActionItemsPage, HelpPage, UpdatesPage
from .insights_page import InsightsPage

log = logging.getLogger("mico360.window")

NAV = [
    ("New Meeting", "✚", "Upload, record or paste a meeting and generate minutes"),
    ("History", "🕑", "Search and reopen past meetings (Ctrl+F to search)"),
    ("Action Items", "✔", "All tasks from every meeting — track status, export CSV"),
    ("Insights", "📊", "Cross-meeting dashboard: action-item health, owners, cadence, themes"),
    ("Company Profiles", "🏢", "Branding for exported minutes: logo, footer, page numbers"),
    ("Prompt Library", "💬", "Create and manage the AI prompts used to write minutes"),
    ("Updates", "⬇", "Check for and install new versions of the app"),
    ("Help & About", "ⓘ", "Guides, contact, terms and privacy (F1)"),
    ("Settings", "⚙", "AI models, recording, email and app preferences"),
]
# Group header shown above the nav item at this index (logical grouping).
NAV_SECTIONS = {0: "Workspace", 4: "Library", 6: "System"}


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{__app_name__}")
        # minimum chosen so no page overflows horizontally; everything above is responsive
        self.setMinimumSize(QSize(1080, 660))
        self.resize(1320, 820)

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
        self.actions_page = ActionItemsPage(ctx, self.toast, on_open_meeting=self._open_meeting)
        self.insights_page = InsightsPage(ctx, self.toast)
        self.profiles_page = ProfilesPage(ctx, self.toast)
        self.prompts_page = PromptsPage(ctx, self.toast, on_change=self.new_page.refresh_prompts)
        self.updates_page = UpdatesPage(ctx, self.toast)
        self.help_page = HelpPage(ctx)
        self.settings_page = SettingsPage(
            ctx, self.toast, on_theme_change=self.apply_theme,
            on_models_change=self._refresh_ai)

        # Wire the New Meeting readiness banner's one-click actions.
        self.new_page.on_open_settings = lambda: self._goto(self.settings_page)
        self.new_page.on_install_model = self._install_model_flow
        self.new_page.on_switch_local = self._switch_to_local
        self.new_page.refresh_readiness()

        # order MUST match NAV
        for p in (self.new_page, self.history_page, self.actions_page, self.insights_page,
                  self.profiles_page, self.prompts_page, self.updates_page, self.help_page,
                  self.settings_page):
            self.stack.addWidget(p)
        layout.addWidget(self.stack, 1)

        self.apply_theme(ctx.settings.get("theme"))
        self._update_status()
        self._setup_shortcuts()

        # optional silent update check on startup
        if ctx.settings.get("auto_check_updates", True) and ctx.settings.get("github_repo", ""):
            QTimer.singleShot(2500, self.updates_page.check)
        # optional git auto-update (source checkouts)
        if ctx.settings.get("git_auto_update", False):
            QTimer.singleShot(1800, self._git_auto_update)
        # first-run onboarding
        QTimer.singleShot(400, self._maybe_onboard)
        # startup reminder: overdue / due-soon action items
        QTimer.singleShot(1600, self._show_task_digest)
        # auto-record: watch for live meetings / calendar starts (opt-in)
        self._watch = None
        self.settings_page.on_auto_record_change = self.apply_auto_record
        self.new_page.recorder_panel.recordingStateChanged.connect(self._on_recording_state)
        QTimer.singleShot(2000, lambda: self.apply_auto_record(
            bool(ctx.settings.get("auto_record", False))))

    # -- auto-record --------------------------------------------------------
    def apply_auto_record(self, on: bool):
        """Start/stop the background meeting watcher to match the setting."""
        if on and self._watch is None:
            from .workers import MeetingWatchWorker
            from ..core import meeting_watch as MW
            lead = float(self.ctx.settings.get("auto_record_lead_minutes", 3) or 3)
            ics = str(self.ctx.settings.get("auto_record_ics", "") or "").strip()

            def calendar():
                items = MW.upcoming_from_outlook()
                if ics:
                    items += MW.upcoming_from_ics(ics)
                return items
            self._watch = MeetingWatchWorker(calendar_fn=calendar, lead_minutes=lead)
            self._watch.meetingDetected.connect(self._on_meeting_detected)
            self._watch.meetingEnded.connect(self._on_meeting_ended)
            self._watch.meetingDue.connect(self._on_meeting_due)
            self._watch.start()
        elif not on and self._watch is not None:
            self._watch.stop(); self._watch.wait(3000); self._watch = None

    def _begin_auto_record(self, label: str) -> bool:
        panel = self.new_page.recorder_panel
        if panel.is_recording():
            return True
        self._goto(self.new_page)
        self.new_page.select_record_tab()                      # Record tab (by widget, not index)
        from ..core import recording as R
        source = "both" if R.system_audio_supported() else "mic"
        self.new_page._auto_generate_pending = True
        ok = panel.start_auto(source, live=True)
        if ok:
            self.toast.show_message(f"● Recording {label} — remember to tell participants.", "warn", 8000)
        else:
            self.new_page._auto_generate_pending = False
            self.toast.show_message("Couldn't start recording automatically — check the microphone.",
                                    "error", 7000)
        return ok

    def _on_meeting_detected(self, app: str, title: str):
        if self.new_page.recorder_panel.is_recording():
            return
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(
                self, "Meeting detected",
                f"A {app} meeting looks like it's running:\n\n“{title}”\n\n"
                "Record it now? (system audio + microphone, with a live transcript; "
                "minutes are generated when it ends)\n\n"
                "Make sure participants know the meeting is being recorded.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        self._begin_auto_record(f"{app}: {title}")

    def _on_meeting_ended(self):
        panel = self.new_page.recorder_panel
        if panel.is_recording() and getattr(panel, "_auto_mode", False):
            panel.stop_auto()
            self.toast.show_message("Meeting ended — saving the recording…", "info", 5000)

    def _on_meeting_due(self, title: str, join_url: str):
        if self.new_page.recorder_panel.is_recording():
            return
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        msg = f"“{title}” is starting now."
        msg += "\n\nJoin it and start recording?" if join_url else "\n\nStart recording?"
        if QMessageBox.question(self, "Meeting starting", msg,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        if join_url:
            QDesktopServices.openUrl(QUrl(join_url))
        self._begin_auto_record(title)

    def _on_recording_state(self, on: bool):
        # Unmissable global indicator: the window/taskbar title itself.
        self.setWindowTitle(f"● Recording — {__app_name__}" if on else __app_name__)

    def _git_auto_update(self):
        from ..core import git_update
        if not git_update.is_git_checkout():
            return
        from .workers import GitUpdateWorker
        self._git_auto_worker = GitUpdateWorker("pull")
        self._git_auto_worker.done.connect(self._on_git_auto)
        self._git_auto_worker.start()

    def _on_git_auto(self, res: dict):
        if res.get("ok") and res.get("updated"):
            self.toast.show_message(
                f"Updated from Git ({res.get('commit', '')}). Restart to apply the update.",
                "success", 8000)

    def _maybe_onboard(self):
        from .onboarding import maybe_show
        maybe_show(self.ctx, self)

    def _show_task_digest(self):
        """A gentle startup nudge if action items are overdue or due this week."""
        try:
            from ..core import tasks as T
            overdue, due_soon = T.reminder_counts(self.ctx.action_items.all_items())
        except Exception:
            return
        if not (overdue or due_soon):
            return
        parts = []
        if overdue:
            parts.append(f"{overdue} overdue")
        if due_soon:
            parts.append(f"{due_soon} due this week")
        self.toast.show_message(
            "⏰ Action items: " + " · ".join(parts) + " — open Action Items to review.",
            "warn", 8000)

    def _refresh_ai(self):
        """Rebuild the model list and the status chip after an AI-mode/model change."""
        self.new_page.refresh_models()
        self._update_status()

    def _install_model_flow(self):
        """Banner action: jump to Settings and focus the model installer."""
        self._goto(self.settings_page)
        self.settings_page.focus_install()

    def _switch_to_local(self):
        """Banner action: switch the AI mode to Local (Ollama)."""
        self.ctx.settings.set("ai_provider", "local")
        keys = getattr(self.settings_page, "_provider_keys", [])
        if "local" in keys:
            self.settings_page.provider_box.setCurrentIndex(keys.index("local"))
        self._refresh_ai()
        self.toast.show_message("Switched to Local (Ollama) mode.", "success")

    def _setup_shortcuts(self):
        def add(seq, fn):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)
        # page navigation
        for i in range(len(NAV)):
            add(f"Ctrl+{i + 1}", lambda idx=i: (self.nav_group.button(idx).setChecked(True),
                                                self._navigate(idx)))
        add("Ctrl+N", lambda: (self._goto(self.new_page), self.new_page._new_meeting()))
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

        brand_col = QVBoxLayout(); brand_col.setSpacing(4)
        self._brand_logo_w = 170
        self.brand_logo = QLabel(); self.brand_logo.setObjectName("BrandLogo")
        self._apply_brand_logo(self.ctx.settings.get("theme"))
        brand_col.addWidget(self.brand_logo, 0, Qt.AlignLeft)
        cap = QLabel("MEETINGS"); cap.setObjectName("BrandSub")
        brand_col.addWidget(cap)
        v.addLayout(brand_col)
        v.addSpacing(14)

        from .components import tip
        self.nav_group = QButtonGroup(self); self.nav_group.setExclusive(True)
        for i, (label, icon, desc) in enumerate(NAV):
            if i in NAV_SECTIONS:                       # logical group header
                if i:
                    v.addSpacing(10)
                hdr = QLabel(NAV_SECTIONS[i].upper()); hdr.setObjectName("NavGroup")
                v.addWidget(hdr)
            btn = QPushButton(f"  {icon}   {label}")
            btn.setObjectName("NavBtn"); btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self._navigate(idx))
            tip(btn, f"{desc}  ·  Ctrl+{i + 1}")
            btn.setAccessibleName(label)
            self.nav_group.addButton(btn, i)
            v.addWidget(btn)
        self.nav_group.button(0).setChecked(True)

        v.addStretch()
        self.status_chip = QLabel(); self.status_chip.setObjectName("Hint")
        self.status_chip.setWordWrap(True)
        tip(self.status_chip, "Status of the active AI mode (Settings → AI mode). Local needs "
                              "Ollama running; MICO360 Cloud needs a network connection")
        v.addWidget(self.status_chip)
        ver = QLabel(f"v{__version__}"); ver.setObjectName("Hint")
        tip(ver, "Installed app version — check Updates for newer releases")
        v.addWidget(ver)
        return side

    def _navigate(self, idx: int):
        self.stack.setCurrentIndex(idx)
        btn = self.nav_group.button(idx)          # keep the sidebar highlight in sync
        if btn is not None:
            btn.setChecked(True)
        page = self.stack.widget(idx)
        if page is self.history_page:
            self.history_page.reload()
        elif page is self.actions_page:
            self.actions_page.reload()
        elif page is self.insights_page:
            self.insights_page.reload()
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
        st = self.ctx.ai_status()
        cloud = self.ctx.provider() == "cloud"
        name = "MICO360 Cloud" if cloud else "Ollama"
        if st.running:
            n = len(st.models)
            self.status_chip.setText(f"● {name} online · {n} model{'s' if n != 1 else ''}")
            self.status_chip.setStyleSheet("color:#22C55E; font-size:9pt;")
        else:
            offline = (f"● {name} unavailable" if cloud
                       else "● Ollama offline — run 'ollama serve'")
            self.status_chip.setText(offline)
            self.status_chip.setStyleSheet("color:#EF4444; font-size:9pt;")

    def _apply_brand_logo(self, name: str):
        """Show the brand lockup that suits the sidebar background: the white
        logo on the dark theme, the full-colour logo on the light theme."""
        fname = "logo.png" if name == "light" else "logo-w.png"
        pm = QPixmap(str(resource_path("assets", fname)))
        if pm.isNull():                                   # fall back to the mark
            pm = QPixmap(str(resource_path("assets", "logo_256.png")))
        if not pm.isNull():
            self.brand_logo.setPixmap(pm.scaledToWidth(
                self._brand_logo_w, Qt.SmoothTransformation))

    # -- theme --------------------------------------------------------------
    def apply_theme(self, name: str):
        scale = float(self.ctx.settings.get("ui_scale", 1.0) or 1.0)
        theme.CURRENT = name
        self.setStyleSheet(theme.build_qss(name, scale))
        if hasattr(self, "brand_logo"):
            self._apply_brand_logo(name)
        if hasattr(self, "insights_page"):          # painted charts pick up the new palette
            self.insights_page.reload()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toast._reposition()

    def closeEvent(self, e):
        # autosave the current meeting + flush any in-progress recording
        try:
            self.new_page._autosave()
        except Exception:
            pass
        try:
            self.new_page.recorder_panel.stop_if_active()
        except Exception:
            pass
        try:
            self.apply_auto_record(False)          # stop the meeting watcher thread
        except Exception:
            pass
        super().closeEvent(e)
