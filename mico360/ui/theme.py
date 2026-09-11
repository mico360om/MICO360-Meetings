"""Dark / light theming via Qt style sheets.

Colors are defined as a palette dict and interpolated into one QSS template so
the two themes stay visually consistent. Sizing uses points/relative padding so
the UI scales correctly under different DPI and zoom levels.
"""
from __future__ import annotations

# MICO360 brand palette: deep maroon (#8B1E1E) + charcoal/warm-neutral, matching
# the logo. Accents use the brand-red family (#8B1E1E → #A83326 → #C7513F).
DARK = {
    "bg": "#17151A",            # warm near-black
    "surface": "#201E24",
    "surface2": "#2A272F",
    "border": "#39353F",
    "text": "#ECEAEF",
    "muted": "#A79FA9",
    "accent": "#A83326",        # brand red, brightened for dark surfaces
    "accent_hover": "#8B1E1E",
    "accent_text": "#FFFFFF",
    "danger": "#E5484D",
    "danger_soft": "#3A1E1E",
    "success": "#3FB950",
    "warning": "#E0A23A",
    "warning_soft": "#352A18",
    "input_bg": "#1A181E",
}

LIGHT = {
    "bg": "#F6F4F3",            # warm off-white
    "surface": "#FFFFFF",
    "surface2": "#F1ECEB",
    "border": "#E4DEDC",
    "text": "#221D1D",         # warm near-black
    "muted": "#6C6269",
    "accent": "#8B1E1E",        # brand maroon
    "accent_hover": "#6E1717",
    "accent_text": "#FFFFFF",
    "danger": "#DC2626",
    "danger_soft": "#FCEBEA",
    "success": "#16A34A",
    "warning": "#B8760F",
    "warning_soft": "#FBF1E1",
    "input_bg": "#FFFFFF",
}

# ---------------------------------------------------------------------------
# Design tokens — one source of truth for the whole app so every page shares the
# same type scale, corner radii, control padding and border weight. Change a
# value here and it propagates everywhere via build_qss().
# ---------------------------------------------------------------------------
TOKENS = {
    # Type scale (pt). Roles, not one-offs: micro < small < hint < body < sub
    # < section-heading < brand < page-title.
    "fs_micro":   "8pt",     # brand tagline, tiny captions
    "fs_small":   "9pt",     # table headers, chips, status, progress text
    "fs_hint":    "9.5pt",   # hints, muted secondary copy, empty-state body
    "fs_base":    "10.5pt",  # body, nav, inputs, buttons
    "fs_sub":     "10.5pt",  # page subtitle
    "fs_section": "13pt",    # every section / card / step / drop / empty title
    "fs_brand":   "16pt",    # sidebar brand wordmark (fallback)
    "fs_title":   "20pt",    # page title

    # Corner radii (px). Containers = lg, controls = md, small chips = sm.
    "r_sm":   "8px",
    "r_md":   "10px",
    "r_lg":   "12px",
    "r_pill": "999px",

    # Control padding — every button shares one height (9px vertical).
    "pad_btn":      "9px 16px",
    "pad_btn_wide": "9px 18px",   # primary / danger get a touch more width
    "pad_input":    "8px 12px",
    "pad_nav":      "11px 14px",

    # Borders
    "bw": "1px",
}

# Semantic colours shared by pages regardless of theme (status / priority
# badges must read the same in light and dark). Single source of truth —
# previously duplicated across pages_extra.py and insights_page.py.
SEMANTIC = {
    "status": {"Pending": "#B8760F", "In Progress": "#A83326",
               "Completed": "#16A34A", "Cancelled": "#9A9AA0", "Overdue": "#DC2626"},
    "priority": {"High": "#DC2626", "Medium": "#B8760F", "Low": "#6C6269"},
    "overdue_tint": "#FCEBEA",
}

_QSS = """
* {{ font-family: "Segoe UI", "Segoe UI Variable", "Inter", Arial, sans-serif; font-size: {fs_base}; }}
QWidget {{ color: {text}; background: transparent; }}
QMainWindow, #Root {{ background: {bg}; }}

/* ---- Sidebar ------------------------------------------------------------ */
#Sidebar {{ background: {surface}; border-right: {bw} solid {border}; }}
#Brand {{ font-size: {fs_brand}; font-weight: 800; color: {text}; }}
#BrandSub {{ color: {muted}; font-size: {fs_micro}; font-weight: 700; }}

#NavGroup {{ color: {muted}; font-size: {fs_micro}; font-weight: 800;
            padding: 2px 4px 2px 14px; }}
QPushButton#NavBtn {{
    text-align: left; padding: {pad_nav}; border: none; border-left: 3px solid transparent;
    border-radius: {r_md}; color: {muted}; background: transparent;
    font-size: {fs_base}; font-weight: 500;
}}
QPushButton#NavBtn:hover {{ background: {surface2}; color: {text}; }}
QPushButton#NavBtn:checked {{ background: {accent}; color: {accent_text};
    font-weight: 700; border-left: 3px solid {accent_text}; }}
QPushButton#NavBtn:checked:hover {{ background: {accent_hover}; }}

/* ---- Typography / page header ------------------------------------------- */
#PageTitle {{ font-size: {fs_title}; font-weight: 800; color: {text}; }}
#PageSub {{ color: {muted}; font-size: {fs_sub}; }}
#ContextBadge {{ color: {accent}; background: {surface2}; border: {bw} solid {border};
                border-radius: {r_sm}; padding: 4px 10px; font-size: {fs_hint}; font-weight: 600; }}
#SpeakerPanel {{ background: {surface2}; border: {bw} solid {border}; border-radius: {r_lg}; }}
#SpeakerBadge {{ color: {accent}; font-weight: 700; font-size: {fs_small}; }}
#KpiNum {{ color: {text}; font-size: 17pt; font-weight: 800; }}
#KpiLabel {{ color: {muted}; font-size: {fs_small}; font-weight: 600; }}
#SectionTitle {{ font-size: {fs_section}; font-weight: 700; color: {text}; }}
QLabel#Muted, #Hint {{ color: {muted}; font-size: {fs_hint}; }}

/* ---- Cards / collapsible sections --------------------------------------- */
#Card {{ background: {surface}; border: {bw} solid {border}; border-radius: {r_lg}; }}
#CardActive {{ background: {surface}; border: 2px solid {accent}; border-radius: {r_lg}; }}
#ProfileName {{ font-size: {fs_section}; font-weight: 700; color: {text}; }}
#ActiveBadge {{ color: {success}; font-weight: 700; font-size: {fs_small}; }}
#LogoThumb {{ background: {surface2}; border: {bw} solid {border}; border-radius: {r_md};
             color: {accent_text}; font-weight: 800; font-size: 16pt; }}
QToolButton#SectionHeader {{
    border: none; background: transparent; color: {text};
    font-size: {fs_section}; font-weight: 700; text-align: left; padding: 12px 8px;
}}
QToolButton#SectionHeader:hover {{ color: {accent}; }}
#SectionHeaderRow:hover {{ background: {surface2}; border-top-left-radius: {r_lg}; border-top-right-radius: {r_lg}; }}

/* ---- Buttons ------------------------------------------------------------ */
QPushButton {{
    background: {surface2}; color: {text}; border: {bw} solid {border};
    border-radius: {r_md}; padding: {pad_btn}; font-weight: 500;
}}
QPushButton:hover {{ border-color: {accent}; background: {surface}; }}
QPushButton:pressed {{ background: {surface2}; }}
QPushButton:disabled {{ color: {muted}; border-color: {border}; background: transparent; }}

QPushButton#Primary {{
    background: {accent}; color: {accent_text}; border: none; font-weight: 600;
    padding: {pad_btn_wide}; border-radius: {r_md};
}}
QPushButton#Primary:hover {{ background: {accent_hover}; }}
QPushButton#Primary:pressed {{ background: {accent_hover}; }}
QPushButton#Primary:disabled {{ background: {surface2}; color: {muted}; }}
QPushButton#Danger {{
    color: {accent_text}; background: {danger}; border: none; font-weight: 600;
    padding: {pad_btn_wide}; border-radius: {r_md};
}}
QPushButton#Danger:hover {{ background: {danger}; }}
QPushButton#Ghost {{ background: transparent; border: {bw} solid {border}; color: {text}; }}
QPushButton#Ghost:hover {{ background: {surface2}; border-color: {accent}; }}

/* ---- Inputs ------------------------------------------------------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {input_bg}; border: {bw} solid {border}; border-radius: {r_md};
    padding: {pad_input}; selection-background-color: {accent}; selection-color: {accent_text};
}}
QPlainTextEdit, QTextEdit {{ padding: 11px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {muted}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border: {bw} solid {accent}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {surface}; border: {bw} solid {border}; border-radius: {r_sm}; padding: 4px;
    selection-background-color: {accent}; selection-color: {accent_text}; outline: none;
}}
QCheckBox {{ spacing: 8px; }}

/* ---- Drop area ---------------------------------------------------------- */
#Drop {{ border: 2px dashed {border}; border-radius: {r_lg}; background: {surface2}; }}
#Drop[hover="true"] {{ border-color: {accent}; background: {surface}; }}
#DropTitle {{ font-size: {fs_section}; font-weight: 700; }}

/* ---- Progress ----------------------------------------------------------- */
QProgressBar {{
    border: none; border-radius: {r_sm}; background: {surface2};
    text-align: center; height: 14px; color: {text}; font-size: {fs_small};
}}
QProgressBar::chunk {{ background: {accent}; border-radius: {r_sm}; }}

/* ---- Tables ------------------------------------------------------------- */
QTableWidget {{
    background: {surface}; border: {bw} solid {border}; border-radius: {r_lg};
    gridline-color: transparent;
}}
QHeaderView::section {{
    background: transparent; color: {muted}; border: none;
    border-bottom: {bw} solid {border}; padding: 10px 8px; font-weight: 700; font-size: {fs_small};
}}
QTableWidget::item {{ padding: 9px 6px; border-bottom: {bw} solid {border}; }}
QTableWidget::item:selected {{ background: {accent}; color: {accent_text}; }}

/* ---- Lists -------------------------------------------------------------- */
QListWidget {{ background: {surface}; border: {bw} solid {border}; border-radius: {r_lg}; padding: 4px; }}
QListWidget::item {{ padding: 10px 12px; border-radius: {r_sm}; margin: 1px 2px; }}
QListWidget::item:hover {{ background: {surface2}; }}
QListWidget::item:selected {{ background: {accent}; color: {accent_text}; }}

/* ---- Scrollbars --------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 4px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {muted}; }}

/* ---- Tabs (underline style) --------------------------------------------- */
#StatusChip {{ border-radius: {r_md}; padding: 4px 10px; font-size: {fs_small}; }}
QTabWidget::pane {{ border: none; border-top: {bw} solid {border}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {muted}; padding: 9px 16px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent; font-weight: 500;
}}
QTabBar::tab:hover {{ color: {text}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; font-weight: 700; }}

QToolTip {{ background: {surface2}; color: {text}; border: {bw} solid {border}; border-radius: {r_sm}; padding: 7px 10px; }}
QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

/* ---- Wizard progress stepper -------------------------------------------- */
#StepNum {{ border: 2px solid {border}; border-radius: 13px; background: {surface};
           color: {muted}; font-weight: 700; font-size: 10pt; }}
#StepNum[state="active"] {{ border-color: {accent}; color: {accent}; background: {surface}; }}
#StepNum[state="done"] {{ border-color: {accent}; color: {accent_text}; background: {accent}; }}
#StepLabel {{ color: {muted}; font-weight: 600; font-size: 10pt; }}
#StepLabel[state="active"] {{ color: {text}; }}
#StepLabel[state="done"] {{ color: {accent}; }}
#StepConn {{ background: {border}; border: none; border-radius: 1px; }}
#StepConn[on="true"] {{ background: {accent}; }}
#StepHeader {{ font-size: {fs_section}; font-weight: 700; color: {text}; }}
#ReviewGroup {{ color: {accent}; font-size: {fs_small}; font-weight: 700; }}
#ReviewVal {{ color: {text}; font-weight: 600; }}

/* ---- Tag pills (Built-in / Custom) & favourite star --------------------- */
#Tag {{ background: {surface2}; border-radius: {r_sm}; padding: 2px 8px;
        font-size: {fs_small}; font-weight: 700; color: {muted}; }}
#Tag[kind="custom"] {{ color: {accent}; }}
#FavBtn {{ background: transparent; border: none; font-size: 15pt; padding: 0 4px; color: {muted}; }}
#FavBtn[on="true"] {{ color: {warning}; }}
#FavBtn:hover {{ color: {warning}; }}

/* ---- Empty states ------------------------------------------------------- */
#EmptyIcon {{ font-size: 34pt; color: {muted}; }}
#EmptyTitle {{ font-size: {fs_section}; font-weight: 700; color: {text}; }}
#EmptyDesc {{ color: {muted}; font-size: {fs_hint}; }}

/* ---- Readiness banner (AI not ready) ------------------------------------ */
#Banner {{ background: {warning_soft}; border: {bw} solid {warning}; border-radius: {r_lg}; }}
#BannerIcon {{ color: {warning}; font-size: 15pt; }}
#BannerText {{ color: {text}; font-size: 10pt; }}
#BannerClose {{ background: transparent; border: none; color: {muted}; font-size: 11pt; font-weight: 700; padding: 0; }}
#BannerClose:hover {{ color: {text}; }}

/* ---- Generation status (stages · progress · errors) --------------------- */
#GenStage {{ color: {muted}; font-weight: 700; font-size: {fs_small}; }}
#GenStage[state="active"] {{ color: {accent}; }}
#GenStage[state="done"] {{ color: {success}; }}
#GenSep {{ color: {muted}; font-size: {fs_small}; }}
#GenMsg {{ color: {text}; font-size: 10pt; }}
#GenMsg[state="ok"] {{ color: {success}; font-weight: 600; }}
#GenMsg[state="err"] {{ color: {danger}; }}
#ErrorCard {{ background: {danger_soft}; border: {bw} solid {danger}; border-radius: {r_lg}; }}
#ErrorIcon {{ color: {danger}; font-size: 15pt; }}
#ErrorText {{ color: {text}; font-size: 10pt; }}
"""


import re

_PT_RE = re.compile(r"(\d+(?:\.\d+)?)pt")


def palette(name: str) -> dict:
    return LIGHT if name == "light" else DARK


def build_qss(name: str, scale: float = 1.0) -> str:
    """Build the stylesheet for a theme, scaling every point-size by `scale`
    (the UI text-size / accessibility factor). Palette + design tokens are the
    single source of truth for colour and sizing."""
    qss = _QSS.format(**palette(name), **TOKENS)
    if scale and abs(scale - 1.0) > 1e-6:
        qss = _PT_RE.sub(lambda m: f"{float(m.group(1)) * scale:.2f}pt", qss)
    return qss
