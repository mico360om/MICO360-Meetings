"""Capture a screenshot of every app screen to ./screenshots/.

Runs on the real Windows platform (NOT offscreen) so fonts render correctly.
Usage:  python tools/capture_screens.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "screenshots"
OUT.mkdir(exist_ok=True)

from PySide6.QtWidgets import QApplication       # noqa: E402
from PySide6.QtCore import Qt                      # noqa: E402


def save(widget, name: str):
    widget.grab().save(str(OUT / f"{name}.png"))
    print("saved", name)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from mico360.ui.context import AppContext
    from mico360.ui.main_window import MainWindow
    from mico360.core.history import Meeting

    ctx = AppContext()

    # seed a little data so History/Profiles screens aren't empty
    for t in ("Q3 Product Planning", "Weekly Engineering Sync", "Client Onboarding Call"):
        ctx.history.save(Meeting(id=0, title=t, created_at=0, updated_at=0,
                                 style="Formal Minutes", model="llama3.1",
                                 transcript="Sample transcript…", minutes="# Meeting Minutes\n…"))

    win = MainWindow(ctx)
    win.resize(1280, 820)
    win.show()
    win.apply_theme("dark")            # force dark for the dark_* captures
    for _ in range(8):
        app.processEvents()

    pages = [("01_new_meeting", win.new_page),
             ("02_history", win.history_page),
             ("03_company_profiles", win.profiles_page),
             ("04_prompt_library", win.prompts_page),
             ("05_updates", win.updates_page),
             ("06_help_about", win.help_page),
             ("07_settings", win.settings_page)]

    for i, (name, page) in enumerate(pages):
        win._navigate(win.stack.indexOf(page))
        if page is win.history_page:
            win.history_page.reload()
        if page is win.prompts_page:
            win.prompts_page.reload()
        for _ in range(6):
            app.processEvents()
        save(win, f"dark_{name}")

    # New Meeting with sections expanded + sample content
    win._navigate(0)
    win.new_page.transcript.setPlainText(
        "Sarah: Good morning everyone, let's start the Q3 planning call.\n"
        "David: The mobile login bug is blocking the beta…")
    win.new_page.minutes.setPlainText(
        "# Meeting Minutes\n**Meeting Title:** Q3 Product Planning\n\n"
        "## Action Items\n| Task | Responsible Person | Deadline | Status |\n"
        "| --- | --- | --- | --- |\n| Close login bug | David | Jul 25 | Pending |")
    win.new_page.sec_source.set_expanded(True)
    win.new_page.sec_transcript.set_expanded(True)
    win.new_page.sec_generate.set_expanded(True)
    win.new_page.sec_minutes.set_expanded(True)
    for _ in range(6):
        app.processEvents()
    save(win, "dark_01b_new_meeting_filled")

    # Recording active state (simulated)
    rp = win.new_page.recorder_panel
    rp.stack.setCurrentIndex(1)
    for k, v in {"Type": "Screen + audio", "Status": "Recording", "Format": "MP4",
                 "Quality": "1920x1080 @ 12 fps", "Microphone": "Default microphone",
                 "Camera": "N/A", "File name": "recording_1718.mp4", "File size": "5.4 MB",
                 "Started": "2026-06-16  10:14:02", "Save location": r"…\MICO360Meetings\tmp"}.items():
        rp.detail_labels[k].setText(v)
    rp.timer_lbl.setText("00:02:37")
    import math
    for j in range(32):
        rp.viz.push(0.2 + 0.6 * abs((j % 8) - 4) / 4)
    rp.viz.set_active(True)
    win._navigate(0)
    win.new_page.sec_source.set_expanded(True)
    win.new_page.source_tabs.setCurrentIndex(1)     # show the Record tab
    win.new_page.sec_transcript.set_expanded(False)
    win.new_page.sec_generate.set_expanded(False)
    win.new_page.sec_minutes.set_expanded(False)
    for _ in range(6):
        app.processEvents()
    save(win, "dark_01c_recording_active")
    rp.stack.setCurrentIndex(0)
    win.new_page.source_tabs.setCurrentIndex(0)

    # Light theme — main screen
    win.apply_theme("light")
    win._navigate(0)
    for _ in range(6):
        app.processEvents()
    save(win, "light_01_new_meeting")
    win._navigate(3); win.prompts_page.reload()
    for _ in range(6):
        app.processEvents()
    save(win, "light_04_prompt_library")
    win.apply_theme("dark")

    # Dialogs
    from mico360.ui.dialogs import ProfileDialog, PromptDialog
    from mico360.core.profiles import CompanyProfile
    pd = ProfileDialog(ctx.profiles, CompanyProfile(name="Acme Corporation",
                       address="123 Business Rd", phone="+92 300 1234567",
                       email="info@acme.com", website="acme.com",
                       footer_text="Confidential — Acme"), None)
    pd.resize(840, 600); pd.show()
    for _ in range(6):
        app.processEvents()
    save(pd, "dialog_company_profile")
    pd.close()

    prd = PromptDialog("Client Meeting Recap",
                       "You are writing a professional client meeting recap…\n\nTranscript:\n[TRANSCRIPT_HERE]",
                       "Client Meeting")
    prd.resize(700, 560); prd.show()
    for _ in range(6):
        app.processEvents()
    save(prd, "dialog_prompt_editor")
    prd.close()

    print(f"\nAll screenshots saved to: {OUT}")
    ctx.close()


if __name__ == "__main__":
    main()
