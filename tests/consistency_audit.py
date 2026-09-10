"""Cross-screen consistency audit — data sync + design uniformity.

Checks that figures/counts/versions/dates are consistent across pages, and that
every page follows the same design tokens (PageTitle, margins, card styling).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

issues: list[str] = []
checks = 0


def ok(cond, label, detail=""):
    global checks
    checks += 1
    if cond:
        print(f"  [OK]   {label}  {detail}")
    else:
        print(f"  [DIFF] {label}  {detail}")
        issues.append(f"{label} — {detail}")


def main():
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QScrollArea
    app = QApplication.instance() or QApplication([])
    from mico360 import __version__
    from mico360.ui.context import AppContext
    from mico360.ui.main_window import MainWindow

    ctx = AppContext()
    ctx.settings.set("onboarded", True)
    win = MainWindow(ctx); win.resize(1280, 820); win.show()
    for _ in range(5):
        app.processEvents()
    for i in range(win.stack.count()):
        win._navigate(i)
    for _ in range(4):
        app.processEvents()

    print("=== Data sync / figures ===")
    # 1) version string consistency
    sidebar_ver = None
    for lbl in win.findChildren(QLabel):
        if lbl.text().strip() == f"v{__version__}":
            sidebar_ver = lbl.text().strip()
    ok(sidebar_ver == f"v{__version__}", "Sidebar version", f"= v{__version__}")
    ok(__version__ in win.updates_page.cur_lbl.text(), "Updates 'current version'",
       win.updates_page.cur_lbl.text())
    from PySide6.QtWidgets import QTextBrowser
    about = " ".join(b.toHtml() for b in win.help_page.findChildren(QTextBrowser))
    ok(f"v{__version__}" in about, "About page version", f"contains v{__version__}")

    # 2) Ollama model count synced: sidebar chip vs Settings env vs New-Meeting dropdown
    st = ctx.ollama_status()
    n_models = len(st.models)
    chip = win.status_chip.text()
    m = re.search(r"(\d+)\s+model", chip)
    chip_n = int(m.group(1)) if m else -1
    win.settings_page._refresh_env()
    env = win.settings_page.env_lbl.text()
    em = re.search(r"(\d+)\s+installed", env)
    env_n = int(em.group(1)) if em else (0 if "No models" in env else -1)
    win.new_page.refresh_models()
    dd = win.new_page.model_box
    dd_n = dd.count() if (dd.isEnabled() and not dd.currentText().startswith("⚠")) else 0
    ok(chip_n == n_models, "Sidebar model count", f"chip={chip_n} actual={n_models}")
    ok(env_n == n_models, "Settings env model count", f"env={env_n} actual={n_models}")
    ok(dd_n == n_models, "New-Meeting model dropdown", f"dropdown={dd_n} actual={n_models}")

    # 3) Prompt count synced across library / dropdown / prompts page
    lib_n = len(ctx.prompts.list())
    win.new_page.refresh_prompts()
    dropdown_n = win.new_page.prompt_box.count()
    win.prompts_page.reload()
    # prompts page has header rows + prompt rows; count only prompt rows
    page_prompt_n = sum(1 for idx in win.prompts_page._row_map if idx >= 0)
    ok(lib_n == dropdown_n, "Prompt count: library vs New-Meeting dropdown", f"{lib_n} vs {dropdown_n}")
    ok(lib_n == page_prompt_n, "Prompt count: library vs Prompt-Library page", f"{lib_n} vs {page_prompt_n}")

    # 4) Output styles count consistent (5 everywhere)
    ok(win.new_page.style_box.count() == 5, "Output styles = 5", str(win.new_page.style_box.count()))

    # 5) Active profile synced (Profiles page label vs settings)
    win.profiles_page.reload()
    active = ctx.active_profile()
    label = win.profiles_page.active_lbl.text()
    if active:
        ok(active.name in label, "Active profile label", label)
    else:
        ok("No active profile" in label, "Active profile label (none)", label)

    print("\n=== Design consistency ===")
    pages = {
        "New Meeting": win.new_page, "History": win.history_page,
        "Action Items": win.actions_page,
        "Company Profiles": win.profiles_page, "Prompt Library": win.prompts_page,
        "Updates": win.updates_page, "Help & About": win.help_page,
        "Settings": win.settings_page,
    }
    # every page has exactly one PageTitle
    for name, pg in pages.items():
        titles = [l for l in pg.findChildren(QLabel) if l.objectName() == "PageTitle"]
        ok(len(titles) == 1, f"{name}: one PageTitle", f"found {len(titles)}")

    # content margins: each page's outer content uses left margin 24
    margins = {}
    for name, pg in pages.items():
        sa = pg.findChild(QScrollArea)
        target = sa.widget() if sa else pg
        lay = target.layout()
        if lay:
            margins[name] = lay.contentsMargins().left()
    uniq = set(margins.values())
    ok(uniq <= {24}, "Uniform content left-margin (24px)", str(margins))

    # every page-subtitle uses objectName PageSub (design token)
    subs = {}
    for name, pg in pages.items():
        subs[name] = any(l.objectName() == "PageSub" for l in pg.findChildren(QLabel))
    missing = [n for n, has in subs.items() if not has]
    ok(not missing, "PageSub subtitle present", f"missing: {missing}" if missing else "all pages")

    # theme applied uniformly (stylesheet non-empty on window)
    ok(len(win.styleSheet()) > 100, "Theme QSS applied", f"{len(win.styleSheet())} chars")

    # responsive: no horizontal overflow at 3 sizes on any page
    from PySide6.QtWidgets import QScrollArea as SA
    overflow = []
    for w, h in [(1080, 660), (1440, 900), (1920, 1080)]:
        win.resize(w, h)
        for i in range(win.stack.count()):
            win._navigate(i)
            for _ in range(4):
                app.processEvents()
            p = win.stack.widget(i)
            for sa in p.findChildren(SA):
                if not sa.isVisible():          # skip inactive tab panes (not displayed)
                    continue
                if sa.horizontalScrollBar().maximum() > 2:
                    overflow.append((p.__class__.__name__, w))
    ok(not overflow, "No horizontal overflow (3 sizes)", str(overflow[:3]))

    print(f"\n==== {checks - len(issues)}/{checks} consistency checks OK ====")
    if issues:
        print("ISSUES:")
        for i in issues:
            print("  -", i)
    return 0 if not issues else 1


if __name__ == "__main__":
    _rc = main()
    # Flush, then os._exit to skip Python/Qt finalization — PySide6 on
    # Python 3.14 intermittently crashes during interpreter teardown
    # (0xC0000409) AFTER tests pass, which would mask a clean result.
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(_rc)
