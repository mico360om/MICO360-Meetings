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
    "success": "#16A34A",
    "warning": "#B8760F",
    "warning_soft": "#FBF1E1",
    "input_bg": "#FFFFFF",
}

_QSS = """
* {{ font-family: "Segoe UI", "Segoe UI Variable", "Inter", Arial, sans-serif; font-size: 10.5pt; }}
QWidget {{ color: {text}; background: transparent; }}
QMainWindow, #Root {{ background: {bg}; }}

/* ---- Sidebar ------------------------------------------------------------ */
#Sidebar {{ background: {surface}; border-right: 1px solid {border}; }}
#Brand {{ font-size: 14pt; font-weight: 800; color: {text}; }}
#BrandSub {{ color: {muted}; font-size: 8pt; font-weight: 700; }}

QPushButton#NavBtn {{
    text-align: left; padding: 11px 14px; border: none; border-radius: 10px;
    color: {muted}; background: transparent; font-size: 10.5pt; font-weight: 500;
}}
QPushButton#NavBtn:hover {{ background: {surface2}; color: {text}; }}
QPushButton#NavBtn:checked {{ background: {accent}; color: {accent_text}; font-weight: 600; }}
QPushButton#NavBtn:checked:hover {{ background: {accent_hover}; }}

/* ---- Typography / page header ------------------------------------------- */
#PageTitle {{ font-size: 19pt; font-weight: 800; color: {text}; }}
#PageSub {{ color: {muted}; font-size: 10.5pt; }}
#SectionTitle {{ font-size: 12pt; font-weight: 700; color: {text}; }}
QLabel#Muted, #Hint {{ color: {muted}; font-size: 9.5pt; }}

/* ---- Cards / collapsible sections --------------------------------------- */
#Card {{ background: {surface}; border: 1px solid {border}; border-radius: 14px; }}
QToolButton#SectionHeader {{
    border: none; background: transparent; color: {text};
    font-size: 12pt; font-weight: 700; text-align: left; padding: 12px 8px;
}}
QToolButton#SectionHeader:hover {{ color: {accent}; }}
#SectionHeaderRow:hover {{ background: {surface2}; border-top-left-radius: 14px; border-top-right-radius: 14px; }}

/* ---- Buttons ------------------------------------------------------------ */
QPushButton {{
    background: {surface2}; color: {text}; border: 1px solid {border};
    border-radius: 10px; padding: 8px 15px; font-weight: 500;
}}
QPushButton:hover {{ border-color: {accent}; background: {surface}; }}
QPushButton:pressed {{ background: {surface2}; }}
QPushButton:disabled {{ color: {muted}; border-color: {border}; background: transparent; }}

QPushButton#Primary {{
    background: {accent}; color: {accent_text}; border: none; font-weight: 600;
    padding: 10px 20px; border-radius: 10px;
}}
QPushButton#Primary:hover {{ background: {accent_hover}; }}
QPushButton#Primary:pressed {{ background: {accent_hover}; }}
QPushButton#Primary:disabled {{ background: {surface2}; color: {muted}; }}
QPushButton#Danger {{
    color: {accent_text}; background: {danger}; border: none; font-weight: 600;
    padding: 10px 18px; border-radius: 10px;
}}
QPushButton#Danger:hover {{ background: {danger}; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid {border}; color: {text}; }}
QPushButton#Ghost:hover {{ background: {surface2}; border-color: {accent}; }}

/* ---- Inputs ------------------------------------------------------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {input_bg}; border: 1px solid {border}; border-radius: 10px;
    padding: 8px 12px; selection-background-color: {accent}; selection-color: {accent_text};
}}
QPlainTextEdit, QTextEdit {{ padding: 11px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {muted}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {accent}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 4px;
    selection-background-color: {accent}; selection-color: {accent_text}; outline: none;
}}
QCheckBox {{ spacing: 8px; }}

/* ---- Drop area ---------------------------------------------------------- */
#Drop {{ border: 2px dashed {border}; border-radius: 14px; background: {surface2}; }}
#Drop[hover="true"] {{ border-color: {accent}; background: {surface}; }}
#DropTitle {{ font-size: 12pt; font-weight: 700; }}

/* ---- Progress ----------------------------------------------------------- */
QProgressBar {{
    border: none; border-radius: 7px; background: {surface2};
    text-align: center; height: 14px; color: {text}; font-size: 8.5pt;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 7px; }}

/* ---- Tables ------------------------------------------------------------- */
QTableWidget {{
    background: {surface}; border: 1px solid {border}; border-radius: 12px;
    gridline-color: transparent;
}}
QHeaderView::section {{
    background: transparent; color: {muted}; border: none;
    border-bottom: 1px solid {border}; padding: 10px 8px; font-weight: 700; font-size: 9pt;
}}
QTableWidget::item {{ padding: 9px 6px; border-bottom: 1px solid {border}; }}
QTableWidget::item:selected {{ background: {accent}; color: {accent_text}; }}

/* ---- Lists -------------------------------------------------------------- */
QListWidget {{ background: {surface}; border: 1px solid {border}; border-radius: 12px; padding: 4px; }}
QListWidget::item {{ padding: 10px 12px; border-radius: 8px; margin: 1px 2px; }}
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
#StatusChip {{ border-radius: 10px; padding: 4px 10px; font-size: 9pt; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {border}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {muted}; padding: 9px 16px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent; font-weight: 500;
}}
QTabBar::tab:hover {{ color: {text}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; font-weight: 700; }}

QToolTip {{ background: {surface2}; color: {text}; border: 1px solid {border}; border-radius: 8px; padding: 7px 10px; }}
QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

/* ---- Readiness banner (AI not ready) ------------------------------------ */
#Banner {{ background: {warning_soft}; border: 1px solid {warning}; border-radius: 12px; }}
#BannerIcon {{ color: {warning}; font-size: 15pt; }}
#BannerText {{ color: {text}; font-size: 10pt; }}
#BannerClose {{ background: transparent; border: none; color: {muted}; font-size: 11pt; font-weight: 700; padding: 0; }}
#BannerClose:hover {{ color: {text}; }}
"""


def palette(name: str) -> dict:
    return LIGHT if name == "light" else DARK


def build_qss(name: str) -> str:
    return _QSS.format(**palette(name))
